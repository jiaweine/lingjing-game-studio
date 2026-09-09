from __future__ import annotations

import numpy as np

from services.multimodal_retriever.worker_lco import LCORuntime, ScoreItem as LCOItem, ScoreRequest as LCORequest
from services.multimodal_retriever.worker_lco_hierarchical import HierarchicalLCORuntime
from services.multimodal_retriever.worker_wemm import ScoreItem as WeMMItem, ScoreRequest as WeMMRequest, WeMMRuntime
from services.multimodal_retriever.worker_wemm_hierarchical import HierarchicalWeMMRuntime


def test_wemm_base_stops_before_starting_next_gpu_batch(monkeypatch):
    runtime = WeMMRuntime()
    runtime.batch_size = 1
    monkeypatch.setattr(runtime, "_query_vector", lambda _query: np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(runtime, "_get_cached_vector", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_put_cached_vector", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime.vector_store, "count", lambda: 0)

    encode_calls = []

    def fake_encode(samples):
        encode_calls.append(list(samples))
        return np.array([[1.0, 0.0]], dtype=np.float32)

    checks = iter([False, True, True])
    monkeypatch.setattr(runtime, "_encode", fake_encode)
    monkeypatch.setattr(runtime, "_deadline_exhausted", lambda _deadline: next(checks, True))

    result = runtime._score_sync(
        WeMMRequest(
            query="boss",
            budget_ms=100,
            items=[
                WeMMItem(key="a", name="first", modality="text"),
                WeMMItem(key="b", name="second", modality="text"),
            ],
        )
    )

    assert len(encode_calls) == 1
    assert len(result["scores"]) == 1
    assert result["budget_exhausted"] is True


def test_lco_base_stops_before_starting_next_gpu_batch(monkeypatch, tmp_path):
    runtime = LCORuntime()
    runtime.batch_size = 1
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    monkeypatch.setattr(runtime, "_query_vector", lambda _query: np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(runtime, "_get_cached_vector", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_put_cached_vector", lambda *_args, **_kwargs: None)

    encode_calls = []

    def fake_encode(conversations):
        encode_calls.append(list(conversations))
        return np.array([[1.0, 0.0]], dtype=np.float32)

    checks = iter([False, True, True])
    monkeypatch.setattr(runtime, "_encode_messages", fake_encode)
    monkeypatch.setattr(runtime, "_deadline_exhausted", lambda _deadline: next(checks, True))

    result = runtime._score_sync(
        LCORequest(
            query="voice",
            budget_ms=100,
            items=[
                LCOItem(key="a", path=str(first), modality="audio"),
                LCOItem(key="b", path=str(second), modality="audio"),
            ],
        )
    )

    assert len(encode_calls) == 1
    assert len(result["scores"]) == 1
    assert result["budget_exhausted"] is True


def test_wemm_hierarchical_returns_coarse_when_budget_ends_before_fine(monkeypatch):
    runtime = HierarchicalWeMMRuntime()
    runtime.top_coarse = 1
    runtime.online_max_fine_windows = 4
    monkeypatch.setattr(runtime, "_source_fingerprint", lambda _item: "fp")
    monkeypatch.setattr(runtime.vector_store, "try_claim_build", lambda **_kwargs: True)
    monkeypatch.setattr(runtime.vector_store, "release_build", lambda **_kwargs: None)
    monkeypatch.setattr(runtime.vector_store, "refresh_build_lease", lambda **_kwargs: True)
    monkeypatch.setattr(runtime, "_stored_video_rows", lambda *_args, **_kwargs: [(0.0, 120.0, np.array([0.9, 0.1], dtype=np.float32))])
    monkeypatch.setattr(runtime, "_deadline_exhausted", lambda _deadline: True)

    def fail_if_fine(*_args, **_kwargs):
        raise AssertionError("fine retrieval must not start after the deadline")

    monkeypatch.setattr(runtime, "_vectors_for_segments", fail_if_fine)
    item = WeMMItem(
        key="video",
        path="/tmp/not-needed.mp4",
        modality="video",
        duration=120.0,
    )

    row = runtime._score_long_video(
        item,
        np.array([1.0, 0.0], dtype=np.float32),
        deadline=1.0,
    )

    assert row is not None
    assert row["start"] == 0.0
    assert "coarse-budget" in row["evidence_ref"]


def test_lco_hierarchical_returns_coarse_when_budget_ends_before_fine(monkeypatch):
    runtime = HierarchicalLCORuntime()
    runtime.top_coarse = 1
    monkeypatch.setattr(runtime, "_source_fingerprint", lambda _item: "fp")
    monkeypatch.setattr(runtime.vector_store, "try_claim_build", lambda **_kwargs: True)
    monkeypatch.setattr(runtime.vector_store, "release_build", lambda **_kwargs: None)
    monkeypatch.setattr(runtime.vector_store, "refresh_build_lease", lambda **_kwargs: True)
    monkeypatch.setattr(runtime, "_stored_audio_rows", lambda *_args, **_kwargs: [(0.0, 60.0, np.array([0.9, 0.1], dtype=np.float32))])
    monkeypatch.setattr(runtime, "_deadline_exhausted", lambda _deadline: True)

    def fail_if_fine(*_args, **_kwargs):
        raise AssertionError("fine acoustic retrieval must not start after the deadline")

    monkeypatch.setattr(runtime, "_vectors_for_segments", fail_if_fine)
    item = LCOItem(
        key="audio",
        path="/tmp/not-needed.wav",
        modality="audio",
        duration=120.0,
    )

    row = runtime._score_long_audio(
        item,
        np.array([1.0, 0.0], dtype=np.float32),
        deadline=1.0,
    )

    assert row is not None
    assert row["start"] == 0.0
    assert "coarse-budget" in row["evidence_ref"]
