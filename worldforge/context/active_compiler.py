from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
from typing import Any

from .compiler import (
    ContextCompiler as _BaseContextCompiler,
    ContextPacket,
    _CONSTRAINT_MARKERS,
    _DECISION_MARKERS,
    _QUESTION_MARKERS,
    _VERIFIED_MARKERS,
    _VERSION_RE,
    _normalize,
)

_CONDITIONAL_MARKERS = (
    "如果",
    "假如",
    "假设",
    "前提是",
    "若",
    "assuming ",
    "assume ",
    "if ",
    "provided that",
)

# Exact active-state slotting is deliberately conservative. We remove state verbs and
# scalar values, but keep technical identifiers, branch/environment words and a
# canonicalized version scope. Different scopes therefore cannot silently collapse.
_SLOT_MARKERS = tuple(
    sorted(
        set(
            _CONSTRAINT_MARKERS
            + _DECISION_MARKERS
            + _VERIFIED_MARKERS
            + _QUESTION_MARKERS
            + _CONDITIONAL_MARKERS
        ),
        key=len,
        reverse=True,
    )
)
_VALUE_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:\s*(?:%|ms|s|hz|fps|秒|毫秒|帧|次|倍))?",
    re.IGNORECASE,
)
_ENGLISH_NOISE_RE = re.compile(
    r"\b(?:is|are|was|were|be|equals?|equal\s+to|currently|current|now|"
    r"seconds?|milliseconds?|frames?|fps|hz|the|a|an)\b",
    re.IGNORECASE,
)
_CJK_NOISE = (
    "是否",
    "已经",
    "现在",
    "目前",
    "当前",
    "最新",
    "这次",
    "这里",
    "等于",
    "变成",
    "变为",
    "是",
    "为",
    "秒",
    "毫秒",
    "帧",
    "次",
    "倍",
)
_PUNCT_RE = re.compile(r"[\s,，。!?！？；;:=：()\[\]{}\"'`]+")
_VERSION_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{1,31}$")


def _version_scope(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _VERSION_RE.finditer(str(text or "")):
        value_match = _VERSION_VALUE_RE.search(match.group(0).strip())
        if value_match:
            values.append(value_match.group(0).lower())
    return tuple(sorted(set(values)))


def _state_slot(text: str) -> str:
    """Return a precision-first exact slot signature for active TaskState rows.

    Values such as ``5``/``6`` are intentionally removed so an explicit update to the
    same property can supersede the prior row. Version scope is retained separately so
    build 1.4.7 and build 2.0.0 remain distinct active facts.
    """
    raw = str(text or "")
    scope = _version_scope(raw)
    normalized = _normalize(_VERSION_RE.sub(" ", raw))
    for marker in _SLOT_MARKERS:
        normalized = normalized.replace(marker.lower(), " ")
    normalized = _VALUE_RE.sub(" ", normalized)
    normalized = _ENGLISH_NOISE_RE.sub(" ", normalized)
    for token in _CJK_NOISE:
        normalized = normalized.replace(token, " ")
    normalized = _PUNCT_RE.sub(" ", normalized)
    body = " ".join(
        part.strip(" .")
        for part in normalized.split()
        if part.strip(" .")
    )
    scope_key = ",".join(scope)
    if not body:
        return f"scope:{scope_key}" if scope_key else ""
    return f"scope:{scope_key}|{body}"


def _is_conditional_confirmation(text: str) -> bool:
    normalized = _normalize(text)
    verified_positions = [
        normalized.find(marker.lower())
        for marker in _VERIFIED_MARKERS
        if normalized.find(marker.lower()) >= 0
    ]
    if not verified_positions:
        return False
    verified_at = min(verified_positions)
    for marker in _CONDITIONAL_MARKERS:
        conditional_at = normalized.find(marker.lower())
        if 0 <= conditional_at < verified_at:
            return True
    return False


def _latest_by_exact_slot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = str(row.get("text", "") or "")
        slot = _state_slot(text) or f"raw:{_normalize(text)}"
        # Reinsert so dict order follows the latest source, not the first occurrence.
        active.pop(slot, None)
        active[slot] = row
    return list(active.values())


class ContextCompiler(_BaseContextCompiler):
    """Context compiler with a precision-first active TaskState view.

    The base compiler remains responsible for bounded retrieval and incremental caches.
    This layer only changes how the derived TaskState is materialized: explicit updates
    replace older rows in the same exact slot, confirmed answers close matching active
    questions, and conditional premises cannot masquerade as confirmed facts.

    Raw history is never rewritten or deleted; supersession applies only to the derived
    active view injected into model context.
    """

    def compile(
        self,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> ContextPacket:
        packet = super().compile(query, history)
        return replace(packet, mode="active-task-state-context-compiler-v3")

    def _task_state(
        self,
        history: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        state, cache_hit = super()._task_state(history)
        active = deepcopy(state)

        active["constraints"] = _latest_by_exact_slot(
            list(active.get("constraints") or [])
        )
        active["decisions"] = _latest_by_exact_slot(
            list(active.get("decisions") or [])
        )

        verified = [
            row
            for row in list(active.get("verified_facts") or [])
            if not _is_conditional_confirmation(str(row.get("text", "") or ""))
        ]
        active["verified_facts"] = _latest_by_exact_slot(verified)

        questions = _latest_by_exact_slot(
            list(active.get("open_questions") or [])
        )
        verified_slots = {
            _state_slot(str(row.get("text", "") or ""))
            for row in active["verified_facts"]
        }
        verified_slots.discard("")
        active["open_questions"] = [
            row
            for row in questions
            if _state_slot(str(row.get("text", "") or "")) not in verified_slots
        ]
        return active, cache_hit
