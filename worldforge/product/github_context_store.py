from __future__ import annotations

import json
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
    UniqueConstraint,
    and_,
    delete,
    insert,
    or_,
    select,
    update,
)

from .github_push_store import ConversationStore as _GitHubPushConversationStore

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ConversationStore(_GitHubPushConversationStore):
    """External workflow store with durable, queryable GitHub PR/commit context.

    Display-oriented context remains in linkage metadata, while the routing table below keeps
    repository + commit identity indexed so a future CI webhook can find affected Lingjing tasks
    without scanning JSON blobs.
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
        self.github_code_contexts = Table(
            "github_code_contexts",
            self.metadata,
            Column("link_id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=False),
            Column("repository", String(240), nullable=False),
            Column("pull_request_number", Integer, nullable=True),
            Column("head_sha", String(40), nullable=True),
            Column("head_ref", String(240), nullable=True),
            Column("base_sha", String(40), nullable=True),
            Column("base_ref", String(240), nullable=True),
            Column("selected_commit_sha", String(40), nullable=True),
            Column("selected_commit_url", String(2000), nullable=True),
            Column("selected_commit_message", String(240), nullable=True),
            Column("updated_at", Float, nullable=False),
            UniqueConstraint("link_id", name="uq_github_code_context_link"),
        )
        Index(
            "ix_github_code_contexts_workspace_conversation",
            self.github_code_contexts.c.workspace_id,
            self.github_code_contexts.c.conversation_id,
        )
        Index(
            "ix_github_code_contexts_repository_head_sha",
            self.github_code_contexts.c.repository,
            self.github_code_contexts.c.head_sha,
        )
        Index(
            "ix_github_code_contexts_repository_selected_sha",
            self.github_code_contexts.c.repository,
            self.github_code_contexts.c.selected_commit_sha,
        )
        if auto_create_schema:
            self.metadata.create_all(self.engine, tables=[self.github_code_contexts])

    @staticmethod
    def _full_sha(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if not _FULL_SHA_RE.fullmatch(normalized):
            raise ValueError("GitHub commit SHA 必须是完整 40 位十六进制")
        return normalized

    def _routing_values(
        self,
        *,
        link: dict[str, Any],
        context: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        repository = self._normalize_github_repository(str(link.get("repository") or ""))
        pull_request = dict(context.get("pull_request") or {})
        commit = dict(context.get("commit") or {})
        pull_request_number = pull_request.get("number")
        if pull_request_number is not None:
            pull_request_number = int(pull_request_number)
            if pull_request_number < 1:
                raise ValueError("GitHub PR number 必须大于 0")
        return {
            "link_id": str(link["id"]),
            "workspace_id": str(link["workspace_id"]),
            "conversation_id": str(link["conversation_id"]),
            "repository": repository,
            "pull_request_number": pull_request_number,
            "head_sha": self._full_sha(context.get("head_commit_sha")),
            "head_ref": str(pull_request.get("head_ref") or "")[:240] or None,
            "base_sha": self._full_sha(pull_request.get("base_sha")),
            "base_ref": str(pull_request.get("base_ref") or "")[:240] or None,
            "selected_commit_sha": self._full_sha(context.get("selected_commit_sha")),
            "selected_commit_url": str(commit.get("url") or "")[:2000] or None,
            "selected_commit_message": str(commit.get("message") or "")[:240] or None,
            "updated_at": now,
        }

    def record_github_code_context(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
        pull_request: dict[str, Any] | None = None,
        commit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not pull_request and not commit:
            raise ValueError("至少需要一个 GitHub PR 或 Commit 上下文")
        row = self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        if row.get("provider") != "github":
            raise ValueError("当前外部关联不是 GitHub")

        meta = dict(row.get("meta") or {})
        context = dict(meta.get("github_context") or {})
        if pull_request is not None:
            normalized_pr = dict(pull_request)
            head_sha = self._full_sha(normalized_pr.get("head_sha"))
            base_sha = self._full_sha(normalized_pr.get("base_sha"))
            if head_sha:
                normalized_pr["head_sha"] = head_sha
                context["head_commit_sha"] = head_sha
            if base_sha:
                normalized_pr["base_sha"] = base_sha
            context["pull_request"] = normalized_pr
        if commit is not None:
            normalized_commit = dict(commit)
            selected_sha = self._full_sha(normalized_commit.get("sha"))
            if not selected_sha:
                raise ValueError("GitHub Commit 缺少完整 SHA")
            normalized_commit["sha"] = selected_sha
            context["commit"] = normalized_commit
            context["selected_commit_sha"] = selected_sha
        meta["github_context"] = context
        safe_meta = self._safe_meta(meta)
        now = time.time()
        routing_values = self._routing_values(link=row, context=context, now=now)

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
                .values(meta=json.dumps(safe_meta, ensure_ascii=False), updated_at=now)
            )
            exists = connection.execute(
                select(self.github_code_contexts.c.link_id).where(
                    self.github_code_contexts.c.link_id == link_id
                )
            ).first()
            if exists:
                connection.execute(
                    update(self.github_code_contexts)
                    .where(self.github_code_contexts.c.link_id == link_id)
                    .values(**routing_values)
                )
            else:
                connection.execute(insert(self.github_code_contexts).values(**routing_values))

        return self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    def get_github_code_context_route(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.github_code_contexts).where(
                    and_(
                        self.github_code_contexts.c.link_id == link_id,
                        self.github_code_contexts.c.workspace_id == workspace_id,
                        self.github_code_contexts.c.conversation_id == conversation_id,
                    )
                )
            ).first()
        return dict(row._mapping) if row else None

    def find_github_commit_routes(
        self,
        *,
        repository: str,
        commit_sha: str,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        repository = self._normalize_github_repository(repository)
        sha = self._full_sha(commit_sha)
        if not sha:
            raise ValueError("GitHub commit SHA 不能为空")
        conditions = [
            self.github_code_contexts.c.repository == repository,
            or_(
                self.github_code_contexts.c.head_sha == sha,
                self.github_code_contexts.c.selected_commit_sha == sha,
            ),
        ]
        if workspace_id:
            conditions.append(self.github_code_contexts.c.workspace_id == workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.github_code_contexts)
                .where(and_(*conditions))
                .order_by(
                    self.github_code_contexts.c.workspace_id,
                    self.github_code_contexts.c.conversation_id,
                    self.github_code_contexts.c.link_id,
                )
            ).fetchall()
        return [dict(row._mapping) for row in rows]

    def unlink_external_issue(self, link_id: str, *, conversation_id: str, workspace_id: str):
        row = super().unlink_external_issue(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.github_code_contexts).where(
                    self.github_code_contexts.c.link_id == link_id
                )
            )
        return row

    def delete_conversation(self, conversation_id: str, **kwargs):
        workspace_id = str(kwargs.get("workspace_id") or "")
        result = super().delete_conversation(conversation_id, **kwargs)
        if workspace_id:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.github_code_contexts).where(
                        and_(
                            self.github_code_contexts.c.workspace_id == workspace_id,
                            self.github_code_contexts.c.conversation_id == conversation_id,
                        )
                    )
                )
        return result
