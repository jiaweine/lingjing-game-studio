from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
from worldforge.models import ActionKind,AgentVote,BeliefState
class CouncilAgent:
 name='base'
 def score(self,action,state,belief,goal):return 0.,'neutral'
class CombatAnalyst(CouncilAgent):
 name='CombatAnalyst'
 def score(self,a,s,b,g):
  er=s.enemy_hp/max(1,s.enemy_max_hp);v=(2.8 if s.energy>=2 else -9) if a==ActionKind.HEAVY_ATTACK else (2.2 if s.energy>=2 else -9) if a==ActionKind.CAST else 1.7 if a==ActionKind.ATTACK else 0.;v+=3.2 if er<.42 and a in (ActionKind.HEAVY_ATTACK,ActionKind.CAST) else 0.;return v,f'enemy_ratio={er:.2f}, energy={s.energy}'
class RiskAnalyst(CouncilAgent):
 name='RiskAnalyst'
 def score(self,a,s,b,g):
  hp=s.player_hp/max(1,s.player_max_hp);v=((1-hp)*2.5+s.threat*1.7) if a==ActionKind.DEFEND else ((1-hp)*7) if a==ActionKind.HEAL else 2.8 if a==ActionKind.SCOUT and b.uncertainty>.45 else -3.8 if a in (ActionKind.HEAVY_ATTACK,ActionKind.CAST) and hp<.3 else (2. if hp<.16 else -5) if a==ActionKind.RETREAT else 0.;return v,f'hp={hp:.2f}, threat={s.threat:.2f}, uncertainty={b.uncertainty:.2f}'
class EconomyAnalyst(CouncilAgent):
 name='EconomyAnalyst'
 def score(self,a,s,b,g):
  v=(2.2 if s.armor<6 else .2) if a==ActionKind.BUY_ARMOR else (1.8 if s.attack<22 else .2) if a==ActionKind.BUY_BLADE else (1.5 if s.gold<22 else .4) if a==ActionKind.FARM else 0.;v-=1.3 if 'exploit-test' in s.tags and a==ActionKind.FARM else 0.;return v,f'gold={s.gold}, attack={s.attack}, armor={s.armor}'
class ProgressAnalyst(CouncilAgent):
 name='ProgressAnalyst'
 def score(self,a,s,b,g):
  rem=max(1,g.max_steps-s.tick);v=3.4+5/rem if a in (ActionKind.ATTACK,ActionKind.HEAVY_ATTACK,ActionKind.CAST) else -3 if a==ActionKind.FARM and rem<6 else -1 if a==ActionKind.SCOUT and s.tick>4 else 0.;return v,f'remaining_steps={rem}'
@dataclass
class PlannerOutput:candidates:list;votes:list;aggregate:dict
class AdaptivePlanner:
 def __init__(self,skills,memory,policy_model=None):self.skills=skills;self.memory=memory;self.policy_model=policy_model;self.agents=[CombatAnalyst(),RiskAnalyst(),EconomyAnalyst(),ProgressAnalyst()]
 def make_belief(self,state):
  if state.discovered_enemy_attack is not None:low=high=state.discovered_enemy_attack;u=.12;behavior='observed'
  else:low=max(1,state.enemy_attack-state.enemy_variance);high=state.enemy_attack+state.enemy_variance;u=min(.9,.35+state.enemy_variance/20);behavior='latent'
  return BeliefState(enemy_attack_low=low,enemy_attack_high=high,enemy_behavior=behavior,uncertainty=u)
 def rank(self,state,legal,goal):
  kinds=[ActionKind(x) for x in legal];belief=self.make_belief(state);votes=[];agg={a.value:0. for a in kinds};sig=self.memory.signature(state)
  def ev(agent,a):
   s,r=agent.score(a,state,belief,goal);return AgentVote(agent=agent.name,action=a,score=round(s,4),reason=r)
  with ThreadPoolExecutor(max_workers=len(self.agents)) as ex:
   for f in [ex.submit(ev,agent,a) for agent in self.agents for a in kinds]:
    v=f.result();votes.append(v);agg[v.action.value]+=v.score
  ms=self.policy_model.rank(state,belief,goal,[a.value for a in kinds]) if self.policy_model else {}
  for a in kinds:
   agg[a.value]+=self.skills.bias(state,a.value,belief.uncertainty)+math.tanh(self.memory.prior(sig,a.value)/10)*2.2+ms.get(a.value,0.)*1.65
   if state.last_action==a.value and a in (ActionKind.FARM,ActionKind.SCOUT,ActionKind.DEFEND):agg[a.value]-=4.8
  ordered=sorted(kinds,key=lambda a:agg[a.value],reverse=True);return PlannerOutput(ordered,votes,{k:round(v,4) for k,v in agg.items()})
