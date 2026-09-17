from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx


class GitHubIssuePublisherError(RuntimeError):
    pass


class GitHubIssuePublisherUnavailable(GitHubIssuePublisherError):
    pass


@dataclass(frozen=True)
class PublishedIssueComment:
    comment_id: int
    html_url: str
    updated: bool


class GitHubIssuePublisher:
    """Server-side boundary for explicit GitHub Issue comment publishing.

    The credential is process configuration, never conversation data. The default API host is
    fixed to api.github.com so a task/link cannot turn this publisher into an arbitrary HTTP
    client. GitHub Enterprise support should be added through an administrator-controlled host
    allowlist rather than accepting a per-task URL.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = str(token or "").strip() or None
        self._timeout_seconds = max(1.0, min(60.0, float(timeout_seconds)))
        self._transport = transport

    @classmethod
    def from_environment(cls) -> "GitHubIssuePublisher":
        return cls(os.getenv("WORLDFORGE_GITHUB_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise GitHubIssuePublisherUnavailable(
                "GitHub 发布未配置；请在服务端配置 WORLDFORGE_GITHUB_TOKEN"
            )
        return {
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {self._token}",
            "x-github-api-version": "2022-11-28",
            "content-type": "application/json",
            "user-agent": "lingjing-game-studio",
        }

    async def publish_comment(
        self,
        *,
        repository: str,
        issue_number: int,
        body: str,
        existing_comment_id: int | None = None,
    ) -> PublishedIssueComment:
        repository = str(repository or "").strip().lower()
        if "/" not in repository or int(issue_number) < 1:
            raise GitHubIssuePublisherError("invalid GitHub issue target")
        body = str(body or "").strip()
        if not body:
            raise GitHubIssuePublisherError("GitHub comment body is empty")
        if len(body.encode("utf-8")) > 60_000:
            raise GitHubIssuePublisherError("GitHub comment body exceeds safe size limit")

        updated = existing_comment_id is not None
        if existing_comment_id is not None:
            url = (
                "https://api.github.com/repos/"
                f"{repository}/issues/comments/{int(existing_comment_id)}"
            )
            method = "PATCH"
        else:
            url = (
                "https://api.github.com/repos/"
                f"{repository}/issues/{int(issue_number)}/comments"
            )
            method = "POST"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json={"body": body},
                )
        except httpx.HTTPError as exc:
            raise GitHubIssuePublisherError("GitHub 发布请求失败") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                payload: dict[str, Any] = dict(response.json())
                detail = str(payload.get("message") or "")[:240]
            except (TypeError, ValueError):
                pass
            suffix = f": {detail}" if detail else ""
            raise GitHubIssuePublisherError(
                f"GitHub 发布失败（HTTP {response.status_code}）{suffix}"
            )

        try:
            payload = dict(response.json())
            comment_id = int(payload["id"])
            html_url = str(payload["html_url"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubIssuePublisherError("GitHub 返回了无效的评论结果") from exc
        if not html_url.startswith("https://github.com/"):
            raise GitHubIssuePublisherError("GitHub 评论 URL 不符合预期")
        return PublishedIssueComment(
            comment_id=comment_id,
            html_url=html_url,
            updated=updated,
        )
