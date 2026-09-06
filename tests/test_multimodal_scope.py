from __future__ import annotations

import asyncio

import pytest

from worldforge.context import MultimodalContextCompiler
from worldforge.context.project_packet import ProjectScopeSnapshot


def _asset(index: int, *, kind: str, build: str | None = None):
    meta = {"kind": kind}
    if build:
        meta["build_ref"] = build
    mime = {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/wav",
        "text": "text/plain",
    }[kind]
    if kind == "video":
        meta.update(duration=100.0, keyframes=[f"f-{index}-{n}.jpg" for n in range(4)])
    return {
        "id": f"asset-{index}",
        "name": f"{kind}-{build or 'general'}-{index}",
        "mime": mime,
        "path": f"/tmp/{kind}-{index}",
        "meta": meta,
    }


def test_explicit_scope_keeps_mismatch_addressable_but_out_of_deep_context():
    compiler = MultimodalContextCompiler()
    assets = [
        _asset(1, kind="image", build="1.4.7"),
        _asset(2, kind="image", build="2.0.0"),
        _asset(3, kind="text"),
    ]
    packet = compiler.compile(
        "检查 build 1.4.7 的截图和日志",
        assets,
        scope=ProjectScopeSnapshot(build_ref="1.4.7", source="request"),
    )

    contexts = {
        row["id"]: row["meta"]["_context"]
        for row in packet.assets
    }
    assert packet.scope_filter_active is True
    assert packet.scope_mismatched_assets == 1
    assert contexts["asset-1"]["selected"] is True
    assert contexts["asset-2"]["selected"] is False
    assert contexts["asset-2"]["scope_eligible"] is False
    assert contexts["asset-3"]["scope_eligible"] is True
    assert "image-2.0.0-2" in packet.manifest
    assert "scope mismatch" in packet.manifest
    assert all(
        row.get("id") != "asset-2"
        for row in compiler.model_assets(packet.assets)
    )


def test_unresolved_comparison_scope_does_not_guess_a_build():
    compiler = MultimodalContextCompiler()
    scope = ProjectScopeSnapshot(
        conflicts={"build_ref": ("1.4.7", "2.0.0")},
        unresolved_conflict=True,
        source="asset",
    )
    packet = compiler.compile(
        "比较两个 build 的截图",
        [
            _asset(1, kind="image", build="1.4.7"),
            _asset(2, kind="image", build="2.0.0"),
        ],
        scope=scope,
    )

    assert packet.scope_filter_active is False
    assert packet.scope_unresolved_conflict is True
    assert packet.scope_mismatched_assets == 0
    selected = [
        row["id"]
        for row in packet.assets
        if row["meta"]["_context"]["selected"]
    ]
    assert selected == ["asset-1", "asset-2"]


@pytest.mark.asyncio
async def test_scope_binding_is_task_local_for_shared_compiler():
    compiler = MultimodalContextCompiler()
    assets = [
        _asset(1, kind="image", build="1.4.7"),
        _asset(2, kind="image", build="2.0.0"),
    ]

    async def selected(build: str) -> list[str]:
        token = compiler.bind_scope(ProjectScopeSnapshot(build_ref=build, source="request"))
        try:
            await asyncio.sleep(0)
            packet = compiler.compile("检查当前 build 截图", assets)
            return [
                row["id"]
                for row in packet.assets
                if row["meta"]["_context"]["selected"]
            ]
        finally:
            compiler.reset_scope(token)

    first, second = await asyncio.gather(selected("1.4.7"), selected("2.0.0"))
    assert first == ["asset-1"]
    assert second == ["asset-2"]
