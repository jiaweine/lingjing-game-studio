from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
from typing import Any

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+#@-]{2,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")
_VERSION_RE = re.compile(
    r"(?i)\b(?:build|version|ver|v)\s*[:=#-]?\s*[A-Za-z0-9][A-Za-z0-9._+-]{1,31}\b"
    r"|版本\s*[:：=#-]?\s*[A-Za-z0-9][A-Za-z0-9._+-]{1,31}"
)

_CONSTRAINT_MARKERS = (
    "必须", "不要", "不能", "禁止", "务必", "保持", "只允许", "不得",
    "must", "do not", "don't", "never", "keep ", "only allow",
)
_DECISION_MARKERS = (
    "决定", "确定", "采用", "改为", "改成", "选择", "锁定", "定为",
    "decided", "choose", "chosen", "switch to", "use ",
)
_VERIFIED_MARKERS = (
    "已经确认", "已确认", "确认过", "已经验证", "已验证", "验证通过",
    "confirmed", "verified", "validated",
)
_QUESTION_MARKERS = ("待确认", "待验证", "需要确认", "需要验证", "还要确认", "还要验证")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _source_id(message: dict[str, Any], index: int) -> str:
    return str(message.get("id") or f"history:{index}")


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    return parts[:40]


def _tokens(text: str) -> Counter[str]:
    normalized = _normalize(text)
    out: Counter[str] = Counter(token.lower() for token in _ASCII_TOKEN_RE.findall(normalized))
    cjk = _CJK_RE.findall(normalized)
    out.update(cjk)
    out.update("".join(cjk[i:i + 2]) for i in range(len(cjk) - 1))
    return out


def _identifier_tokens(text: str) -> set[str]:
    identifiers = {match.group(0).lower() for match in _VERSION_RE.finditer(text)}
    identifiers.update(
        token.lower()
        for token in _ASCII_TOKEN_RE.findall(text)
        if any(ch.isdigit() for ch in token) or "_" in token or "/" in token or "." in token
    )
    return identifiers


@dataclass(frozen=True)
class ContextPacket:
    messages: list[dict[str, Any]]
    task_state: dict[str, Any]
    raw_history_messages: int
    selected_history_messages: int
    long_range_hits: int
    message_chars: int
    mode: str = "hybrid-context-compiler-v1"

    def render_task_state(self) -> str:
        state = self.task_state
        lines = [
            "以下是从持久任务历史编译出的结构化状态。约束/决定需要优先保持；"
            "只有“已确认事实”可视为用户明确确认，其他召回内容仍需结合当前证据判断。"
        ]
        goal = state.get("goal")
        if goal:
            lines.append(f"- 原始目标 [{goal['source']}]: {goal['text']}")
        for label, key in (
            ("约束", "constraints"),
            ("决定", "decisions"),
            ("已确认事实", "verified_facts"),
            ("待确认/待验证", "open_questions"),
            ("版本/Build 线索", "version_refs"),
        ):
            rows = state.get(key) or []
            if not rows:
                continue
            lines.append(f"- {label}:")
            for row in rows:
                lines.append(f"  · [{row['source']}] {row['text']}")
        return "\n".join(lines)

    def intent_text(self) -> str:
        state_text = self.render_task_state()
        selected = "\n".join(str(message.get("content", "")) for message in self.messages)
        return f"{state_text}\n{selected}"

    def stats(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "raw_history_messages": self.raw_history_messages,
            "selected_history_messages": self.selected_history_messages,
            "long_range_hits": self.long_range_hits,
            "message_chars": self.message_chars,
        }


