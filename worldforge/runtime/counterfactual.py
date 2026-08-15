from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import statistics,uuid
from worldforge.models import ActionKind,BranchResult,GameAction
class CounterfactualBrancher:
 def __init__(self,planner,verifier):self.planner=planner;self.verifier=verifier
 def evaluate(self,env,candidates,goal,width=4,horizon=3,rollouts=3):
  selected=candidates[:width]
  def run_action(action,branch_idx):
   scores=[];survivals=[];successes=[];final=None;sample_actions=[];violations_union=set();outcome=None;terminal=False
   for rollout in range(rollouts):
    sim=env.clone(seed_offset=0 if rollout==0 else branch_idx*101+rollout);total=0.;rollout_actions=[];act=action
    for _ in range(horizon):
     before=sim.state.model_copy(deep=True);after,reward,terminal,info=sim.step(GameAction(kind=act,rationale='counterfactual'));verification=self.verifier.verify(before,after,info,goal,getattr(sim,'anomalies',[]));total+=reward;rollout_actions.append(act.value);violations_union.update(verification.violations);final=after;outcome=after.outcome
     if terminal:break
     act=self.planner.rank(after,sim.legal_actions(after),goal).candidates[0]
    score=self.verifier.branch_score(final,total,goal,list(violations_union));scores.append(score);survivals.append(max(0.,final.player_hp/max(1,final.player_max_hp)));successes.append(1. if final.outcome=='victory' else 0.)
    if not sample_actions:sample_actions=rollout_actions
   mean=statistics.mean(scores);down=min(scores);disp=statistics.pstdev(scores) if len(scores)>1 else 0.;sp=statistics.mean(successes);risk=mean-.45*disp+.2*down+16*sp
   return BranchResult(branch_id=f'b-{uuid.uuid4().hex[:8]}',first_action=action,rollout_actions=sample_actions,score=round(risk,4),expected_score=round(mean,4),downside_score=round(down,4),success_probability=round(sp,4),survival=round(statistics.mean(survivals),4),terminal=terminal,outcome=outcome,violations=sorted(violations_union),final_state=final)
  with ThreadPoolExecutor(max_workers=max(1,len(selected))) as ex:results=[f.result() for f in [ex.submit(run_action,a,i) for i,a in enumerate(selected)]]
  return sorted(results,key=lambda x:x.score,reverse=True)
