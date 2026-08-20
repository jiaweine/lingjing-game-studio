from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from sqlalchemy import func, select


class TaskEventFanoutHub:
    """One durable event cursor shared by all conversation WebSocket clients."""

    def __init__(
        self,
        store,
        *,
        poll_interval: float = .12,
        batch_size: int = 1000,
        queue_size: int = 512,
        max_subscribers_per_conversation: int = 64,
        max_subscribers_total: int = 2048,
    ) -> None:
        self.store = store
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.queue_size = queue_size
        self.max_subscribers_per_conversation = max(
            1, int(max_subscribers_per_conversation)
        )
        self.max_subscribers_total = max(
            self.max_subscribers_per_conversation,
            int(max_subscribers_total),
        )
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._subscriber_count = 0
        self._task: asyncio.Task | None = None
        self._cursor = 0
        self._subscriber_event = asyncio.Event()
        self.poll_count = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
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
        rows = self.subscribers.get(conversation_id)
        current = len(rows) if rows else 0
        if (
            current >= self.max_subscribers_per_conversation
            or self._subscriber_count >= self.max_subscribers_total
        ):
            raise RuntimeError("too many conversation subscribers")
        queue = asyncio.Queue(maxsize=self.queue_size)
        self.subscribers[conversation_id].add(queue)
        self._subscriber_count += 1
        self._subscriber_event.set()
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue) -> None:
        rows = self.subscribers.get(conversation_id)
        if not rows:
            return
        if queue in rows:
            rows.discard(queue)
            self._subscriber_count = max(0, self._subscriber_count - 1)
        if not rows:
            self.subscribers.pop(conversation_id, None)

    @property
    def subscriber_count(self) -> int:
        return self._subscriber_count

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

    async def _wait_for_subscriber(self) -> None:
        # Durable replay in the WebSocket handler covers events created while there are
        # no live clients. Skip the global DB polling loop entirely until a subscriber
        # appears, and advance the live cursor to the latest durable event first.
        self._cursor = await asyncio.to_thread(self._latest_id)
        if self.subscribers:
            return
        self._subscriber_event.clear()
        if self.subscribers:
            return
        await self._subscriber_event.wait()

    async def _run(self) -> None:
        while True:
            if not self.subscribers:
                await self._wait_for_subscriber()
                continue
            self.poll_count += 1
            events = await asyncio.to_thread(self._fetch_after, self._cursor)
            if not events:
                await asyncio.sleep(self.poll_interval)
                continue
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
