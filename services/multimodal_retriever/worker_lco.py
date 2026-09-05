from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import os
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .vector_store import PersistentVectorStore

app = FastAPI(title="Lingjing LCO Audio Embedding Worker", version="0.1.0")


class ScoreItem(BaseModel):
    key: str
    path: str
    mime: str = ""
    name: str = ""
    modality: str
    duration: float | None = None


class ScoreRequest(BaseModel):
    query: str
    items: list[ScoreItem] = Field(default_factory=list, max_length=48)


@dataclass(frozen=True)
class CacheEntry:
    fingerprint: tuple[int, int]
    vector: np.ndarray


class LRUVectorCache:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(8, int(capacity))
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


class LCORuntime:
    """Specialist acoustic embedding worker based on LCO-Embedding-Omni.

    The 7B model is only used for sound/speech/music queries. Acoustic vectors are derived
    state and are persisted independently from the product database so expensive embeddings
    survive worker restarts while raw media remains authoritative and fully rebuildable.
    """

    def __init__(self) -> None:
        self.model_name = os.getenv(
            "LCO_MODEL", "LCO-Embedding/LCO-Embedding-Omni-7B"
        )
        self.device = os.getenv("LCO_DEVICE", "cuda")
        self.batch_size = max(1, int(os.getenv("LCO_BATCH_SIZE", "2")))
        self.video_max_frames = max(
            2, int(os.getenv("LCO_VIDEO_MAX_FRAMES", "12"))
        )
        self.video_fps = max(0.1, float(os.getenv("LCO_VIDEO_FPS", "1")))
        self.video_max_pixels = max(
            28 * 28,
            int(os.getenv("LCO_VIDEO_MAX_PIXELS", str(224 * 224))),
        )
        self.asset_cache = LRUVectorCache(
            int(os.getenv("LCO_ASSET_CACHE", "4096"))
        )
        cache_root = Path(
            os.getenv("LINGJING_RETRIEVER_CACHE_DIR", "outputs/retrieval-cache")
        )
        self.vector_store = PersistentVectorStore(
            os.getenv("LCO_VECTOR_CACHE_DB", str(cache_root / "lco-vectors.sqlite3"))
        )
        self.persistent_max_rows = max(
            1000, int(os.getenv("LCO_VECTOR_CACHE_MAX_ROWS", "120000"))
        )
        self.query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.query_cache_limit = max(
            16, int(os.getenv("LCO_QUERY_CACHE", "256"))
        )
        self._query_lock = RLock()
        self._model_lock = RLock()
        self._model: Any = None
        self._processor: Any = None
        self._process_mm_info: Any = None
        self.gpu_lock = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        return f"lco:{self.model_name}"

    def _load(self):
        with self._model_lock:
            if self._model is not None:
                return self._model, self._processor, self._process_mm_info
            try:
                import torch
                from transformers import (
                    Qwen2_5OmniProcessor,
                    Qwen2_5OmniThinkerForConditionalGeneration,
                )
                from qwen_omni_utils import process_mm_info
            except ImportError as exc:
                raise RuntimeError(
                    "LCO worker dependencies are missing; install requirements-lco.txt"
                ) from exc

            processor = Qwen2_5OmniProcessor.from_pretrained(self.model_name)
            model = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(
                self.model_name,
                torch_dtype=torch.bfloat16,
                device_map=self.device if self.device != "cuda" else "auto",
            )
            model.eval()
            self._model = model
            self._processor = processor
            self._process_mm_info = process_mm_info
            return model, processor, process_mm_info

    @staticmethod
    def _fingerprint(path: str) -> tuple[int, int]:
        stat = Path(path).stat()
        return int(stat.st_size), int(stat.st_mtime_ns)

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
        if self.vector_store.count() > self.persistent_max_rows:
            self.vector_store.prune(max_rows=self.persistent_max_rows)

    def _text_message(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"{query}\nSummarize the above text in one word:",
                    }
                ],
            }
        ]

    def _item_message(self, item: ScoreItem) -> list[dict[str, Any]]:
        if item.modality == "audio":
            content = [
                {"type": "audio", "audio": item.path},
                {"type": "text", "text": "\nSummarize the above audio in one word:"},
            ]
        elif item.modality == "video_with_audio":
            content = [
                {
                    "type": "video",
                    "video": item.path,
                    "max_pixels": self.video_max_pixels,
                    "fps": self.video_fps,
                    "max_frames": self.video_max_frames,
                },
                {
                    "type": "text",
                    "text": "\nSummarize the above video and its audio in one word:",
                },
            ]
        else:
            raise ValueError(f"unsupported modality: {item.modality}")
        return [{"role": "user", "content": content}]

    def _encode_messages(
        self, conversations: list[list[dict[str, Any]]]
    ) -> np.ndarray:
        import torch

        model, processor, process_mm_info = self._load()
        text = processor.apply_chat_template(
            conversations,
            tokenize=False,
            add_generation_prompt=True,
        )
        audio_inputs, image_inputs, video_inputs = process_mm_info(
            conversations,
            use_audio_in_video=True,
        )
        inputs = processor(
            text=text,
            audio=audio_inputs,
            images=image_inputs,
            videos=video_inputs,
            return_tensors="pt",
            padding=True,
        )
        try:
            inputs = inputs.to(self.device)
        except (AttributeError, RuntimeError):
            inputs = inputs.to("cuda")
        with torch.inference_mode():
            outputs = model(
                **inputs,
                output_hidden_states=True,
                return_dict=True,
            ).hidden_states[-1][:, -1, :]
            outputs = torch.nn.functional.normalize(outputs.float(), dim=-1)
        return outputs.detach().cpu().numpy().astype(np.float32, copy=False)

    def _query_vector(self, query: str) -> np.ndarray:
        normalized = str(query).strip()
        with self._query_lock:
            cached = self.query_cache.get(normalized)
            if cached is not None:
                self.query_cache.move_to_end(normalized)
                return cached
        vector = self._encode_messages([self._text_message(normalized)])[0]
        with self._query_lock:
            self.query_cache[normalized] = vector
            self.query_cache.move_to_end(normalized)
            while len(self.query_cache) > self.query_cache_limit:
                self.query_cache.popitem(last=False)
        return vector

    def _score_sync(self, request: ScoreRequest) -> dict[str, Any]:
        query_vector = self._query_vector(request.query)
        valid: list[tuple[ScoreItem, tuple[int, int]]] = []
        uncached: list[tuple[ScoreItem, tuple[int, int]]] = []
        vectors: dict[str, np.ndarray] = {}

        for item in request.items:
            if item.modality not in {"audio", "video_with_audio"}:
                continue
            path = Path(item.path)
            if not path.is_file():
                continue
            try:
                fingerprint = self._fingerprint(item.path)
            except OSError:
                continue
            valid.append((item, fingerprint))
            cache_key = f"{item.modality}:{item.key}"
            cached = self._get_cached_vector(cache_key, fingerprint)
            if cached is not None:
                vectors[item.key] = cached
            else:
                uncached.append((item, fingerprint))

        for start in range(0, len(uncached), self.batch_size):
            batch = uncached[start : start + self.batch_size]
            conversations = [
                self._item_message(item) for item, _fingerprint in batch
            ]
            encoded = self._encode_messages(conversations)
            for (item, fingerprint), vector in zip(batch, encoded, strict=True):
                vectors[item.key] = vector
                self._put_cached_vector(
                    f"{item.modality}:{item.key}", fingerprint, vector
                )

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
                    "evidence_ref": f"asset:{item.key}:acoustic",
                }
            )
        scores.sort(key=lambda row: row["score"], reverse=True)
        return {
            "backend": self.backend_name,
            "scores": scores,
            "memory_cache_entries": len(self.asset_cache),
            "persistent_cache_entries": self.vector_store.count(),
        }

    async def score(self, request: ScoreRequest) -> dict[str, Any]:
        async with self.gpu_lock:
            return await asyncio.to_thread(self._score_sync, request)


RUNTIME = LCORuntime()


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "backend": RUNTIME.backend_name,
        "device": RUNTIME.device,
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
