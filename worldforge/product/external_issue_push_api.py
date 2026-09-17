from __future__ import annotations

from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from worldforge.security import Principal

from .external_issue_summary import build_github_issue_summary
from .github_issue_publisher import (
    GitHubIssuePublisher,
    GitHubIssuePublisherError,
    GitHubIssuePublisherUnavailable,
)


class ExternalIssuePushRequest(BaseModel):
    kind: Literal["reproduction", "verification"]


def build_external_issue_push_router(
    *,
    store,
    require_editor: Callable,
    publisher: GitHubIssuePublisher,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/conversations/{conversation_id}/external-links/{link_id}/push"
    )
    async def push_external_issue_summary(
        conversation_id: str,
        link_id: str,
        req: ExternalIssuePushRequest,
        request: Request,
        principal: Principal = Depends(require_editor),
    ):
        try:
            conversation = store.get_conversation(
                conversation_id, workspace_id=principal.workspace_id
            )
            link = store.get_external_issue_link(
                link_id,
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
            )
            messages = store.list_messages(
                conversation_id, workspace_id=principal.workspace_id
            )
        except KeyError as exc:
            raise HTTPException(404, "任务或 GitHub Issue 关联不存在") from exc

        if link.get("provider") != "github" or link.get("resource_type") != "issue":
            raise HTTPException(409, "当前外部关联不是可发布的 GitHub Issue")

        try:
            body = build_github_issue_summary(
                conversation=conversation,
                messages=messages,
                push_kind=req.kind,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        meta = dict(link.get("meta") or {})
        comments = dict(meta.get("github_comments") or {})
        previous = dict(comments.get(req.kind) or {})
        previous_comment_id = previous.get("id")
        try:
            existing_comment_id = (
                int(previous_comment_id) if previous_comment_id is not None else None
            )
        except (TypeError, ValueError):
            existing_comment_id = None

        try:
            published = await publisher.publish_comment(
                repository=str(link["repository"]),
                issue_number=int(link["external_key"]),
                body=body,
                existing_comment_id=existing_comment_id,
            )
        except GitHubIssuePublisherUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except GitHubIssuePublisherError as exc:
            raise HTTPException(502, str(exc)) from exc

        updated_link = store.record_external_issue_push(
            link_id,
            conversation_id=conversation_id,
            workspace_id=principal.workspace_id,
            push_kind=req.kind,
            comment_id=published.comment_id,
            comment_url=published.html_url,
        )
        store.add_audit(
            request_id=getattr(request.state, "request_id", "external-issue-push"),
            action="external_issue.push",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="external_issue_link",
            resource_id=link_id,
            payload={
                "provider": "github",
                "repository": link["repository"],
                "external_key": link["external_key"],
                "push_kind": req.kind,
                "comment_id": published.comment_id,
                "updated": published.updated,
            },
        )
        return {
            "ok": True,
            "kind": req.kind,
            "updated": published.updated,
            "comment_id": published.comment_id,
            "comment_url": published.html_url,
            "link": updated_link,
        }

    return router
