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
    """Persistent skill artifacts whose activation is controlled by HarnessGenome gates."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {
            skill.skill_id: skill.model_copy(deep=True)
            for skill in _load_seed_skills()
        }
        self.history: list[Skill] = []

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
            if skill.status != "active":
                continue
            gate = genome.skill_gates.get(skill_id)
            out[skill_id] = gate.activation(features) if gate else 0.0
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
            if skill.status != "active":
                continue
            activation = activations.get(skill_id, 0.0)
            reliability = (
                planner.skill_base_factor
                + skill.success_rate * planner.skill_success_factor
            )
            total += skill.action_bias.get(action, 0.0) * activation * reliability
        return total

    def propose_patch(self, skill_id: str, action: str, delta: float, reason: str) -> Skill:
        before = self.skills[skill_id]
        patched = before.model_copy(deep=True)
        patched.parent_generation = before.generation
        patched.generation += 1
        patched.action_bias[action] = round(
            patched.action_bias.get(action, 0.0) + delta, 3
        )
        patched.description = before.description + f" [evolved: {reason}]"
        patched.status = "candidate"
        return patched

    def accept(self, patched: Skill) -> None:
        old = self.skills[patched.skill_id]
        self.history.append(old.model_copy(deep=True))
        patched.status = "active"
        patched.evidence_count += 1
        self.skills[patched.skill_id] = patched

    def snapshot(self) -> list[dict]:
        return [skill.model_dump() for skill in self.skills.values()]
