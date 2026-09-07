from __future__ import annotations

from collections import Counter, defaultdict
import copy
from typing import Any

from worldforge.benchmarks.multimodal_corpus import QUALITY_PROTOCOL
from worldforge.benchmarks.multimodal_workspace import validate_workspace, workspace_digest


_ALLOWED_MODALITIES = {"text", "image", "video", "audio"}
_SCOPE_KEYS = {
    "build_ref": ("build_ref", "build", "version"),
    "branch_ref": ("branch_ref", "branch"),
    "commit_ref": ("commit_ref", "commit", "sha", "git_sha"),
    "environment_ref": ("environment_ref", "environment", "env"),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _positive_deficit(current: int, minimum: int) -> int:
    return max(0, int(minimum) - int(current))


def _scope_values(meta: dict[str, Any], field: str) -> set[str]:
    values = {
        _clean(meta.get(key))
        for key in _SCOPE_KEYS[field]
        if _clean(meta.get(key))
    }
    return values


def _case_annotation_state(
    case: dict[str, Any],
    *,
    known_asset_ids: set[str],
    minimum_annotators: int,
) -> dict[str, Any]:
    case_id = _clean(case.get("id"))
    query_ok = bool(_clean(case.get("query")))
    source_group_ok = bool(_clean(case.get("source_group")))
    targets = {
        _clean(value).lower()
        for value in list(case.get("target_modalities") or [])
        if _clean(value)
    }
    target_ok = bool(targets) and not (targets - _ALLOWED_MODALITIES)
    candidates = [
        _clean(value)
        for value in list(case.get("candidate_asset_ids") or [])
        if _clean(value)
    ]
    candidate_set = set(candidates)
    candidates_ok = bool(candidates) and len(candidates) == len(candidate_set) and not (
        candidate_set - known_asset_ids
    )
    relevant = [dict(row or {}) for row in list(case.get("relevant") or [])]
    relevant_ids = {
        _clean(row.get("asset_id"))
        for row in relevant
        if _clean(row.get("asset_id"))
    }
    relevant_ok = bool(relevant) and bool(relevant_ids) and not (
        relevant_ids - candidate_set
    )
    forbidden = {
        _clean(value)
        for value in list(case.get("forbidden_asset_ids") or [])
        if _clean(value)
    }
    forbidden_ok = not (forbidden - candidate_set) and not (forbidden & relevant_ids)
    annotation = dict(case.get("annotation") or {})
    try:
        annotator_count = int(annotation.get("annotator_count") or 0)
    except (TypeError, ValueError):
        annotator_count = 0
    annotators_ok = annotator_count >= minimum_annotators
    adjudicated = bool(annotation.get("adjudicated"))

    missing: list[str] = []
    if not query_ok:
        missing.append("query")
    if not source_group_ok:
        missing.append("source_group")
    if not target_ok:
        missing.append("target_modalities")
    if not candidates_ok:
        missing.append("candidate_asset_ids")
    if not relevant_ok:
        missing.append("relevant")
    if not forbidden_ok:
        missing.append("forbidden_asset_ids_consistency")
    if not annotators_ok:
        missing.append("annotator_count")
    if not adjudicated:
        missing.append("adjudicated")

    temporal = False
    invalid_temporal = False
    for row in relevant:
        start = row.get("start")
        end = row.get("end")
        if start is None and end is None:
            continue
        if start is None or end is None:
            invalid_temporal = True
            continue
        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError):
            invalid_temporal = True
            continue
        if start_value < 0 or end_value <= start_value:
            invalid_temporal = True
            continue
        temporal = True
    if invalid_temporal:
        missing.append("valid_temporal_interval")

    semantic_hard_negative_count = max(
        0,
        len(candidate_set - relevant_ids - forbidden),
    )
    return {
        "id": case_id,
        "complete": not missing,
        "missing": missing,
        "query_annotated": query_ok,
        "source_group_annotated": source_group_ok,
        "target_modalities_annotated": target_ok,
        "candidate_assets_annotated": candidates_ok,
        "relevant_annotated": relevant_ok,
        "scope_labels_consistent": forbidden_ok,
        "annotator_count": annotator_count,
        "annotators_ready": annotators_ok,
        "adjudicated": adjudicated,
        "target_modalities": sorted(targets & _ALLOWED_MODALITIES),
        "candidate_asset_count": len(candidate_set),
        "relevant_asset_count": len(relevant_ids),
        "forbidden_asset_count": len(forbidden),
        "semantic_hard_negative_count": semantic_hard_negative_count,
        "temporal": temporal,
    }


