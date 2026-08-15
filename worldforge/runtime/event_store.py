from __future__ import annotations

import hashlib
import json
import base64
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from worldforge.models import RuntimeEvent


class EventStore:
    """Append-only, hash-chained event store with fork/replay support."""

    def __init__(self, path: str | Path = "worldforge.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
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

    def create_session(self, session_id: str, *, parent_session_id: str | None = None,
                       parent_seq: int | None = None, meta: dict[str, Any] | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO sessions(session_id,parent_session_id,parent_seq,created_at,meta_json) VALUES(?,?,?,?,?)",
                (session_id, parent_session_id, parent_seq, time.time(), json.dumps(meta or {}, ensure_ascii=False)),
            )

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> RuntimeEvent:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT seq, hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session_id,)
            ).fetchone()
            seq = int(row["seq"]) + 1 if row else 1
            prev_hash = row["hash"] if row else "GENESIS"
            ts = time.time()
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(f"{session_id}|{seq}|{event_type}|{payload_json}|{ts:.6f}|{prev_hash}".encode()).hexdigest()
            c.execute(
                "INSERT INTO events(session_id,seq,event_type,payload_json,ts,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
                (session_id, seq, event_type, payload_json, ts, prev_hash, digest),
            )
        return RuntimeEvent(session_id=session_id, seq=seq, event_type=event_type, payload=payload,
                            ts=ts, hash=digest, prev_hash=prev_hash)

    def list_events(self, session_id: str, after_seq: int = 0) -> list[RuntimeEvent]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq", (session_id, after_seq)
            ).fetchall()
        return [RuntimeEvent(session_id=r["session_id"], seq=r["seq"], event_type=r["event_type"],
                             payload=json.loads(r["payload_json"]), ts=r["ts"], hash=r["hash"], prev_hash=r["prev_hash"])
                for r in rows]

    def verify_chain(self, session_id: str) -> bool:
        events = self.list_events(session_id)
        prev = "GENESIS"
        for e in events:
            if e.prev_hash != prev:
                return False
            payload_json = json.dumps(e.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(f"{e.session_id}|{e.seq}|{e.event_type}|{payload_json}|{e.ts:.6f}|{e.prev_hash}".encode()).hexdigest()
            if digest != e.hash:
                return False
            prev = e.hash
        return True

    def save_snapshot(self, session_id: str, seq: int, snapshot: dict[str, Any]) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO snapshots(session_id,seq,snapshot_json,created_at) VALUES(?,?,?,?)",
                (session_id, seq, base64.b85encode(pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)).decode("ascii"), time.time()),
            )

    def get_snapshot(self, session_id: str, seq: int | None = None) -> dict[str, Any] | None:
        q = "SELECT snapshot_json FROM snapshots WHERE session_id=?"
        params: list[Any] = [session_id]
        if seq is not None:
            q += " AND seq<=?"
            params.append(seq)
        q += " ORDER BY seq DESC LIMIT 1"
        with self._conn() as c:
            r = c.execute(q, params).fetchone()
        return pickle.loads(base64.b85decode(r["snapshot_json"].encode("ascii"))) if r else None

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT session_id,parent_session_id,parent_seq,created_at,meta_json FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            count = 0
            with self._conn() as c:
                cr = c.execute("SELECT COUNT(*) AS n FROM events WHERE session_id=?", (r["session_id"],)).fetchone()
                count = int(cr["n"]) if cr else 0
            out.append({
                "session_id": r["session_id"],
                "parent_session_id": r["parent_session_id"],
                "parent_seq": r["parent_seq"],
                "created_at": r["created_at"],
                "meta": json.loads(r["meta_json"] or "{}"),
                "event_count": count,
                "hash_chain_valid": self.verify_chain(r["session_id"]),
            })
        return out

    def session_meta(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT session_id,parent_session_id,parent_seq,created_at,meta_json FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not r:
            return None
        return {
            "session_id": r["session_id"],
            "parent_session_id": r["parent_session_id"],
            "parent_seq": r["parent_seq"],
            "created_at": r["created_at"],
            "meta": json.loads(r["meta_json"] or "{}"),
        }

    def fork(self, source_session_id: str, at_seq: int, new_session_id: str, meta: dict[str, Any] | None = None) -> None:
        self.create_session(new_session_id, parent_session_id=source_session_id, parent_seq=at_seq, meta=meta)
        for event in self.list_events(source_session_id):
            if event.seq > at_seq:
                break
            self.append(new_session_id, event.event_type, {**event.payload, "_forked_from": source_session_id, "_source_seq": event.seq})
