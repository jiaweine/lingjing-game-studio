from fastapi.testclient import TestClient

from worldforge.api.app import app, product_store
from worldforge.product.control import github_repository_client
from worldforge.product.github_repository_client import GitHubPullRequestContext


def _linked_issue():
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "PR 上下文", "scene": "regression"},
    ).json()
    link = client.post(
        f"/api/conversations/{conversation['id']}/external-links",
        json={"repository": "owner/game", "issue_number": 7},
    ).json()
    return client, conversation, link


def test_github_context_requires_exactly_one_code_reference():
    client, conversation, link = _linked_issue()
    endpoint = (
        f"/api/conversations/{conversation['id']}/external-links/{link['id']}/github-context"
    )
    assert client.post(endpoint, json={}).status_code == 400
    assert client.post(
        endpoint,
        json={"pull_request_number": 12, "commit_sha": "abc1234"},
    ).status_code == 400


def test_github_context_resolves_pr_on_server_and_persists_indexed_head_sha(monkeypatch):
    client, conversation, link = _linked_issue()
    calls = []

    async def fake_resolve_pull_request(*, repository, pull_request_number):
        calls.append((repository, pull_request_number))
        return GitHubPullRequestContext(
            number=12,
            title="Fix boss phase 2",
            url="https://github.com/owner/game/pull/12",
            state="open",
            merged=False,
            head_sha="a" * 40,
            head_ref="fix/boss",
            base_sha="b" * 40,
            base_ref="main",
        )

    monkeypatch.setattr(
        github_repository_client,
        "resolve_pull_request",
        fake_resolve_pull_request,
    )
    endpoint = (
        f"/api/conversations/{conversation['id']}/external-links/{link['id']}/github-context"
    )
    response = client.post(endpoint, json={"pull_request_number": 12})

    assert response.status_code == 200
    assert calls == [("owner/game", 12)]
    context = response.json()["github_context"]
    assert context["pull_request"]["number"] == 12
    assert context["head_commit_sha"] == "a" * 40

    persisted = client.get(
        f"/api/conversations/{conversation['id']}/external-links"
    ).json()[0]
    assert persisted["meta"]["github_context"] == context

    route = product_store.get_github_code_context_route(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=conversation["workspace_id"],
    )
    assert route["repository"] == "owner/game"
    assert route["pull_request_number"] == 12
    assert route["head_sha"] == "a" * 40
    assert product_store.find_github_commit_routes(
        repository="owner/game",
        commit_sha="a" * 40,
        workspace_id=conversation["workspace_id"],
    )[0]["conversation_id"] == conversation["id"]
