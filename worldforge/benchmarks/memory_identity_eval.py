from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from worldforge.context.memory_identity import MemoryIdentityResolver


@dataclass(frozen=True)
class IdentityRiskCoveragePoint:
    threshold: float
    coverage: float
    precision: float
    positive_recall: float
    false_merge_rate: float
    abstention_rate: float


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
    safe_coverage_at_zero_false_merge: float
    unsafe_best_candidate_false_merge_rate: float
    family_metrics: dict[str, dict[str, float | int]]
    risk_coverage_curve: tuple[IdentityRiskCoveragePoint, ...]
    evidence_class: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_coverage_curve"] = [
            asdict(point) for point in self.risk_coverage_curve
        ]
        return payload


@dataclass(frozen=True)
class _Case:
    family: str
    proposal: dict[str, Any]
    heads: tuple[dict[str, Any], ...]
    expected: str | None


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
        "id": f"bench:{key}:{build_ref or 'general'}:{state}",
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


def _curated_cases() -> list[_Case]:
    """Small hand-authored floor retained from IdentityBench v1."""
    return [
        _Case(
            "curated-value-update-zh",
            _proposal(
                "已确认 build 1.4.7 护盾冷却是 5 秒。",
                build_ref="1.4.7",
                branch_ref="release",
            ),
            (
                _head(
                    "combat.shield.cooldown",
                    "build 1.4.7 护盾冷却已确认是 6 秒。",
                    build_ref="1.4.7",
                    branch_ref="release",
                ),
            ),
            "combat.shield.cooldown",
        ),
        _Case(
            "curated-value-update-en",
            _proposal("Confirmed shield cooldown is 5 seconds."),
            (_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),),
            "combat.shield.cooldown",
        ),
        _Case(
            "curated-cross-build",
            _proposal(
                "已确认 build 2.0.0 护盾冷却是 5 秒。",
                build_ref="2.0.0",
                branch_ref="release",
            ),
            (
                _head(
                    "combat.shield.cooldown",
                    "build 1.4.7 护盾冷却已确认是 6 秒。",
                    build_ref="1.4.7",
                    branch_ref="release",
                ),
            ),
            "combat.shield.cooldown",
        ),
        _Case(
            "curated-kind-update",
            _proposal("必须保持 tickrate=60。", kind="constraint"),
            (
                _head(
                    "runtime.tickrate.required",
                    "必须保持 tickrate=30。",
                    kind="constraint",
                ),
            ),
            "runtime.tickrate.required",
        ),
        _Case(
            "curated-predicate-collision",
            _proposal("已确认护盾持续时间是 5 秒。"),
            (_head("combat.shield.cooldown", "护盾冷却已确认是 6 秒。"),),
            None,
        ),
        _Case(
            "curated-entity-collision",
            _proposal("Confirmed ice_shield cooldown is 5 seconds."),
            (
                _head(
                    "combat.fire_shield.cooldown",
                    "Confirmed fire_shield cooldown is 6 seconds.",
                ),
            ),
            None,
        ),
        _Case(
            "curated-ambiguous",
            _proposal("Confirmed shield cooldown is 5 seconds."),
            (
                _head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
                _head("ui.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
            ),
            None,
        ),
        _Case(
            "curated-unrelated",
            _proposal("Confirmed boss damage coefficient is 1.4."),
            (_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),),
            None,
        ),
        _Case(
            "curated-kind-mismatch",
            _proposal("Confirmed shield cooldown is 5 seconds.", kind="fact"),
            (
                _head(
                    "combat.shield.rule",
                    "Shield cooldown must remain 6 seconds.",
                    kind="constraint",
                ),
            ),
            None,
        ),
        _Case(
            "curated-retracted",
            _proposal("Confirmed shield cooldown is 5 seconds."),
            (
                _head(
                    "combat.shield.cooldown",
                    "Confirmed shield cooldown is 6 seconds.",
                    state="retracted",
                ),
            ),
            None,
        ),
    ]


_ENTITIES = (
    "shield_core",
    "fire_shield",
    "ice_shield",
    "arcane_barrier",
    "boss_enrage",
    "boss_stagger",
    "player_dash",
    "player_roll",
    "stamina_regen",
    "mana_regen",
    "health_regen",
    "weapon_recoil",
    "weapon_spread",
    "projectile_speed",
    "projectile_lifetime",
    "ai_alert",
    "ai_patrol",
    "ai_search",
    "nav_repath",
    "net_reconcile",
    "net_snapshot",
    "render_scale",
    "render_shadow",
    "audio_reverb",
    "audio_occlusion",
    "loot_respawn",
    "quest_timeout",
    "ui_fade",
    "camera_shake",
    "camera_zoom",
)
_PREDICATES = ("cooldown", "duration", "threshold", "coefficient", "interval")


