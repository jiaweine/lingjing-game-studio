from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select

from .memory_consolidator import MemoryConsolidator
from .memory_identity import MemoryIdentityResolver
from .project_memory import ProjectMemoryStore


_MAX_IDENTITY_HEADS = 2000


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
            with memory_store.engine.connect() as connection:
                memory_store._require_member(
                    connection, principal.workspace_id, principal.user_id
                )
                memory_store._require_project(
                    connection, principal.workspace_id, project_id
                )
                proposal_row = connection.execute(
                    select(memory_consolidator.proposals).where(
                        and_(
                            memory_consolidator.proposals.c.id == proposal_id,
                            memory_consolidator.proposals.c.workspace_id
                            == principal.workspace_id,
                            memory_consolidator.proposals.c.project_id == project_id,
                        )
                    )
                ).first()
                if proposal_row is None:
                    raise KeyError(proposal_id)
                proposal = dict(proposal_row._mapping)
                if proposal.get("status") != "pending":
                    raise ValueError(
                        "只有 pending memory proposal 可以请求 identity suggestion"
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
                            memory_store.items.c.kind == proposal.get("kind"),
                        )
                    )
                    .order_by(
                        memory_store.items.c.pinned.desc(),
                        memory_store.items.c.created_at.desc(),
                    )
                    .limit(_MAX_IDENTITY_HEADS + 1)
                ).all()

            truncated = len(rows) > _MAX_IDENTITY_HEADS
            rows = rows[:_MAX_IDENTITY_HEADS]
            heads = [memory_store._decode_item(row) for row in rows]
            resolution = identity_resolver.resolve(proposal, heads)
            return {
                "proposal_id": proposal_id,
                "proposal_suggested_key": proposal.get("suggested_key"),
                "read_only": True,
                "candidate_heads_evaluated": len(heads),
                "candidate_heads_truncated": truncated,
                **resolution.to_dict(),
            }
        except KeyError as exc:
            raise HTTPException(404, "记忆 proposal 不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
