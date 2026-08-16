from __future__ import annotations

import base64
import hashlib
import json
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from worldforge.models import RuntimeEvent


class EventStore:
    """Append-only, hash-chained event store with fork/replay support.

    Sequence allocation is serialized by SQLite itself (`BEGIN IMMEDIATE`), not only by a Python
    lock. That keeps the append contract correct when multiple processes or workers target the same
    session database.
    """

    def __init__(self, path: str | Path = "worldforge.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(
                    session_id TEXT PRIMARY KEY,
                    parent_session_id TEXT,
                    parent_seq INTEGER,
                    created_at REAL NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS events(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    PRIMARY KEY(session_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_events_session_type ON events(session_id,event_type);
                CREATE TABLE IF NOT EXISTS snapshots(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id, seq)
                );
                """
            )

    def create_session(
        self,
        session_id: str,
        *,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions(session_id,parent_session_id,parent_seq,created_at,meta_json) VALUES(?,?,?,?,?)",
                (
                    session_id,
                    parent_session_id,
                    parent_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> RuntimeEvent:
        # Python lock keeps threads cheap; BEGIN IMMEDIATE is the actual cross-process guarantee.
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT seq, hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                seq = int(row["seq"]) + 1 if row else 1
                prev_hash = row["hash"] if row else "GENESIS"
                ts = time.time()
                payload_json = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                digest = hashlib.sha256(
                    f"{session_id}|{seq}|{event_type}|{payload_json}|{ts:.6f}|{prev_hash}".encode()
                ).hexdigest()
                conn.execute(
                    "INSERT INTO events(session_id,seq,event_type,payload_json,ts,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
                    (session_id, seq, event_type, payload_json, ts, prev_hash, digest),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return RuntimeEvent(
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            ts=ts,
            hash=digest,
            prev_hash=prev_hash,
        )

    def list_events(self, session_id: str, after_seq: int = 0) -> list[RuntimeEvent]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq",
                (session_id, after_seq),
            ).fetchall()
        return [
            RuntimeEvent(
                session_id=r["session_id"],
                seq=r["seq"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"]),
                ts=r["ts"],
                hash=r["hash"],
                prev_hash=r["prev_hash"],
            )
            for r in rows
        ]

    def verify_chain(self, session_id: str) -> bool:
        events = self.list_events(session_id)
        prev = "GENESIS"
        for event in events:
            if event.prev_hash != prev:
                return False
            payload_json = json.dumps(
                event.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            digest = hashlib.sha256(
                f"{event.session_id}|{event.seq}|{event.event_type}|{payload_json}|{event.ts:.6f}|{event.prev_hash}".encode()
            ).hexdigest()
            if digest != event.hash:
                return False
            prev = event.hash
        return True

    def save_snapshot(self, session_id: str, seq: int, snapshot: dict[str, Any]) -> None:
        encoded = base64.b85encode(
            pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
        ).decode("ascii")
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO snapshots(session_id,seq,snapshot_json,created_at) VALUES(?,?,?,?)",
                (session_id, seq, encoded, time.time()),
            )

    def get_snapshot(self, session_id: str, seq: int | None = None) -> dict[str, Any] | None:
        query = "SELECT snapshot_json FROM snapshots WHERE session_id=?"
        params: list[Any] = [session_id]
        if seq is not None:
            query += " AND seq<=?"
            params.append(seq)
        query += " ORDER BY seq DESC LIMIT 1"
        with self._conn() as c:
            row = c.execute(query, params).fetchone()
        return (
            pickle.loads(base64.b85decode(row["snapshot_json"].encode("ascii")))
            if row
            else None
        )

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT s.session_id,s.parent_session_id,s.parent_seq,s.created_at,s.meta_json,
                       COUNT(e.seq) AS event_count
                FROM sessions s
                LEFT JOIN events e ON e.session_id=s.session_id
                GROUP BY s.session_id,s.parent_session_id,s.parent_seq,s.created_at,s.meta_json
                ORDER BY s.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "parent_session_id": row["parent_session_id"],
                "parent_seq": row["parent_seq"],
                "created_at": row["created_at"],
                "meta": json.loads(row["meta_json"] or "{}"),
                "event_count": int(row["event_count"] or 0),
                "hash_chain_valid": self.verify_chain(row["session_id"]),
            }
            for row in rows
        ]

    def session_meta(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT session_id,parent_session_id,parent_seq,created_at,meta_json FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "session_id": row["session_id"],
            "parent_session_id": row["parent_session_id"],
            "parent_seq": row["parent_seq"],
            "created_at": row["created_at"],
            "meta": json.loads(row["meta_json"] or "{}"),
        }

    def fork(
        self,
        source_session_id: str,
        at_seq: int,
        new_session_id: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.create_session(
            new_session_id,
            parent_session_id=source_session_id,
            parent_seq=at_seq,
            meta=meta,
        )
        for event in self.list_events(source_session_id):
            if event.seq > at_seq:
                break
            self.append(
                new_session_id,
                event.event_type,
                {
                    **event.payload,
                    "_forked_from": source_session_id,
                    "_source_seq": event.seq,
                },
            )
