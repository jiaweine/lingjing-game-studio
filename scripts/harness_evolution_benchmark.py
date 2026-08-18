from __future__ import annotations

import json
from pathlib import Path
import tempfile

from worldforge.runtime import (
    EvolutionEvidence,
    GameEvolutionConfig,
    HarnessEvolutionEngine,
    HarnessGenomeStore,
)

BENCHMARK_PROTOCOL = "sealed-heldout-game-harness-2026-08"


def run_benchmark() -> dict:
    """Run the sealed train/held-out promotion protocol and return auditable metrics."""
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    evidence = EvolutionEvidence(
        where="uncertainty",
        why="timeout",
        feature_priorities={
            "uncertainty": 1.0,
            "urgency": 0.9,
            "threat": 0.55,
            "hp_missing": 0.35,
            "finish_window": 0.25,
        },
        summary="uncertain long-horizon trajectories are timing out before robust progress",
        prediction=(
            "evidence-aligned harness edits should improve held-out objective "
            "without safety loss"
        ),
    )
    config = GameEvolutionConfig(
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

    with tempfile.TemporaryDirectory(prefix="lingjing-harness-benchmark-") as temp_dir:
        engine = HarnessEvolutionEngine(
            config=config,
            archive_path=Path(temp_dir) / "promotion-archive.json",
        )
        result = engine.evolve(evidence, baseline=baseline)

    accepted = [candidate for candidate in result.candidates if candidate.accepted]
    promoted_candidate = next(
        (
            candidate
            for candidate in accepted
            if candidate.genome.genome_id == result.champion.genome_id
        ),
        None,
    )
    payload = result.to_dict()
    payload["protocol"] = {
        "id": BENCHMARK_PROTOCOL,
        "train_seeds": list(config.train_seeds),
        "heldout_seeds": list(config.heldout_seeds),
        "heldout_used_for_search": False,
        "promotion_requires_nonnegative_heldout_gain": True,
        "promotion_requires_nonnegative_paired_bootstrap_lcb": True,
        "promotion_requires_no_quality_regression": True,
        "promotion_requires_no_safety_regression": True,
    }
    payload["candidate_count"] = len(result.candidates)
    payload["accepted_candidate_count"] = len(accepted)
    if promoted_candidate is not None:
        payload["promoted_evaluation"] = {
            "operator": promoted_candidate.operator,
            "novelty": round(promoted_candidate.novelty, 6),
            "paired_gain": round(promoted_candidate.paired_gain, 6),
            "lower_bound": round(promoted_candidate.lower_bound, 6),
            "train": {
                "objective": round(promoted_candidate.train.objective, 6),
                "quality": round(promoted_candidate.train.quality, 6),
                "safety": round(promoted_candidate.train.safety, 6),
                "efficiency": round(promoted_candidate.train.efficiency, 6),
                "operations": round(promoted_candidate.train.operations, 3),
            },
            "heldout": {
                "objective": round(promoted_candidate.heldout.objective, 6),
                "quality": round(promoted_candidate.heldout.quality, 6),
                "safety": round(promoted_candidate.heldout.safety, 6),
                "efficiency": round(promoted_candidate.heldout.efficiency, 6),
                "operations": round(promoted_candidate.heldout.operations, 3),
            },
        }
    return payload


def main() -> None:
    payload = run_benchmark()
    print("HARNESS-EVOLUTION-BENCHMARK")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if not payload["promoted"]:
        raise SystemExit("Harness promotion gate failed: no candidate passed sealed held-out credit.")
    if payload["heldout_gain"] < 0 or payload["lower_bound"] < 0:
        raise SystemExit("Harness promotion gate failed: held-out evidence is negative.")


if __name__ == "__main__":
    main()
