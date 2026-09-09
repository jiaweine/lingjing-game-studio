from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any


MODEL_PROTOCOL = {
    "name": "lingjing-long-horizon-model-v1",
    "protocol_version": "1.0",
    "minimum_cases": 40,
    "minimum_cases_per_category": 10,
    "categories": ("qa", "update", "abstention", "workflow"),
    "minimum_long_range_cases": 30,
}


def canonical_model_dataset_digest(dataset: dict[str, Any]) -> str:
    payload = dict(dataset or {})
    payload.pop("dataset_digest", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _groups(value: Any) -> list[list[str]]:
    groups: list[list[str]] = []
    for raw in list(value or []):
        if isinstance(raw, str):
            aliases = [_text(raw)]
        else:
            aliases = [_text(item) for item in list(raw or []) if _text(item)]
        aliases = [item for item in aliases if item]
        if aliases:
            groups.append(aliases)
    return groups


def validate_model_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    data = dict(dataset or {})
    errors: list[str] = []
    blockers: list[str] = []
    categories = {name: 0 for name in MODEL_PROTOCOL["categories"]}
    case_ids: set[str] = set()
    long_range = 0
    cases = [dict(row or {}) for row in list(data.get("cases") or [])]

    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            errors.append(f"{prefix}: missing id")
        elif case_id in case_ids:
            errors.append(f"{prefix}: duplicate id {case_id!r}")
        case_ids.add(case_id)
        category = str(case.get("category") or "").strip().lower()
        if category not in categories:
            errors.append(f"{prefix}: invalid category {category!r}")
        else:
            categories[category] += 1
        history = [dict(row or {}) for row in list(case.get("history") or [])]
        if not history:
            errors.append(f"{prefix}: history must be non-empty")
        for turn_index, message in enumerate(history):
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                errors.append(f"{prefix}.history[{turn_index}]: invalid role")
            if not str(message.get("content") or "").strip():
                errors.append(f"{prefix}.history[{turn_index}]: empty content")
        if not str(case.get("query") or "").strip():
            errors.append(f"{prefix}: query must be non-empty")
        raw_anchor = case.get("long_range_anchor_index")
        try:
            anchor_index = int(raw_anchor)
        except (TypeError, ValueError):
            anchor_index = -1
        if anchor_index < 0 or anchor_index >= len(history):
            errors.append(f"{prefix}: invalid long_range_anchor_index")
        elif anchor_index < max(0, len(history) - 8):
            long_range += 1
        else:
            blockers.append(f"{prefix}: anchor is still inside legacy last-8 window")

        rubric = dict(case.get("rubric") or {})
        required = _groups(rubric.get("required_any"))
        forbidden = [_text(item) for item in list(rubric.get("forbidden_any") or []) if _text(item)]
        workflow = _groups(rubric.get("workflow_order"))
        abstention_markers = [
            _text(item)
            for item in list(rubric.get("abstention_markers") or [])
            if _text(item)
        ]
        abstention_required = bool(rubric.get("abstention_required"))
        if not required and not forbidden and not workflow and not abstention_required:
            errors.append(f"{prefix}: rubric is empty")
        if category == "abstention" and not abstention_required:
            errors.append(f"{prefix}: abstention category must require abstention")
        if abstention_required and not abstention_markers:
            errors.append(f"{prefix}: abstention markers are required")
        if category == "workflow" and len(workflow) < 2:
            errors.append(f"{prefix}: workflow category needs >=2 ordered rubric groups")

    if str(data.get("name") or "") != MODEL_PROTOCOL["name"]:
        blockers.append(f"name must be {MODEL_PROTOCOL['name']!r}")
    if str(data.get("protocol_version") or "") != MODEL_PROTOCOL["protocol_version"]:
        blockers.append("protocol_version mismatch")
    if str(data.get("evidence_class") or "") != "human-authored-heldout":
        blockers.append("evidence_class must be 'human-authored-heldout'")
    if not bool(data.get("frozen")):
        blockers.append("frozen=true is required")
    if not bool((data.get("heldout_policy") or {}).get("development_excluded")):
        blockers.append("heldout_policy.development_excluded=true is required")
    if len(cases) < int(MODEL_PROTOCOL["minimum_cases"]):
        blockers.append(
            f"requires >= {MODEL_PROTOCOL['minimum_cases']} cases; got {len(cases)}"
        )
    for category, count in categories.items():
        minimum = int(MODEL_PROTOCOL["minimum_cases_per_category"])
        if count < minimum:
            blockers.append(f"requires >= {minimum} {category} cases; got {count}")
    if long_range < int(MODEL_PROTOCOL["minimum_long_range_cases"]):
        blockers.append(
            f"requires >= {MODEL_PROTOCOL['minimum_long_range_cases']} long-range cases; "
            f"got {long_range}"
        )
    return {
        "dataset_digest": canonical_model_dataset_digest(data),
        "cases": len(cases),
        "category_counts": categories,
        "long_range_cases": long_range,
        "structurally_valid": not errors,
        "strict_quality_eligible": not errors and not blockers,
        "errors": errors,
        "quality_blockers": blockers,
    }


@dataclass(frozen=True)
class ModelCaseScore:
    case_id: str
    category: str
    required_groups: int
    required_groups_hit: int
    forbidden_hit: bool
    abstention_required: bool
    abstention_compliant: bool
    workflow_groups: int
    workflow_order_compliant: bool
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_alias_position(response: str, aliases: list[str]) -> int | None:
    positions = [response.find(alias) for alias in aliases if alias and response.find(alias) >= 0]
    return min(positions) if positions else None


def score_model_response(case: dict[str, Any], response: str) -> ModelCaseScore:
    text = _text(response)
    rubric = dict(case.get("rubric") or {})
    required = _groups(rubric.get("required_any"))
    required_hits = sum(1 for aliases in required if any(alias in text for alias in aliases))
    forbidden = [_text(item) for item in list(rubric.get("forbidden_any") or []) if _text(item)]
    forbidden_hit = any(item in text for item in forbidden)
    abstention_required = bool(rubric.get("abstention_required"))
    abstention_markers = [
        _text(item)
        for item in list(rubric.get("abstention_markers") or [])
        if _text(item)
    ]
    abstention_compliant = (
        any(marker in text for marker in abstention_markers)
        if abstention_required
        else True
    )
    workflow = _groups(rubric.get("workflow_order"))
    positions = [_first_alias_position(text, aliases) for aliases in workflow]
    workflow_compliant = True
    if workflow:
        workflow_compliant = all(position is not None for position in positions) and all(
            int(positions[index]) < int(positions[index + 1])
            for index in range(len(positions) - 1)
        )
    passed = bool(
        required_hits == len(required)
        and not forbidden_hit
        and abstention_compliant
        and workflow_compliant
    )
    return ModelCaseScore(
        case_id=str(case.get("id") or ""),
        category=str(case.get("category") or ""),
        required_groups=len(required),
        required_groups_hit=required_hits,
        forbidden_hit=forbidden_hit,
        abstention_required=abstention_required,
        abstention_compliant=abstention_compliant,
        workflow_groups=len(workflow),
        workflow_order_compliant=workflow_compliant,
        passed=passed,
    )


def aggregate_model_scores(scores: list[ModelCaseScore]) -> dict[str, Any]:
    cases = len(scores)
    required = sum(score.required_groups for score in scores)
    required_hit = sum(score.required_groups_hit for score in scores)
    abstention_cases = [score for score in scores if score.abstention_required]
    workflow_cases = [score for score in scores if score.workflow_groups]
    categories: dict[str, dict[str, float | int]] = {}
    for category in MODEL_PROTOCOL["categories"]:
        rows = [score for score in scores if score.category == category]
        categories[category] = {
            "cases": len(rows),
            "pass_rate": round(sum(row.passed for row in rows) / max(1, len(rows)), 6),
        }
    return {
        "cases": cases,
        "case_pass_rate": round(sum(score.passed for score in scores) / max(1, cases), 6),
        "required_anchor_recall": round(required_hit / max(1, required), 6),
        "forbidden_claim_rate": round(
            sum(score.forbidden_hit for score in scores) / max(1, cases), 6
        ),
        "abstention_compliance": round(
            sum(score.abstention_compliant for score in abstention_cases)
            / max(1, len(abstention_cases)),
            6,
        ),
        "workflow_order_compliance": round(
            sum(score.workflow_order_compliant for score in workflow_cases)
            / max(1, len(workflow_cases)),
            6,
        ),
        "category_metrics": categories,
    }
