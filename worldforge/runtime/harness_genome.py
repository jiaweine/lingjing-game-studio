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

    weights: dict[str, float] = Field(default_factory=dict)
    threshold: float = 0.0
    temperature: float = 0.25

    def activation(self, features: dict[str, float]) -> float:
        score = sum(features.get(name, 0.0) * weight for name, weight in self.weights.items())
        temperature = max(0.05, float(self.temperature))
        x = max(-40.0, min(40.0, (score - self.threshold) / temperature))
        return 1.0 / (1.0 + math.exp(-x))


class FeatureGene(BaseModel):
    """Evolvable representation scales; no game-policy thresholds live in Python."""

    scales: dict[str, float] = Field(default_factory=lambda: {
        "gold": 50.0,
        "attack": 32.0,
        "armor": 14.0,
        "enemy_attack": 35.0,
        "enemy_variance": 15.0,
        "score_gap": 80.0,
        "combo": 5.0,
        "stage": 3.0,
    })
    gold_cap: float = 2.0
    score_clip: float = 2.5

    def scale(self, name: str) -> float:
        return max(1e-6, float(self.scales.get(name, 1.0)))


class SpecialistGene(BaseModel):
    gene_id: str
    role: str
    mode: Literal["core", "dynamic"] = "dynamic"
    gate: LinearGate = Field(default_factory=LinearGate)
    confidence: float = 0.7
    action_bias: dict[str, float] = Field(default_factory=dict)
    action_feature_weights: dict[str, dict[str, float]] = Field(default_factory=dict)
    enabled: bool = True

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
    gate: LinearGate = Field(default_factory=LinearGate)
    action_bias: dict[str, float] = Field(default_factory=dict)
    reliability: float = 1.0
    enabled: bool = True


class BeliefGene(BaseModel):
    observed_uncertainty: float = 0.12
    latent_base: float = 0.35
    variance_scale: float = 20.0
    uncertainty_cap: float = 0.90


class MemoryGene(BaseModel):
    """Evolvable retrieval kernel for continual harness memory."""

    feature_weights: dict[str, float] = Field(default_factory=lambda: {
        "hp": 1.2,
        "enemy_hp": 1.2,
        "energy": 0.8,
        "gold": 0.4,
        "attack": 0.4,
        "armor": 0.4,
        "enemy_attack": 0.8,
        "enemy_variance": 0.6,
        "threat": 1.0,
        "combo": 0.3,
        "stage": 0.3,
    })
    similarity_temperature: float = 0.7
    success_bonus: float = 0.15
    recency_decay: float = 0.002


class PlannerGene(BaseModel):
    skill_weight: float = 1.0
    skill_base_factor: float = 0.7
    skill_success_factor: float = 0.5
    memory_weight: float = 2.2
    memory_scale: float = 10.0
    policy_weight: float = 1.65
    specialist_weight: float = 1.0
    specialist_cap: float = 4.5
    repeat_penalties: dict[str, float] = Field(default_factory=lambda: {
        "farm": 4.8,
        "scout": 4.8,
        "defend": 4.8,
    })
    disagreement_cap: float = 4.0
    scout_base: float = 1.15
    scout_gain: float = 1.35
    defend_gain: float = 0.22
    commit_base: float = 0.28
    commit_threat_gain: float = 0.42
    retreat_low_gain: float = -0.12
    retreat_high_gain: float = 0.08
    retreat_threat_boundary: float = 0.70
    default_risk_friction: float = 0.08


class SearchGene(BaseModel):
    mean_weight: float = 1.0
    dispersion_penalty: float = 0.45
    downside_weight: float = 0.20
    success_bonus: float = 16.0
    width_base: float = 1.5
    width_uncertainty_gain: float = 2.0
    width_threat_gain: float = 1.2
    horizon_base: float = 1.0
    horizon_uncertainty_gain: float = 2.0
    rollout_base: float = 1.0
    rollout_uncertainty_gain: float = 2.0

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
    progress_weight: float = 24.0
    health_weight: float = 17.0
    gold_weight: float = 0.04
    threat_penalty: float = 26.0
    violation_penalty: float = 8.0
    victory_bonus: float = 70.0
    defeat_penalty: float = 90.0


class MutationPolicyGene(BaseModel):
    """Self-referential mutation policy inspired by Promptbreeder."""

    operator_logits: dict[str, float] = Field(default_factory=lambda: {
        "parameter_jitter": 0.0,
        "gate_mutation": 0.0,
        "skill_mutation": -0.1,
        "memory_mutation": -0.1,
        "specialist_split": -0.4,
        "specialist_prune": -0.8,
        "recombine": -0.5,
        "meta_mutation": -0.7,
    })
    sigma: float = 0.18
    temperature: float = 0.75
    exploration: float = 0.55


