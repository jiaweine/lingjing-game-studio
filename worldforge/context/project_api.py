from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .project_memory import MemoryConflict, ProjectMemoryStore


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=4000)
    default_branch: str | None = Field(default=None, max_length=160)


class MemoryPutRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="fact", max_length=48)
    content: str = Field(min_length=1, max_length=20000)
    value: dict[str, Any] = Field(default_factory=dict)
    state: str = Field(default="active", max_length=32)
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
    source_type: str = Field(default="user_api", max_length=48)
    source_id: str | None = Field(default=None, max_length=128)
    source_excerpt: str = Field(default="", max_length=4000)


class MemoryStateRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=240)
    state: str = Field(max_length=32)
    build_ref: str | None = Field(default=None, max_length=160)
    branch_ref: str | None = Field(default=None, max_length=200)
    commit_ref: str | None = Field(default=None, max_length=160)
    environment_ref: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=4000)


def build_project_memory_router(
    *,
    memory_store: ProjectMemoryStore,
    product_store,
    require_principal: Callable,
    require_editor: Callable,
) -> APIRouter:
    router = APIRouter(tags=["project-memory"])

    def audit(request: Request, principal, action: str, resource_type: str, resource_id: str, payload=None):
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
            raise HTTPException(409 if isinstance(exc, ValueError) else 403, str(exc)) from exc
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
        source_id = req.source_id or f"api:{getattr(request.state, 'request_id', uuid.uuid4().hex)}"
        try:
            row = memory_store.put_memory(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                memory_key=req.memory_key,
                kind=req.kind,
                content=req.content,
                value=req.value,
                state=req.state,
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
                source_type=req.source_type,
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
            {"project_id": project_id, "memory_key": row["memory_key"], "revision": row["revision"]},
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
        source_id = f"api:{getattr(request.state, 'request_id', uuid.uuid4().hex)}"
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
            {"project_id": project_id, "memory_key": row["memory_key"], "state": row["state"]},
        )
        return row

    return router
