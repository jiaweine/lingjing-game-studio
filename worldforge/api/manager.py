from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from worldforge.models import RunConfig, RunSummary, RuntimeEvent
from worldforge.runtime import WorldForgeEngine


class RunManager:
    """Process-local live task manager backed by durable runtime events.

    Live asyncio Tasks are intentionally ephemeral. Status is reconstructed from the event store
    when the process no longer owns a task, so restart does not turn a failed/cancelled run into an
    ambiguous "stored" state.
    """

    def __init__(self, data_dir: str | Path) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = WorldForgeEngine(data_dir / "worldforge.db")
        self.tasks: dict[str, asyncio.Task] = {}
        self.summaries: dict[str, RunSummary] = {}
        self.queues = defaultdict(list)

    async def start(self, config: RunConfig) -> str:
        import uuid

        session_id = f"wf-{uuid.uuid4().hex[:10]}"

        async def sink(event: RuntimeEvent) -> None:
            for q in list(self.queues.get(session_id, [])):
                try:
                    q.put_nowait(event.model_dump())
                except asyncio.QueueFull:
                    pass

        async def execute() -> None:
            try:
                self.summaries[session_id] = await self.engine.run(
                    config, session_id=session_id, sink=sink
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ev = self.engine.events.append(
                    session_id,
                    "run.failed",
                    {"error_type": type(exc).__name__, "error": str(exc)[:800]},
                )
                await sink(ev)
                raise

        task = asyncio.create_task(execute(), name=session_id)
        self.tasks[session_id] = task
        return session_id

    def status(self, session_id: str):
        task = self.tasks.get(session_id)
        summary = self.summaries.get(session_id)
        events = self.engine.events.list_events(session_id)
        event_types = {event.event_type for event in events}

        if summary:
            status = "completed"
        elif task and task.cancelled():
            status = "cancelled"
        elif task and task.done():
            try:
                status = "failed" if task.exception() else "completed"
            except asyncio.CancelledError:
                status = "cancelled"
        elif task:
            status = "running"
        elif "run.completed" in event_types:
            status = "completed"
        elif "run.cancelled" in event_types:
            status = "cancelled"
        elif "run.failed" in event_types:
            status = "failed"
        elif events:
            status = "interrupted"
        else:
            status = "unknown"

        durable_summary = summary.model_dump() if summary else None
        if durable_summary is None:
            completed = next((e for e in reversed(events) if e.event_type == "run.completed"), None)
            if completed:
                durable_summary = completed.payload.get("summary")
        return {
            "session_id": session_id,
            "status": status,
            "summary": durable_summary,
            "event_count": len(events),
            "last_event": events[-1].model_dump() if events else None,
            "resumable": status == "interrupted" and bool(self.engine.events.get_snapshot(session_id)),
        }

    async def cancel(self, session_id: str):
        task = self.tasks.get(session_id)
        if not task:
            return self.status(session_id)
        if task.done():
            return self.status(session_id)
        task.cancel()
        ev = self.engine.events.append(session_id, "run.cancelled", {"reason": "operator_stop"})
        for q in list(self.queues.get(session_id, [])):
            try:
                q.put_nowait(ev.model_dump())
            except asyncio.QueueFull:
                pass
        return {"session_id": session_id, "status": "cancelled"}

    def subscribe(self, session_id: str):
        q = asyncio.Queue(maxsize=500)
        self.queues[session_id].append(q)
        return q

    def unsubscribe(self, session_id: str, q) -> None:
        if q in self.queues.get(session_id, []):
            self.queues[session_id].remove(q)
        if not self.queues.get(session_id):
            self.queues.pop(session_id, None)
