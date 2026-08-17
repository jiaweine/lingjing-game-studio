from __future__ import annotations

from worldforge.models import Skill, WorldState


DEFAULT_SKILLS = [
    Skill(
        skill_id="survival_guard",
        name="Survival Guard",
        description="Prefer defense or healing when the next damage window can terminate the run.",
        trigger="hp_low_or_threat_high",
        action_bias={"defend": 2.4, "heal": 4.2, "scout": .8},
        success_rate=.72,
    ),
    Skill(
        skill_id="burst_window",
        name="Burst Window",
        description="Spend energy when the enemy is in a finishable range.",
        trigger="enemy_low_energy_ready",
        action_bias={"heavy_attack": 4.0, "cast": 3.4, "attack": 1.2},
        success_rate=.69,
    ),
    Skill(
        skill_id="information_first",
        name="Information First",
        description="Probe early under high uncertainty before irreversible commitments.",
        trigger="uncertainty_high",
        action_bias={"scout": 4.2, "defend": .8},
        success_rate=.66,
    ),
    Skill(
        skill_id="economy_guard",
        name="Economy Guard",
        description="Avoid over-spending and suspicious reward-loop behavior.",
        trigger="economy_or_exploit",
        action_bias={"farm": .4, "buy_armor": 1.7, "buy_blade": 1.0, "attack": .7},
        success_rate=.61,
    ),
]


class SkillBank:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {
            skill.skill_id: skill.model_copy(deep=True) for skill in DEFAULT_SKILLS
        }
        self.history: list[Skill] = []

    def active_for(self, state: WorldState, uncertainty: float = .5) -> list[Skill]:
        out: list[Skill] = []
        for skill in self.skills.values():
            if skill.status != "active":
                continue
            if skill.skill_id == "survival_guard" and (
                state.player_hp < 45 or state.threat > .62
            ):
                out.append(skill)
            elif skill.skill_id == "burst_window" and (
                state.enemy_hp < state.enemy_max_hp * .48 and state.energy >= 2
            ):
                out.append(skill)
            elif skill.skill_id == "information_first" and (
                uncertainty > .48 and state.discovered_enemy_attack is None
            ):
                out.append(skill)
            elif skill.skill_id == "economy_guard" and any(
                tag in state.tags for tag in ("economy", "exploit-test")
            ):
                out.append(skill)
        return out

    def bias(self, state: WorldState, action: str, uncertainty: float = .5) -> float:
        return sum(
            skill.action_bias.get(action, 0.0) * (.7 + skill.success_rate * .5)
            for skill in self.active_for(state, uncertainty)
        )

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
