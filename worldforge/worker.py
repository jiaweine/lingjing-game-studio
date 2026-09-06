from __future__ import annotations

import asyncio
import os
import socket

from worldforge.api.app import _fail_product_job, _run_analysis_job, product_store
from worldforge.context.history_snapshot import history_from_job_payload


async def run_worker():
    worker_id = os.getenv(
        "WORLDFORGE_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    idle = float(os.getenv("WORLDFORGE_WORKER_IDLE_SECONDS", "0.8"))
    while True:
        job = product_store.claim_job(worker_id)
        if not job:
            await asyncio.sleep(idle)
            continue
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
            history, history_meta = history_from_job_payload(
                product_store,
                conversation_id=job["conversation_id"],
                workspace_id=job["workspace_id"],
                payload=payload,
            )
            await _run_analysis_job(
                conversation_id=job["conversation_id"],
                workspace_id=job["workspace_id"],
                text=str(payload.get("text", "")),
                provider_key=str(payload.get("provider", "auto")),
                history=history,
                assets=assets,
                job_id=job["id"],
                project_context=dict(payload.get("project_context") or {}),
                history_snapshot_meta=history_meta,
            )
        except Exception as exc:
            await _fail_product_job(job["id"], repr(exc))


def main():
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
