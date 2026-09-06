from __future__ import annotations

from worldforge.context import ContextCompiler


def _message(index: int, content: str) -> dict:
    return {
        "id": f"active-{index}",
        "role": "user",
        "content": content,
        "payload": {},
    }


def test_latest_confirmed_fact_replaces_same_build_property_slot():
    packet = ContextCompiler().compile(
        "当前护盾冷却是多少？",
        [
            _message(0, "已确认 build 1.4.7 护盾冷却是 6 秒。"),
            _message(1, "已确认 build 1.4.7 护盾冷却是 5 秒。"),
        ],
    )
    verified = [row["text"] for row in packet.task_state["verified_facts"]]

    assert verified == ["已确认 build 1.4.7 护盾冷却是 5 秒。"]
    assert packet.mode.startswith("active-task-state-")
    assert "context-compiler-v" in packet.mode


def test_different_build_scopes_do_not_supersede_each_other():
    packet = ContextCompiler().compile(
        "比较两个 build 的护盾冷却",
        [
            _message(0, "已确认 build 1.4.7 护盾冷却是 5 秒。"),
            _message(1, "已确认 build 2.0.0 护盾冷却是 4 秒。"),
        ],
    )
    verified = "\n".join(row["text"] for row in packet.task_state["verified_facts"])

    assert "build 1.4.7" in verified
    assert "build 2.0.0" in verified


def test_confirmation_closes_matching_question_and_conditional_premise_abstains():
    packet = ContextCompiler().compile(
        "当前有哪些已确认结论和待确认问题？",
        [
            _message(0, "待确认 build 1.4.7 shield_race 是否只在 release 分支复现？"),
            _message(1, "已确认 build 1.4.7 shield_race 只在 release 分支复现。"),
            _message(2, "如果已确认 build 2.0.0 新护盾公式已经上线，再继续做数值回归。"),
        ],
    )
    verified = "\n".join(row["text"] for row in packet.task_state["verified_facts"])
    questions = "\n".join(row["text"] for row in packet.task_state["open_questions"])

    assert "shield_race" in verified
    assert "shield_race" not in questions
    assert "新护盾公式" not in verified


def test_numeric_constraint_update_keeps_only_latest_active_value():
    packet = ContextCompiler().compile(
        "继续按当前 tickrate 约束执行",
        [
            _message(0, "必须保持 tickrate=30。"),
            _message(1, "必须保持 tickrate=60。"),
        ],
    )
    constraints = [row["text"] for row in packet.task_state["constraints"]]

    assert constraints == ["必须保持 tickrate=60。"]
