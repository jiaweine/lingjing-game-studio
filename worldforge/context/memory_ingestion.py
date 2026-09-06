from __future__ import annotations

import json
import logging
import os
import socket
import time
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from worldforge.settings import settings

from .memory_consolidator import MemoryConsolidator, MemoryConflict
from .project_memory import ProjectMemoryStore
from .project_packet import ProjectScopeSnapshot

logger = logging.getLogger("worldforge.context.memory_ingestion")


class MemoryIngestionConsumer:
    """Consume durable ``message.accepted`` events into reviewable memory proposals.

    The product transaction already commits the authoritative user message, analysis job,
    and ``message.accepted`` task event with one timestamp. The task event is therefore the
    durable outbox intent; this consumer adds a separate receipt/lease state so analysis-job
    cancellation, API restarts, and duplicate delivery cannot silently lose or duplicate
    proposal staging.

    The consumer never writes authoritative Project Memory. Its only derived output is the
    existing ``pending`` proposal table, which still requires an explicit later approval.
    """

    EVENT_TYPE = "message.accepted"

    def __init__(
        self,
        product_store,
        *,
        memory_store: ProjectMemoryStore | None = None,
        consolidator: MemoryConsolidator | None = None,
        auto_create_schema: bool | None = None,
        lease_seconds: float = 60.0,
    ) -> None:
        self.product_store = product_store
        self.engine = product_store.engine
        auto_create = (
            settings.auto_create_schema
            if auto_create_schema is None
            else bool(auto_create_schema)
        )
        self.memory_store = memory_store or ProjectMemoryStore(
            self.engine,
            auto_create_schema=auto_create,
        )
        self.consolidator = consolidator or MemoryConsolidator(
            self.engine,
            self.memory_store,
            auto_create_schema=auto_create,
        )
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.metadata = MetaData()
        self.receipts = Table(
            "context_memory_ingestion_receipts",
            self.metadata,
            Column("event_id", Integer, primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("message_id", String(64), nullable=True, index=True),
            Column("project_id", String(64), nullable=True, index=True),
            Column("status", String(32), nullable=False, index=True),
            Column("attempts", Integer, nullable=False, default=0),
            Column("worker_id", String(96), nullable=True),
            Column("claimed_at", Float, nullable=True),
            Column("available_at", Float, nullable=False, default=0.0, index=True),
            Column("completed_at", Float, nullable=True),
            Column("proposal_count", Integer, nullable=False, default=0),
            Column("last_error", Text, nullable=False, default=""),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        if auto_create:
            self.metadata.create_all(self.engine)

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row._mapping if hasattr(row, "_mapping") else row)

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        try:
            data = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _default_worker_id() -> str:
        return f"memory-ingestion:{socket.gethostname()}:{os.getpid()}"

    def _pending_events(self, *, limit: int, now: float) -> list[dict[str, Any]]:
        events = self.product_store.task_events
        receipts = self.receipts
        stale_before = now - self.lease_seconds
        retryable = or_(
            receipts.c.event_id.is_(None),
            and_(
                receipts.c.status == "failed",
                receipts.c.available_at <= now,
            ),
            and_(
                receipts.c.status == "processing",
                or_(
                    receipts.c.claimed_at.is_(None),
                    receipts.c.claimed_at <= stale_before,
                ),
            ),
        )
        statement = (
            select(events)
            .select_from(events.outerjoin(receipts, receipts.c.event_id == events.c.id))
            .where(and_(events.c.type == self.EVENT_TYPE, retryable))
            .order_by(events.c.id)
            .limit(max(1, min(5000, int(limit))))
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            item["payload"] = self._payload(item.get("payload"))
            result.append(item)
        return result

    def _claim(self, event: dict[str, Any], *, worker_id: str, now: float) -> int | None:
        event_id = int(event["id"])
        stale_before = now - self.lease_seconds
        retryable_existing = or_(
            and_(
                self.receipts.c.status == "failed",
                self.receipts.c.available_at <= now,
            ),
            and_(
                self.receipts.c.status == "processing",
                or_(
                    self.receipts.c.claimed_at.is_(None),
                    self.receipts.c.claimed_at <= stale_before,
                ),
            ),
        )
        with self.engine.begin() as connection:
            result = connection.execute(
                update(self.receipts)
                .where(
                    and_(
                        self.receipts.c.event_id == event_id,
                        retryable_existing,
                    )
                )
                .values(
                    status="processing",
                    attempts=self.receipts.c.attempts + 1,
                    worker_id=worker_id[:96],
                    claimed_at=now,
                    available_at=now,
                    completed_at=None,
                    last_error="",
                    updated_at=now,
                )
            )
            if result.rowcount:
                attempts = connection.execute(
                    select(self.receipts.c.attempts).where(
                        self.receipts.c.event_id == event_id
                    )
                ).scalar_one()
                return int(attempts)

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(self.receipts).values(
                        event_id=event_id,
                        workspace_id=str(event.get("workspace_id") or "")[:64],
                        conversation_id=str(event.get("conversation_id") or "")[:64],
                        message_id=None,
                        project_id=None,
                        status="processing",
                        attempts=1,
                        worker_id=worker_id[:96],
                        claimed_at=now,
                        available_at=now,
                        completed_at=None,
                        proposal_count=0,
                        last_error="",
                        created_at=now,
                        updated_at=now,
                    )
                )
            return 1
        except IntegrityError:
            # Another process won the first-delivery insert. Completed/active leases are not
            # stealable; failed/stale rows will become visible on a later drain.
            return None

    def _finish(
        self,
        event_id: int,
        *,
        status: str,
        message_id: str | None,
        project_id: str | None,
        proposal_count: int = 0,
        error: str = "",
        attempts: int = 1,
    ) -> None:
        now = time.time()
        if status not in {"completed", "ignored", "failed"}:
            raise ValueError(f"invalid ingestion receipt status: {status}")
        if status == "failed":
            delay = min(300.0, float(2 ** min(max(1, int(attempts)), 8)))
            available_at = now + delay
            completed_at = None
        else:
            available_at = now
            completed_at = now
        with self.engine.begin() as connection:
            connection.execute(
                update(self.receipts)
                .where(self.receipts.c.event_id == int(event_id))
                .values(
                    message_id=(message_id or None),
                    project_id=(project_id or None),
                    status=status,
                    available_at=available_at,
                    completed_at=completed_at,
                    proposal_count=max(0, int(proposal_count)),
                    last_error=str(error or "")[:4000],
                    updated_at=now,
                )
            )

    def _source_for_event(
        self,
        event: dict[str, Any],
    ) -> tuple[str, str, str, str, ProjectScopeSnapshot]:
        payload = self._payload(event.get("payload"))
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("message.accepted event missing message_id")
        workspace_id = str(event.get("workspace_id") or "")
        conversation_id = str(event.get("conversation_id") or "")
        created_at = float(event.get("created_at") or 0.0)

        with self.engine.connect() as connection:
            message = connection.execute(
                select(
                    self.product_store.messages.c.role,
                    self.product_store.messages.c.content,
                    self.product_store.messages.c.created_at,
                ).where(
                    and_(
                        self.product_store.messages.c.id == message_id,
                        self.product_store.messages.c.conversation_id == conversation_id,
                    )
                )
            ).first()
            if message is None:
                raise KeyError("authoritative source message no longer exists")
            if str(message.role) != "user":
                raise ValueError("message.accepted source must be a user message")

            # create_message_job() commits message, analysis job and message.accepted using
            # one timestamp. Exact equality is the primary immutable linkage; the tiny
            # fallback protects database float round-tripping without allowing a broad
            # "latest job" substitution that could drift to a later user turn.
            jobs = self.product_store.jobs
            job = connection.execute(
                select(jobs.c.id, jobs.c.payload, jobs.c.created_at)
                .where(
                    and_(
                        jobs.c.workspace_id == workspace_id,
                        jobs.c.conversation_id == conversation_id,
                        jobs.c.created_at == created_at,
                    )
                )
                .order_by(jobs.c.id)
                .limit(1)
            ).first()
            if job is None:
                job = connection.execute(
                    select(jobs.c.id, jobs.c.payload, jobs.c.created_at)
                    .where(
                        and_(
                            jobs.c.workspace_id == workspace_id,
                            jobs.c.conversation_id == conversation_id,
                            jobs.c.created_at >= created_at - 0.001,
                            jobs.c.created_at <= created_at + 0.001,
                        )
                    )
                    .order_by(func.abs(jobs.c.created_at - created_at), jobs.c.id)
                    .limit(1)
                ).first()
        if job is None:
            raise LookupError("frozen analysis job envelope unavailable for message.accepted")

        job_payload = self._payload(job.payload)
        project_context = dict(job_payload.get("project_context") or {})
        actor_id = str(
            job_payload.get("actor_id") or project_context.get("actor_id") or ""
        ).strip()
        project_actor = str(project_context.get("actor_id") or "").strip()
        if actor_id and project_actor and actor_id != project_actor:
            raise PermissionError("ingestion actor mismatch in frozen job envelope")
        project_id = str(project_context.get("project_id") or "").strip()
        if not actor_id or not project_id:
            raise RuntimeError("unbound-message")
        scope = ProjectScopeSnapshot.from_dict(project_context.get("scope"))
        return message_id, str(message.content), actor_id, project_id, scope

    def process_event(
        self,
        event: dict[str, Any],
        *,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        if str(event.get("type") or "") != self.EVENT_TYPE:
            return {"claimed": False, "status": "skipped", "proposal_count": 0}
        worker = str(worker_id or self._default_worker_id())[:96]
        now = time.time()
        attempts = self._claim(event, worker_id=worker, now=now)
        if attempts is None:
            return {"claimed": False, "status": "leased", "proposal_count": 0}

        event_id = int(event["id"])
        message_id: str | None = None
        project_id: str | None = None
        try:
            try:
                message_id, content, actor_id, project_id, scope = self._source_for_event(event)
            except RuntimeError as exc:
                if str(exc) != "unbound-message":
                    raise
                self._finish(
                    event_id,
                    status="ignored",
                    message_id=str(self._payload(event.get("payload")).get("message_id") or "") or None,
                    project_id=None,
                    error="conversation was not bound to a project at message commit",
                    attempts=attempts,
                )
                return {"claimed": True, "status": "ignored", "proposal_count": 0}
            except (KeyError, PermissionError, ValueError) as exc:
                # Deleted/revoked/invalid authoritative sources are intentionally not retried:
                # ingestion must never resurrect a source the product no longer authorizes.
                self._finish(
                    event_id,
                    status="ignored",
                    message_id=str(self._payload(event.get("payload")).get("message_id") or "") or None,
                    project_id=project_id,
                    error=repr(exc),
                    attempts=attempts,
                )
                return {"claimed": True, "status": "ignored", "proposal_count": 0}

            proposals = self.consolidator.propose_user_message(
                workspace_id=str(event.get("workspace_id") or ""),
                actor_id=actor_id,
                project_id=project_id,
                conversation_id=str(event.get("conversation_id") or ""),
                message_id=message_id,
                content=content,
                scope=scope,
            )
            proposal_count = sum(1 for row in proposals if row.get("status") == "pending")
            self._finish(
                event_id,
                status="completed",
                message_id=message_id,
                project_id=project_id,
                proposal_count=proposal_count,
                attempts=attempts,
            )
            return {
                "claimed": True,
                "status": "completed",
                "proposal_count": proposal_count,
            }
        except (LookupError, MemoryConflict) as exc:
            self._finish(
                event_id,
                status="failed",
                message_id=message_id,
                project_id=project_id,
                error=repr(exc),
                attempts=attempts,
            )
            logger.warning(
                "memory ingestion deferred",
                extra={"event_id": event_id, "attempts": attempts, "error": repr(exc)},
            )
            return {"claimed": True, "status": "failed", "proposal_count": 0}
        except Exception as exc:
            self._finish(
                event_id,
                status="failed",
                message_id=message_id,
                project_id=project_id,
                error=repr(exc),
                attempts=attempts,
            )
            logger.exception(
                "memory ingestion failed",
                extra={"event_id": event_id, "attempts": attempts},
            )
            return {"claimed": True, "status": "failed", "proposal_count": 0}

    def drain(
        self,
        *,
        limit: int = 250,
        worker_id: str | None = None,
    ) -> dict[str, int]:
        now = time.time()
        events = self._pending_events(limit=limit, now=now)
        stats = {
            "scanned": len(events),
            "claimed": 0,
            "completed": 0,
            "ignored": 0,
            "failed": 0,
            "proposals": 0,
        }
        worker = worker_id or self._default_worker_id()
        for event in events:
            result = self.process_event(event, worker_id=worker)
            if not result.get("claimed"):
                continue
            stats["claimed"] += 1
            status = str(result.get("status") or "")
            if status in {"completed", "ignored", "failed"}:
                stats[status] += 1
            stats["proposals"] += int(result.get("proposal_count") or 0)
        return stats

    def get_receipt(self, event_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.receipts).where(self.receipts.c.event_id == int(event_id))
            ).first()
        return self._dict(row) if row is not None else None
