from __future__ import annotations

from typing import Any

from .evidence_controller import EvidenceAssessment, EvidenceController, EvidencePlan
from .retrieval_sidecar import MultimodalRetrievalResult
from .scoped_retrieval import scope_eligible_assets


class ScopedEvidenceController(EvidenceController):
    """Run evidence routing/sufficiency only over assets eligible for the active scope."""

    def plan(
        self,
        query: str,
        assets: list[dict[str, Any]],
        *,
        retriever_enabled: bool,
    ) -> EvidencePlan:
        return super().plan(
            query,
            scope_eligible_assets(assets),
            retriever_enabled=retriever_enabled,
        )

    def assess(
        self,
        plan: EvidencePlan,
        result: MultimodalRetrievalResult,
        assets: list[dict[str, Any]],
    ) -> EvidenceAssessment:
        return super().assess(plan, result, scope_eligible_assets(assets))
