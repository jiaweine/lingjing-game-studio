from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from threading import RLock
import time
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .vector_store import PersistentVectorStore

app = FastAPI(title="Lingjing WeMM Embedding Worker", version="0.1.0")


class ScoreItem(BaseModel):
    key: str
    path: str = ""
    mime: str = ""
    name: str = ""
    modality: str
    duration: float | None = None


class ScoreRequest(BaseModel):
    query: str
    items: list[ScoreItem] = Field(default_factory=list, max_length=128)
    # Cooperative compute budget. It cannot interrupt a CUDA kernel already in flight, but
    # it prevents the worker from starting another expensive batch after the caller's SLO
    # has effectively expired.
    budget_ms: int | None = Field(default=None, ge=50, le=120000)


@dataclass(frozen=True)
class CacheEntry:
    fingerprint: tuple[int, int]
    vector: np.ndarray


class LRUVectorCache:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(16, int(capacity))
        self._rows: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str, fingerprint: tuple[int, int]) -> np.ndarray | None:
        with self._lock:
            row = self._rows.get(key)
            if row is None or row.fingerprint != fingerprint:
                if row is not None:
                    self._rows.pop(key, None)
                return None
            self._rows.move_to_end(key)
            return row.vector

    def put(self, key: str, fingerprint: tuple[int, int], vector: np.ndarray) -> None:
        with self._lock:
            self._rows[key] = CacheEntry(
                fingerprint, vector.astype(np.float32, copy=False)
            )
            self._rows.move_to_end(key)
            while len(self._rows) > self.capacity:
                self._rows.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)


