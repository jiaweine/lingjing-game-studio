from __future__ import annotations

import copy
import uuid
from worldforge.models import Skill, WorldState


DEFAULT_SKILLS = [
    Skill(skill_id="survival_guard", name="Survival Guard", description="Prefer defense/heal when the next damage window can kill the player.",
          trigger="hp_low_or_threat_high", action_bias={"defend": 2.4, "heal": 4.2, "scout": 0.8}, success_rate=.72),
    Skill(skill_id="burst_window", name="Burst Window", description="Spend energy when the enemy is in a finishable range.",
          trigger="enemy_low_energy_ready", action_bias={"heavy_attack": 4.0, "cast": 3.4, "attack": 1.2}, success_rate=.69),
    Skill(skill_id="information_first", name="Information First", description="Scout early under high uncertainty before irreversible actions.",
          trigger="uncertainty_high", action_bias={"scout": 4.2, "defend": 0.8}, success_rate=.66),
    Skill(skill_id="economy_guard", name="Economy Guard", description="Avoid over-spending and detect reward-loop behavior in economy tests.",
          trigger="economy_or_exploit", action_bias={"farm": 0.4, "buy_armor": 1.7, "buy_blade": 1.0, "attack": 0.7}, success_rate=.61),
]


class SkillBank:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {s.skill_id: s.model_copy(deep=True) for s in DEFAULT_SKILLS}
        self.history: list[Skill] = []

    def active_for(self, state: WorldState, uncertainty: float = .5) -> list[Skill]:
        out: list[Skill] = []
        for s in self.skills.values():
            if s.status != "active":
                continue
            if s.skill_id == "survival_guard" and (state.player_hp < 45 or state.threat > .62): out.append(s)
            elif s.skill_id == "burst_window" and state.enemy_hp < state.enemy_max_hp * .48 and state.energy >= 2: out.append(s)
            elif s.skill_id == "information_first" and uncertainty > .48 and state.discovered_enemy_attack is None: out.append(s)
            elif s.skill_id == "economy_guard" and any(t in state.tags for t in ["economy", "exploit-test"]): out.append(s)
        return out

    def bias(self, state: WorldState, action: str, uncertainty: float = .5) -> float:
        total = 0.0
        for skill in self.active_for(state, uncertainty):
            total += skill.action_bias.get(action, 0.0) * (0.7 + skill.success_rate * .5)
        return total

    def propose_patch(self, skill_id: str, action: str, delta: float, reason: str) -> Skill:
        before = self.skills[skill_id]
        patched = before.model_copy(deep=True)
        patched.parent_version = before.version
        patched.version += 1
        patched.action_bias[action] = round(patched.action_bias.get(action, 0.0) + delta, 3)
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
        return [s.model_dump() for s in self.skills.values()]
