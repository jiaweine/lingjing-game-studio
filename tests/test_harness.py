import asyncio

from worldforge.harness import ExecutionBudget, MissionExecutor, MissionSpec, MissionStatus
from worldforge.runtime import WorldForgeEngine


def test_mission_plan_is_deterministic_and_risk_driven(tmp_path):
    engine = WorldForgeEngine(tmp_path / "mission.db")
    executor = MissionExecutor(engine)
    spec = MissionSpec(
        goal="复现版本回归里 Boss 第二阶段的异常，并验证结果",
        scene="regression",
        budget=ExecutionBudget(max_agents=6, max_tool_calls=12, max_parallelism=2),
    )
    first = executor.plan(spec, "mission-fixed")
    second = executor.plan(spec, "mission-fixed")
    assert first == second
    assert first.risk in {"high", "critical"}
    assert first.steps[0].role == "WorldObserver"
    assert first.steps[-1].role == "VerifierAgent"
    assert len([s for s in first.steps if s.tool == "game.simulate"]) >= 2


def test_mission_executes_tools_and_verifies_child_sessions(tmp_path):
    engine = WorldForgeEngine(tmp_path / "mission-run.db")
    executor = MissionExecutor(engine)
    spec = MissionSpec(
        goal="检查 boss 战的异常路径",
        scenario_id="boss_burst",
        budget=ExecutionBudget(
            max_agents=4,
            max_tool_calls=8,
            max_parallelism=2,
            rollout_horizon=1,
            rollouts_per_branch=1,
        ),
    )
    result = asyncio.run(executor.run(spec, mission_id="mission-run"))
    assert result.status == MissionStatus.COMPLETED
    assert result.child_sessions
    assert result.verification["event_chain_valid"]
    assert result.verification["child_sessions_verified"]
    event_types = {e.event_type for e in engine.events.list_events("mission-run")}
    assert {"mission.started", "mission.planned", "tool.requested", "tool.completed", "mission.completed"} <= event_types


def test_mission_resume_reuses_completed_tool_calls(tmp_path):
    engine = WorldForgeEngine(tmp_path / "resume.db")
    executor = MissionExecutor(engine)
    spec = MissionSpec(
        goal="做一次 Boss 路径验证",
        scenario_id="boss_burst",
        budget=ExecutionBudget(
            max_agents=3,
            max_tool_calls=6,
            max_parallelism=1,
            rollout_horizon=1,
            rollouts_per_branch=1,
        ),
    )
    first = asyncio.run(executor.run(spec, mission_id="mission-resume"))
    before = len(engine.events.list_events("mission-resume"))
    second = asyncio.run(executor.run(spec, mission_id="mission-resume"))
    after = len(engine.events.list_events("mission-resume"))
    assert second == first
    assert after == before
