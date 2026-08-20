import asyncio

from worldforge.product.fanout import TaskEventFanoutHub
from worldforge.product.store import ConversationStore


def test_fanout_delivers_durable_event_once(tmp_path):
    async def scenario():
        store = ConversationStore(
            tmp_path / "product.db",
            tmp_path / "assets",
        )
        conversation = store.create_conversation("fanout")
        hub = TaskEventFanoutHub(
            store, poll_interval=.01, queue_size=8
        )
        await hub.start()
        queue = hub.subscribe(conversation["id"])
        try:
            created = store.add_event(
                conversation["id"],
                "progress",
                {"step": "执行"},
            )
            delivered = await asyncio.wait_for(queue.get(), timeout=1)
            assert delivered["id"] == created["id"]
            assert delivered["payload"]["step"] == "执行"
        finally:
            hub.unsubscribe(conversation["id"], queue)
            await hub.stop()

    asyncio.run(scenario())


def test_fanout_does_not_poll_database_without_subscribers(tmp_path):
    async def scenario():
        store = ConversationStore(
            tmp_path / "product.db",
            tmp_path / "assets",
        )
        hub = TaskEventFanoutHub(store, poll_interval=.005, queue_size=8)
        await hub.start()
        try:
            await asyncio.sleep(.04)
            assert hub.poll_count == 0

            conversation = store.create_conversation("wake fanout")
            queue = hub.subscribe(conversation["id"])
            try:
                created = store.add_event(
                    conversation["id"],
                    "progress",
                    {"step": "唤醒"},
                )
                delivered = await asyncio.wait_for(queue.get(), timeout=1)
                assert delivered["id"] == created["id"]
                assert hub.poll_count > 0
            finally:
                hub.unsubscribe(conversation["id"], queue)
        finally:
            await hub.stop()

    asyncio.run(scenario())
