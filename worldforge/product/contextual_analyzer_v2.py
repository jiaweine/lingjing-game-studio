from __future__ import annotations

from typing import Any

from worldforge.context.token_budget import ProviderAwareContextBudgetBroker

from .contextual_analyzer import ProductAnalyzer as _ContextualProductAnalyzer


class ProductAnalyzer(_ContextualProductAnalyzer):
    """ContextOS analyzer with request-scoped provider token-budget binding.

    Provider selection still belongs to the existing inference registry/BaseAnalyzer path.
    This wrapper only supplies the requested provider identity to the context packer. Explicit
    providers can use their declared token profile; ``auto`` uses an explicit
    ``LINGJING_AUTO_*`` common-denominator profile when configured, otherwise it safely keeps
    the legacy character budget. ContextVar binding keeps concurrent analysis jobs isolated.
    """

    def __init__(self, engine, providers):
        super().__init__(engine, providers)
        previous = self.context_budget
        self.context_budget = ProviderAwareContextBudgetBroker(
            max_history_messages=previous.max_history_messages,
            history_char_budget=previous.history_char_budget,
            kernel_char_budget=previous.kernel_char_budget,
            per_message_char_budget=previous.per_message_char_budget,
            asset_text_char_budget=previous.asset_text_char_budget,
            section_char_budgets=dict(previous.section_char_budgets),
        )

    def _requested_provider_model(self, provider_key: str | None) -> str | None:
        key = str(provider_key or "auto")
        if key in {"auto", "demo"}:
            return None
        registry = getattr(self.providers, "providers", None)
        if not isinstance(registry, dict):
            return None
        provider = registry.get(key)
        info = getattr(provider, "info", None)
        model = getattr(info, "model", None)
        return str(model) if model else None

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
        token = self.context_budget.bind_provider(
            provider_key,
            model=self._requested_provider_model(provider_key),
        )
        try:
            return await super().run(
                text=text,
                assets=assets,
                provider_key=provider_key,
                sink=sink,
                history=history,
                human_feedback_gate=human_feedback_gate,
                project_memory=project_memory,
            )
        finally:
            self.context_budget.reset_provider(token)
