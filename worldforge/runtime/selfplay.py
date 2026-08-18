from __future__ import annotations

from dataclasses import dataclass
import statistics

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import ActionKind, GameAction

from .harness_genome import HarnessGenomeStore, state_features
from .memory import EpisodicMemory
from .planner import AdaptivePlanner
from .skill_bank import SkillBank


@dataclass
class PopulationResult:
    profile: str
    scenario: str
    success_rate: float
    avg_score: float
    failure_signature: str


class PopulationSelfPlay:
    """Derives stress populations from the current evolvable specialist topology."""

    def __init__(self, skill_bank: SkillBank | None = None) -> None:
        self.skill_bank = skill_bank or SkillBank()

    def _profiles(self, scenario_id: str) -> dict[str, dict[str, float]]:
        spec = get_scenario(scenario_id)
        planner = AdaptivePlanner(self.skill_bank, EpisodicMemory())
        belief = planner.make_belief(spec.state)
        features = state_features(spec.state, belief, spec.goal)
        profiles: dict[str, dict[str, float]] = {}
        for gene in HarnessGenomeStore.current().specialists:
            if not gene.enabled:
                continue
            actions = set(gene.action_bias) | set(gene.action_feature_weights)
            profiles[gene.gene_id] = {
                action: gene.score(action, features)
                for action in actions
            }
        return profiles

    def run_population(self, scenario_id: str, seeds: int = 8) -> list[PopulationResult]:
        spec = get_scenario(scenario_id)
        outputs: list[PopulationResult] = []
        profiles = self._profiles(scenario_id)
        for profile, bias in profiles.items():
            scores: list[float] = []
            successes = 0
            causes: list[str] = []
            for index in range(seeds):
                env = BalanceLabEnv()
                state = env.reset(spec, 700 + index * 19)
                planner = AdaptivePlanner(self.skill_bank, EpisodicMemory())
                for _ in range(spec.goal.max_steps):
                    ranked = planner.rank(
                        state,
                        env.legal_actions(state),
                        spec.goal,
                    )
                    aggregate = {
                        action: ranked.aggregate[action] + bias.get(action, 0.0)
                        for action in ranked.aggregate
                    }
                    choice = ActionKind(max(aggregate, key=aggregate.get))
                    state, _, done, _ = env.step(
                        GameAction(kind=choice, source=f"selfplay:{profile}")
                    )
                    if done:
                        break
                scores.append(state.score)
                successes += int(state.outcome == "victory")
                causes.append(state.outcome or "timeout")
            common = max(set(causes), key=causes.count)
            outputs.append(
                PopulationResult(
                    profile,
                    scenario_id,
                    successes / max(1, seeds),
                    statistics.mean(scores) if scores else 0.0,
                    common,
                )
            )
        return outputs

    def curriculum(self, scenario_id: str, seeds: int = 8) -> dict:
        population = self.run_population(scenario_id, seeds)
        if not population:
            return {
                "scenario": scenario_id,
                "hardest_profile": None,
                "failure_signature": "no-active-specialists",
                "priority": 0.0,
                "population": [],
                "next_focus": "evolve specialist topology",
            }
        hardest = min(population, key=lambda item: item.success_rate)
        return {
            "scenario": scenario_id,
            "hardest_profile": hardest.profile,
            "failure_signature": hardest.failure_signature,
            "priority": round(1.0 - hardest.success_rate, 3),
            "population": [item.__dict__ for item in population],
            "next_focus": (
                f"stress genome specialist {hardest.profile} and feed its failures "
                "into semantic-QD harness evolution"
            ),
        }
