from __future__ import annotations

import asyncio

from worldforge.api.manager import RunManager
from worldforge.models import RunConfig


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


def test_run_manager_persists_access_scope_before_scheduled_task_runs(tmp_path, monkeypatch):
    async def scenario():
        manager = RunManager(tmp_path / "runs")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_run(*_args, **_kwargs):
            entered.set()
            await release.wait()

        monkeypatch.setattr(manager.engine, "run", blocked_run)
        config = RunConfig(
            scenario_id="boss_burst",
            seed=7,
            max_steps=2,
            rollouts_per_branch=1,
        )
        session_id = await manager.start(
            config,
            workspace_id="workspace-immediate",
            user_id="user-immediate",
        )

        # start() contains no scheduling yield after create_task(). The durable ACL metadata
        # must therefore exist before the new task gets its first event-loop turn.
        assert entered.is_set() is False
        meta = manager.engine.events.session_meta(session_id)
        assert meta is not None
        assert meta["meta"]["workspace_id"] == "workspace-immediate"
        assert meta["meta"]["user_id"] == "user-immediate"
        assert meta["meta"]["config"]["scenario_id"] == "boss_burst"
        assert meta["meta"]["lifecycle"] == "scheduled"
        assert manager.status(session_id)["status"] == "running"

        # Cleanup timing belongs to the dedicated task-lifecycle test above; this regression
        # only asserts that the access boundary exists before the scheduled task can run.
        task = manager.tasks[session_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

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
    manager.engine.events.append(
        cancelled_id,
        "run.cancelled",
        {"reason": "operator_stop"},
    )
    cancelled = manager.status(cancelled_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["summary"] is None

    assert manager.tasks == {}


def test_run_manager_status_never_loads_complete_event_history(tmp_path, monkeypatch):
    manager = RunManager(tmp_path / "runs")
    session_id = "wf-bounded-status"
    store = manager.engine.events

    store.append(session_id, "run.started", {})
    for index in range(64):
        store.append(session_id, "world.state", {"tick": index})
    store.append(
        session_id,
        "run.completed",
        {"summary": {"status": "completed", "score": 99.0}},
    )

    def forbid_history_scan(*_args, **_kwargs):
        raise AssertionError("status polling must not call list_events")

    monkeypatch.setattr(store, "list_events", forbid_history_scan)

    status = manager.status(session_id)
    assert status["status"] == "completed"
    assert status["summary"] == {"status": "completed", "score": 99.0}
    assert status["event_count"] == 66
    assert status["last_event"]["seq"] == 66
    assert status["last_event"]["event_type"] == "run.completed"


def test_run_manager_status_reports_stored_without_terminal_event(tmp_path, monkeypatch):
    manager = RunManager(tmp_path / "runs")
    session_id = "wf-stored"
    store = manager.engine.events
    store.append(session_id, "run.started", {})
    store.append(session_id, "world.state", {"tick": 1})

    monkeypatch.setattr(
        store,
        "list_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("status polling must not call list_events")
        ),
    )

    status = manager.status(session_id)
    assert status["status"] == "stored"
    assert status["summary"] is None
    assert status["event_count"] == 2
    assert status["last_event"]["event_type"] == "world.state"
