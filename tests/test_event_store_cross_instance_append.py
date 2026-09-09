from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from worldforge.runtime import EventStore


class _LatestCursorProxy:
    def __init__(self, cursor, connection):
        self._cursor = cursor
        self._connection = connection

    def fetchone(self):
        row = self._cursor.fetchone()
        if not self._connection.serialized:
            self._connection.barrier.wait(timeout=5)
        return row


class _ConnectionProxy:
    def __init__(self, connection, barrier):
        self._connection = connection
        self.barrier = barrier
        self.serialized = False

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).upper()
        if normalized == "BEGIN IMMEDIATE":
            self.serialized = True
        cursor = self._connection.execute(sql, params)
        if normalized.startswith(
            "SELECT SEQ, HASH FROM EVENTS WHERE SESSION_ID=? ORDER BY SEQ DESC LIMIT 1"
        ):
            return _LatestCursorProxy(cursor, self)
        return cursor


def test_cross_instance_append_serializes_sequence_allocation(tmp_path, monkeypatch):
    path = tmp_path / "shared-events.db"
    first = EventStore(path)
    second = EventStore(path)
    session_id = "shared-session"
    first.create_session(session_id)

    barrier = threading.Barrier(2)
    first_real_conn = first._conn
    second_real_conn = second._conn
    monkeypatch.setattr(
        first,
        "_conn",
        lambda: _ConnectionProxy(first_real_conn(), barrier),
    )
    monkeypatch.setattr(
        second,
        "_conn",
        lambda: _ConnectionProxy(second_real_conn(), barrier),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(first.append, session_id, "worker.event", {"worker": "a"}),
            pool.submit(second.append, session_id, "worker.event", {"worker": "b"}),
        ]
        events = [future.result(timeout=10) for future in futures]

    assert {event.seq for event in events} == {1, 2}
    reader = EventStore(path)
    assert reader.latest_seq(session_id) == 2
    assert reader.verify_chain(session_id) is True
    assert {event.payload["worker"] for event in reader.list_events(session_id)} == {"a", "b"}
