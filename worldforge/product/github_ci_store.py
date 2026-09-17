from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import (
    BigInteger,
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
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from .github_context_store import ConversationStore as _GitHubContextConversationStore

_DELIVERY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ConversationStore(_GitHubContextConversationStore):
    """GitHub CI opt-in and delivery-deduplication layer.

    A CI event can only route to a task after code context exists and the task/link has explicitly
    enabled automatic verification. Raw webhook payloads and provider credentials are not stored.
    """

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
        self.github_ci_subscriptions = Table(
            "github_ci_subscriptions",
            self.metadata,
            Column("link_id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=False),
            Column("enabled", Integer, nullable=False, default=0),
            Column("updated_by", String(64), nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        Index(
            "ix_github_ci_subscriptions_workspace_conversation",
            self.github_ci_subscriptions.c.workspace_id,
            self.github_ci_subscriptions.c.conversation_id,
        )
        self.github_ci_deliveries = Table(
            "github_ci_deliveries",
            self.metadata,
            Column("delivery_id", String(128), primary_key=True),
            Column("repository", String(240), nullable=False),
            Column("head_sha", String(40), nullable=False),
            Column("workflow_run_id", BigInteger, nullable=False),
            Column("workflow_name", String(240), nullable=False),
            Column("conclusion", String(32), nullable=False),
            Column("status", String(32), nullable=False),
            Column("matched_routes", Integer, nullable=False, default=0),
            Column("enqueued_jobs", Integer, nullable=False, default=0),
            Column("error", Text, nullable=True),
            Column("received_at", Float, nullable=False),
            Column("processed_at", Float, nullable=True),
        )
        Index(
            "ix_github_ci_deliveries_repository_head_sha",
            self.github_ci_deliveries.c.repository,
            self.github_ci_deliveries.c.head_sha,
        )
        Index(
            "ix_github_ci_deliveries_workflow_run",
            self.github_ci_deliveries.c.workflow_run_id,
        )
        if auto_create_schema:
            self.metadata.create_all(
                self.engine,
                tables=[self.github_ci_subscriptions, self.github_ci_deliveries],
            )

    @staticmethod
    def _ci_subscription_row(row: Any) -> dict[str, Any]:
        data = dict(row._mapping if hasattr(row, "_mapping") else row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def get_github_ci_subscription(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.github_ci_subscriptions).where(
                    and_(
                        self.github_ci_subscriptions.c.link_id == link_id,
                        self.github_ci_subscriptions.c.workspace_id == workspace_id,
                        self.github_ci_subscriptions.c.conversation_id == conversation_id,
                    )
                )
            ).first()
        if not row:
            return {
                "link_id": link_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "enabled": False,
                "updated_by": None,
                "updated_at": None,
            }
        return self._ci_subscription_row(row)

    def set_github_ci_subscription(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
        enabled: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        route = self.get_github_code_context_route(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        if enabled and not route:
            raise ValueError("请先绑定 GitHub PR 或 Commit，再开启 CI 自动验证")
        now = time.time()
        values = {
            "link_id": link_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "enabled": 1 if enabled else 0,
            "updated_by": str(updated_by),
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(self.github_ci_subscriptions.c.link_id).where(
                    self.github_ci_subscriptions.c.link_id == link_id
                )
            ).first()
            if existing:
                connection.execute(
                    update(self.github_ci_subscriptions)
                    .where(self.github_ci_subscriptions.c.link_id == link_id)
                    .values(**values)
                )
            else:
                connection.execute(insert(self.github_ci_subscriptions).values(**values))
        return self.get_github_ci_subscription(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    def list_enabled_github_ci_routes(
        self,
        *,
        repository: str,
        commit_sha: str,
    ) -> list[dict[str, Any]]:
        routes = self.find_github_commit_routes(
            repository=repository,
            commit_sha=commit_sha,
        )
        if not routes:
            return []
        link_ids = [str(route["link_id"]) for route in routes]
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.github_ci_subscriptions.c.link_id).where(
                    and_(
                        self.github_ci_subscriptions.c.link_id.in_(link_ids),
                        self.github_ci_subscriptions.c.enabled == 1,
                    )
                )
            ).fetchall()
        enabled = {str(row[0]) for row in rows}
        return [route for route in routes if str(route["link_id"]) in enabled]

    def list_external_issue_links(self, conversation_id: str, *, workspace_id: str):
        links = super().list_external_issue_links(
            conversation_id,
            workspace_id=workspace_id,
        )
        if not links:
            return links
        link_ids = [str(link["id"]) for link in links]
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.github_ci_subscriptions).where(
                    self.github_ci_subscriptions.c.link_id.in_(link_ids)
                )
            ).fetchall()
        subscriptions = {
            str(row._mapping["link_id"]): self._ci_subscription_row(row) for row in rows
        }
        for link in links:
            link["ci_subscription"] = subscriptions.get(
                str(link["id"]),
                {"enabled": False, "updated_at": None, "updated_by": None},
            )
        return links

    def begin_github_ci_delivery(
        self,
        *,
        delivery_id: str,
        repository: str,
        head_sha: str,
        workflow_run_id: int,
        workflow_name: str,
        conclusion: str,
    ) -> tuple[dict[str, Any], bool]:
        delivery_id = str(delivery_id or "").strip()
        if not _DELIVERY_ID_RE.fullmatch(delivery_id):
            raise ValueError("invalid GitHub delivery id")
        repository = self._normalize_github_repository(repository)
        sha = self._full_sha(head_sha)
        if not sha:
            raise ValueError("GitHub workflow head SHA 不能为空")
        now = time.time()
        values = {
            "delivery_id": delivery_id,
            "repository": repository,
            "head_sha": sha,
            "workflow_run_id": int(workflow_run_id),
            "workflow_name": str(workflow_name or "")[:240],
            "conclusion": str(conclusion or "")[:32],
            "status": "received",
            "matched_routes": 0,
            "enqueued_jobs": 0,
            "error": None,
            "received_at": now,
            "processed_at": None,
        }
        is_new = True
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.github_ci_deliveries).values(**values))
        except IntegrityError:
            is_new = False
        return self.get_github_ci_delivery(delivery_id), is_new

    def get_github_ci_delivery(self, delivery_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.github_ci_deliveries).where(
                    self.github_ci_deliveries.c.delivery_id == delivery_id
                )
            ).first()
        if not row:
            raise KeyError(delivery_id)
        return dict(row._mapping)

    def complete_github_ci_delivery(
        self,
        delivery_id: str,
        *,
        matched_routes: int,
        enqueued_jobs: int,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(self.github_ci_deliveries)
                .where(self.github_ci_deliveries.c.delivery_id == delivery_id)
                .values(
                    status="failed" if error else "processed",
                    matched_routes=max(0, int(matched_routes)),
                    enqueued_jobs=max(0, int(enqueued_jobs)),
                    error=str(error or "")[:4000] or None,
                    processed_at=now,
                )
            )
        if result.rowcount != 1:
            raise KeyError(delivery_id)
        return self.get_github_ci_delivery(delivery_id)

    def unlink_external_issue(self, link_id: str, *, conversation_id: str, workspace_id: str):
        row = super().unlink_external_issue(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.github_ci_subscriptions).where(
                    self.github_ci_subscriptions.c.link_id == link_id
                )
            )
        return row

    def delete_conversation(self, conversation_id: str, **kwargs):
        workspace_id = str(kwargs.get("workspace_id") or "")
        result = super().delete_conversation(conversation_id, **kwargs)
        if workspace_id:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.github_ci_subscriptions).where(
                        and_(
                            self.github_ci_subscriptions.c.workspace_id == workspace_id,
                            self.github_ci_subscriptions.c.conversation_id == conversation_id,
                        )
                    )
                )
        return result
