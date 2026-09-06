from __future__ import annotations

from typing import Any

from .project_memory import ProjectMemoryStore
from .project_packet import (
    ProjectMemoryPacket,
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
    """Freeze the authorized project + scoped memory packet at enqueue time.

    An unbound conversation intentionally gets no project memory. We never infer a project
    from title, scene or workspace. The returned dict is safe to persist inside a bounded job
    payload and can be replayed by in-process or external workers without querying current
    memory heads again.
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
        "memory_packet": packet.to_dict(),
    }


def packet_from_job_context(raw: dict[str, Any] | None) -> ProjectMemoryPacket | None:
    data = dict(raw or {})
    return ProjectMemoryPacket.from_dict(data.get("memory_packet"))


def validate_job_project_access(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    job_context: dict[str, Any] | None,
) -> ProjectMemoryPacket | None:
    """Revalidate authorization while keeping the enqueue-time memory revision frozen."""
    data = dict(job_context or {})
    actor_id = str(data.get("actor_id") or "")
    project_id = str(data.get("project_id") or "")
    packet = packet_from_job_context(data)
    if not actor_id or not project_id or packet is None:
        return None
    if packet.project_id != project_id:
        raise PermissionError("job project memory snapshot identity mismatch")
    # Membership/project status are live authorization controls; memory heads are not read.
    store.get_project(
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=project_id,
    )
    return packet


def record_job_memory_usage(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    conversation_id: str,
    job_context: dict[str, Any] | None,
    reason: str = "analysis-context-snapshot",
) -> int:
    """Audit frozen memory consumption without mutating memory importance or truth state."""
    data = dict(job_context or {})
    actor_id = str(data.get("actor_id") or "")
    project_id = str(data.get("project_id") or "")
    packet = validate_job_project_access(
        store,
        workspace_id=workspace_id,
        job_context=data,
    )
    if not actor_id or not project_id or packet is None:
        return 0
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
