from collections import defaultdict,deque
from dataclasses import dataclass
@dataclass
class OutcomeRecord:scenario:str;state_signature:str;action:str;reward:float;success:bool
class EpisodicMemory:
 def __init__(self,capacity=2000):self.records=deque(maxlen=capacity);self.action_stats=defaultdict(list)
 def add(self,record):
  self.records.append(record);key=(record.state_signature,record.action);self.action_stats[key].append(record.reward);self.action_stats[key]=self.action_stats[key][-50:]
 def prior(self,state_signature,action):
  vals=self.action_stats.get((state_signature,action),[]);return sum(vals)/len(vals) if vals else 0.
 @staticmethod
 def signature(state):
  hp='low' if state.player_hp<35 else 'mid' if state.player_hp<70 else 'high';enemy='low' if state.enemy_hp<state.enemy_max_hp*.35 else 'mid' if state.enemy_hp<state.enemy_max_hp*.7 else 'high';energy='ready' if state.energy>=2 else 'dry';return f'hp:{hp}|enemy:{enemy}|energy:{energy}|gold:{state.gold//20}|threat:{int(state.threat*3)}'
