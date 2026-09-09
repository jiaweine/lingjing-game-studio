from __future__ import annotations

import asyncio
from collections import OrderedDict
import os
from pathlib import Path
import time
from typing import Any

from .retrieval_sidecar import MultimodalRetrievalClient


def _kind(asset: dict[str, Any]) -> str:
    meta = asset.get("meta", {}) or {}
    context = meta.get("_context", {}) or {}
    kind = str(context.get("kind") or meta.get("kind") or "")
    if kind and kind != "file":
        return kind
    mime = str(asset.get("mime", "") or "")
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or mime in {
        "application/json", "application/xml", "text/csv"
    }:
        return "text"
    if mime.startswith("image/"):
        return "image"
    return kind or "file"


class PreindexScheduler:
    """Best-effort post-request warm-up for independently isolated indexer replicas.

    This is deliberately an optimization, not a durable job system. Raw assets and online
    deterministic retrieval remain correct if every task is cancelled. Process-local
    dedupe prevents repeated scheduling; cross-process build leases in the indexer prevent
    duplicate GPU work across API workers. Production may later replace this scheduler with
    the existing durable job plane without changing the /v1/index contract.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("LINGJING_MM_PREINDEX_ENABLED", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.max_concurrency = max(
            1, int(os.getenv("LINGJING_MM_PREINDEX_CONCURRENCY", "1"))
        )
        self.retry_seconds = max(
            30.0, float(os.getenv("LINGJING_MM_PREINDEX_RETRY_SECONDS", "600"))
        )
        self.max_seen = max(
            64, int(os.getenv("LINGJING_MM_PREINDEX_DEDUPE", "2048"))
        )
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._semaphore: asyncio.Semaphore | None = None
        self.scheduled = 0
        self.completed = 0
        self.failed = 0

    @staticmethod
    def _candidate(asset: dict[str, Any]) -> bool:
        kind = _kind(asset)
        meta = asset.get("meta", {}) or {}
        duration = float(meta.get("duration") or 0.0)
        if kind == "text":
            return True
        if kind == "video":
            return duration >= 90.0
        if kind == "audio":
            return duration >= 60.0
        return False

    @staticmethod
    def _fingerprint(asset: dict[str, Any]) -> str:
        path = str(asset.get("path", "") or "")
        try:
            stat = Path(path).stat()
            source = f"{int(stat.st_size)}:{int(stat.st_mtime_ns)}"
        except OSError:
            source = "missing"
        return f"{asset.get('id','')}:{_kind(asset)}:{source}"

    def _key(self, assets: list[dict[str, Any]]) -> str:
        return "|".join(sorted(self._fingerprint(asset) for asset in assets))

    def _remember(self, key: str, now: float) -> None:
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_seen:
            self._seen.popitem(last=False)

    def _should_schedule(self, key: str, now: float) -> bool:
        previous = self._seen.get(key)
        if previous is None:
            return True
        return now - previous >= self.retry_seconds

    async def _run(
        self,
        client: MultimodalRetrievalClient,
        assets: list[dict[str, Any]],
        key: str,
    ) -> None:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        try:
            async with self._semaphore:
                include_audio = any(_kind(asset) == "audio" for asset in assets)
                result = await client.preindex(
                    assets,
                    include_audio=include_audio,
                )
            if result.accepted:
                self.completed += 1
            else:
                self.failed += 1
                # Allow a later request to retry after the configured cool-down rather than
                # permanently treating a missing/down indexer as warm.
                self._seen[key] = time.monotonic() - self.retry_seconds
        except Exception:
            # This task is never allowed to affect a product request. The client already
            # converts normal transport errors into values; this protects against unexpected
            # programmer/runtime failures in the optimization path.
            self.failed += 1
            self._seen[key] = time.monotonic() - self.retry_seconds

    def schedule(
        self,
        client: MultimodalRetrievalClient,
        assets: list[dict[str, Any]],
    ) -> bool:
        if not self.enabled or not client.enabled:
            return False
        candidates = [dict(asset) for asset in assets if self._candidate(asset)]
        if not candidates:
            return False
        now = time.monotonic()
        key = self._key(candidates)
        if not key or not self._should_schedule(key, now):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._remember(key, now)
        task = loop.create_task(self._run(client, candidates, key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self.scheduled += 1
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "preindex_enabled": self.enabled,
            "preindex_scheduled_total": self.scheduled,
            "preindex_completed_total": self.completed,
            "preindex_failed_total": self.failed,
            "preindex_inflight": len(self._tasks),
        }
