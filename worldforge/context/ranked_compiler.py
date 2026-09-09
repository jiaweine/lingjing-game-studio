from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from .compiler import ContextPacket, _search_tokens, _source_id
from .fast_compiler import ContextCompiler as _FastContextCompiler

_IDENTIFIER_SEPARATORS = ("_", "/", ".", ":", "#", "@")


def _identifier_like(token: str) -> bool:
    value = str(token or "")
    return bool(
        any(ch.isdigit() for ch in value)
        or any(separator in value for separator in _IDENTIFIER_SEPARATORS)
    )


class ContextCompiler(_FastContextCompiler):
    """Precision-safe candidate planner for large dense histories.

    v4 removed O(index-size) copies from the append hot path, but dense queries still
    unioned every posting for every query token. Common words such as ``build`` and
    ``shield`` could therefore create tens of thousands of candidates before scoring.

    v5 keeps the same append-only index and uses document frequency as a query planner:
    exact identifier-like tokens are preferred when sufficiently selective; otherwise
    rare lexical/CJK tokens are accumulated. If no selective signal exists the compiler
    deliberately falls back to the full union rather than imposing a lossy hard cap.
    """

    def compile(
        self,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> ContextPacket:
        packet = super().compile(query, history)
        return replace(packet, mode="active-task-state-ranked-index-context-compiler-v5")

    @staticmethod
    def _candidate_budget(history_size: int) -> int:
        # Grows sub-linearly and is used only to decide whether a token is selective.
        # Posting lists are never truncated to this value.
        return max(256, min(4096, int(math.sqrt(max(1, history_size)) * 16)))

    def _planned_candidates(
        self,
        postings: dict[str, list[int]],
        query_tokens: set[str],
        history_size: int,
    ) -> set[int]:
        rows = sorted(
            (
                (len(postings[token]), token, postings[token])
                for token in query_tokens
                if postings.get(token)
            ),
            key=lambda row: (row[0], row[1]),
        )
        if not rows:
            return set()

        budget = self._candidate_budget(history_size)
        selective_limit = max(budget, max(64, history_size // 50))

        identifier_rows = [
            row
            for row in rows
            if _identifier_like(row[1]) and row[0] <= selective_limit
        ]
        if identifier_rows:
            candidates: set[int] = set()
            # Keep all selective identifier postings. We do not slice posting lists,
            # because doing so would turn a performance optimization into hidden recall loss.
            for _df, _token, indices in identifier_rows[:8]:
                candidates.update(indices)
            if candidates:
                return candidates

        rare_rows = [row for row in rows if row[0] <= selective_limit]
        if rare_rows:
            candidates = set()
            minimum_evidence = max(32, self.retrieved_messages * 8)
            for _df, _token, indices in rare_rows[:8]:
                candidates.update(indices)
                if len(candidates) >= minimum_evidence:
                    break
            if candidates:
                return candidates

        # Ambiguous/common-token query: correctness wins over speed. This preserves the
        # old deterministic lexical behavior and lets semantic retrieval handle broader
        # meaning when enabled at the higher ContextOS evidence layer.
        candidates: set[int] = set()
        for _df, _token, indices in rows:
            candidates.update(indices)
        return candidates

    def _candidate_indices(
        self,
        history: list[dict[str, Any]],
        query_tokens: set[str],
    ) -> tuple[set[int], bool]:
        if not query_tokens:
            return set(), False
        cache_key = self._cache_key(history)
        if not cache_key:
            return super()._candidate_indices(history, query_tokens)

        with self._cache_lock:
            cached = self._retrieval_cache.get(cache_key)
            cache_hit = False
            if cached and self._prefix_valid(cached[0], cached[1], history):
                processed, _last_source, postings = cached
                cache_hit = True
                self._retrieval_cache.move_to_end(cache_key)
            else:
                processed = 0
                postings: dict[str, list[int]] = {}
                self._retrieval_cache.pop(cache_key, None)

            for index in range(processed, len(history)):
                message = history[index]
                if message.get("role") not in {"user", "assistant"}:
                    continue
                content = str(message.get("content", "") or "")
                for token in _search_tokens(content):
                    postings.setdefault(token, []).append(index)

            self._retrieval_cache[cache_key] = (
                len(history),
                _source_id(history[-1], len(history) - 1),
                postings,
            )
            self._retrieval_cache.move_to_end(cache_key)
            self._trim_cache(self._retrieval_cache)
            return (
                self._planned_candidates(postings, query_tokens, len(history)),
                cache_hit,
            )
