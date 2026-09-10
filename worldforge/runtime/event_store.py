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

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            session_id=row["session_id"],
            seq=row["seq"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            ts=row["ts"],
            hash=row["hash"],
            prev_hash=row["prev_hash"],
        )

    @staticmethod
    def _append_in_connection(
        c: sqlite3.Connection,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        row = c.execute(
            "SELECT seq, hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        seq = int(row["seq"]) + 1 if row else 1
        prev_hash = row["hash"] if row else "GENESIS"
        ts = time.time()
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(
            f"{session_id}|{seq}|{event_type}|{payload_json}|{ts:.6f}|{prev_hash}".encode()
        ).hexdigest()
        c.execute(
            "INSERT INTO events(session_id,seq,event_type,payload_json,ts,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                seq,
                event_type,
                payload_json,
                ts,
                prev_hash,
                digest,
            ),
        )
        return RuntimeEvent(
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            ts=ts,
            hash=digest,
            prev_hash=prev_hash,
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
                "INSERT OR REPLACE INTO sessions(session_id,parent_session_id,parent_seq,created_at,meta_json) VALUES(?,?,?,?,?)",
                (
                    session_id,
                    parent_session_id,
                    parent_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        with self._lock, self._conn() as c:
            # Sequence allocation and insert must share one cross-connection write transaction.
            # A process-local lock cannot prevent two EventStore instances from otherwise
            # reading the same latest seq and racing on the same (session_id, seq) key.
            c.execute("BEGIN IMMEDIATE")
            return self._append_in_connection(c, session_id, event_type, payload)

    def latest_seq(self, session_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT seq FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return int(row["seq"]) if row else 0

    def next_event(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> RuntimeEvent | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 1",
                (session_id, int(after_seq)),
            ).fetchone()
        return self._event_from_row(row) if row else None

    def latest_event_of_type(
        self,
        session_id: str,
        event_type: str,
    ) -> RuntimeEvent | None:
        """Return one indexed event without materializing unrelated trace payloads."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM events "
                "WHERE session_id=? AND event_type=? "
                "ORDER BY seq DESC LIMIT 1",
                (session_id, str(event_type)),
            ).fetchone()
        return self._event_from_row(row) if row else None

    def payload_value_counts(
        self,
        session_id: str,
        event_type: str,
        payload_key: str,
    ) -> dict[str, int]:
        """Count one payload field across one indexed event type.

        Only matching ``payload_json`` values are read and decoded. This keeps trace-summary
        consumers from loading large planner/counterfactual/checkpoint payloads they never use.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT payload_json FROM events "
                "WHERE session_id=? AND event_type=? ORDER BY seq",
                (session_id, str(event_type)),
            ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            value = json.loads(row["payload_json"]).get(payload_key)
            if value is None:
                continue
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def status_snapshot(self, session_id: str) -> dict[str, Any]:
        """Read the bounded durable state needed by RunManager.status().

        Event ``seq`` values are append-only and contiguous within a session, so the latest
        sequence is also the durable event count. Status polling therefore needs only two
        ``LIMIT 1`` lookups instead of loading or counting the complete run history.
        """
        terminal_types = ("run.completed", "run.failed", "run.cancelled")
        with self._conn() as c:
            last_row = c.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            terminal_row = c.execute(
                "SELECT * FROM events "
                "WHERE session_id=? AND event_type IN (?,?,?) "
                "ORDER BY seq DESC LIMIT 1",
                (session_id, *terminal_types),
            ).fetchone()
        last_event = self._event_from_row(last_row) if last_row else None
        terminal_event = self._event_from_row(terminal_row) if terminal_row else None
        return {
            "event_count": int(last_event.seq) if last_event else 0,
            "last_event": last_event,
            "terminal_event": terminal_event,
        }

    def list_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> list[RuntimeEvent]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq",
                (session_id, after_seq),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def verify_chain(self, session_id: str) -> bool:
        """Verify the complete chain without materializing the complete trace in memory."""
        prev = "GENESIS"
        expected_seq = 1
        with self._conn() as c:
            cursor = c.execute(
                "SELECT session_id,seq,event_type,payload_json,ts,prev_hash,hash "
                "FROM events WHERE session_id=? ORDER BY seq",
                (session_id,),
            )
            for row in cursor:
                if int(row["seq"]) != expected_seq or row["prev_hash"] != prev:
                    return False
                payload_json = json.dumps(
                    json.loads(row["payload_json"]),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                digest = hashlib.sha256(
                    f"{row['session_id']}|{row['seq']}|{row['event_type']}|"
                    f"{payload_json}|{float(row['ts']):.6f}|{row['prev_hash']}".encode()
                ).hexdigest()
                if digest != row["hash"]:
                    return False
                prev = row["hash"]
                expected_seq += 1
        return True

    def save_snapshot(
        self,
        session_id: str,
        seq: int,
        snapshot: dict[str, Any],
    ) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO snapshots(session_id,seq,snapshot_json,created_at) VALUES(?,?,?,?)",
                (
                    session_id,
                    seq,
                    base64.b85encode(
                        pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
                    ).decode("ascii"),
                    time.time(),
                ),
            )

    def get_snapshot(
        self,
        session_id: str,
        seq: int | None = None,
    ) -> dict[str, Any] | None:
        q = "SELECT snapshot_json FROM snapshots WHERE session_id=?"
        params: list[Any] = [session_id]
        if seq is not None:
            q += " AND seq<=?"
            params.append(seq)
        q += " ORDER BY seq DESC LIMIT 1"
        with self._conn() as c:
            r = c.execute(q, params).fetchone()
        return (
            pickle.loads(base64.b85decode(r["snapshot_json"].encode("ascii")))
            if r
            else None
        )

    def list_sessions(
        self,
        limit: int = 30,
        *,
        verify_hash_chain: bool = False,
    ) -> list[dict[str, Any]]:
        """List recent session metadata without silently claiming integrity verification.

        Listing is lightweight by default: event counts are derived from contiguous max seq and
        hash-chain fields are explicitly marked unchecked. Callers that genuinely need integrity
        evidence must pass ``verify_hash_chain=True`` or call ``verify_chain`` directly.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT session_id,parent_session_id,parent_seq,created_at,meta_json "
                "FROM sessions ORDER BY created_at DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
            session_ids = [str(row["session_id"]) for row in rows]
            counts: dict[str, int] = {}
            if session_ids:
                placeholders = ",".join("?" for _ in session_ids)
                event_rows = c.execute(
                    "SELECT session_id, MAX(seq) AS event_count FROM events "
                    f"WHERE session_id IN ({placeholders}) GROUP BY session_id",
                    session_ids,
                ).fetchall()
                counts = {
                    str(row["session_id"]): int(row["event_count"] or 0)
                    for row in event_rows
                }

        out = []
        for row in rows:
            session_id = str(row["session_id"])
            checked = bool(verify_hash_chain)
            out.append(
                {
                    "session_id": session_id,
                    "parent_session_id": row["parent_session_id"],
                    "parent_seq": row["parent_seq"],
                    "created_at": row["created_at"],
                    "meta": json.loads(row["meta_json"] or "{}"),
                    "event_count": counts.get(session_id, 0),
                    "hash_chain_checked": checked,
                    "hash_chain_valid": self.verify_chain(session_id) if checked else None,
                }
            )
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

    def fork(
        self,
        source_session_id: str,
        at_seq: int,
        new_session_id: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        at_seq = int(at_seq)
        if source_session_id == new_session_id:
            raise ValueError("fork target must differ from source session")

        with self._lock, self._conn() as c:
            # The same write transaction validates the source/target boundary and copies the
            # prefix. This prevents another writer from extending the source between validation
            # and copy, and makes a partial target impossible if any copy step fails.
            c.execute("BEGIN IMMEDIATE")
            source_row = c.execute(
                "SELECT session_id FROM sessions WHERE session_id=?",
                (source_session_id,),
            ).fetchone()
            if source_row is None:
                raise KeyError(f"unknown source session: {source_session_id}")

            latest_row = c.execute(
                "SELECT seq FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (source_session_id,),
            ).fetchone()
            source_latest = int(latest_row["seq"]) if latest_row else 0
            if at_seq < 0 or at_seq > source_latest:
                raise ValueError(
                    f"fork seq {at_seq} outside source history 0..{source_latest}"
                )

            target_row = c.execute(
                "SELECT session_id FROM sessions WHERE session_id=?",
                (new_session_id,),
            ).fetchone()
            if target_row is not None:
                raise ValueError(f"fork target already exists: {new_session_id}")

            c.execute(
                "INSERT INTO sessions(session_id,parent_session_id,parent_seq,created_at,meta_json) VALUES(?,?,?,?,?)",
                (
                    new_session_id,
                    source_session_id,
                    at_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            cursor = c.execute(
                "SELECT * FROM events "
                "WHERE session_id=? AND seq<=? ORDER BY seq",
                (source_session_id, at_seq),
            )
            for row in cursor:
                event = self._event_from_row(row)
                self._append_in_connection(
                    c,
                    new_session_id,
                    event.event_type,
                    {
                        **event.payload,
                        "_forked_from": source_session_id,
                        "_source_seq": event.seq,
                    },
                )
