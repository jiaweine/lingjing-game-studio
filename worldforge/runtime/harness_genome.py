from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import RLock
import json
import math
import os
from typing import Literal
import uuid

from pydantic import BaseModel, Field

from worldforge.models import BeliefState, GoalState, WorldState


class LinearGate(BaseModel):
    """Smooth evolvable activation gate over normalized world features."""

    weights: dict[str, float]
    threshold: float
    temperature: float

    def activation(self, features: dict[str, float]) -> float:
        score = sum(features.get(name, 0.0) * weight for name, weight in self.weights.items())
        # Numerical guardrails belong to the frozen interpreter, not to task policy.
        temperature = max(1e-6, float(self.temperature))
        x = max(-40.0, min(40.0, (score - self.threshold) / temperature))
        return 1.0 / (1.0 + math.exp(-x))


class FeatureGene(BaseModel):
    """Evolvable representation scales. Bootstrap values live in JSON, never Python."""

    scales: dict[str, float]
    value_caps: dict[str, float]
    value_clips: dict[str, float]

    def scale(self, name: str) -> float:
        return max(1e-6, float(self.scales.get(name, 1.0)))

    def cap(self, name: str, value: float) -> float:
        cap = self.value_caps.get(name)
        return min(cap, value) if cap is not None else value

    def clip(self, name: str, value: float) -> float:
        limit = self.value_clips.get(name)
        if limit is None:
            return value
        return max(-limit, min(limit, value))


class SpecialistGene(BaseModel):
    gene_id: str
    role: str
    mode: Literal["core", "dynamic"]
    gate: LinearGate
    confidence: float
    action_bias: dict[str, float]
    action_feature_weights: dict[str, dict[str, float]]
    enabled: bool

    def activation(self, features: dict[str, float]) -> float:
        return self.gate.activation(features) if self.enabled else 0.0

    def raw_score(self, action: str, features: dict[str, float]) -> float:
        return self.action_bias.get(action, 0.0) + sum(
            features.get(name, 0.0) * weight
            for name, weight in self.action_feature_weights.get(action, {}).items()
        )

    def score(self, action: str, features: dict[str, float]) -> float:
        return self.raw_score(action, features) * self.activation(features) * self.confidence


class SkillGene(BaseModel):
    gate: LinearGate
    action_bias: dict[str, float]
    reliability: float
    enabled: bool


class BeliefGene(BaseModel):
    observed_uncertainty: float
    latent_base: float
    variance_scale: float
    uncertainty_cap: float


class MemoryGene(BaseModel):
    """Evolvable similarity kernel for continual harness memory."""

    feature_weights: dict[str, float]
    similarity_temperature: float
    success_bonus: float
    recency_decay: float


class PlannerGene(BaseModel):
    skill_weight: float
    skill_base_factor: float
    skill_success_factor: float
    memory_weight: float
    memory_scale: float
    policy_weight: float
    specialist_weight: float
    specialist_cap: float
    repeat_penalties: dict[str, float]
    disagreement_cap: float
    epistemic_base: dict[str, float]
    epistemic_tension: dict[str, float]
    epistemic_threat_tension: dict[str, float]


class SearchGene(BaseModel):
    mean_weight: float
    dispersion_penalty: float
    downside_weight: float
    success_bonus: float
    width_base: float
    width_uncertainty_gain: float
    width_threat_gain: float
    horizon_base: float
    horizon_uncertainty_gain: float
    rollout_base: float
    rollout_uncertainty_gain: float

    def allocate(
        self,
        *,
        uncertainty: float,
        threat: float,
        width_cap: int,
        horizon_cap: int,
        rollout_cap: int,
    ) -> tuple[int, int, int]:
        width = round(
            self.width_base
            + self.width_uncertainty_gain * uncertainty
            + self.width_threat_gain * threat
        )
        horizon = round(self.horizon_base + self.horizon_uncertainty_gain * uncertainty)
        rollouts = round(self.rollout_base + self.rollout_uncertainty_gain * uncertainty)
        return (
            max(1, min(width_cap, width)),
            max(1, min(horizon_cap, horizon)),
            max(1, min(rollout_cap, rollouts)),
        )


class UtilityGene(BaseModel):
    progress_weight: float
    health_weight: float
    gold_weight: float
    threat_penalty: float
    violation_penalty: float
    victory_bonus: float
    defeat_penalty: float


class MutationPolicyGene(BaseModel):
    """Self-referential mutation policy inspired by Promptbreeder."""

    operator_logits: dict[str, float]
    sigma: float
    temperature: float
    exploration: float


class HarnessGenome(BaseModel):
    """Complete evolvable program surface around the frozen Runtime kernel."""

    genome_id: str = Field(default_factory=lambda: f"hg-{uuid.uuid4().hex[:10]}")
    generation: int
    parent_ids: list[str]
    origin: str
    features: FeatureGene
    belief: BeliefGene
    memory: MemoryGene
    planner: PlannerGene
    search: SearchGene
    utility: UtilityGene
    specialists: list[SpecialistGene]
    skills: dict[str, SkillGene]
    mutation_policy: MutationPolicyGene

    def card(self) -> dict:
        return {
            "genome_id": self.genome_id,
            "generation": self.generation,
            "parents": list(self.parent_ids),
            "origin": self.origin,
            "specialists": len([gene for gene in self.specialists if gene.enabled]),
            "skills": len([gene for gene in self.skills.values() if gene.enabled]),
        }


