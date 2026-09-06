from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[a-z][a-z0-9_./:+#@-]*", re.IGNORECASE)
_VERSION_RE = re.compile(
    r"(?i)\b(?:build|version|ver|v)\s*[:=#-]?\s*[a-z0-9][a-z0-9._+-]{1,31}\b"
    r"|版本\s*[:：=#-]?\s*[a-z0-9][a-z0-9._+-]{1,31}"
)
# Longest unit alternatives must come first. Otherwise `5 seconds` can match only the leading
# `s` and leave `econds`, creating a fake stable identifier shared by unrelated memories.
_NUMBER_RE = re.compile(
    r"(?<![a-z_])[-+]?\d+(?:\.\d+)?"
    r"(?:\s*(?:milliseconds|millisecond|seconds|second|secs|sec|minutes|minute|分钟|ms|s|秒|分|%|％))?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9_\u4e00-\u9fff]+", re.IGNORECASE)
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")

# These words describe assertion status or generic glue, not semantic identity. Removing them
# makes "confirmed cooldown=6" and "cooldown is now 5" comparable without pretending that
# their values are equivalent.
_ASSERTION_NOISE = (
    "已经确认",
    "已确认",
    "确认过",
    "已经验证",
    "已验证",
    "验证通过",
    "confirmed",
    "verified",
    "validated",
    "我们决定",
    "决定",
    "we decided",
)
_STOP_TOKENS = {
    "已经",
    "确认",
    "已经确认",
    "已确认",
    "验证",
    "通过",
    "现在",
    "当前",
    "改为",
    "改成",
    "变为",
    "更新",
    "build",
    "version",
    "ver",
    "value",
    "milliseconds",
    "millisecond",
    "seconds",
    "second",
    "secs",
    "sec",
    "minutes",
    "minute",
    "ms",
}
_SCOPE_FIELDS = ("build_ref", "branch_ref", "commit_ref", "environment_ref")


@dataclass(frozen=True)
class MemoryIdentityCandidate:
    memory_key: str
    score: float
    recommended: bool
    reasons: tuple[str, ...]
    best_head_id: str | None
    best_revision: int | None
    scope_relation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_key": self.memory_key,
            "score": self.score,
            "recommended": self.recommended,
            "reasons": list(self.reasons),
            "best_head_id": self.best_head_id,
            "best_revision": self.best_revision,
            "scope_relation": self.scope_relation,
        }


@dataclass(frozen=True)
class MemoryIdentityResolution:
    proposal_kind: str
    candidates: tuple[MemoryIdentityCandidate, ...]
    abstained: bool
    best_score: float
    score_margin: float
    threshold: float
    margin_threshold: float
    mode: str = "deterministic-memory-identity-v1"

    @property
    def recommended_key(self) -> str | None:
        for candidate in self.candidates:
            if candidate.recommended:
                return candidate.memory_key
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "proposal_kind": self.proposal_kind,
            "abstained": self.abstained,
            "recommended_key": self.recommended_key,
            "best_score": self.best_score,
            "score_margin": self.score_margin,
            "threshold": self.threshold,
            "margin_threshold": self.margin_threshold,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def _normalize(text: str) -> str:
    value = str(text or "").strip().lower()
    for marker in _ASSERTION_NOISE:
        value = value.replace(marker.lower(), " ")
    value = _VERSION_RE.sub(" ", value)
    value = _NUMBER_RE.sub(" ", value)
    value = _PUNCT_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    # Removing an assertion marker in the middle of Chinese text can introduce a synthetic
    # whitespace boundary (`护盾冷却 已确认 是` -> `护盾冷却 是`). Canonicalize that boundary so
    # CJK n-grams describe the subject/predicate rather than the extractor's edit position.
    return _CJK_SPACE_RE.sub("", value)


def _tokens(text: str) -> set[str]:
    value = _normalize(text)
    out: set[str] = set()
    for raw in _ASCII_RE.findall(value):
        for token in re.split(r"[./:+#@-]+", raw.lower()):
            if len(token) >= 2 and token not in _STOP_TOKENS:
                out.add(token)
    for run in _CJK_RE.findall(value):
        if len(run) == 1:
            if run not in _STOP_TOKENS:
                out.add(run)
            continue
        for size in (2, 3):
            if len(run) < size:
                continue
            for index in range(len(run) - size + 1):
                token = run[index:index + size]
                if token not in _STOP_TOKENS:
                    out.add(token)
    return out


