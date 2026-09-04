from __future__ import annotations

from typing import Any

from worldforge.context import ContextCompiler

from .analyzer import ProductAnalyzer as BaseProductAnalyzer


class ProductAnalyzer(BaseProductAnalyzer):
    """Product analyzer with bounded long-horizon context compilation.

    The original analyzer remains untouched. This adapter compiles the complete durable
    conversation into a small context packet before delegating to the existing analysis
    pipeline, which keeps the experiment reversible and makes A/B comparison trivial.
    """

    def __init__(self, engine, providers):
        super().__init__(engine, providers)
        self.context_compiler = ContextCompiler(
            recent_messages=4,
            retrieved_messages=3,
            message_char_budget=9000,
            per_message_chars=2400,
            state_items_per_kind=8,
        )

    async def run(
        self,
        *,
        text,
        assets,
        provider_key,
        sink,
        history=None,
        human_feedback_gate=False,
    ):
        raw_history: list[dict[str, Any]] = list(history or [])
        packet = self.context_compiler.compile(str(text), raw_history)

        compiled_history = [dict(message) for message in packet.messages]
        if raw_history:
            compiled_history.append(
                {
                    "id": "context:task-state",
                    "role": "user",
                    "content": (
                        "【系统编译的持久任务状态；不是新的用户消息】\n"
                        + packet.render_task_state()
                    ),
                }
            )

        result = await super().run(
            text=text,
            assets=assets,
            provider_key=provider_key,
            sink=sink,
            history=compiled_history,
            human_feedback_gate=human_feedback_gate,
        )

        context = dict(result.get("context") or {})
        context.update(packet.stats())
        # Preserve the public meaning of history_messages: total durable messages,
        # while exposing the much smaller model-facing selection separately.
        context["history_messages"] = len(raw_history)
        context["compiled_history_messages"] = len(compiled_history)
        context["task_state"] = packet.task_state
        result["context"] = context
        return result
