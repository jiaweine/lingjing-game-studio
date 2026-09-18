import json

import pytest

from worldforge.product.engine_probe_verifier import (
    EngineProbeVerificationError,
    evaluate_probe_snapshot,
)


def _snapshot(state: str, value: float, *, duplicate: bool = False) -> bytes:
    row = {
        "probe_id": "demo.boss_shield.damage_gate",
        "observed_state": state,
        "observed_value": value,
        "note": "external observation",
        "game_object": "BossShieldFixture",
    }
    probes = [row, dict(row)] if duplicate else [row]
    return json.dumps({"active_scene": "BossShieldDemo", "probes": probes}).encode()


def test_probe_contract_classifies_bug_reproduction_without_granting_authority():
    result = evaluate_probe_snapshot(
        _snapshot("damage_applied_while_shielded", 120.0)
    )

    assert len(result) == 1
    verdict = result[0]
    assert verdict["probe_id"] == "demo.boss_shield.damage_gate"
    assert verdict["contract_id"] == "demo.boss_shield.damage_gate.v1"
    assert verdict["outcome"] == "reproduced"
    assert verdict["observed_value"] == 120.0
    assert verdict["verifier"] == "engine-probe-contract-v1"
    assert verdict["authority"] == "contract-evaluation-only"
    assert "verified" not in verdict


def test_probe_contract_classifies_fixed_observation():
    result = evaluate_probe_snapshot(
        _snapshot("damage_blocked_while_shielded", 0.0)
    )

    assert result[0]["outcome"] == "passed"
    assert "blocked" in result[0]["reason"]


def test_probe_contract_refuses_inconsistent_or_duplicate_observations():
    inconsistent = evaluate_probe_snapshot(
        _snapshot("damage_blocked_while_shielded", 12.0)
    )
    assert inconsistent[0]["outcome"] == "unknown"

    duplicate = evaluate_probe_snapshot(
        _snapshot("damage_applied_while_shielded", 120.0, duplicate=True)
    )
    assert duplicate[0]["outcome"] == "ambiguous"


def test_unknown_probe_is_not_self_authorizing():
    raw = {
        "probes": [
            {
                "probe_id": "project.claims.everything_is_fine",
                "observed_state": "passed",
                "observed_value": 0,
            }
        ]
    }
    assert evaluate_probe_snapshot(json.dumps(raw).encode()) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"probes":{}}',
    ],
)
def test_probe_verifier_rejects_invalid_snapshot_shapes(payload):
    with pytest.raises(EngineProbeVerificationError):
        evaluate_probe_snapshot(payload)
