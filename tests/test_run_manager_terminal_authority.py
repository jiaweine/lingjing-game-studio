from __future__ import annotations

import asyncio

from worldforge.api.manager import RunManager
from worldforge.models import RunConfig


def test_postprocess_exception_after_completed_keeps_completed_terminal(tmp_path, monkeypatch):
    async def scenario():
        manager = RunManager(tmp_path / "runs")

        async def completed_then_boom(_config, *, session_id, **_kwargs):
            manager.engine.events.append(
                session_id,
                "run.completed",
                {"summary": {"status": "completed", "score": 42.0}},
            )
            raise RuntimeError("postprocess exploded")

        monkeypatch.setattr(manager.engine, "run", completed_then_boom)
        session_id = await manager.start(
            RunConfig(
                scenario_id="boss_burst",
                seed=7,
                max_steps=2,
                rollouts_per_branch=1,
            )
        )
        task = manager.tasks[session_id]
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], RuntimeError)

        status = manager.status(session_id)
        assert status["status"] == "completed"
        assert status["summary"] == {"status": "completed", "score": 42.0}

        events = manager.engine.events.list_events(session_id)
        event_types = [event.event_type for event in events]
        assert event_types.count("run.completed") == 1
        assert "run.failed" not in event_types
        assert event_types[-1] == "run.postprocess_failed"
        assert events[-1].payload["terminal_event"] == "run.completed"
        assert events[-1].payload["terminal_seq"] == 1

    asyncio.run(scenario())


def test_exception_before_terminal_still_marks_run_failed(tmp_path, monkeypatch):
    async def scenario():
        manager = RunManager(tmp_path / "runs")

        async def boom_before_terminal(*_args, **_kwargs):
            raise RuntimeError("kernel exploded")

        monkeypatch.setattr(manager.engine, "run", boom_before_terminal)
        session_id = await manager.start(
            RunConfig(
                scenario_id="boss_burst",
                seed=7,
                max_steps=2,
                rollouts_per_branch=1,
            )
        )
        task = manager.tasks[session_id]
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], RuntimeError)

        status = manager.status(session_id)
        assert status["status"] == "failed"
        events = manager.engine.events.list_events(session_id)
        assert [event.event_type for event in events] == ["run.failed"]
        assert "kernel exploded" in events[0].payload["error"]

    asyncio.run(scenario())
