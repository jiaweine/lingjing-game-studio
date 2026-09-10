from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.context.memory_ingestion import MemoryIngestionConsumer
from worldforge.context.project_job import build_job_project_context
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.product.store import ConversationStore


def _store(database_url: str | None, root: Path) -> ConversationStore:
    return ConversationStore(
        root / "product.db" if database_url is None else None,
        root / "assets",
        database_url=database_url,
        auto_create_schema=True,
        seed_dev_identity=False,
    )


def _cleanup(
    product: ConversationStore,
    memory: ProjectMemoryStore,
    consumer: MemoryIngestionConsumer,
    *,
    workspace_id: str,
    user_id: str,
    conversation_id: str,
    project_id: str,
) -> None:
    """Best-effort removal of the benchmark namespace from a disposable shared DB."""
    consolidator = consumer.consolidator
    with product.engine.begin() as connection:
        connection.execute(
            delete(consumer.receipts).where(
                consumer.receipts.c.workspace_id == workspace_id
            )
        )
        connection.execute(
            delete(consolidator.proposals).where(
                consolidator.proposals.c.workspace_id == workspace_id
            )
        )
        connection.execute(
            delete(memory.usage).where(memory.usage.c.workspace_id == workspace_id)
        )
        connection.execute(
            delete(memory.relations).where(memory.relations.c.workspace_id == workspace_id)
        )
        connection.execute(
            delete(memory.heads).where(memory.heads.c.workspace_id == workspace_id)
        )
        connection.execute(
            delete(memory.items).where(memory.items.c.workspace_id == workspace_id)
        )
        connection.execute(
            delete(memory.project_conversations).where(
                memory.project_conversations.c.workspace_id == workspace_id
            )
        )
        connection.execute(
            delete(memory.projects).where(memory.projects.c.id == project_id)
        )
        connection.execute(
            delete(product.task_events).where(
                product.task_events.c.workspace_id == workspace_id
            )
        )
        connection.execute(
            delete(product.jobs).where(product.jobs.c.workspace_id == workspace_id)
        )
        connection.execute(
            delete(product.messages).where(
                product.messages.c.conversation_id == conversation_id
            )
        )
        connection.execute(
            delete(product.conversations).where(
                product.conversations.c.id == conversation_id
            )
        )
        connection.execute(
            delete(product.memberships).where(
                product.memberships.c.workspace_id == workspace_id
            )
        )
        connection.execute(delete(product.users).where(product.users.c.id == user_id))
        connection.execute(
            delete(product.workspaces).where(product.workspaces.c.id == workspace_id)
        )


