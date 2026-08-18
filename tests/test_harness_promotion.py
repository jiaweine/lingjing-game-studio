import json

from worldforge.runtime import (
    EvolutionEvidence,
    GameEvolutionConfig,
    HarnessEvolutionEngine,
    HarnessGenomeStore,
)


def test_bootstrap_harness_promotes_on_independent_heldout(tmp_path):
    """End-to-end proof that the harness can improve itself, not only emit candidates.

    Candidate generation and refinement use train seeds only. Held-out seeds are evaluated
    after the search path is frozen and are used solely by the independent promotion gate.
    """
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
        prediction="evidence-aligned harness edits should improve held-out objective without safety loss",
    )
    engine = HarnessEvolutionEngine(
        config=GameEvolutionConfig(
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
        ),
        archive_path=tmp_path / "promotion-archive.json",
    )
    result = engine.evolve(evidence, baseline=baseline)
    payload = result.to_dict()
    print("HARNESS-PROMOTION", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    assert result.promoted, json.dumps(payload, ensure_ascii=False, indent=2)
    assert result.champion.generation > baseline.generation, payload
    assert result.heldout_gain >= 0.0, payload
    assert result.lower_bound >= 0.0, payload
    assert HarnessGenomeStore.current().genome_id == result.champion.genome_id
    assert (tmp_path / "promotion-archive.json").exists()
