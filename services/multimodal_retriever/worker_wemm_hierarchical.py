from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .text_chunks import TextChunk, stream_text_chunks
from .video_segments import materialize_video_segment, merge_windows, segment_windows
from .worker_wemm import ScoreItem, ScoreRequest, WeMMRuntime

app = FastAPI(title="Lingjing Hierarchical WeMM Worker", version="0.2.0")


class IndexRequest(BaseModel):
    items: list[ScoreItem] = Field(default_factory=list, max_length=128)


class HierarchicalWeMMRuntime(WeMMRuntime):
    """WeMM worker with bounded online retrieval and rebuildable offline indexing.

    Online requests may cold-build only a small amount of derived state. Full text/video
    indexing is exposed separately through /v1/index so production can point indexing at a
    dedicated low-priority GPU replica. Source-level builds are singleflight across workers;
    concurrent losers reuse partial vectors or return no semantic row instead of waiting.
    Cooperative deadlines are checked between GPU batches and coarse/fine stages.
    """

    def __init__(self) -> None:
        super().__init__()
        self.build_owner = f"{socket.gethostname()}:{os.getpid()}:{id(self)}"
        self.build_lease_seconds = max(
            30.0, float(os.getenv("WEMM_BUILD_LEASE_SECONDS", "300"))
        )
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
        self.online_max_coarse_windows = max(
            2, int(os.getenv("WEMM_ONLINE_MAX_COARSE_WINDOWS", "8"))
        )
        self.online_max_fine_windows = max(
            2, int(os.getenv("WEMM_ONLINE_MAX_FINE_WINDOWS", "8"))
        )
        self.text_chunk_chars = max(
            1000, int(os.getenv("WEMM_TEXT_CHUNK_CHARS", "8000"))
        )
        self.text_overlap_chars = max(
            0, int(os.getenv("WEMM_TEXT_OVERLAP_CHARS", "800"))
        )
        self.text_max_chunks = max(
            32, int(os.getenv("WEMM_TEXT_MAX_CHUNKS", "20000"))
        )
        self.online_text_max_chunks = max(
            1, int(os.getenv("WEMM_ONLINE_TEXT_MAX_CHUNKS", "16"))
        )
        self.text_excerpt_chars = max(
            400, int(os.getenv("WEMM_TEXT_EXCERPT_CHARS", "2800"))
        )
        self.text_index_batch = max(
            1, int(os.getenv("WEMM_TEXT_INDEX_BATCH", "32"))
        )
        # Protected by the single GPU lane. Keeping the deadline as transient runtime state
        # lets lower-level helpers cooperate without changing their public/tested signatures.
        self._active_deadline: float | None = None

    @property
    def backend_name(self) -> str:
        return f"wemm-hierarchical:{self.model_name}:{self.dimension}d"

    def _source_fingerprint(self, item: ScoreItem) -> str | None:
        try:
            return self._fingerprint_string(self._fingerprint(item.path))
        except OSError:
            return None

    def _vectors_for_segments(
        self,
        item: ScoreItem,
        windows: list[tuple[float, float]],
        *,
        level: str,
    ) -> list[tuple[float, float, np.ndarray]]:
        cached_rows: list[tuple[float, float, np.ndarray]] = []
        missing: list[tuple[float, float, str, tuple[int, int], str]] = []
        source_fp = self._source_fingerprint(item) or "unknown"

        for start, end in windows:
            if self._deadline_exhausted(self._active_deadline):
                break
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
            vector = self._get_cached_vector(cache_key, fingerprint)
            if vector is not None:
                cached_rows.append((start, end, vector))
                self.vector_store.put_unit(
                    cache_key=cache_key,
                    backend=self.backend_name,
                    source_key=item.key,
                    source_fingerprint=source_fp,
                    modality="video",
                    start=start,
                    end=end,
                )
            else:
                missing.append((start, end, clip, fingerprint, cache_key))

        # Externalize the batching so an online request can stop between CUDA batches. An
        # offline index build has no active deadline and therefore simply runs all batches.
        for offset in range(0, len(missing), self.batch_size):
            if self._deadline_exhausted(self._active_deadline):
                break
            batch = missing[offset : offset + self.batch_size]
            encoded = self._encode(
                [{"video": clip} for _start, _end, clip, _fp, _key in batch]
            )
            for (start, end, _clip, fingerprint, cache_key), vector in zip(
                batch, encoded, strict=True
            ):
                self._put_cached_vector(cache_key, fingerprint, vector)
                self.vector_store.put_unit(
                    cache_key=cache_key,
                    backend=self.backend_name,
                    source_key=item.key,
                    source_fingerprint=source_fp,
                    modality="video",
                    start=start,
                    end=end,
                )
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

    def _stored_video_rows(
        self,
        item: ScoreItem,
        source_fp: str,
        *,
        level: str | None = None,
    ) -> list[tuple[float, float, np.ndarray]]:
        rows = self.vector_store.list_source_vectors(
            source_key=item.key,
            source_fingerprint=source_fp,
            backend=self.backend_name,
            modality="video",
        )
        out: list[tuple[float, float, np.ndarray]] = []
        marker = f"video-segment:{level}:" if level else None
        for row in rows:
            if marker and marker not in str(row.get("cache_key", "")):
                continue
            if row.get("start") is None or row.get("end") is None:
                continue
            out.append((float(row["start"]), float(row["end"]), row["vector"]))
        return out

    def _cached_source_segment(
        self,
        item: ScoreItem,
        source_fp: str,
        query_vector: np.ndarray,
    ) -> dict[str, Any] | None:
        ranked = self._rank_segments(
            query_vector, self._stored_video_rows(item, source_fp)
        )
        if not ranked:
            return None
        score, start, end = ranked[0]
        return {
            "key": item.key,
            "score": round(max(-1.0, min(1.0, score)), 7),
            "modality": "video",
            "start": round(start, 3),
            "end": round(end, 3),
            "evidence_ref": (
                f"asset:{item.key}:segment:cached:{start:.3f}-{end:.3f}"
            ),
        }

    def _score_long_video(
        self,
        item: ScoreItem,
        query_vector: np.ndarray,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any] | None:
        duration = float(item.duration or 0.0)
        source_fp = self._source_fingerprint(item)
        if duration <= 0 or source_fp is None:
            return None

        lease_key = f"build:video:{item.key}:{source_fp}"
        claimed = self.vector_store.try_claim_build(
            cache_key=lease_key,
            backend=self.backend_name,
            owner=self.build_owner,
            lease_seconds=self.build_lease_seconds,
        )
        if not claimed:
            return self._cached_source_segment(item, source_fp, query_vector)

        try:
            coarse_vectors = self._stored_video_rows(item, source_fp, level="coarse")
            if not coarse_vectors:
                if self._deadline_exhausted(deadline):
                    return None
                coarse = segment_windows(
                    0.0,
                    duration,
                    self.coarse_seconds,
                    max_windows=self.online_max_coarse_windows,
                )
                coarse_vectors = self._vectors_for_segments(item, coarse, level="coarse")
            self.vector_store.refresh_build_lease(
                cache_key=lease_key,
                backend=self.backend_name,
                owner=self.build_owner,
                lease_seconds=self.build_lease_seconds,
            )
            ranked_coarse = self._rank_segments(query_vector, coarse_vectors)
            if not ranked_coarse:
                return None

            if self._deadline_exhausted(deadline):
                score, start, end = ranked_coarse[0]
                return {
                    "key": item.key,
                    "score": round(max(-1.0, min(1.0, score)), 7),
                    "modality": "video",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "evidence_ref": (
                        f"asset:{item.key}:segment:coarse-budget:{start:.3f}-{end:.3f}"
                    ),
                }

            fine_candidates: list[tuple[float, float]] = []
            for _score, start, end in ranked_coarse[: self.top_coarse]:
                fine_candidates.extend(
                    segment_windows(
                        start,
                        end,
                        self.fine_seconds,
                        max_windows=max(
                            2,
                            self.online_max_fine_windows // self.top_coarse,
                        ),
                    )
                )
            fine = merge_windows(fine_candidates)[: self.online_max_fine_windows]
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
        finally:
            self.vector_store.release_build(
                cache_key=lease_key,
                backend=self.backend_name,
                owner=self.build_owner,
            )

    def _index_text_batch(
        self,
        item: ScoreItem,
        source_fp: str,
        batch: list[TextChunk],
    ) -> None:
        missing: list[tuple[TextChunk, tuple[int, int], str]] = []
        for chunk in batch:
            fingerprint = self._text_fingerprint(chunk.text)
            cache_key = f"text-chunk:{item.key}:{chunk.start}:{chunk.end}"
            vector = self._get_cached_vector(cache_key, fingerprint)
            if vector is None:
                missing.append((chunk, fingerprint, cache_key))
            else:
                self.vector_store.put_unit(
                    cache_key=cache_key,
                    backend=self.backend_name,
                    source_key=item.key,
                    source_fingerprint=source_fp,
                    modality="text",
                    char_start=chunk.start,
                    char_end=chunk.end,
                    excerpt=chunk.text[: self.text_excerpt_chars],
                )

        if missing:
            encoded = self._encode([chunk.text for chunk, _fp, _key in missing])
            for (chunk, fingerprint, cache_key), vector in zip(
                missing, encoded, strict=True
            ):
                self._put_cached_vector(cache_key, fingerprint, vector)
                self.vector_store.put_unit(
                    cache_key=cache_key,
                    backend=self.backend_name,
                    source_key=item.key,
                    source_fingerprint=source_fp,
                    modality="text",
                    char_start=chunk.start,
                    char_end=chunk.end,
                    excerpt=chunk.text[: self.text_excerpt_chars],
                )

    def _ensure_text_index(
        self,
        item: ScoreItem,
        source_fp: str,
        *,
        full_build: bool = False,
    ) -> list[dict[str, Any]]:
        config = (
            f"{self.text_chunk_chars}:{self.text_overlap_chars}:{self.text_max_chunks}"
        )
        complete_key = (
            f"complete:{self.backend_name}:text:{item.key}:{source_fp}:{config}"
        )
        expected_raw = self.vector_store.get_meta(complete_key)
        rows = self.vector_store.list_source_vectors(
            source_key=item.key,
            source_fingerprint=source_fp,
            backend=self.backend_name,
            modality="text",
        )
        try:
            expected = int(expected_raw) if expected_raw is not None else -1
        except ValueError:
            expected = -1
        if expected >= 0 and len(rows) == expected:
            return rows
        if rows and not full_build:
            return rows

        lease_key = f"build:text:{item.key}:{source_fp}:{config}"
        claimed = self.vector_store.try_claim_build(
            cache_key=lease_key,
            backend=self.backend_name,
            owner=self.build_owner,
            lease_seconds=self.build_lease_seconds,
        )
        if not claimed:
            return rows

        max_chunks = self.text_max_chunks if full_build else self.online_text_max_chunks
        # Keep offline throughput high, but online work must be stoppable after each actual
        # model batch rather than hiding many batches inside one large model.encode call.
        batch_limit = self.text_index_batch if full_build else min(
            self.text_index_batch, self.batch_size
        )
        try:
            batch: list[TextChunk] = []
            count = 0
            for chunk in stream_text_chunks(
                item.path,
                chunk_chars=self.text_chunk_chars,
                overlap_chars=self.text_overlap_chars,
                max_chunks=max_chunks,
            ):
                if not full_build and self._deadline_exhausted(self._active_deadline):
                    break
                batch.append(chunk)
                count += 1
                if len(batch) >= batch_limit:
                    if not full_build and self._deadline_exhausted(self._active_deadline):
                        break
                    self._index_text_batch(item, source_fp, batch)
                    batch = []
                    self.vector_store.refresh_build_lease(
                        cache_key=lease_key,
                        backend=self.backend_name,
                        owner=self.build_owner,
                        lease_seconds=self.build_lease_seconds,
                    )
            if batch and (
                full_build or not self._deadline_exhausted(self._active_deadline)
            ):
                self._index_text_batch(item, source_fp, batch)
            # Only a true full scan gets a completion marker. A deadline-shortened online
            # prefix is valid partial evidence but must never masquerade as a complete index.
            if full_build or (
                count < max_chunks and not self._deadline_exhausted(self._active_deadline)
            ):
                self.vector_store.set_meta(complete_key, str(count))
            return self.vector_store.list_source_vectors(
                source_key=item.key,
                source_fingerprint=source_fp,
                backend=self.backend_name,
                modality="text",
            )
        finally:
            self.vector_store.release_build(
                cache_key=lease_key,
                backend=self.backend_name,
                owner=self.build_owner,
            )

    def _score_text_file(
        self,
        item: ScoreItem,
        query_vector: np.ndarray,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any] | None:
        path = Path(item.path)
        if not path.is_file():
            return None
        source_fp = self._source_fingerprint(item)
        if source_fp is None:
            return None
        if self._deadline_exhausted(deadline):
            rows = self.vector_store.list_source_vectors(
                source_key=item.key,
                source_fingerprint=source_fp,
                backend=self.backend_name,
                modality="text",
            )
        else:
            rows = self._ensure_text_index(item, source_fp, full_build=False)
        if not rows:
            return None

        matrix = np.vstack([row["vector"] for row in rows])
        scores = matrix @ query_vector
        best_index = int(np.argmax(scores))
        best = rows[best_index]
        score = float(scores[best_index])
        char_start = int(best.get("char_start") or 0)
        char_end = int(best.get("char_end") or char_start)
        return {
            "key": item.key,
            "score": round(max(-1.0, min(1.0, score)), 7),
            "modality": "text",
            "char_start": char_start,
            "char_end": char_end,
            "text_excerpt": str(best.get("excerpt") or "")[: self.text_excerpt_chars],
            "evidence_ref": f"asset:{item.key}:chars:{char_start}-{char_end}",
        }

    def _preindex_long_video(self, item: ScoreItem) -> bool:
        duration = float(item.duration or 0.0)
        source_fp = self._source_fingerprint(item)
        if duration <= 0 or source_fp is None:
            return False
        lease_key = f"build:video:{item.key}:{source_fp}"
        if not self.vector_store.try_claim_build(
            cache_key=lease_key,
            backend=self.backend_name,
            owner=self.build_owner,
            lease_seconds=self.build_lease_seconds,
        ):
            return False
        try:
            coarse = segment_windows(
                0.0,
                duration,
                self.coarse_seconds,
                max_windows=self.max_coarse_windows,
            )
            self._vectors_for_segments(item, coarse, level="coarse")
            return True
        finally:
            self.vector_store.release_build(
                cache_key=lease_key,
                backend=self.backend_name,
                owner=self.build_owner,
            )

    def _index_sync(self, request: IndexRequest) -> dict[str, Any]:
        # Offline indexing must never inherit a stale online deadline.
        previous_deadline = self._active_deadline
        self._active_deadline = None
        indexed_text = 0
        indexed_video = 0
        skipped = 0
        try:
            for item in request.items:
                if item.modality == "text_file" and Path(item.path).is_file():
                    source_fp = self._source_fingerprint(item)
                    if source_fp is None:
                        skipped += 1
                        continue
                    self._ensure_text_index(item, source_fp, full_build=True)
                    indexed_text += 1
                elif (
                    item.modality == "video"
                    and float(item.duration or 0.0) >= self.segment_threshold
                    and Path(item.path).is_file()
                ):
                    if self._preindex_long_video(item):
                        indexed_video += 1
                    else:
                        skipped += 1
                else:
                    skipped += 1
        finally:
            self._active_deadline = previous_deadline
        return {
            "backend": self.backend_name,
            "indexed_text_files": indexed_text,
            "indexed_long_videos": indexed_video,
            "skipped": skipped,
            "persistent_cache_entries": self.vector_store.count(),
        }

    def _score_sync(self, request: ScoreRequest) -> dict[str, Any]:
        deadline = self._deadline_for_request(request)
        previous_deadline = self._active_deadline
        self._active_deadline = deadline
        try:
            long_videos: list[ScoreItem] = []
            text_files: list[ScoreItem] = []
            direct_items: list[ScoreItem] = []
            for item in request.items:
                if item.modality == "text_file" and Path(item.path).is_file():
                    text_files.append(item)
                elif (
                    item.modality == "video"
                    and float(item.duration or 0.0) >= self.segment_threshold
                    and Path(item.path).is_file()
                ):
                    long_videos.append(item)
                else:
                    direct_items.append(item)

            scores: list[dict[str, Any]] = []
            if direct_items and not self._deadline_exhausted(deadline):
                remaining = self._remaining_budget_ms(deadline)
                base = super()._score_sync(
                    ScoreRequest(
                        query=request.query,
                        items=direct_items,
                        budget_ms=remaining,
                    )
                )
                scores.extend(list(base.get("scores", []) or []))

            if (long_videos or text_files) and not self._deadline_exhausted(deadline):
                query_vector = self._query_vector(request.query)
                for item in long_videos:
                    if self._deadline_exhausted(deadline):
                        break
                    row = self._score_long_video(
                        item,
                        query_vector,
                        deadline=deadline,
                    )
                    if row:
                        scores.append(row)
                for item in text_files:
                    if self._deadline_exhausted(deadline):
                        break
                    row = self._score_text_file(
                        item,
                        query_vector,
                        deadline=deadline,
                    )
                    if row:
                        scores.append(row)

            scores.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
            return {
                "backend": self.backend_name,
                "dimension": self.dimension,
                "scores": scores,
                "memory_cache_entries": len(self.asset_cache),
                "persistent_cache_entries": self.vector_store.count(),
                "hierarchical_videos": len(long_videos),
                "semantic_text_files": len(text_files),
                "budget_exhausted": self._deadline_exhausted(deadline),
            }
        finally:
            self._active_deadline = previous_deadline


