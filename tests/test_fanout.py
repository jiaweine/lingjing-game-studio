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
