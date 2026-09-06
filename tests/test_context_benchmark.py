from __future__ import annotations

from worldforge.benchmarks import run_context_benchmark


def test_context_benchmark_meets_mechanism_thresholds():
    result = run_context_benchmark()

    assert result.constraint_recall == 1.0
    assert result.long_range_identifier_recall == 1.0
    assert result.context_compression_ratio >= 0.80
    assert result.semantic_routing_accuracy >= 0.95
    assert 0.0 < result.semantic_call_rate < 1.0
    assert result.max_temporal_frame_budget <= 6
    assert result.current_state_tracking == 1.0
    assert result.open_question_closure == 1.0
    assert result.premise_awareness == 1.0
    assert result.incremental_state_cache == 1.0
    assert result.long_horizon_turns >= 500
    assert result.passed is True
