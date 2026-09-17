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

    @staticmethod
    def _structured_outcome(result: dict[str, Any]) -> dict[str, Any]:
        """Expose a product-level outcome without exceeding the verification contract.

        The current product analyzer can inspect user-provided evidence and, when explicitly
        enabled, run synthetic built-in scenarios. Neither path is equivalent to independent
        execution in the user's actual project. Until a governed external-engine observation
        is independently verified, bug/reproduction tasks therefore stay at
        ``insufficient_evidence`` instead of being promoted to ``reproduced`` or
        ``not_reproduced`` merely because generated prose sounds decisive.

        Non-issue workflows keep their existing human quality gate. They are marked as
        ``issue_lifecycle=False`` so approving a useful balance/content analysis does not get
        blocked by the stricter Bug/Fix verifier requirement.
        """
        intent = str(result.get("intent") or "general")
        context = dict(result.get("context") or {})
        project_execution = bool(context.get("actual_project_execution_available"))
        claim_ceiling = str(context.get("verification_claim_ceiling") or "")
        runtime_scope = str(
            context.get("runtime_verification_scope")
            or context.get("runtime_scope")
            or "none"
        )

        if intent in {"battle_review", "regression"}:
            common = {
                "issue_lifecycle": True,
                "requires_project_verification": True,
                "intent": intent,
                "claim_ceiling": claim_ceiling or "evidence-bounded-observation",
                "runtime_scope": runtime_scope,
            }
            if not project_execution:
                return {
                    **common,
                    "state": "insufficient_evidence",
                    "label": "证据不足",
                    "verified": False,
                    "project_execution": False,
                    "reason": (
                        "当前没有用户项目中的独立验证执行证据；素材分析和内置机制模拟"
                        "不能单独证明问题已复现或修复已生效。"
                    ),
                    "next_action": "连接真实项目执行，或补充可独立复核的项目级执行证据。",
                }
            return {
                **common,
                "state": "needs_verifier_decision",
                "label": "需要验证结论",
                "verified": False,
                "project_execution": True,
                "reason": "已存在真实项目执行上下文，但仍需独立 Verifier 决定是否可标记为已复现或已修复。",
                "next_action": "等待或执行独立 Verifier 判定。",
            }

        return {
            "issue_lifecycle": False,
            "requires_project_verification": False,
            "intent": intent,
            "state": "analysis_complete",
            "label": "分析完成",
            "verified": False,
            "project_execution": project_execution,
            "reason": "当前结果是受证据边界约束的分析结论；是否接受该分析仍由原有人工质量门决定。",
            "claim_ceiling": claim_ceiling or "evidence-bounded-observation",
            "runtime_scope": runtime_scope,
            "next_action": "根据结果中的下一步验证动作继续推进。",
        }

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
        result["outcome"] = self._structured_outcome(result)
        return result
