from __future__ import annotations

import json

from sqlalchemy import delete, update

from worldforge.context.memory_ingestion import MemoryIngestionConsumer
from worldforge.context.project_job import build_job_project_context
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def _setup(tmp_path):
    product = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
    project = memory.create_project(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        name="Locator v2",
        default_branch="release",
    )
    conversation = product.create_conversation(
        "Locator task",
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
    return product, memory, project, conversation


def _enqueue(product, memory, project, conversation, content="已确认 build 1.4.7 护盾冷却是 5 秒。"):
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
    event = [
        row
        for row in product.list_events(
            conversation["id"], workspace_id=DEMO_WORKSPACE_ID
        )
        if row["type"] == "message.accepted"
        and row["payload"].get("message_id") == message["id"]
    ][0]
    return message, job, event


def test_new_message_accepted_event_freezes_minimal_ingestion_locator(tmp_path):
    product, memory, project, conversation = _setup(tmp_path)
    _message, job, event = _enqueue(product, memory, project, conversation)

    locator = event["payload"]["ingestion_locator"]
    assert locator["version"] == 2
    assert locator["job_id"] == job["id"]
    assert locator["actor_id"] == DEMO_USER_ID
    assert locator["project_actor_id"] == DEMO_USER_ID
    assert locator["project_id"] == project["id"]
    assert locator["scope"]["build_ref"] == "1.4.7"
    assert locator["scope"]["branch_ref"] == "release"
    # The durable locator is deliberately minimal and never embeds governed memory bodies.
    assert "memories" not in locator
    assert "query" not in locator


def test_v2_ingestion_survives_analysis_job_row_cleanup(tmp_path):
    product, memory, project, conversation = _setup(tmp_path)
    message, job, event = _enqueue(product, memory, project, conversation)

    # Simulate retention/cleanup of the disposable analysis job after the authoritative
    # message + outbox event have committed. v2 ingestion must not depend on that row.
    with product.engine.begin() as connection:
        connection.execute(delete(product.jobs).where(product.jobs.c.id == job["id"]))

    consumer = MemoryIngestionConsumer(product, auto_create_schema=True)
    stats = consumer.drain(worker_id="locator-v2-no-job")
    assert stats["completed"] == 1
    assert stats["failed"] == 0
    assert stats["proposals"] == 1

    proposals = consumer.consolidator.list_proposals(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        message_id=message["id"],
        status="pending",
    )
    assert len(proposals) == 1
    assert proposals[0]["build_ref"] == "1.4.7"
    assert proposals[0]["branch_ref"] == "release"
    receipt = consumer.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["project_id"] == project["id"]


def test_historical_event_without_locator_keeps_timestamp_fallback(tmp_path):
    product, memory, project, conversation = _setup(tmp_path)
    message, _job, event = _enqueue(
        product,
        memory,
        project,
        conversation,
        content="发布前必须运行 regression-suite-legacy。",
    )

    legacy_payload = {
        "message_id": message["id"],
        "asset_count": event["payload"]["asset_count"],
    }
    with product.engine.begin() as connection:
        connection.execute(
            update(product.task_events)
            .where(product.task_events.c.id == event["id"])
            .values(payload=json.dumps(legacy_payload, ensure_ascii=False))
        )

    consumer = MemoryIngestionConsumer(product, auto_create_schema=True)
    stats = consumer.drain(worker_id="legacy-fallback")
    assert stats["completed"] == 1
    assert stats["failed"] == 0
    proposals = consumer.consolidator.list_proposals(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        message_id=message["id"],
        status="pending",
    )
    assert len(proposals) == 1
    assert "regression-suite-legacy" in proposals[0]["content"]


def test_v2_locator_actor_mismatch_is_terminally_ignored(tmp_path):
    product, memory, project, conversation = _setup(tmp_path)
    message, _job, event = _enqueue(product, memory, project, conversation)
    payload = dict(event["payload"])
    locator = dict(payload["ingestion_locator"])
    locator["actor_id"] = "user-tampered"
    payload["ingestion_locator"] = locator
    with product.engine.begin() as connection:
        connection.execute(
            update(product.task_events)
            .where(product.task_events.c.id == event["id"])
            .values(payload=json.dumps(payload, ensure_ascii=False))
        )

    consumer = MemoryIngestionConsumer(product, auto_create_schema=True)
    stats = consumer.drain(worker_id="locator-v2-mismatch")
    assert stats["ignored"] == 1
    assert stats["failed"] == 0
    assert stats["proposals"] == 0
    receipt = consumer.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "ignored"
    assert receipt["message_id"] == message["id"]
