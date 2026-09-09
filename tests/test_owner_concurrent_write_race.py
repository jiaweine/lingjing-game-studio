from __future__ import annotations

import threading
import time

from sqlalchemy import insert, select

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
            selected = [getattr(column, "name", None) for column in statement.selected_columns]
            if from_names == {"workspaces"} and selected == ["id"]:
                self._paused = True
                # Both vulnerable transactions have established the same SQLite read snapshot
                # before either one changes owner membership state.
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


def test_sqlite_concurrent_owner_demotion_and_removal_preserve_business_contract(tmp_path):
    database = tmp_path / "product.db"
    first = ConversationStore(database, tmp_path / "assets-a")
    second = ConversationStore(database, tmp_path / "assets-b")
    second_owner = "user-owner-b"
    with first.engine.begin() as connection:
        connection.execute(
            insert(first.users).values(
                id=second_owner,
                email="owner-b-race@example.invalid",
                name="Owner B",
                password_hash="!test",
                status="active",
                created_at=time.time(),
            )
        )
        connection.execute(
            insert(first.memberships).values(
                workspace_id=DEMO_WORKSPACE_ID,
                user_id=second_owner,
                role="owner",
                created_at=time.time(),
            )
        )

    barrier = threading.Barrier(2)
    first.engine = _EngineProxy(first.engine, barrier)
    second.engine = _EngineProxy(second.engine, barrier)
    outcomes: list[tuple[str, object]] = []
    outcomes_lock = threading.Lock()

    def demote_first_owner():
        try:
            value = first.set_member_role(DEMO_WORKSPACE_ID, DEMO_USER_ID, "member")
        except Exception as exc:  # assertion below checks the public failure contract
            outcome = ("error", exc)
        else:
            outcome = ("ok", value)
        with outcomes_lock:
            outcomes.append(outcome)

    def remove_second_owner():
        try:
            second.remove_member(DEMO_WORKSPACE_ID, second_owner)
        except Exception as exc:  # assertion below checks the public failure contract
            outcome = ("error", exc)
        else:
            outcome = ("ok", None)
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=demote_first_owner),
        threading.Thread(target=remove_second_owner),
    ]
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
    assert "所有者" in str(failures[0])

    with first.engine.connect() as connection:
        owners = connection.execute(
            select(first.memberships.c.user_id).where(
                (first.memberships.c.workspace_id == DEMO_WORKSPACE_ID)
                & (first.memberships.c.role == "owner")
            )
        ).fetchall()
    assert len(owners) == 1
