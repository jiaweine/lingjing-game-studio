from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from worldforge.security import Principal


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DELIVERY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _webhook_secret() -> bytes:
    value = os.getenv("WORLDFORGE_GITHUB_WEBHOOK_SECRET", "").strip()
    if not value:
        raise HTTPException(
            503,
            "GitHub webhook 未配置；请在服务端配置 WORLDFORGE_GITHUB_WEBHOOK_SECRET",
        )
    return value.encode("utf-8")


def _verify_signature(body: bytes, signature: str | None) -> None:
    signature = str(signature or "").strip().lower()
    if not signature.startswith("sha256="):
        raise HTTPException(401, "GitHub webhook 签名缺失")
    expected = "sha256=" + hmac.new(_webhook_secret(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "GitHub webhook 签名无效")


def _execution_principal(store, match: dict, latest_job: dict) -> Principal | None:
    conversation = store.get_conversation(
        match["conversation_id"], workspace_id=match["workspace_id"]
    )
    project_context = dict((latest_job.get("payload") or {}).get("project_context") or {})
    actor_id = str(project_context.get("actor_id") or conversation.get("created_by") or "")
    if not actor_id:
        return None
    membership = store.get_membership(match["workspace_id"], actor_id)
    if not membership:
        return None
    try:
        user = store.get_user(actor_id)
    except KeyError:
        return None
    if str(user.get("status") or "") != "active":
        return None
    return Principal(
        user_id=actor_id,
        workspace_id=match["workspace_id"],
        email=str(user.get("email") or ""),
        role=str(membership.get("role") or "member"),
    )


def build_github_ci_webhook_router(
    *,
    store,
    schedule_retry: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.post("/integrations/github/webhook")
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        body = await request.body()
        if len(body) > 1_048_576:
            raise HTTPException(413, "GitHub webhook payload 过大")
        _verify_signature(body, request.headers.get("x-hub-signature-256"))

        delivery_id = str(request.headers.get("x-github-delivery") or "").strip()
        event = str(request.headers.get("x-github-event") or "").strip().lower()
        if not _DELIVERY_RE.fullmatch(delivery_id):
            raise HTTPException(400, "GitHub delivery id 无效")
        if not event:
            raise HTTPException(400, "GitHub event 缺失")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "GitHub webhook JSON 无效") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "GitHub webhook payload 无效")

        action = str(payload.get("action") or "")[:64] or None
        repository = str((payload.get("repository") or {}).get("full_name") or "").lower() or None
        workflow = dict(payload.get("workflow_run") or {})
        head_sha = str(workflow.get("head_sha") or "").lower() or None
        conclusion = str(workflow.get("conclusion") or "")[:32] or None
        workflow_run_id = str(workflow.get("id") or "")[:64] or None
        workflow_name = str(workflow.get("name") or "")[:240] or None
        workflow_url = str(workflow.get("html_url") or "")[:2000] or None

        claimed = store.begin_github_webhook_delivery(
            delivery_id=delivery_id,
            event=event,
            action=action,
            repository=repository,
            head_sha=head_sha,
            workflow_run_id=workflow_run_id,
            workflow_name=workflow_name,
            workflow_url=workflow_url,
            conclusion=conclusion,
        )
        if not claimed:
            return {"ok": True, "duplicate": True, "delivery_id": delivery_id}

        if event != "workflow_run":
            store.complete_github_webhook_delivery(delivery_id, status="ignored_event")
            return {"ok": True, "ignored": "event", "delivery_id": delivery_id}
        if action != "completed" or conclusion != "success":
            store.complete_github_webhook_delivery(delivery_id, status="ignored_result")
            return {"ok": True, "ignored": "result", "delivery_id": delivery_id}
        if not repository or not head_sha or not _FULL_SHA_RE.fullmatch(head_sha):
            store.complete_github_webhook_delivery(delivery_id, status="invalid_payload")
            raise HTTPException(400, "GitHub workflow_run 缺少有效 repository/head_sha")

        matches = store.find_github_ci_matches(repository=repository, head_sha=head_sha)
        unique_matches: dict[tuple[str, str], dict] = {}
        for match in matches:
            key = (str(match["workspace_id"]), str(match["conversation_id"]))
            unique_matches.setdefault(key, match)

        trigger = {
            "source": "github_workflow_run",
            "delivery_id": delivery_id,
            "repository": repository,
            "head_sha": head_sha,
            "workflow_run_id": workflow_run_id,
            "workflow_name": workflow_name,
            "workflow_url": workflow_url,
            "conclusion": conclusion,
        }
        enqueued = 0
        deferred = 0
        for match in unique_matches.values():
            latest = store.latest_job(
                match["conversation_id"], workspace_id=match["workspace_id"]
            )
            if not latest:
                deferred += 1
                continue
            principal = _execution_principal(store, match, latest)
            if principal is None:
                deferred += 1
                continue
            try:
                job = store.enqueue_ci_revalidation(
                    workspace_id=match["workspace_id"],
                    conversation_id=match["conversation_id"],
                    trigger=trigger,
                )
            except ValueError:
                deferred += 1
                continue
            store.add_event(
                match["conversation_id"],
                "ci.revalidation.queued",
                {
                    "job_id": job["id"],
                    "repository": repository,
                    "head_sha": head_sha,
                    "workflow_run_id": workflow_run_id,
                    "workflow_name": workflow_name,
                    "workflow_url": workflow_url,
                },
                workspace_id=match["workspace_id"],
            )
            store.add_audit(
                request_id=f"github:{delivery_id}"[:64],
                action="github_ci.revalidation.enqueue",
                workspace_id=match["workspace_id"],
                user_id=principal.user_id,
                resource_type="conversation",
                resource_id=match["conversation_id"],
                payload={
                    "job_id": job["id"],
                    "repository": repository,
                    "head_sha": head_sha,
                    "workflow_run_id": workflow_run_id,
                },
            )
            await schedule_retry(job, background_tasks, principal)
            enqueued += 1

        matched = len(unique_matches)
        status = "enqueued" if enqueued else ("deferred" if matched else "no_match")
        store.complete_github_webhook_delivery(
            delivery_id,
            status=status,
            matched_count=matched,
            enqueued_count=enqueued,
        )
        return {
            "ok": True,
            "delivery_id": delivery_id,
            "matched": matched,
            "enqueued": enqueued,
            "deferred": deferred,
        }

    return router
