from __future__ import annotations

import pytest

from worldforge.context.project_packet import ProjectMemoryPacket, ProjectScopeSnapshot
from worldforge.product import ProductAnalyzer


class _CapturingProvider:
    def __init__(self):
        self.messages = None
        self.assets = None

    async def chat(self, *, messages, assets=None, **_kwargs):
        self.messages = list(messages)
        self.assets = list(assets or [])
        return "ok"


class _Providers:
    def __init__(self, provider):
        self.provider = provider

    def choose(self, *_args, **_kwargs):
        return self.provider


def _history_message(index: int, role: str, content: str):
    return {
        "id": f"msg-{index}",
        "role": role,
        "content": content,
        "payload": {},
        "created_at": float(index + 1),
    }


@pytest.mark.asyncio
async def test_long_range_retrieval_and_project_memory_survive_final_last8_boundary():
    provider = _CapturingProvider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))

    history = [
        _history_message(0, "user", "原始目标：定位 marker_zeta 相关问题。"),
        _history_message(1, "assistant", "marker_zeta old clue one: shield trace"),
        _history_message(2, "user", "marker_zeta old clue two: 必须保留 release 证据。"),
        _history_message(3, "assistant", "marker_zeta old clue three: cooldown trace"),
    ]
    for index in range(4, 24):
        history.append(
            _history_message(
                index,
                "user" if index % 2 == 0 else "assistant",
                f"unrelated filler turn {index}",
            )
        )
    # Four recent messages are mandatory. Together with first-user + three retrieved hits,
    # ContextCompiler can legitimately select eight conversation messages pre-budget.
    history.extend(
        [
            _history_message(24, "user", "最近一步 A"),
            _history_message(25, "assistant", "最近一步 B"),
            _history_message(26, "user", "最近一步 C"),
            _history_message(27, "assistant", "最近一步 D"),
        ]
    )

    memory_content = "release 分支发布前必须执行 regression-suite-gamma"
    project_memory = ProjectMemoryPacket(
        project_id="project-atlas",
        project_name="Atlas",
        scope=ProjectScopeSnapshot(branch_ref="release"),
        memories=(
            {
                "id": "memory-1",
                "memory_key": "release.regression.required",
                "revision": 3,
                "kind": "constraint",
                "content": memory_content,
                "state": "active",
                "confidence": 1.0,
                "importance": 0.95,
                "pinned": False,
                "build_ref": None,
                "branch_ref": "release",
                "commit_ref": None,
                "environment_ref": None,
                "source_type": "user_confirmed",
                "source_id": "proposal-1",
                "source_excerpt": memory_content,
                "retrieval_score": 0.9,
            },
        ),
        query="marker_zeta 发布前要求",
        chars=len(memory_content),
    )

    async def sink(_type, _payload):
        return None

    result = await analyzer.run(
        text="marker_zeta 发布前要求是什么？",
        assets=[],
        provider_key="auto",
        sink=sink,
        history=history,
        project_memory=project_memory,
        human_feedback_gate=False,
    )

    assert provider.messages is not None
    # Base provider payload = system + at most 8 packed prior messages + current user prompt.
    assert len(provider.messages) <= 10
    provider_text = "\n".join(str(row.get("content", "")) for row in provider.messages)
    assert "marker_zeta old clue one" in provider_text
    assert "marker_zeta old clue two" in provider_text
    assert "marker_zeta old clue three" in provider_text
    assert "Context Kernel Pack" in provider_text
    assert "PROJECT LONG-TERM MEMORY" in provider_text
    assert "regression-suite-gamma" in provider_text

    context = result["context"]
    assert context["compiled_history_messages_pre_budget"] > 8
    assert context["compiled_history_messages"] <= 8
    assert context["context_budget_provider_last_n_safe"] is True
    assert context["context_budget_provider_per_message_safe"] is True
    assert context["context_budget_system_messages_merged"] >= 2
    assert context["context_budget_kernel_chars"] <= 5800
