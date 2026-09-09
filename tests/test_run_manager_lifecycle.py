from __future__ import annotations

import asyncio

from worldforge.api.manager import RunManager


def test_run_manager_releases_completed_and_failed_tasks(tmp_path):
    async def scenario():
        manager = RunManager(tmp_path / "runs")

        completed = asyncio.create_task(asyncio.sleep(0))
        manager._track_task("completed-task", completed)
        await completed
        await asyncio.sleep(0)
        assert "completed-task" not in manager.tasks

        async def boom():
            raise RuntimeError("expected test failure")

        failed = asyncio.create_task(boom())
        manager._track_task("failed-task", failed)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert failed.done()
        assert isinstance(failed.exception(), RuntimeError)
        assert "failed-task" not in manager.tasks

    asyncio.run(scenario())


def test_run_manager_status_is_reconstructed_from_durable_terminal_events(tmp_path):
    manager = RunManager(tmp_path / "runs")

    completed_id = "wf-completed"
    manager.engine.events.append(completed_id, "run.started", {})
    manager.engine.events.append(
        completed_id,
        "run.completed",
        {"summary": {"status": "completed", "score": 12.5}},
    )
    completed = manager.status(completed_id)
    assert completed["status"] == "completed"
    assert completed["summary"] == {"status": "completed", "score": 12.5}

    failed_id = "wf-failed"
    manager.engine.events.append(failed_id, "run.started", {})
    manager.engine.events.append(failed_id, "run.failed", {"error": "boom"})
    failed = manager.status(failed_id)
    assert failed["status"] == "failed"
    assert failed["summary"] is None

    cancelled_id = "wf-cancelled"
    manager.engine.events.append(cancelled_id, "run.started", {})
    manager.engine.events.append(cancelled_id, "run.cancelled", {"reason": "operator_stop"})
    cancelled = manager.status(cancelled_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["summary"] is None

    assert manager.tasks == {}
