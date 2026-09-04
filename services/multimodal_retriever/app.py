from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Lingjing Multimodal Retrieval Coordinator", version="0.1.0")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+#@-]{2,}")
_AUDIO_MARKERS = (
    "音频", "声音", "语音", "台词", "说话", "声效", "音效", "音乐", "听到",
    "audio", "sound", "voice", "speech", "music",
)
_TIME_MARKERS = ("秒", "分钟", "时间", "附近", "区间", "s", "sec", "min", ":")


def _tokens(text: str) -> set[str]:
    value = re.sub(r"\s+", " ", str(text or "").strip()).lower()
    out = {token.lower() for token in _TOKEN_RE.findall(value)}
    for run in _CJK_RE.findall(value):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[index:index + 2] for index in range(len(run) - 1))
            if len(run) <= 8:
                out.add(run)
    return out


def _audio_query(text: str) -> bool:
    value = str(text or "").lower()
    return any(marker in value for marker in _AUDIO_MARKERS)


def _temporal_query(text: str) -> bool:
    value = str(text or "").lower()
    return any(marker in value for marker in _TIME_MARKERS) and any(ch.isdigit() for ch in value)


class AssetDescriptor(BaseModel):
    id: str
    name: str = ""
    mime: str = ""
    path: str = ""
    size: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class RankRequest(BaseModel):
    query: str
    top_k: int = Field(default=24, ge=1, le=200)
    assets: list[AssetDescriptor]


@dataclass(frozen=True)
class WorkerScore:
    asset_id: str
    score: float
    backend: str
    modality: str
    start: float | None = None
    end: float | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class WorkerResult:
    backend: str
    scores: list[WorkerScore]
    latency_ms: float
    error: str | None = None


class WorkerClient:
    def __init__(self, url: str, *, timeout_seconds: float = 0.9) -> None:
        self.url = url.strip().rstrip("/")
        self.timeout_seconds = max(0.05, float(timeout_seconds))

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def score(
        self,
        *,
        query: str,
        items: list[dict[str, Any]],
        backend_hint: str,
    ) -> WorkerResult:
        if not self.enabled or not items:
            return WorkerResult(backend_hint, [], 0.0, "disabled")
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.url}/v1/score",
                    json={"query": query, "items": items},
                )
            elapsed = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                return WorkerResult(backend_hint, [], elapsed, f"http_{response.status_code}")
            data = response.json()
            backend = str(data.get("backend") or backend_hint)
            allowed = {str(item.get("key", "")) for item in items}
            rows: list[WorkerScore] = []
            for raw in list(data.get("scores", []) or []):
                asset_id = str(raw.get("key", ""))
                if asset_id not in allowed:
                    continue
                try:
                    score = float(raw.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(score):
                    continue
                rows.append(
                    WorkerScore(
                        asset_id=asset_id,
                        score=max(-1.0, min(1.0, score)),
                        backend=backend,
                        modality=str(raw.get("modality") or "unknown"),
                        start=(float(raw["start"]) if raw.get("start") is not None else None),
                        end=(float(raw["end"]) if raw.get("end") is not None else None),
                        evidence_ref=(str(raw.get("evidence_ref")) if raw.get("evidence_ref") else None),
                    )
                )
            return WorkerResult(backend, rows, elapsed)
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            return WorkerResult(backend_hint, [], elapsed, type(exc).__name__)


VISUAL_WORKER = WorkerClient(
    os.getenv("LINGJING_WEMM_WORKER_URL", ""),
    timeout_seconds=float(os.getenv("LINGJING_WEMM_WORKER_TIMEOUT_MS", "900")) / 1000.0,
)
AUDIO_WORKER = WorkerClient(
    os.getenv("LINGJING_LCO_WORKER_URL", ""),
    timeout_seconds=float(os.getenv("LINGJING_LCO_WORKER_TIMEOUT_MS", "1100")) / 1000.0,
)


def _kind(asset: AssetDescriptor) -> str:
    kind = str(asset.meta.get("kind", "") or "")
    if kind and kind != "file":
        return kind
    if asset.mime.startswith("image/"):
        return "image"
    if asset.mime.startswith("video/"):
        return "video"
    if asset.mime.startswith("audio/"):
        return "audio"
    if asset.mime.startswith("text/"):
        return "text"
    return kind or "file"


def _lexical_score(query_tokens: set[str], asset: AssetDescriptor) -> float:
    searchable = " ".join(
        [
            asset.name,
            asset.mime,
            str(asset.meta.get("build", "")),
            str(asset.meta.get("branch", "")),
            str(asset.meta.get("commit", "")),
        ]
    )
    doc_tokens = _tokens(searchable)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens) / len(query_tokens)
    exact_identifiers = [
        token for token in query_tokens
        if any(ch.isdigit() for ch in token) or "_" in token or "." in token
    ]
    lowered = searchable.lower()
    exact = sum(1 for token in exact_identifiers if token in lowered)
    return min(1.0, overlap * 0.75 + min(0.25, exact * 0.08))


