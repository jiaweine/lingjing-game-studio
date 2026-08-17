from worldforge.product.analyzer import ProductAnalyzer
from worldforge.providers.registry import ProviderRegistry


def test_video_keyframes_become_visual_inference_evidence():
    assets = [
        {
            "id": "video-1",
            "name": "boss.mp4",
            "mime": "video/mp4",
            "path": "/tmp/boss.mp4",
            "meta": {
                "kind": "video",
                "keyframes": ["/tmp/frame-a.jpg", "/tmp/frame-b.jpg"],
            },
        },
        {
            "id": "audio-1",
            "name": "voice.wav",
            "mime": "audio/wav",
            "path": "/tmp/voice.wav",
            "meta": {"kind": "audio"},
        },
    ]
    model_assets = ProductAnalyzer._model_assets(assets)
    images = [row for row in model_assets if row["mime"] == "image/jpeg"]
    audio = [row for row in model_assets if row["mime"].startswith("audio/")]
    assert len(images) == 2
    assert all(row["meta"]["source_asset_id"] == "video-1" for row in images)
    assert len(audio) == 1


def test_text_preview_is_grounded_in_prompt_context():
    context = ProductAnalyzer._asset_context([
        {
            "name": "combat.log",
            "mime": "text/plain",
            "meta": {
                "kind": "text",
                "preview": "frame=418 damage=9999 shield=0",
            },
        }
    ])
    assert "combat.log" in context
    assert "damage=9999" in context


def test_local_omni_route_requires_full_media_capability(monkeypatch):
    monkeypatch.setenv("LOCAL_OMNI_BASE_URL", "http://localhost:8901/v1")
    registry = ProviderRegistry()
    provider = registry.choose("auto", [
        {"mime": "image/png"},
        {"mime": "audio/wav"},
        {"mime": "video/mp4"},
    ])
    assert provider is not None
    assert provider.info.key == "local_omni"
    assert provider.info.multimodal is True
    assert provider.info.supports_audio is True
    assert provider.info.supports_video is True