def _stable_ascii_identifiers(text: str) -> set[str]:
    identifiers: set[str] = set()
    without_versions = _VERSION_RE.sub(" ", str(text or "").lower())
    without_values = _NUMBER_RE.sub(" ", without_versions)
    for raw in _ASCII_RE.findall(without_values):
        token = raw.strip("./:+#@-")
        if (
            len(token) >= 3
            and token not in _STOP_TOKENS
            and not token.isdigit()
            and not re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s)?", token)
        ):
            identifiers.add(token)
    return identifiers


def _structured_identifiers(text: str) -> set[str]:
    """Identifiers likely to name concrete game/config entities rather than prose words."""
    return {
        token
        for token in _stable_ascii_identifiers(text)
        if any(separator in token for separator in ("_", ".", "/", ":", "#", "@"))
    }


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    common = len(left & right)
    containment = common / max(1, min(len(left), len(right)))
    jaccard = common / max(1, len(left | right))
    return 0.68 * containment + 0.32 * jaccard


def _skeleton_similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _scope_relation(proposal: dict[str, Any], head: dict[str, Any]) -> tuple[str, float]:
    supplied = 0
    exact = 0
    conflicts = 0
    proposal_specific = 0
    head_specific = 0
    for field in _SCOPE_FIELDS:
        left = str(proposal.get(field) or "").strip()
        right = str(head.get(field) or "").strip()
        proposal_specific += int(bool(left))
        head_specific += int(bool(right))
        if left or right:
            supplied += 1
        if left and right:
            if left == right:
                exact += 1
            else:
                conflicts += 1

    if conflicts:
        # Same semantic key can legitimately exist in several builds. Cross-scope identity is
        # therefore possible, but it must win on semantic evidence rather than scope.
        return "cross-scope", 0.28
    if supplied == 0:
        return "general", 1.0
    if exact == supplied:
        return "exact", 1.0
    if proposal_specific and not head_specific:
        return "proposal-scoped/head-general", 0.72
    if head_specific and not proposal_specific:
        return "proposal-general/head-scoped", 0.42
    return "compatible", 0.82


def _head_score(proposal: dict[str, Any], head: dict[str, Any]) -> tuple[float, tuple[str, ...], str]:
    if str(proposal.get("kind") or "").lower() != str(head.get("kind") or "").lower():
        return 0.0, ("kind-mismatch",), "incompatible"

    proposal_content = str(proposal.get("content") or "")
    head_content = str(head.get("content") or "")
    proposal_normalized = _normalize(proposal_content)
    head_normalized = _normalize(head_content)
    proposal_tokens = _tokens(proposal_content)
    head_tokens = _tokens(head_content)
    lexical = _overlap(proposal_tokens, head_tokens)
    skeleton = _skeleton_similarity(proposal_content, head_content)
    exact_skeleton = bool(proposal_normalized and proposal_normalized == head_normalized)

    proposal_ids = _stable_ascii_identifiers(proposal_content)
    head_ids = _stable_ascii_identifiers(head_content)
    identifier = _overlap(proposal_ids, head_ids)
    proposal_entities = _structured_identifiers(proposal_content)
    head_entities = _structured_identifiers(head_content)
    entity_conflict = bool(
        proposal_entities
        and head_entities
        and not (proposal_entities & head_entities)
    )

    key_tokens = _tokens(str(head.get("memory_key") or ""))
    key_affinity = _overlap(proposal_tokens, key_tokens)
    scope_relation, scope_score = _scope_relation(proposal, head)

    score = (
        0.44 * lexical
        + 0.26 * skeleton
        + 0.10 * identifier
        + 0.12 * key_affinity
        + 0.08 * scope_score
        + (0.08 if exact_skeleton else 0.0)
        - (0.22 if entity_conflict else 0.0)
    )

    reasons: list[str] = []
    if lexical >= 0.75:
        reasons.append("strong-stable-token-overlap")
    elif lexical >= 0.50:
        reasons.append("moderate-stable-token-overlap")
    if exact_skeleton:
        reasons.append("exact-value-stripped-skeleton")
    elif skeleton >= 0.80:
        reasons.append("same-value-stripped-skeleton")
    elif skeleton >= 0.62:
        reasons.append("similar-value-stripped-skeleton")
    if identifier >= 0.66:
        reasons.append("shared-stable-identifiers")
    if entity_conflict:
        reasons.append("conflicting-structured-identifiers")
    if key_affinity >= 0.55:
        reasons.append("proposal-matches-key-semantics")
    reasons.append(f"scope:{scope_relation}")
    return min(1.0, max(0.0, score)), tuple(reasons), scope_relation


