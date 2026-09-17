from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import and_, update

from .external_link_store import ConversationStore as _ExternalLinkConversationStore


class ConversationStore(_ExternalLinkConversationStore):
    """External-link store extended with non-secret GitHub comment synchronization metadata."""

    def record_external_issue_push(
        self,
        link_id: str,
        *,
        conversation_id: str,
        workspace_id: str,
        push_kind: str,
        comment_id: int,
        comment_url: str,
    ) -> dict[str, Any]:
        if push_kind not in {"reproduction", "verification"}:
            raise ValueError("unsupported external issue push kind")
        row = self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        meta = dict(row.get("meta") or {})
        comments = dict(meta.get("github_comments") or {})
        comments[push_kind] = {
            "id": int(comment_id),
            "url": str(comment_url)[:2000],
        }
        meta["github_comments"] = comments
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
                    sync_state=f"{push_kind}_pushed",
                    meta=json.dumps(safe_meta, ensure_ascii=False),
                    updated_at=now,
                    last_pushed_at=now,
                )
            )
        return self.get_external_issue_link(
            link_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