def annotation_readiness_report(workspace: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(workspace or {}))
    requirements = dict(QUALITY_PROTOCOL["quality_evidence_requirements"])
    workspace_validation = validate_workspace(payload)
    catalog = [dict(row or {}) for row in list(payload.get("asset_catalog") or [])]
    cases = [dict(row or {}) for row in list(payload.get("cases") or [])]
    known_asset_ids = {
        _clean(row.get("id")) for row in catalog if _clean(row.get("id"))
    }
    minimum_annotators = int(requirements["minimum_annotators_per_case"])

    asset_modality_counts = Counter()
    asset_hash_paths: dict[str, list[str]] = defaultdict(list)
    asset_hash_ids: dict[str, list[str]] = defaultdict(list)
    asset_scope_distribution: dict[str, Counter[str]] = {
        field: Counter() for field in _SCOPE_KEYS
    }
    asset_scope_conflicts: list[dict[str, Any]] = []
    asset_id_to_sha: dict[str, str] = {}

    for asset in catalog:
        asset_id = _clean(asset.get("id"))
        meta = dict(asset.get("meta") or {})
        kind = _clean(meta.get("kind")).lower()
        if kind in _ALLOWED_MODALITIES:
            asset_modality_counts[kind] += 1
        sha = _clean(asset.get("sha256")).lower()
        path = _clean(asset.get("path"))
        if sha:
            asset_hash_paths[sha].append(path)
            asset_hash_ids[sha].append(asset_id)
            if asset_id:
                asset_id_to_sha[asset_id] = sha
        for field in _SCOPE_KEYS:
            values = _scope_values(meta, field)
            if len(values) > 1:
                asset_scope_conflicts.append(
                    {
                        "asset_id": asset_id,
                        "field": field,
                        "values": sorted(values),
                    }
                )
            for value in values:
                asset_scope_distribution[field][value] += 1

    duplicate_content_groups = [
        {
            "sha256": sha,
            "asset_ids": sorted(asset_hash_ids[sha]),
            "paths": sorted(asset_hash_paths[sha]),
        }
        for sha in sorted(asset_hash_paths)
        if len(asset_hash_paths[sha]) > 1
    ]

    states = [
        _case_annotation_state(
            case,
            known_asset_ids=known_asset_ids,
            minimum_annotators=minimum_annotators,
        )
        for case in cases
    ]
    state_by_id = {state["id"]: state for state in states if state["id"]}

    source_groups = Counter()
    target_modality_cases = Counter({kind: 0 for kind in sorted(_ALLOWED_MODALITIES)})
    temporal_cases = 0
    scope_negative_cases = 0
    candidate_references = 0
    relevant_labels = 0
    forbidden_references = 0
    semantic_hard_negatives = 0
    hash_to_source_groups: dict[str, set[str]] = defaultdict(set)

    for case, state in zip(cases, states, strict=True):
        source_group = _clean(case.get("source_group"))
        if source_group:
            source_groups[source_group] += 1
        for modality in state["target_modalities"]:
            target_modality_cases[modality] += 1
        temporal_cases += int(state["temporal"])
        scope_negative_cases += int(state["forbidden_asset_count"] > 0)
        candidate_references += int(state["candidate_asset_count"])
        relevant_labels += int(state["relevant_asset_count"])
        forbidden_references += int(state["forbidden_asset_count"])
        semantic_hard_negatives += int(state["semantic_hard_negative_count"])
        if source_group:
            for asset_id in {
                _clean(value)
                for value in list(case.get("candidate_asset_ids") or [])
                if _clean(value)
            }:
                sha = asset_id_to_sha.get(asset_id)
                if sha:
                    hash_to_source_groups[sha].add(source_group)

    cross_source_group_duplicate_content = [
        {
            "sha256": sha,
            "source_groups": sorted(groups),
            "asset_ids": sorted(asset_hash_ids.get(sha, [])),
            "paths": sorted(asset_hash_paths.get(sha, [])),
        }
        for sha, groups in sorted(hash_to_source_groups.items())
        if len(groups) > 1
    ]

    minimum_cases = int(requirements["minimum_cases"])
    minimum_source_groups = int(requirements["minimum_unique_source_groups"])
    minimum_per_modality = int(requirements["minimum_cases_per_target_modality"])
    minimum_temporal = int(requirements["minimum_temporal_cases"])
    minimum_scope_negative = int(requirements["minimum_scope_negative_cases"])

    deficits = {
        "cases": _positive_deficit(len(cases), minimum_cases),
        "unique_source_groups": _positive_deficit(
            len(source_groups), minimum_source_groups
        ),
        "target_modalities": {
            kind: _positive_deficit(target_modality_cases[kind], minimum_per_modality)
            for kind in sorted(_ALLOWED_MODALITIES)
        },
        "temporal_cases": _positive_deficit(temporal_cases, minimum_temporal),
        "scope_negative_cases": _positive_deficit(
            scope_negative_cases, minimum_scope_negative
        ),
    }
    protocol_coverage_ready = not deficits["cases"] and not deficits[
        "unique_source_groups"
    ] and not any(deficits["target_modalities"].values()) and not deficits[
        "temporal_cases"
    ] and not deficits["scope_negative_cases"]

    incomplete_states = [state for state in states if not state["complete"]]
    missing_field_counts = Counter(
        field for state in incomplete_states for field in state["missing"]
    )
    fully_annotated_cases = len(states) - len(incomplete_states)
    heldout_excluded = bool(
        (payload.get("heldout_policy") or {}).get("development_excluded")
    )
    annotation_complete = (
        bool(cases)
        and fully_annotated_cases == len(cases)
        and heldout_excluded
        and bool(workspace_validation["structurally_valid"])
    )

    warnings: list[dict[str, Any]] = []
    if duplicate_content_groups:
        warnings.append(
            {
                "code": "duplicate-content",
                "count": len(duplicate_content_groups),
                "message": (
                    "identical content appears under multiple asset paths; review whether "
                    "these are intentional duplicates before split sealing"
                ),
            }
        )
    if cross_source_group_duplicate_content:
        warnings.append(
            {
                "code": "cross-source-group-content-reuse",
                "count": len(cross_source_group_duplicate_content),
                "message": (
                    "the same content hash is referenced by cases in multiple source_groups; "
                    "review for source-group leakage or intentional shared negatives"
                ),
            }
        )
    if asset_scope_conflicts:
        warnings.append(
            {
                "code": "asset-scope-alias-conflict",
                "count": len(asset_scope_conflicts),
                "message": (
                    "one or more assets declare conflicting aliases for the same scope field; "
                    "resolve provenance before using them as scope negatives"
                ),
            }
        )

    return {
        "protocol": QUALITY_PROTOCOL["name"],
        "protocol_version": QUALITY_PROTOCOL["protocol_version"],
        "workspace_digest": workspace_digest(payload),
        "evidence_claim": "none-annotation-readiness-audit",
        "readiness_semantics": (
            "authoring/coverage audit only; not a frozen corpus and not model-quality evidence"
        ),
        "requirements": requirements,
        "workspace_validation": {
            "structurally_valid": bool(workspace_validation["structurally_valid"]),
            "authoring_freeze_ready": bool(workspace_validation["freeze_ready"]),
            "errors": list(workspace_validation["errors"]),
            "freeze_blockers": list(workspace_validation["freeze_blockers"]),
        },
        "assets": {
            "total": len(catalog),
            "unique_content_hashes": len(asset_hash_paths),
            "modality_counts": {
                kind: int(asset_modality_counts[kind])
                for kind in sorted(_ALLOWED_MODALITIES)
            },
            "duplicate_content_groups": duplicate_content_groups,
            "scope_distribution": {
                field: dict(sorted(counter.items()))
                for field, counter in asset_scope_distribution.items()
            },
            "scope_alias_conflicts": asset_scope_conflicts,
        },
        "cases": {
            "total": len(cases),
            "fully_annotated": fully_annotated_cases,
            "incomplete": len(incomplete_states),
            "missing_field_counts": dict(sorted(missing_field_counts.items())),
            "incomplete_cases": incomplete_states,
            "source_group_counts": dict(sorted(source_groups.items())),
            "unique_source_groups": len(source_groups),
            "target_modality_cases": {
                kind: int(target_modality_cases[kind])
                for kind in sorted(_ALLOWED_MODALITIES)
            },
            "temporal_cases": temporal_cases,
            "scope_negative_cases": scope_negative_cases,
            "candidate_references": candidate_references,
            "relevant_labels": relevant_labels,
            "forbidden_references": forbidden_references,
            "semantic_hard_negative_candidates": semantic_hard_negatives,
        },
        "coverage": {
            "deficits": deficits,
            "protocol_coverage_ready": protocol_coverage_ready,
            "annotation_complete": annotation_complete,
            "development_excluded": heldout_excluded,
            "ready_for_strict_freeze_attempt": (
                protocol_coverage_ready
                and annotation_complete
                and bool(workspace_validation["freeze_ready"])
            ),
        },
        "leakage_audit": {
            "cross_source_group_duplicate_content": cross_source_group_duplicate_content,
            "warning_count": len(warnings),
            "warnings": warnings,
            "automatic_block": False,
            "note": (
                "duplicate-content findings require human review; this audit does not "
                "silently alter source_group or candidate labels"
            ),
        },
    }
