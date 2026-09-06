from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .evidence_controller import EvidencePlan
from .retrieval_sidecar import MultimodalRetrievalResult
from .verification_contract import VerificationContract


def _compact(text: Any, limit: int = 700) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _claim_status(contract: VerificationContract) -> str:
    ceiling = contract.claim_ceiling
    if ceiling.startswith("hypothesis-only"):
        return "hypothesis"
    if ceiling.startswith("directional-comparison"):
        return "directional"
    if ceiling.startswith("localized-observation"):
        return "localized-observation"
    return "evidence-bounded-observation"


@dataclass(frozen=True)
class ClaimEvidenceGraph:
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    requirements: dict[str, Any]
    verified: bool
    verification_reason: str
    mode: str = "deterministic-claim-envelope-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "claims": self.claims,
            "evidence": self.evidence,
            "edges": self.edges,
            "requirements": self.requirements,
            "verified": self.verified,
            "verification_reason": self.verification_reason,
        }

    def stats(self) -> dict[str, Any]:
        authority_counts: dict[str, int] = {}
        for node in self.evidence:
            authority = str(node.get("authority") or "unknown")
            authority_counts[authority] = authority_counts.get(authority, 0) + 1
        return {
            "claim_graph_mode": self.mode,
            "claim_graph_claims": len(self.claims),
            "claim_graph_evidence_nodes": len(self.evidence),
            "claim_graph_edges": len(self.edges),
            "claim_graph_authority_counts": authority_counts,
            "claim_graph_verified": self.verified,
            "claim_graph_verification_reason": self.verification_reason,
        }


def build_claim_evidence_graph(
    *,
    answer: Any,
    evidence: list[dict[str, Any]],
    plan: EvidencePlan,
    semantic_result: MultimodalRetrievalResult,
    contract: VerificationContract,
    runtime_result: dict[str, Any] | None,
) -> ClaimEvidenceGraph:
    """Build an auditable claim envelope without pretending retrieval equals proof.

    The first version deliberately avoids LLM-based claim extraction. The generated answer
    is represented as one bounded claim envelope; raw assets are merely `considered`,
    embedding hits only `locate` candidate evidence, and built-in runtime checks are marked
    `mechanism-only`. Stronger relations (`supports`, `contradicts`, `proves`) are reserved
    for future structured project adapters/verifiers that can establish them independently.
    """
    root_claim = {
        "id": "C1",
        "kind": "analysis-output",
        "text": _compact(answer),
        "status": _claim_status(contract),
        "claim_ceiling": contract.claim_ceiling,
        "confidence_cap": round(contract.confidence_cap, 2),
    }
    claims = [root_claim]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_source_kinds: set[str] = set()

    for index, row in enumerate(evidence, start=1):
        evidence_id = str(row.get("id") or f"E{index}")
        if evidence_id in seen_ids:
            evidence_id = f"E{index}"
        seen_ids.add(evidence_id)
        kind = str(row.get("type") or "file")
        asset_id = row.get("asset_id")
        if kind == "replay":
            authority = "synthetic-mechanism"
            relation = "mechanism-only"
        elif asset_id:
            authority = "raw-project-evidence"
            relation = "considered"
            raw_source_kinds.add(kind)
        else:
            authority = "derived-summary"
            relation = "considered"
        nodes.append(
            {
                "id": evidence_id,
                "kind": kind,
                "title": _compact(row.get("title"), 300),
                "asset_id": asset_id,
                "authority": authority,
            }
        )
        edges.append(
            {
                "claim_id": "C1",
                "evidence_id": evidence_id,
                "relation": relation,
            }
        )

    for index, hit in enumerate(semantic_result.hits, start=1):
        evidence_id = f"L{index}"
        locator: dict[str, Any] = {}
        if hit.start is not None:
            locator["start"] = hit.start
        if hit.end is not None:
            locator["end"] = hit.end
        if hit.char_start is not None:
            locator["char_start"] = hit.char_start
        if hit.char_end is not None:
            locator["char_end"] = hit.char_end
        nodes.append(
            {
                "id": evidence_id,
                "kind": str(hit.modality or "semantic-locator"),
                "asset_id": hit.asset_id,
                "authority": "semantic-locator-only",
                "score": round(float(hit.score), 6),
                "locator": locator,
                "evidence_ref": hit.evidence_ref,
            }
        )
        edges.append(
            {
                "claim_id": "C1",
                "evidence_id": evidence_id,
                "relation": "locates",
            }
        )

    required_sources = int(contract.require_independent_sources)
    independent_sources = len(raw_source_kinds)
    project_execution = bool(contract.actual_project_execution_available)
    identity_ok = (
        not contract.require_identity_alignment
        or bool(contract.identity_fields_present)
    )
    localization_ok = (
        not contract.require_source_localization
        or any(
            node.get("locator")
            for node in nodes
            if node.get("authority") == "semantic-locator-only"
        )
        or any(
            node.get("authority") == "raw-project-evidence" for node in nodes
        )
    )

    if plan.causal:
        verified = bool(
            project_execution
            and independent_sources >= required_sources
            and identity_ok
            and localization_ok
        )
        reason = (
            "causal-project-verification-satisfied"
            if verified
            else "causal-project-verification-missing"
        )
    elif plan.comparison:
        verified = bool(identity_ok and independent_sources >= required_sources)
        reason = (
            "comparison-identity-satisfied"
            if verified
            else "comparison-identity-missing"
        )
    else:
        verified = bool(independent_sources >= required_sources and localization_ok)
        reason = (
            "observation-evidence-envelope-satisfied"
            if verified
            else "observation-evidence-envelope-incomplete"
        )

    # Synthetic runtime output is intentionally visible for audit but never upgrades
    # project verification. `runtime_result` is only recorded as a mechanism check flag.
    requirements = {
        "required_independent_sources": required_sources,
        "observed_independent_source_kinds": independent_sources,
        "require_project_execution_for_causality": bool(
            contract.require_project_execution_for_causality
        ),
        "actual_project_execution_available": project_execution,
        "require_identity_alignment": bool(contract.require_identity_alignment),
        "identity_fields_present": list(contract.identity_fields_present),
        "require_source_localization": bool(contract.require_source_localization),
        "synthetic_runtime_present": bool(runtime_result),
        "synthetic_runtime_counts_as_project_verification": False,
    }
    return ClaimEvidenceGraph(
        claims=claims,
        evidence=nodes,
        edges=edges,
        requirements=requirements,
        verified=verified,
        verification_reason=reason,
    )
