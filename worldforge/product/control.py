from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from worldforge.security import Principal


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    assigned_to: str | None = None
    pinned: bool | None = None


class InviteCreate(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    role: str = "member"


class MemberRoleUpdate(BaseModel):
    role: str


class ApprovalResolve(BaseModel):
    approved: bool


class FeedbackUpdate(BaseModel):
    verdict: str
    evidence_useful: bool | None = None
    human_verified: bool = False
    note: str = Field(default="", max_length=2000)


class ProductEventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    conversation_id: str | None = None
    payload: dict = Field(default_factory=dict)


ALLOWED_PRODUCT_EVENTS = {
    "task.search",
    "evidence.open",
    "result.copy",
    "deliverable.copy",
    "task.retry",
    "task.archive",
    "task.restore",
    "task.handoff",
}


def build_control_router(
    *,
    store,
    storage,
    require_principal: Callable,
    session_response: Callable,
    schedule_retry: Callable,
) -> APIRouter:
    router = APIRouter()

    def require_manager(principal: Principal) -> None:
        if principal.role not in {"owner", "admin"}:
            raise HTTPException(403, "只有工作空间管理员可以执行此操作")

    def audit(request: Request, principal: Principal, action: str, resource_type: str, resource_id: str, payload: dict | None = None) -> None:
        store.add_audit(
            request_id=getattr(request.state, "request_id", "product-control"),
            action=action,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
        )

    @router.get("/api/workspaces")
    def workspace_list(principal: Principal = Depends(require_principal)):
        return store.list_user_workspaces(principal.user_id)

    @router.post("/api/workspaces/{workspace_id}/switch")
    def workspace_switch(
        workspace_id: str,
        response: Response,
        principal: Principal = Depends(require_principal),
    ):
        membership = store.get_membership(workspace_id, principal.user_id)
        if not membership:
            raise HTTPException(403, "你不是该工作空间成员")
        user = store.get_user(principal.user_id)
        switched = Principal(
            user_id=principal.user_id,
            workspace_id=workspace_id,
            email=user["email"],
            role=membership["role"],
        )
        return session_response(switched, response)

    @router.get("/api/workspace/members")
    def member_list(principal: Principal = Depends(require_principal)):
        return store.list_members(principal.workspace_id)

    @router.patch("/api/workspace/members/{user_id}")
    def member_role_update(
        user_id: str,
        req: MemberRoleUpdate,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        current = store.get_membership(principal.workspace_id, user_id)
        if principal.role != "owner" and current and (current["role"] == "owner" or req.role == "owner"):
            raise HTTPException(403, "只有所有者可以变更所有者角色")
        try:
            row = store.set_member_role(principal.workspace_id, user_id, req.role)
        except KeyError as exc:
            raise HTTPException(404, "成员不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit(request, principal, "member.role.update", "user", user_id, {"role": req.role})
        return row

    @router.delete("/api/workspace/members/{user_id}")
    def member_remove(
        user_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        current = store.get_membership(principal.workspace_id, user_id)
        if principal.role != "owner" and current and current["role"] == "owner":
            raise HTTPException(403, "只有所有者可以移除所有者")
        try:
            store.remove_member(principal.workspace_id, user_id)
        except KeyError as exc:
            raise HTTPException(404, "成员不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit(request, principal, "member.remove", "user", user_id)
        return {"ok": True}

    @router.get("/api/workspace/invites")
    def invite_list(principal: Principal = Depends(require_principal)):
        require_manager(principal)
        return store.list_invites(principal.workspace_id)

    @router.post("/api/workspace/invites")
    def invite_create(
        req: InviteCreate,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        try:
            invite = store.create_invite(
                workspace_id=principal.workspace_id,
                created_by=principal.user_id,
                email=req.email,
                role=req.role,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit(request, principal, "invite.create", "invite", invite["id"], {"role": invite["role"], "email": invite.get("email")})
        return invite

    @router.delete("/api/workspace/invites/{invite_id}")
    def invite_revoke(
        invite_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        try:
            invite = store.revoke_invite(invite_id, principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "邀请不存在或已使用") from exc
        audit(request, principal, "invite.revoke", "invite", invite_id)
        return invite

    @router.post("/api/invites/{token}/accept")
    def invite_accept(
        token: str,
        response: Response,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            membership = store.accept_invite(token, principal.user_id)
            invite = store.get_invite(token)
        except KeyError as exc:
            raise HTTPException(404, "邀请不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        switched = Principal(
            user_id=principal.user_id,
            workspace_id=invite["workspace_id"],
            email=principal.email,
            role=membership["role"],
        )
        audit(request, principal, "invite.accept", "invite", invite["id"], {"workspace_id": invite["workspace_id"]})
        return session_response(switched, response)

    @router.patch("/api/conversations/{conversation_id}")
    def conversation_update(
        conversation_id: str,
        req: ConversationUpdate,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            row = store.update_conversation(
                conversation_id,
                workspace_id=principal.workspace_id,
                title=req.title,
                assigned_to=req.assigned_to if "assigned_to" in req.model_fields_set else ...,
                pinned=req.pinned,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        action = "task.handoff" if "assigned_to" in req.model_fields_set else "conversation.update"
        audit(request, principal, action, "conversation", conversation_id, req.model_dump(exclude_unset=True))
        return row

    @router.post("/api/conversations/{conversation_id}/archive")
    def conversation_archive(
        conversation_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            row = store.archive_conversation(conversation_id, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit(request, principal, "conversation.archive", "conversation", conversation_id)
        store.record_product_event(workspace_id=principal.workspace_id, user_id=principal.user_id, conversation_id=conversation_id, name="task.archive")
        return row

    @router.post("/api/conversations/{conversation_id}/restore")
    def conversation_restore(
        conversation_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            row = store.restore_conversation(conversation_id, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        audit(request, principal, "conversation.restore", "conversation", conversation_id)
        store.record_product_event(workspace_id=principal.workspace_id, user_id=principal.user_id, conversation_id=conversation_id, name="task.restore")
        return row

    @router.post("/api/conversations/{conversation_id}/delete-request")
    def conversation_delete_request(
        conversation_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)
        if latest and latest["status"] in {"queued", "running"}:
            raise HTTPException(409, "执行中的任务需要先停止，才能请求永久删除")
        try:
            approval = store.create_approval(
                workspace_id=principal.workspace_id,
                conversation_id=conversation_id,
                action="conversation.delete",
                requested_by=principal.user_id,
                reason="永久删除任务及其素材需要显式确认",
            )
            store.add_event(conversation_id, "approval.requested", {"approval": approval}, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        audit(request, principal, "approval.request", "approval", approval["id"], {"action": "conversation.delete"})
        return approval

    @router.post("/api/approvals/{approval_id}/resolve")
    def approval_resolve(
        approval_id: str,
        req: ApprovalResolve,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        try:
            approval = store.resolve_approval(approval_id, workspace_id=principal.workspace_id, user_id=principal.user_id, approved=req.approved)
            store.add_event(approval["conversation_id"], "approval.resolved", {"approval": approval}, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "审批不存在") from exc
        audit(request, principal, "approval.resolve", "approval", approval_id, {"approved": req.approved})
        return approval

    @router.delete("/api/conversations/{conversation_id}")
    def conversation_delete(
        conversation_id: str,
        request: Request,
        approval_id: str = Query(..., min_length=1),
        principal: Principal = Depends(require_principal),
    ):
        require_manager(principal)
        try:
            approval = store.get_approval(approval_id, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "审批不存在") from exc
        if approval["conversation_id"] != conversation_id or approval["action"] != "conversation.delete" or approval["status"] != "approved":
            raise HTTPException(409, "删除审批尚未通过或不匹配当前任务")
        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)
        if latest and latest["status"] in {"queued", "running"}:
            raise HTTPException(409, "执行中的任务需要先停止，才能永久删除")
        assets = store.list_assets(conversation_id, workspace_id=principal.workspace_id)
        keys: list[str] = []
        for asset in assets:
            keys.append(str(asset["path"]))
            keys.extend(str(key) for key in (asset.get("meta") or {}).get("keyframes", []))
        for key in dict.fromkeys(keys):
            try:
                storage.delete(key)
            except Exception as exc:
                audit(request, principal, "conversation.delete.storage_failed", "conversation", conversation_id, {"object_key": key, "error": repr(exc)})
                raise HTTPException(503, "素材清理失败，任务尚未删除；可以稍后重试") from exc
        if not store.consume_approval(approval_id, workspace_id=principal.workspace_id, conversation_id=conversation_id, action="conversation.delete"):
            raise HTTPException(409, "删除审批已被使用")
        store.delete_conversation(conversation_id, workspace_id=principal.workspace_id)
        audit(request, principal, "conversation.delete", "conversation", conversation_id, {"asset_objects": len(keys)})
        return {"ok": True}

    @router.get("/api/conversations/{conversation_id}/control")
    def conversation_control(
        conversation_id: str,
        principal: Principal = Depends(require_principal),
    ):
        try:
            return {
                "approvals": store.list_approvals(conversation_id, workspace_id=principal.workspace_id),
                "feedback": store.list_feedback(conversation_id, workspace_id=principal.workspace_id),
                "quality_gate": store.feedback_gate(conversation_id, workspace_id=principal.workspace_id),
            }
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc

    @router.post("/api/jobs/{job_id}/retry")
    async def job_retry(
        job_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            job = store.retry_job(job_id, workspace_id=principal.workspace_id)
        except KeyError as exc:
            raise HTTPException(404, "执行不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await schedule_retry(job, background_tasks, principal)
        store.record_product_event(workspace_id=principal.workspace_id, user_id=principal.user_id, conversation_id=job["conversation_id"], name="task.retry")
        audit(request, principal, "job.retry", "job", job["id"], {"source_job_id": job_id})
        return {"status": job["status"], "job_id": job["id"]}

    @router.put("/api/messages/{message_id}/feedback")
    def feedback_update(
        message_id: str,
        req: FeedbackUpdate,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        try:
            feedback = store.upsert_feedback(
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                message_id=message_id,
                verdict=req.verdict,
                evidence_useful=req.evidence_useful,
                human_verified=req.human_verified,
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "交付结果不存在") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit(request, principal, "result.feedback", "message", message_id, {"verdict": req.verdict, "human_verified": req.human_verified})
        return feedback

    @router.post("/api/product-events")
    def product_event(
        req: ProductEventCreate,
        principal: Principal = Depends(require_principal),
    ):
        if req.name not in ALLOWED_PRODUCT_EVENTS:
            raise HTTPException(400, "不支持的产品事件")
        try:
            store.record_product_event(
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                conversation_id=req.conversation_id,
                name=req.name,
                payload=req.payload,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        return {"ok": True}

    @router.get("/api/metrics")
    def product_metrics(principal: Principal = Depends(require_principal)):
        require_manager(principal)
        return store.product_metrics(workspace_id=principal.workspace_id)

    @router.get("/api/quality-gate")
    def quality_gate(
        conversation_id: str,
        principal: Principal = Depends(require_principal),
    ):
        return store.feedback_gate(conversation_id, workspace_id=principal.workspace_id)

    return router
