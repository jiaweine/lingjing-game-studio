from worldforge.envs import BalanceLabEnv,get_scenario
from worldforge.models import ActionKind,GameAction

def test_snapshot_restore_exact():
    env=BalanceLabEnv();s=env.reset(get_scenario('boss_burst'),7);snap=env.snapshot();env.step(GameAction(kind=ActionKind.ATTACK));env.restore(snap);assert env.state.model_dump()==s.model_dump()

def test_no_illegal_negative_energy():
    env=BalanceLabEnv();s=env.reset(get_scenario('glass_cannon'),3)
    for _ in range(4):
        legal=env.legal_actions(env.state);act=ActionKind(legal[0]);state,_,done,_=env.step(GameAction(kind=act));assert state.energy>=0
        if done:break
