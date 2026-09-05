from __future__ import annotations

import time

import numpy as np

from services.multimodal_retriever.vector_store import PersistentVectorStore
from services.multimodal_retriever.worker_wemm import ScoreItem
from services.multimodal_retriever.worker_wemm_hierarchical import HierarchicalWeMMRuntime


def test_build_lease_is_exclusive_and_expired_lease_is_recoverable(tmp_path):
    path = tmp_path / "vectors.sqlite3"
    first = PersistentVectorStore(path)
    second = PersistentVectorStore(path)

    assert first.try_claim_build(
        cache_key="build:asset-1",
        backend="test",
        owner="worker-a",
        lease_seconds=0.05,
    )
    assert not second.try_claim_build(
        cache_key="build:asset-1",
        backend="test",
        owner="worker-b",
        lease_seconds=1.0,
    )

    time.sleep(0.07)
    assert second.try_claim_build(
        cache_key="build:asset-1",
        backend="test",
        owner="worker-b",
        lease_seconds=1.0,
    )
    second.release_build(
        cache_key="build:asset-1", backend="test", owner="worker-b"
    )
    assert first.try_claim_build(
        cache_key="build:asset-1",
        backend="test",
        owner="worker-a",
        lease_seconds=1.0,
    )


def test_concurrent_long_video_request_reuses_partial_index_instead_of_rebuilding(monkeypatch, tmp_path):
    video = tmp_path / "run.mp4"
    video.write_bytes(b"not-a-real-video-but-fingerprintable")

    runtime = HierarchicalWeMMRuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "wemm.sqlite3")
    item = ScoreItem(
        key="video-1",
        path=str(video),
        mime="video/mp4",
        name="run.mp4",
        modality="video",
        duration=600.0,
    )
    source_fp = runtime._source_fingerprint(item)
    assert source_fp is not None

    cache_key = "video-segment:coarse:video-1:120.000:240.000"
    runtime.vector_store.put(
        cache_key,
        "derived-fingerprint",
        runtime.backend_name,
        np.array([1.0, 0.0], dtype=np.float32),
    )
    runtime.vector_store.put_unit(
        cache_key=cache_key,
        backend=runtime.backend_name,
        source_key=item.key,
        source_fingerprint=source_fp,
        modality="video",
        start=120.0,
        end=240.0,
    )
    lease_key = f"build:video:{item.key}:{source_fp}"
    assert runtime.vector_store.try_claim_build(
        cache_key=lease_key,
        backend=runtime.backend_name,
        owner="other-worker",
        lease_seconds=60.0,
    )

    def must_not_build(*_args, **_kwargs):
        raise AssertionError("concurrent request must not duplicate segment build")

    monkeypatch.setattr(runtime, "_vectors_for_segments", must_not_build)
    row = runtime._score_long_video(
        item,
        np.array([1.0, 0.0], dtype=np.float32),
    )

    assert row is not None
    assert row["start"] == 120.0
    assert row["end"] == 240.0
    assert ":cached:" in row["evidence_ref"]
