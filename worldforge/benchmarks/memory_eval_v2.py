from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from worldforge.context.memory_ingestion import MemoryIngestionConsumer
from worldforge.context.project_job import build_job_project_context
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID

from .memory_eval import run_memory_benchmark as _run_memory_benchmark_v1


@dataclass(frozen=True)
class MemoryBenchmarkResult:
    cross_conversation_recall: float
    update_tracking: float
    scoped_version_isolation: float
    conflict_abstention: float
    selective_forgetting: float
    pending_memory_isolation: float
    provenance_integrity: float
    queued_snapshot_revocation: float
    restart_persistence: float
    ingestion_outbox_cancellation: float
    score: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ingestion_outbox_cancellation() -> float:
    """A committed durable user message must survive analysis cancellation and restart.

    This isolates ingestion correctness from model/runtime execution: the analysis job is
    cancelled before any worker runs, then a fresh store/consumer must recover the immutable
    ``message.accepted`` outbox event exactly once and stage only a pending proposal.
    """
    with TemporaryDirectory(prefix="lingjing-memory-outbox-bench-") as tmpdir:
        root = Path(tmpdir)
        db_path = root / "product.db"
        product = ConversationStore(
            db_path,
            root / "assets",
            seed_dev_identity=True,
        )
        memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
        project = memory.create_project(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            name="MemoryBench Outbox",
            default_branch="release",
        )
        conversation = product.create_conversation(
            "Outbox source",
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

        content = "已确认 build 1.4.7 护盾冷却是 5 秒。"
        project_context = build_job_project_context(
            memory,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            conversation_id=conversation["id"],
            query=content,
            requested_scope={"build_ref": "1.4.7", "branch_ref": "release"},
        )
        if project_context is None:
            return 0.0
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
            event
            for event in product.list_events(
                conversation["id"], workspace_id=DEMO_WORKSPACE_ID
            )
            if event["type"] == "message.accepted"
            and event["payload"].get("message_id") == message["id"]
        ]
        if len(accepted) != 1:
            return 0.0
        event = accepted[0]
        if not (
            float(message["created_at"])
            == float(job["created_at"])
            == float(event["created_at"])
        ):
            return 0.0
        cancelled = product.cancel_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
        if cancelled["status"] != "cancelled":
            return 0.0

        restarted_product = ConversationStore(
            db_path,
            root / "assets-restarted",
            seed_dev_identity=True,
        )
        consumer = MemoryIngestionConsumer(
            restarted_product,
            auto_create_schema=True,
        )
        first = consumer.drain(worker_id="memory-benchmark-restart")
        proposals = consumer.consolidator.list_proposals(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            message_id=message["id"],
            status="pending",
        )
        receipt = consumer.get_receipt(event["id"])
        second = consumer.drain(worker_id="memory-benchmark-replay")

        return float(
            first["completed"] == 1
            and first["failed"] == 0
            and first["proposals"] == 1
            and len(proposals) == 1
            and proposals[0]["content"] == content
            and proposals[0]["build_ref"] == "1.4.7"
            and proposals[0]["branch_ref"] == "release"
            and receipt is not None
            and receipt["status"] == "completed"
            and receipt["proposal_count"] == 1
            and receipt["attempts"] == 1
            and restarted_product.get_job(
                job["id"], workspace_id=DEMO_WORKSPACE_ID
            )["status"]
            == "cancelled"
            and second["scanned"] == 0
        )


def run_memory_benchmark() -> MemoryBenchmarkResult:
    base = _run_memory_benchmark_v1()
    outbox = _ingestion_outbox_cancellation()
    metrics = [
        base.cross_conversation_recall,
        base.update_tracking,
        base.scoped_version_isolation,
        base.conflict_abstention,
        base.selective_forgetting,
        base.pending_memory_isolation,
        base.provenance_integrity,
        base.queued_snapshot_revocation,
        base.restart_persistence,
        outbox,
    ]
    score = sum(metrics) / len(metrics)
    passed = all(value == 1.0 for value in metrics)
    return MemoryBenchmarkResult(
        cross_conversation_recall=base.cross_conversation_recall,
        update_tracking=base.update_tracking,
        scoped_version_isolation=base.scoped_version_isolation,
        conflict_abstention=base.conflict_abstention,
        selective_forgetting=base.selective_forgetting,
        pending_memory_isolation=base.pending_memory_isolation,
        provenance_integrity=base.provenance_integrity,
        queued_snapshot_revocation=base.queued_snapshot_revocation,
        restart_persistence=base.restart_persistence,
        ingestion_outbox_cancellation=outbox,
        score=round(score, 6),
        passed=passed,
    )
