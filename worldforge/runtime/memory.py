from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math

from .harness_genome import HarnessGenomeStore


@dataclass
class OutcomeRecord:
    scenario: str
    state_signature: str
    action: str
    reward: float
    success: bool


class EpisodicMemory:
    """Continuous state-similarity memory without hand-authored game thresholds."""

    def __init__(self, capacity: int = 2000):
        self.records = deque(maxlen=capacity)

    def add(self, record: OutcomeRecord) -> None:
        self.records.append(record)

    def prior(self, state_signature: str, action: str) -> float:
        target = self._decode(state_signature)
        if not target:
            return 0.0
        temperature = max(
            0.05,
            min(2.0, HarnessGenomeStore.current().planner.memory_scale / 10.0),
        )
        weighted = 0.0
        mass = 0.0
        for record in self.records:
            if record.action != action:
                continue
            vector = self._decode(record.state_signature)
            if len(vector) != len(target):
                continue
            distance = math.sqrt(
                sum((left - right) ** 2 for left, right in zip(target, vector))
                / max(1, len(target))
            )
            weight = math.exp(-distance / temperature)
            weighted += record.reward * weight
            mass += weight
        return weighted / mass if mass > 1e-12 else 0.0

    @staticmethod
    def signature(state) -> str:
        vector = [
            state.player_hp / max(1.0, state.player_max_hp),
            state.enemy_hp / max(1.0, state.enemy_max_hp),
            state.energy / max(1.0, state.max_energy),
            state.gold / 100.0,
            state.attack / 40.0,
            state.armor / 20.0,
            state.enemy_attack / 40.0,
            state.enemy_variance / 20.0,
            state.threat,
            state.combo / 5.0,
            float(state.stage),
        ]
        return json.dumps(vector, separators=(",", ":"))

    @staticmethod
    def _decode(signature: str) -> list[float]:
        try:
            values = json.loads(signature)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []
        try:
            return [float(value) for value in values]
        except (TypeError, ValueError):
            return []
