import asyncio

from worldforge.product.analyzer import ProductAnalyzer


class _ProviderRoute:
    def __init__(self, provider=None):
        self.provider = provider

    def choose(self, _preferred, _assets):
        return self.provider


class _ModelProvider:
    async def chat(self, **_kwargs):
        return "模型形成的待验证结论"


def _run_without_runtime(provider=None):
    events = []

    async def sink(type_, payload):
        events.append((type_, payload))

    analyzer = ProductAnalyzer(object(), _ProviderRoute(provider))
    result = asyncio.run(
        analyzer.run(
            text="检查 NPC 角色行为切换",
            assets=[],
            provider_key="auto",
            sink=sink,
        )
    )
    return result, events


def test_synthetic_evidence_has_limited_confidence_weight():
    observed = [
        {
            "id": "E1",
            "type": "video",
            "provenance": "observed",
        }
    ]
    synthetic = observed + [
        {
            "id": "E2",
            "type": "simulation",
            "provenance": "synthetic",
        }
    ]
    reproduced = observed + [
        {
            "id": "E2",
            "type": "replay",
            "provenance": "reproduced",
        }
    ]

    observed_score = ProductAnalyzer._evidence_confidence(observed)
    synthetic_score = ProductAnalyzer._evidence_confidence(synthetic, {"status": "done"})
    reproduced_score = ProductAnalyzer._evidence_confidence(reproduced)

    assert synthetic_score > observed_score
    assert synthetic_score - observed_score <= 0.08
    assert reproduced_score > synthetic_score


def test_runtime_presence_alone_does_not_inflate_confidence():
    evidence = [
        {
            "id": "E1",
            "type": "text",
            "provenance": "observed",
        }
    ]

    without_runtime = ProductAnalyzer._evidence_confidence(evidence, None)
    with_runtime = ProductAnalyzer._evidence_confidence(
        evidence,
        {"status": "done", "evidence_provenance": "synthetic"},
    )

    assert with_runtime == without_runtime


def test_evidence_pack_preserves_provenance_aware_wording():
    analyzer = object.__new__(ProductAnalyzer)
    evidence = [
        {
            "id": "E1",
            "type": "video",
            "title": "boss-phase-2.mp4",
            "provenance": "observed",
        },
        {
            "id": "E2",
            "type": "simulation",
            "title": "WorldForge 场景 boss_burst",
            "provenance": "synthetic",
        },
    ]

    deliverables = analyzer._deliverables("regression", evidence, {"status": "done"})
    reproduction_card = next(
        row for row in deliverables if row["type"] == "reproduction_card"
    )
    evidence_pack = next(row for row in deliverables if row["type"] == "evidence_pack")

    assert "真实" in reproduction_card["summary"]
    assert "内部模拟" in evidence_pack["summary"]
    assert evidence_pack["evidence_ids"] == ["E1", "E2"]


def test_demo_fallback_is_explicitly_non_verified():
    result, events = _run_without_runtime()

    assert result["analysis_mode"] == "demo"
    assert result["claim_status"] == "hypothesis_only"
    assert result["verification_status"] == "not_verified"
    assert result["answer"].startswith("### 演示分析（非真实游戏结论）")
    assert result["context"]["real_game_reproduced"] is False
    assert any(
        type_ == "notice" and payload["title"] == "当前为演示分析"
        for type_, payload in events
    )


def test_model_output_still_requires_real_game_verification():
    result, _events = _run_without_runtime(_ModelProvider())

    assert result["analysis_mode"] == "model_assisted"
    assert result["answer"] == "模型形成的待验证结论"
    assert result["claim_status"] == "hypothesis_only"
    assert result["verification_status"] == "not_verified"
