from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from worldforge.providers.context_budget import (
    estimate_text_tokens,
    load_provider_context_budget,
)

from .budget import BudgetedContext, ContextBudgetBroker

_MESSAGE_OVERHEAD_TOKENS = 4
_MIN_MESSAGE_TOKENS = 52
_SECTION_WEIGHTS = {
    "verification": 5,
    "task_state": 4,
    "project_memory": 2,
    "evidence_control": 1,
    "other": 1,
}


@dataclass(frozen=True)
class _RequestProviderHint:
    key: str
    model: str | None = None


def _content(message: dict[str, Any] | None) -> str:
    return str((message or {}).get("content", "") or "")


def _message_tokens(message: dict[str, Any]) -> int:
    return estimate_text_tokens(_content(message)) + _MESSAGE_OVERHEAD_TOKENS


def _clip_text_tokens(text: str, token_limit: int, *, label: str) -> str:
    value = str(text or "")
    token_limit = max(1, int(token_limit))
    if estimate_text_tokens(value) <= token_limit:
        return value

    suffix = f"\n…[{label} truncated by provider token budget]"
    if estimate_text_tokens(suffix) >= token_limit:
        suffix = "…"
    low, high = 0, len(value)
    best = suffix if estimate_text_tokens(suffix) <= token_limit else ""
    while low <= high:
        middle = (low + high) // 2
        candidate = value[:middle] + suffix
        if estimate_text_tokens(candidate) <= token_limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _clip_message_tokens(
    message: dict[str, Any],
    token_limit: int,
    *,
    label: str,
) -> dict[str, Any]:
    item = dict(message)
    payload_limit = max(1, int(token_limit) - _MESSAGE_OVERHEAD_TOKENS)
    item["content"] = _clip_text_tokens(
        _content(item),
        payload_limit,
        label=label,
    )
    return item


