from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select

from worldforge.settings import settings

from .memory_consolidator import MemoryConsolidator
from .memory_identity_api import register_memory_identity_routes
from .project_memory import MemoryConflict, ProjectMemoryStore


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=4000)
    default_branch: str | None = Field(default=None, max_length=160)


class MemoryPutRequest(BaseModel):
    """Explicit user-authored memory input; provenance/state are server-owned fields."""

    model_config = ConfigDict(extra="forbid")

    memory_key: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="fact", max_length=48)
    content: str = Field(min_length=1, max_length=20000)
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    pinned: bool = False
    build_ref: str | None = Field(default=None, max_length=160)
    branch_ref: str | None = Field(default=None, max_length=200)
    commit_ref: str | None = Field(default=None, max_length=160)
    environment_ref: str | None = Field(default=None, max_length=160)
    valid_from: float | None = None
    valid_to: float | None = None
    expires_at: float | None = None
    source_excerpt: str = Field(default="", max_length=4000)


class MemoryStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_key: str = Field(min_length=1, max_length=240)
    state: str = Field(max_length=32)
    build_ref: str | None = Field(default=None, max_length=160)
    branch_ref: str | None = Field(default=None, max_length=200)
    commit_ref: str | None = Field(default=None, max_length=160)
    environment_ref: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=4000)


class ProposalApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_key: str | None = Field(default=None, max_length=240)
    content: str | None = Field(default=None, max_length=20000)
    note: str = Field(default="", max_length=4000)


class ProposalRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=4000)


