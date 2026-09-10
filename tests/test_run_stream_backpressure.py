from __future__ import annotations

import asyncio

from worldforge.api.manager import RunManager
from worldforge.replay_queue import DurableReplayQueue, ReplayRequired


def test_run_manager_recovers_overflowed_live_queue_from_durable_events(tmp_path):
    async def scenario():
        manager = RunManager(tmp_path / "runs", queue_size=2)
        session_id = "wf-overflow-test"
        manager.engine.events.create_session(session_id)
        baseline = manager.engine.events.append(session_id, "run.started", {"step": 0})
        queue = manager.subscribe(session_id)
        assert queue.cursor == baseline.seq

        emitted = []
        for step in (1, 2, 3):
            event = manager.engine.events.append(session_id, "progress", {"step": step})
            emitted.append(event)
            manager._fanout(session_id, event.model_dump())

        assert queue.overflowed is True
        recovered = [await queue.get(), await queue.get(), await queue.get()]
        assert [row["seq"] for row in recovered] == [event.seq for event in emitted]
        assert [row["payload"]["step"] for row in recovered] == [1, 2, 3]
        assert queue.cursor == emitted[-1].seq

        # One more durable event arrives after recovery catches up. Calling get must first
        # observe that durable replay is complete, then resume ordinary live delivery.
        async def emit_after_replay_check():
            await asyncio.sleep(0)
            event = manager.engine.events.append(session_id, "progress", {"step": 4})
            manager._fanout(session_id, event.model_dump())
            return event

        emitter = asyncio.create_task(emit_after_replay_check())
        live = await asyncio.wait_for(queue.get(), timeout=2)
        fourth = await emitter
        assert live["seq"] == fourth.seq
        assert live["payload"]["step"] == 4
        assert queue.overflowed is False

        manager.unsubscribe(session_id, queue)
        assert session_id not in manager.queues

    asyncio.run(scenario())


def test_replay_queue_without_durable_callback_fails_closed_on_overflow():
    async def scenario():
        queue = DurableReplayQueue(maxsize=1)
        assert queue.offer({"id": 1}) is True
        assert queue.offer({"id": 2}) is False
        assert queue.overflowed is True
        try:
            await queue.get()
        except ReplayRequired:
            return
        raise AssertionError("overflow without durable replay must require external replay")

    asyncio.run(scenario())


def test_replay_queue_detects_sequence_gap_and_replays_missing_events():
    async def scenario():
        durable = {
            2: {"seq": 2, "payload": {"step": 2}},
            3: {"seq": 3, "payload": {"step": 3}},
        }

        def replay_next(after_seq: int):
            return durable.get(after_seq + 1)

        queue = DurableReplayQueue(
            maxsize=2,
            initial_cursor=1,
            replay_next=replay_next,
            sequence_of=lambda row: int(row["seq"]),
        )

        # Seq 2 never reached this live queue. Seeing seq 3 must not silently deliver a gap.
        assert queue.offer({"seq": 3, "payload": {"step": 3}}) is False
        assert queue.overflowed is True
        assert await queue.get() == durable[2]
        assert await queue.get() == durable[3]
        assert queue.cursor == 3
        assert queue.overflowed is False

        assert queue.offer({"seq": 4, "payload": {"step": 4}}) is True
        assert await queue.get() == {"seq": 4, "payload": {"step": 4}}

    asyncio.run(scenario())


def test_replay_queue_ignores_duplicate_live_sequence():
    async def scenario():
        queue = DurableReplayQueue(
            maxsize=2,
            initial_cursor=4,
            replay_next=lambda _after: None,
            sequence_of=lambda row: int(row["seq"]),
        )

        assert queue.offer({"seq": 4, "payload": {"step": 4}}) is False
        assert queue.qsize() == 0
        assert queue.offer({"seq": 5, "payload": {"step": 5}}) is True
        assert await queue.get() == {"seq": 5, "payload": {"step": 5}}

    asyncio.run(scenario())
