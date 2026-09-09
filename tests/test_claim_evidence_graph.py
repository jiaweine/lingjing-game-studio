from __future__ import annotations

from worldforge.context.claim_graph import build_claim_evidence_graph
from worldforge.context.evidence_controller import EvidenceController
from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalHit,
    MultimodalRetrievalResult,
)
from worldforge.context.verification_contract import build_verification_contract


def _asset(asset_id: str, kind: str, **meta):
    mime = {
        "video": "video/mp4",
        "text": "text/plain",
        "image": "image/png",
    }.get(kind, "application/octet-stream")
    return {
        "id": asset_id,
        "name": f"{asset_id}.{kind}",
        "mime": mime,
        "meta": {"kind": kind, **meta},
    }


def test_causal_graph_never_promotes_synthetic_runtime_to_project_proof():
    assets = [_asset("run", "video"), _asset("log", "text")]
    plan = EvidenceController().plan(
        "为什么 Boss 偶发双盾？结合录像和日志找根因",
        assets,
        retriever_enabled=True,
    )
    contract = build_verification_contract(
        plan, assets, actual_project_execution_available=False
    )
    semantic = MultimodalRetrievalResult(
        hits=[
            MultimodalRetrievalHit(
                asset_id="run",
                score=0.91,
                modality="video",
                start=120.0,
                end=132.0,
                evidence_ref="asset:run:segment:120-132",
            )
        ],
        backend="wemm",
        latency_ms=10.0,
        available=True,
    )
    graph = build_claim_evidence_graph(
        answer="双盾可能与重复触发有关。",
        evidence=[
            {"id": "E1", "type": "video", "asset_id": "run", "title": "录像"},
            {"id": "E2", "type": "text", "asset_id": "log", "title": "日志"},
            {"id": "E3", "type": "replay", "title": "synthetic replay"},
        ],
        plan=plan,
        semantic_result=semantic,
        contract=contract,
        runtime_result={"outcome": "failure"},
    )

    assert graph.verified is False
    assert graph.claims[0]["status"] == "hypothesis"
    assert graph.requirements["synthetic_runtime_counts_as_project_verification"] is False
    assert any(
        edge["relation"] == "mechanism-only" and edge["evidence_id"] == "E3"
        for edge in graph.edges
    )
    assert any(edge["relation"] == "locates" for edge in graph.edges)


def test_comparison_without_build_identity_stays_directional():
    assets = [_asset("before", "image"), _asset("after", "image")]
    plan = EvidenceController().plan(
        "对比前后截图差异",
        assets,
        retriever_enabled=False,
    )
    contract = build_verification_contract(plan, assets)
    graph = build_claim_evidence_graph(
        answer="两张图看起来不同。",
        evidence=[
            {"id": "E1", "type": "image", "asset_id": "before", "title": "before"},
            {"id": "E2", "type": "image", "asset_id": "after", "title": "after"},
        ],
        plan=plan,
        semantic_result=MultimodalRetrievalResult([], None, 0.0, False),
        contract=contract,
        runtime_result=None,
    )

    assert contract.claim_ceiling == "directional-comparison-only"
    assert graph.claims[0]["status"] == "directional"
    assert graph.verified is False
