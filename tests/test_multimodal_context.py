from __future__ import annotations

import pytest

from worldforge.context import MultimodalContextCompiler
from worldforge.context.media_derivatives import augment_model_assets, query_needs_audio
from worldforge.product import ProductAnalyzer


def _asset(index: int, *, kind: str, name: str, path: str, mime: str, **meta):
    return {
        "id": f"asset-{index}",
        "name": name,
        "path": path,
        "mime": mime,
        "meta": {"kind": kind, **meta},
    }


def test_text_search_reads_full_file_not_only_upload_preview(tmp_path):
    path = tmp_path / "long.log"
    prefix = "ordinary startup line\n" * 500
    target = "build 1.4.7 shield_race only appears on release branch\n"
    suffix = "ordinary tail line\n" * 500
    path.write_text(prefix + target + suffix, encoding="utf-8")
    preview = (prefix + target + suffix)[:4000]
    assert "shield_race" not in preview

    compiler = MultimodalContextCompiler()
    packet = compiler.compile(
        "build 1.4.7 的 shield_race 在哪里出现？",
        [
            _asset(
                1,
                kind="text",
                name="runtime.log",
                path=str(path),
                mime="text/plain",
                preview=preview,
                chars=path.stat().st_size,
            )
        ],
    )

    assert packet.text_full_content_hits == 1
    assert "shield_race only appears on release branch" in packet.manifest
    context = packet.assets[0]["meta"]["_context"]
    assert context["selected"] is True
    assert context["full_content_hits"] >= 1


def test_video_temporal_query_selects_nearest_available_keyframe():
    compiler = MultimodalContextCompiler(frames_per_video=3)
    packet = compiler.compile(
        "重点看 60 秒附近的画面",
        [
            _asset(
                1,
                kind="video",
                name="boss-run.mp4",
                path="/tmp/boss-run.mp4",
                mime="video/mp4",
                duration=100.0,
                width=1920,
                height=1080,
                keyframes=["f0.jpg", "f1.jpg", "f2.jpg", "f3.jpg"],
            )
        ],
    )

    model_assets = compiler.model_assets(packet.assets)
    timestamps = [
        asset.get("meta", {}).get("timestamp")
        for asset in model_assets
        if asset.get("meta", {}).get("source_kind") == "video"
    ]
    assert 60.0 in timestamps
    # Display formatting is intentionally human-readable (60s -> 1:00.0); semantic
    # correctness is asserted from the structured timestamp above.
    assert "1:00.0" in packet.manifest


def test_every_asset_remains_in_manifest_while_model_payload_is_bounded():
    assets = []
    for index in range(12):
        assets.append(
            _asset(
                index,
                kind="image",
                name=f"screen-{index}.png",
                path=f"/tmp/screen-{index}.png",
                mime="image/png",
                width=1280,
                height=720,
            )
        )
    for index in range(5):
        assets.append(
            _asset(
                20 + index,
                kind="audio",
                name=f"voice-{index}.wav",
                path=f"/tmp/voice-{index}.wav",
                mime="audio/wav",
                duration=8.0 + index,
            )
        )
    assets.append(
        _asset(
            40,
            kind="video",
            name="full-run.mp4",
            path="/tmp/full-run.mp4",
            mime="video/mp4",
            duration=90.0,
            keyframes=[f"vf-{index}.jpg" for index in range(8)],
        )
    )

    compiler = MultimodalContextCompiler(image_budget=9, audio_budget=3)
    packet = compiler.compile("继续检查所有素材", assets)
    model_assets = compiler.model_assets(packet.assets)

    for asset in assets:
        assert asset["name"] in packet.manifest
    assert packet.total_assets == len(assets)
    assert sum(1 for item in model_assets if str(item.get("mime", "")).startswith("image/")) <= 9
    assert sum(1 for item in model_assets if str(item.get("mime", "")).startswith("audio/")) <= 3


def test_small_selected_video_is_preserved_as_raw_provider_evidence(tmp_path):
    video = tmp_path / "boss-run.mp4"
    video.write_bytes(b"small-video-fixture")
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame-fixture")
    compiler = MultimodalContextCompiler(frames_per_video=1)
    packet = compiler.compile(
        "分析这段录像",
        [
            _asset(
                1,
                kind="video",
                name="boss-run.mp4",
                path=str(video),
                mime="video/mp4",
                duration=12.0,
                keyframes=[str(frame)],
            )
        ],
    )
    packet.assets[0]["meta"]["_context"]["needs_audio"] = False
    model_assets = augment_model_assets(
        packet.assets,
        compiler.model_assets(packet.assets),
    )

    assert any(item.get("mime") == "video/mp4" for item in model_assets)
    assert any(item.get("mime") == "image/jpeg" for item in model_assets)


def test_audio_intent_detection_covers_game_sound_questions():
    assert query_needs_audio("听一下 37 秒附近的音效是不是重复触发") is True
    assert query_needs_audio("只比较两张截图的 UI 布局") is False


class _CapturingProvider:
    def __init__(self):
        self.messages = None
        self.assets = None

    async def chat(self, *, messages, assets=None, **kwargs):
        self.messages = messages
        self.assets = assets
        return "multimodal-context-ok"


class _Providers:
    def __init__(self, provider):
        self.provider = provider

    def choose(self, *_args, **_kwargs):
        return self.provider


@pytest.mark.asyncio
async def test_product_analyzer_injects_far_log_content_into_prompt(tmp_path):
    path = tmp_path / "huge-config.log"
    path.write_text(
        ("normal line\n" * 700)
        + "marker_zeta = enabled_only_for_release\n"
        + ("tail line\n" * 300),
        encoding="utf-8",
    )
    provider = _CapturingProvider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))
    asset = _asset(
        1,
        kind="text",
        name="huge-config.log",
        path=str(path),
        mime="text/plain",
        preview=("normal line\n" * 300)[:4000],
    )

    async def sink(_type, _payload):
        return None

    result = await analyzer.run(
        text="请分析 marker_zeta 的上下文",
        assets=[asset],
        provider_key="auto",
        sink=sink,
        history=[],
        human_feedback_gate=False,
    )

    sent = "\n".join(
        str(message.get("content", "")) for message in provider.messages or []
    )
    assert "marker_zeta = enabled_only_for_release" in sent
    assert result["context"]["multimodal_assets"] == 1
    assert result["context"]["multimodal_text_full_content_hits"] == 1
