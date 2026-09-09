from __future__ import annotations

import pytest

from worldforge.product import ProductAnalyzer


class _ExplodingEngine:
    async def run(self, *_args, **_kwargs):
        raise AssertionError("synthetic engine must not run in the default product path")


class _NoProviders:
    def choose(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_contextos_does_not_run_synthetic_scenario_by_default(monkeypatch):
    monkeypatch.delenv("LINGJING_PRODUCT_SYNTHETIC_REVIEW", raising=False)
    analyzer = ProductAnalyzer(_ExplodingEngine(), _NoProviders())

    async def sink(_type, _payload):
        return None

    result = await analyzer.run(
        text="这个版本的回归 bug 是否已经复现？",
        assets=[],
        provider_key="auto",
        sink=sink,
        history=[],
        human_feedback_gate=False,
    )

    assert analyzer.synthetic_review_enabled is False
    assert result["runtime"] is None
    assert result["context"]["runtime_scope"] == "none"
    assert result["context"]["runtime_verification_scope"] == "none"
    assert result["context"]["synthetic_review_opt_in"] is False
    assert not any(row.get("type") == "replay" for row in result["evidence"])
    assert "尚未" in result["answer"]
    assert result["claim_evidence_graph"]["verified"] is False


def test_base_analyzer_fallback_never_claims_real_project_reproduction_without_runtime():
    from worldforge.product.analyzer import ProductAnalyzer as BaseProductAnalyzer

    analyzer = object.__new__(BaseProductAnalyzer)
    answer = analyzer._demo_answer("regression", None)

    assert "尚未" in answer
    assert "用户项目环境" in answer
    assert "稳定复现条件" not in answer