RUNTIME = HierarchicalWeMMRuntime()


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "backend": RUNTIME.backend_name,
        "device": RUNTIME.device,
        "dimension": RUNTIME.dimension,
        "model_loaded": RUNTIME._model is not None,
        "memory_cache_entries": len(RUNTIME.asset_cache),
        "persistent_cache_entries": RUNTIME.vector_store.count(),
        "segment_threshold": RUNTIME.segment_threshold,
        "coarse_seconds": RUNTIME.coarse_seconds,
        "fine_seconds": RUNTIME.fine_seconds,
        "text_chunk_chars": RUNTIME.text_chunk_chars,
        "online_text_max_chunks": RUNTIME.online_text_max_chunks,
        "online_max_coarse_windows": RUNTIME.online_max_coarse_windows,
        "online_max_fine_windows": RUNTIME.online_max_fine_windows,
    }


@app.post("/v1/score")
async def score(request: ScoreRequest) -> dict[str, Any]:
    try:
        async with RUNTIME.gpu_lock:
            return await asyncio.to_thread(RUNTIME._score_sync, request)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/index")
async def index(request: IndexRequest) -> dict[str, Any]:
    """Full derived indexing endpoint intended for a dedicated low-priority replica."""
    try:
        async with RUNTIME.gpu_lock:
            return await asyncio.to_thread(RUNTIME._index_sync, request)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
