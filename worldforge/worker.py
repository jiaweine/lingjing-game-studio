from __future__ import annotations

import asyncio
import os
import socket
import time

from sqlalchemy import and_, select, update

from worldforge.api.app import _fail_product_job, _run_analysis_job, product_store


def renew_job_lease(store, job_id: str, worker_id: str, *, now: float | None = None) -> bool:
    """Refresh one running job lease only when it is still owned by this worker."""
    now = time.time() if now is None else float(now)
    with store.engine.begin() as connection:
        result = connection.execute(
            update(store.jobs)
            .where(
                and_(
                    store.jobs.c.id == job_id,
                    store.jobs.c.status == "running",
                    store.jobs.c.worker_id == worker_id,
                )
            )
            .values(claimed_at=now)
        )
    return result.rowcount == 1


def requeue_expired_jobs(
    store,
    *,
    lease_seconds: float,
    max_attempts: int = 3,
    now: float | None = None,
) -> dict[str, int]:
    """Recover jobs abandoned by dead external workers.

    `api-inprocess` jobs are intentionally excluded: this lease protocol belongs to the
    external worker mode. Each reclaim is compare-and-swap guarded by both the stale
    lease timestamp and original worker id, so a renewed or reassigned job cannot be
    stolen by a concurrent reaper.
    """
    now = time.time() if now is None else float(now)
    cutoff = now - max(30.0, float(lease_seconds))
    max_attempts = max(1, int(max_attempts))
    requeued = 0
    failed = 0

    with store.engine.begin() as connection:
        rows = connection.execute(
            select(
                store.jobs.c.id,
                store.jobs.c.conversation_id,
                store.jobs.c.claimed_at,
                store.jobs.c.worker_id,
                store.jobs.c.attempts,
            ).where(
                and_(
                    store.jobs.c.status == "running",
                    store.jobs.c.claimed_at.is_not(None),
                    store.jobs.c.claimed_at < cutoff,
                    store.jobs.c.worker_id.is_not(None),
                    store.jobs.c.worker_id != "api-inprocess",
                )
            )
        ).fetchall()

        for row in rows:
            stale_claimed_at = row.claimed_at
            stale_worker_id = row.worker_id
            where = and_(
                store.jobs.c.id == row.id,
                store.jobs.c.status == "running",
                store.jobs.c.claimed_at == stale_claimed_at,
                store.jobs.c.worker_id == stale_worker_id,
            )
            if int(row.attempts or 0) >= max_attempts:
                result = connection.execute(
                    update(store.jobs)
                    .where(where)
                    .values(
                        status="failed",
                        worker_id=None,
                        claimed_at=None,
                        completed_at=now,
                        last_error="worker lease expired",
                    )
                )
                if result.rowcount:
                    failed += 1
                    connection.execute(
                        update(store.conversations)
                        .where(store.conversations.c.id == row.conversation_id)
                        .values(status="blocked", updated_at=now)
                    )
            else:
                result = connection.execute(
                    update(store.jobs)
                    .where(where)
                    .values(
                        status="queued",
                        worker_id=None,
                        claimed_at=None,
                        available_at=now,
                        last_error="worker lease expired; requeued",
                    )
                )
                if result.rowcount:
                    requeued += 1

    return {"requeued": requeued, "failed": failed}


async def _lease_heartbeat(
    store,
    job_id: str,
    worker_id: str,
    *,
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        renewed = await asyncio.to_thread(
            renew_job_lease,
            store,
            job_id,
            worker_id,
        )
        if not renewed:
            return


async def run_worker():
    worker_id = os.getenv(
        "WORLDFORGE_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    idle = max(.05, float(os.getenv("WORLDFORGE_WORKER_IDLE_SECONDS", "0.8")))
    lease_seconds = max(
        30.0, float(os.getenv("WORLDFORGE_JOB_LEASE_SECONDS", "300"))
    )
    max_attempts = max(1, int(os.getenv("WORLDFORGE_JOB_MAX_ATTEMPTS", "3")))
    heartbeat_interval = max(5.0, min(30.0, lease_seconds / 3.0))
    reap_interval = max(5.0, min(30.0, lease_seconds / 3.0))
    last_reap = 0.0

    while True:
        now = time.time()
        if now - last_reap >= reap_interval:
            await asyncio.to_thread(
                requeue_expired_jobs,
                product_store,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
            )
            last_reap = now

        job = await asyncio.to_thread(product_store.claim_job, worker_id)
        if not job:
            await asyncio.sleep(idle)
            continue

        heartbeat = asyncio.create_task(
            _lease_heartbeat(
                product_store,
                job["id"],
                worker_id,
                interval_seconds=heartbeat_interval,
            ),
            name=f"lease-{job['id']}",
        )
        try:
            payload = job["payload"]
            assets = []
            for asset_id in payload.get("asset_ids", []):
                try:
                    assets.append(
                        await asyncio.to_thread(
                            product_store.get_asset,
                            asset_id,
                            workspace_id=job["workspace_id"],
                        )
                    )
                except KeyError:
                    pass
            await _run_analysis_job(
                conversation_id=job["conversation_id"],
                workspace_id=job["workspace_id"],
                text=str(payload.get("text", "")),
                provider_key=str(payload.get("provider", "auto")),
                history=list(payload.get("history", [])),
                assets=assets,
                job_id=job["id"],
            )
        except Exception as exc:
            await _fail_product_job(job["id"], repr(exc), max_attempts=max_attempts)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass


def main():
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
