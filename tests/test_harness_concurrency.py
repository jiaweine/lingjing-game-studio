from __future__ import annotations

import json
import multiprocessing as mp

from worldforge.runtime.harness_evolution import (
    EpisodeMetrics,
    EvolutionCandidate,
    EvolutionEvidence,
    GenomeEvaluation,
)
from worldforge.runtime.harness_genome import HarnessGenome, HarnessGenomeStore
from worldforge.runtime.harness_search import AtomicSemanticQDArchive


def _candidate_from(genome: HarnessGenome, cell: str) -> EvolutionCandidate:
    episode = EpisodeMetrics(
        scenario_id="concurrency",
        seed=1,
        objective=0.8,
        quality=0.8,
        safety=1.0,
        efficiency=0.8,
        success=1.0,
        final_score=1.0,
        operations=1.0,
    )
    evaluation = GenomeEvaluation(
        objective=episode.objective,
        quality=episode.quality,
        safety=episode.safety,
        efficiency=episode.efficiency,
        operations=episode.operations,
        episodes=(episode,),
    )
    evidence = EvolutionEvidence(
        where=cell,
        why="concurrency",
        feature_priorities={"uncertainty": 1.0},
        summary="concurrency regression",
        prediction="atomic persistence preserves independent elites",
    )
    return EvolutionCandidate(
        genome=genome,
        operator="concurrency-test",
        evidence=evidence,
        train=evaluation,
        heldout=evaluation,
        novelty=0.1,
        paired_gain=0.01,
        lower_bound=0.0,
        accepted=True,
    )


def _promotion_worker(path: str, ready, start, results, suffix: str) -> None:
    HarnessGenomeStore.configure(path)
    baseline = HarnessGenomeStore.snapshot()
    candidate = baseline.model_copy(deep=True)
    candidate.genome_id = f"hg-worker-{suffix}"
    candidate.generation = baseline.generation + 1
    candidate.parent_ids = [baseline.genome_id]
    candidate.origin = f"worker-{suffix}"
    ready.put(suffix)
    if not start.wait(20):
        raise RuntimeError("promotion start barrier timed out")
    promoted = HarnessGenomeStore.promote(
        candidate,
        expected_baseline=baseline,
    )
    results.put((suffix, promoted))


def _archive_worker(path: str, ready, start, cell: str) -> None:
    HarnessGenomeStore.configure(None)
    genome = HarnessGenomeStore.current().model_copy(deep=True)
    genome.genome_id = f"hg-archive-{cell}"
    archive = AtomicSemanticQDArchive(path)
    ready.put(cell)
    if not start.wait(20):
        raise RuntimeError("archive start barrier timed out")
    archive.add(cell, _candidate_from(genome, cell))


def _bootstrap_file(path) -> HarnessGenome:
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    path.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    return baseline


def test_stale_higher_generation_cannot_overwrite_new_baseline(tmp_path):
    path = tmp_path / "harness.json"
    baseline = _bootstrap_file(path)
    HarnessGenomeStore.configure(path)

    winner = baseline.model_copy(deep=True)
    winner.genome_id = "hg-winner"
    winner.generation = baseline.generation + 1
    winner.parent_ids = [baseline.genome_id]

    stale = baseline.model_copy(deep=True)
    stale.genome_id = "hg-stale-refined"
    stale.generation = baseline.generation + 3
    stale.parent_ids = [baseline.genome_id]

    assert HarnessGenomeStore.promote(winner, expected_baseline=baseline) is True
    assert HarnessGenomeStore.promote(stale, expected_baseline=baseline) is False

    durable = HarnessGenome.model_validate_json(path.read_text(encoding="utf-8"))
    assert durable.genome_id == winner.genome_id
    assert durable.generation == winner.generation


def test_task_generation_remains_pinned_during_promotion(tmp_path):
    path = tmp_path / "harness.json"
    baseline = _bootstrap_file(path)
    HarnessGenomeStore.configure(path)
    pinned = HarnessGenomeStore.snapshot()

    next_generation = baseline.model_copy(deep=True)
    next_generation.genome_id = "hg-next"
    next_generation.generation = baseline.generation + 1
    next_generation.parent_ids = [baseline.genome_id]

    with HarnessGenomeStore.use(pinned):
        assert HarnessGenomeStore.current().genome_id == baseline.genome_id
        assert HarnessGenomeStore.promote(
            next_generation,
            expected_baseline=baseline,
        ) is True
        assert HarnessGenomeStore.current().genome_id == baseline.genome_id

    assert HarnessGenomeStore.snapshot().genome_id == next_generation.genome_id


def test_only_one_process_can_promote_same_baseline(tmp_path):
    path = tmp_path / "harness.json"
    baseline = _bootstrap_file(path)
    context = mp.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    workers = [
        context.Process(
            target=_promotion_worker,
            args=(str(path), ready, start, results, suffix),
        )
        for suffix in ("a", "b")
    ]
    for worker in workers:
        worker.start()
    assert {ready.get(timeout=20), ready.get(timeout=20)} == {"a", "b"}
    start.set()
    outcomes = [results.get(timeout=20), results.get(timeout=20)]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0

    assert sum(1 for _, promoted in outcomes if promoted) == 1, outcomes
    durable = HarnessGenome.model_validate_json(path.read_text(encoding="utf-8"))
    assert durable.generation == baseline.generation + 1
    assert durable.genome_id in {"hg-worker-a", "hg-worker-b"}


def test_archive_preserves_concurrent_cells(tmp_path):
    path = tmp_path / "archive.json"
    context = mp.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    workers = [
        context.Process(
            target=_archive_worker,
            args=(str(path), ready, start, cell),
        )
        for cell in ("uncertainty::timeout", "threat::rollback")
    ]
    for worker in workers:
        worker.start()
    assert {ready.get(timeout=20), ready.get(timeout=20)} == {
        "uncertainty::timeout",
        "threat::rollback",
    }
    start.set()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["cells"]) == {
        "uncertainty::timeout",
        "threat::rollback",
    }
    assert all(payload["cells"][cell] for cell in payload["cells"])
