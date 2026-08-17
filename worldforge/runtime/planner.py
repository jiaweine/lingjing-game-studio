from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math

from worldforge.models import ActionKind, AgentVote, BeliefState


class CouncilAgent:
    name = "base"
    def score(self, action, state, belief, goal):
        return 0.0, "neutral"


class CombatAnalyst(CouncilAgent):
    name = "CombatAnalyst"
    def score(self, action, state, belief, goal):
        enemy_ratio = state.enemy_hp / max(1, state.enemy_max_hp)
        value = (
            (2.8 if state.energy >= 2 else -9)
            if action == ActionKind.HEAVY_ATTACK else
            (2.2 if state.energy >= 2 else -9)
            if action == ActionKind.CAST else
            1.7 if action == ActionKind.ATTACK else 0.0
        )
        value += 3.2 if enemy_ratio < .42 and action in (ActionKind.HEAVY_ATTACK, ActionKind.CAST) else 0.0
        return value, f"enemy_ratio={enemy_ratio:.2f}, energy={state.energy}"


class RiskAnalyst(CouncilAgent):
    name = "RiskAnalyst"
    def score(self, action, state, belief, goal):
        hp = state.player_hp / max(1, state.player_max_hp)
        value = (
            ((1-hp)*2.5 + state.threat*1.7) if action == ActionKind.DEFEND else
            ((1-hp)*7) if action == ActionKind.HEAL else
            2.8 if action == ActionKind.SCOUT and belief.uncertainty > .45 else
            -3.8 if action in (ActionKind.HEAVY_ATTACK, ActionKind.CAST) and hp < .3 else
            (2.0 if hp < .16 else -5) if action == ActionKind.RETREAT else 0.0
        )
        return value, f"hp={hp:.2f}, threat={state.threat:.2f}, uncertainty={belief.uncertainty:.2f}"


class EconomyAnalyst(CouncilAgent):
    name = "EconomyAnalyst"
    def score(self, action, state, belief, goal):
        value = (
            (2.2 if state.armor < 6 else .2) if action == ActionKind.BUY_ARMOR else
            (1.8 if state.attack < 22 else .2) if action == ActionKind.BUY_BLADE else
            (1.5 if state.gold < 22 else .4) if action == ActionKind.FARM else 0.0
        )
        value -= 1.3 if "exploit-test" in state.tags and action == ActionKind.FARM else 0.0
        return value, f"gold={state.gold}, attack={state.attack}, armor={state.armor}"


class ProgressAnalyst(CouncilAgent):
    name = "ProgressAnalyst"
    def score(self, action, state, belief, goal):
        remaining = max(1, goal.max_steps - state.tick)
        value = (
            3.4 + 5/remaining if action in (ActionKind.ATTACK, ActionKind.HEAVY_ATTACK, ActionKind.CAST)
            else -3 if action == ActionKind.FARM and remaining < 6
            else -1 if action == ActionKind.SCOUT and state.tick > 4
            else 0.0
        )
        return value, f"remaining_steps={remaining}"


@dataclass
class PlannerOutput:
    candidates: list
    votes: list
    aggregate: dict


class AdaptivePlanner:
    def __init__(self, skills, memory, policy_model=None):
        self.skills = skills
        self.memory = memory
        self.policy_model = policy_model
        self.agents = [CombatAnalyst(), RiskAnalyst(), EconomyAnalyst(), ProgressAnalyst()]

    def make_belief(self, state):
        if state.discovered_enemy_attack is not None:
            low = high = state.discovered_enemy_attack
            uncertainty = .12
            behavior = "observed"
        else:
            low = max(1, state.enemy_attack-state.enemy_variance)
            high = state.enemy_attack+state.enemy_variance
            uncertainty = min(.9, .35+state.enemy_variance/20)
            behavior = "latent"
        return BeliefState(
            enemy_attack_low=low,
            enemy_attack_high=high,
            enemy_behavior=behavior,
            uncertainty=uncertainty,
        )

    def rank(self, state, legal, goal, *, extra_bias: dict[str, float] | None = None):
        kinds = [ActionKind(value) for value in legal]
        belief = self.make_belief(state)
        votes = []
        aggregate = {action.value: 0.0 for action in kinds}
        signature = self.memory.signature(state)

        def evaluate(agent, action):
            score, reason = agent.score(action, state, belief, goal)
            return AgentVote(
                agent=agent.name, action=action, score=round(score, 4), reason=reason
            )

        with ThreadPoolExecutor(max_workers=len(self.agents)) as executor:
            futures = [
                executor.submit(evaluate, agent, action)
                for agent in self.agents for action in kinds
            ]
            for future in futures:
                vote = future.result()
                votes.append(vote)
                aggregate[vote.action.value] += vote.score

        policy_scores = (
            self.policy_model.rank(state, belief, goal, [action.value for action in kinds])
            if self.policy_model else {}
        )
        extra_bias = extra_bias or {}
        for action in kinds:
            aggregate[action.value] += (
                self.skills.bias(state, action.value, belief.uncertainty)
                + math.tanh(self.memory.prior(signature, action.value)/10)*2.2
                + policy_scores.get(action.value, 0.0)*1.65
                + extra_bias.get(action.value, 0.0)
            )
            if state.last_action == action.value and action in (
                ActionKind.FARM, ActionKind.SCOUT, ActionKind.DEFEND
            ):
                aggregate[action.value] -= 4.8

        ordered = sorted(kinds, key=lambda action: aggregate[action.value], reverse=True)
        return PlannerOutput(
            ordered, votes,
            {key: round(value, 4) for key, value in aggregate.items()},
        )
