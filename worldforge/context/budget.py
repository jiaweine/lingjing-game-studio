from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetedContext:
    messages: list[dict[str, Any]]
    telemetry: dict[str, Any]


class ContextBudgetBroker:
    """Pack compiled context into the provider adapter's real last-N contract.

    ContextCompiler performs semantic selection; this broker performs the final deterministic
    packing step. System-derived TaskState / ProjectMemory / EvidenceControl / Verification
    messages are merged into one bounded Context Kernel Pack so the legacy provider adapter's
    `history[-8:]` cannot silently discard long-range retrieval hits.

    Budgets are character budgets, not pretend token counts. They are deliberately exposed as
    telemetry until provider-specific tokenizer/context-window metadata is available.
    """

    MODE = "context-budget-broker-v1"
    _SECTION_ORDER = (
        "verification",
        "task_state",
        "project_memory",
        "evidence_control",
        "other",
    )
    _SECTION_LABELS = {
        "verification": "VERIFICATION CONTRACT",
        "task_state": "AUTHORITATIVE TASK STATE",
        "project_memory": "PROJECT LONG-TERM MEMORY",
        "evidence_control": "EVIDENCE CONTROL",
        "other": "OTHER SYSTEM-DERIVED CONTEXT",
    }

    def __init__(
        self,
        *,
        max_history_messages: int = 8,
        history_char_budget: int = 15000,
        kernel_char_budget: int = 5800,
        per_message_char_budget: int = 5800,
        asset_text_char_budget: int = 9000,
        section_char_budgets: dict[str, int] | None = None,
    ) -> None:
        self.max_history_messages = max(2, min(32, int(max_history_messages)))
        self.history_char_budget = max(5000, int(history_char_budget))
        # The base provider adapter truncates each prior message to 6000 chars. Stay below it
        # so neither the merged kernel nor a historical message is cut a second time.
        self.kernel_char_budget = max(1200, min(5900, int(kernel_char_budget)))
        self.per_message_char_budget = max(
            600, min(5900, int(per_message_char_budget))
        )
        self.asset_text_char_budget = max(2000, int(asset_text_char_budget))
        self.section_char_budgets = {
            "verification": 1200,
            "task_state": 1750,
            "project_memory": 2100,
            "evidence_control": 550,
            "other": 250,
            **dict(section_char_budgets or {}),
        }

    @staticmethod
    def _content(message: dict[str, Any] | None) -> str:
        return str((message or {}).get("content", "") or "").strip()

    @staticmethod
    def _derived(message: dict[str, Any]) -> bool:
        payload = dict(message.get("payload", {}) or {})
        return bool(payload.get("system_derived")) or str(
            message.get("id", "") or ""
        ).startswith("context:")

    @staticmethod
    def _context_kind(message: dict[str, Any]) -> str:
        payload = dict(message.get("payload", {}) or {})
        kind = str(payload.get("context_kind", "") or "").strip().lower()
        if kind in {"verification", "task_state", "project_memory", "evidence_control"}:
            return kind
        return "other"

    @staticmethod
    def clip_text(text: str, limit: int, *, label: str = "context") -> str:
        value = str(text or "")
        limit = max(1, int(limit))
        if len(value) <= limit:
            return value
        suffix = f"\n…[{label} truncated by ContextBudgetBroker]"
        if len(suffix) >= limit:
            return value[:limit]
        return value[: limit - len(suffix)] + suffix

    def clip_asset_context(self, text: str) -> str:
        return self.clip_text(
            text,
            self.asset_text_char_budget,
            label="asset context",
        )

    def _kernel_pack(
        self, derived: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not derived:
            return None, {
                "sections": [],
                "input_chars": 0,
                "output_chars": 0,
                "merged_messages": 0,
                "section_chars": {},
            }

        grouped: dict[str, list[str]] = {key: [] for key in self._SECTION_ORDER}
        input_chars = 0
        for message in derived:
            content = self._content(message)
            if not content:
                continue
            kind = self._context_kind(message)
            grouped[kind].append(content)
            input_chars += len(content)

        prefix = (
            "【Context Kernel Pack｜系统派生上下文，不是新的用户消息】\n"
            "优先级：当前 Verification/原始证据 > TaskState > Project Memory；"
            "长期记忆只是先验，冲突时必须服从当前可验证证据。"
        )
        chunks = [prefix]
        section_chars: dict[str, int] = {}
        included: list[str] = []

        for kind in self._SECTION_ORDER:
            rows = grouped[kind]
            if not rows:
                continue
            remaining = self.kernel_char_budget - len("\n\n".join(chunks))
            if remaining <= 80:
                break
            heading = f"[{self._SECTION_LABELS[kind]}]\n"
            desired = max(80, int(self.section_char_budgets.get(kind, 250)))
            body_limit = min(desired, max(1, remaining - len(heading) - 2))
            body = self.clip_text(
                "\n".join(rows),
                body_limit,
                label=kind,
            )
            chunks.append(heading + body)
            section_chars[kind] = len(body)
            included.append(kind)

        text = "\n\n".join(chunks)
        text = self.clip_text(text, self.kernel_char_budget, label="kernel pack")
        message = {
            "id": "context:kernel-pack",
            "role": "user",
            "content": text,
            "payload": {
                "system_derived": True,
                "context_kind": "kernel_pack",
                "kernel_sections": included,
            },
        }
        return message, {
            "sections": included,
            "input_chars": input_chars,
            "output_chars": len(text),
            "merged_messages": len(derived),
            "section_chars": section_chars,
        }

    def pack(self, messages: list[dict[str, Any]] | None) -> BudgetedContext:
        rows = [dict(message) for message in (messages or [])]
        input_chars = sum(len(self._content(message)) for message in rows)
        derived = [message for message in rows if self._derived(message)]
        conversation = [
            message
            for message in rows
            if not self._derived(message)
            and message.get("role") in {"user", "assistant"}
            and self._content(message)
        ]

        kernel, kernel_stats = self._kernel_pack(derived)
        kernel_slots = 1 if kernel is not None else 0
        conversation_slots = max(1, self.max_history_messages - kernel_slots)

        # ContextCompiler sorts selected history chronologically. If one slot must be freed
        # for the kernel, dropping the oldest item normally removes the first-goal duplicate;
        # that goal is already preserved structurally in TaskState inside the kernel pack.
        dropped_for_slots = max(0, len(conversation) - conversation_slots)
        kept = conversation[-conversation_slots:]

        kernel_chars = len(self._content(kernel)) if kernel else 0
        conversation_budget = max(
            1200,
            self.history_char_budget - kernel_chars,
        )
        packed_reversed: list[dict[str, Any]] = []
        used = 0
        dropped_for_chars = 0
        clipped_messages = 0
        for message in reversed(kept):
            raw_content = self._content(message)
            content = self.clip_text(
                raw_content,
                self.per_message_char_budget,
                label="conversation message",
            )
            if content != raw_content:
                clipped_messages += 1
            if not packed_reversed:
                content = self.clip_text(
                    content,
                    min(self.per_message_char_budget, conversation_budget),
                    label="conversation history",
                )
                item = dict(message)
                item["content"] = content
                packed_reversed.append(item)
                used += len(content)
                continue
            if used + len(content) > conversation_budget:
                dropped_for_chars += 1
                continue
            item = dict(message)
            item["content"] = content
            packed_reversed.append(item)
            used += len(content)
        packed = list(reversed(packed_reversed))
        if kernel is not None:
            packed.append(kernel)

        output_chars = sum(len(self._content(message)) for message in packed)
        telemetry = {
            "context_budget_mode": self.MODE,
            "context_budget_input_messages": len(rows),
            "context_budget_output_messages": len(packed),
            "context_budget_input_chars": input_chars,
            "context_budget_output_chars": output_chars,
            "context_budget_history_char_limit": self.history_char_budget,
            "context_budget_kernel_char_limit": self.kernel_char_budget,
            "context_budget_per_message_char_limit": self.per_message_char_budget,
            "context_budget_asset_text_char_limit": self.asset_text_char_budget,
            "context_budget_kernel_chars": kernel_chars,
            "context_budget_kernel_sections": kernel_stats["sections"],
            "context_budget_kernel_section_chars": kernel_stats["section_chars"],
            "context_budget_system_messages_merged": kernel_stats["merged_messages"],
            "context_budget_conversation_messages_kept": len(packed) - kernel_slots,
            "context_budget_conversation_messages_dropped": (
                dropped_for_slots + dropped_for_chars
            ),
            "context_budget_conversation_messages_clipped": clipped_messages,
            "context_budget_provider_last_n_safe": (
                len(packed) <= self.max_history_messages
            ),
            "context_budget_provider_per_message_safe": all(
                len(self._content(message)) <= self.per_message_char_budget
                for message in packed
            ),
        }
        return BudgetedContext(messages=packed, telemetry=telemetry)