class MemoryIdentityResolver:
    """Suggest likely existing memory identities without ever mutating memory truth.

    False merge is more dangerous than false split, so this resolver deliberately abstains
    unless the best candidate clears both an absolute score and a best-vs-second margin.
    Callers may show candidates to a human reviewer, but MUST NOT auto-approve or auto-change
    proposal keys based on this output.
    """

    def __init__(
        self,
        *,
        threshold: float = 0.72,
        margin_threshold: float = 0.10,
        candidate_floor: float = 0.34,
    ) -> None:
        self.threshold = min(0.98, max(0.50, float(threshold)))
        self.margin_threshold = min(0.40, max(0.03, float(margin_threshold)))
        self.candidate_floor = min(self.threshold, max(0.20, float(candidate_floor)))

    def resolve(
        self,
        proposal: dict[str, Any],
        heads: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        top_k: int = 3,
    ) -> MemoryIdentityResolution:
        kind = str(proposal.get("kind") or "").strip().lower()
        content = str(proposal.get("content") or "").strip()
        if not kind or not content:
            return MemoryIdentityResolution(
                proposal_kind=kind,
                candidates=(),
                abstained=True,
                best_score=0.0,
                score_margin=0.0,
                threshold=self.threshold,
                margin_threshold=self.margin_threshold,
            )

        # Aggregate by semantic key. Several scoped heads can share one key; identity is the
        # key, while revision progression remains scope-specific inside ProjectMemoryStore.
        best_by_key: dict[str, tuple[float, tuple[str, ...], str, dict[str, Any]]] = {}
        for raw in heads:
            head = dict(raw or {})
            key = str(head.get("memory_key") or "").strip().lower()
            if not key or str(head.get("state") or "active") == "retracted":
                continue
            score, reasons, scope_relation = _head_score(proposal, head)
            if score < self.candidate_floor:
                continue
            previous = best_by_key.get(key)
            if previous is None or score > previous[0]:
                best_by_key[key] = (score, reasons, scope_relation, head)

        ranked = sorted(
            best_by_key.items(),
            key=lambda item: (item[1][0], int(item[1][3].get("revision") or 0)),
            reverse=True,
        )
        best_score = ranked[0][1][0] if ranked else 0.0
        second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score if ranked else 0.0
        recommended_key = (
            ranked[0][0]
            if ranked
            and best_score >= self.threshold
            and (len(ranked) == 1 or margin >= self.margin_threshold)
            else None
        )

        candidates: list[MemoryIdentityCandidate] = []
        for key, (score, reasons, scope_relation, head) in ranked[: max(1, min(8, int(top_k)))]:
            candidates.append(
                MemoryIdentityCandidate(
                    memory_key=key,
                    score=round(score, 6),
                    recommended=(key == recommended_key),
                    reasons=reasons,
                    best_head_id=str(head.get("id") or "") or None,
                    best_revision=(int(head.get("revision")) if head.get("revision") is not None else None),
                    scope_relation=scope_relation,
                )
            )

        return MemoryIdentityResolution(
            proposal_kind=kind,
            candidates=tuple(candidates),
            abstained=recommended_key is None,
            best_score=round(best_score, 6),
            score_margin=round(margin, 6),
            threshold=self.threshold,
            margin_threshold=self.margin_threshold,
        )
