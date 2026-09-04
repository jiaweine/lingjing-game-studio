from __future__ import annotations

import numpy as np

from services.multimodal_retriever.worker_lco import ScoreItem
from services.multimodal_retriever.worker_lco_hierarchical import HierarchicalLCORuntime


def test_hierarchical_audio_retrieval_localizes_best_fine_window(monkeypatch):
    runtime = HierarchicalLCORuntime()
    runtime.top_coarse = 1
    runtime.coarse_seconds = 60.0
    runtime.fine_seconds = 12.0
    runtime.max_coarse_windows = 4
    runtime.max_fine_windows = 8

    calls = []

    def fake_vectors(item, windows, *, level):
        calls.append((level, list(windows)))
        if level == "coarse":
            return [
                (0.0, 60.0, np.array([0.2, 0.8], dtype=np.float32)),
                (60.0, 120.0, np.array([0.95, 0.05], dtype=np.float32)),
            ]
        assert all(60.0 <= start < 120.0 for start, _end in windows)
        return [
            (60.0, 72.0, np.array([0.70, 0.30], dtype=np.float32)),
            (72.0, 84.0, np.array([0.99, 0.01], dtype=np.float32)),
            (84.0, 96.0, np.array([0.75, 0.25], dtype=np.float32)),
        ]

    monkeypatch.setattr(runtime, "_vectors_for_segments", fake_vectors)
    item = ScoreItem(
        key="audio-1",
        path="/tmp/run.mp4",
        mime="video/mp4",
        name="run.mp4",
        modality="video_with_audio",
        duration=120.0,
    )

    row = runtime._score_long_audio(
        item,
        np.array([1.0, 0.0], dtype=np.float32),
    )

    assert row is not None
    assert row["start"] == 72.0
    assert row["end"] == 84.0
    assert row["modality"] == "video_with_audio"
    assert row["evidence_ref"].endswith("acoustic:fine:72.000-84.000")
    assert [level for level, _windows in calls] == ["coarse", "fine"]
