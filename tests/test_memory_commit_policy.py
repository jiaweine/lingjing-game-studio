import asyncio
import copy

from worldforge.api.manager import RunManager
from worldforge.models import RunConfig
from worldforge.runtime.memory import EpisodicMemory, OutcomeRecord


def _record(action="attack"):
    return OutcomeRecord("boss_burst", "{}", action, 1.0, True)


def test_shared_memory_suppression_keeps_run_local_memory_learning():
    shared = EpisodicMemory()
    local = copy.deepcopy(shared)

    with EpisodicMemory.suppress_commits_for(shared):
        shared.add(_record("shared"))
        local.add(_record("local"))

    assert len(shared.records) == 0
    assert len(local.records) == 1

    shared.add(_record("after"))
    assert len(shared.records) == 1


def test_implicit_non_adaptive_managed_run_does_not_mutate_shared_memory(tmp_path):
    async def exercise():
        manager = RunManager(tmp_path / "runtime")
        before = list(manager.engine.memory.records)

        session_id = await manager.start(
            RunConfig(
                scenario_id="boss_burst",
                seed=29,
                max_steps=1,
                enable_counterfactual=False,
                enable_recursive_agents=False,
            )
        )
        await manager.tasks[session_id]

        session = manager.engine.events.session_meta(session_id)
        started = next(
            event
            for event in manager.engine.events.list_events(session_id)
            if event.event_type == "run.started"
        )

        assert started.payload["config"]["enable_evolution"] is False
        assert session["meta"]["commit_shared_memory"] is False
        assert list(manager.engine.memory.records) == before

    asyncio.run(exercise())
