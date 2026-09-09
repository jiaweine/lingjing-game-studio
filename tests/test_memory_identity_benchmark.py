from __future__ import annotations

from worldforge.benchmarks.memory_identity_eval import run_memory_identity_benchmark


def test_memory_identity_benchmark_prioritizes_zero_false_merge():
    result = run_memory_identity_benchmark()
    assert result.passed is True
    assert result.cases >= 250
    assert result.negative_cases >= result.positive_cases
    assert result.false_merge_rate == 0.0
    assert result.precision == 1.0
    assert result.positive_recall >= 0.80
    assert result.safe_coverage_at_zero_false_merge >= 0.25
    assert result.evidence_class == "synthetic-adversarial-safety-floor-not-sota"


def test_memory_identity_benchmark_reports_risk_coverage_and_unsafe_comparator():
    result = run_memory_identity_benchmark()
    assert len(result.risk_coverage_curve) >= 6
    assert {point.threshold for point in result.risk_coverage_curve} >= {
        0.60,
        0.72,
        0.90,
    }
    assert result.unsafe_best_candidate_false_merge_rate >= result.false_merge_rate
    assert "generated-predicate-collision" in result.family_metrics
    assert "generated-entity-collision" in result.family_metrics
    assert "generated-ambiguous-keys" in result.family_metrics
