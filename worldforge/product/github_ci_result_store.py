from __future__ import annotations

import copy
from typing import Any

from .github_ci_store import ConversationStore as _GitHubCIConversationStore


class ConversationStore(_GitHubCIConversationStore):
    """Attach integration-sourced verification scope to the durable assistant result."""

    def complete_job_answer(
        self,
        job_id: str,
        *,
        workspace_id: str,
        content: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            job = self.get_job(job_id, workspace_id=workspace_id)
        except KeyError:
            return None
        job_payload = dict(job.get("payload") or {})
        ci_trigger = dict(job_payload.get("ci_trigger") or {})
        if ci_trigger:
            result_payload = copy.deepcopy(dict(payload or {}))
            context = dict(result_payload.get("context") or {})
            project_context = dict(job_payload.get("project_context") or {})
            scope = dict(project_context.get("scope") or {})
            verification_scope = {
                key: str(scope.get(key) or "").strip()
                for key in (
                    "build_ref",
                    "branch_ref",
                    "commit_ref",
                    "environment_ref",
                )
            }
            if not verification_scope["commit_ref"]:
                verification_scope["commit_ref"] = str(
                    ci_trigger.get("head_sha") or ""
                ).strip().lower()
            context["ci_trigger"] = ci_trigger
            context["verification_scope"] = verification_scope
            result_payload["context"] = context
            payload = result_payload
        return super().complete_job_answer(
            job_id,
            workspace_id=workspace_id,
            content=content,
            payload=payload,
        )
