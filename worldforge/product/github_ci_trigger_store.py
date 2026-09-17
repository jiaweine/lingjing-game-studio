from __future__ import annotations

import time
from typing import Any

from sqlalchemy import Column, Float, Index, String, Table, and_, delete, insert, select, update

from .github_ci_store import ConversationStore as _GitHubCIConversationStore


class ConversationStore(_GitHubCIConversationStore):
    """GitHub CI store extended with an exact workflow-name trigger filter per link."""

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
        self.github_ci_workflow_filters = Table(
            "github_ci_workflow_filters",
            self.metadata,
            Column("link_id", String(64), primary_key=True),
            Column("workflow_name", String(240), nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        Index(
            "ix_github_ci_workflow_filters_name",
            self.github_ci_workflow_filters.c.workflow_name,
        )
        if auto_create_schema:
            self.metadata.create_all(self.engine, tables=[self.github_ci_workflow_filters])

    @staticmethod
    def _workflow_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("开启 CI 自动验证时必须指定 GitHub workflow 名称")
        if len(name) > 240:
            raise ValueError("GitHub workflow 名称过长")
        return name

    def _workflow_filter(self, link_id: str) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.github_ci_workflow_filters.c.workflow_name).where(
                    self.github_ci_workflow_filters.c.link_id == link_id
                )
            ).first()
        return str(row[0]) if row else None

    def get_github_ci_subscription(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        result = super().get_github_ci_subscription(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        result["workflow_name"] = self._workflow_filter(link_id)
        return result

    def set_github_ci_subscription(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
        enabled: bool,
        updated_by: str,
        workflow_name: str | None = None,
    ) -> dict[str, Any]:
        current_name = self._workflow_filter(link_id)
        resolved_name = self._workflow_name(workflow_name or current_name) if enabled else (
            str(workflow_name or current_name or "").strip() or None
        )
        result = super().set_github_ci_subscription(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            enabled=enabled,
            updated_by=updated_by,
        )
        now = time.time()
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(self.github_ci_workflow_filters.c.link_id).where(
                    self.github_ci_workflow_filters.c.link_id == link_id
                )
            ).first()
            if resolved_name:
                values = {
                    "link_id": link_id,
                    "workflow_name": resolved_name,
                    "updated_at": now,
                }
                if existing:
                    connection.execute(
                        update(self.github_ci_workflow_filters)
                        .where(self.github_ci_workflow_filters.c.link_id == link_id)
                        .values(**values)
                    )
                else:
                    connection.execute(insert(self.github_ci_workflow_filters).values(**values))
            elif existing:
                connection.execute(
                    delete(self.github_ci_workflow_filters).where(
                        self.github_ci_workflow_filters.c.link_id == link_id
                    )
                )
        result["workflow_name"] = resolved_name
        return result

    def list_enabled_github_ci_routes(
        self,
        *,
        repository: str,
        commit_sha: str,
        workflow_name: str,
    ) -> list[dict[str, Any]]:
        workflow_name = self._workflow_name(workflow_name)
        routes = super().list_enabled_github_ci_routes(
            repository=repository,
            commit_sha=commit_sha,
        )
        if not routes:
            return []
        link_ids = [str(route["link_id"]) for route in routes]
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    self.github_ci_workflow_filters.c.link_id,
                    self.github_ci_workflow_filters.c.workflow_name,
                ).where(
                    and_(
                        self.github_ci_workflow_filters.c.link_id.in_(link_ids),
                        self.github_ci_workflow_filters.c.workflow_name == workflow_name,
                    )
                )
            ).fetchall()
        allowed = {str(row[0]) for row in rows}
        return [route for route in routes if str(route["link_id"]) in allowed]

    def list_external_issue_links(self, conversation_id: str, *, workspace_id: str):
        links = super().list_external_issue_links(
            conversation_id,
            workspace_id=workspace_id,
        )
        for link in links:
            subscription = dict(link.get("ci_subscription") or {})
            subscription["workflow_name"] = self._workflow_filter(str(link["id"]))
            link["ci_subscription"] = subscription
        return links

    def unlink_external_issue(self, link_id: str, *, conversation_id: str, workspace_id: str):
        row = super().unlink_external_issue(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.github_ci_workflow_filters).where(
                    self.github_ci_workflow_filters.c.link_id == link_id
                )
            )
        return row

    def delete_conversation(self, conversation_id: str, **kwargs):
        workspace_id = str(kwargs.get("workspace_id") or "")
        result = super().delete_conversation(conversation_id, **kwargs)
        if workspace_id:
            with self.engine.begin() as connection:
                link_ids = connection.execute(
                    select(self.github_ci_subscriptions.c.link_id).where(
                        and_(
                            self.github_ci_subscriptions.c.workspace_id == workspace_id,
                            self.github_ci_subscriptions.c.conversation_id == conversation_id,
                        )
                    )
                ).fetchall()
                if link_ids:
                    connection.execute(
                        delete(self.github_ci_workflow_filters).where(
                            self.github_ci_workflow_filters.c.link_id.in_([str(row[0]) for row in link_ids])
                        )
                    )
        return result
