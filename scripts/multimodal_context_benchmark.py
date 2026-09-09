from __future__ import annotations

import json
from pathlib import Path
import tempfile

from worldforge.context import MultimodalContextCompiler
from worldforge.context.project_packet import ProjectScopeSnapshot


def _asset(index, *, kind, name, mime, path, build=None, **meta):
    payload = {"kind": kind, **meta}
    if build:
        payload["build_ref"] = build
    return {
        "id": f"asset-{index}",
        "name": name,
        "mime": mime,
        "path": str(path),
        "meta": payload,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lingjing-mm-bench-") as tmp:
        root = Path(tmp)
        release_log = root / "release-runtime.log"
        release_log.write_text(
            ("ordinary release log line\n" * 800)
            + "build 1.4.7 shield_race root cause = duplicate callback after network jitter\n"
            + ("ordinary release tail\n" * 400),
            encoding="utf-8",
        )
        stale_log = root / "stale-runtime.log"
        stale_log.write_text(
            "build 2.0.0 shield_race root cause = unrelated stale hypothesis\n",
            encoding="utf-8",
        )

        assets = [
            _asset(
                1,
                kind="text",
                name="release-runtime.log",
                mime="text/plain",
                path=release_log,
                build="1.4.7",
                preview=("ordinary release log line\n" * 100)[:1500],
                chars=release_log.stat().st_size,
            ),
            _asset(
                2,
                kind="text",
                name="stale-runtime.log",
                mime="text/plain",
                path=stale_log,
                build="2.0.0",
                preview=stale_log.read_text(encoding="utf-8"),
            ),
            _asset(
                3,
                kind="image",
                name="release-shield-state.png",
                mime="image/png",
                path=root / "release.png",
                build="1.4.7",
                width=1920,
                height=1080,
            ),
            _asset(
                4,
                kind="image",
                name="stale-shield-state.png",
                mime="image/png",
                path=root / "stale.png",
                build="2.0.0",
                width=1920,
                height=1080,
            ),
            _asset(
                5,
                kind="video",
                name="release-boss-run.mp4",
                mime="video/mp4",
                path=root / "release.mp4",
                build="1.4.7",
                duration=100.0,
                keyframes=[str(root / f"release-frame-{n}.jpg") for n in range(4)],
            ),
            _asset(
                6,
                kind="video",
                name="stale-boss-run.mp4",
                mime="video/mp4",
                path=root / "stale.mp4",
                build="2.0.0",
                duration=100.0,
                keyframes=[str(root / f"stale-frame-{n}.jpg") for n in range(4)],
            ),
            _asset(
                7,
                kind="audio",
                name="release-game-audio.wav",
                mime="audio/wav",
                path=root / "release.wav",
                build="1.4.7",
                duration=100.0,
            ),
            _asset(
                8,
                kind="audio",
                name="stale-game-audio.wav",
                mime="audio/wav",
                path=root / "stale.wav",
                build="2.0.0",
                duration=100.0,
            ),
            _asset(
                9,
                kind="text",
                name="project-wide-notes.txt",
                mime="text/plain",
                path=root / "notes.txt",
                preview="项目级通用诊断说明",
            ),
        ]

        compiler = MultimodalContextCompiler(
            selected_asset_budget=12,
            frames_per_video=3,
            image_budget=9,
            audio_budget=3,
        )
        scope = ProjectScopeSnapshot(build_ref="1.4.7", source="request")
        packet = compiler.compile(
            "检查 build 1.4.7 的 shield_race：结合日志、截图、60秒附近录像和声音。",
            assets,
            scope=scope,
        )
        model_assets = compiler.model_assets(packet.assets)
        selected = [
            row
            for row in packet.assets
            if row["meta"]["_context"]["selected"]
        ]
        mismatch_ids = {
            row["id"]
            for row in packet.assets
            if not row["meta"]["_context"].get("scope_eligible", True)
        }
        model_source_ids = {
            str((row.get("meta", {}) or {}).get("source_asset_id") or row.get("id"))
            for row in model_assets
        }
        video_times = [
            (row.get("meta", {}) or {}).get("timestamp")
            for row in model_assets
            if (row.get("meta", {}) or {}).get("source_asset_id") == "asset-5"
        ]
        selected_kinds = {
            row["meta"]["_context"]["kind"]
            for row in selected
        }

        comparison = compiler.compile(
            "比较 build 1.4.7 和 2.0.0 的截图",
            [assets[2], assets[3]],
            scope=ProjectScopeSnapshot(
                conflicts={"build_ref": ("1.4.7", "2.0.0")},
                unresolved_conflict=True,
                source="asset",
            ),
        )

        result = {
            "benchmark": "multimodal-context-scope-v1",
            "all_assets_addressable": all(row["name"] in packet.manifest for row in assets),
            "scope_filter_active": packet.scope_filter_active,
            "scope_mismatched_assets": packet.scope_mismatched_assets,
            "scope_mismatch_ids": sorted(mismatch_ids),
            "selected_asset_ids": [row["id"] for row in selected],
            "selected_kinds": sorted(selected_kinds),
            "full_log_hit": packet.text_full_content_hits,
            "far_log_fact_visible": "duplicate callback after network jitter" in packet.manifest,
            "stale_build_not_model_facing": not bool(model_source_ids & mismatch_ids),
            "temporal_60s_frame": 60.0 in video_times,
            "model_assets": len(model_assets),
            "comparison_scope_abstains": (
                comparison.scope_unresolved_conflict
                and not comparison.scope_filter_active
                and comparison.selected_assets == 2
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))

        assert result["all_assets_addressable"]
        assert result["scope_filter_active"]
        assert result["scope_mismatched_assets"] == 4
        assert mismatch_ids == {"asset-2", "asset-4", "asset-6", "asset-8"}
        assert {"text", "image", "video", "audio"}.issubset(selected_kinds)
        assert result["full_log_hit"] >= 1
        assert result["far_log_fact_visible"]
        assert result["stale_build_not_model_facing"]
        assert result["temporal_60s_frame"]
        assert result["model_assets"] <= 12
        assert result["comparison_scope_abstains"]


if __name__ == "__main__":
    main()
