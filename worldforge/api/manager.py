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
        self.queues: dict[str, list[DurableReplayQueue]] = defaultdict(list)
        self.queue_size = max(1, int(queue_size))

    def _fanout(self, session_id: str, payload: dict) -> None:
        for queue in list(self.queues.get(session_id, [])):
            queue.offer(payload)

    def _track_task(self, session_id: str, task: asyncio.Task) -> None:
        self.tasks[session_id] = task

        def release(done: asyncio.Task) -> None:
            if self.tasks.get(session_id) is done:
                self.tasks.pop(session_id, None)
            try:
                done.exception()
            except asyncio.CancelledError:
                pass

        task.add_done_callback(release)

    async def start(
        self,
        config: RunConfig,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        import uuid

        session_id = f"wf-{uuid.uuid4().hex[:10]}"
        session_scope = {
            "workspace_id": workspace_id,
            "user_id": user_id,
        }
        # Persist the access-control boundary before returning the public session id.
        # The engine will enrich/replace this metadata when execution begins, but a client
        # that immediately polls or opens a WebSocket must never see a transient 404 simply
        # because the scheduled task has not received its first event-loop turn yet.
        self.engine.events.create_session(
            session_id,
            meta={
                "config": config.model_dump(),
                "lifecycle": "scheduled",
                **session_scope,
            },
        )

        async def sink(event: RuntimeEvent) -> None:
            self._fanout(session_id, event.model_dump())

        async def execute() -> None:
            try:
                await self.engine.run(
                    config,
                    session_id=session_id,
                    sink=sink,
                    session_meta=session_scope,
                )
            except Exception as exc:
                # A canonical run can already be terminal while outer post-processing (for
                # example harness evolution) is still executing. Durable terminal history is
                # authoritative: never append a second terminal event that rewrites that fact.
                snapshot = self.engine.events.status_snapshot(session_id)
                terminal = snapshot["terminal_event"]
                if terminal is None:
                    event_type = "run.failed"
                    payload = {"error": repr(exc)}
                else:
                    event_type = "run.postprocess_failed"
                    payload = {
                        "error": repr(exc),
                        "terminal_event": terminal.event_type,
                        "terminal_seq": terminal.seq,
                    }
                event = self.engine.events.append(
                    session_id,
                    event_type,
                    payload,
                )
                await sink(event)
                raise

        task = asyncio.create_task(execute(), name=session_id)
        self._track_task(session_id, task)
        return session_id

    def status(self, session_id):
        task = self.tasks.get(session_id)
        snapshot = self.engine.events.status_snapshot(session_id)
        terminal = snapshot["terminal_event"]
        summary = None
        if terminal is not None:
            if terminal.event_type == "run.completed":
                status = "completed"
                summary = terminal.payload.get("summary")
            elif terminal.event_type == "run.failed":
                status = "failed"
            else:
                status = "cancelled"
        elif task:
            status = "running"
        elif snapshot["last_event"] is not None:
            status = "stored"
        else:
            status = "unknown"
        return {
            "session_id": session_id,
            "status": status,
            "summary": summary,
            "event_count": snapshot["event_count"],
            "last_event": (
                snapshot["last_event"].model_dump()
                if snapshot["last_event"] is not None
                else None
            ),
        }

    async def cancel(self, session_id):
        # The canonical kernel can persist run.completed before the outer self-evolution task
        # finishes. Durable terminal state is authoritative: a late operator cancel must not
        # append run.cancelled after run.completed and rewrite an already-finished run's status.
        current = self.status(session_id)
        if current["status"] in {"completed", "failed", "cancelled"}:
            return current

        task = self.tasks.get(session_id)
        if not task:
            return current
        if task.done():
            return self.status(session_id)
        task.cancel()
        event = self.engine.events.append(
            session_id,
            "run.cancelled",
            {"reason": "operator_stop"},
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
