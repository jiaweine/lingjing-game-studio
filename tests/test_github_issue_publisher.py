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
    assert second.updated is True
    assert [call["method"] for call in calls] == ["POST", "PATCH"]
    assert calls[0]["url"] == "https://api.github.com/repos/owner/game/issues/7/comments"
    assert calls[1]["url"] == "https://api.github.com/repos/owner/game/issues/comments/321"
    assert calls[0]["authorization"] == "Bearer server-secret"
    assert calls[0]["payload"] == {"body": "first summary"}
    assert calls[1]["payload"] == {"body": "updated summary"}
