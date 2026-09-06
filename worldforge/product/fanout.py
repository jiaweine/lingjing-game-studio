from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from sqlalchemy import func, select

from worldforge.context.memory_ingestion import MemoryIngestionConsumer

logger = logging.getLogger("worldforge.product.fanout")


class TaskEventFanoutHub:
    """One durable event cursor shared by all conversation WebSocket clients.

    ``message.accepted`` is also the durable memory-ingestion outbox. The hub already owns
    the always-on task-event cursor, so it opportunistically drains that independent consumer
    without tying proposal extraction to the analysis-job lifecycle.
    """

    def __init__(
        self,
        store,
        *,
        poll_interval: float = .12,
        batch_size: int = 1000,
        queue_size: int = 512,
        memory_ingestion_consumer=None,
    ) -> None:
        self.store = store
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.queue_size = queue_size
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._task: asyncio.Task | None = None
        self._cursor = 0
        self.poll_count = 0
        self.memory_ingestion = (
            memory_ingestion_consumer
            if memory_ingestion_consumer is not None
            else MemoryIngestionConsumer(store)
        )

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        # Recover committed message.accepted events that may have survived an API crash before
        # their derived proposal was staged. This runs before the fanout cursor jumps to latest.
        await self._drain_memory_ingestion(max_batches=8)
        self._cursor = await asyncio.to_thread(self._latest_id)
        self._task = asyncio.create_task(self._run(), name="task-event-fanout")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe(self, conversation_id: str) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=self.queue_size)
        self.subscribers[conversation_id].add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue) -> None:
        rows = self.subscribers.get(conversation_id)
        if not rows:
            return
        rows.discard(queue)
        if not rows:
            self.subscribers.pop(conversation_id, None)

    def _latest_id(self) -> int:
        with self.store.engine.connect() as connection:
            value = connection.execute(
                select(func.max(self.store.task_events.c.id))
            ).scalar_one_or_none()
        return int(value or 0)

    def _fetch_after(self, cursor: int) -> list[dict]:
        table = self.store.task_events
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(table)
                .where(table.c.id > cursor)
                .order_by(table.c.id)
                .limit(self.batch_size)
            ).fetchall()
        events = []
        for row in rows:
            data = dict(row._mapping)
            data["payload"] = json.loads(data.get("payload") or "{}")
            events.append(data)
        return events

    async def _drain_memory_ingestion(self, *, max_batches: int) -> None:
        if self.memory_ingestion is None:
            return
        for _ in range(max(1, int(max_batches))):
            try:
                stats = await asyncio.to_thread(
                    self.memory_ingestion.drain,
                    limit=self.batch_size,
                    worker_id="task-event-fanout",
                )
            except Exception:
                # Project-memory proposals are derived/advisory. A migration or extraction
                # problem must be loud in logs, but it must not take down task-event delivery.
                logger.exception("memory ingestion outbox drain failed")
                return
            if int(stats.get("failed") or 0):
                logger.warning("memory ingestion outbox has deferred failures", extra=stats)
            if int(stats.get("scanned") or 0) < self.batch_size:
                return
            await asyncio.sleep(0)

    async def _run(self) -> None:
        while True:
            self.poll_count += 1
            events = await asyncio.to_thread(self._fetch_after, self._cursor)
            if not events:
                await asyncio.sleep(self.poll_interval)
                continue

            # The outbox consumer has its own durable receipts/claims, so every API process
            # may observe the same event safely. Analysis job status is deliberately irrelevant.
            if any(str(event.get("type") or "") == "message.accepted" for event in events):
                await self._drain_memory_ingestion(max_batches=2)

            for event in events:
                self._cursor = max(self._cursor, int(event["id"]))
                conversation_id = str(event["conversation_id"])
                for queue in tuple(self.subscribers.get(conversation_id, ())):
                    if queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
            if len(events) >= self.batch_size:
                await asyncio.sleep(0)
