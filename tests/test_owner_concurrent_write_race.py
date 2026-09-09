from __future__ import annotations

import threading
import time

from sqlalchemy import event, insert, select

from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


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

    def synchronize_write_intent(
        _connection,
        _clauseelement,
        _multiparams,
        _params,
        execution_options,
    ):
        if execution_options.get("_lingjing_sqlite_write_intent"):
            # Both callers reach the DB write-intent at the same time. SQLite must serialize
            # them before either caller is allowed to count owners and mutate membership.
            barrier.wait(timeout=5)

    event.listen(first.engine, "before_execute", synchronize_write_intent)
    event.listen(second.engine, "before_execute", synchronize_write_intent)
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
