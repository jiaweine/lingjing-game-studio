from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from worldforge.context.history_snapshot import build_history_snapshot
from worldforge.security import Principal


class GitHubCISubscriptionRequest(BaseModel):
    enabled: bool
    workflow_name: str | None = Field(default=None, max_length=240)


def _webhook_secret() -> bytes:
    secret = os.getenv("WORLDFORGE_GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "GitHub webhook secret 未配置")
    return secret.encode("utf-8")


def _verify_signature(body: bytes, signature: str | None) -> None:
    supplied = str(signature or "").strip()
    if not supplied.startswith("sha256="):
        raise HTTPException(401, "GitHub webhook 签名缺失")
    expected = "sha256=" + hmac.new(_webhook_secret(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "GitHub webhook 签名无效")


def _workflow_payload(payload: dict) -> tuple[str, int, str, str, str, str]:
    repository = str((payload.get("repository") or {}).get("full_name") or "").strip().lower()
    workflow = dict(payload.get("workflow_run") or {})
    try:
        run_id = int(workflow["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "GitHub workflow_run.id 无效") from exc
    workflow_name = str(workflow.get("name") or "").strip()
    head_sha = str(workflow.get("head_sha") or "").strip().lower()
    status = str(workflow.get("status") or "").strip().lower()
    conclusion = str(workflow.get("conclusion") or "").strip().lower()
    if not repository or not workflow_name or len(head_sha) != 40:
        raise HTTPException(400, "GitHub workflow payload 缺少 repository/workflow/head_sha")
    return repository, run_id, workflow_name, head_sha, status, conclusion


def build_github_ci_router(
    *,
    store,
    require_principal: Callable,
    schedule_job: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/conversations/{conversation_id}/external-links/{link_id}/ci-subscription"
    )
    def set_ci_subscription(
        conversation_id: str,
        link_id: str,
        req: GitHubCISubscriptionRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        if principal.role == "viewer":
            raise HTTPException(403, "只读成员不能修改 CI 自动验证")
        try:
            subscription = store.set_github_ci_subscription(
                link_id,
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
                enabled=req.enabled,
                updated_by=principal.user_id,
                workflow_name=req.workflow_name,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务或 GitHub Issue 关联不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.add_audit(
            request_id=getattr(request.state, "request_id", "github-ci-subscription"),
            action="external_issue.github_ci_subscription.update",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="external_issue_link",
            resource_id=link_id,
            payload={
                "enabled": subscription["enabled"],
                "workflow_name": subscription.get("workflow_name"),
            },
        )
        return subscription

    @router.post("/api/integrations/github/webhook")
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
        x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
        x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    ):
        body = await request.body()
        if len(body) > 1_000_000:
            raise HTTPException(413, "GitHub webhook payload 过大")
        _verify_signature(body, x_hub_signature_256)
        if str(x_github_event or "").strip() != "workflow_run":
            return {"ok": True, "ignored": True, "reason": "unsupported_event"}
        try:
            payload = dict(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(400, "GitHub webhook JSON 无效") from exc
        if str(payload.get("action") or "").strip().lower() != "completed":
            return {"ok": True, "ignored": True, "reason": "workflow_not_completed"}

        repository, run_id, workflow_name, head_sha, status, conclusion = _workflow_payload(payload)
        delivery_id = str(x_github_delivery or "").strip()
        try:
            delivery, is_new = store.begin_github_ci_delivery(
                delivery_id=delivery_id,
                repository=repository,
                head_sha=head_sha,
                workflow_run_id=run_id,
                workflow_name=workflow_name,
                conclusion=conclusion,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not is_new:
            return {
                "ok": True,
                "duplicate": True,
                "delivery_id": delivery_id,
                "status": delivery["status"],
            }

        if status != "completed" or conclusion != "success":
            store.complete_github_ci_delivery(
                delivery_id,
                matched_routes=0,
                enqueued_jobs=0,
            )
            return {"ok": True, "ignored": True, "reason": "workflow_not_successful"}

        routes = store.list_enabled_github_ci_routes(
            repository=repository,
            commit_sha=head_sha,
            workflow_name=workflow_name,
        )
        enqueued = 0
        errors: list[str] = []
        for route in routes:
            workspace_id = str(route["workspace_id"])
            conversation_id = str(route["conversation_id"])
            link_id = str(route["link_id"])
            try:
                subscription = store.get_github_ci_subscription(
                    link_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                )
                actor_id = str(subscription.get("updated_by") or "")
                membership = store.get_membership(workspace_id, actor_id) if actor_id else None
                if not membership or membership.get("role") == "viewer":
                    raise ValueError("开启自动验证的成员已无编辑权限")
                user = store.get_user(actor_id)
                conversation = store.get_conversation(
                    conversation_id,
                    workspace_id=workspace_id,
                )
                if conversation.get("archived_at") is not None:
                    raise ValueError("任务已归档")
                latest = store.latest_job(conversation_id, workspace_id=workspace_id)
                if not latest:
                    raise ValueError("任务没有可复用的历史执行")
                latest_payload = dict(latest.get("payload") or {})
                history = store.list_messages(conversation_id, workspace_id=workspace_id)
                project_context = copy.deepcopy(latest_payload.get("project_context") or {})
                scope = dict(project_context.get("scope") or {})
                scope["commit_ref"] = head_sha
                if route.get("head_ref"):
                    scope["branch_ref"] = str(route["head_ref"])
                project_context["scope"] = scope
                project_context["actor_id"] = actor_id
                text = (
                    f"GitHub CI workflow “{workflow_name}” 已成功（run {run_id}）。"
                    f"请按当前任务已有的复现条件，对 commit {head_sha} 重新执行修复验证。"
                    "只有项目级证据和 Verifier 都支持时，才可以标记为已验证。"
                )
                asset_ids = list(dict.fromkeys(latest_payload.get("asset_ids") or []))
                job_payload = {
                    "text": text,
                    "provider": str(latest_payload.get("provider") or "auto"),
                    "history_snapshot": build_history_snapshot(history),
                    "asset_ids": asset_ids,
                    "actor_id": actor_id,
                    "project_context": project_context,
                    "automation": {
                        "source": "github_ci",
                        "repository": repository,
                        "workflow_name": workflow_name,
                        "workflow_run_id": run_id,
                        "head_sha": head_sha,
                        "delivery_id": delivery_id,
                        "link_id": link_id,
                    },
                }
                _, job = store.create_message_job(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    content=text,
                    asset_ids=asset_ids,
                    job_payload=job_payload,
                    title_if_first=None,
                )
                principal = Principal(
                    user_id=actor_id,
                    workspace_id=workspace_id,
                    email=str(user["email"]),
                    role=str(membership["role"]),
                )
                await schedule_job(job, background_tasks, principal)
                store.add_event(
                    conversation_id,
                    "ci.revalidation.queued",
                    {
                        "job_id": job["id"],
                        "repository": repository,
                        "workflow_name": workflow_name,
                        "workflow_run_id": run_id,
                        "head_sha": head_sha,
                    },
                    workspace_id=workspace_id,
                )
                store.add_audit(
                    request_id=f"github:{delivery_id}"[:64],
                    action="github_ci.revalidation.enqueue",
                    workspace_id=workspace_id,
                    user_id=actor_id,
                    resource_type="job",
                    resource_id=job["id"],
                    payload={
                        "link_id": link_id,
                        "repository": repository,
                        "workflow_name": workflow_name,
                        "workflow_run_id": run_id,
                        "head_sha": head_sha,
                    },
                )
                enqueued += 1
            except (KeyError, ValueError) as exc:
                errors.append(f"{link_id}:{str(exc)[:240]}")

        store.complete_github_ci_delivery(
            delivery_id,
            matched_routes=len(routes),
            enqueued_jobs=enqueued,
            error="; ".join(errors) if errors else None,
        )
        return {
            "ok": not errors,
            "delivery_id": delivery_id,
            "matched_routes": len(routes),
            "enqueued_jobs": enqueued,
            "errors": errors,
        }

    return router
