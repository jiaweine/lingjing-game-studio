from __future__ import annotations

import numpy as np

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


def test_hierarchical_retrieval_expands_only_best_coarse_region(monkeypatch):
    runtime = HierarchicalWeMMRuntime()
    runtime.top_coarse = 1
    runtime.coarse_seconds = 120.0
    runtime.fine_seconds = 24.0
    runtime.max_coarse_windows = 4
    runtime.max_fine_windows = 8

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
        path="/tmp/video.mp4",
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
