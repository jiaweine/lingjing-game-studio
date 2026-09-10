from __future__ import annotations

import asyncio
import sqlite3
import threading

from worldforge.api.manager import RunManager
from worldforge.models import RunConfig
from worldforge.runtime import EventStore


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
    manager.engine.events.create_session(completed_id)
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
    manager.engine.events.create_session(failed_id)
    manager.engine.events.append(failed_id, "run.started", {})
    manager.engine.events.append(failed_id, "run.failed", {"error": "boom"})
    failed = manager.status(failed_id)
    assert failed["status"] == "failed"
    assert failed["summary"] is None

    cancelled_id = "wf-cancelled"
    manager.engine.events.create_session(cancelled_id)
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


def test_late_cancel_cannot_overwrite_persisted_completed_status(tmp_path):
    async def scenario():
        manager = RunManager(tmp_path / "runs")
        session_id = "wf-completed-before-wrapper-finished"
        manager.engine.events.create_session(session_id)
        manager.engine.events.append(session_id, "run.started", {})
        manager.engine.events.append(
            session_id,
            "run.completed",
            {"summary": {"status": "completed", "score": 42.0}},
        )

        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        manager._track_task(session_id, task)

        result = await manager.cancel(session_id)
        assert result["status"] == "completed"
        assert result["summary"] == {"status": "completed", "score": 42.0}
        assert task.cancelled() is False
        assert task.done() is False
        assert [
            event.event_type
            for event in manager.engine.events.list_events(session_id)
        ] == ["run.started", "run.completed"]

        release.set()
        await task
        await asyncio.sleep(0)
        assert session_id not in manager.tasks

    asyncio.run(scenario())


def test_nonterminal_cancel_persists_single_cancel_event(tmp_path):
    async def scenario():
        manager = RunManager(tmp_path / "runs")
        session_id = "wf-active-cancel"
        manager.engine.events.create_session(session_id)
        manager.engine.events.append(session_id, "run.started", {})

        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        manager._track_task(session_id, task)

        result = await manager.cancel(session_id)
        assert result["status"] == "cancelled"
        await asyncio.gather(task, return_exceptions=True)
        events = manager.engine.events.list_events(session_id)
        assert [event.event_type for event in events] == ["run.started", "run.cancelled"]
        assert manager.status(session_id)["status"] == "cancelled"

    asyncio.run(scenario())


def test_run_manager_status_never_loads_complete_event_history(tmp_path, monkeypatch):
    manager = RunManager(tmp_path / "runs")
    session_id = "wf-bounded-status"
    store = manager.engine.events
    store.create_session(session_id)

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
    store.create_session(session_id)
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


def test_event_store_status_snapshot_uses_one_read_view(tmp_path, monkeypatch):
    path = tmp_path / "status-snapshot.db"
    store = EventStore(path)
    writer = EventStore(path)
    session_id = "status-snapshot"
    store.create_session(session_id)
    store.append(session_id, "run.started", {})

    first_query_seen = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []
    original_conn = store._conn

    class CoordinatedConnection:
        def __init__(self, raw: sqlite3.Connection) -> None:
            self.raw = raw
            self.first_query = True

        def __enter__(self):
            self.raw.__enter__()
            return self

        def __exit__(self, *args):
            return self.raw.__exit__(*args)

        def execute(self, sql, params=()):
            cursor = self.raw.execute(sql, params)
            if self.first_query and sql.startswith(
                "SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1"
            ):
                self.first_query = False
                first_query_seen.set()
                if not writer_done.wait(timeout=2):
                    raise AssertionError("concurrent writer did not finish")
            return cursor

    def coordinated_conn():
        return CoordinatedConnection(original_conn())

    monkeypatch.setattr(store, "_conn", coordinated_conn)

    def append_terminal() -> None:
        if not first_query_seen.wait(timeout=2):
            writer_errors.append(AssertionError("status snapshot did not reach first query"))
            writer_done.set()
            return
        try:
            writer.append(session_id, "run.completed", {"summary": {"status": "completed"}})
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_done.set()

    thread = threading.Thread(target=append_terminal)
    thread.start()
    snapshot = store.status_snapshot(session_id)
    thread.join(timeout=2)

    assert writer_errors == []
    assert snapshot["event_count"] == 1
    assert snapshot["last_event"] is not None
    assert snapshot["last_event"].event_type == "run.started"
    assert snapshot["terminal_event"] is None