def _worker_items(assets: list[AssetDescriptor], *, audio_query: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visual: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    for asset in assets:
        kind = _kind(asset)
        base = {
            "key": asset.id,
            "path": asset.path,
            "mime": asset.mime,
            "name": asset.name,
            "duration": asset.meta.get("duration"),
        }
        if kind in {"image", "video"}:
            visual.append({**base, "modality": kind})
        # LCO is intentionally specialist-routed. We do not spend 7B-model GPU time on
        # audio/video acoustics unless the query actually asks about sound/speech/music.
        if audio_query and kind == "audio":
            audio.append({**base, "modality": "audio"})
        elif audio_query and kind == "video" and asset.meta.get("has_audio"):
            audio.append({**base, "modality": "video_with_audio"})
    return visual, audio


def _normalize_backend(rows: list[WorkerScore]) -> dict[str, float]:
    if not rows:
        return {}
    values = [row.score for row in rows]
    low, high = min(values), max(values)
    if high - low < 1e-8:
        return {row.asset_id: max(0.0, min(1.0, (row.score + 1.0) / 2.0)) for row in rows}
    return {row.asset_id: (row.score - low) / (high - low) for row in rows}


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "visual_worker": VISUAL_WORKER.enabled,
        "audio_worker": AUDIO_WORKER.enabled,
    }


@app.post("/v1/rank")
async def rank(request: RankRequest) -> dict[str, Any]:
    started = time.perf_counter()
    query_tokens = _tokens(request.query)
    wants_audio = _audio_query(request.query)
    wants_time = _temporal_query(request.query)
    visual_items, audio_items = _worker_items(request.assets, audio_query=wants_audio)

    visual_task = VISUAL_WORKER.score(
        query=request.query,
        items=visual_items,
        backend_hint="wemm",
    )
    audio_task = AUDIO_WORKER.score(
        query=request.query,
        items=audio_items,
        backend_hint="lco",
    )
    visual_result, audio_result = await asyncio.gather(visual_task, audio_task)

    visual_norm = _normalize_backend(visual_result.scores)
    audio_norm = _normalize_backend(audio_result.scores)
    visual_raw = {row.asset_id: row for row in visual_result.scores}
    audio_raw = {row.asset_id: row for row in audio_result.scores}

    hits: list[dict[str, Any]] = []
    for asset in request.assets:
        lexical = _lexical_score(query_tokens, asset)
        visual = visual_norm.get(asset.id, 0.0)
        audio = audio_norm.get(asset.id, 0.0)
        kind = _kind(asset)

        # Recall union, not model monopoly. Exact identifiers/metadata still matter; WeMM
        # owns visual semantic recall; LCO owns acoustic recall only on audio-intent turns.
        score = max(
            lexical * 0.55,
            visual * 0.88 + lexical * 0.12,
            audio * 0.90 + lexical * 0.10,
        )
        if wants_time and kind in {"video", "audio"}:
            score += 0.04
        score = min(1.0, score)

        if score <= 0.0 and not (visual or audio):
            continue
        source = visual_raw.get(asset.id) or audio_raw.get(asset.id)
        hit = {
            "asset_id": asset.id,
            "score": round(score, 6),
            "modality": kind,
        }
        if source is not None:
            if source.start is not None:
                hit["start"] = source.start
            if source.end is not None:
                hit["end"] = source.end
            if source.evidence_ref:
                hit["evidence_ref"] = source.evidence_ref
        hits.append(hit)

    hits.sort(key=lambda row: row["score"], reverse=True)
    active_backends = [
        result.backend
        for result in (visual_result, audio_result)
        if result.scores and not result.error
    ]
    backend = "+".join(active_backends) if active_backends else "lexical-fallback"
    elapsed = (time.perf_counter() - started) * 1000.0
    return {
        "backend": backend,
        "latency_ms": round(elapsed, 3),
        "hits": hits[: request.top_k],
        "debug": {
            "visual_latency_ms": round(visual_result.latency_ms, 3),
            "audio_latency_ms": round(audio_result.latency_ms, 3),
            "visual_error": visual_result.error,
            "audio_error": audio_result.error,
            "audio_query": wants_audio,
        },
    }
