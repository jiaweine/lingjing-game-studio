from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select

from .memory_consolidator import MemoryConsolidator
from .memory_identity import MemoryIdentityResolver
from .project_memory import ProjectMemoryStore


def register_memory_identity_routes(
    router: APIRouter,
    *,
    memory_store: ProjectMemoryStore,
    memory_consolidator: MemoryConsolidator,
    require_principal: Callable,
    resolver: MemoryIdentityResolver | None = None,
) -> None:
    """Register read-only shadow suggestions for proposal → existing-memory identity.

    The resolver can suggest an existing semantic key, but this route never mutates a
    proposal, memory head, revision, or approval state. A human review action remains the
    only path from a pending proposal to authoritative Project Memory.
    """

    identity_resolver = resolver or MemoryIdentityResolver()

    @router.get(
        "/api/projects/{project_id}/memory-proposals/{proposal_id}/identity-suggestions"
    )
    def memory_identity_suggestions(
        project_id: str,
        proposal_id: str,
        principal=Depends(require_principal),
    ):
        try:
            proposals = memory_consolidator.list_proposals(
                workspace_id=principal.workspace_id,
                actor_id=principal.user_id,
                project_id=project_id,
                status=None,
                limit=500,
            )
            proposal = next(
                (row for row in proposals if row.get("id") == proposal_id),
                None,
            )
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.get("status") != "pending":
                raise ValueError("只有 pending memory proposal 可以请求 identity suggestion")

            with memory_store.engine.connect() as connection:
                memory_store._require_member(
                    connection, principal.workspace_id, principal.user_id
                )
                memory_store._require_project(
                    connection, principal.workspace_id, project_id
                )
                rows = connection.execute(
                    select(memory_store.items)
                    .select_from(
                        memory_store.heads.join(
                            memory_store.items,
                            memory_store.heads.c.memory_id == memory_store.items.c.id,
                        )
                    )
                    .where(
                        and_(
                            memory_store.heads.c.workspace_id == principal.workspace_id,
                            memory_store.heads.c.project_id == project_id,
                            memory_store.heads.c.state != "retracted",
                            memory_store.items.c.workspace_id == principal.workspace_id,
                            memory_store.items.c.project_id == project_id,
                        )
                    )
                    .order_by(
                        memory_store.items.c.pinned.desc(),
                        memory_store.items.c.created_at.desc(),
                    )
                    .limit(1000)
                ).all()
            heads = [memory_store._decode_item(row) for row in rows]
            resolution = identity_resolver.resolve(proposal, heads)
            return {
                "proposal_id": proposal_id,
                "proposal_suggested_key": proposal.get("suggested_key"),
                "read_only": True,
                **resolution.to_dict(),
            }
        except KeyError as exc:
            raise HTTPException(404, "记忆 proposal 不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
