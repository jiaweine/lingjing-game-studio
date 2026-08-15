from __future__ import annotations
import copy,random
from worldforge.models import ActionKind,GameAction,GoalState,ScenarioSpec,WorldState
from .base import GameEnvironment
SCENARIOS={
'boss_burst':ScenarioSpec(scenario_id='boss_burst',name='Boss 爆发窗口测试',description='隐藏爆发阈值与资源管理测试',difficulty='高难',state=WorldState(enemy_hp=135,enemy_max_hp=135,enemy_attack=18,enemy_variance=8,threat=.62,gold=24),goal=GoalState(primary='识别爆发机制并击败 Boss',max_steps=18),hidden={'burst_every':4,'burst_bonus':12}),
'economy_trap':ScenarioSpec(scenario_id='economy_trap',name='经济系统陷阱测试',description='跨阶段经济规划与数值平衡',difficulty='中等',state=WorldState(player_hp=92,enemy_hp=125,enemy_max_hp=125,gold=38,enemy_attack=15),goal=GoalState(primary='保持正向经济并完成战斗',max_steps=20),hidden={'inflation_after':6}),
'glass_cannon':ScenarioSpec(scenario_id='glass_cannon',name='玻璃大炮极端 Build',description='高输出低容错风险测试',difficulty='高难',state=WorldState(player_hp=68,player_max_hp=80,enemy_hp=118,enemy_max_hp=118,attack=25,armor=1,enemy_variance=10),goal=GoalState(primary='控制灾难风险并击败敌人',max_steps=16,risk_tolerance=.3),hidden={'crit_chance':.23}),
'loot_exploit':ScenarioSpec(scenario_id='loot_exploit',name='奖励循环漏洞回归',description='高收益刷取循环与异常奖励测试',difficulty='专家',state=WorldState(enemy_hp=145,enemy_max_hp=145,gold=8),goal=GoalState(primary='完成战斗并识别奖励循环异常',max_steps=22),hidden={'exploit_threshold':4})}
class BalanceLabEnv(GameEnvironment):
 def __init__(self):self.state=WorldState();self.scenario_id='boss_burst';self.hidden={};self.farm_count=0;self.anomaly_flags=[];self._rng=random.Random(0);self.defended=False
 def reset(self,scenario,seed):self.state=scenario.state.model_copy(deep=True);self.state.tick=0;self.state.terminal=False;self.state.outcome=None;self.state.score=0.;self.scenario_id=scenario.scenario_id;self.hidden=copy.deepcopy(scenario.hidden);self.farm_count=0;self.anomaly_flags=[];self._rng=random.Random(seed);self.seed=seed;return self.state.model_copy(deep=True)
 def _price(self,item):return 26 if item=='blade' else 22
 def legal_actions(self,s):
  if s.terminal:return []
  a=['attack','defend','scout','farm']
  if s.energy>=2:a+=['heavy_attack','cast']
  if s.healing_potions and s.player_hp<s.player_max_hp:a+=['heal']
  if s.gold>=26:a+=['buy_blade']
  if s.gold>=22:a+=['buy_armor']
  if s.player_hp<s.player_max_hp*.3:a+=['retreat']
  return a
 def _enemy_damage(self):
  raw=self.state.enemy_attack+self._rng.randint(-self.state.enemy_variance,self.state.enemy_variance)
  if self.scenario_id=='boss_burst' and (self.state.tick+1)%self.hidden.get('burst_every',99)==0:raw+=self.hidden.get('burst_bonus',0)
  return max(0,raw-self.state.armor*(2 if self.defended else 1))
 def step(self,action):
  s=self.state
  if s.terminal:return s.model_copy(deep=True),0.,True,{'invalid':True,'reason':'terminal'}
  if action.kind.value not in self.legal_actions(s):s.score-=7;return s.model_copy(deep=True),-7.,False,{'invalid':True,'reason':'illegal_action'}
  s.tick+=1;s.last_action=action.kind.value;r=-.35;info={'invalid':False,'damage_dealt':0,'damage_taken':0,'events':[]};self.defended=False
  if action.kind==ActionKind.ATTACK:d=max(1,s.attack+self._rng.randint(-3,4));s.enemy_hp-=d;s.energy=min(s.max_energy,s.energy+1);r+=d*.45;info['damage_dealt']=d
  elif action.kind==ActionKind.HEAVY_ATTACK:d=max(1,int(s.attack*1.55)+self._rng.randint(-4,5));s.enemy_hp-=d;s.energy-=2;r+=d*.5;info['damage_dealt']=d
  elif action.kind==ActionKind.CAST:d=int(s.attack*1.25)+8;s.enemy_hp-=d;s.energy-=2;r+=d*.47;info['damage_dealt']=d
  elif action.kind==ActionKind.DEFEND:self.defended=True;s.energy=min(s.max_energy,s.energy+1);r+=2.4
  elif action.kind==ActionKind.HEAL:before=s.player_hp;s.player_hp=min(s.player_max_hp,s.player_hp+32);s.healing_potions-=1;r+=(s.player_hp-before)*.22
  elif action.kind==ActionKind.SCOUT:s.discovered_enemy_attack=s.enemy_attack;s.threat=max(.05,s.threat-.12);s.energy=min(s.max_energy,s.energy+1);r+=3
  elif action.kind==ActionKind.FARM:self.farm_count+=1;gold=11+self._rng.randint(0,5);s.gold+=gold;r+=gold*.18;if_exploit=self.scenario_id=='loot_exploit' and self.farm_count>=self.hidden.get('exploit_threshold',999);self.anomaly_flags+=['reward_loop'] if if_exploit and 'reward_loop' not in self.anomaly_flags else []
  elif action.kind==ActionKind.BUY_BLADE:s.gold-=26;s.attack+=6;r+=3.5
  elif action.kind==ActionKind.BUY_ARMOR:s.gold-=22;s.armor+=3;r+=4
  elif action.kind==ActionKind.RETREAT:s.terminal=True;s.outcome='retreated';r-=18
  if not s.terminal and s.enemy_hp>0:d=self._enemy_damage();s.player_hp-=d;info['damage_taken']=d;r-=d*.36
  if s.enemy_hp<=0:s.terminal=True;s.outcome='victory';r+=55+max(0,s.player_hp)*.25+s.gold*.08
  elif s.player_hp<=0:s.player_hp=0;s.terminal=True;s.outcome='defeat';r-=45
  s.score+=r;s.threat=min(1.,max(0.,.25+s.enemy_attack/42-s.armor/28+(.15 if s.player_hp<35 else 0)));return s.model_copy(deep=True),r,s.terminal,info
 def snapshot(self):return {'state':self.state.model_dump(),'scenario_id':self.scenario_id,'hidden':copy.deepcopy(self.hidden),'farm_count':self.farm_count,'anomaly_flags':list(self.anomaly_flags),'seed':getattr(self,'seed',0),'rng_state':self._rng.getstate()}
 def restore(self,x):self.state=WorldState.model_validate(copy.deepcopy(x['state']));self.scenario_id=x['scenario_id'];self.hidden=copy.deepcopy(x['hidden']);self.farm_count=x['farm_count'];self.anomaly_flags=list(x['anomaly_flags']);self.seed=x['seed'];self._rng=random.Random();self._rng.setstate(x['rng_state'])
 def clone(self,seed_offset=0):c=BalanceLabEnv();c.restore(self.snapshot());c._rng.seed(self.seed*1009+self.state.tick*97+seed_offset) if seed_offset else None;return c
 @property
 def anomalies(self):return list(self.anomaly_flags)
def list_scenarios():return [v.model_copy(deep=True) for v in SCENARIOS.values()]
def get_scenario(scenario_id):
 if scenario_id not in SCENARIOS:raise KeyError(f'unknown scenario: {scenario_id}')
 return SCENARIOS[scenario_id].model_copy(deep=True)
