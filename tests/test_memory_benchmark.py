from worldforge.benchmarks import run_memory_benchmark


def test_lingjing_memory_benchmark_passes_all_governance_competencies():
    result = run_memory_benchmark()
    assert result.passed, result.to_dict()
    assert result.score == 1.0
    assert result.cross_conversation_recall == 1.0
    assert result.update_tracking == 1.0
    assert result.scoped_version_isolation == 1.0
    assert result.conflict_abstention == 1.0
    assert result.selective_forgetting == 1.0
    assert result.pending_memory_isolation == 1.0
    assert result.provenance_integrity == 1.0
    assert result.queued_snapshot_revocation == 1.0
    assert result.restart_persistence == 1.0
