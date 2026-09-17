import pytest

from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def _store(tmp_path):
    return ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )


def test_github_issue_link_is_durable_and_idempotent(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="Boss phase 2 偶发秒杀",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )

    first = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="Jiaweine/Lingjing-Game-Studio",
        issue_number=29,
        title="Push verified results back into GitHub/Jira/CI workflows",
    )
    second = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="jiaweine/lingjing-game-studio",
        issue_number=29,
        title="Updated issue title",
    )

    links = store.list_external_issue_links(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )

    assert first["id"] == second["id"]
    assert len(links) == 1
    assert links[0]["provider"] == "github"
    assert links[0]["resource_type"] == "issue"
    assert links[0]["repository"] == "jiaweine/lingjing-game-studio"
    assert links[0]["external_key"] == "29"
    assert links[0]["external_url"] == (
        "https://github.com/jiaweine/lingjing-game-studio/issues/29"
    )
    assert links[0]["external_title"] == "Updated issue title"
    assert links[0]["sync_state"] == "linked"
    assert "token" not in links[0]


def test_external_issue_link_can_be_removed(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="回归验证",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    link = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="owner/game",
        issue_number=17,
    )

    removed = store.unlink_external_issue(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )

    assert removed["id"] == link["id"]
    assert store.list_external_issue_links(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    ) == []


def test_github_issue_link_rejects_non_repository_identifiers(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="安全边界",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )

    with pytest.raises(ValueError, match="owner/repo"):
        store.link_github_issue(
            conversation["id"],
            workspace_id=DEMO_WORKSPACE_ID,
            created_by=DEMO_USER_ID,
            repository="https://github.com/owner/repo",
            issue_number=1,
        )

    with pytest.raises(ValueError, match="大于 0"):
        store.link_github_issue(
            conversation["id"],
            workspace_id=DEMO_WORKSPACE_ID,
            created_by=DEMO_USER_ID,
            repository="owner/repo",
            issue_number=0,
        )


def test_external_issue_metadata_rejects_provider_credentials(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="凭证不进任务数据",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )

    with pytest.raises(ValueError, match="token/secret/credential"):
        store.link_github_issue(
            conversation["id"],
            workspace_id=DEMO_WORKSPACE_ID,
            created_by=DEMO_USER_ID,
            repository="owner/repo",
            issue_number=9,
            meta={"provider": {"access_token": "never-store-this"}},
        )

    assert store.list_external_issue_links(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    ) == []
