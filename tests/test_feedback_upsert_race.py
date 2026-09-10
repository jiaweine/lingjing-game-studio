from __future__ import annotations

import time

from sqlalchemy import insert

from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


class _ConnectionProxy:
    def __init__(self, connection, before_execute):
        self._connection = connection
        self._before_execute = before_execute

    def execute(self, statement, *args, **kwargs):
        self._before_execute(self._connection, statement)
        return self._connection.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _EngineProxy:
    def __init__(self, engine, before_execute):
        self._engine = engine
        self._before_execute = before_execute

    def begin(self):
        outer = self
        context = self._engine.begin()

        class _Context:
            def __enter__(self):
                connection = context.__enter__()
                return _ConnectionProxy(connection, outer._before_execute)

            def __exit__(self, exc_type, exc, tb):
                return context.__exit__(exc_type, exc, tb)

        return _Context()

    def connect(self):
        return self._engine.connect()

    def __getattr__(self, name):
        return getattr(self._engine, name)


def test_feedback_upsert_survives_competing_first_insert(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conversation = store.create_conversation("feedback race")
    message = store.add_message(
        conversation["id"],
        "assistant",
        "candidate answer",
        {},
        workspace_id=DEMO_WORKSPACE_ID,
    )
    real_engine = store.engine
    injected = False

    def competing_insert(connection, statement):
        nonlocal injected
        table = getattr(statement, "table", None)
        if injected or getattr(table, "name", None) != "result_feedback":
            return
        if not (getattr(statement, "is_insert", False) or getattr(statement, "is_update", False)):
            return
        injected = True
        now = time.time()
        connection.execute(
            insert(store.result_feedback).values(
                message_id=message["id"],
                user_id=DEMO_USER_ID,
                workspace_id=DEMO_WORKSPACE_ID,
                conversation_id=conversation["id"],
                verdict="incorrect",
                evidence_useful=0,
                human_verified=0,
                note="competing write",
                created_at=now,
                updated_at=now,
            )
        )

    store.engine = _EngineProxy(real_engine, competing_insert)
    result = store.upsert_feedback(
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
        message_id=message["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
        note="latest write",
    )

    assert injected is True
    assert result["verdict"] == "correct"
    assert result["human_verified"] == 1
    assert result["note"] == "latest write"
    rows = store.list_feedback(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert len(rows) == 1
