from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence_controller import EvidencePlan


def _kind(asset: dict[str, Any]) -> str:
    meta = asset.get("meta", {}) or {}
    context = meta.get("_context", {}) or {}
    kind = str(context.get("kind") or meta.get("kind") or "")
    if kind and kind != "file":
        return kind
    mime = str(asset.get("mime", "") or "")
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/") or mime in {
        "application/json", "application/xml", "text/csv"
    }:
        return "text"
    return kind or "file"


@dataclass(frozen=True)
class VerificationContract:
    claim_ceiling: str
    confidence_cap: float
    require_independent_sources: int
    require_project_execution_for_causality: bool
    require_identity_alignment: bool
    require_source_localization: bool
    observed_modalities: tuple[str, ...]
    identity_fields_present: tuple[str, ...]
    actual_project_execution_available: bool = False

    def render(self) -> str:
        rules = [
            "检索向量、摘要和派生索引只能定位证据，不能单独证明事实。",
            "内置 WorldForge synthetic scenario 只验证运行机制/算法路径，不等同于用户项目真实复现。",
        ]
        if self.require_source_localization:
            rules.append("涉及时间/日志片段的结论必须引用原始素材时间段或字符范围。")
        if self.require_identity_alignment:
            rules.append("版本比较必须确认 build/branch/commit/config 身份对齐；缺失时只能给方向性判断。")
        if self.require_project_execution_for_causality:
            rules.append(
                f"因果结论至少需要 {self.require_independent_sources} 类独立项目证据，且需真实项目执行、反事实或 Verifier 复现；否则只标记为假设。"
            )
        rules.append(f"当前允许的最高结论强度: {self.claim_ceiling}。")
        return "【验证契约；系统约束，不是用户陈述】\n" + "\n".join(
            f"- {rule}" for rule in rules
        )

    def stats(self) -> dict[str, Any]:
        return {
            "verification_claim_ceiling": self.claim_ceiling,
            "verification_confidence_cap": round(self.confidence_cap, 2),
            "verification_require_independent_sources": self.require_independent_sources,
            "verification_require_project_execution_for_causality": self.require_project_execution_for_causality,
            "verification_require_identity_alignment": self.require_identity_alignment,
            "verification_require_source_localization": self.require_source_localization,
            "verification_observed_modalities": list(self.observed_modalities),
            "verification_identity_fields_present": list(self.identity_fields_present),
            "actual_project_execution_available": self.actual_project_execution_available,
            "synthetic_runtime_counts_as_project_verification": False,
        }


def build_verification_contract(
    plan: EvidencePlan,
    assets: list[dict[str, Any]],
    *,
    actual_project_execution_available: bool = False,
) -> VerificationContract:
    modalities = tuple(sorted({_kind(asset) for asset in assets if _kind(asset) != "file"}))
    identity_fields = []
    for key in ("build", "branch", "commit", "config"):
        if any((asset.get("meta", {}) or {}).get(key) for asset in assets):
            identity_fields.append(key)

    require_identity = bool(plan.comparison)
    require_localization = bool(
        plan.needs_temporal
        or plan.needs_visual
        or plan.needs_audio
        or plan.needs_text
    )
    require_project_execution = bool(plan.causal)
    independent = 2 if plan.causal else 1

    if plan.causal and not actual_project_execution_available:
        ceiling = "hypothesis-only-until-real-project-verification"
        cap = 0.64
    elif plan.comparison and not identity_fields:
        ceiling = "directional-comparison-only"
        cap = 0.60
    elif plan.needs_temporal:
        ceiling = "localized-observation"
        cap = 0.82
    else:
        ceiling = "evidence-bounded-observation"
        cap = 0.86

    return VerificationContract(
        claim_ceiling=ceiling,
        confidence_cap=cap,
        require_independent_sources=independent,
        require_project_execution_for_causality=require_project_execution,
        require_identity_alignment=require_identity,
        require_source_localization=require_localization,
        observed_modalities=modalities,
        identity_fields_present=tuple(identity_fields),
        actual_project_execution_available=actual_project_execution_available,
    )
