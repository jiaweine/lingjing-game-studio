from __future__ import annotations

import time
from typing import Any

from sqlalchemy import and_, select, update

from worldforge.product.store import ConversationStore


DEFAULT_STALE_AFTER_SECONDS = 30 * 60
MAX_RECOVERY_BATCH = 1000
RECOVERY_REASON = "worker heartbeat/lease is absent; stale running job was failed without replay"


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_RECOVERY_BATCH))


def list_stale_running_jobs(
    store: ConversationStore,
    *,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    now: float | None = None,
    workspace_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return running jobs whose persisted claim is older than an explicit cutoff.

    This is deliberately read-only. A running job is never replayed automatically.
    Jobs without a persisted ``claimed_at`` are not guessed to be stale.
    """

    current = time.time() if now is None else float(now)
    cutoff = current - max(0.0, float(stale_after_seconds))
    predicate = and_(
        store.jobs.c.status == "running",
        store.jobs.c.claimed_at.is_not(None),
        store.jobs.c.claimed_at <= cutoff,
    )
    if workspace_id:
        predicate = and_(predicate, store.jobs.c.workspace_id == workspace_id)

    with store.engine.connect() as connection:
        rows = connection.execute(
            select(store.jobs)
            .where(predicate)
            .order_by(store.jobs.c.claimed_at, store.jobs.c.created_at, store.jobs.c.id)
            .limit(_bounded_limit(limit))
        ).fetchall()
    return [store._json_row(row) for row in rows]


def _candidate_fence(store: ConversationStore, candidate: dict[str, Any]):
    predicate = and_(
        store.jobs.c.id == candidate["id"],
        store.jobs.c.workspace_id == candidate["workspace_id"],
        store.jobs.c.status == "running",
        store.jobs.c.attempts == int(candidate.get("attempts") or 0),
    )
    worker_id = candidate.get("worker_id")
    claimed_at = candidate.get("claimed_at")
    predicate = and_(
        predicate,
        store.jobs.c.worker_id.is_(None)
        if worker_id is None
        else store.jobs.c.worker_id == worker_id,
        store.jobs.c.claimed_at.is_(None)
        if claimed_at is None
        else store.jobs.c.claimed_at == float(claimed_at),
    )
    return predicate


def fail_stale_running_jobs(
    store: ConversationStore,
    *,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    now: float | None = None,
    workspace_id: str | None = None,
    limit: int = 100,
    reason: str = RECOVERY_REASON,
) -> list[dict[str, Any]]:
    """Fail stale running jobs without requeueing or replaying their side effects.

    Candidates are scanned first and each write is fenced by the observed
    ``(id, workspace_id, status, attempts, worker_id, claimed_at)`` snapshot.
    If a live worker completes/cancels/fails the job after the scan, recovery loses
    the CAS and leaves that newer state untouched.
    """

    current = time.time() if now is None else float(now)
    candidates = list_stale_running_jobs(
        store,
        stale_after_seconds=stale_after_seconds,
        now=current,
        workspace_id=workspace_id,
        limit=limit,
    )
    changed: list[dict[str, Any]] = []
    failure_reason = str(reason).strip()[:8000] or RECOVERY_REASON

    for candidate in candidates:
        with store.engine.begin() as connection:
            result = connection.execute(
                update(store.jobs)
                .where(_candidate_fence(store, candidate))
                .values(
                    status="failed",
                    last_error=failure_reason,
                    completed_at=current,
                )
            )
            if result.rowcount != 1:
                continue
            connection.execute(
                update(store.conversations)
                .where(
                    and_(
                        store.conversations.c.id == candidate["conversation_id"],
                        store.conversations.c.workspace_id == candidate["workspace_id"],
                    )
                )
                .values(status="blocked", updated_at=current)
            )
        changed.append(
            store.get_job(candidate["id"], workspace_id=candidate["workspace_id"])
        )

    return changed
