from __future__ import annotations

import time
from typing import Any

from sqlalchemy import and_, select

from .project_memory import ProjectMemoryStore
from .project_packet import (
    ProjectMemoryPacket,
    ProjectScopeSnapshot,
    compile_project_memory_packet,
    resolve_project_scope,
)


def build_job_project_context(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    actor_id: str,
    conversation_id: str,
    query: str,
    selected_assets: list[dict[str, Any]] | None = None,
    requested_scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Freeze authorized project/scope and immutable memory locators at enqueue time.

    Governed memory content is deliberately NOT copied into the job row. This keeps delete
    and retraction meaningful while still preventing a retry from silently switching to a
    newly-created memory revision.
    """
    project = store.project_for_conversation(
        workspace_id=workspace_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
    )
    if project is None:
        return None
    scope = resolve_project_scope(
        list(selected_assets or []), requested=dict(requested_scope or {})
    )
    packet = compile_project_memory_packet(
        store,
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=str(project["id"]),
        query=query,
        scope=scope,
    )
    return {
        "actor_id": actor_id,
        "project_id": str(project["id"]),
        "project_name": str(project.get("name") or ""),
        "scope": scope.to_dict(),
        "memory_snapshot": packet.to_job_snapshot(),
    }


def _snapshot_refs(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = dict(raw or {})
    snapshot = dict(data.get("memory_snapshot") or {})
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(snapshot.get("memory_refs") or [])[:24]:
        row = dict(item or {})
        memory_id = str(row.get("id") or "").strip()[:64]
        if not memory_id or memory_id in seen:
            continue
        try:
            revision = max(1, int(row.get("revision") or 1))
            score = float(row.get("retrieval_score") or 0.0)
        except (TypeError, ValueError):
            continue
        refs.append({"id": memory_id, "revision": revision, "retrieval_score": score})
        seen.add(memory_id)
    return refs


def materialize_job_project_memory(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    job_context: dict[str, Any] | None,
    now: float | None = None,
) -> ProjectMemoryPacket | None:
    """Reauthorize and materialize only frozen refs that are still the active current head.

    If a memory is superseded, disputed, retracted, expired or deleted after enqueue, the
    old ref is invalidated and omitted. We never substitute the new head because that would
    make retries semantically drift from the original job.
    """
    data = dict(job_context or {})
    actor_id = str(data.get("actor_id") or "")
    project_id = str(data.get("project_id") or "")
    snapshot = dict(data.get("memory_snapshot") or {})
    snapshot_project_id = str(snapshot.get("project_id") or "")
    if not actor_id or not project_id or not snapshot:
        return None
    if snapshot_project_id and snapshot_project_id != project_id:
        raise PermissionError("job project memory snapshot identity mismatch")

    project = store.get_project(
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=project_id,
    )
    refs = _snapshot_refs(data)
    scope = ProjectScopeSnapshot.from_dict(snapshot.get("scope") or data.get("scope"))
    if not refs:
        return ProjectMemoryPacket(
            project_id=project_id,
            project_name=str(snapshot.get("project_name") or project.get("name") or "未命名项目"),
            scope=scope,
            memories=(),
            query=str(snapshot.get("query") or "")[:2000],
            chars=0,
        )

    ids = [row["id"] for row in refs]
    statement = (
        select(store.items)
        .select_from(store.items.join(store.heads, store.heads.c.memory_id == store.items.c.id))
        .where(
            and_(
                store.items.c.workspace_id == workspace_id,
                store.items.c.project_id == project_id,
                store.items.c.id.in_(ids),
                store.heads.c.workspace_id == workspace_id,
                store.heads.c.project_id == project_id,
                store.heads.c.state == "active",
            )
        )
    )
    with store.engine.connect() as connection:
        current = {
            str(row.id): store._decode_item(row)
            for row in connection.execute(statement).all()
        }

    timestamp = time.time() if now is None else float(now)
    selected: list[dict[str, Any]] = []
    invalidated = 0
    used = 0
    for ref in refs:
        row = current.get(ref["id"])
        if row is None or int(row.get("revision") or 0) != ref["revision"]:
            invalidated += 1
            continue
        if row.get("expires_at") is not None and float(row["expires_at"]) <= timestamp:
            invalidated += 1
            continue
        if row.get("valid_from") is not None and float(row["valid_from"]) > timestamp:
            invalidated += 1
            continue
        if row.get("valid_to") is not None and float(row["valid_to"]) <= timestamp:
            invalidated += 1
            continue
        content = str(row.get("content") or "").strip()[:2400]
        if not content:
            invalidated += 1
            continue
        safe = {
            "id": str(row["id"]),
            "memory_key": str(row["memory_key"]),
            "revision": int(row["revision"]),
            "kind": str(row["kind"]),
            "content": content,
            "state": str(row["state"]),
            "confidence": float(row["confidence"]),
            "importance": float(row["importance"]),
            "pinned": bool(row["pinned"]),
            "build_ref": row.get("build_ref"),
            "branch_ref": row.get("branch_ref"),
            "commit_ref": row.get("commit_ref"),
            "environment_ref": row.get("environment_ref"),
            "source_type": str(row["source_type"]),
            "source_id": str(row["source_id"]),
            "source_excerpt": str(row.get("source_excerpt") or "")[:800],
            "retrieval_score": ref["retrieval_score"],
        }
        selected.append(safe)
        used += len(content)

    return ProjectMemoryPacket(
        project_id=project_id,
        project_name=str(snapshot.get("project_name") or project.get("name") or "未命名项目"),
        scope=scope,
        memories=tuple(selected),
        query=str(snapshot.get("query") or "")[:2000],
        chars=used,
        invalidated_refs=invalidated,
    )


def record_job_memory_usage(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    conversation_id: str,
    job_context: dict[str, Any] | None,
    packet: ProjectMemoryPacket | None,
    reason: str = "analysis-context-snapshot",
) -> int:
    """Audit actual materialized consumption without mutating memory truth/importance."""
    data = dict(job_context or {})
    actor_id = str(data.get("actor_id") or "")
    project_id = str(data.get("project_id") or "")
    if not actor_id or not project_id or packet is None:
        return 0
    if packet.project_id != project_id:
        raise PermissionError("materialized packet project mismatch")
    recorded = 0
    for row in packet.memories:
        store.record_usage(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            memory_id=str(row["id"]),
            conversation_id=conversation_id,
            reason=reason,
            score=float(row.get("retrieval_score") or 0.0),
        )
        recorded += 1
    return recorded
