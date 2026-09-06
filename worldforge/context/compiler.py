from __future__ import annotations

from collections import OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import math
import re
from threading import RLock
from typing import Any

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
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
_QUESTION_MARKERS = (
    "待确认", "待验证", "需要确认", "需要验证", "还要确认", "还要验证"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _source_id(message: dict[str, Any], index: int) -> str:
    return str(message.get("id") or f"history:{index}")


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    return [
        part.strip()
        for part in _SENTENCE_SPLIT_RE.split(text)
        if part.strip()
    ][:40]


def _search_tokens(text: str) -> set[str]:
    """Cheap multilingual search tokens optimized for repeated retrieval."""
    normalized = _normalize(text)
    out = {token.lower() for token in _ASCII_TOKEN_RE.findall(normalized)}
    for run in _CJK_RUN_RE.findall(normalized):
        if len(run) == 1:
            out.add(run)
            continue
        out.update(run[index:index + 2] for index in range(len(run) - 1))
        if len(run) <= 6:
            out.add(run)
    return out


def _identifier_tokens(text: str) -> set[str]:
    identifiers = {match.group(0).lower() for match in _VERSION_RE.finditer(text)}
    identifiers.update(
        token.lower()
        for token in _ASCII_TOKEN_RE.findall(text)
        if any(ch.isdigit() for ch in token)
        or "_" in token
        or "/" in token
        or "." in token
    )
    return identifiers


@dataclass(frozen=True)
class ContextPacket:
    messages: list[dict[str, Any]]
    task_state: dict[str, Any]
    raw_history_messages: int
    selected_history_messages: int
    candidate_history_messages: int
    long_range_hits: int
    message_chars: int
    state_cache_hit: bool = False
    retrieval_cache_hit: bool = False
    mode: str = "indexed-hybrid-context-compiler-v2"

    def render_task_state(self) -> str:
        state = self.task_state
        lines = [
            "以下是从持久任务历史编译出的结构化状态。用户约束/决定需要优先保持；"
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
        selected = "\n".join(
            str(message.get("content", "")) for message in self.messages
        )
        return f"{self.render_task_state()}\n{selected}"

    def stats(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "raw_history_messages": self.raw_history_messages,
            "selected_history_messages": self.selected_history_messages,
            "candidate_history_messages": self.candidate_history_messages,
            "long_range_hits": self.long_range_hits,
            "message_chars": self.message_chars,
            "state_cache_hit": self.state_cache_hit,
            "retrieval_cache_hit": self.retrieval_cache_hit,
        }


class ContextCompiler:
    """Bounded long-horizon context compiler with incremental in-process indexes.

    Raw persisted messages remain the source of truth. The compiler maintains only
    rebuildable derived caches: a user-governed task-state snapshot and an inverted
    token index. Normal turns therefore avoid both additional model calls and O(T)
    rescans as conversations become long; a worker restart simply rebuilds caches.
    """

    def __init__(
        self,
        *,
        recent_messages: int = 6,
        retrieved_messages: int = 6,
        message_char_budget: int = 12000,
        per_message_chars: int = 2600,
        state_items_per_kind: int = 8,
        cache_limit: int = 128,
    ) -> None:
        self.recent_messages = max(2, recent_messages)
        self.retrieved_messages = max(0, retrieved_messages)
        self.message_char_budget = max(3000, message_char_budget)
        self.per_message_chars = max(600, per_message_chars)
        self.state_items_per_kind = max(2, state_items_per_kind)
        self.cache_limit = max(8, cache_limit)
        self._cache_lock = RLock()
        self._state_cache: OrderedDict[
            str, tuple[int, str, dict[str, Any]]
        ] = OrderedDict()
        self._retrieval_cache: OrderedDict[
            str, tuple[int, str, dict[str, list[int]]]
        ] = OrderedDict()

    def compile(
        self,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> ContextPacket:
        rows = list(history or [])
        state, state_cache_hit = self._task_state(rows)
        if not rows:
            return ContextPacket(
                [], state, 0, 0, 0, 0, 0,
                state_cache_hit=state_cache_hit,
                retrieval_cache_hit=False,
            )

        first_user = next(
            (
                index
                for index, message in enumerate(rows)
                if message.get("role") == "user"
            ),
            None,
        )
        recent_start = max(0, len(rows) - self.recent_messages)
        mandatory = set(range(recent_start, len(rows)))
        if first_user is not None:
            mandatory.add(first_user)

        query_tokens = _search_tokens(query)
        query_ids = _identifier_tokens(query)
        candidate_indices, retrieval_cache_hit = self._candidate_indices(
            rows, query_tokens
        )
        candidates: list[tuple[float, int]] = []
        for index in candidate_indices:
            if index in mandatory or index < 0 or index >= len(rows):
                continue
            message = rows[index]
            role = str(message.get("role", ""))
            content = str(message.get("content", "") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            candidates.append(
                (
                    self._score_message(
                        query_tokens=query_tokens,
                        query_ids=query_ids,
                        content=content,
                        role=role,
                        age=len(rows) - 1 - index,
                    ),
                    index,
                )
            )

        candidates.sort(reverse=True)
        retrieved = [
            index
            for score, index in candidates[: self.retrieved_messages]
            if score > 0.18
        ]

        priorities: dict[int, float] = {}
        if first_user is not None:
            priorities[first_user] = 100.0
        for index in range(recent_start, len(rows)):
            priorities[index] = max(
                priorities.get(index, 0.0),
                80.0 + index / max(1, len(rows)),
            )
        for rank, index in enumerate(retrieved):
            priorities[index] = max(
                priorities.get(index, 0.0),
                60.0 - rank,
            )

        chosen: list[int] = []
        used = 0
        for index, _priority in sorted(
            priorities.items(), key=lambda item: (-item[1], item[0])
        ):
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
        selected = [
            {
                "id": _source_id(rows[index], index),
                "role": rows[index].get("role"),
                "content": str(rows[index].get("content", "") or "")[
                    : self.per_message_chars
                ],
                "_context_index": index,
            }
            for index in chosen
        ]
        long_range = sum(
            1
            for index in chosen
            if index < recent_start
            and (first_user is None or index != first_user)
        )
        return ContextPacket(
            messages=selected,
            task_state=state,
            raw_history_messages=len(rows),
            selected_history_messages=len(selected),
            candidate_history_messages=len(candidate_indices),
            long_range_hits=long_range,
            message_chars=used,
            state_cache_hit=state_cache_hit,
            retrieval_cache_hit=retrieval_cache_hit,
        )

    def _candidate_indices(
        self,
        history: list[dict[str, Any]],
        query_tokens: set[str],
    ) -> tuple[set[int], bool]:
        if not query_tokens:
            return set(), False
        cache_key = self._cache_key(history)
        if not cache_key:
            return {
                index
                for index, message in enumerate(history)
                if message.get("role") in {"user", "assistant"}
                and _search_tokens(str(message.get("content", "") or ""))
                & query_tokens
            }, False

        with self._cache_lock:
            cached = self._retrieval_cache.get(cache_key)
            cache_hit = False
            if cached and self._prefix_valid(cached[0], cached[1], history):
                processed, _last_source, cached_postings = cached
                postings = defaultdict(list, cached_postings)
                cache_hit = True
                self._retrieval_cache.move_to_end(cache_key)
            else:
                processed = 0
                postings = defaultdict(list)
                self._retrieval_cache.pop(cache_key, None)

            for index in range(processed, len(history)):
                message = history[index]
                if message.get("role") not in {"user", "assistant"}:
                    continue
                content = str(message.get("content", "") or "")
                for token in _search_tokens(content):
                    postings[token].append(index)

            frozen_postings = dict(postings)
            self._retrieval_cache[cache_key] = (
                len(history),
                _source_id(history[-1], len(history) - 1),
                frozen_postings,
            )
            self._retrieval_cache.move_to_end(cache_key)
            self._trim_cache(self._retrieval_cache)

            candidates: set[int] = set()
            for token in query_tokens:
                candidates.update(frozen_postings.get(token, ()))
            return candidates, cache_hit

    def _score_message(
        self,
        *,
        query_tokens: set[str],
        query_ids: set[str],
        content: str,
        role: str,
        age: int,
    ) -> float:
        content_lower = _normalize(content)
        lexical = (
            sum(1 for token in query_tokens if token in content_lower)
            / max(1, len(query_tokens))
            if query_tokens
            else 0.0
        )
        identifier_hits = sum(
            1 for token in query_ids if token in content_lower
        )
        identifier_score = (
            min(1.0, identifier_hits / max(1, len(query_ids)))
            if query_ids
            else 0.0
        )
        recency = math.exp(-max(0, age) / 36.0)
        marker = (
            1.0
            if self._has_any(
                content_lower,
                _CONSTRAINT_MARKERS
                + _DECISION_MARKERS
                + _VERIFIED_MARKERS,
            )
            else 0.0
        )
        return (
            0.62 * lexical
            + 0.22 * identifier_score
            + 0.06 * recency
            + 0.06 * marker
            + 0.04 * (1.0 if role == "user" else 0.0)
        )

    def _task_state(
        self,
        history: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        if not history:
            return self._empty_state(), False

        cache_key = self._cache_key(history)
        start_index = 0
        state = self._empty_state()
        cache_hit = False
        if cache_key:
            with self._cache_lock:
                cached = self._state_cache.get(cache_key)
                if cached and self._prefix_valid(
                    cached[0], cached[1], history
                ):
                    start_index = cached[0]
                    state = deepcopy(cached[2])
                    cache_hit = True
                    self._state_cache.move_to_end(cache_key)
                else:
                    self._state_cache.pop(cache_key, None)

        keys = (
            "constraints",
            "decisions",
            "verified_facts",
            "open_questions",
            "version_refs",
        )
        seen = {
            key: {_normalize(row["text"]) for row in state.get(key, [])}
            for key in keys
        }
        buckets = {key: state[key] for key in keys}
        goal = state.get("goal")

        for index in range(start_index, len(history)):
            message = history[index]
            # Durable task state is user-governed. Assistant conclusions remain
            # retrievable history, but cannot silently become constraints or facts.
            if message.get("role") != "user":
                continue
            content = str(message.get("content", "") or "")
            source = _source_id(message, index)
            if goal is None and content.strip():
                goal = {
                    "text": content.strip()[:1200],
                    "source": source,
                }

            for sentence in _sentences(content[:12000]):
                normalized = _normalize(sentence)
                if not normalized:
                    continue
                if self._has_any(normalized, _CONSTRAINT_MARKERS):
                    self._append_unique(
                        buckets, seen, "constraints", sentence, source
                    )
                if self._has_any(normalized, _DECISION_MARKERS):
                    self._append_unique(
                        buckets, seen, "decisions", sentence, source
                    )
                if self._has_any(normalized, _VERIFIED_MARKERS):
                    self._append_unique(
                        buckets, seen, "verified_facts", sentence, source
                    )
                if (
                    sentence.rstrip().endswith(("?", "？"))
                    or self._has_any(normalized, _QUESTION_MARKERS)
                ):
                    self._append_unique(
                        buckets, seen, "open_questions", sentence, source
                    )
                for match in _VERSION_RE.finditer(sentence):
                    self._append_unique(
                        buckets,
                        seen,
                        "version_refs",
                        match.group(0),
                        source,
                    )

        state = {"goal": goal, **buckets}
        if cache_key:
            with self._cache_lock:
                self._state_cache[cache_key] = (
                    len(history),
                    _source_id(history[-1], len(history) - 1),
                    deepcopy(state),
                )
                self._state_cache.move_to_end(cache_key)
                self._trim_cache(self._state_cache)
        return state, cache_hit

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "goal": None,
            "constraints": [],
            "decisions": [],
            "verified_facts": [],
            "open_questions": [],
            "version_refs": [],
        }

    @staticmethod
    def _cache_key(history: list[dict[str, Any]]) -> str:
        if not history:
            return ""
        return str(history[0].get("id") or "")

    @staticmethod
    def _prefix_valid(
        processed: int,
        last_source: str,
        history: list[dict[str, Any]],
    ) -> bool:
        return (
            0 < processed <= len(history)
            and _source_id(
                history[processed - 1], processed - 1
            ) == last_source
        )

    def _trim_cache(self, cache: OrderedDict) -> None:
        while len(cache) > self.cache_limit:
            cache.popitem(last=False)

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
        buckets[key].append(
            {"text": text[:1200], "source": source}
        )
        if len(buckets[key]) > self.state_items_per_kind:
            buckets[key].pop(0)

    @staticmethod
    def _has_any(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in markers)
