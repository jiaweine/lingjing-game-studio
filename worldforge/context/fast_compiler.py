from __future__ import annotations

from dataclasses import replace
from typing import Any

from .active_compiler import ContextCompiler as _ActiveContextCompiler
from .compiler import ContextPacket, _search_tokens, _source_id


class ContextCompiler(_ActiveContextCompiler):
    """Active-state compiler with an append-only zero-copy retrieval index hot path.

    The base v2 index rebuilt shallow copies of the complete postings dictionary on every
    cache hit. Dense 50k-message histories therefore reported a cache hit while still paying
    O(index-size) work each turn. This layer mutates the derived postings cache in place under
    the existing RLock and only tokenizes newly appended messages. Raw history remains the
    source of truth and any prefix invalidation still discards the derived index.
    """

    def compile(
        self,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> ContextPacket:
        packet = super().compile(query, history)
        return replace(packet, mode="active-task-state-fast-index-context-compiler-v4")

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

            candidates: set[int] = set()
            for token in query_tokens:
                candidates.update(postings.get(token, ()))
            return candidates, cache_hit
