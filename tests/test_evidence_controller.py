from __future__ import annotations

from worldforge.context.evidence_controller import EvidenceController
from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalHit,
    MultimodalRetrievalResult,
)


def _asset(asset_id: str, kind: str, *, selected=True, reasons=None, full_hits=0, time_hints=None, has_audio=False):
    mime = {
        "video": "video/mp4",
        "image": "image/png",
        "audio": "audio/wav",
        "text": "text/plain",
    }.get(kind, "application/octet-stream")
    return {
        "id": asset_id,
        "name": f"{asset_id}.{kind}",
        "mime": mime,
        "meta": {
            "kind": kind,
            "has_audio": has_audio,
            "_context": {
                "kind": kind,
                "selected": selected,
                "reasons": list(reasons or []),
                "full_content_hits": full_hits,
                "time_hints": list(time_hints or []),
            },
        },
    }


def test_exact_timestamp_goes_directly_to_raw_temporal_evidence():
    controller = EvidenceController()
    plan = controller.plan(
        "37 秒附近 Boss 护盾图标是什么状态？",
        [_asset("run", "video", time_hints=[37.0])],
        retriever_enabled=True,
    )

    assert plan.needs_temporal is True
    assert plan.semantic_retrieval is False
    assert plan.reason == "direct-temporal-source-evidence"
    assert plan.temporal_frame_budget == 4


def test_causal_multimodal_problem_spends_semantic_budget():
    controller = EvidenceController()
    assets = [
        _asset("run", "video", has_audio=True),
        _asset("log", "text"),
        _asset("config", "text"),
    ]
    plan = controller.plan(
        "为什么 release 版本偶发 Boss 双盾？对比录像、日志和配置找根因",
        assets,
        retriever_enabled=True,
    )

    assert plan.causal is True
    assert plan.comparison is True
    assert plan.semantic_retrieval is True
    assert plan.temporal_frame_budget == 6
    assert plan.max_semantic_promotions == 8


def test_full_text_identifier_hit_avoids_unnecessary_gpu_retrieval():
    controller = EvidenceController()
    log = _asset(
        "runtime-log",
        "text",
        reasons=["identifier-match", "full-content-match"],
        full_hits=3,
    )
    plan = controller.plan(
        "检查 build 1.4.7 的 shield_race 日志",
        [log],
        retriever_enabled=True,
    )

    assert plan.exact_identifier is True
    assert plan.semantic_retrieval is False
    assert plan.deterministic_score >= 0.5


def test_single_large_log_with_no_lexical_hit_still_uses_semantic_chunks():
    controller = EvidenceController()
    plan = controller.plan(
        "日志里有没有资源释放顺序异常？",
        [_asset("runtime-log", "text")],
        retriever_enabled=True,
    )

    assert plan.needs_text is True
    assert plan.semantic_retrieval is True


def test_localized_semantic_hit_stops_after_one_high_value_round():
    controller = EvidenceController()
    assets = [_asset("run", "video"), _asset("log", "text")]
    plan = controller.plan(
        "为什么 Boss 偶发双盾？结合视频和日志找原因",
        assets,
        retriever_enabled=True,
    )
    result = MultimodalRetrievalResult(
        hits=[
            MultimodalRetrievalHit(
                asset_id="run",
                score=0.92,
                modality="video",
                start=132.0,
                end=146.0,
                evidence_ref="asset:run:segment:fine:132-146",
            ),
            MultimodalRetrievalHit(
                asset_id="log",
                score=0.87,
                modality="text",
                char_start=1000,
                char_end=1800,
                text_excerpt="shield apply event repeated",
                evidence_ref="asset:log:chars:1000-1800",
            ),
        ],
        backend="wemm",
        latency_ms=33.0,
        available=True,
    )

    assessment = controller.assess(plan, result, assets)

    assert assessment.stop is True
    assert assessment.localized_hits == 2
    assert assessment.stop_reason == "localized-semantic-evidence-sufficient"
    assert "两类独立证据" in assessment.verifier_posture