def build_project_memory_router(
    *,
    memory_store: ProjectMemoryStore,
    product_store,
    require_principal: Callable,
    require_editor: Callable,
    memory_consolidator: MemoryConsolidator | None = None,
) -> APIRouter:
    # Keep the existing app integration backward compatible. Development/test may create
    # derived proposal schema alongside other auto-created tables; production keeps
    # auto_create_schema=False and therefore requires the Alembic migration before rollout.
    memory_consolidator = memory_consolidator or MemoryConsolidator(
        memory_store.engine,
        memory_store,
        auto_create_schema=settings.auto_create_schema,
    )
    router = APIRouter(tags=["project-memory"])

    def audit(
        request: Request,
        principal,
        action: str,
        resource_type: str,
        resource_id: str,
        payload=None,
    ):
        product_store.add_audit(
            request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
            action=action,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=dict(payload or {}),
        )

    @router.post("/api/projects")
    def project_create(
        req: ProjectCreateRequest,
        request: Request,
        principal=Depends(require_editor),
    ):
        try:
            project = memory_store.create_project(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                name=req.name,
                external_ref=req.external_ref,
                default_branch=req.default_branch,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(
                409 if isinstance(exc, ValueError) else 403, str(exc)
            ) from exc
        audit(request, principal, "project.create", "project", project["id"])
        return project

    @router.get("/api/projects")
    def project_list(principal=Depends(require_principal)):
        try:
            return memory_store.list_projects(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.post("/api/projects/{project_id}/conversations/{conversation_id}")
    def project_bind_conversation(
        project_id: str,
        conversation_id: str,
        request: Request,
        principal=Depends(require_editor),
    ):
        try:
            mapping = memory_store.bind_conversation(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                conversation_id=conversation_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "项目或任务不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit(
            request,
            principal,
            "project.bind_conversation",
            "conversation",
            conversation_id,
            {"project_id": project_id},
        )
        return mapping

    @router.get("/api/conversations/{conversation_id}/project")
    def conversation_project(
        conversation_id: str,
        principal=Depends(require_principal),
    ):
        try:
            project = memory_store.project_for_conversation(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                conversation_id=conversation_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"project": project}

    @router.post("/api/projects/{project_id}/memories")
    def memory_put(
        project_id: str,
        req: MemoryPutRequest,
        request: Request,
        principal=Depends(require_editor),
    ):
        source_id = (
            f"api:{getattr(request.state, 'request_id', uuid.uuid4().hex)}"
        )
        try:
            row = memory_store.put_memory(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                memory_key=req.memory_key,
                kind=req.kind,
                content=req.content,
                value=req.value,
                state="active",
                confidence=req.confidence,
                importance=req.importance,
                pinned=req.pinned,
                build_ref=req.build_ref,
                branch_ref=req.branch_ref,
                commit_ref=req.commit_ref,
                environment_ref=req.environment_ref,
                valid_from=req.valid_from,
                valid_to=req.valid_to,
                expires_at=req.expires_at,
                source_type="user_api",
                source_id=source_id,
                source_excerpt=req.source_excerpt or req.content[:1000],
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except MemoryConflict as exc:
            raise HTTPException(409, "记忆版本并发冲突，请重试") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(
            request,
            principal,
            "memory.put",
            "project_memory",
            row["id"],
            {
                "project_id": project_id,
                "memory_key": row["memory_key"],
                "revision": row["revision"],
                "source_type": "user_api",
            },
        )
        return row

    @router.get("/api/projects/{project_id}/memories")
    def memory_list(
        project_id: str,
        build_ref: str | None = Query(default=None, max_length=160),
        branch_ref: str | None = Query(default=None, max_length=200),
        commit_ref: str | None = Query(default=None, max_length=160),
        environment_ref: str | None = Query(default=None, max_length=160),
        include_nonactive: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
        principal=Depends(require_principal),
    ):
        try:
            return memory_store.list_current_memories(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                build_ref=build_ref,
                branch_ref=branch_ref,
                commit_ref=commit_ref,
                environment_ref=environment_ref,
                include_nonactive=include_nonactive,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.get("/api/projects/{project_id}/memory-heads")
    def memory_head_list(
        project_id: str,
        include_nonactive: bool = True,
        limit: int = Query(default=250, ge=1, le=1000),
        principal=Depends(require_principal),
    ):
        """Governance view: return every current memory head across every identity scope.

        This intentionally does not apply inference-time scope shadowing. A project-memory
        management UI must be able to see build/branch/commit-specific heads that would be
        hidden from an unrelated inference scope, including disputed/retracted heads.
        """
        try:
            with memory_store.engine.connect() as connection:
                memory_store._require_member(
                    connection, principal.workspace_id, principal.user_id
                )
                memory_store._require_project(
                    connection, principal.workspace_id, project_id
                )
                conditions = [
                    memory_store.heads.c.workspace_id == principal.workspace_id,
                    memory_store.heads.c.project_id == project_id,
                    memory_store.items.c.workspace_id == principal.workspace_id,
                    memory_store.items.c.project_id == project_id,
                ]
                if not include_nonactive:
                    conditions.append(memory_store.heads.c.state == "active")
                rows = connection.execute(
                    select(memory_store.items)
                    .select_from(
                        memory_store.heads.join(
                            memory_store.items,
                            memory_store.heads.c.memory_id == memory_store.items.c.id,
                        )
                    )
                    .where(and_(*conditions))
                    .order_by(
                        memory_store.items.c.pinned.desc(),
                        memory_store.items.c.created_at.desc(),
                    )
                    .limit(max(1, min(1000, int(limit))))
                ).all()
            return [memory_store._decode_item(row) for row in rows]
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.get("/api/projects/{project_id}/memory-history")
    def memory_history(
        project_id: str,
        memory_key: str = Query(min_length=1, max_length=240),
        build_ref: str | None = Query(default=None, max_length=160),
        branch_ref: str | None = Query(default=None, max_length=200),
        commit_ref: str | None = Query(default=None, max_length=160),
        environment_ref: str | None = Query(default=None, max_length=160),
        principal=Depends(require_principal),
    ):
        try:
            return memory_store.memory_history(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                memory_key=memory_key,
                build_ref=build_ref,
                branch_ref=branch_ref,
                commit_ref=commit_ref,
                environment_ref=environment_ref,
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.post("/api/projects/{project_id}/memory-state")
    def memory_state(
        project_id: str,
        req: MemoryStateRequest,
        request: Request,
        principal=Depends(require_editor),
    ):
        source_id = (
            f"api:{getattr(request.state, 'request_id', uuid.uuid4().hex)}"
        )
        try:
            row = memory_store.set_memory_state(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                memory_key=req.memory_key,
                state=req.state,
                build_ref=req.build_ref,
                branch_ref=req.branch_ref,
                commit_ref=req.commit_ref,
                environment_ref=req.environment_ref,
                source_type="user_api",
                source_id=source_id,
                source_excerpt=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "项目记忆不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except MemoryConflict as exc:
            raise HTTPException(409, "记忆版本并发冲突，请重试") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(
            request,
            principal,
            "memory.state",
            "project_memory",
            row["id"],
            {
                "project_id": project_id,
                "memory_key": row["memory_key"],
                "state": row["state"],
            },
        )
        return row

    @router.get("/api/projects/{project_id}/memory-proposals")
    def memory_proposal_list(
        project_id: str,
        status: str = Query(default="pending", max_length=32),
        conversation_id: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=100, ge=1, le=500),
        principal=Depends(require_principal),
    ):
        status_filter = None if status.strip().lower() == "all" else status
        try:
            return memory_consolidator.list_proposals(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                status=status_filter,
                conversation_id=conversation_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post(
        "/api/projects/{project_id}/memory-proposals/{proposal_id}/approve"
    )
    def memory_proposal_approve(
        project_id: str,
        proposal_id: str,
        req: ProposalApproveRequest,
        request: Request,
        principal=Depends(require_editor),
    ):
        try:
            result = memory_consolidator.approve_proposal(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                proposal_id=proposal_id,
                memory_key=req.memory_key,
                content=req.content,
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "记忆 proposal 不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except MemoryConflict as exc:
            raise HTTPException(409, "记忆 proposal 并发冲突，请重试") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        memory = result.get("memory") or {}
        audit(
            request,
            principal,
            "memory.proposal.approve",
            "memory_proposal",
            proposal_id,
            {
                "project_id": project_id,
                "memory_id": memory.get("id"),
                "memory_key": memory.get("memory_key"),
                "revision": memory.get("revision"),
            },
        )
        return result

    @router.post(
        "/api/projects/{project_id}/memory-proposals/{proposal_id}/reject"
    )
    def memory_proposal_reject(
        project_id: str,
        proposal_id: str,
        req: ProposalRejectRequest,
        request: Request,
        principal=Depends(require_editor),
    ):
        try:
            proposal = memory_consolidator.reject_proposal(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                proposal_id=proposal_id,
                note=req.note,
            )
        except KeyError as exc:
            raise HTTPException(404, "记忆 proposal 不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except MemoryConflict as exc:
            raise HTTPException(409, "记忆 proposal 并发冲突，请重试") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(
            request,
            principal,
            "memory.proposal.reject",
            "memory_proposal",
            proposal_id,
            {"project_id": project_id},
        )
        return proposal

    register_memory_identity_routes(
        router,
        memory_store=memory_store,
        memory_consolidator=memory_consolidator,
        require_principal=require_principal,
    )
    return router
