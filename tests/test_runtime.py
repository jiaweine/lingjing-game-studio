import asyncio
import math

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import ActionKind, GoalState, RunConfig, WorldState
from worldforge.runtime import (
    AdaptivePlanner,
    CounterfactualBrancher,
    EpisodicMemory,
    EventStore,
    GroupRelativePolicyOptimizer,
    PolicyGroup,
    RecursiveAgentScheduler,
    SkillBank,
    StateVerifier,
    WorldForgeEngine,
    WorldForgePolicy,
)


def test_event_hash_chain(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.create_session("s")
    store.append("s", "a", {"x": 1})
    store.append("s", "b", {"x": 2})
    assert store.verify_chain("s")


def test_counterfactual_keeps_canonical_state():
    env = BalanceLabEnv()
    scenario = get_scenario("boss_burst")
    state = env.reset(scenario, 7)
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    verifier = StateVerifier()
    brancher = CounterfactualBrancher(planner, verifier)
    ranked = planner.rank(
        state, env.legal_actions(state), scenario.goal
    )
    before = env.state.model_dump()
    results = brancher.evaluate(
        env,
        ranked.candidates,
        scenario.goal,
        width=3,
        horizon=2,
        rollouts=2,
    )
    assert results
    assert env.state.model_dump() == before




def test_counterfactual_rollouts_do_not_share_violations():
    from dataclasses import dataclass
    from worldforge.models import GameAction

    @dataclass
    class Verification:
        violations: list[str]

    class FakeVerifier:
        def verify(self, before, after, info, goal, anomalies):
            return Verification(["bad_rollout"] if info["bad"] else [])
        def branch_score(self, state, reward, goal, violations):
            return reward - (100 if violations else 0)

    class FakePlanner:
        def rank(self, state, legal, goal):
            raise AssertionError("horizon=1 terminal rollout should not replan")

    class FakeEnv:
        def __init__(self, bad=False):
            self.state = WorldState()
            self.bad = bad
            self.anomalies = []
        def clone(self, seed_offset=0):
            return FakeEnv(bad=(seed_offset == 0))
        def legal_actions(self, state):
            return ["attack"]
        def step(self, action: GameAction):
            next_state = self.state.model_copy(deep=True)
            next_state.tick += 1
            next_state.terminal = True
            next_state.outcome = "timeout"
            self.state = next_state
            return next_state, 1.0, True, {"bad": self.bad}

    result = CounterfactualBrancher(FakePlanner(), FakeVerifier()).evaluate(
        FakeEnv(),
        [ActionKind.ATTACK],
        GoalState(primary="test"),
        width=1,
        horizon=1,
        rollouts=2,
    )[0]
    # First rollout: 1 - 100 = -99. Second rollout: 1.
    assert result.expected_score == -49.0
    assert result.violations == ["bad_rollout"]


def test_recursive_specialists_influence_planner():
    state = WorldState(
        player_hp=20,
        player_max_hp=100,
        enemy_hp=90,
        enemy_max_hp=100,
        threat=.85,
        healing_potions=1,
    )
    goal = GoalState(primary="survive and finish")
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    scheduler = RecursiveAgentScheduler()
    belief = planner.make_belief(state)
    tree = scheduler.deliberate(state, belief, goal)
    bias = scheduler.aggregate_bias(tree)
    assert bias.get("heal", 0) > 0
    ranked = planner.rank(
        state,
        ["attack", "defend", "heal", "scout"],
        goal,
        extra_bias=bias,
    )
    assert ranked.aggregate["heal"] > planner.rank(
        state, ["attack", "defend", "heal", "scout"], goal
    ).aggregate["heal"]


def test_group_relative_optimizer_is_finite():
    policy = WorldForgePolicy()
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory(), policy)
    scenario = get_scenario("boss_burst")
    state = scenario.state.model_copy(deep=True)
    belief = planner.make_belief(state)
    group = PolicyGroup(
        state=state,
        belief=belief,
        goal=scenario.goal,
        rewards={"attack": 1.0, "defend": 2.0, "scout": 4.0},
    )
    candidate, metrics = GroupRelativePolicyOptimizer(
        epochs=3, kl_limit=.1
    ).optimize(policy, [group] * 8)
    assert metrics["groups"] == 8
    assert math.isfinite(metrics["mean_kl"])
    assert candidate.card.generation == policy.card.generation + 1
    assert all(math.isfinite(float(x)) for x in candidate.W2.ravel())


def test_engine_completes_and_trace_valid(tmp_path):
    engine = WorldForgeEngine(tmp_path / "wf.db")
    summary = asyncio.run(engine.run(
        RunConfig(
            scenario_id="boss_burst",
            seed=9,
            max_steps=12,
            rollouts_per_branch=1,
        ),
        demo_delay=0,
    ))
    assert summary.status == "completed"
    assert engine.events.verify_chain(summary.session_id)
    types = {
        event.event_type
        for event in engine.events.list_events(summary.session_id)
    }
    assert "decision.committed" in types
    assert "counterfactual.evaluated" in types
    assert "run.completed" in types
    assert "subagent.deliberation" in types


def test_persistent_snapshot_roundtrip(tmp_path):
    store = EventStore(tmp_path / "snap.db")
    store.create_session("s")
    env = BalanceLabEnv()
    env.reset(get_scenario("boss_burst"), 17)
    snapshot = env.snapshot()
    store.save_snapshot("s", 1, snapshot)
    loaded = store.get_snapshot("s", 1)
    from worldforge.models import GameAction
    env.step(GameAction(kind=ActionKind.ATTACK))
    env.restore(loaded)
    assert env.snapshot()["state"] == snapshot["state"]
    assert env.snapshot()["rng_state"] == snapshot["rng_state"]
