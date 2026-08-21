from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.models import RunConfig
from worldforge.runtime import (
    GameEvolutionConfig,
    HarnessEvolutionEngine,
    HarnessGenomeStore,
    SelfEvolvingWorldForgeEngine,
)

BENCHMARK_PROTOCOL = "runtime-trace-sealed-heldout-game-harness-2026-08"
BENCHMARK_SNAPSHOT = ROOT / "docs" / "benchmark-results.json"


def _evolution_config() -> GameEvolutionConfig:
    return GameEvolutionConfig(
        population=8,
        train_seeds=(11, 23),
        heldout_seeds=(37, 51),
        eval_width_cap=2,
        eval_horizon_cap=2,
        eval_rollout_cap=2,
        bootstrap_samples=128,
        confidence_quantile=0.10,
        refinement_rounds=2,
        elite_fraction=0.5,
        refinements_per_elite=1,
        min_heldout_gain=0.0,
        min_lower_bound=0.0,
        quality_tolerance=0.0,
        efficiency_tolerance=0.02,
        seed=20260818,
    )


async def _run_benchmark_async() -> dict:
    """Exercise the product evolution path from trace diagnosis through durable promotion."""
    config = _evolution_config()
    with tempfile.TemporaryDirectory(prefix="lingjing-harness-benchmark-") as temp_dir:
        root = Path(temp_dir)
        runtime = SelfEvolvingWorldForgeEngine(root / "runtime.db")
        runtime.harness_evolver = HarnessEvolutionEngine(
            config=config,
            archive_path=root / "promotion-archive.json",
        )
        baseline = HarnessGenomeStore.snapshot()

        # The trigger is deliberately a real, bounded Runtime trajectory rather than a
        # hand-authored EvolutionEvidence object. One canonical step cannot finish boss_burst,
        # so the frozen Runtime produces a timeout trace and the same TraceReflector used by the
        # product derives WHERE × WHY mutation pressure from that evidence.
        trigger_config = RunConfig(
            scenario_id="boss_burst",
            seed=7,
            max_steps=1,
            branch_width=2,
            rollout_horizon=2,
            rollouts_per_branch=2,
            enable_counterfactual=True,
            enable_recursive_agents=True,
            enable_evolution=True,
        )
        summary = await runtime.run(trigger_config, demo_delay=0)
        events = runtime.events.list_events(summary.session_id)

        started = next(
            (event for event in events if event.event_type == "harness.evolution.started"),
            None,
        )
        evolution = next(
            (event for event in events if event.event_type == "harness.evolution"),
            None,
        )
        if started is None or evolution is None:
            raise RuntimeError(
                "Runtime trace did not enter Harness evolution; benchmark must exercise the "
                "same diagnosis path as the product."
            )

        payload = dict(evolution.payload)
        accepted = [candidate for candidate in payload["candidates"] if candidate["accepted"]]
        champion_id = payload["champion"]["genome_id"]
        promoted_candidate = next(
            (
                candidate
                for candidate in accepted
                if candidate["genome"]["genome_id"] == champion_id
            ),
            None,
        )
        current = HarnessGenomeStore.snapshot()
        action_sequence = [
            event.payload.get("action")
            for event in events
            if event.event_type == "action.executed" and event.payload.get("action")
        ]
        action_counts = Counter(action_sequence)

        payload["protocol"] = {
            "id": BENCHMARK_PROTOCOL,
            "evidence_source": "verified-runtime-event-trace",
            "hand_authored_evolution_evidence": False,
            "trigger_scenario": trigger_config.scenario_id,
            "trigger_seed": trigger_config.seed,
            "trigger_max_steps": trigger_config.max_steps,
            "train_seeds": list(config.train_seeds),
            "heldout_seeds": list(config.heldout_seeds),
            "heldout_used_for_search": False,
            "promotion_requires_nonnegative_heldout_gain": True,
            "promotion_requires_nonnegative_paired_bootstrap_lcb": True,
            "promotion_requires_no_quality_regression": True,
            "promotion_requires_no_safety_regression": True,
            "durable_compare_and_swap_promotion": True,
        }
        payload["trigger"] = {
            "summary": summary.model_dump(),
            "evidence": started.payload["evidence"],
            "action_sequence": action_sequence,
            "action_counts": dict(action_counts),
        }
        payload["candidate_count"] = len(payload["candidates"])
        payload["accepted_candidate_count"] = len(accepted)
        payload["durable_generation"] = current.generation
        payload["durable_genome_id"] = current.genome_id
        if promoted_candidate is not None:
            payload["promoted_evaluation"] = {
                "operator": promoted_candidate["operator"],
                "novelty": promoted_candidate["novelty"],
                "paired_gain": promoted_candidate["paired_gain"],
                "lower_bound": promoted_candidate["lower_bound"],
                "train": promoted_candidate["train"],
                "heldout": promoted_candidate["heldout"],
            }

        if summary.outcome == "victory":
            raise RuntimeError("Benchmark trigger unexpectedly completed instead of producing evidence.")
        if current.generation <= baseline.generation:
            raise RuntimeError("Harness generation did not advance durably after the benchmark run.")
        if current.genome_id != champion_id:
            raise RuntimeError("Durable Harness does not match the independently evaluated champion.")
        return payload