def run_load_protocol(
    *,
    database_url: str | None = None,
    events: int = 100,
    workers: int = 4,
    keep_data: bool = False,
) -> dict:
    events = max(1, min(20_000, int(events)))
    workers = max(1, min(64, int(workers)))
    temp = tempfile.TemporaryDirectory(prefix="lingjing-memory-load-")
    root = Path(temp.name)
    product = _store(database_url, root)
    dialect = str(product.engine.dialect.name or "unknown")
    namespace = uuid.uuid4().hex[:12]
    identity = product.create_user_workspace(
        email=f"memory-load-{namespace}@bench.local",
        name="Memory Load Bench",
        password_hash="!benchmark-only",
        workspace_name=f"memory-load-{namespace}",
    )
    workspace_id = str(identity["workspace_id"])
    user_id = str(identity["user_id"])
    memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
    project = memory.create_project(
        workspace_id=workspace_id,
        actor_id=user_id,
        name=f"Memory Load {namespace}",
        default_branch="release",
    )
    conversation = product.create_conversation(
        f"Memory load {namespace}",
        "general",
        workspace_id=workspace_id,
        created_by=user_id,
    )
    memory.bind_conversation(
        workspace_id=workspace_id,
        actor_id=user_id,
        project_id=project["id"],
        conversation_id=conversation["id"],
    )

    setup_started = time.perf_counter()
    for index in range(events):
        content = f"已确认 load_probe_{index:06d} threshold 是 {1000 + index}。"
        project_context = build_job_project_context(
            memory,
            workspace_id=workspace_id,
            actor_id=user_id,
            conversation_id=conversation["id"],
            query=content,
            requested_scope={"build_ref": "load-v1", "branch_ref": "release"},
        )
        if project_context is None:
            raise RuntimeError("benchmark conversation unexpectedly lost project binding")
        _message, job = product.create_message_job(
            workspace_id=workspace_id,
            conversation_id=conversation["id"],
            content=content,
            asset_ids=[],
            job_payload={
                "text": content,
                "provider": "auto",
                "asset_ids": [],
                "actor_id": user_id,
                "project_context": project_context,
            },
        )
        # The new outbox locator is the point of this load protocol: analysis job retention is
        # allowed to be independent of durable memory ingestion after commit.
        with product.engine.begin() as connection:
            connection.execute(delete(product.jobs).where(product.jobs.c.id == job["id"]))
    setup_seconds = time.perf_counter() - setup_started

    # Model external workers as independent processes: each worker gets its own
    # ConversationStore/SQLAlchemy engine while all engines point at the same disposable DB.
    # Sharing one default QueuePool across 16 synthetic workers artificially caps checkout at
    # 15 connections (pool_size=5 + max_overflow=10) and can turn SQLite write contention into
    # a pool timeout before the ingestion protocol itself is exercised.
    worker_products = [_store(database_url, root) for _ in range(workers)]
    consumers = [
        MemoryIngestionConsumer(worker_product, auto_create_schema=True, lease_seconds=30.0)
        for worker_product in worker_products
    ]
    load_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        first_pass = list(
            pool.map(
                lambda item: item[0].drain(
                    limit=events,
                    worker_id=f"load-worker-{item[1]:02d}",
                ),
                [(consumer, index) for index, consumer in enumerate(consumers)],
            )
        )
    # Drain any rows that lost a first-pass race or were observed while another worker held a
    # lease. Completed receipts are invisible, so this converges without duplicate proposals.
    tail_passes = 0
    while tail_passes < 8:
        stats = consumers[0].drain(
            limit=events,
            worker_id=f"load-tail-{tail_passes:02d}",
        )
        tail_passes += 1
        if stats["scanned"] == 0:
            break
    load_seconds = time.perf_counter() - load_started

    with product.engine.connect() as connection:
        receipts = connection.execute(
            select(consumers[0].receipts).where(
                consumers[0].receipts.c.workspace_id == workspace_id
            )
        ).all()
        proposals = connection.execute(
            select(consumers[0].consolidator.proposals.c.id).where(
                consumers[0].consolidator.proposals.c.workspace_id == workspace_id
            )
        ).all()
    receipt_rows = [dict(row._mapping) for row in receipts]
    completed = sum(1 for row in receipt_rows if row["status"] == "completed")
    ignored = sum(1 for row in receipt_rows if row["status"] == "ignored")
    failed = sum(1 for row in receipt_rows if row["status"] == "failed")
    max_attempts = max((int(row["attempts"] or 0) for row in receipt_rows), default=0)
    duplicate_receipt_ids = len(receipt_rows) - len({int(row["event_id"]) for row in receipt_rows})
    complete = bool(
        len(receipt_rows) == events
        and completed == events
        and ignored == 0
        and failed == 0
        and len(proposals) == events
        and duplicate_receipt_ids == 0
    )
    result = {
        "benchmark": "memory-ingestion-multiworker-load-v1",
        "database_dialect": dialect,
        "evidence_class": (
            "postgresql-multiworker-load-measurement"
            if dialect == "postgresql"
            else "sqlite-concurrency-mechanism-smoke"
        ),
        "events": events,
        "workers": workers,
        "worker_engine_topology": "one-engine-per-worker",
        "setup_seconds": round(setup_seconds, 6),
        "ingestion_seconds": round(load_seconds, 6),
        "throughput_events_per_second": round(events / max(load_seconds, 1e-9), 3),
        "receipt_rows": len(receipt_rows),
        "completed_receipts": completed,
        "ignored_receipts": ignored,
        "failed_receipts": failed,
        "proposal_rows": len(proposals),
        "duplicate_receipt_ids": duplicate_receipt_ids,
        "max_receipt_attempts": max_attempts,
        "first_pass_claimed": sum(int(row.get("claimed") or 0) for row in first_pass),
        "first_pass_completed": sum(int(row.get("completed") or 0) for row in first_pass),
        "tail_passes": tail_passes,
        "analysis_jobs_retained": 0,
        "complete": complete,
        "quality_claim": "none-load-protocol-only",
    }

    if not keep_data:
        _cleanup(
            product,
            memory,
            consumers[0],
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation["id"],
            project_id=project["id"],
        )
    for worker_product in worker_products:
        worker_product.engine.dispose()
    product.engine.dispose()
    temp.cleanup()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--confirm-disposable-database", action="store_true")
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    if args.database_url and not str(args.database_url).startswith("sqlite"):
        if not args.confirm_disposable_database:
            raise SystemExit(
                "external/non-SQLite load runs require --confirm-disposable-database; "
                "never point this benchmark at production data"
            )
    result = run_load_protocol(
        database_url=args.database_url,
        events=args.events,
        workers=args.workers,
        keep_data=args.keep_data,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_complete and not result["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
