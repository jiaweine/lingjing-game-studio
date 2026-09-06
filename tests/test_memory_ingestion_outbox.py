from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from worldforge.context.memory_consolidator import MemoryConsolidator
from worldforge.context.memory_ingestion import MemoryIngestionConsumer
from worldforge.context.project_job import build_job_project_context
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.product.fanout import TaskEventFanoutHub
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def _setup(tmp_path):
    db_path = tmp_path / "product.db"
    product = ConversationStore(
        db_path,
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
    consolidator = MemoryConsolidator(
        product.engine,
        memory,
        auto_create_schema=True,
    )
    project = memory.create_project(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        name="Outbox Atlas",
        default_branch="release",
    )
    conversation = product.create_conversation(
        "Outbox task",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    memory.bind_conversation(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        conversation_id=conversation["id"],
    )
    return db_path, product, memory, consolidator, project, conversation


def _enqueue(product, memory, project, conversation, content):
    project_context = build_job_project_context(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        conversation_id=conversation["id"],
        query=content,
        requested_scope={"build_ref": "1.4.7", "branch_ref": "release"},
    )
    assert project_context is not None
    message, job = product.create_message_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        content=content,
        asset_ids=[],
        job_payload={
            "text": content,
            "provider": "auto",
            "asset_ids": [],
            "actor_id": DEMO_USER_ID,
            "project_context": project_context,
        },
    )
    accepted = [
        row
        for row in product.list_events(
            conversation["id"], workspace_id=DEMO_WORKSPACE_ID
        )
        if row["type"] == "message.accepted"
        and row["payload"].get("message_id") == message["id"]
    ]
    assert len(accepted) == 1
    event = accepted[0]
    # This is the current outbox-envelope linkage: create_message_job commits message, job,
    # and event from one transaction timestamp. If Store changes this, the test forces an
    # explicit envelope migration instead of silently switching to a "latest job" lookup.
    assert float(message["created_at"]) == float(job["created_at"]) == float(event["created_at"])
    assert str(job["payload"]["project_context"]["project_id"]) == str(project["id"])
    return message, job, event


def test_cancelled_analysis_job_does_not_cancel_memory_ingestion_and_restart_recovers(tmp_path):
    db_path, product, memory, _consolidator, project, conversation = _setup(tmp_path)
    content = "已确认 build 1.4.7 护盾冷却是 5 秒。"
    message, job, event = _enqueue(
        product, memory, project, conversation, content
    )

    cancelled = product.cancel_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert cancelled["status"] == "cancelled"

    # Simulate a process crash after the message transaction committed but before any
    # analysis worker or proposal-staging code ran.
    restarted_product = ConversationStore(
        db_path,
        tmp_path / "assets-restarted",
        seed_dev_identity=True,
    )
    restarted_consumer = MemoryIngestionConsumer(
        restarted_product,
        auto_create_schema=True,
    )
    first = restarted_consumer.drain(worker_id="restart-test")
    assert first["completed"] == 1
    assert first["failed"] == 0
    assert first["proposals"] == 1

    proposals = restarted_consumer.consolidator.list_proposals(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        message_id=message["id"],
        status="pending",
    )
    assert len(proposals) == 1
    assert proposals[0]["content"] == content
    assert proposals[0]["build_ref"] == "1.4.7"
    assert proposals[0]["branch_ref"] == "release"
    assert restarted_product.get_job(
        job["id"], workspace_id=DEMO_WORKSPACE_ID
    )["status"] == "cancelled"

    receipt = restarted_consumer.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["message_id"] == message["id"]
    assert receipt["project_id"] == project["id"]
    assert receipt["proposal_count"] == 1
    assert receipt["attempts"] == 1

    second = restarted_consumer.drain(worker_id="restart-test-replay")
    assert second["scanned"] == 0
    proposals_after_replay = restarted_consumer.consolidator.list_proposals(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        message_id=message["id"],
        status=None,
    )
    assert [row["id"] for row in proposals_after_replay] == [proposals[0]["id"]]


def test_fanout_startup_drains_committed_outbox_before_advancing_event_cursor(tmp_path):
    async def scenario():
        db_path, product, memory, _consolidator, project, conversation = _setup(tmp_path)
        content = "发布前必须运行 regression-suite-delta。"
        message, job, event = _enqueue(
            product, memory, project, conversation, content
        )
        product.cancel_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)

        # A fresh API process constructs a fresh store/hub. start() must recover the old
        # message.accepted outbox row before setting its normal websocket fanout cursor.
        restarted_product = ConversationStore(
            db_path,
            tmp_path / "assets-fanout-restart",
            seed_dev_identity=True,
        )
        hub = TaskEventFanoutHub(
            restarted_product,
            poll_interval=.01,
            queue_size=8,
        )
        await hub.start()
        try:
            receipt = hub.memory_ingestion.get_receipt(event["id"])
            assert receipt is not None
            assert receipt["status"] == "completed"
            proposals = hub.memory_ingestion.consolidator.list_proposals(
                workspace_id=DEMO_WORKSPACE_ID,
                actor_id=DEMO_USER_ID,
                project_id=project["id"],
                message_id=message["id"],
                status="pending",
            )
            assert len(proposals) == 1
            assert "regression-suite-delta" in proposals[0]["content"]
        finally:
            await hub.stop()

    asyncio.run(scenario())


def test_unbound_message_outbox_is_terminally_ignored_instead_of_guessing_project(tmp_path):
    product = ConversationStore(
        tmp_path / "unbound.db",
        tmp_path / "assets-unbound",
        seed_dev_identity=True,
    )
    conversation = product.create_conversation(
        "Unbound",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    message, _job = product.create_message_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        content="已确认这个值是 5 秒。",
        asset_ids=[],
        job_payload={
            "text": "已确认这个值是 5 秒。",
            "provider": "auto",
            "asset_ids": [],
            "actor_id": DEMO_USER_ID,
            "project_context": None,
        },
    )
    event = [
        row
        for row in product.list_events(
            conversation["id"], workspace_id=DEMO_WORKSPACE_ID
        )
        if row["type"] == "message.accepted"
    ][0]

    consumer = MemoryIngestionConsumer(product, auto_create_schema=True)
    stats = consumer.drain(worker_id="unbound-test")
    assert stats["ignored"] == 1
    assert stats["proposals"] == 0
    receipt = consumer.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "ignored"
    assert receipt["message_id"] == message["id"]
    assert receipt["project_id"] is None
    assert consumer.drain(worker_id="unbound-replay")["scanned"] == 0


def test_two_consumers_racing_one_event_commit_one_receipt_and_one_proposal(tmp_path):
    _db_path, product, memory, consolidator, project, conversation = _setup(tmp_path)
    content = "我们决定采用方案 Delta。"
    message, _job, event = _enqueue(
        product, memory, project, conversation, content
    )
    first = MemoryIngestionConsumer(
        product,
        memory_store=memory,
        consolidator=consolidator,
        auto_create_schema=True,
    )
    second = MemoryIngestionConsumer(
        product,
        auto_create_schema=True,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: pair[0].drain(worker_id=pair[1]),
                [(first, "consumer-a"), (second, "consumer-b")],
            )
        )

    assert sum(row["completed"] for row in results) == 1
    proposals = consolidator.list_proposals(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        message_id=message["id"],
        status=None,
    )
    assert len(proposals) == 1
    receipt = first.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["proposal_count"] == 1
