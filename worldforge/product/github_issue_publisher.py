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
    recovered: bool = False


class GitHubIssuePublisher:
    """Server-side boundary for explicit GitHub Issue comment publishing.

    The credential is process configuration, never conversation data. The default API host is
    fixed to api.github.com so a task/link cannot turn this publisher into an arbitrary HTTP
    client. GitHub Enterprise support should be added through an administrator-controlled host
    allowlist rather than accepting a per-task URL.

    When an idempotency marker is supplied and local linkage metadata lost the previous comment
    id, the publisher can recover the comment created by this credential and update it rather than
    creating a duplicate comment.
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
        self._viewer_login: str | None = None

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

    @staticmethod
    def _error(response: httpx.Response, action: str) -> GitHubIssuePublisherError:
        detail = ""
        try:
            payload: dict[str, Any] = dict(response.json())
            detail = str(payload.get("message") or "")[:240]
        except (TypeError, ValueError):
            pass
        suffix = f": {detail}" if detail else ""
        return GitHubIssuePublisherError(
            f"GitHub {action}失败（HTTP {response.status_code}）{suffix}"
        )

    async def _viewer(self, client: httpx.AsyncClient) -> str:
        if self._viewer_login:
            return self._viewer_login
        response = await client.get(
            "https://api.github.com/user",
            headers=self._headers(),
        )
        if response.status_code >= 400:
            raise self._error(response, "身份检查")
        try:
            login = str(dict(response.json())["login"]).strip().lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubIssuePublisherError("GitHub 返回了无效的发布身份") from exc
        if not login:
            raise GitHubIssuePublisherError("GitHub 返回了空的发布身份")
        self._viewer_login = login
        return login

    async def _recover_comment_id(
        self,
        client: httpx.AsyncClient,
        *,
        repository: str,
        issue_number: int,
        idempotency_marker: str,
    ) -> int | None:
        login = await self._viewer(client)
        for page in range(1, 11):
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
                headers=self._headers(),
                params={"per_page": 100, "page": page},
            )
            if response.status_code >= 400:
                raise self._error(response, "幂等恢复")
            try:
                rows = list(response.json())
            except (TypeError, ValueError) as exc:
                raise GitHubIssuePublisherError("GitHub 返回了无效的评论列表") from exc
            for raw in rows:
                row = dict(raw or {})
                user = dict(row.get("user") or {})
                if str(user.get("login") or "").strip().lower() != login:
                    continue
                if idempotency_marker not in str(row.get("body") or ""):
                    continue
                try:
                    return int(row["id"])
                except (KeyError, TypeError, ValueError):
                    continue
            if len(rows) < 100:
                break
        return None

    async def publish_comment(
        self,
        *,
        repository: str,
        issue_number: int,
        body: str,
        existing_comment_id: int | None = None,
        idempotency_marker: str | None = None,
    ) -> PublishedIssueComment:
        repository = str(repository or "").strip().lower()
        if "/" not in repository or int(issue_number) < 1:
            raise GitHubIssuePublisherError("invalid GitHub issue target")
        body = str(body or "").strip()
        marker = str(idempotency_marker or "").strip()
        if marker and not (marker.startswith("<!--") and marker.endswith("-->")):
            raise GitHubIssuePublisherError("invalid GitHub idempotency marker")
        if marker and marker not in body:
            body = f"{body}\n\n{marker}"
        if not body:
            raise GitHubIssuePublisherError("GitHub comment body is empty")
        if len(body.encode("utf-8")) > 60_000:
            raise GitHubIssuePublisherError("GitHub comment body exceeds safe size limit")

        recovered = False
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                comment_id = existing_comment_id
                if comment_id is None and marker:
                    comment_id = await self._recover_comment_id(
                        client,
                        repository=repository,
                        issue_number=int(issue_number),
                        idempotency_marker=marker,
                    )
                    recovered = comment_id is not None

                updated = comment_id is not None
                if comment_id is not None:
                    url = (
                        "https://api.github.com/repos/"
                        f"{repository}/issues/comments/{int(comment_id)}"
                    )
                    method = "PATCH"
                else:
                    url = (
                        "https://api.github.com/repos/"
                        f"{repository}/issues/{int(issue_number)}/comments"
                    )
                    method = "POST"

                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json={"body": body},
                )
        except httpx.HTTPError as exc:
            raise GitHubIssuePublisherError("GitHub 发布请求失败") from exc

        if response.status_code >= 400:
            raise self._error(response, "发布")

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
            recovered=recovered,
        )
