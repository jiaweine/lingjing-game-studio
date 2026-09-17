from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    delete,
    insert,
    select,
    update,
)

from .store import _id
from .value_store import ConversationStore as _ValueConversationStore

_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SENSITIVE_META_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "cookie",
    "api_key",
    "apikey",
)


class ConversationStore(_ValueConversationStore):
    """Conversation store with durable external work-item linkage.

    This layer deliberately stores identifiers and synchronization metadata only. OAuth tokens,
    personal access tokens and provider secrets never belong in conversation/task rows.
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
        self.external_issue_links = Table(
            "external_issue_links",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=False),
            Column("provider", String(32), nullable=False),
            Column("resource_type", String(32), nullable=False),
            Column("repository", String(240), nullable=False),
            Column("external_key", String(96), nullable=False),
            Column("external_url", Text, nullable=False),
            Column("external_title", String(240), nullable=True),
            Column("sync_state", String(32), nullable=False, default="linked"),
            Column("created_by", String(64), nullable=False),
            Column("meta", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
            Column("last_pushed_at", Float, nullable=True),
            UniqueConstraint(
                "workspace_id",
                "conversation_id",
                "provider",
                "repository",
                "external_key",
                name="uq_external_issue_link_target",
            ),
        )
        Index(
            "ix_external_issue_links_workspace_conversation",
            self.external_issue_links.c.workspace_id,
            self.external_issue_links.c.conversation_id,
        )
        Index(
            "ix_external_issue_links_provider_target",
            self.external_issue_links.c.provider,
            self.external_issue_links.c.repository,
            self.external_issue_links.c.external_key,
        )
        if auto_create_schema:
            self.metadata.create_all(self.engine, tables=[self.external_issue_links])

    @staticmethod
    def _normalize_github_repository(repository: str) -> str:
        value = str(repository or "").strip().strip("/")
        if not _GITHUB_REPOSITORY_RE.fullmatch(value):
            raise ValueError("GitHub repository 必须使用 owner/repo 格式")
        return value

    @staticmethod
    def _safe_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(meta or {})

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).strip().lower().replace("-", "_")
                    if any(part in normalized for part in _SENSITIVE_META_KEY_PARTS):
                        raise ValueError("external issue metadata 不能包含 token/secret/credential")
                    walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    walk(child)

        walk(payload)
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("external issue metadata 必须可 JSON 序列化") from exc
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("external issue metadata 不能超过 16KB")
        return payload

    @staticmethod
    def _external_link_row(row: Any) -> dict[str, Any]:
        data = dict(row._mapping if hasattr(row, "_mapping") else row)
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data["meta"] = {}
        return data

    def list_external_issue_links(
        self,
        conversation_id: str,
        *,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.external_issue_links)
                .where(
                    and_(
                        self.external_issue_links.c.workspace_id == workspace_id,
                        self.external_issue_links.c.conversation_id == conversation_id,
                    )
                )
                .order_by(
                    self.external_issue_links.c.created_at,
                    self.external_issue_links.c.id,
                )
            ).fetchall()
        return [self._external_link_row(row) for row in rows]

    def link_github_issue(
        self,
        conversation_id: str,
        *,
        workspace_id: str,
        created_by: str,
        repository: str,
        issue_number: int,
        title: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        repository = self._normalize_github_repository(repository)
        if int(issue_number) < 1:
            raise ValueError("GitHub issue number 必须大于 0")
        external_key = str(int(issue_number))
        external_url = f"https://github.com/{repository}/issues/{external_key}"
        now = time.time()
        normalized_title = str(title or "").strip()[:240] or None
        payload = json.dumps(self._safe_meta(meta), ensure_ascii=False)

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(self.external_issue_links.c.id).where(
                    and_(
                        self.external_issue_links.c.workspace_id == workspace_id,
                        self.external_issue_links.c.conversation_id == conversation_id,
                        self.external_issue_links.c.provider == "github",
                        self.external_issue_links.c.repository == repository,
                        self.external_issue_links.c.external_key == external_key,
                    )
                )
            ).first()
            if existing:
                link_id = str(existing[0])
                connection.execute(
                    update(self.external_issue_links)
                    .where(self.external_issue_links.c.id == link_id)
                    .values(
                        external_url=external_url,
                        external_title=normalized_title,
                        sync_state="linked",
                        meta=payload,
                        updated_at=now,
                    )
                )
            else:
                link_id = _id("link")
                connection.execute(
                    insert(self.external_issue_links).values(
                        id=link_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        provider="github",
                        resource_type="issue",
                        repository=repository,
                        external_key=external_key,
                        external_url=external_url,
                        external_title=normalized_title,
                        sync_state="linked",
                        created_by=created_by,
                        meta=payload,
                        created_at=now,
                        updated_at=now,
                        last_pushed_at=None,
                    )
                )

        return self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    def get_external_issue_link(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.external_issue_links).where(
                    and_(
                        self.external_issue_links.c.id == link_id,
                        self.external_issue_links.c.workspace_id == workspace_id,
                        self.external_issue_links.c.conversation_id == conversation_id,
                    )
                )
            ).first()
        if not row:
            raise KeyError(link_id)
        return self._external_link_row(row)

    def unlink_external_issue(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        row = self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.external_issue_links).where(
                    and_(
                        self.external_issue_links.c.id == link_id,
                        self.external_issue_links.c.workspace_id == workspace_id,
                        self.external_issue_links.c.conversation_id == conversation_id,
                    )
                )
            )
        return row

    def mark_external_issue_pushed(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
        push_kind: str,
    ) -> dict[str, Any]:
        self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(
                update(self.external_issue_links)
                .where(
                    and_(
                        self.external_issue_links.c.id == link_id,
                        self.external_issue_links.c.workspace_id == workspace_id,
                        self.external_issue_links.c.conversation_id == conversation_id,
                    )
                )
                .values(sync_state=str(push_kind or "pushed")[:32], updated_at=now, last_pushed_at=now)
            )
        return self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    def delete_conversation(self, conversation_id: str, **kwargs):
        workspace_id = str(kwargs.get("workspace_id") or "")
        result = super().delete_conversation(conversation_id, **kwargs)
        if workspace_id:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.external_issue_links).where(
                        and_(
                            self.external_issue_links.c.workspace_id == workspace_id,
                            self.external_issue_links.c.conversation_id == conversation_id,
                        )
                    )
                )
        return result
