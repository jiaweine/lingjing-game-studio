from worldforge.benchmarks import run_benchmark


def test_benchmark_runs():
    rows = run_benchmark(
        seeds=4, scenarios=["boss_burst", "glass_cannon"]
    )
    assert len(rows) == 4
    assert all(0 <= row.success_rate <= 1 for row in rows)


def test_full_runtime_improves_verification_smoke():
    rows = {
        row.harness: row
        for row in run_benchmark(
            seeds=4, scenarios=["boss_burst", "glass_cannon"]
        )
    }
    assert (
        rows["WorldForge Runtime"].success_rate
        >= rows["Policy + Verification"].success_rate
    )
