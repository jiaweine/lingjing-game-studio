from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class EngineProbeVerificationError(Exception):
    pass


@dataclass(frozen=True)
class EngineProbeContract:
    probe_id: str
    contract_id: str
    title: str


_BOSS_SHIELD = EngineProbeContract(
    probe_id="demo.boss_shield.damage_gate",
    contract_id="demo.boss_shield.damage_gate.v1",
    title="Boss shield blocks incoming damage while active",
)

KNOWN_ENGINE_PROBE_CONTRACTS = {
    _BOSS_SHIELD.probe_id: _BOSS_SHIELD,
}


def _coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _evaluate_boss_shield(observation: dict[str, Any]) -> str:
    state = str(observation.get("observed_state") or "").strip()
    value = _coerce_number(observation.get("observed_value"))

    if state == "damage_applied_while_shielded" and value is not None and value > 0.01:
        return "reproduced"
    if state == "damage_blocked_while_shielded" and value is not None and abs(value) <= 0.01:
        return "passed"
    return "unknown"


def evaluate_probe_snapshot(data: bytes | str) -> list[dict[str, Any]]:
    try:
        raw = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        snapshot = json.loads(raw)
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise EngineProbeVerificationError("engine snapshot is not valid JSON") from exc

    if not isinstance(snapshot, dict):
        raise EngineProbeVerificationError("engine snapshot must be a JSON object")

    probes = snapshot.get("probes") or []
    if not isinstance(probes, list):
        raise EngineProbeVerificationError("engine snapshot probes must be a list")
    if len(probes) > 32:
        raise EngineProbeVerificationError("engine snapshot has too many probes")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in probes:
        if not isinstance(row, dict):
            continue
        probe_id = str(row.get("probe_id") or "").strip()
        if probe_id not in KNOWN_ENGINE_PROBE_CONTRACTS:
            continue
        grouped.setdefault(probe_id, []).append(row)

    evaluations: list[dict[str, Any]] = []
    for probe_id, contract in KNOWN_ENGINE_PROBE_CONTRACTS.items():
        observations = grouped.get(probe_id, [])
        if not observations:
            continue
        if len(observations) > 1:
            evaluations.append(
                {
                    "probe_id": probe_id,
                    "contract_id": contract.contract_id,
                    "title": contract.title,
                    "outcome": "ambiguous",
                    "observed_state": None,
                    "observed_value": None,
                    "verifier": "engine-probe-contract-v1",
                    "authority": "contract-evaluation-only",
                    "reason": "multiple observations reported for a single governed probe",
                }
            )
            continue

        observation = observations[0]
        outcome = _evaluate_boss_shield(observation)
        evaluations.append(
            {
                "probe_id": probe_id,
                "contract_id": contract.contract_id,
                "title": contract.title,
                "outcome": outcome,
                "observed_state": str(observation.get("observed_state") or "")[:240],
                "observed_value": _coerce_number(observation.get("observed_value")),
                "verifier": "engine-probe-contract-v1",
                "authority": "contract-evaluation-only",
                "reason": (
                    "shielded damage was observed"
                    if outcome == "reproduced"
                    else "shielded damage was blocked"
                    if outcome == "passed"
                    else "observation did not match a governed pass/fail predicate"
                ),
            }
        )
    return evaluations