class ContextCompiler:
    """Fast, dependency-free long-horizon context compiler.

    Raw messages remain the durable source of truth. This compiler derives a bounded
    task-state snapshot and retrieves high-value historical turns on each request.
    It deliberately avoids additional model calls on the hot path.
    """

    def __init__(
        self,
        *,
        recent_messages: int = 6,
        retrieved_messages: int = 6,
        message_char_budget: int = 12000,
        per_message_chars: int = 2600,
        state_items_per_kind: int = 8,
    ) -> None:
        self.recent_messages = max(2, recent_messages)
        self.retrieved_messages = max(0, retrieved_messages)
        self.message_char_budget = max(3000, message_char_budget)
        self.per_message_chars = max(600, per_message_chars)
        self.state_items_per_kind = max(2, state_items_per_kind)

    def compile(self, query: str, history: list[dict[str, Any]] | None) -> ContextPacket:
        rows = list(history or [])
        state = self._task_state(rows)
        if not rows:
            return ContextPacket([], state, 0, 0, 0, 0)

        first_user = next(
            (index for index, message in enumerate(rows) if message.get("role") == "user"),
            None,
        )
        recent_start = max(0, len(rows) - self.recent_messages)
        mandatory = set(range(recent_start, len(rows)))
        if first_user is not None:
            mandatory.add(first_user)

        query_tokens = _tokens(query)
        query_ids = _identifier_tokens(query)
        candidates: list[tuple[float, int]] = []
        for index, message in enumerate(rows):
            if index in mandatory:
                continue
            role = str(message.get("role", ""))
            content = str(message.get("content", "") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            score = self._score_message(
                query_tokens=query_tokens,
                query_ids=query_ids,
                content=content,
                role=role,
                age=len(rows) - 1 - index,
            )
            candidates.append((score, index))

        candidates.sort(reverse=True)
        retrieved = [
            index for score, index in candidates[: self.retrieved_messages]
            if score > 0.18
        ]

        priorities: dict[int, float] = {}
        if first_user is not None:
            priorities[first_user] = 100.0
        for index in range(recent_start, len(rows)):
            priorities[index] = max(priorities.get(index, 0.0), 80.0 + index / max(1, len(rows)))
        for rank, index in enumerate(retrieved):
            priorities[index] = max(priorities.get(index, 0.0), 60.0 - rank)

        chosen: list[int] = []
        used = 0
        for index, _priority in sorted(priorities.items(), key=lambda item: (-item[1], item[0])):
            message = rows[index]
            if message.get("role") not in {"user", "assistant"}:
                continue
            content = str(message.get("content", "") or "").strip()
            if not content:
                continue
            trimmed = content[: self.per_message_chars]
            cost = len(trimmed)
            if chosen and used + cost > self.message_char_budget:
                continue
            chosen.append(index)
            used += cost

        chosen.sort()
        selected: list[dict[str, Any]] = []
        for index in chosen:
            message = rows[index]
            selected.append(
                {
                    "id": _source_id(message, index),
                    "role": message.get("role"),
                    "content": str(message.get("content", "") or "")[: self.per_message_chars],
                    "_context_index": index,
                }
            )

        long_range = sum(
            1
            for index in chosen
            if index < recent_start and (first_user is None or index != first_user)
        )
        return ContextPacket(
            messages=selected,
            task_state=state,
            raw_history_messages=len(rows),
            selected_history_messages=len(selected),
            long_range_hits=long_range,
            message_chars=used,
        )

    def _score_message(
        self,
        *,
        query_tokens: Counter[str],
        query_ids: set[str],
        content: str,
        role: str,
        age: int,
    ) -> float:
        content_lower = _normalize(content)
        if query_tokens:
            lexical = sum(1 for token in query_tokens if token in content_lower) / max(1, len(query_tokens))
        else:
            lexical = 0.0
        identifier_hits = sum(1 for token in query_ids if token in content_lower)
        identifier_score = min(1.0, identifier_hits / max(1, len(query_ids))) if query_ids else 0.0
        recency = math.exp(-max(0, age) / 36.0)
        marker = 1.0 if self._has_any(content_lower, _CONSTRAINT_MARKERS + _DECISION_MARKERS + _VERIFIED_MARKERS) else 0.0
        return 0.62 * lexical + 0.22 * identifier_score + 0.06 * recency + 0.06 * marker + 0.04 * (1.0 if role == "user" else 0.0)

    def _task_state(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        goal = None
        buckets: dict[str, list[dict[str, str]]] = {
            "constraints": [],
            "decisions": [],
            "verified_facts": [],
            "open_questions": [],
            "version_refs": [],
        }
        seen = {key: set() for key in buckets}

        for index, message in enumerate(history):
            role = str(message.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content", "") or "")
            source = _source_id(message, index)
            if goal is None and role == "user" and content.strip():
                goal = {"text": content.strip()[:1200], "source": source}

            for sentence in _sentences(content[:12000]):
                normalized = _normalize(sentence)
                if not normalized:
                    continue
                if self._has_any(normalized, _CONSTRAINT_MARKERS):
                    self._append_unique(buckets, seen, "constraints", sentence, source)
                if self._has_any(normalized, _DECISION_MARKERS):
                    self._append_unique(buckets, seen, "decisions", sentence, source)
                if role == "user" and self._has_any(normalized, _VERIFIED_MARKERS):
                    self._append_unique(buckets, seen, "verified_facts", sentence, source)
                if (
                    sentence.rstrip().endswith(("?", "？"))
                    or self._has_any(normalized, _QUESTION_MARKERS)
                ):
                    self._append_unique(buckets, seen, "open_questions", sentence, source)
                for match in _VERSION_RE.finditer(sentence):
                    self._append_unique(
                        buckets, seen, "version_refs", match.group(0), source
                    )

        return {"goal": goal, **buckets}

    def _append_unique(
        self,
        buckets: dict[str, list[dict[str, str]]],
        seen: dict[str, set[str]],
        key: str,
        text: str,
        source: str,
    ) -> None:
        normalized = _normalize(text)
        if normalized in seen[key]:
            return
        seen[key].add(normalized)
        buckets[key].append({"text": text[:1200], "source": source})
        if len(buckets[key]) > self.state_items_per_kind:
            buckets[key].pop(0)

    @staticmethod
    def _has_any(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in markers)
