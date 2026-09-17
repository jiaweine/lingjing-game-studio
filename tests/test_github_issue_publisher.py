import json

import httpx
import pytest

from worldforge.product.github_issue_publisher import (
    GitHubIssuePublisher,
    GitHubIssuePublisherUnavailable,
)


@pytest.mark.asyncio
async def test_github_publisher_requires_server_side_credential():
    publisher = GitHubIssuePublisher(token=None)
    with pytest.raises(GitHubIssuePublisherUnavailable, match="WORLDFORGE_GITHUB_TOKEN"):
        await publisher.publish_comment(
            repository="owner/game",
            issue_number=7,
            body="test",
        )


@pytest.mark.asyncio
async def test_github_publisher_posts_then_updates_comment():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "payload": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200 if request.method == "PATCH" else 201,
            json={
                "id": 321,
                "html_url": "https://github.com/owner/game/issues/7#issuecomment-321",
            },
        )

    publisher = GitHubIssuePublisher(
        token="server-secret",
        transport=httpx.MockTransport(handler),
    )
    first = await publisher.publish_comment(
        repository="Owner/Game",
        issue_number=7,
        body="first summary",
    )
    second = await publisher.publish_comment(
        repository="owner/game",
        issue_number=7,
        body="updated summary",
        existing_comment_id=first.comment_id,
    )

    assert first.comment_id == 321
    assert first.updated is False
    assert first.recovered is False
    assert second.updated is True
    assert second.recovered is False
    assert [call["method"] for call in calls] == ["POST", "PATCH"]
    assert calls[0]["url"] == "https://api.github.com/repos/owner/game/issues/7/comments"
    assert calls[1]["url"] == "https://api.github.com/repos/owner/game/issues/comments/321"
    assert calls[0]["authorization"] == "Bearer server-secret"
    assert calls[0]["payload"] == {"body": "first summary"}
    assert calls[1]["payload"] == {"body": "updated summary"}


@pytest.mark.asyncio
async def test_github_publisher_recovers_own_marked_comment_before_posting_duplicate():
    calls = []
    marker = "<!-- lingjing-external-push:abc123 -->"

    async def handler(request: httpx.Request):
        calls.append((request.method, str(request.url)))
        if str(request.url) == "https://api.github.com/user":
            return httpx.Response(200, json={"login": "lingjing-bot"})
        if request.method == "GET" and "/issues/7/comments" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 111,
                        "body": f"spoof {marker}",
                        "user": {"login": "someone-else"},
                    },
                    {
                        "id": 321,
                        "body": f"old summary\n\n{marker}",
                        "user": {"login": "lingjing-bot"},
                    },
                ],
            )
        if request.method == "PATCH":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["body"].endswith(marker)
            return httpx.Response(
                200,
                json={
                    "id": 321,
                    "html_url": "https://github.com/owner/game/issues/7#issuecomment-321",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    publisher = GitHubIssuePublisher(
        token="server-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await publisher.publish_comment(
        repository="owner/game",
        issue_number=7,
        body="recovered summary",
        idempotency_marker=marker,
    )

    assert result.comment_id == 321
    assert result.updated is True
    assert result.recovered is True
    assert not any(method == "POST" for method, _ in calls)
    assert calls[-1] == (
        "PATCH",
        "https://api.github.com/repos/owner/game/issues/comments/321",
    )
