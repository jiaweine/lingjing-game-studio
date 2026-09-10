from __future__ import annotations

import time

import pytest
from sqlalchemy import insert, select, update

from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


class _ConnectionProxy:
    def __init__(self, connection, *, before_execute=None):
        self._connection = connection
        self._before_execute = before_execute

    def execute(self, statement, *args, **kwargs):
        if self._before_execute is not None:
            self._before_execute(self._connection, statement)
        return self._connection.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _EngineProxy:
    """Inject one deterministic competing write without changing production code timing."""

    def __init__(self, engine, *, before_begin=None, before_execute=None):
        self._engine = engine
        self._before_begin = before_begin
        self._before_execute = before_execute
        self._begun = False

    def begin(self):
        outer = self
        real_context = self._engine.begin()

        class _Context:
            def __enter__(self):
                if not outer._begun and outer._before_begin is not None:
                    outer._begun = True
                    outer._before_begin()
                connection = real_context.__enter__()
                return _ConnectionProxy(
                    connection,
                    before_execute=outer._before_execute,
                )

            def __exit__(self, exc_type, exc, tb):
                return real_context.__exit__(exc_type, exc, tb)

        return _Context()

    def connect(self):
        return self._engine.connect()

    def __getattr__(self, name):
        return getattr(self._engine, name)


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )


def test_losing_approval_resolution_cannot_overwrite_winner_conversation_state(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        "Approval race",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    approval = store.create_approval(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        action="conversation.delete",
        requested_by=DEMO_USER_ID,
    )
    real_engine = store.engine
    injected = False

    def competing_winner(connection, statement):
        nonlocal injected
        table = getattr(statement, "table", None)
        if injected or not getattr(statement, "is_update", False):
            return
        if getattr(table, "name", None) != "approval_requests":
            return
        injected = True
        now = time.time()
        # Simulate an approval request that won after this caller read `pending`, but before
        # this caller's compare-and-set. The winner also moved the conversation atomically.
        connection.execute(
            update(store.approval_requests)
            .where(store.approval_requests.c.id == approval["id"])
            .values(status="approved", resolved_by="winner", resolved_at=now)
        )
        connection.execute(
            update(store.conversations)
            .where(store.conversations.c.id == conversation["id"])
            .values(status="waiting_approval", updated_at=now)
        )

    store.engine = _EngineProxy(real_engine, before_execute=competing_winner)
    resolved = store.resolve_approval(
        approval["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
        approved=False,
    )

    assert injected is True
    assert resolved["status"] == "approved"
    assert resolved["resolved_by"] == "winner"
    assert store.get_conversation(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )["status"] == "waiting_approval"


def test_owner_demotion_rechecks_last_owner_inside_serialized_transaction(tmp_path):
    store = _store(tmp_path)
    second_owner = "user-owner-b"
    real_engine = store.engine
    with real_engine.begin() as connection:
        connection.execute(
            insert(store.users).values(
                id=second_owner,
                email="owner-b@example.invalid",
                name="Owner B",
                password_hash="!test",
                status="active",
                created_at=time.time(),
            )
        )
        connection.execute(
            insert(store.memberships).values(
                workspace_id=DEMO_WORKSPACE_ID,
                user_id=second_owner,
                role="owner",
                created_at=time.time(),
            )
        )

    def competing_demotion():
        # This happens after a vulnerable implementation has already observed two owners but
        # before it writes its own demotion. A correct implementation rechecks under the same
        # workspace lock/transaction and refuses to remove the final owner.
        with real_engine.begin() as connection:
            connection.execute(
                update(store.memberships)
                .where(
                    (store.memberships.c.workspace_id == DEMO_WORKSPACE_ID)
                    & (store.memberships.c.user_id == second_owner)
                )
                .values(role="member")
            )

    store.engine = _EngineProxy(real_engine, before_begin=competing_demotion)
    with pytest.raises(ValueError, match="至少保留一位所有者"):
        store.set_member_role(DEMO_WORKSPACE_ID, DEMO_USER_ID, "member")

    with store.engine.connect() as connection:
        owners = connection.execute(
            select(store.memberships.c.user_id).where(
                (store.memberships.c.workspace_id == DEMO_WORKSPACE_ID)
                & (store.memberships.c.role == "owner")
            )
        ).fetchall()
    assert [row[0] for row in owners] == [DEMO_USER_ID]
