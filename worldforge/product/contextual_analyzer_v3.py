from __future__ import annotations

from typing import Any

from .contextual_analyzer_v2 import ProductAnalyzer as _TokenBudgetProductAnalyzer


class ProductAnalyzer(_TokenBudgetProductAnalyzer):
    """ContextOS analyzer that surfaces task-local provider native-token telemetry."""

    async def run(
        self,
        *,
        text,
        assets,
        provider_key,
        sink,
        history=None,
        human_feedback_gate=False,
        project_memory=None,
    ):
        result = await super().run(
            text=text,
            assets=assets,
            provider_key=provider_key,
            sink=sink,
            history=history,
            human_feedback_gate=human_feedback_gate,
            project_memory=project_memory,
        )

        selected_key = None
        request_choice = getattr(self.providers, "request_choice", None)
        if callable(request_choice):
            selected_key = request_choice()

        provider = None
        registry = getattr(self.providers, "providers", None)
        if isinstance(registry, dict) and selected_key:
            provider = registry.get(selected_key)
        telemetry_fn = getattr(provider, "request_telemetry", None)
        provider_telemetry: dict[str, Any] = (
            telemetry_fn() if callable(telemetry_fn) else {}
        )

        context = dict(result.get("context") or {})
        context["provider_selected_key"] = selected_key
        context["provider_selected_model"] = getattr(
            getattr(provider, "info", None), "model", None
        )
        context["provider_native_token_telemetry"] = provider_telemetry
        result["context"] = context
        return result
