from __future__ import annotations

import pytest

from worldforge.runtime import EventStore


def test_fork_streams_only_requested_prefix(tmp_path, monkeypatch):
    path = tmp_path / "fork-prefix.db"
    store = EventStore(path)
    source_id = "source"
    target_id = "target"
    store.create_session(source_id, meta={"kind": "source"})
    for index in range(64):
        store.append(source_id, "world.state", {"tick": index})

    monkeypatch.setattr(
        store,
        "list_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fork must query only the requested prefix")
        ),
    )

    store.fork(source_id, 3, target_id, meta={"kind": "branch"})

    reader = EventStore(path)
    target_meta = reader.session_meta(target_id)
    assert target_meta is not None
    assert target_meta["parent_session_id"] == source_id
    assert target_meta["parent_seq"] == 3
    assert target_meta["meta"] == {"kind": "branch"}
    assert reader.latest_seq(target_id) == 3
    copied = reader.list_events(target_id)
    assert [event.payload["_source_seq"] for event in copied] == [1, 2, 3]
    assert all(event.payload["_forked_from"] == source_id for event in copied)
    assert reader.verify_chain(target_id) is True


def test_fork_rejects_out_of_range_seq_before_creating_target(tmp_path):
    store = EventStore(tmp_path / "fork-range.db")
    store.create_session("source")
    store.append("source", "run.started", {})

    with pytest.raises(ValueError, match="outside source history"):
        store.fork("source", 2, "target")

    assert store.session_meta("target") is None
    assert store.latest_seq("target") == 0


def test_fork_rejects_unknown_source_and_existing_target(tmp_path):
    store = EventStore(tmp_path / "fork-identity.db")

    with pytest.raises(KeyError, match="unknown source session"):
        store.fork("missing", 0, "target")
    assert store.session_meta("target") is None

    store.create_session("source")
    store.append("source", "run.started", {})
    store.create_session("target", meta={"keep": True})
    store.append("target", "existing.event", {"keep": True})

    with pytest.raises(ValueError, match="target already exists"):
        store.fork("source", 1, "target")

    assert store.latest_seq("target") == 1
    existing = store.next_event("target")
    assert existing is not None
    assert existing.event_type == "existing.event"
    assert existing.payload == {"keep": True}
    assert store.session_meta("target")["meta"] == {"keep": True}


def test_fork_allows_genesis_of_known_empty_source(tmp_path):
    store = EventStore(tmp_path / "fork-empty.db")
    store.create_session("empty-source")

    store.fork("empty-source", 0, "empty-target")

    target = store.session_meta("empty-target")
    assert target is not None
    assert target["parent_session_id"] == "empty-source"
    assert target["parent_seq"] == 0
    assert store.latest_seq("empty-target") == 0
    assert store.verify_chain("empty-target") is True
