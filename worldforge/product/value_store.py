from __future__ import annotations

import json
import statistics
import time
from typing import Any

from sqlalchemy import and_, select

from .store import ConversationStore as _BaseConversationStore


class ConversationStore(_BaseConversationStore):
    """Product store with customer-value metrics layered on existing durable data.

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
                .order_by(self.messages.c.created_at)
            ).fetchall()

        conversation_by_id = {str(row["id"]): row for row in conversations}
        currently_verified = {
            str(row["id"])
            for row in conversations
            if str(row.get("status") or "") == "verified"
        }

        first_verified_at: dict[str, float] = {}
        for row in feedback:
            conversation_id = str(row["conversation_id"])
            timestamp = float(row.get("updated_at") or row.get("created_at") or 0.0)
            previous = first_verified_at.get(conversation_id)
            if timestamp > 0 and (previous is None or timestamp < previous):
                first_verified_at[conversation_id] = timestamp

        verified_ids = currently_verified & set(first_verified_at)
        verified_durations: list[float] = []
        for conversation_id in verified_ids:
            created = float(conversation_by_id[conversation_id].get("created_at") or 0.0)
            verified = first_verified_at[conversation_id]
            if created > 0 and verified >= created:
                verified_durations.append(verified - created)

        provider_results = 0
        exact_token_results = 0
        estimated_input_tokens = 0
        exact_input_tokens = 0
        provider_keys: set[str] = set()
        provider_models: set[str] = set()
        for row in message_rows:
            try:
                payload = json.loads(row.payload or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
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

        metrics.update(
            {
                "verified_issue_count": len(verified_ids),
                "weekly_verified_issues": sum(
                    1
                    for conversation_id in verified_ids
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
