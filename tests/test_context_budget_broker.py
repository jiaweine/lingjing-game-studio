from __future__ import annotations

from worldforge.context.budget import ContextBudgetBroker


def _conversation(index: int, content: str | None = None):
    return {
        "id": f"msg-{index}",
        "role": "user" if index % 2 == 0 else "assistant",
        "content": content or f"history-{index}-" + ("x" * 900),
        "_context_index": index,
    }


def _derived(kind: str, content: str):
    return {
        "id": f"context:{kind}",
        "role": "user",
        "content": content,
        "payload": {"system_derived": True, "context_kind": kind},
    }


def test_kernel_pack_prevents_legacy_last8_from_dropping_selected_history():
    broker = ContextBudgetBroker()
    history = [_conversation(index) for index in range(8)]
    history[1]["content"] = "long-range-marker-zeta " + ("r" * 1200)
    rows = [
        *history,
        _derived("task_state", "task-state-marker " + ("t" * 2600)),
        _derived("project_memory", "project-memory-marker " + ("m" * 4200)),
        _derived("evidence_control", "evidence-control-marker " + ("e" * 1300)),
        _derived("verification", "verification-marker " + ("v" * 2200)),
    ]

    packed = broker.pack(rows)
    assert len(packed.messages) == 8
    assert packed.messages[-1]["id"] == "context:kernel-pack"
    assert packed.telemetry["context_budget_provider_last_n_safe"] is True
    assert packed.telemetry["context_budget_system_messages_merged"] == 4
    assert packed.telemetry["context_budget_conversation_messages_dropped"] == 1

    # The oldest goal duplicate may be dropped because TaskState preserves it, but an older
    # retrieved hit must survive the final provider packing stage.
    text = "\n".join(message["content"] for message in packed.messages)
    assert "long-range-marker-zeta" in text
    assert "task-state-marker" in text
    assert "project-memory-marker" in text
    assert "verification-marker" in text
    assert "[VERIFICATION CONTRACT]" in packed.messages[-1]["content"]
    assert "[AUTHORITATIVE TASK STATE]" in packed.messages[-1]["content"]
    assert len(packed.messages[-1]["content"]) <= 5800


def test_every_provider_history_message_stays_below_downstream_per_message_truncation():
    broker = ContextBudgetBroker()
    rows = [
        _conversation(1, "a" * 12000),
        _conversation(2, "b" * 12000),
        _derived("task_state", "c" * 12000),
    ]
    packed = broker.pack(rows)

    assert len(packed.messages) <= 8
    assert all(len(message["content"]) <= 5900 for message in packed.messages)
    assert packed.telemetry["context_budget_output_chars"] <= 15000


def test_asset_text_context_has_independent_explicit_budget():
    broker = ContextBudgetBroker(asset_text_char_budget=9000)
    clipped = broker.clip_asset_context("manifest\n" + ("evidence" * 4000))
    assert len(clipped) <= 9000
    assert "truncated by ContextBudgetBroker" in clipped


def test_plain_eight_message_history_is_not_needlessly_merged_or_dropped():
    broker = ContextBudgetBroker()
    rows = [_conversation(index, f"m{index}") for index in range(8)]
    packed = broker.pack(rows)
    assert [message["id"] for message in packed.messages] == [
        f"msg-{index}" for index in range(8)
    ]
    assert packed.telemetry["context_budget_system_messages_merged"] == 0
    assert packed.telemetry["context_budget_conversation_messages_dropped"] == 0
