from worldforge.context import ContextCompiler


def _message(index: int, content: str, role: str = "assistant"):
    return {
        "id": f"ranked-{index}",
        "role": role,
        "content": content,
        "payload": {},
    }


def test_identifier_first_planner_avoids_common_token_candidate_explosion():
    compiler = ContextCompiler(recent_messages=4, retrieved_messages=3)
    history = [
        _message(
            index,
            f"build 1.4.{index % 19} shield telemetry frame={index} branch=dev",
        )
        for index in range(5000)
    ]
    history[777] = _message(
        777,
        "XR-914-ZETA build 1.4.7 render_deadlock captured in release branch",
        "user",
    )

    packet = compiler.compile(
        "检查 build 1.4.7 的 XR-914-ZETA render_deadlock",
        history,
    )

    assert packet.mode == "active-task-state-ranked-index-context-compiler-v5"
    assert packet.candidate_history_messages < 500
    assert any("XR-914-ZETA" in row["content"] for row in packet.messages)


def test_rare_natural_language_tokens_recall_far_fact_without_identifier():
    compiler = ContextCompiler(recent_messages=4, retrieved_messages=3)
    history = [
        _message(index, f"普通研发记录 {index}，检查动画同步和资源加载。")
        for index in range(3000)
    ]
    history[313] = _message(
        313,
        "已确认网络抖动之后，护盾回收阶段会重复注册回调，导致双重触发。",
        "user",
    )

    packet = compiler.compile(
        "网络抖动后护盾回收为什么会重复注册回调并双重触发？",
        history,
    )

    assert packet.candidate_history_messages < 128
    assert any("重复注册回调" in row["content"] for row in packet.messages)


def test_common_only_query_keeps_safe_full_union_fallback():
    compiler = ContextCompiler(recent_messages=4, retrieved_messages=3)
    history = [
        _message(index, "继续检查护盾系统和研发进度")
        for index in range(300)
    ]

    packet = compiler.compile("继续检查护盾系统", history)

    assert packet.candidate_history_messages == 300
