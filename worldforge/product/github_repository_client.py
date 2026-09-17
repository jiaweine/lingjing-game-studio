from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any

import httpx


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


class GitHubRepositoryClientError(RuntimeError):
    pass


class GitHubRepositoryClientUnavailable(GitHubRepositoryClientError):
    pass


@dataclass(frozen=True)
class GitHubPullRequestContext:
    number: int
    title: str
    url: str
    state: str
    merged: bool
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "merged": self.merged,
            "head_sha": self.head_sha,
            "head_ref": self.head_ref,
            "base_sha": self.base_sha,
            "base_ref": self.base_ref,
        }


@dataclass(frozen=True)
class GitHubCommitContext:
    sha: str
    url: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"sha": self.sha, "url": self.url, "message": self.message}


class GitHubRepositoryClient:
    """Read-only server-side GitHub repository context boundary."""

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
    def from_environment(cls) -> "GitHubRepositoryClient":
        return cls(os.getenv("WORLDFORGE_GITHUB_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise GitHubRepositoryClientUnavailable(
                "GitHub 代码上下文未配置；请在服务端配置 WORLDFORGE_GITHUB_TOKEN"
            )
        return {
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {self._token}",
            "x-github-api-version": "2022-11-28",
            "user-agent": "lingjing-game-studio",
        }

    @staticmethod
    def _repository(repository: str) -> str:
        value = str(repository or "").strip().strip("/").lower()
        if not _REPOSITORY_RE.fullmatch(value):
            raise GitHubRepositoryClientError("invalid GitHub repository")
        return value

    @staticmethod
    def _error(response: httpx.Response, action: str) -> GitHubRepositoryClientError:
        detail = ""
        try:
            detail = str(dict(response.json()).get("message") or "")[:240]
        except (TypeError, ValueError):
            pass
        suffix = f": {detail}" if detail else ""
        return GitHubRepositoryClientError(
            f"GitHub {action}失败（HTTP {response.status_code}）{suffix}"
        )

    async def _get(self, url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GitHubRepositoryClientError("GitHub 代码上下文请求失败") from exc
        if response.status_code >= 400:
            raise self._error(response, "代码上下文读取")
        try:
            return dict(response.json())
        except (TypeError, ValueError) as exc:
            raise GitHubRepositoryClientError("GitHub 返回了无效的代码上下文") from exc

    async def resolve_pull_request(
        self,
        *,
        repository: str,
        pull_request_number: int,
    ) -> GitHubPullRequestContext:
        repository = self._repository(repository)
        number = int(pull_request_number)
        if number < 1:
            raise GitHubRepositoryClientError("invalid GitHub pull request number")
        payload = await self._get(
            f"https://api.github.com/repos/{repository}/pulls/{number}"
        )
        try:
            head = dict(payload["head"])
            base = dict(payload["base"])
            html_url = str(payload["html_url"])
            if not html_url.startswith(f"https://github.com/{repository}/pull/"):
                raise ValueError("unexpected pull request url")
            return GitHubPullRequestContext(
                number=int(payload["number"]),
                title=str(payload.get("title") or "")[:240],
                url=html_url,
                state=str(payload.get("state") or "unknown")[:32],
                merged=bool(payload.get("merged")),
                head_sha=str(head["sha"]).lower(),
                head_ref=str(head.get("ref") or "")[:240],
                base_sha=str(base["sha"]).lower(),
                base_ref=str(base.get("ref") or "")[:240],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubRepositoryClientError("GitHub 返回了无效的 PR 上下文") from exc

    async def resolve_commit(
        self,
        *,
        repository: str,
        commit_sha: str,
    ) -> GitHubCommitContext:
        repository = self._repository(repository)
        requested = str(commit_sha or "").strip()
        if not _SHA_RE.fullmatch(requested):
            raise GitHubRepositoryClientError("commit SHA 必须是 7-40 位十六进制")
        payload = await self._get(
            f"https://api.github.com/repos/{repository}/commits/{requested}"
        )
        try:
            sha = str(payload["sha"]).lower()
            html_url = str(payload["html_url"])
            commit = dict(payload.get("commit") or {})
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("unexpected commit sha")
            if not html_url.startswith(f"https://github.com/{repository}/commit/"):
                raise ValueError("unexpected commit url")
            message = str(commit.get("message") or "").splitlines()[0][:240]
            return GitHubCommitContext(sha=sha, url=html_url, message=message)
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubRepositoryClientError("GitHub 返回了无效的 Commit 上下文") from exc
