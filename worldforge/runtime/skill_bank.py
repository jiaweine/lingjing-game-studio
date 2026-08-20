from __future__ import annotations

from pathlib import Path
import json

from worldforge.models import BeliefState, GoalState, Skill, WorldState

from .harness_genome import HarnessGenomeStore, lightweight_features, state_features


def _load_seed_skills() -> list[Skill]:
    path = Path(__file__).with_name("default_skills.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Skill.model_validate(item) for item in data]


class SkillBank:
    """Skill metadata/evidence store; executable behavior lives in HarnessGenome."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {
            skill.skill_id: skill.model_copy(deep=True)
            for skill in _load_seed_skills()
        }

    @staticmethod
    def _features(
        state: WorldState,
        uncertainty: float,
        goal: GoalState | None,
    ) -> dict[str, float]:
        if goal is None:
            return lightweight_features(state, uncertainty)
        belief = BeliefState(
            enemy_attack_low=max(1, state.enemy_attack - state.enemy_variance),
            enemy_attack_high=state.enemy_attack + state.enemy_variance,
            uncertainty=uncertainty,
        )
        return state_features(state, belief, goal)

    def activations(
        self,
        state: WorldState,
        uncertainty: float = .5,
        *,
        goal: GoalState | None = None,
    ) -> dict[str, float]:
        genome = HarnessGenomeStore.current()
        features = self._features(state, uncertainty, goal)
        out: dict[str, float] = {}
        for skill_id, skill in self.skills.items():
            gene = genome.skills.get(skill_id)
            if skill.status != "active" or gene is None or not gene.enabled:
                continue
            out[skill_id] = gene.gate.activation(features)
        return out

    def active_for(
        self,
        state: WorldState,
        uncertainty: float = .5,
        *,
        goal: GoalState | None = None,
    ) -> list[Skill]:
        activations = self.activations(state, uncertainty, goal=goal)
        return [
            skill
            for skill_id, skill in self.skills.items()
            if skill.status == "active" and activations.get(skill_id, 0.0) >= .5
        ]

    def bias(
        self,
        state: WorldState,
        action: str,
        uncertainty: float = .5,
        *,
        goal: GoalState | None = None,
    ) -> float:
        genome = HarnessGenomeStore.current()
        planner = genome.planner
        activations = self.activations(state, uncertainty, goal=goal)
        total = 0.0
        for skill_id, skill in self.skills.items():
            gene = genome.skills.get(skill_id)
            if skill.status != "active" or gene is None or not gene.enabled:
                continue
            activation = activations.get(skill_id, 0.0)
            evidence_reliability = (
                planner.skill_base_factor
                + skill.success_rate * planner.skill_success_factor
            )
            total += (
                gene.action_bias.get(action, 0.0)
                * activation
                * gene.reliability
                * evidence_reliability
            )
        return total

    def snapshot(self) -> list[dict]:
        genome = HarnessGenomeStore.current()
        return [
            {
                **skill.model_dump(),
                "harness_gene": (
                    genome.skills[skill.skill_id].model_dump()
                    if skill.skill_id in genome.skills
                    else None
                ),
            }
            for skill in self.skills.values()
        ]
