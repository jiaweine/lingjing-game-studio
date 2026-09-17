from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def test_github_code_context_is_durable_and_queryable_for_ci_routing(tmp_path):
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    conversation = store.create_conversation(
        title="Boss 修复验证",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    link = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="Owner/Game",
        issue_number=7,
    )
    pushed = store.record_external_issue_push(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        push_kind="reproduction",
        comment_id=123,
        comment_url="https://github.com/owner/game/issues/7#issuecomment-123",
    )

    updated = store.record_github_code_context(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        pull_request={
            "number": 12,
            "title": "Fix boss phase 2",
            "url": "https://github.com/owner/game/pull/12",
            "state": "open",
            "merged": False,
            "head_sha": "a" * 40,
            "head_ref": "fix/boss",
            "base_sha": "b" * 40,
            "base_ref": "main",
        },
    )
    updated = store.record_github_code_context(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        commit={
            "sha": "c" * 40,
            "url": f"https://github.com/owner/game/commit/{'c' * 40}",
            "message": "Fix phase transition",
        },
    )

    assert updated["sync_state"] == pushed["sync_state"] == "reproduction_pushed"
    context = updated["meta"]["github_context"]
    assert context["pull_request"]["number"] == 12
    assert context["head_commit_sha"] == "a" * 40
    assert context["commit"]["sha"] == "c" * 40
    assert context["selected_commit_sha"] == "c" * 40
    assert updated["meta"]["github_comments"]["reproduction"]["id"] == 123

    route = store.get_github_code_context_route(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )
    assert route["repository"] == "owner/game"
    assert route["pull_request_number"] == 12
    assert route["head_sha"] == "a" * 40
    assert route["selected_commit_sha"] == "c" * 40

    head_routes = store.find_github_commit_routes(
        repository="OWNER/GAME",
        commit_sha="a" * 40,
    )
    selected_routes = store.find_github_commit_routes(
        repository="owner/game",
        commit_sha="c" * 40,
        workspace_id=DEMO_WORKSPACE_ID,
    )
    assert [item["conversation_id"] for item in head_routes] == [conversation["id"]]
    assert [item["link_id"] for item in selected_routes] == [link["id"]]

    reloaded = store.get_external_issue_link(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )
    assert reloaded["meta"]["github_context"] == context

    store.unlink_external_issue(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )
    assert store.find_github_commit_routes(
        repository="owner/game",
        commit_sha="a" * 40,
    ) == []
