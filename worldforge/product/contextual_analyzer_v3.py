from __future__ import annotations

from typing import Any

from worldforge.context.project_packet import ProjectMemoryPacket
from worldforge.context.scoped_evidence import ScopedEvidenceController
from worldforge.context.scoped_retrieval import ScopedMultimodalRetrievalClient

from .contextual_analyzer_v2 import ProductAnalyzer as _TokenBudgetProductAnalyzer


class ProductAnalyzer(_TokenBudgetProductAnalyzer):
    """ContextOS analyzer with native-token telemetry and task-local scope safety."""

    def __init__(self, engine, providers):
        super().__init__(engine, providers)
        # Keep the stable ContextOS run path intact while making scope a hard control-plane
        # boundary for expensive retrieval/evidence planning. Both replacements retain the
        # original fail-open contracts and environment configuration.
        self.semantic_retriever = ScopedMultimodalRetrievalClient()
        self.evidence_controller = ScopedEvidenceController()

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
        packet = (
            project_memory
            if isinstance(project_memory, ProjectMemoryPacket)
            else ProjectMemoryPacket.from_dict(project_memory)
        )
        bind = getattr(self.multimodal_compiler, "bind_scope", None)
        reset = getattr(self.multimodal_compiler, "reset_scope", None)
        token = bind(packet.scope if packet is not None else None) if callable(bind) else None
        try:
            result = await super().run(
                text=text,
                assets=assets,
                provider_key=provider_key,
                sink=sink,
                history=history,
                human_feedback_gate=human_feedback_gate,
                project_memory=packet,
            )
        finally:
            if token is not None and callable(reset):
                reset(token)

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
