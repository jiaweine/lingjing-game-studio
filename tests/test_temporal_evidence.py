from __future__ import annotations

from worldforge.context import temporal_evidence as temporal


def test_extract_time_ranges_supports_seconds_colon_and_minutes():
    assert temporal.extract_time_ranges("重点看 35-45 秒的盾牌变化") == [(35.0, 45.0)]
    assert temporal.extract_time_ranges("检查 1:10 到 1:20") == [(70.0, 80.0)]
    assert temporal.extract_time_ranges("听 2min 至 2.5min") == [(120.0, 150.0)]


def test_dense_interval_frames_is_bounded_and_uses_explicit_range(monkeypatch):
    captured = []

    def fake_frame(asset, timestamp):
        captured.append(round(timestamp, 3))
        return {
            "id": f"v:dense:{timestamp}",
            "name": "dense",
            "mime": "image/jpeg",
            "path": f"/tmp/{timestamp:.3f}.jpg",
            "meta": {
                "kind": "image",
                "source_kind": "video",
                "source_asset_id": asset["id"],
                "timestamp": timestamp,
                "derived": "dense_interval_frame",
            },
        }

    monkeypatch.setattr(temporal, "_frame_at", fake_frame)
    assets = [
        {
            "id": "v",
            "name": "run.mp4",
            "mime": "video/mp4",
            "path": "/tmp/run.mp4",
            "meta": {
                "kind": "video",
                "duration": 120.0,
                "_context": {
                    "kind": "video",
                    "selected": True,
                    "query_text": "分析 35-45 秒这个区间的异常",
                },
            },
        }
    ]

    rows = temporal.dense_interval_frames(assets, max_total_frames=4)

    assert len(rows) == 4
    assert captured[0] == 35.0
    assert captured[-1] == 45.0
    assert all(35.0 <= value <= 45.0 for value in captured)


def test_merge_temporal_evidence_prioritizes_exact_then_dense_and_caps_images(monkeypatch):
    monkeypatch.setattr(
        temporal,
        "dense_interval_frames",
        lambda *_args, **_kwargs: [
            {
                "id": "dense-1",
                "mime": "image/jpeg",
                "path": "/dense-1.jpg",
                "meta": {"derived": "dense_interval_frame"},
            },
            {
                "id": "dense-2",
                "mime": "image/jpeg",
                "path": "/dense-2.jpg",
                "meta": {"derived": "dense_interval_frame"},
            },
        ],
    )
    base = [
        {
            "id": "exact",
            "mime": "image/jpeg",
            "path": "/exact.jpg",
            "meta": {"derived": "exact_temporal_frame"},
        },
        {
            "id": "scene",
            "mime": "image/jpeg",
            "path": "/scene.jpg",
            "meta": {"derived": "scene_memory_frame"},
        },
        {
            "id": "video",
            "mime": "video/mp4",
            "path": "/video.mp4",
            "meta": {},
        },
    ]

    merged = temporal.merge_temporal_evidence([], base, max_images=3)

    assert [row["id"] for row in merged] == [
        "exact",
        "dense-1",
        "dense-2",
        "video",
    ]
