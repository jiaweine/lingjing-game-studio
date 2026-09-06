from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from worldforge.context.memory_identity import MemoryIdentityResolver


@dataclass(frozen=True)
class MemoryIdentityBenchmarkResult:
    cases: int
    positive_cases: int
    negative_cases: int
    correct: int
    precision: float
    positive_recall: float
    false_merge_rate: float
    false_split_rate: float
    abstention_rate: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _head(
    key: str,
    content: str,
    *,
    kind: str = "fact",
    state: str = "active",
    build_ref: str | None = None,
    branch_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"bench:{key}:{build_ref or 'general'}",
        "memory_key": key,
        "revision": 1,
        "kind": kind,
        "content": content,
        "state": state,
        "build_ref": build_ref,
        "branch_ref": branch_ref,
        "commit_ref": None,
        "environment_ref": None,
    }


def _proposal(
    content: str,
    *,
    kind: str = "fact",
    build_ref: str | None = None,
    branch_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "content": content,
        "build_ref": build_ref,
        "branch_ref": branch_ref,
        "commit_ref": None,
        "environment_ref": None,
    }


def run_memory_identity_benchmark() -> MemoryIdentityBenchmarkResult:
    """Mechanism benchmark for identity suggestions, not a semantic-memory SOTA claim.

    False merge is treated as the primary safety failure. Positive recall is secondary because
    abstention only costs a reviewer one manual key choice, while a false merge corrupts a
    revision chain and can later make stale facts look authoritative.
    """
    resolver = MemoryIdentityResolver()
    cases: list[tuple[dict[str, Any], list[dict[str, Any]], str | None]] = [
        (
            _proposal(
                "已确认 build 1.4.7 护盾冷却是 5 秒。",
                build_ref="1.4.7",
                branch_ref="release",
            ),
            [
                _head(
                    "combat.shield.cooldown",
                    "build 1.4.7 护盾冷却已确认是 6 秒。",
                    build_ref="1.4.7",
                    branch_ref="release",
                )
            ],
            "combat.shield.cooldown",
        ),
        (
            _proposal("Confirmed shield cooldown is 5 seconds."),
            [_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds.")],
            "combat.shield.cooldown",
        ),
        (
            _proposal(
                "已确认 build 2.0.0 护盾冷却是 5 秒。",
                build_ref="2.0.0",
                branch_ref="release",
            ),
            [
                _head(
                    "combat.shield.cooldown",
                    "build 1.4.7 护盾冷却已确认是 6 秒。",
                    build_ref="1.4.7",
                    branch_ref="release",
                )
            ],
            "combat.shield.cooldown",
        ),
        (
            _proposal("必须保持 tickrate=60。", kind="constraint"),
            [_head("runtime.tickrate.required", "必须保持 tickrate=30。", kind="constraint")],
            "runtime.tickrate.required",
        ),
        # Different predicate: sharing the entity 'shield' is not enough to merge identity.
        (
            _proposal("已确认护盾持续时间是 5 秒。"),
            [_head("combat.shield.cooldown", "护盾冷却已确认是 6 秒。")],
            None,
        ),
        # Different named entity: fire_shield and ice_shield must stay separate.
        (
            _proposal("Confirmed ice_shield cooldown is 5 seconds."),
            [_head("combat.fire_shield.cooldown", "Confirmed fire_shield cooldown is 6 seconds.")],
            None,
        ),
        # Ambiguous equal candidates must abstain even though both look individually strong.
        (
            _proposal("Confirmed shield cooldown is 5 seconds."),
            [
                _head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
                _head("ui.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
            ],
            None,
        ),
        (
            _proposal("Confirmed boss damage coefficient is 1.4."),
            [_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds.")],
            None,
        ),
        # Kind is part of identity safety: a fact proposal must not silently revise a rule.
        (
            _proposal("Confirmed shield cooldown is 5 seconds.", kind="fact"),
            [_head("combat.shield.rule", "Shield cooldown must remain 6 seconds.", kind="constraint")],
            None,
        ),
        # Retracted heads are historical evidence, not active identity targets for auto-suggest.
        (
            _proposal("Confirmed shield cooldown is 5 seconds."),
            [_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds.", state="retracted")],
            None,
        ),
    ]

    correct = 0
    recommendations = 0
    correct_recommendations = 0
    positive = 0
    false_split = 0
    negative = 0
    false_merge = 0
    abstentions = 0

    for proposal, heads, expected in cases:
        resolution = resolver.resolve(proposal, heads)
        predicted = resolution.recommended_key
        correct += int(predicted == expected)
        abstentions += int(predicted is None)
        recommendations += int(predicted is not None)
        if expected is not None:
            positive += 1
            correct_recommendations += int(predicted == expected)
            false_split += int(predicted != expected)
        else:
            negative += 1
            false_merge += int(predicted is not None)

    precision = correct_recommendations / max(1, recommendations)
    recall = correct_recommendations / max(1, positive)
    false_merge_rate = false_merge / max(1, negative)
    false_split_rate = false_split / max(1, positive)
    abstention_rate = abstentions / len(cases)
    passed = bool(
        false_merge_rate == 0.0
        and precision == 1.0
        and recall >= 0.75
    )
    return MemoryIdentityBenchmarkResult(
        cases=len(cases),
        positive_cases=positive,
        negative_cases=negative,
        correct=correct,
        precision=round(precision, 6),
        positive_recall=round(recall, 6),
        false_merge_rate=round(false_merge_rate, 6),
        false_split_rate=round(false_split_rate, 6),
        abstention_rate=round(abstention_rate, 6),
        passed=passed,
    )
