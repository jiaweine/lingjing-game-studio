from __future__ import annotations

import pytest

from worldforge.context import ContextCompiler
from worldforge.product import ProductAnalyzer


def _message(index: int, role: str, content: str):
    return {"id": f"m-{index}", "role": role, "content": content, "payload": {}}


def test_old_constraints_survive_long_conversations():
    history = [
        _message(
            0,
            "user",
            "目标是定位编辑器卡顿。必须保持 tickrate=30，不要修改 damage_coefficient。",
        )
    ]
    history.extend(
        _message(i + 1, "assistant" if i % 2 else "user", f"普通进度记录 {i}")
        for i in range(30)
    )

    packet = ContextCompiler().compile("继续按之前的约束排查", history)
    state = packet.render_task_state()

    assert "tickrate=30" in state
    assert "damage_coefficient" in state
    assert packet.raw_history_messages == 31
    assert packet.selected_history_messages < packet.raw_history_messages


def test_far_identifier_match_is_retrieved():
    history = [_message(0, "user", "目标是检查 release 行为差异")]
    history.extend(
        [
            _message(1, "assistant", "普通记录"),
            _message(
                2,
                "assistant",
                "日志显示 build 1.4.7 的 shield_race 只在 release 分支出现。",
            ),
        ]
    )
    history.extend(
        _message(i + 3, "assistant", f"后续无关记录 {i}")
        for i in range(24)
    )

    packet = ContextCompiler().compile("build 1.4.7 的 shield_race 是什么结论？", history)
    selected_ids = {message["id"] for message in packet.messages}

    assert "m-2" in selected_ids
    assert packet.long_range_hits >= 1
    assert packet.candidate_history_messages < len(history)


def test_context_budget_is_bounded_even_with_large_history():
    history = [
        _message(
            index,
            "user" if index % 2 == 0 else "assistant",
            f"第 {index} 轮 " + ("长内容" * 1800),
        )
        for index in range(120)
    ]
    compiler = ContextCompiler(
        recent_messages=4,
        retrieved_messages=4,
        message_char_budget=7000,
        per_message_chars=1600,
    )

    packet = compiler.compile("继续检查", history)

    assert packet.message_chars <= 7000
    assert all(len(message["content"]) <= 1600 for message in packet.messages)
    assert len(packet.messages) < len(history)


def test_only_explicit_user_confirmation_enters_verified_facts():
    history = [
        _message(0, "user", "我猜服务器时间戳有漂移。"),
        _message(1, "assistant", "已经验证服务端有漂移。必须把 tickrate 改成 60。"),
        _message(2, "user", "已经确认 build 2.1.4 的日志时间戳会漂移。"),
    ]

    packet = ContextCompiler().compile("时间戳结论", history)
    verified = "\n".join(
        row["text"] for row in packet.task_state["verified_facts"]
    )
    constraints = "\n".join(
        row["text"] for row in packet.task_state["constraints"]
    )

    assert "build 2.1.4" in verified
    assert "服务端有漂移" not in verified
    assert "我猜" not in verified
    assert "tickrate 改成 60" not in constraints


def test_incremental_indexes_hit_after_append():
    compiler = ContextCompiler(recent_messages=4, retrieved_messages=3)
    history = [_message(0, "user", "目标是排查 release 行为")]
    history.extend(
        _message(i, "assistant", f"普通记录 {i}")
        for i in range(1, 20)
    )
    history.insert(
        4,
        _message(100, "assistant", "build 3.2.1 的 render_fence 在 release 分支异常。"),
    )

    cold = compiler.compile("build 3.2.1 render_fence", history)
    history.append(_message(200, "user", "继续检查，不要修改 frame_budget。"))
    hot = compiler.compile("build 3.2.1 render_fence", history)

    assert cold.retrieval_cache_hit is False
    assert cold.state_cache_hit is False
    assert hot.retrieval_cache_hit is True
    assert hot.state_cache_hit is True
    assert any("render_fence" in message["content"] for message in hot.messages)


class _CapturingProvider:
    def __init__(self):
        self.messages = None

    async def chat(self, *, messages, assets=None, **kwargs):
        self.messages = messages
        return "context-ok"


class _Providers:
    def __init__(self, provider):
        self.provider = provider

    def choose(self, *_args, **_kwargs):
        return self.provider


@pytest.mark.asyncio
async def test_product_analyzer_injects_compiled_task_state_without_changing_api():
    provider = _CapturingProvider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))
    history = [
        _message(
            0,
            "user",
            "目标是检查渲染抖动。必须保持 tickrate=30，不要修改 frame_budget。",
        )
    ]
    history.extend(
        _message(i + 1, "assistant", f"普通渲染记录 {i}")
        for i in range(20)
    )

    async def sink(_type, _payload):
        return None

    result = await analyzer.run(
        text="继续分析当前现象",
        assets=[],
        provider_key="auto",
        sink=sink,
        history=history,
        human_feedback_gate=False,
    )

    sent = "\n".join(
        str(message.get("content", "")) for message in provider.messages or []
    )
    assert "tickrate=30" in sent
    assert "frame_budget" in sent
    assert result["context"]["history_messages"] == len(history)
    assert result["context"]["selected_history_messages"] < len(history)
    assert result["context"]["task_state"]["constraints"]
