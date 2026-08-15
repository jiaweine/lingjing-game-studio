from worldforge.benchmarks import run_benchmark

def test_benchmark_runs():
    rows=run_benchmark(seeds=4,scenarios=['boss_burst','glass_cannon']);assert len(rows)==4;assert all(0<=r.success_rate<=1 for r in rows)

def test_full_harness_improves_verifier_smoke():
    rows={r.harness:r for r in run_benchmark(seeds=4,scenarios=['boss_burst','glass_cannon'])};assert rows['WorldForge Harness'].success_rate>=rows['M1 + Verifier'].success_rate
