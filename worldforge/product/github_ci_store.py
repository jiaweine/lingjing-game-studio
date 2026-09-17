from __future__ import annotations

import copy
import re
import time
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Table,
    Text,
    and_,
    delete,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from worldforge.context.history_snapshot import build_history_snapshot

from .github_context_store import ConversationStore as _GitHubContextConversationStore


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WEBHOOK_PROCESSING_LEASE_SECONDS = 60.0


class ConversationStore(_GitHubContextConversationStore):
    """GitHub workflow store with indexed commit bindings and webhook replay protection."""

    def __init__(
        self,
        db_path=None,
        asset_dir="assets",
        *,
        database_url: str | None = None,
        auto_create_schema: bool = True,
        seed_dev_identity: bool = True,
    ) -> None:
        super().__init__(
            db_path=db_path,
            asset_dir=asset_dir,
            database_url=database_url,
            auto_create_schema=auto_create_schema,
            seed_dev_identity=seed_dev_identity,
        )
        self.github_code_bindings = Table(
            "github_code_bindings",
            self.metadata,
            Column("link_id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=False),
            Column("repository", String(240), nullable=False),
            Column("pull_request_number", Integer, nullable=True),
            Column("head_commit_sha", String(40), nullable=True),
            Column("selected_commit_sha", String(40), nullable=True),
            Column("updated_at", Float, nullable=False),
        )
        Index(
            "ix_github_code_bindings_repository_head",
            self.github_code_bindings.c.repository,
            self.github_code_bindings.c.head_commit_sha,
        )
        Index(
            "ix_github_code_bindings_repository_selected",
            self.github_code_bindings.c.repository,
            self.github_code_bindings.c.selected_commit_sha,
        )
        Index(
            "ix_github_code_bindings_workspace_conversation",
            self.github_code_bindings.c.workspace_id,
            self.github_code_bindings.c.conversation_id,
        )
        self.github_webhook_deliveries = Table(
            "github_webhook_deliveries",
            self.metadata,
            Column("delivery_id", String(128), primary_key=True),
            Column("event", String(64), nullable=False),
            Column("action", String(64), nullable=True),
            Column("repository", String(240), nullable=True),
            Column("head_sha", String(40), nullable=True),
            Column("workflow_run_id", String(64), nullable=True),
            Column("workflow_name", String(240), nullable=True),
            Column("workflow_url", Text, nullable=True),
            Column("conclusion", String(32), nullable=True),
            Column("status", String(32), nullable=False),
            Column("matched_count", Integer, nullable=False, default=0),
            Column("enqueued_count", Integer, nullable=False, default=0),
            Column("created_at", Float, nullable=False),
            Column("claimed_at", Float, nullable=False),
            Column("completed_at", Float, nullable=True),
        )
        Index(
            "ix_github_webhook_deliveries_status_created",
            self.github_webhook_deliveries.c.status,
            self.github_webhook_deliveries.c.created_at,
        )
        if auto_create_schema:
            self.metadata.create_all(
                self.engine,
                tables=[self.github_code_bindings, self.github_webhook_deliveries],
            )

    def _sync_github_code_binding(self, link: dict[str, Any]) -> None:
        meta = dict(link.get("meta") or {})
        context = dict(meta.get("github_context") or {})
        pull = dict(context.get("pull_request") or {})
        repository = str(link.get("repository") or "").strip().lower()
        head_sha = str(context.get("head_commit_sha") or "").strip().lower() or None
        selected_sha = str(context.get("selected_commit_sha") or "").strip().lower() or None
        if head_sha and not _FULL_SHA_RE.fullmatch(head_sha):
            head_sha = None
        if selected_sha and not _FULL_SHA_RE.fullmatch(selected_sha):
            selected_sha = None
        pull_number = pull.get("number")
        try:
            pull_number = int(pull_number) if pull_number is not None else None
        except (TypeError, ValueError):
            pull_number = None
        values = {
            "link_id": str(link["id"]),
            "workspace_id": str(link["workspace_id"]),
            "conversation_id": str(link["conversation_id"]),
            "repository": repository,
            "pull_request_number": pull_number,
            "head_commit_sha": head_sha,
            "selected_commit_sha": selected_sha,
            "updated_at": time.time(),
        }
        update_values = {key: value for key, value in values.items() if key != "link_id"}
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(self.github_code_bindings.c.link_id).where(
                        self.github_code_bindings.c.link_id == values["link_id"]
                    )
                ).first()
                if existing:
                    connection.execute(
                        update(self.github_code_bindings)
                        .where(self.github_code_bindings.c.link_id == values["link_id"])
                        .values(**update_values)
                    )
                else:
                    connection.execute(insert(self.github_code_bindings).values(**values))
        except IntegrityError:
            with self.engine.begin() as connection:
                connection.execute(
                    update(self.github_code_bindings)
                    .where(self.github_code_bindings.c.link_id == values["link_id"])
                    .values(**update_values)
                )

    def record_github_code_context(self, link_id: str, **kwargs) -> dict[str, Any]:
        link = super().record_github_code_context(link_id, **kwargs)
        self._sync_github_code_binding(link)
        return link

    def find_github_ci_matches(
        self,
        *,
        repository: str,
        head_sha: str,
    ) -> list[dict[str, Any]]:
        repository = str(repository or "").strip().lower()
        head_sha = str(head_sha or "").strip().lower()
        if not repository or not _FULL_SHA_RE.fullmatch(head_sha):
            return []
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.github_code_bindings).where(
                    and_(
                        self.github_code_bindings.c.repository == repository,
                        or_(
                            self.github_code_bindings.c.head_commit_sha == head_sha,
                            self.github_code_bindings.c.selected_commit_sha == head_sha,
                        ),
                    )
                )
            ).fetchall()
        return [dict(row._mapping) for row in rows]

    def begin_github_webhook_delivery(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        repository: str | None,
        head_sha: str | None,
        workflow_run_id: str | None,
        workflow_name: str | None,
        workflow_url: str | None,
        conclusion: str | None,
    ) -> bool:
        now = time.time()
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(self.github_webhook_deliveries).values(
                        delivery_id=str(delivery_id)[:128],
                        event=str(event)[:64],
                        action=str(action or "")[:64] or None,
                        repository=str(repository or "")[:240].lower() or None,
                        head_sha=str(head_sha or "")[:40].lower() or None,
                        workflow_run_id=str(workflow_run_id or "")[:64] or None,
                        workflow_name=str(workflow_name or "")[:240] or None,
                        workflow_url=str(workflow_url or "")[:2000] or None,
                        conclusion=str(conclusion or "")[:32] or None,
                        status="received",
                        matched_count=0,
                        enqueued_count=0,
                        created_at=now,
                        claimed_at=now,
                        completed_at=None,
                    )
                )
            return True
        except IntegrityError:
            lease_cutoff = now - _WEBHOOK_PROCESSING_LEASE_SECONDS
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.github_webhook_deliveries)
                    .where(
                        and_(
                            self.github_webhook_deliveries.c.delivery_id == delivery_id,
                            self.github_webhook_deliveries.c.status == "received",
                            self.github_webhook_deliveries.c.claimed_at <= lease_cutoff,
                        )
                    )
                    .values(claimed_at=now)
                )
            return result.rowcount == 1

    def complete_github_webhook_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        matched_count: int = 0,
        enqueued_count: int = 0,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(self.github_webhook_deliveries)
                .where(self.github_webhook_deliveries.c.delivery_id == delivery_id)
                .values(
                    status=str(status)[:32],
                    matched_count=max(0, int(matched_count)),
                    enqueued_count=max(0, int(enqueued_count)),
                    completed_at=time.time(),
                )
            )

    def get_github_webhook_delivery(self, delivery_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.github_webhook_deliveries).where(
                    self.github_webhook_deliveries.c.delivery_id == delivery_id
                )
            ).first()
        if not row:
            raise KeyError(delivery_id)
        return dict(row._mapping)

    def enqueue_ci_revalidation(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        trigger: dict[str, Any],
    ) -> dict[str, Any]:
        latest = self.latest_job(conversation_id, workspace_id=workspace_id)
        if not latest:
            raise ValueError("任务没有可复用的历史执行")
        trigger_delivery = str(trigger.get("delivery_id") or "")
        latest_trigger = dict((latest.get("payload") or {}).get("ci_trigger") or {})
        if trigger_delivery and str(latest_trigger.get("delivery_id") or "") == trigger_delivery:
            return latest
        if latest["status"] in {"queued", "running"}:
            raise ValueError("任务已有执行正在进行")
        head_sha = str(trigger.get("head_sha") or "").strip().lower()
        if not _FULL_SHA_RE.fullmatch(head_sha):
            raise ValueError("CI commit SHA 无效")

        payload = copy.deepcopy(dict(latest.get("payload") or {}))
        original_text = str(payload.get("text") or "").strip()
        workflow_name = str(trigger.get("workflow_name") or "GitHub Actions")[:240]
        run_id = str(trigger.get("workflow_run_id") or "")[:64]
        scope_line = f"【验证范围】Commit={head_sha}"
        ci_line = (
            f"【CI 自动重验】{workflow_name} 已成功完成"
            + (f"（run {run_id}）" if run_id else "")
            + "。请在该 commit 上重新执行原验证目标；CI 成功本身不代表问题已修复，最终结论必须由 Verifier 和证据决定。"
        )
        payload["text"] = "\n\n".join(part for part in [original_text, scope_line, ci_line] if part)
        payload["history_snapshot"] = build_history_snapshot(
            self.list_messages(conversation_id, workspace_id=workspace_id)
        )
        payload["ci_trigger"] = dict(trigger)

        project_context = copy.deepcopy(dict(payload.get("project_context") or {}))
        if project_context:
            scope = dict(project_context.get("scope") or {})
            scope["commit_ref"] = head_sha
            project_context["scope"] = scope
            snapshot = copy.deepcopy(dict(project_context.get("memory_snapshot") or {}))
            if snapshot:
                snapshot_scope = dict(snapshot.get("scope") or {})
                snapshot_scope["commit_ref"] = head_sha
                snapshot["scope"] = snapshot_scope
                # A CI revalidation is a new version-scoped job, not a retry of the old job.
                # Keep project identity but never carry frozen refs selected for the old commit.
                snapshot["memory_refs"] = []
                project_context["memory_snapshot"] = snapshot
            payload["project_context"] = project_context

        return self.enqueue_job(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            payload=payload,
        )

    def unlink_external_issue(self, link_id: str, **kwargs) -> dict[str, Any]:
        row = super().unlink_external_issue(link_id, **kwargs)
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.github_code_bindings).where(
                    self.github_code_bindings.c.link_id == link_id
                )
            )
        return row

    def delete_conversation(self, conversation_id: str, **kwargs):
        workspace_id = str(kwargs.get("workspace_id") or "")
        result = super().delete_conversation(conversation_id, **kwargs)
        if workspace_id:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.github_code_bindings).where(
                        and_(
                            self.github_code_bindings.c.workspace_id == workspace_id,
                            self.github_code_bindings.c.conversation_id == conversation_id,
                        )
                    )
                )
        return result
