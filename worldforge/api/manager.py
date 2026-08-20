from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from worldforge.envs import get_scenario
from worldforge.models import RunConfig, RuntimeEvent
from worldforge.runtime import WorldForgeEngine
from worldforge.runtime.harness_genome import HarnessGenomeStore
from worldforge.runtime.provenance import build_runtime_provenance
from worldforge.runtime.run_report import build_run_report


class RunManager:
    def __init__(self, data_dir: str | Path) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = WorldForgeEngine(data_dir / "worldforge.db")
        self.tasks: dict[str, asyncio.Task] = {}
        self.summaries = {}
        self.queues = defaultdict(list)

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
                session_meta = {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                }
                scenario = get_scenario(config.scenario_id)
                session_meta["provenance"] = build_runtime_provenance(
                    kernel=self.engine,
                    policy=self.engine.policy_model,
                    harness_genome=HarnessGenomeStore.current(),
                    skill_bank=self.engine.skills,
                    memory=self.engine.memory,
                    verifier=self.engine.verifier,
                    scenario=scenario.model_dump(),
                    config=config.model_dump(),
                    session_meta=session_meta,
                )
                self.summaries[session_id] = await self.engine.run(
                    config,
                    session_id=session_id,
                    sink=sink,
                    session_meta=session_meta,
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
        session = self.engine.events.session_meta(session_id)
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
            "provenance": (session or {}).get("meta", {}).get("provenance"),
        }

    def report(self, session_id):
        events = self.engine.events.list_events(session_id)
        return build_run_report(
            session_id,
            events,
            policy_fallback=self.engine.policy_model.card_dict(),
            hash_chain_valid=lambda: self.engine.events.verify_chain(session_id),
        )

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
        for queue in list(self.queues.get(session_id, [])):
            try:
                queue.put_nowait(event.model_dump())
            except asyncio.QueueFull:
                pass
        return {"session_id": session_id, "status": "cancelled"}

    def subscribe(self, session_id):
        queue = asyncio.Queue(maxsize=500)
        self.queues[session_id].append(queue)
        return queue

    def unsubscribe(self, session_id, queue):
        if queue in self.queues.get(session_id, []):
            self.queues[session_id].remove(queue)
