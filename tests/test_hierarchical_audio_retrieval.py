from __future__ import annotations

import numpy as np

from services.multimodal_retriever.vector_store import PersistentVectorStore
from services.multimodal_retriever.worker_lco import ScoreItem
from services.multimodal_retriever.worker_lco_hierarchical import HierarchicalLCORuntime


def test_hierarchical_audio_retrieval_localizes_best_fine_window(monkeypatch, tmp_path):
    runtime = HierarchicalLCORuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "lco.sqlite3")
    runtime.top_coarse = 1
    runtime.coarse_seconds = 60.0
    runtime.fine_seconds = 12.0
    runtime.online_max_coarse_windows = 4
    runtime.online_max_fine_windows = 8
    video = tmp_path / "run.mp4"
    video.write_bytes(b"fixture")

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
        path=str(video),
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


def test_online_acoustic_cold_build_obeys_hard_window_budget(monkeypatch, tmp_path):
    runtime = HierarchicalLCORuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "lco.sqlite3")
    runtime.online_max_coarse_windows = 3
    runtime.online_max_fine_windows = 4
    runtime.top_coarse = 1
    runtime.coarse_seconds = 20.0
    runtime.fine_seconds = 4.0
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fixture")
    calls = []

    def fake_vectors(item, windows, *, level):
        calls.append((level, list(windows)))
        return [
            (start, end, np.array([1.0, 0.0], dtype=np.float32))
            for start, end in windows
        ]

    monkeypatch.setattr(runtime, "_vectors_for_segments", fake_vectors)
    item = ScoreItem(
        key="voice",
        path=str(audio),
        mime="audio/wav",
        name="voice.wav",
        modality="audio",
        duration=600.0,
    )

    row = runtime._score_long_audio(
        item, np.array([1.0, 0.0], dtype=np.float32)
    )

    assert row is not None
    coarse = next(windows for level, windows in calls if level == "coarse")
    fine = next(windows for level, windows in calls if level == "fine")
    assert len(coarse) <= runtime.online_max_coarse_windows
    assert len(fine) <= runtime.online_max_fine_windows