class WeMMRuntime:
    def __init__(self) -> None:
        self.model_name = os.getenv("WEMM_MODEL", "tencent/WeMM-Embedding-2B")
        self.device = os.getenv("WEMM_DEVICE", "cuda")
        self.dimension = int(os.getenv("WEMM_DIMENSION", "256"))
        self.batch_size = max(1, int(os.getenv("WEMM_BATCH_SIZE", "4")))
        self.asset_cache = LRUVectorCache(int(os.getenv("WEMM_ASSET_CACHE", "8192")))
        cache_root = Path(
            os.getenv("LINGJING_RETRIEVER_CACHE_DIR", "outputs/retrieval-cache")
        )
        self.vector_store = PersistentVectorStore(
            os.getenv("WEMM_VECTOR_CACHE_DB", str(cache_root / "wemm-vectors.sqlite3"))
        )
        self.persistent_max_rows = max(
            1000, int(os.getenv("WEMM_VECTOR_CACHE_MAX_ROWS", "200000"))
        )
        self.query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.query_cache_limit = max(16, int(os.getenv("WEMM_QUERY_CACHE", "512")))
        self._query_lock = RLock()
        self._model: Any = None
        self._model_lock = RLock()
        # A single GPU lane avoids uncontrolled concurrent model.encode calls. Scale worker
        # replicas horizontally when more throughput is required.
        self.gpu_lock = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        return f"wemm:{self.model_name}:{self.dimension}d"

    @staticmethod
    def _deadline_for_request(request: ScoreRequest) -> float | None:
        if request.budget_ms is None:
            return None
        return time.perf_counter() + max(0.05, request.budget_ms / 1000.0)

    @staticmethod
    def _deadline_exhausted(deadline: float | None) -> bool:
        return deadline is not None and time.perf_counter() >= deadline

    @staticmethod
    def _remaining_budget_ms(deadline: float | None) -> int | None:
        if deadline is None:
            return None
        return max(50, int((deadline - time.perf_counter()) * 1000.0))

    def _load(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is required; install requirements-wemm.txt"
                ) from exc
            model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True,
                device=self.device,
            )
            supported = getattr(
                getattr(model[0].auto_model, "config", None),
                "matryoshka_dimensions",
                None,
            )
            if supported is not None and self.dimension not in set(
                int(x) for x in supported
            ):
                raise RuntimeError(
                    f"WEMM_DIMENSION={self.dimension} not supported; available={supported}"
                )
            self._model = model
            return model

    @staticmethod
    def _fingerprint(path: str) -> tuple[int, int]:
        source = Path(path)
        stat = source.stat()
        return int(stat.st_size), int(stat.st_mtime_ns)

    @staticmethod
    def _text_fingerprint(text: str) -> tuple[int, int]:
        data = text.encode("utf-8", errors="ignore")
        digest = hashlib.sha256(data).digest()
        return len(data), int.from_bytes(digest[:8], "big", signed=False)

    @staticmethod
    def _fingerprint_string(fingerprint: tuple[int, int]) -> str:
        return f"{int(fingerprint[0])}:{int(fingerprint[1])}"

    def _get_cached_vector(
        self,
        cache_key: str,
        fingerprint: tuple[int, int],
    ) -> np.ndarray | None:
        cached = self.asset_cache.get(cache_key, fingerprint)
        if cached is not None:
            return cached
        cached = self.vector_store.get(
            cache_key,
            self._fingerprint_string(fingerprint),
            self.backend_name,
        )
        if cached is not None:
            self.asset_cache.put(cache_key, fingerprint, cached)
        return cached

    def _put_cached_vector(
        self,
        cache_key: str,
        fingerprint: tuple[int, int],
        vector: np.ndarray,
    ) -> None:
        self.asset_cache.put(cache_key, fingerprint, vector)
        self.vector_store.put(
            cache_key,
            self._fingerprint_string(fingerprint),
            self.backend_name,
            vector,
        )

    @staticmethod
    def _sample(item: ScoreItem) -> object:
        if item.modality == "image":
            return {"image": item.path}
        if item.modality == "video":
            return {"video": item.path}
        if item.modality == "text":
            return item.name or item.path
        raise ValueError(f"unsupported modality: {item.modality}")

    def _encode(self, samples: list[object]) -> np.ndarray:
        model = self._load()
        encoded = model.encode(
            samples,
            batch_size=self.batch_size,
            truncate_dim=self.dimension,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        matrix = np.asarray(encoded, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        return matrix

    def _query_vector(self, query: str) -> np.ndarray:
        normalized = str(query).strip()
        with self._query_lock:
            cached = self.query_cache.get(normalized)
            if cached is not None:
                self.query_cache.move_to_end(normalized)
                return cached

        fingerprint = self._text_fingerprint(normalized)
        key_hash = hashlib.sha256(
            normalized.encode("utf-8", errors="ignore")
        ).hexdigest()
        persistent_key = f"query:{key_hash}"
        vector = self._get_cached_vector(persistent_key, fingerprint)
        if vector is None:
            vector = self._encode([normalized])[0]
            self._put_cached_vector(persistent_key, fingerprint, vector)

        with self._query_lock:
            self.query_cache[normalized] = vector
            self.query_cache.move_to_end(normalized)
            while len(self.query_cache) > self.query_cache_limit:
                self.query_cache.popitem(last=False)
        return vector

    def _score_sync(self, request: ScoreRequest) -> dict[str, Any]:
        deadline = self._deadline_for_request(request)
        query_vector = self._query_vector(request.query)
        valid: list[tuple[ScoreItem, tuple[int, int]]] = []
        vectors: dict[str, np.ndarray] = {}
        uncached: list[tuple[ScoreItem, tuple[int, int], str]] = []

        for item in request.items:
            if item.modality not in {"image", "video", "text"}:
                continue
            if item.modality != "text":
                path = Path(item.path)
                if not path.is_file():
                    continue
                try:
                    fingerprint = self._fingerprint(item.path)
                except OSError:
                    continue
            else:
                text_value = item.name or item.path
                fingerprint = self._text_fingerprint(text_value)
            valid.append((item, fingerprint))
            cache_key = f"{item.modality}:{item.key}"
            cached = self._get_cached_vector(cache_key, fingerprint)
            if cached is not None:
                vectors[item.key] = cached
            else:
                uncached.append((item, fingerprint, cache_key))

        # External batching makes the cooperative deadline meaningful. SentenceTransformer's
        # internal batch loop cannot be stopped by an HTTP disconnect once model.encode has
        # started, so we only start a new CUDA batch while budget remains.
        for offset in range(0, len(uncached), self.batch_size):
            if self._deadline_exhausted(deadline):
                break
            batch = uncached[offset : offset + self.batch_size]
            encoded = self._encode(
                [self._sample(item) for item, _fingerprint, _key in batch]
            )
            for (item, fingerprint, cache_key), vector in zip(
                batch, encoded, strict=True
            ):
                vectors[item.key] = vector
                self._put_cached_vector(cache_key, fingerprint, vector)

        scores = []
        for item, _fingerprint in valid:
            vector = vectors.get(item.key)
            if vector is None:
                continue
            score = float(np.dot(query_vector, vector))
            scores.append(
                {
                    "key": item.key,
                    "score": round(max(-1.0, min(1.0, score)), 7),
                    "modality": item.modality,
                    "evidence_ref": f"asset:{item.key}",
                }
            )
        scores.sort(key=lambda row: row["score"], reverse=True)
        if self.vector_store.count() > self.persistent_max_rows:
            self.vector_store.prune(max_rows=self.persistent_max_rows)
        return {
            "backend": self.backend_name,
            "dimension": self.dimension,
            "scores": scores,
            "memory_cache_entries": len(self.asset_cache),
            "persistent_cache_entries": self.vector_store.count(),
            "budget_exhausted": self._deadline_exhausted(deadline),
        }

    async def score(self, request: ScoreRequest) -> dict[str, Any]:
        async with self.gpu_lock:
            return await asyncio.to_thread(self._score_sync, request)


RUNTIME = WeMMRuntime()


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
    }


@app.post("/v1/score")
async def score(request: ScoreRequest) -> dict[str, Any]:
    try:
        return await RUNTIME.score(request)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