def _fit_conversation_messages(
    messages: list[dict[str, Any]],
    *,
    token_budget: int,
    per_message_budget: int,
) -> tuple[list[dict[str, Any]], int]:
    """Fit conversation rows while preserving one old selected hit plus recent turns."""
    rows = [dict(message) for message in messages]
    if not rows or token_budget <= 0:
        return [], len(rows)

    per_message_budget = max(
        _MIN_MESSAGE_TOKENS,
        min(int(per_message_budget), int(token_budget)),
    )
    desired = [min(_message_tokens(row), per_message_budget) for row in rows]
    if sum(desired) <= token_budget:
        return rows, 0

    max_count = max(1, token_budget // _MIN_MESSAGE_TOKENS)
    dropped = 0
    if len(rows) > max_count:
        dropped = len(rows) - max_count
        if max_count == 1:
            rows = [rows[-1]]
        else:
            # ContextCompiler's chronological selection puts any retained long-range hit near
            # the front and recent turns at the back. Keep both ends rather than degenerating
            # into another pure last-N window under token pressure.
            rows = [rows[0], *rows[-(max_count - 1):]]
        desired = [min(_message_tokens(row), per_message_budget) for row in rows]

    quotas = [min(value, _MIN_MESSAGE_TOKENS) for value in desired]
    while sum(quotas) > token_budget and len(rows) > 1:
        rows.pop(0)
        desired.pop(0)
        quotas.pop(0)
        dropped += 1

    remaining = max(0, token_budget - sum(quotas))
    while remaining > 0:
        needy = [index for index, quota in enumerate(quotas) if quota < desired[index]]
        if not needy:
            break
        share = max(1, remaining // len(needy))
        consumed = 0
        for index in needy:
            add = min(share, desired[index] - quotas[index], remaining - consumed)
            if add <= 0:
                continue
            quotas[index] += add
            consumed += add
            if consumed >= remaining:
                break
        if consumed <= 0:
            break
        remaining -= consumed

    packed = [
        _clip_message_tokens(row, quota, label="conversation message")
        for row, quota in zip(rows, quotas)
    ]
    while packed and sum(_message_tokens(row) for row in packed) > token_budget:
        if len(packed) == 1:
            packed[0] = _clip_message_tokens(
                packed[0], token_budget, label="conversation history"
            )
            break
        packed.pop(0)
        dropped += 1
    return packed, dropped


class ProviderAwareContextBudgetBroker(ContextBudgetBroker):
    """Provider-aware token guard layered on the deterministic character broker.

    The existing character pack remains the universal compatibility floor. When the request's
    provider has an operator-declared token profile, this class additionally enforces a history
    token budget using a dependency-free multilingual estimator. Unknown models/providers stay
    on the character path and are marked as such in telemetry rather than receiving guessed
    vendor limits.
    """

    MODE = "provider-aware-context-budget-v2"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._request_provider: ContextVar[_RequestProviderHint] = ContextVar(
            f"lingjing_context_budget_provider_{id(self)}",
            default=_RequestProviderHint("auto", None),
        )

    def bind_provider(
        self,
        provider_key: str | None,
        *,
        model: str | None = None,
    ) -> Token:
        return self._request_provider.set(
            _RequestProviderHint(str(provider_key or "auto"), model)
        )

    def reset_provider(self, token: Token) -> None:
        self._request_provider.reset(token)

    def _split_kernel_sections(
        self,
        content: str,
    ) -> tuple[str, list[tuple[str, str, str]]]:
        markers: list[tuple[int, str, str]] = []
        for kind in self._SECTION_ORDER:
            heading = f"[{self._SECTION_LABELS[kind]}]\n"
            position = content.find(heading)
            if position >= 0:
                markers.append((position, kind, heading))
        markers.sort()
        if not markers:
            return content, []

        prefix = content[: markers[0][0]].rstrip()
        sections: list[tuple[str, str, str]] = []
        for index, (position, kind, heading) in enumerate(markers):
            body_start = position + len(heading)
            body_end = markers[index + 1][0] if index + 1 < len(markers) else len(content)
            sections.append((kind, heading, content[body_start:body_end].strip()))
        return prefix, sections

    def _clip_kernel_tokens(
        self,
        kernel: dict[str, Any],
        token_limit: int,
    ) -> dict[str, Any]:
        if _message_tokens(kernel) <= token_limit:
            return dict(kernel)

        payload_limit = max(1, token_limit - _MESSAGE_OVERHEAD_TOKENS)
        prefix, sections = self._split_kernel_sections(_content(kernel))
        if not sections:
            return _clip_message_tokens(kernel, token_limit, label="context kernel")

        prefix_cap = max(36, min(120, int(payload_limit * 0.20)))
        clipped_prefix = _clip_text_tokens(prefix, prefix_cap, label="kernel preamble")
        prefix_tokens = estimate_text_tokens(clipped_prefix)
        remaining = max(0, payload_limit - prefix_tokens)

        active_sections = list(sections)
        # Every surviving section gets heading + a small body floor. If that cannot fit,
        # discard low-priority sections from the tail before touching Verification/TaskState.
        while active_sections:
            heading_tokens = sum(
                estimate_text_tokens(heading) for _kind, heading, _body in active_sections
            )
            body_room = remaining - heading_tokens
            if body_room >= 16 * len(active_sections):
                break
            active_sections.pop()
        if not active_sections:
            item = dict(kernel)
            item["content"] = clipped_prefix
            return item

        heading_tokens = sum(
            estimate_text_tokens(heading) for _kind, heading, _body in active_sections
        )
        body_budget = max(0, remaining - heading_tokens)
        floor = min(32, max(8, body_budget // max(1, len(active_sections) * 3)))
        quotas = [floor for _ in active_sections]
        leftover = max(0, body_budget - sum(quotas))

        while leftover > 0:
            expandable = [
                index
                for index, (_kind, _heading, body) in enumerate(active_sections)
                if quotas[index] < estimate_text_tokens(body)
            ]
            if not expandable:
                break
            weight_sum = sum(
                _SECTION_WEIGHTS.get(active_sections[index][0], 1)
                for index in expandable
            )
            consumed = 0
            for index in expandable:
                kind, _heading, body = active_sections[index]
                desired = estimate_text_tokens(body)
                weight = _SECTION_WEIGHTS.get(kind, 1)
                share = max(1, int(leftover * weight / max(1, weight_sum)))
                add = min(share, desired - quotas[index], leftover - consumed)
                if add <= 0:
                    continue
                quotas[index] += add
                consumed += add
                if consumed >= leftover:
                    break
            if consumed <= 0:
                break
            leftover -= consumed

        chunks = [clipped_prefix] if clipped_prefix else []
        for (kind, heading, body), quota in zip(active_sections, quotas):
            clipped = _clip_text_tokens(body, quota, label=kind)
            chunks.append(heading + clipped)
        rebuilt = "\n\n".join(chunks)
        if estimate_text_tokens(rebuilt) > payload_limit:
            rebuilt = _clip_text_tokens(rebuilt, payload_limit, label="context kernel")
        item = dict(kernel)
        item["content"] = rebuilt
        return item

    def pack(self, messages: list[dict[str, Any]] | None) -> BudgetedContext:
        char_stage = super().pack(messages)
        hint = self._request_provider.get()
        profile = load_provider_context_budget(hint.key, model=hint.model)
        char_messages = [dict(message) for message in char_stage.messages]
        char_tokens = sum(_message_tokens(message) for message in char_messages)

        telemetry = dict(char_stage.telemetry)
        telemetry.update(
            {
                "context_budget_char_stage_output_messages": len(char_messages),
                "context_budget_char_stage_output_chars": sum(
                    len(_content(message)) for message in char_messages
                ),
                "context_budget_requested_provider_key": hint.key,
                "context_budget_requested_provider_model": hint.model,
                "context_budget_token_estimator": profile.token_estimator,
                "context_budget_context_window_tokens": profile.context_window_tokens,
                "context_budget_output_reserve_tokens": profile.output_reserve_tokens,
                "context_budget_history_token_limit": profile.history_budget_tokens,
                "context_budget_token_profile_source": profile.source,
                "context_budget_char_stage_estimated_tokens": char_tokens,
            }
        )

        if not profile.enabled:
            telemetry.update(
                {
                    "context_budget_mode": self.MODE,
                    "context_budget_unit": "chars",
                    "context_budget_token_mode": "fallback-char-unconfigured-provider",
                    "context_budget_token_fallback": True,
                    "context_budget_output_estimated_tokens": char_tokens,
                    "context_budget_token_limit_safe": None,
                }
            )
            return BudgetedContext(messages=char_messages, telemetry=telemetry)

        limit = int(profile.history_budget_tokens or 0)
        kernel = next(
            (
                message
                for message in char_messages
                if str(message.get("id", "")) == "context:kernel-pack"
            ),
            None,
        )
        conversation = [
            message
            for message in char_messages
            if str(message.get("id", "")) != "context:kernel-pack"
        ]

        packed_kernel: dict[str, Any] | None = None
        kernel_tokens = 0
        if kernel is not None:
            kernel_limit = min(
                limit,
                int(profile.kernel_budget_tokens or max(160, int(limit * 0.48))),
            )
            packed_kernel = self._clip_kernel_tokens(kernel, kernel_limit)
            kernel_tokens = _message_tokens(packed_kernel)

        conversation_budget = max(0, limit - kernel_tokens)
        per_message_budget = int(
            profile.per_message_budget_tokens
            or max(_MIN_MESSAGE_TOKENS, int(limit * 0.40))
        )
        packed_conversation, token_dropped = _fit_conversation_messages(
            conversation,
            token_budget=conversation_budget,
            per_message_budget=per_message_budget,
        )
        packed = list(packed_conversation)
        if packed_kernel is not None:
            packed.append(packed_kernel)

        output_tokens = sum(_message_tokens(message) for message in packed)
        output_chars = sum(len(_content(message)) for message in packed)
        telemetry.update(
            {
                "context_budget_mode": self.MODE,
                "context_budget_unit": "estimated_tokens",
                "context_budget_token_mode": "provider-profile-enforced",
                "context_budget_token_fallback": False,
                "context_budget_output_messages": len(packed),
                "context_budget_output_chars": output_chars,
                "context_budget_output_estimated_tokens": output_tokens,
                "context_budget_token_conversation_messages_dropped": token_dropped,
                "context_budget_token_limit_safe": output_tokens <= limit,
                "context_budget_provider_last_n_safe": len(packed) <= self.max_history_messages,
            }
        )
        return BudgetedContext(messages=packed, telemetry=telemetry)
