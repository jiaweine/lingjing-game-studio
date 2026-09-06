from __future__ import annotations

import asyncio

from worldforge.product.fanout import TaskEventFanoutHub
from worldforge.product.store import ConversationStore


class _CountingIngestion:
    def __init__(self) -> None:
        self.calls = 0

    def drain(self, *, limit: int, worker_id: str):
        self.calls += 1
        return {
            "scanned": 0,
            "claimed": 0,
            "completed": 0,
            "ignored": 0,
            "failed": 0,
            "proposals": 0,
        }


def test_fanout_periodically_recovers_memory_ingestion_without_new_events(tmp_path):
    async def scenario():
        store = ConversationStore(
            tmp_path / "product.db",
            tmp_path / "assets",
        )
        ingestion = _CountingIngestion()
        hub = TaskEventFanoutHub(
            store,
            poll_interval=.01,
            memory_ingestion_consumer=ingestion,
            memory_recovery_interval=.05,
        )
        await hub.start()
        try:
            startup_calls = ingestion.calls
            assert startup_calls == 1
            await asyncio.sleep(.14)
            # No task event was created after startup. Periodic recovery must still run so a
            # large historical backlog or a failed receipt whose backoff expired is retried.
            assert ingestion.calls >= startup_calls + 2
        finally:
            await hub.stop()

    asyncio.run(scenario())
