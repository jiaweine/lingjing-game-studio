from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException

from .video_segments import materialize_video_segment, merge_windows, segment_windows
from .worker_wemm import ScoreItem, ScoreRequest, WeMMRuntime

app = FastAPI(title="Lingjing Hierarchical WeMM Worker", version="0.1.0")


class HierarchicalWeMMRuntime(WeMMRuntime):
    """WeMM worker with VideoARM-style coarse-to-fine segment navigation.

    Short videos/images use the normal embedding path. A long video is first represented by
    a bounded set of coarse clips; only the best coarse regions are expanded into fine clips.
    Segment files and embeddings are cached. The returned start/end interval is a locator,
    not authoritative evidence: WorldForge re-opens the raw video for exact/dense frames.
    """

    def __init__(self) -> None:
        super().__init__()
        self.segment_threshold = max(
            30.0, float(os.getenv("WEMM_SEGMENT_THRESHOLD_SECONDS", "90"))
        )
        self.coarse_seconds = max(
            30.0, float(os.getenv("WEMM_VIDEO_COARSE_SECONDS", "240"))
        )
        self.fine_seconds = max(
            4.0, float(os.getenv("WEMM_VIDEO_FINE_SECONDS", "24"))
        )
        self.max_coarse_windows = max(
            4, int(os.getenv("WEMM_VIDEO_MAX_COARSE_WINDOWS", "24"))
        )
        self.top_coarse = max(1, int(os.getenv("WEMM_VIDEO_TOP_COARSE", "2")))
        self.max_fine_windows = max(
            4, int(os.getenv("WEMM_VIDEO_MAX_FINE_WINDOWS", "24"))
        )

    @property
    def backend_name(self) -> str:
        return f"wemm-hierarchical:{self.model_name}:{self.dimension}d"

    def _vectors_for_segments(
        self,
        item: ScoreItem,
        windows: list[tuple[float, float]],
        *,
        level: str,
    ) -> list[tuple[float, float, np.ndarray]]:
        cached_rows: list[tuple[float, float, np.ndarray]] = []
        missing: list[tuple[float, float, str, tuple[int, int], str]] = []

        for start, end in windows:
            clip = materialize_video_segment(
                item.path,
                asset_key=item.key,
                start=start,
                end=end,
            )
            if not clip:
                continue
            try:
                fingerprint = self._fingerprint(clip)
            except OSError:
                continue
            cache_key = f"video-segment:{level}:{item.key}:{start:.3f}:{end:.3f}"
            vector = self.asset_cache.get(cache_key, fingerprint)
            if vector is not None:
                cached_rows.append((start, end, vector))
            else:
                missing.append((start, end, clip, fingerprint, cache_key))

        if missing:
            samples = [{"video": clip} for _start, _end, clip, _fp, _key in missing]
            encoded = self._encode(samples)
            for (start, end, _clip, fingerprint, cache_key), vector in zip(
                missing, encoded, strict=True
            ):
                self.asset_cache.put(cache_key, fingerprint, vector)
                cached_rows.append((start, end, vector))

        cached_rows.sort(key=lambda row: row[0])
        return cached_rows

    @staticmethod
    def _rank_segments(
        query_vector: np.ndarray,
        rows: list[tuple[float, float, np.ndarray]],
    ) -> list[tuple[float, float, float]]:
        scored = [
            (float(np.dot(query_vector, vector)), start, end)
            for start, end, vector in rows
        ]
        scored.sort(reverse=True)
        return scored

    def _score_long_video(
        self,
        item: ScoreItem,
        query_vector: np.ndarray,
    ) -> dict[str, Any] | None:
        duration = float(item.duration or 0.0)
        if duration <= 0:
            return None

        coarse = segment_windows(
            0.0,
            duration,
            self.coarse_seconds,
            max_windows=self.max_coarse_windows,
        )
        coarse_vectors = self._vectors_for_segments(item, coarse, level="coarse")
        ranked_coarse = self._rank_segments(query_vector, coarse_vectors)
        if not ranked_coarse:
            return None

        fine_candidates = []
        for _score, start, end in ranked_coarse[: self.top_coarse]:
            fine_candidates.extend(
                segment_windows(
                    start,
                    end,
                    self.fine_seconds,
                    max_windows=max(2, self.max_fine_windows // self.top_coarse),
                )
            )
        fine = merge_windows(fine_candidates)[: self.max_fine_windows]
        fine_vectors = self._vectors_for_segments(item, fine, level="fine")
        ranked_fine = self._rank_segments(query_vector, fine_vectors)

        if ranked_fine:
            score, start, end = ranked_fine[0]
            level = "fine"
        else:
            score, start, end = ranked_coarse[0]
            level = "coarse"

        return {
            "key": item.key,
            "score": round(max(-1.0, min(1.0, score)), 7),
            "modality": "video",
            "start": round(start, 3),
            "end": round(end, 3),
            "evidence_ref": (
                f"asset:{item.key}:segment:{level}:{start:.3f}-{end:.3f}"
            ),
        }

    def _score_sync(self, request: ScoreRequest) -> dict[str, Any]:
        long_videos: list[ScoreItem] = []
        direct_items: list[ScoreItem] = []
        for item in request.items:
            if (
                item.modality == "video"
                and float(item.duration or 0.0) >= self.segment_threshold
                and Path(item.path).is_file()
            ):
                long_videos.append(item)
            else:
                direct_items.append(item)

        base = super()._score_sync(
            ScoreRequest(query=request.query, items=direct_items)
        )
        scores = list(base.get("scores", []) or [])
        query_vector = self._query_vector(request.query)
        for item in long_videos:
            row = self._score_long_video(item, query_vector)
            if row:
                scores.append(row)
        scores.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
        return {
            "backend": self.backend_name,
            "dimension": self.dimension,
            "scores": scores,
            "cache_entries": len(self.asset_cache),
            "hierarchical_videos": len(long_videos),
        }


RUNTIME = HierarchicalWeMMRuntime()


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "backend": RUNTIME.backend_name,
        "device": RUNTIME.device,
        "dimension": RUNTIME.dimension,
        "model_loaded": RUNTIME._model is not None,
        "asset_cache_entries": len(RUNTIME.asset_cache),
        "segment_threshold": RUNTIME.segment_threshold,
        "coarse_seconds": RUNTIME.coarse_seconds,
        "fine_seconds": RUNTIME.fine_seconds,
    }


@app.post("/v1/score")
async def score(request: ScoreRequest) -> dict[str, Any]:
    try:
        async with RUNTIME.gpu_lock:
            return await asyncio.to_thread(RUNTIME._score_sync, request)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