def _tag_features(state: WorldState) -> dict[str, float]:
    return {f"tag:{tag}": 1.0 for tag in state.tags}


def state_features(
    state: WorldState,
    belief: BeliefState,
    goal: GoalState,
) -> dict[str, float]:
    representation = HarnessGenomeStore.current().features
    hp = state.player_hp / max(1.0, state.player_max_hp)
    enemy = state.enemy_hp / max(1.0, state.enemy_max_hp)
    remaining = max(0.0, goal.max_steps - state.tick) / max(1.0, goal.max_steps)
    features = {
        "bias": 1.0,
        "hp": hp,
        "hp_missing": 1.0 - hp,
        "enemy_hp": enemy,
        "finish_window": 1.0 - enemy,
        "energy": state.energy / max(1.0, state.max_energy),
        "gold": representation.cap("gold", state.gold / representation.scale("gold")),
        "attack": state.attack / representation.scale("attack"),
        "armor": state.armor / representation.scale("armor"),
        "enemy_attack": state.enemy_attack / representation.scale("enemy_attack"),
        "enemy_variance": state.enemy_variance / representation.scale("enemy_variance"),
        "threat": state.threat,
        "uncertainty": belief.uncertainty,
        "remaining": remaining,
        "urgency": 1.0 - remaining,
        "score_gap": representation.clip(
            "score_gap",
            (goal.target_score - state.score) / representation.scale("score_gap"),
        ),
        "health_gap": max(0.0, goal.min_health_ratio - hp),
        "combo": state.combo / representation.scale("combo"),
        "stage": state.stage / representation.scale("stage"),
        "observed": 1.0 if state.discovered_enemy_attack is not None else 0.0,
    }
    features.update(_tag_features(state))
    return features


def lightweight_features(state: WorldState, uncertainty: float) -> dict[str, float]:
    representation = HarnessGenomeStore.current().features
    hp = state.player_hp / max(1.0, state.player_max_hp)
    enemy = state.enemy_hp / max(1.0, state.enemy_max_hp)
    features = {
        "bias": 1.0,
        "hp": hp,
        "hp_missing": 1.0 - hp,
        "enemy_hp": enemy,
        "finish_window": 1.0 - enemy,
        "energy": state.energy / max(1.0, state.max_energy),
        "gold": representation.cap("gold", state.gold / representation.scale("gold")),
        "attack": state.attack / representation.scale("attack"),
        "armor": state.armor / representation.scale("armor"),
        "enemy_attack": state.enemy_attack / representation.scale("enemy_attack"),
        "enemy_variance": state.enemy_variance / representation.scale("enemy_variance"),
        "threat": state.threat,
        "uncertainty": uncertainty,
        "combo": state.combo / representation.scale("combo"),
        "stage": state.stage / representation.scale("stage"),
        "observed": 1.0 if state.discovered_enemy_attack is not None else 0.0,
    }
    features.update(_tag_features(state))
    return features


class HarnessGenomeStore:
    """Frozen-kernel owner of the active evolvable harness genome."""

    _lock = RLock()
    _active: HarnessGenome | None = None
    _path: Path | None = None
    _override: ContextVar[HarnessGenome | None] = ContextVar(
        "worldforge_harness_override", default=None
    )

    @classmethod
    def configure(cls, path: str | Path | None) -> None:
        with cls._lock:
            cls._path = Path(path) if path else None
            cls._active = None

    @classmethod
    def _bootstrap(cls) -> HarnessGenome:
        path = Path(__file__).with_name("default_harness_genome.json")
        return HarnessGenome.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def current(cls) -> HarnessGenome:
        override = cls._override.get()
        if override is not None:
            return override
        with cls._lock:
            if cls._active is None:
                configured = cls._path
                if configured and configured.exists():
                    try:
                        cls._active = HarnessGenome.model_validate_json(
                            configured.read_text(encoding="utf-8")
                        )
                    except (ValueError, json.JSONDecodeError):
                        cls._active = cls._bootstrap()
                else:
                    cls._active = cls._bootstrap()
            return cls._active

    @classmethod
    def promote(cls, genome: HarnessGenome) -> None:
        with cls._lock:
            cls._active = genome.model_copy(deep=True)
            if cls._path:
                cls._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cls._path.with_suffix(cls._path.suffix + ".tmp")
                tmp.write_text(genome.model_dump_json(indent=2), encoding="utf-8")
                os.replace(tmp, cls._path)

    @classmethod
    @contextmanager
    def use(cls, genome: HarnessGenome):
        token = cls._override.set(genome)
        try:
            yield genome
        finally:
            cls._override.reset(token)
