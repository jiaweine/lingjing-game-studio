from __future__ import annotations
import argparse, json, statistics, uuid
from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.integrations.external_harness import ExternalHarnessClient
from worldforge.models import GameAction


def run(endpoint:str, scenario_id:str, seeds:int):
    client=ExternalHarnessClient(endpoint); spec=get_scenario(scenario_id); rows=[]
    for i in range(seeds):
        env=BalanceLabEnv(); state=env.reset(spec,5000+i*37); invalid=0
        sid=f"ext-{uuid.uuid4().hex[:8]}"
        for step in range(spec.goal.max_steps):
            if state.terminal: break
            legal=env.legal_actions(state)
            action=client.act(session_id=sid,state=state,legal=legal,goal=spec.goal,step=step)
            state,_,done,info=env.step(GameAction(kind=action,source='external-harness'))
            invalid += int(info.get('invalid',False))
            if done: break
        rows.append({"success":state.outcome=='victory',"score":state.score,"steps":state.tick,"invalid":invalid})
    return {"scenario":scenario_id,"seeds":seeds,"success_rate":sum(x['success'] for x in rows)/seeds,"avg_score":statistics.mean(x['score'] for x in rows),"avg_steps":statistics.mean(x['steps'] for x in rows),"invalid_actions":sum(x['invalid'] for x in rows)}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--endpoint',required=True); p.add_argument('--scenario',default='boss_burst'); p.add_argument('--seeds',type=int,default=20)
    a=p.parse_args(); print(json.dumps(run(a.endpoint,a.scenario,a.seeds),indent=2))
