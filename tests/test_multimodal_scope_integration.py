from __future__ import annotations

from dataclasses import dataclass

import pytest

from worldforge.context.project_packet import ProjectMemoryPacket, ProjectScopeSnapshot
from worldforge.product import ProductAnalyzer


@dataclass
class _Info:
    key: str = "demo"
    model: str = "scope-test-model"
    configured: bool = True
    multimodal: bool = True
    supports_video: bool = False
    supports_audio: bool = False


class _Provider:
    def __init__(self) -> None:
        self.info = _Info()
        self.assets = None

    async def chat(self, *, messages, assets=None, **kwargs):
        self.assets = list(assets or [])
        return "scope-ok"


class _Providers:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.providers = {"demo": provider}

    def choose(self, *_args, **_kwargs):
        return self.provider


def _image(index: int, build: str):
    return {
        "id": f"image-{index}",
        "name": f"shield-{build}.png",
        "mime": "image/png",
        "path": f"/tmp/shield-{build}.png",
        "meta": {"kind": "image", "build_ref": build, "width": 1280, "height": 720},
    }


@pytest.mark.asyncio
async def test_product_analyzer_does_not_send_wrong_build_asset_to_provider():
    provider = _Provider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))
    packet = ProjectMemoryPacket(
        project_id="project-scope-test",
        project_name="Scope Test",
        scope=ProjectScopeSnapshot(build_ref="1.4.7", source="request"),
        memories=(),
        query="检查当前 build",
        chars=0,
    )

    async def sink(_event_type, _payload):
        return None

    result = await analyzer.run(
        text="只看当前 build 1.4.7 的截图，判断护盾状态",
        assets=[_image(1, "1.4.7"), _image(2, "2.0.0")],
        provider_key="demo",
        sink=sink,
        history=[],
        human_feedback_gate=False,
        project_memory=packet,
    )

    assert result["answer"] == "scope-ok"
    assert result["context"]["multimodal_scope_filter_active"] is True
    assert result["context"]["multimodal_scope_mismatched_assets"] == 1
    assert result["context"]["multimodal_selected_asset_ids"] == ["image-1"]
    assert provider.assets is not None
    assert [row["id"] for row in provider.assets] == ["image-1"]
