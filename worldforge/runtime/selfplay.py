from __future__ import annotations
from dataclasses import dataclass
import statistics
from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import ActionKind, GameAction
from .planner import AdaptivePlanner
from .memory import EpisodicMemory
from .skill_bank import SkillBank


@dataclass
class PopulationResult:
    profile: str
    scenario: str
    success_rate: float
    avg_score: float
    failure_signature: str


class PopulationSelfPlay:
    """Runs diverse player archetypes to expose strategy blind spots and create curricula."""
    PROFILES = {
        "aggressive": {"heavy_attack": 3.0, "cast": 2.2, "attack": 1.5, "defend": -1.0},
        "conservative": {"defend": 2.6, "heal": 2.8, "scout": 1.4, "heavy_attack": -.7},
        "economist": {"farm": 2.2, "buy_armor": 1.4, "buy_blade": 1.5},
        "explorer": {"scout": 3.2, "farm": .7, "attack": .4},
    }

    def __init__(self, skill_bank: SkillBank | None = None) -> None:
        self.skill_bank = skill_bank or SkillBank()

    def run_population(self, scenario_id: str, seeds: int = 8) -> list[PopulationResult]:
        spec=get_scenario(scenario_id); outputs=[]
        for profile,bias in self.PROFILES.items():
            scores=[]; successes=0; causes=[]
            for i in range(seeds):
                env=BalanceLabEnv(); st=env.reset(spec,700+i*19)
                planner=AdaptivePlanner(self.skill_bank,EpisodicMemory())
                for _ in range(spec.goal.max_steps):
                    ranked=planner.rank(st,env.legal_actions(st),spec.goal)
                    agg={a: ranked.aggregate[a]+bias.get(a,0) for a in ranked.aggregate}
                    choice=ActionKind(max(agg,key=agg.get))
                    st,_,done,_=env.step(GameAction(kind=choice,source=f"selfplay:{profile}"))
                    if done: break
                scores.append(st.score); successes+=int(st.outcome=='victory')
                causes.append('survival' if st.outcome=='defeat' else 'timeout' if st.outcome=='timeout' else 'success')
            common=max(set(causes),key=causes.count)
            outputs.append(PopulationResult(profile,scenario_id,successes/seeds,statistics.mean(scores),common))
        return outputs

    def curriculum(self, scenario_id: str, seeds: int = 8) -> dict:
        pop=self.run_population(scenario_id,seeds)
        hardest=min(pop,key=lambda x:x.success_rate)
        return {
            "scenario": scenario_id,
            "hardest_profile": hardest.profile,
            "failure_signature": hardest.failure_signature,
            "priority": round(1-hardest.success_rate,3),
            "population": [x.__dict__ for x in pop],
            "next_focus": f"stress {hardest.profile} trajectories with counterfactual risk and verifier coverage",
        }
