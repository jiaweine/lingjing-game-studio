from __future__ import annotations

from worldforge.runtime.event_store import EventStore


def _populate(store: EventStore, session_id: str, count: int) -> None:
    store.create_session(session_id, meta={"workspace_id": "workspace-a"})
    for index in range(count):
        store.append(session_id, "world.state", {"tick": index})


def test_session_listing_defaults_to_unverified_lightweight_summary(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "sessions.db")
    _populate(store, "session-a", 5)
    _populate(store, "session-b", 3)

    def forbid_verification(*_args, **_kwargs):
        raise AssertionError("recent session listing must not verify complete hash chains")

    monkeypatch.setattr(store, "verify_chain", forbid_verification)
    rows = store.list_sessions(limit=10)
    by_id = {row["session_id"]: row for row in rows}

    assert by_id["session-a"]["event_count"] == 5
    assert by_id["session-b"]["event_count"] == 3
    assert by_id["session-a"]["hash_chain_checked"] is False
    assert by_id["session-a"]["hash_chain_valid"] is None
    assert by_id["session-b"]["hash_chain_checked"] is False
    assert by_id["session-b"]["hash_chain_valid"] is None


def test_session_listing_can_request_explicit_hash_chain_verification(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "sessions.db")
    _populate(store, "session-a", 2)
    checked: list[str] = []

    def verified(session_id: str) -> bool:
        checked.append(session_id)
        return True

    monkeypatch.setattr(store, "verify_chain", verified)
    rows = store.list_sessions(limit=10, verify_hash_chain=True)

    assert len(rows) == 1
    assert rows[0]["event_count"] == 2
    assert rows[0]["hash_chain_checked"] is True
    assert rows[0]["hash_chain_valid"] is True
    assert checked == ["session-a"]
