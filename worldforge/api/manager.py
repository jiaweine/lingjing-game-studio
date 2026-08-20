from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path

from worldforge.models import RunConfig, RuntimeEvent
from worldforge.runtime import WorldForgeEngine


class RunManager:
    def __init__(self, data_dir: str | Path, *, summary_limit: int = 256) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = WorldForgeEngine(data_dir / "worldforge.db")
        self.tasks: dict[str, asyncio.Task] = {}
        self.summaries: OrderedDict[str, object] = OrderedDict()
        self.queues: dict[str, list[asyncio.Queue]] = {}
        self.summary_limit = max(1, int(summary_limit))

    async def start(
        self,
        config: RunConfig,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        import uuid
        session_id = f"wf-{uuid.uuid4().hex[:10]}"

        async def sink(event: RuntimeEvent) -> None:
            for queue in list(self.queues.get(session_id, [])):
                try:
                    queue.put_nowait(event.model_dump())
                except asyncio.QueueFull:
                    # Runtime events are durable; a reconnect replays anything a slow
                    # client could not consume from this bounded live queue.
                    pass

        async def execute() -> None:
            try:
                summary = await self.engine.run(
                    config,
                    session_id=session_id,
                    sink=sink,
                    session_meta={
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                    },
                )
                self.summaries[session_id] = summary
                self.summaries.move_to_end(session_id)
                while len(self.summaries) > self.summary_limit:
                    self.summaries.popitem(last=False)
            except Exception as exc:
                event = self.engine.events.append(
                    session_id, "run.failed", {"error": repr(exc)}
                )
                await sink(event)
                raise
            finally:
                # Durable events are the source of truth after a run finishes. Keeping
                # every completed asyncio.Task forever creates linear process growth.
                self.tasks.pop(session_id, None)

        self.tasks[session_id] = asyncio.create_task(
            execute(), name=session_id
        )
        return session_id

    def status(self, session_id):
        task = self.tasks.get(session_id)
        summary = self.summaries.get(session_id)
        events = self.engine.events.list_events(session_id)
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
        elif events:
            event_types = {event.event_type for event in events}
            if "run.completed" in event_types:
                status = "completed"
            elif "run.failed" in event_types:
                status = "failed"
            elif "run.cancelled" in event_types:
                status = "cancelled"
            else:
                status = "stored"
        else:
            status = "unknown"
        return {
            "session_id": session_id,
            "status": status,
            "summary": summary.model_dump() if summary else None,
            "event_count": len(events),
            "last_event": events[-1].model_dump() if events else None,
        }

    async def cancel(self, session_id):
        task = self.tasks.get(session_id)
        if not task:
            # Finished tasks are intentionally reclaimed from memory. Fall back to the
            # durable event state rather than regressing completed runs to "unknown".
            return self.status(session_id)
        if task.done():
            return self.status(session_id)
        task.cancel()
        event = self.engine.events.append(
            session_id, "run.cancelled", {"reason": "operator_stop"}
        )
        for queue in list(self.queues.get(session_id, [])):
            try:
                queue.put_nowait(event.model_dump())
            except asyncio.QueueFull:
                pass
        return {"session_id": session_id, "status": "cancelled"}

    def subscribe(self, session_id):
        queue = asyncio.Queue(maxsize=500)
        self.queues.setdefault(session_id, []).append(queue)
        return queue

    def unsubscribe(self, session_id, queue):
        queues = self.queues.get(session_id)
        if not queues:
            return
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self.queues.pop(session_id, None)
