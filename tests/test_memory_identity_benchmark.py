from __future__ import annotations

from worldforge.benchmarks.memory_identity_eval import run_memory_identity_benchmark


def test_memory_identity_benchmark_prioritizes_zero_false_merge():
    result = run_memory_identity_benchmark()
    assert result.passed is True
    assert result.false_merge_rate == 0.0
    assert result.precision == 1.0
    assert result.positive_recall >= 0.75
