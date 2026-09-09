from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from worldforge.models import RunConfig, RuntimeEvent
from worldforge.replay_queue import DurableReplayQueue
from worldforge.runtime import WorldForgeEngine


class RunManager:
    def __init__(self, data_dir: str | Path, *, queue_size: int = 500) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = WorldForgeEngine(data_dir / "worldforge.db")
        self.tasks: dict[str, asyncio.Task] = {}
        self.summaries = {}
        self.queues: dict[str, list[DurableReplayQueue]] = defaultdict(list)
        self.queue_size = max(1, int(queue_size))

    def _fanout(self, session_id: str, payload: dict) -> None:
        for queue in list(self.queues.get(session_id, [])):
            queue.offer(payload)

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
            self._fanout(session_id, event.model_dump())

        async def execute() -> None:
            try:
                self.summaries[session_id] = await self.engine.run(
                    config,
                    session_id=session_id,
                    sink=sink,
                    session_meta={
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                    },
                )
            except Exception as exc:
                event = self.engine.events.append(
                    session_id, "run.failed", {"error": repr(exc)}
                )
                await sink(event)
                raise

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
            status = (
                "completed"
                if any(event.event_type == "run.completed" for event in events)
                else "stored"
            )
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
            return {"session_id": session_id, "status": "unknown"}
        if task.done():
            return self.status(session_id)
        task.cancel()
        event = self.engine.events.append(
            session_id, "run.cancelled", {"reason": "operator_stop"}
        )
        self._fanout(session_id, event.model_dump())
        return {"session_id": session_id, "status": "cancelled"}

    def subscribe(self, session_id):
        initial_cursor = self.engine.events.latest_seq(session_id)

        def replay_next(after_seq: int):
            event = self.engine.events.next_event(session_id, after_seq)
            return event.model_dump() if event is not None else None

        queue = DurableReplayQueue(
            maxsize=self.queue_size,
            initial_cursor=initial_cursor,
            replay_next=replay_next,
            sequence_of=lambda payload: int(payload.get("seq") or 0),
        )
        self.queues[session_id].append(queue)
        return queue

    def unsubscribe(self, session_id, queue):
        if queue in self.queues.get(session_id, []):
            self.queues[session_id].remove(queue)
        if not self.queues.get(session_id):
            self.queues.pop(session_id, None)