def run_benchmark() -> dict:
    return asyncio.run(_run_benchmark_async())


def _snapshot_summary(payload: dict) -> dict:
    promoted = payload["promoted_evaluation"]
    return {
        "protocol_id": payload["protocol"]["id"],
        "evidence_source": payload["protocol"]["evidence_source"],
        "candidate_genomes": payload["candidate_count"],
        "accepted_candidates": payload["accepted_candidate_count"],
        "baseline_generation": payload["baseline"]["generation"],
        "promoted_generation": payload["durable_generation"],
        "train_objective_gain": payload["train_gain"],
        "sealed_heldout_gain": payload["heldout_gain"],
        "paired_bootstrap_lcb": payload["lower_bound"],
        "heldout_quality": promoted["heldout"]["quality"],
        "heldout_safety": promoted["heldout"]["safety"],
        "heldout_efficiency": promoted["heldout"]["efficiency"],
        "heldout_operations": promoted["heldout"]["operations"],
        "winning_operator": promoted["operator"],
    }


def _check_or_update_snapshot(payload: dict, *, update_snapshot: bool) -> None:
    actual = _snapshot_summary(payload)
    if update_snapshot:
        BENCHMARK_SNAPSHOT.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    expected = json.loads(BENCHMARK_SNAPSHOT.read_text(encoding="utf-8"))
    if actual != expected:
        raise RuntimeError(
            "Harness benchmark result drifted from docs/benchmark-results.json; "
            "review the protocol change, then run "
            "`python scripts/harness_evolution_benchmark.py --update-snapshot` "
            "and update the human-readable documentation."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="write the reviewed compact benchmark summary used by documentation",
    )
    args = parser.parse_args()
    payload = run_benchmark()
    print("HARNESS-EVOLUTION-BENCHMARK")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if not payload["promoted"]:
        raise SystemExit("Harness promotion gate failed: no candidate passed sealed held-out credit.")
    if payload["heldout_gain"] < 0 or payload["lower_bound"] < 0:
        raise SystemExit("Harness promotion gate failed: held-out evidence is negative.")
    if payload["accepted_candidate_count"] < 1:
        raise SystemExit("Harness promotion gate failed: no accepted candidate is auditable.")
    if payload["protocol"]["hand_authored_evolution_evidence"]:
        raise SystemExit("Harness benchmark must derive mutation pressure from Runtime evidence.")
    _check_or_update_snapshot(payload, update_snapshot=args.update_snapshot)


if __name__ == "__main__":
    main()
