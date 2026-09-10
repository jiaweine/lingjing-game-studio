from __future__ import annotations

from worldforge.runtime import EventStore


def test_verify_chain_streams_without_list_events(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "streaming-chain.db")
    session_id = "streaming-chain"
    store.create_session(session_id)
    for index in range(256):
        store.append(
            session_id,
            "trace.chunk",
            {"index": index, "payload": "x" * 4096},
        )

    monkeypatch.setattr(
        store,
        "list_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verify_chain must stream rows instead of materializing history")
        ),
    )

    assert store.verify_chain(session_id) is True


def test_verify_chain_detects_payload_tampering(tmp_path):
    store = EventStore(tmp_path / "tampered-chain.db")
    session_id = "tampered-chain"
    store.create_session(session_id)
    store.append(session_id, "run.started", {"seed": 7})
    store.append(session_id, "world.state", {"tick": 1, "hp": 88})
    store.append(session_id, "run.completed", {"status": "completed"})
    assert store.verify_chain(session_id) is True

    with store._conn() as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE session_id=? AND seq=?",
            ('{"hp":1,"tick":1}', session_id, 2),
        )

    assert store.verify_chain(session_id) is False


def test_verify_chain_detects_sequence_gap(tmp_path):
    store = EventStore(tmp_path / "gapped-chain.db")
    session_id = "gapped-chain"
    store.create_session(session_id)
    for index in range(4):
        store.append(session_id, "world.state", {"tick": index})
    assert store.verify_chain(session_id) is True

    with store._conn() as connection:
        connection.execute(
            "DELETE FROM events WHERE session_id=? AND seq=?",
            (session_id, 2),
        )

    assert store.verify_chain(session_id) is False


def test_verify_chain_rejects_unknown_session(tmp_path):
    store = EventStore(tmp_path / "unknown-chain.db")

    assert store.verify_chain("does-not-exist") is False

    store.create_session("known-empty")
    assert store.verify_chain("known-empty") is True