class HarnessGenome(BaseModel):
    """Complete evolvable program surface around the frozen Runtime kernel."""

    genome_id: str = Field(default_factory=lambda: f"hg-{uuid.uuid4().hex[:10]}")
    generation: int = 1
    parent_ids: list[str] = Field(default_factory=list)
    origin: str = "bootstrap"
    features: FeatureGene = Field(default_factory=FeatureGene)
    belief: BeliefGene = Field(default_factory=BeliefGene)
    memory: MemoryGene = Field(default_factory=MemoryGene)
    planner: PlannerGene = Field(default_factory=PlannerGene)
    search: SearchGene = Field(default_factory=SearchGene)
    utility: UtilityGene = Field(default_factory=UtilityGene)
    specialists: list[SpecialistGene] = Field(default_factory=list)
    skills: dict[str, SkillGene] = Field(default_factory=dict)
    # Legacy bootstrap compatibility. New genomes serialize only ``skills``.
    skill_gates: dict[str, LinearGate] = Field(default_factory=dict, exclude=True)
    mutation_policy: MutationPolicyGene = Field(default_factory=MutationPolicyGene)

    def model_post_init(self, __context) -> None:
        if not self.skills and self.skill_gates:
            self.skills = {
                skill_id: SkillGene(gate=gate)
                for skill_id, gate in self.skill_gates.items()
            }

    def card(self) -> dict:
        return {
            "genome_id": self.genome_id,
            "generation": self.generation,
            "parents": list(self.parent_ids),
            "origin": self.origin,
            "specialists": len([gene for gene in self.specialists if gene.enabled]),
            "skills": len([gene for gene in self.skills.values() if gene.enabled]),
        }


def state_features(
    state: WorldState,
    belief: BeliefState,
    goal: GoalState,
) -> dict[str, float]:
    representation = HarnessGenomeStore.current().features
    hp = state.player_hp / max(1.0, state.player_max_hp)
    enemy = state.enemy_hp / max(1.0, state.enemy_max_hp)
    remaining = max(0.0, goal.max_steps - state.tick) / max(1.0, goal.max_steps)
    score_gap = (goal.target_score - state.score) / representation.scale("score_gap")
    return {
        "bias": 1.0,
        "hp": hp,
        "hp_missing": 1.0 - hp,
        "enemy_hp": enemy,
        "finish_window": 1.0 - enemy,
        "energy": state.energy / max(1.0, state.max_energy),
        "gold": min(representation.gold_cap, state.gold / representation.scale("gold")),
        "attack": state.attack / representation.scale("attack"),
        "armor": state.armor / representation.scale("armor"),
        "enemy_attack": state.enemy_attack / representation.scale("enemy_attack"),
        "enemy_variance": state.enemy_variance / representation.scale("enemy_variance"),
        "threat": state.threat,
        "uncertainty": belief.uncertainty,
        "remaining": remaining,
        "urgency": 1.0 - remaining,
        "score_gap": max(-representation.score_clip, min(representation.score_clip, score_gap)),
        "health_gap": max(0.0, goal.min_health_ratio - hp),
        "combo": state.combo / representation.scale("combo"),
        "stage": state.stage / representation.scale("stage"),
        "observed": 1.0 if state.discovered_enemy_attack is not None else 0.0,
        "economy": 1.0 if "economy" in state.tags else 0.0,
        "exploit": 1.0 if "exploit-test" in state.tags else 0.0,
        "boss": 1.0 if "boss" in state.tags else 0.0,
        "glass_cannon": 1.0 if "glass-cannon" in state.tags else 0.0,
    }


def lightweight_features(state: WorldState, uncertainty: float) -> dict[str, float]:
    representation = HarnessGenomeStore.current().features
    hp = state.player_hp / max(1.0, state.player_max_hp)
    enemy = state.enemy_hp / max(1.0, state.enemy_max_hp)
    return {
        "bias": 1.0,
        "hp": hp,
        "hp_missing": 1.0 - hp,
        "enemy_hp": enemy,
        "finish_window": 1.0 - enemy,
        "energy": state.energy / max(1.0, state.max_energy),
        "gold": min(representation.gold_cap, state.gold / representation.scale("gold")),
        "attack": state.attack / representation.scale("attack"),
        "armor": state.armor / representation.scale("armor"),
        "enemy_attack": state.enemy_attack / representation.scale("enemy_attack"),
        "enemy_variance": state.enemy_variance / representation.scale("enemy_variance"),
        "threat": state.threat,
        "uncertainty": uncertainty,
        "combo": state.combo / representation.scale("combo"),
        "stage": state.stage / representation.scale("stage"),
        "observed": 1.0 if state.discovered_enemy_attack is not None else 0.0,
        "economy": 1.0 if "economy" in state.tags else 0.0,
        "exploit": 1.0 if "exploit-test" in state.tags else 0.0,
        "boss": 1.0 if "boss" in state.tags else 0.0,
        "glass_cannon": 1.0 if "glass-cannon" in state.tags else 0.0,
    }


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