def _generated_adversarial_cases() -> list[_Case]:
    """Deterministically generate broad collision/version/paraphrase pressure.

    This is still a synthetic mechanism benchmark. Scale here improves regression coverage; it
    does not convert deterministic cases into real-world identity accuracy evidence.
    """
    cases: list[_Case] = []
    for index, entity in enumerate(_ENTITIES):
        predicate = _PREDICATES[index % len(_PREDICATES)]
        other_predicate = _PREDICATES[(index + 1) % len(_PREDICATES)]
        other_entity = _ENTITIES[(index + 1) % len(_ENTITIES)]
        key = f"game.{entity}.{predicate}"
        old_value = 10 + index
        new_value = 100 + index

        # Four positive update families: numeric changes, assertion paraphrase, cross-build,
        # and cross-branch scope. Identity should remain the semantic key while revision scope
        # remains a separate ProjectMemory concern.
        cases.extend(
            [
                _Case(
                    "generated-value-update",
                    _proposal(f"Confirmed {entity} {predicate} is {new_value}."),
                    (_head(key, f"Confirmed {entity} {predicate} is {old_value}."),),
                    key,
                ),
                _Case(
                    "generated-assertion-paraphrase",
                    _proposal(f"Validated {entity} {predicate} = {new_value}."),
                    (_head(key, f"Verified {entity} {predicate} = {old_value}."),),
                    key,
                ),
                _Case(
                    "generated-cross-build",
                    _proposal(
                        f"Confirmed build 2.0.0 {entity} {predicate} is {new_value}.",
                        build_ref="2.0.0",
                        branch_ref="release",
                    ),
                    (
                        _head(
                            key,
                            f"Confirmed build 1.4.7 {entity} {predicate} is {old_value}.",
                            build_ref="1.4.7",
                            branch_ref="release",
                        ),
                    ),
                    key,
                ),
                _Case(
                    "generated-cross-branch",
                    _proposal(
                        f"Confirmed {entity} {predicate} is {new_value}.",
                        build_ref="1.4.7",
                        branch_ref="hotfix",
                    ),
                    (
                        _head(
                            key,
                            f"Confirmed {entity} {predicate} is {old_value}.",
                            build_ref="1.4.7",
                            branch_ref="release",
                        ),
                    ),
                    key,
                ),
            ]
        )

        # Five safety-negative families deliberately share surface form, entity or predicate.
        # A false merge here is a more serious failure than abstaining on one of the positives.
        cases.extend(
            [
                _Case(
                    "generated-predicate-collision",
                    _proposal(f"Confirmed {entity} {other_predicate} is {new_value}."),
                    (_head(key, f"Confirmed {entity} {predicate} is {old_value}."),),
                    None,
                ),
                _Case(
                    "generated-entity-collision",
                    _proposal(f"Confirmed {other_entity} {predicate} is {new_value}."),
                    (_head(key, f"Confirmed {entity} {predicate} is {old_value}."),),
                    None,
                ),
                _Case(
                    "generated-ambiguous-keys",
                    _proposal(f"Confirmed {entity} {predicate} is {new_value}."),
                    (
                        _head(
                            f"game.{entity}.{predicate}",
                            f"Confirmed {entity} {predicate} is {old_value}.",
                        ),
                        _head(
                            f"ui.{entity}.{predicate}",
                            f"Confirmed {entity} {predicate} is {old_value}.",
                        ),
                    ),
                    None,
                ),
                _Case(
                    "generated-kind-mismatch",
                    _proposal(
                        f"Confirmed {entity} {predicate} is {new_value}.",
                        kind="fact",
                    ),
                    (
                        _head(
                            key,
                            f"{entity} {predicate} must remain {old_value}.",
                            kind="constraint",
                        ),
                    ),
                    None,
                ),
                _Case(
                    "generated-retracted-head",
                    _proposal(f"Confirmed {entity} {predicate} is {new_value}."),
                    (
                        _head(
                            key,
                            f"Confirmed {entity} {predicate} is {old_value}.",
                            state="retracted",
                        ),
                    ),
                    None,
                ),
            ]
        )
    return cases


def _all_cases() -> list[_Case]:
    return [*_curated_cases(), *_generated_adversarial_cases()]


