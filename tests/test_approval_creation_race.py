from __future__ import annotations

import threading

from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


class _ConnectionProxy:
    def __init__(self, connection, barrier):
        self._connection = connection
        self._barrier = barrier
        self._paused = False

    def execute(self, statement, *args, **kwargs):
        result = self._connection.execute(statement, *args, **kwargs)
        if not self._paused and getattr(statement, "is_select", False):
            from_names = {
                getattr(item, "name", None)
                for item in statement.get_final_froms()
            }
            if "approval_requests" in from_names:
                self._paused = True
                # Both transactions have observed no existing pending/approved request.
                self._barrier.wait(timeout=5)
        return result

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _EngineProxy:
    def __init__(self, engine, barrier):
        self._engine = engine
        self._barrier = barrier

    def begin(self):
        outer = self
        context = self._engine.begin()

        class _Context:
            def __enter__(self):
                return _ConnectionProxy(context.__enter__(), outer._barrier)

            def __exit__(self, exc_type, exc, tb):
                return context.__exit__(exc_type, exc, tb)

        return _Context()

    def connect(self):
        return self._engine.connect()

    def __getattr__(self, name):
        return getattr(self._engine, name)


def test_sqlite_concurrent_approval_creation_preserves_single_pending_request(tmp_path):
    database = tmp_path / "product.db"
    first = ConversationStore(database, tmp_path / "assets-a")
    second = ConversationStore(database, tmp_path / "assets-b")
    conversation = first.create_conversation("approval creation race")
    barrier = threading.Barrier(2)
    first.engine = _EngineProxy(first.engine, barrier)
    second.engine = _EngineProxy(second.engine, barrier)
    outcomes: list[tuple[str, object]] = []
    outcomes_lock = threading.Lock()

    def submit(store):
        try:
            result = store.create_approval(
                workspace_id=DEMO_WORKSPACE_ID,
                conversation_id=conversation["id"],
                action="conversation.delete",
                requested_by=DEMO_USER_ID,
            )
        except Exception as exc:  # assertions below verify the public conflict contract
            outcome = ("error", exc)
        else:
            outcome = ("ok", result)
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=submit, args=(first,)), threading.Thread(target=submit, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    successes = [value for status, value in outcomes if status == "ok"]
    failures = [value for status, value in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ValueError)
    assert "审批" in str(failures[0])

    with first.engine.connect() as connection:
        pending = connection.execute(
            first.approval_requests.select().where(
                (first.approval_requests.c.workspace_id == DEMO_WORKSPACE_ID)
                & (first.approval_requests.c.conversation_id == conversation["id"])
                & (first.approval_requests.c.action == "conversation.delete")
                & (first.approval_requests.c.status == "pending")
            )
        ).fetchall()
    assert len(pending) == 1
