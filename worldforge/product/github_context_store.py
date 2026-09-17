from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import and_, update

from .github_push_store import ConversationStore as _GitHubPushConversationStore


class ConversationStore(_GitHubPushConversationStore):
    """External workflow store with non-secret GitHub PR/commit context."""

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
            context["pull_request"] = dict(pull_request)
            head_sha = str(pull_request.get("head_sha") or "").strip().lower()
            if head_sha:
                context["head_commit_sha"] = head_sha
        if commit is not None:
            context["commit"] = dict(commit)
            context["selected_commit_sha"] = str(commit.get("sha") or "").strip().lower()
        meta["github_context"] = context
        safe_meta = self._safe_meta(meta)
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
                .values(
                    meta=json.dumps(safe_meta, ensure_ascii=False),
                    updated_at=now,
                )
            )
        return self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
