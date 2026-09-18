from __future__ import annotations

import json
import statistics
import time
from typing import Any

from sqlalchemy import and_, select

from .store import ConversationStore as _BaseConversationStore


class ConversationStore(_BaseConversationStore):
    """Product store with issue-verification semantics and customer-value metrics.

    Human feedback can confirm that an assistant result is correct, but that is not the same
    thing as proving the underlying game issue is fixed. Only outcomes explicitly marked as an
    issue lifecycle require verifier-authoritative project truth before the conversation can
    close as ``verified``. Non-issue workflows retain the existing human quality gate.

    Monetary cost is intentionally not inferred from model names or estimated tokens. The
    product only reports cost when an authoritative provider/billing source is eventually
    persisted. Until then the API makes the missing coverage explicit instead of manufacturing
    a cost-per-issue number.
    """

    @staticmethod
    def _provider_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
        context = dict(payload.get("context") or {})
        provider_key = context.get("provider_selected_key")
        provider_model = context.get("provider_selected_model")
        telemetry = dict(context.get("provider_native_token_telemetry") or {})
        if not provider_key and not provider_model and not telemetry:
            return None

        exact = telemetry.get("native_token_count_input_tokens")
        estimated = telemetry.get("native_token_estimated_text_tokens")
        try:
            exact_tokens = int(exact) if exact is not None else None
        except (TypeError, ValueError):
            exact_tokens = None
        try:
            estimated_tokens = int(estimated) if estimated is not None else None
        except (TypeError, ValueError):
            estimated_tokens = None
        return {
            "provider_key": provider_key,
            "provider_model": provider_model,
            "exact_input_tokens": exact_tokens if exact_tokens and exact_tokens > 0 else None,
            "estimated_input_tokens": (
                estimated_tokens if estimated_tokens and estimated_tokens > 0 else None
            ),
        }

    @staticmethod
    def _is_issue_lifecycle(outcome: dict[str, Any] | None) -> bool:
        if not outcome:
            return False
        return bool(
            outcome.get("issue_lifecycle")
            or outcome.get("requires_project_verification")
        )

    def _latest_structured_outcome(
        self, conversation_id: str, *, workspace_id: str
    ) -> dict[str, Any] | None:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.messages.c.payload)
                .select_from(
                    self.messages.join(
                        self.conversations,
                        self.messages.c.conversation_id == self.conversations.c.id,
                    )
                )
                .where(
                    and_(
                        self.conversations.c.workspace_id == workspace_id,
                        self.messages.c.conversation_id == conversation_id,
                        self.messages.c.role == "assistant",
                    )
                )
                .order_by(self.messages.c.created_at.desc(), self.messages.c.id.desc())
                .limit(1)
            ).first()
        if not row:
            return None
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        outcome = payload.get("outcome")
        return dict(outcome) if isinstance(outcome, dict) else None

    def feedback_gate(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:
        gate = dict(super().feedback_gate(conversation_id, workspace_id=workspace_id))
        outcome = self._latest_structured_outcome(
            conversation_id, workspace_id=workspace_id
        )
        if not gate.get("approved") or not self._is_issue_lifecycle(outcome):
            return gate
        if bool(outcome.get("verified")):
            return gate

        label = str(outcome.get("label") or "尚未验证")
        reason = str(outcome.get("reason") or "当前结果尚未形成项目级验证事实")
        gate.update(
            {
                "approved": False,
                "task_status": "review",
                "issue_outcome": outcome,
                "reason": (
                    f"人工已确认这条交付本身正确，但问题结论仍为“{label}”。"
                    f"{reason}"
                ),
            }
        )
        return gate

    def product_metrics(self, *, workspace_id: str) -> dict[str, Any]:
        metrics = dict(super().product_metrics(workspace_id=workspace_id))
        now = time.time()
        week_start = now - (7 * 24 * 3600)

        with self.engine.connect() as connection:
            conversations = [
                self._dict(row)
                for row in connection.execute(
                    select(self.conversations).where(
                        self.conversations.c.workspace_id == workspace_id
                    )
                ).fetchall()
            ]
            feedback = [
                self._dict(row)
                for row in connection.execute(
                    select(self.result_feedback).where(
                        and_(
                            self.result_feedback.c.workspace_id == workspace_id,
                            self.result_feedback.c.human_verified == 1,
                            self.result_feedback.c.verdict == "correct",
                        )
                    )
                ).fetchall()
            ]
            message_rows = connection.execute(
                select(self.messages)
                .select_from(
                    self.messages.join(
                        self.conversations,
                        self.messages.c.conversation_id == self.conversations.c.id,
                    )
                )
                .where(
                    and_(
                        self.conversations.c.workspace_id == workspace_id,
                        self.messages.c.role == "assistant",
                    )
                )
                .order_by(self.messages.c.created_at, self.messages.c.id)
            ).fetchall()

        conversation_by_id = {str(row["id"]): row for row in conversations}
        currently_verified = {
            str(row["id"])
            for row in conversations
            if str(row.get("status") or "") == "verified"
        }

        provider_results = 0
        exact_token_results = 0
        estimated_input_tokens = 0
        exact_input_tokens = 0
        provider_keys: set[str] = set()
        provider_models: set[str] = set()
        latest_outcomes: dict[str, dict[str, Any]] = {}
        latest_outcome_message_ids: dict[str, str] = {}
        for row in message_rows:
            try:
                payload = json.loads(row.payload or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            outcome = payload.get("outcome")
            if isinstance(outcome, dict):
                conversation_id = str(row.conversation_id)
                latest_outcomes[conversation_id] = dict(outcome)
                latest_outcome_message_ids[conversation_id] = str(row.id)

            usage = self._provider_usage(payload)
            if usage is None:
                continue
            provider_results += 1
            if usage["provider_key"]:
                provider_keys.add(str(usage["provider_key"]))
            if usage["provider_model"]:
                provider_models.add(str(usage["provider_model"]))
            if usage["estimated_input_tokens"] is not None:
                estimated_input_tokens += int(usage["estimated_input_tokens"])
            if usage["exact_input_tokens"] is not None:
                exact_input_tokens += int(usage["exact_input_tokens"])
                exact_token_results += 1

        authoritative_issue_ids = {
            conversation_id
            for conversation_id in currently_verified
            if self._is_issue_lifecycle(latest_outcomes.get(conversation_id))
            and bool(latest_outcomes[conversation_id].get("verified"))
        }

        # A previous "human verified" click may only mean that an earlier insufficient-evidence
        # answer was correctly cautious. Time-to-Verified-Issue must therefore be anchored to
        # the human confirmation of the *current verifier-authoritative issue result*, not the
        # earliest confirmation on any historical assistant message in the conversation.
        first_verified_at: dict[str, float] = {}
        for row in feedback:
            conversation_id = str(row["conversation_id"])
            if conversation_id not in authoritative_issue_ids:
                continue
            if str(row["message_id"]) != latest_outcome_message_ids.get(conversation_id):
                continue
            timestamp = float(row.get("updated_at") or row.get("created_at") or 0.0)
            previous = first_verified_at.get(conversation_id)
            if timestamp > 0 and (previous is None or timestamp < previous):
                first_verified_at[conversation_id] = timestamp

        verified_issue_ids = authoritative_issue_ids & set(first_verified_at)
        verified_durations: list[float] = []
        for conversation_id in verified_issue_ids:
            created = float(conversation_by_id[conversation_id].get("created_at") or 0.0)
            verified = first_verified_at[conversation_id]
            if created > 0 and verified >= created:
                verified_durations.append(verified - created)

        metrics.update(
            {
                "verified_issue_count": len(verified_issue_ids),
                "weekly_verified_issues": sum(
                    1
                    for conversation_id in verified_issue_ids
                    if first_verified_at[conversation_id] >= week_start
                ),
                "median_time_to_verified_issue_seconds": (
                    round(float(statistics.median(verified_durations)), 2)
                    if verified_durations
                    else None
                ),
                "provider_usage_result_count": provider_results,
                "provider_usage_exact_token_coverage_rate": (
                    round(exact_token_results / provider_results, 4)
                    if provider_results
                    else 0.0
                ),
                "provider_estimated_input_tokens": estimated_input_tokens,
                "provider_exact_input_tokens": exact_input_tokens,
                "provider_keys_observed": sorted(provider_keys),
                "provider_models_observed": sorted(provider_models),
                "cost_available": False,
                "total_provider_cost_usd": None,
                "cost_per_verified_issue_usd": None,
                "cost_unavailable_reason": (
                    "No authoritative provider billing/cost record is persisted; token telemetry "
                    "is not converted into money using guessed model pricing."
                ),
            }
        )
        return metrics
