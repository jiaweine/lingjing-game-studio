from __future__ import annotations

import numpy as np

from services.multimodal_retriever.vector_store import PersistentVectorStore
from services.multimodal_retriever.video_segments import merge_windows, segment_windows
from services.multimodal_retriever.worker_wemm import ScoreItem
from services.multimodal_retriever.worker_wemm_hierarchical import HierarchicalWeMMRuntime


def test_segment_windows_adapts_to_global_window_budget():
    rows = segment_windows(0.0, 3600.0, 60.0, max_windows=12)
    assert len(rows) == 12
    assert rows[0] == (0.0, 300.0)
    assert rows[-1] == (3300.0, 3600.0)


def test_merge_windows_deduplicates_but_preserves_distinct_intervals():
    assert merge_windows([(0, 24), (0.0, 24.0), (24, 48), (48, 48)]) == [
        (0.0, 24.0),
        (24.0, 48.0),
    ]


def test_hierarchical_retrieval_expands_only_best_coarse_region(monkeypatch, tmp_path):
    runtime = HierarchicalWeMMRuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "vectors.sqlite3")
    runtime.top_coarse = 1
    runtime.coarse_seconds = 120.0
    runtime.fine_seconds = 24.0
    runtime.online_max_coarse_windows = 4
    runtime.online_max_fine_windows = 8
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture")

    calls = []

    def fake_vectors(item, windows, *, level):
        calls.append((level, list(windows)))
        if level == "coarse":
            return [
                (0.0, 120.0, np.array([0.95, 0.05], dtype=np.float32)),
                (120.0, 240.0, np.array([0.10, 0.90], dtype=np.float32)),
            ]
        assert all(start < 120.0 for start, _end in windows)
        return [
            (0.0, 24.0, np.array([0.70, 0.30], dtype=np.float32)),
            (24.0, 48.0, np.array([0.99, 0.01], dtype=np.float32)),
            (48.0, 72.0, np.array([0.80, 0.20], dtype=np.float32)),
        ]

    monkeypatch.setattr(runtime, "_vectors_for_segments", fake_vectors)
    item = ScoreItem(
        key="video-1",
        path=str(video),
        mime="video/mp4",
        name="video.mp4",
        modality="video",
        duration=240.0,
    )
    row = runtime._score_long_video(
        item,
        np.array([1.0, 0.0], dtype=np.float32),
    )

    assert row is not None
    assert row["start"] == 24.0
    assert row["end"] == 48.0
    assert row["evidence_ref"].endswith("segment:fine:24.000-48.000")
    assert [level for level, _windows in calls] == ["coarse", "fine"]


def test_online_video_cold_build_obeys_hard_window_budget(monkeypatch, tmp_path):
    runtime = HierarchicalWeMMRuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "vectors.sqlite3")
    runtime.online_max_coarse_windows = 3
    runtime.online_max_fine_windows = 4
    runtime.top_coarse = 1
    runtime.coarse_seconds = 10.0
    runtime.fine_seconds = 2.0
    video = tmp_path / "long.mp4"
    video.write_bytes(b"fixture")
    calls = []

    def fake_vectors(item, windows, *, level):
        calls.append((level, list(windows)))
        return [
            (start, end, np.array([1.0, 0.0], dtype=np.float32))
            for start, end in windows
        ]

    monkeypatch.setattr(runtime, "_vectors_for_segments", fake_vectors)
    item = ScoreItem(
        key="long",
        path=str(video),
        mime="video/mp4",
        name="long.mp4",
        modality="video",
        duration=600.0,
    )

    row = runtime._score_long_video(
        item, np.array([1.0, 0.0], dtype=np.float32)
    )

    assert row is not None
    coarse = next(windows for level, windows in calls if level == "coarse")
    fine = next(windows for level, windows in calls if level == "fine")
    assert len(coarse) <= runtime.online_max_coarse_windows
    assert len(fine) <= runtime.online_max_fine_windows
