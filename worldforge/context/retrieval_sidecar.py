from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class MultimodalRetrievalHit:
    asset_id: str
    score: float
    modality: str | None = None
    start: float | None = None
    end: float | None = None
    char_start: int | None = None
    char_end: int | None = None
    text_excerpt: str | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class MultimodalRetrievalResult:
    hits: list[MultimodalRetrievalHit]
    backend: str | None
    latency_ms: float | None
    available: bool
    error: str | None = None

    def stats(self) -> dict[str, Any]:
        return {
            "semantic_retrieval_available": self.available,
            "semantic_retrieval_backend": self.backend,
            "semantic_retrieval_hits": len(self.hits),
            "semantic_retrieval_latency_ms": self.latency_ms,
            "semantic_retrieval_error": self.error,
        }


@dataclass(frozen=True)
class MultimodalIndexResult:
    accepted: bool
    latency_ms: float | None
    error: str | None = None
    details: dict[str, Any] | None = None


class MultimodalRetrievalClient:
    """Fail-open client for separately scalable multimodal retrieval/indexing.

    Online `rank` has a strict short timeout. Full derived indexing is a different lifecycle
    with its own longer timeout and must be routed by the coordinator only to explicitly
    configured indexer replicas. Neither path is authoritative: raw assets remain the source
    of truth, and all derived caches may be deleted and rebuilt.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        timeout_seconds: float | None = None,
        index_timeout_seconds: float | None = None,
        top_k: int = 24,
    ) -> None:
        self.endpoint = (
            endpoint
            if endpoint is not None
            else os.getenv("LINGJING_MM_RETRIEVER_URL", "")
        ).strip().rstrip("/")
        env_timeout = os.getenv("LINGJING_MM_RETRIEVER_TIMEOUT_MS", "1200")
        env_index_timeout = os.getenv(
            "LINGJING_MM_INDEX_TIMEOUT_MS", "305000"
        )
        self.timeout_seconds = (
            max(0.05, float(timeout_seconds))
            if timeout_seconds is not None
            else max(0.05, float(env_timeout) / 1000.0)
        )
        self.index_timeout_seconds = (
            max(0.1, float(index_timeout_seconds))
            if index_timeout_seconds is not None
            else max(0.1, float(env_index_timeout) / 1000.0)
        )
        self.top_k = max(1, int(top_k))

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    @staticmethod
    def _descriptor(asset: dict[str, Any]) -> dict[str, Any]:
        meta = asset.get("meta", {}) or {}
        path = str(asset.get("path", "") or "")
        try:
            size = (
                Path(path).stat().st_size
                if path and Path(path).is_file()
                else None
            )
        except OSError:
            size = None
        keep_meta = {
            key: meta.get(key)
            for key in (
                "kind",
                "duration",
                "width",
                "height",
                "has_audio",
                "sample_rate",
                "channels",
                "chars",
                "lines",
                "keyframes",
                "keyframe_times",
                "build",
                "branch",
                "commit",
            )
            if meta.get(key) is not None
        }
        return {
            "id": str(asset.get("id", "")),
            "name": str(asset.get("name", "")),
            "mime": str(asset.get("mime", "")),
            "path": path,
            "size": size,
            "meta": keep_meta,
        }

    async def rank(
        self, query: str, assets: list[dict[str, Any]]
    ) -> MultimodalRetrievalResult:
        if not self.enabled or not assets:
            return MultimodalRetrievalResult([], None, None, False)
        payload = {
            "query": str(query),
            "top_k": min(self.top_k, max(1, len(assets))),
            "assets": [self._descriptor(asset) for asset in assets],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/rank", json=payload
                )
            if response.status_code >= 400:
                return MultimodalRetrievalResult(
                    [], None, None, False, f"http_{response.status_code}"
                )
            data = response.json()
            allowed = {str(asset.get("id", "")) for asset in assets}
            hits: list[MultimodalRetrievalHit] = []
            seen: set[str] = set()
            for raw in list(data.get("hits", []) or []):
                asset_id = str(raw.get("asset_id", ""))
                if not asset_id or asset_id not in allowed or asset_id in seen:
                    continue
                try:
                    score = float(raw.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
                if not (score == score):
                    continue
                try:
                    start = (
                        float(raw["start"])
                        if raw.get("start") is not None
                        else None
                    )
                    end = (
                        float(raw["end"])
                        if raw.get("end") is not None
                        else None
                    )
                    char_start = (
                        int(raw["char_start"])
                        if raw.get("char_start") is not None
                        else None
                    )
                    char_end = (
                        int(raw["char_end"])
                        if raw.get("char_end") is not None
                        else None
                    )
                except (TypeError, ValueError):
                    continue
                excerpt = (
                    str(raw.get("text_excerpt") or "").strip()[:4000] or None
                )
                seen.add(asset_id)
                hits.append(
                    MultimodalRetrievalHit(
                        asset_id=asset_id,
                        score=max(-100.0, min(100.0, score)),
                        modality=(
                            str(raw.get("modality"))
                            if raw.get("modality")
                            else None
                        ),
                        start=start,
                        end=end,
                        char_start=char_start,
                        char_end=char_end,
                        text_excerpt=excerpt,
                        evidence_ref=(
                            str(raw.get("evidence_ref"))
                            if raw.get("evidence_ref")
                            else None
                        ),
                    )
                )
            hits.sort(key=lambda hit: hit.score, reverse=True)
            latency = data.get("latency_ms")
            return MultimodalRetrievalResult(
                hits=hits[: self.top_k],
                backend=(
                    str(data.get("backend")) if data.get("backend") else None
                ),
                latency_ms=(
                    float(latency) if latency is not None else None
                ),
                available=True,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return MultimodalRetrievalResult(
                [], None, None, False, type(exc).__name__
            )

    async def preindex(
        self,
        assets: list[dict[str, Any]],
        *,
        include_audio: bool = False,
    ) -> MultimodalIndexResult:
        """Best-effort warm-up; callers must never make product durability depend on it."""
        if not self.enabled or not assets:
            return MultimodalIndexResult(False, None, "disabled")
        payload = {
            "assets": [self._descriptor(asset) for asset in assets],
            "include_audio": bool(include_audio),
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.index_timeout_seconds
            ) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/index", json=payload
                )
            if response.status_code >= 400:
                return MultimodalIndexResult(
                    False, None, f"http_{response.status_code}"
                )
            data = dict(response.json() or {})
            latencies = []
            for key in ("visual", "audio"):
                value = (data.get(key) or {}).get("latency_ms")
                if value is not None:
                    try:
                        latencies.append(float(value))
                    except (TypeError, ValueError):
                        pass
            return MultimodalIndexResult(
                accepted=bool(data.get("accepted")),
                latency_ms=max(latencies) if latencies else None,
                details=data,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return MultimodalIndexResult(
                False, None, type(exc).__name__
            )


def apply_retrieval_hits(
    assets: list[dict[str, Any]],
    result: MultimodalRetrievalResult,
    *,
    max_semantic_promotions: int = 8,
) -> None:
    """Fuse sidecar hits without allowing an embedding model to erase baseline evidence."""
    if not result.available or not result.hits:
        return
    by_id = {str(asset.get("id", "")): asset for asset in assets}
    baseline_ranks = [
        int(
            (asset.get("meta", {}) or {})
            .get("_context", {})
            .get("rank")
            or 0
        )
        for asset in assets
    ]
    next_rank = max(baseline_ranks or [0]) + 1
    promoted = 0
    for semantic_rank, hit in enumerate(result.hits, start=1):
        asset = by_id.get(hit.asset_id)
        if asset is None:
            continue
        meta = asset.setdefault("meta", {})
        context = meta.setdefault("_context", {})
        context["semantic_score"] = hit.score
        context["semantic_rank"] = semantic_rank
        context["semantic_backend"] = result.backend
        if hit.start is not None:
            hints = list(context.get("time_hints", []) or [])
            if hit.start not in hints:
                hints.insert(0, hit.start)
            context["time_hints"] = hints[:6]
        if hit.char_start is not None:
            context["semantic_char_start"] = hit.char_start
        if hit.char_end is not None:
            context["semantic_char_end"] = hit.char_end
        if hit.text_excerpt:
            context["semantic_excerpt"] = hit.text_excerpt
        if hit.evidence_ref:
            context["semantic_evidence_ref"] = hit.evidence_ref
        if (
            not context.get("selected")
            and promoted < max_semantic_promotions
        ):
            context["selected"] = True
            context["rank"] = next_rank
            context.setdefault("reasons", []).append("semantic-retrieval")
            next_rank += 1
            promoted += 1
