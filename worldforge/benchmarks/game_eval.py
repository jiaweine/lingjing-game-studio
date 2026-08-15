from __future__ import annotations
import statistics
from dataclasses import dataclass
from pathlib import Path
from worldforge.envs import BalanceLabEnv,get_scenario,list_scenarios
from worldforge.models import ActionKind,BenchmarkRow,GameAction
from worldforge.runtime import AdaptivePlanner,CounterfactualBrancher,EpisodicMemory,SkillBank,StateVerifier
from worldforge.runtime.worldforge_model import WorldForgeM1
@dataclass
class EpisodeResult:
    success:bool;score:float;steps:int;invalid:int;recovery:int;decision_ops:int
def _components():
    skills=SkillBank();memory=EpisodicMemory();verifier=StateVerifier();model_path=Path(__file__).resolve().parents[2]/"models"/"worldforge_m1.json";model=WorldForgeM1.load_or_bootstrap(model_path);planner=AdaptivePlanner(skills,memory,model);return model,verifier,planner,CounterfactualBrancher(planner,verifier)
def run_episode(harness,scenario_id,seed):
    spec=get_scenario(scenario_id);env=BalanceLabEnv();state=env.reset(spec,seed);model,verifier,planner,brancher=_components();invalid=recovery=decision_ops=0
    for _ in range(spec.goal.max_steps):
        if state.terminal:break
        legal=env.legal_actions(state);before_snapshot=env.snapshot();belief=planner.make_belief(state);branches=[]
        if harness=="M1 直接策略":scores=model.rank(state,belief,spec.goal,legal);action=ActionKind(max(scores,key=scores.get));decision_ops+=len(scores)
        elif harness=="M1 + Planner":ranked=planner.rank(state,legal,spec.goal);action=ranked.candidates[0];decision_ops+=len(ranked.candidates)
        elif harness=="M1 + Verifier":ranked=planner.rank(state,legal,spec.goal);action=ranked.candidates[0];decision_ops+=len(ranked.candidates)
        elif harness=="WorldForge Harness":ranked=planner.rank(state,legal,spec.goal);branches=brancher.evaluate(env,ranked.candidates,spec.goal,width=4,horizon=3,rollouts=2);action=branches[0].first_action if branches else ranked.candidates[0];decision_ops+=len(ranked.candidates)+len(branches)*3*2
        else:raise ValueError(harness)
        before=state.model_copy(deep=True);state,_,done,info=env.step(GameAction(kind=action));invalid+=int(info.get("invalid",False));ver=verifier.verify(before,state,info,spec.goal,env.anomalies)
        if harness in {"M1 + Verifier","WorldForge Harness"} and ver.recommendation=="rollback":
            env.restore(before_snapshot);state=env.state.model_copy(deep=True);recovery+=1;ranked=planner.rank(state,env.legal_actions(state),spec.goal);alts=[b.first_action for b in branches if b.first_action!=action] if branches else [a for a in ranked.candidates if a!=action];alt=alts[0] if alts else ranked.candidates[0];state,_,done,_=env.step(GameAction(kind=alt))
        if done:break
    return EpisodeResult(state.outcome=="victory",state.score,state.tick,invalid,recovery,decision_ops)
def run_benchmark(seeds=24,scenarios=None):
    scenario_ids=scenarios or [s.scenario_id for s in list_scenarios()];rows=[]
    for harness in ["M1 直接策略","M1 + Planner","M1 + Verifier","WorldForge Harness"]:
        results=[run_episode(harness,scenario,1000+i*31+j*7) for j,scenario in enumerate(scenario_ids) for i in range(seeds)];successes=sum(r.success for r in results);failures=len(results)-successes;recoveries=sum(r.recovery for r in results);rows.append(BenchmarkRow(harness=harness,success_rate=round(successes/len(results),4),avg_score=round(statistics.mean(r.score for r in results),3),avg_steps=round(statistics.mean(r.steps for r in results),3),invalid_action_rate=round(sum(r.invalid for r in results)/max(1,sum(r.steps for r in results)),4),recovery_rate=round(recoveries/max(1,failures+recoveries),4),avg_decision_ops=round(statistics.mean(r.decision_ops for r in results),2)))
    return rows
