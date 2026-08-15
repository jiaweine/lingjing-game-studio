import asyncio
from worldforge.envs import BalanceLabEnv,get_scenario
from worldforge.models import RunConfig
from worldforge.runtime import AdaptivePlanner,CounterfactualBrancher,EpisodicMemory,EventStore,SkillBank,StateVerifier,WorldForgeEngine

def test_event_hash_chain(tmp_path):
    store=EventStore(tmp_path/'events.db');store.create_session('s');store.append('s','a',{'x':1});store.append('s','b',{'x':2});assert store.verify_chain('s')

def test_counterfactual_keeps_canonical_state():
    env=BalanceLabEnv();state=env.reset(get_scenario('boss_burst'),7);planner=AdaptivePlanner(SkillBank(),EpisodicMemory());verifier=StateVerifier();b=CounterfactualBrancher(planner,verifier);ranked=planner.rank(state,env.legal_actions(state),get_scenario('boss_burst').goal);before=env.state.model_dump();results=b.evaluate(env,ranked.candidates,get_scenario('boss_burst').goal,width=3,horizon=2,rollouts=2);assert results;assert env.state.model_dump()==before

def test_engine_completes_and_trace_valid(tmp_path):
    engine=WorldForgeEngine(tmp_path/'wf.db');summary=asyncio.run(engine.run(RunConfig(scenario_id='boss_burst',seed=9,max_steps=12,rollouts_per_branch=1),demo_delay=0));assert summary.status=='completed';assert engine.events.verify_chain(summary.session_id);types={e.event_type for e in engine.events.list_events(summary.session_id)};assert 'decision.committed' in types and 'counterfactual.evaluated' in types and 'run.completed' in types

def test_persistent_snapshot_roundtrip(tmp_path):
    store=EventStore(tmp_path/'snap.db');store.create_session('s');env=BalanceLabEnv();env.reset(get_scenario('boss_burst'),17);snap=env.snapshot();store.save_snapshot('s',1,snap);loaded=store.get_snapshot('s',1);env.step(__import__('worldforge.models',fromlist=['GameAction']).GameAction(kind=__import__('worldforge.models',fromlist=['ActionKind']).ActionKind.ATTACK));env.restore(loaded);assert env.snapshot()['state']==snap['state'];assert env.snapshot()['rng_state']==snap['rng_state']
