from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from worldforge.security import Principal

from .github_repository_client import (
    GitHubRepositoryClient,
    GitHubRepositoryClientError,
    GitHubRepositoryClientUnavailable,
)


class GitHubCodeContextRequest(BaseModel):
    pull_request_number: int | None = Field(default=None, ge=1)
    commit_sha: str | None = Field(default=None, min_length=7, max_length=40)


def build_github_context_router(
    *,
    store,
    require_principal: Callable,
    client: GitHubRepositoryClient,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/conversations/{conversation_id}/external-links/{link_id}/github-context"
    )
    async def github_context_set(
        conversation_id: str,
        link_id: str,
        req: GitHubCodeContextRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        if principal.role == "viewer":
            raise HTTPException(403, "只读成员不能修改 GitHub 代码上下文")
        if (req.pull_request_number is None) == (req.commit_sha is None):
            raise HTTPException(400, "每次只能关联一个 PR 或一个 Commit")
        try:
            link = store.get_external_issue_link(
                link_id,
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务或 GitHub Issue 关联不存在") from exc
        if link.get("provider") != "github" or link.get("resource_type") != "issue":
            raise HTTPException(409, "当前外部关联不是 GitHub Issue")

        pull_request = None
        commit = None
        try:
            if req.pull_request_number is not None:
                resolved = await client.resolve_pull_request(
                    repository=str(link["repository"]),
                    pull_request_number=req.pull_request_number,
                )
                pull_request = resolved.as_dict()
            else:
                resolved_commit = await client.resolve_commit(
                    repository=str(link["repository"]),
                    commit_sha=str(req.commit_sha),
                )
                commit = resolved_commit.as_dict()
        except GitHubRepositoryClientUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except GitHubRepositoryClientError as exc:
            raise HTTPException(502, str(exc)) from exc

        try:
            updated = store.record_github_code_context(
                link_id,
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
                pull_request=pull_request,
                commit=commit,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        context = dict((updated.get("meta") or {}).get("github_context") or {})
        store.add_audit(
            request_id=getattr(request.state, "request_id", "github-context"),
            action="external_issue.github_context.update",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="external_issue_link",
            resource_id=link_id,
            payload={
                "repository": link["repository"],
                "pull_request_number": req.pull_request_number,
                "commit_sha": req.commit_sha,
                "head_commit_sha": context.get("head_commit_sha"),
                "selected_commit_sha": context.get("selected_commit_sha"),
            },
        )
        return {"ok": True, "link": updated, "github_context": context}

    return router
