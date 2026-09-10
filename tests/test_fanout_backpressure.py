from __future__ import annotations

import asyncio

import pytest

from worldforge.product.fanout import FanoutQueueOverflow, TaskEventFanoutHub
from worldforge.product.store import ConversationStore


def test_slow_fanout_subscriber_disconnects_instead_of_skipping_durable_events(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
        conversation = store.create_conversation("slow subscriber")
        hub = TaskEventFanoutHub(store, queue_size=2)
        queue = hub.subscribe(conversation["id"])

        first = store.add_event(conversation["id"], "progress", {"step": 1})
        hub._fanout_event(first)
        delivered = await queue.get()
        assert delivered["id"] == first["id"]

        remaining = [
            store.add_event(conversation["id"], "progress", {"step": step})
            for step in (2, 3, 4)
        ]
        for event in remaining:
            hub._fanout_event(event)

        assert queue.overflowed is True
        assert queue.qsize() == 2
        with pytest.raises(FanoutQueueOverflow):
            await queue.get()

        replay = store.list_events(
            conversation["id"],
            after_id=first["id"],
        )
        assert [event["id"] for event in replay] == [event["id"] for event in remaining]

    asyncio.run(scenario())


def test_fanout_overflow_is_isolated_to_slow_subscriber(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
        conversation = store.create_conversation("mixed subscribers")
        hub = TaskEventFanoutHub(store, queue_size=1)
        slow = hub.subscribe(conversation["id"])
        fast = hub.subscribe(conversation["id"])

        first = store.add_event(conversation["id"], "progress", {"step": 1})
        hub._fanout_event(first)
        assert (await fast.get())["id"] == first["id"]

        second = store.add_event(conversation["id"], "progress", {"step": 2})
        hub._fanout_event(second)
        assert slow.overflowed is True
        assert fast.overflowed is False
        assert (await fast.get())["id"] == second["id"]

        with pytest.raises(FanoutQueueOverflow):
            await slow.get()

    asyncio.run(scenario())
