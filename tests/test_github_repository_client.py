import httpx
import pytest

from worldforge.product.github_repository_client import (
    GitHubRepositoryClient,
    GitHubRepositoryClientUnavailable,
)


@pytest.mark.asyncio
async def test_github_repository_client_requires_server_credential():
    client = GitHubRepositoryClient(token=None)
    with pytest.raises(GitHubRepositoryClientUnavailable, match="WORLDFORGE_GITHUB_TOKEN"):
        await client.resolve_pull_request(repository="owner/game", pull_request_number=12)


@pytest.mark.asyncio
async def test_github_repository_client_resolves_pull_and_commit_context():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(str(request.url))
        if str(request.url).endswith("/pulls/12"):
            return httpx.Response(
                200,
                json={
                    "number": 12,
                    "title": "Fix boss phase 2",
                    "html_url": "https://github.com/owner/game/pull/12",
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "a" * 40, "ref": "fix/boss"},
                    "base": {"sha": "b" * 40, "ref": "main"},
                },
            )
        if "/commits/abc1234" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "sha": "c" * 40,
                    "html_url": f"https://github.com/owner/game/commit/{'c' * 40}",
                    "commit": {"message": "Fix phase transition\n\nDetails"},
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = GitHubRepositoryClient(
        token="server-secret",
        transport=httpx.MockTransport(handler),
    )
    pull = await client.resolve_pull_request(
        repository="Owner/Game",
        pull_request_number=12,
    )
    commit = await client.resolve_commit(
        repository="owner/game",
        commit_sha="abc1234",
    )

    assert pull.as_dict() == {
        "number": 12,
        "title": "Fix boss phase 2",
        "url": "https://github.com/owner/game/pull/12",
        "state": "open",
        "merged": False,
        "head_sha": "a" * 40,
        "head_ref": "fix/boss",
        "base_sha": "b" * 40,
        "base_ref": "main",
    }
    assert commit.sha == "c" * 40
    assert commit.message == "Fix phase transition"
    assert calls == [
        "https://api.github.com/repos/owner/game/pulls/12",
        "https://api.github.com/repos/owner/game/commits/abc1234",
    ]
