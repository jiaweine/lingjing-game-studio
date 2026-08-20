from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

# Allow `python scripts/stress_smoke.py` from a clean checkout.
# The workflow intentionally runs this as a script rather than `python -m`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.api.app import app


async def _burst(client: httpx.AsyncClient, path: str, requests: int, concurrency: int):
    semaphore = asyncio.Semaphore(concurrency)
    rows: list[tuple[int, float]] = []

    async def one():
        async with semaphore:
            started = time.perf_counter()
            response = await client.get(path)
            rows.append((response.status_code, (time.perf_counter() - started) * 1000))

    await asyncio.gather(*(one() for _ in range(requests)))
    return rows


def _summary(rows):
    latencies = sorted(value for _, value in rows)
    statuses = Counter(status for status, _ in rows)
    p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * .95) - 1))
    return {
        "requests": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p95_ms": round(latencies[p95_index], 2) if latencies else 0.0,
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
    }


async def main():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=30,
    ) as client:
        public_rows = await _burst(
            client,
            "/api/health",
            requests=240,
            concurrency=48,
        )
        public = _summary(public_rows)
        assert set(public["statuses"]) == {200}, public

        protected_rows = await _burst(
            client,
            "/api/conversations?limit=1",
            requests=160,
            concurrency=40,
        )
        protected = _summary(protected_rows)
        assert not any(status >= 500 for status in protected["statuses"]), protected
        assert protected["statuses"].get(200, 0) > 0, protected
        assert protected["statuses"].get(429, 0) > 0, protected
        assert set(protected["statuses"]) <= {200, 429}, protected

    report = {
        "public_health_burst": public,
        "protected_rate_limit_burst": protected,
        "note": (
            "In-process ASGI smoke only. It validates bounded degradation and catches "
            "5xx/concurrency regressions; it is not a production capacity benchmark."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
