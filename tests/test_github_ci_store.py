import pytest

from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def _store(tmp_path):
    return ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )


def _conversation_and_link(store):
    conversation = store.create_conversation(
        title="CI 修复验证",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    link = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="owner/game",
        issue_number=7,
    )
    return conversation, link


def test_ci_subscription_requires_code_context_and_exact_workflow(tmp_path):
    store = _store(tmp_path)
    conversation, link = _conversation_and_link(store)

    with pytest.raises(ValueError, match="PR 或 Commit"):
        store.set_github_ci_subscription(
            link["id"],
            conversation_id=conversation["id"],
            workspace_id=DEMO_WORKSPACE_ID,
            enabled=True,
            updated_by=DEMO_USER_ID,
            workflow_name="Build Game",
        )

    store.record_github_code_context(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        commit={
            "sha": "a" * 40,
            "url": f"https://github.com/owner/game/commit/{'a' * 40}",
            "message": "Fix boss phase",
        },
    )

    with pytest.raises(ValueError, match="workflow"):
        store.set_github_ci_subscription(
            link["id"],
            conversation_id=conversation["id"],
            workspace_id=DEMO_WORKSPACE_ID,
            enabled=True,
            updated_by=DEMO_USER_ID,
        )

    subscription = store.set_github_ci_subscription(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        enabled=True,
        updated_by=DEMO_USER_ID,
        workflow_name="Build Game",
    )
    assert subscription["enabled"] is True
    assert subscription["workflow_name"] == "Build Game"

    assert len(
        store.list_enabled_github_ci_routes(
            repository="owner/game",
            commit_sha="a" * 40,
            workflow_name="Build Game",
        )
    ) == 1
    assert store.list_enabled_github_ci_routes(
        repository="owner/game",
        commit_sha="a" * 40,
        workflow_name="Unit Tests",
    ) == []


def test_ci_filter_is_removed_with_external_link(tmp_path):
    store = _store(tmp_path)
    conversation, link = _conversation_and_link(store)
    store.record_github_code_context(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        commit={
            "sha": "b" * 40,
            "url": f"https://github.com/owner/game/commit/{'b' * 40}",
            "message": "Fix",
        },
    )
    store.set_github_ci_subscription(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        enabled=True,
        updated_by=DEMO_USER_ID,
        workflow_name="Build Game",
    )

    store.unlink_external_issue(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )

    with store.engine.connect() as connection:
        assert connection.execute(store.github_ci_workflow_filters.select()).fetchall() == []
        assert connection.execute(store.github_ci_subscriptions.select()).fetchall() == []
        assert connection.execute(store.github_code_contexts.select()).fetchall() == []
