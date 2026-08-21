from __future__ import annotations

import asyncio
import os
import socket

from worldforge.api.app import (
    _fail_product_job,
    _maintain_product_job_lease,
    _run_analysis_job,
    product_store,
)
from worldforge.settings import settings


async def run_worker():
    worker_id = os.getenv(
        "WORLDFORGE_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    idle = float(os.getenv("WORLDFORGE_WORKER_IDLE_SECONDS", "0.8"))
    while True:
        job = product_store.claim_job(
            worker_id,
            lease_seconds=settings.job_lease_seconds,
        )
        if not job:
            await asyncio.sleep(idle)
            continue
        lease_token = job["lease_token"]
        heartbeat = asyncio.create_task(
            _maintain_product_job_lease(
                job["id"],
                worker_id=worker_id,
                lease_token=lease_token,
            )
        )
        payload = job["payload"]
        try:
            assets = []
            for asset_id in payload.get("asset_ids", []):
                try:
                    assets.append(
                        product_store.get_asset(
                            asset_id, workspace_id=job["workspace_id"]
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
                lease_token=lease_token,
            )
        except Exception as exc:
            await _fail_product_job(
                job["id"],
                repr(exc),
                lease_token=lease_token,
            )
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