def _score(
    cases: list[_Case],
    resolver: MemoryIdentityResolver,
    *,
    force_best_candidate: bool = False,
) -> dict[str, float | int]:
    correct = 0
    recommendations = 0
    correct_recommendations = 0
    positive = 0
    false_split = 0
    negative = 0
    false_merge = 0
    abstentions = 0

    for case in cases:
        resolution = resolver.resolve(case.proposal, case.heads)
        if force_best_candidate:
            predicted = (
                resolution.candidates[0].memory_key if resolution.candidates else None
            )
        else:
            predicted = resolution.recommended_key
        correct += int(predicted == case.expected)
        abstentions += int(predicted is None)
        recommendations += int(predicted is not None)
        if case.expected is not None:
            positive += 1
            correct_recommendations += int(predicted == case.expected)
            false_split += int(predicted != case.expected)
        else:
            negative += 1
            false_merge += int(predicted is not None)

    precision = correct_recommendations / max(1, recommendations)
    recall = correct_recommendations / max(1, positive)
    return {
        "cases": len(cases),
        "positive_cases": positive,
        "negative_cases": negative,
        "correct": correct,
        "recommendations": recommendations,
        "precision": precision,
        "positive_recall": recall,
        "false_merge_rate": false_merge / max(1, negative),
        "false_split_rate": false_split / max(1, positive),
        "abstention_rate": abstentions / max(1, len(cases)),
        "coverage": recommendations / max(1, len(cases)),
    }


def _rounded(metrics: dict[str, float | int]) -> dict[str, float | int]:
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in metrics.items()
    }


def run_memory_identity_benchmark() -> MemoryIdentityBenchmarkResult:
    """Large deterministic safety benchmark for identity suggestions.

    False merge remains the primary failure. The suite intentionally contains more negatives
    than positives and reports a threshold risk/coverage curve plus an unsafe no-abstention
    comparator. This is a regression/safety protocol, not external semantic-memory SOTA proof.
    """
    cases = _all_cases()
    resolver = MemoryIdentityResolver()
    default = _score(cases, resolver)

    families = sorted({case.family for case in cases})
    family_metrics = {
        family: _rounded(
            _score([case for case in cases if case.family == family], resolver)
        )
        for family in families
    }

    points: list[IdentityRiskCoveragePoint] = []
    for threshold in (0.60, 0.66, 0.72, 0.78, 0.84, 0.90):
        metrics = _score(cases, MemoryIdentityResolver(threshold=threshold))
        points.append(
            IdentityRiskCoveragePoint(
                threshold=threshold,
                coverage=round(float(metrics["coverage"]), 6),
                precision=round(float(metrics["precision"]), 6),
                positive_recall=round(float(metrics["positive_recall"]), 6),
                false_merge_rate=round(float(metrics["false_merge_rate"]), 6),
                abstention_rate=round(float(metrics["abstention_rate"]), 6),
            )
        )

    safe_coverage = max(
        (
            point.coverage
            for point in points
            if point.false_merge_rate == 0.0 and point.precision == 1.0
        ),
        default=0.0,
    )
    unsafe = _score(cases, resolver, force_best_candidate=True)

    precision = float(default["precision"])
    recall = float(default["positive_recall"])
    false_merge_rate = float(default["false_merge_rate"])
    false_split_rate = float(default["false_split_rate"])
    abstention_rate = float(default["abstention_rate"])
    passed = bool(
        len(cases) >= 250
        and int(default["negative_cases"]) >= int(default["positive_cases"])
        and false_merge_rate == 0.0
        and precision == 1.0
        and recall >= 0.80
        and safe_coverage >= 0.25
    )
    return MemoryIdentityBenchmarkResult(
        cases=len(cases),
        positive_cases=int(default["positive_cases"]),
        negative_cases=int(default["negative_cases"]),
        correct=int(default["correct"]),
        precision=round(precision, 6),
        positive_recall=round(recall, 6),
        false_merge_rate=round(false_merge_rate, 6),
        false_split_rate=round(false_split_rate, 6),
        abstention_rate=round(abstention_rate, 6),
        safe_coverage_at_zero_false_merge=round(safe_coverage, 6),
        unsafe_best_candidate_false_merge_rate=round(
            float(unsafe["false_merge_rate"]), 6
        ),
        family_metrics=family_metrics,
        risk_coverage_curve=tuple(points),
        evidence_class="synthetic-adversarial-safety-floor-not-sota",
        passed=passed,
    )
