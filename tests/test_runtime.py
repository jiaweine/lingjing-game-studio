import asyncio

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import ActionKind, RunConfig
from worldforge.runtime import (
    AdaptivePlanner,
    CounterfactualBrancher,
    EpisodicMemory,
    EventStore,
    SkillBank,
    StateVerifier,
    WorldForgeEngine,
)
from worldforge.runtime.recursive import RecursiveAgentScheduler
from worldforge.runtime.sandbox import ActionSandbox


def test_event_hash_chain(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.create_session("s")
    store.append("s", "a", {"x": 1})
    store.append("s", "b", {"x": 2})
    assert store.verify_chain("s")


def test_counterfactual_keeps_canonical_state():
    env = BalanceLabEnv()
    state = env.reset(get_scenario("boss_burst"), 7)
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    verifier = StateVerifier()
    brancher = CounterfactualBrancher(planner, verifier)
    ranked = planner.rank(state, env.legal_actions(state), get_scenario("boss_burst").goal)
    before = env.state.model_dump()
    results = brancher.evaluate(
        env,
        ranked.candidates,
        get_scenario("boss_burst").goal,
        width=3,
        horizon=2,
        rollouts=2,
    )
    assert results
    assert env.state.model_dump() == before


def test_engine_completes_and_trace_valid(tmp_path):
    engine = WorldForgeEngine(tmp_path / "wf.db")
    summary = asyncio.run(
        engine.run(
            RunConfig(scenario_id="boss_burst", seed=9, max_steps=12, rollouts_per_branch=1),
            demo_delay=0,
        )
    )
    assert summary.status == "completed"
    assert engine.events.verify_chain(summary.session_id)
    types = {e.event_type for e in engine.events.list_events(summary.session_id)}
    assert "decision.committed" in types
    assert "counterfactual.evaluated" in types
    assert "run.completed" in types


def test_persistent_snapshot_roundtrip(tmp_path):
    store = EventStore(tmp_path / "snap.db")
    store.create_session("s")
    env = BalanceLabEnv()
    env.reset(get_scenario("boss_burst"), 17)
    snap = env.snapshot()
    store.save_snapshot("s", 1, snap)
    loaded = store.get_snapshot("s", 1)
    env.step(__import__("worldforge.models", fromlist=["GameAction"]).GameAction(kind=ActionKind.ATTACK))
    env.restore(loaded)
    assert env.snapshot()["state"] == snap["state"]
    assert env.snapshot()["rng_state"] == snap["rng_state"]


def test_sandbox_history_is_reversible_and_run_scoped():
    a = ActionSandbox(max_irreversible_per_window=1)
    b = ActionSandbox(max_irreversible_per_window=1)
    state = get_scenario("boss_burst").state.model_copy(deep=True)
    legal = [ActionKind.HEAVY_ATTACK.value, ActionKind.ATTACK.value]
    a.record(ActionKind.HEAVY_ATTACK)
    assert not a.validate(ActionKind.HEAVY_ATTACK, state, legal).allowed
    assert b.validate(ActionKind.HEAVY_ATTACK, state, legal).allowed
    a.undo(ActionKind.HEAVY_ATTACK)
    assert a.validate(ActionKind.HEAVY_ATTACK, state, legal).allowed


def test_recursive_specialists_have_deterministic_ids_and_dynamic_children():
    state = get_scenario("boss_burst").state.model_copy(deep=True)
    state.player_hp = 22
    state.tags.append("economy")
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    belief = planner.make_belief(state)
    belief.uncertainty = 0.7
    scheduler = RecursiveAgentScheduler()
    first = asyncio.run(
        scheduler.analyze(state, belief, get_scenario("boss_burst").goal, session_id="same", tick=3)
    )
    second = asyncio.run(
        scheduler.analyze(state, belief, get_scenario("boss_burst").goal, session_id="same", tick=3)
    )
    assert first.node_id == second.node_id
    assert first.status == "completed"
    assert first.evidence["dynamic_specialists"] >= 2
    roles = {node.role for child in first.children for node in [child, *child.children]}
    assert {"CombatAgent", "RiskAgent", "EconomyAgent", "MechanicsProbe", "SurvivalAudit"} <= roles
    assert all(node.status == "completed" for child in first.children for node in [child, *child.children])


def test_engine_subagent_trace_is_executed_not_placeholder(tmp_path):
    engine = WorldForgeEngine(tmp_path / "agents.db")
    summary = asyncio.run(
        engine.run(
            RunConfig(
                scenario_id="boss_burst",
                seed=9,
                max_steps=2,
                rollouts_per_branch=1,
                enable_evolution=False,
            ),
            demo_delay=0,
        )
    )
    rows = [e for e in engine.events.list_events(summary.session_id) if e.event_type == "subagent.tree"]
    assert rows
    tree = rows[0].payload["tree"]
    assert tree["status"] == "completed"
    assert tree["evidence"]["specialists"] >= 2
    assert all("evidence" in child and child["elapsed_ms"] >= 0 for child in tree["children"])
