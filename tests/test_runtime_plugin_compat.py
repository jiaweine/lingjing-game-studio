from worldforge.runtime import WorldForgeEngine


def test_legacy_failure_evolver_alias_points_to_canonical_harness_evolution(tmp_path):
    engine = WorldForgeEngine(tmp_path / "worldforge.db")
    described = {row["name"]: row for row in engine.plugins.describe()}

    assert "harness-evolution" in described
    assert "failure-evolver" in described
    assert described["failure-evolver"]["metadata"] == {
        "compatibility_alias": True,
        "deprecated": True,
        "alias_for": "harness-evolution",
        "heldout_gate": True,
    }
    assert engine.plugins.get("failure-evolver") is engine.plugins.get(
        "harness-evolution"
    )
