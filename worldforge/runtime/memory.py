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
    """Continual state-similarity memory controlled by the active HarnessGenome."""

    def __init__(self, capacity: int = 2000):
        self.records = deque(maxlen=capacity)

    def add(self, record: OutcomeRecord) -> None:
        self.records.append(record)

    def prior(self, state_signature: str, action: str) -> float:
        target = self._decode(state_signature)
        if not target:
            return 0.0
        gene = HarnessGenomeStore.current().memory
        temperature = max(0.02, gene.similarity_temperature)
        weighted = 0.0
        mass = 0.0
        records = list(self.records)
        for index, record in enumerate(records):
            if record.action != action:
                continue
            vector = self._decode(record.state_signature)
            if not vector:
                continue
            common = set(target) & set(vector) & set(gene.feature_weights)
            if not common:
                continue
            weight_mass = sum(max(0.0, gene.feature_weights[name]) for name in common)
            if weight_mass <= 1e-12:
                continue
            distance = math.sqrt(
                sum(
                    max(0.0, gene.feature_weights[name])
                    * (target[name] - vector[name]) ** 2
                    for name in common
                )
                / weight_mass
            )
            age = max(0, len(records) - 1 - index)
            similarity = math.exp(-distance / temperature)
            recency = math.exp(-max(0.0, gene.recency_decay) * age)
            weight = similarity * recency
            value = record.reward + (gene.success_bonus if record.success else 0.0)
            weighted += value * weight
            mass += weight
        return weighted / mass if mass > 1e-12 else 0.0

    @staticmethod
    def signature(state) -> str:
        representation = HarnessGenomeStore.current().features
        vector = {
            "hp": state.player_hp / max(1.0, state.player_max_hp),
            "enemy_hp": state.enemy_hp / max(1.0, state.enemy_max_hp),
            "energy": state.energy / max(1.0, state.max_energy),
            "gold": state.gold / representation.scale("gold"),
            "attack": state.attack / representation.scale("attack"),
            "armor": state.armor / representation.scale("armor"),
            "enemy_attack": state.enemy_attack / representation.scale("enemy_attack"),
            "enemy_variance": state.enemy_variance / representation.scale("enemy_variance"),
            "threat": state.threat,
            "combo": state.combo / representation.scale("combo"),
            "stage": state.stage / representation.scale("stage"),
        }
        return json.dumps(vector, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _decode(signature: str) -> dict[str, float]:
        try:
            values = json.loads(signature)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(values, dict):
            return {}
        try:
            return {str(key): float(value) for key, value in values.items()}
        except (TypeError, ValueError):
            return {}
