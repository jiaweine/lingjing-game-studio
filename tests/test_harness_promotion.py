from worldforge.runtime import (
    EvolutionConfig,
    EvolutionEvidence,
    HarnessEvolutionEngine,
    HarnessGenomeStore,
)


def test_bootstrap_harness_finds_a_safe_next_generation(tmp_path):
    """End-to-end proof that the harness can improve itself, not merely mutate."""
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    evidence = EvolutionEvidence(
        where="uncertainty",
        why="timeout",
        feature_priorities={
            "uncertainty": 1.0,
            "urgency": 0.9,
            "threat": 0.6,
            "finish_window": 0.4,
            "hp_missing": 0.2,
        },
        summary="held-out game-R&D stress signal",
        prediction="improve held-out objective without verifier or cost regression",
    )
    engine = HarnessEvolutionEngine(
        config=EvolutionConfig(
            population=12,
            train_seeds=(11, 23),
            heldout_seeds=(37, 51),
            eval_width_cap=2,
            eval_horizon_cap=2,
            eval_rollout_cap=1,
            bootstrap_samples=128,
            seed=20260818,
        ),
        archive_path=tmp_path / "promotion-archive.json",
    )
    result = engine.evolve(evidence, baseline=baseline)
    print("HARNESS-PROMOTION", result.to_dict())
    assert result.promoted, result.to_dict()
    assert result.champion.generation == baseline.generation + 1
    assert result.heldout_gain >= -engine.config.heldout_tolerance
    assert result.lower_bound >= -engine.config.heldout_tolerance
