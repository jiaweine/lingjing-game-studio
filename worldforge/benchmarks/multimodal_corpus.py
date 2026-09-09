from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any


QUALITY_PROTOCOL: dict[str, Any] = {
    "name": "game-rd-mm-v1",
    "protocol_version": "1.0",
    "annotation_guideline_version": "game-rd-mm-v1-annotation-1",
    "quality_evidence_requirements": {
        "minimum_cases": 100,
        "minimum_unique_source_groups": 20,
        "minimum_cases_per_target_modality": 20,
        "minimum_temporal_cases": 20,
        "minimum_scope_negative_cases": 25,
        "minimum_annotators_per_case": 2,
        "require_adjudication": True,
        "require_frozen_manifest": True,
        "require_content_sha256": True,
        "require_file_hash_verification": True,
        "require_heldout_development_exclusion": True,
    },
}

_ALLOWED_MODALITIES = {"text", "image", "video", "audio"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_corpus_digest(dataset: dict[str, Any]) -> str:
    """Stable digest for the annotation manifest, independent of an embedded digest field."""
    payload = copy.deepcopy(dict(dataset or {}))
    payload.pop("corpus_digest", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_dataset_paths(
    dataset: dict[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    """Resolve corpus-relative asset paths for runtime without changing the frozen manifest."""
    payload = copy.deepcopy(dict(dataset or {}))
    for case in list(payload.get("cases") or []):
        for asset in list(case.get("assets") or []):
            raw = str(asset.get("path") or "").strip()
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                asset["path"] = str((base_dir / path).resolve())
    return payload


def _kind(asset: dict[str, Any]) -> str:
    meta = dict(asset.get("meta") or {})
    value = str(meta.get("kind") or "").strip().lower()
    if value and value != "file":
        return value
    mime = str(asset.get("mime") or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/"):
        return "text"
    return value or "file"


def _scope_eligible(asset: dict[str, Any]) -> bool:
    context = dict((asset.get("meta") or {}).get("_context") or {})
    return context.get("scope_eligible") is not False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_corpus(
    dataset: dict[str, Any],
    *,
    base_dir: Path | None = None,
    verify_files: bool = False,
) -> dict[str, Any]:
    """Validate structure and determine whether a corpus may support measured quality claims.

    Structural errors make a benchmark unsafe to run. Quality blockers are stricter: a
    structurally valid corpus may still be useful for development or smoke tests, but it may
    not be labeled held-out quality evidence until every blocker is cleared.
    """
    dataset = dict(dataset or {})
    requirements = dict(QUALITY_PROTOCOL["quality_evidence_requirements"])
    errors: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    cases = list(dataset.get("cases") or [])
    case_ids: set[str] = set()
    source_groups: set[str] = set()
    modality_counts = {kind: 0 for kind in sorted(_ALLOWED_MODALITIES)}
    temporal_cases = 0
    scope_negative_cases = 0
    asset_fingerprints: dict[str, str] = {}
    verified_files: dict[tuple[str, str], bool] = {}
    file_hash_failures = 0

    for index, raw_case in enumerate(cases):
        case = dict(raw_case or {})
        prefix = f"cases[{index}]"
        case_id = str(case.get("id") or "").strip()
        query = str(case.get("query") or "").strip()
        if not case_id:
            errors.append(f"{prefix}: missing id")
            case_id = f"<missing-{index}>"
        elif case_id in case_ids:
            errors.append(f"{prefix}: duplicate id {case_id!r}")
        case_ids.add(case_id)
        if not query:
            errors.append(f"{prefix}: query must be non-empty")

        source_group = str(case.get("source_group") or "").strip()
        if source_group:
            source_groups.add(source_group)
        else:
            blockers.append(f"{prefix}: missing source_group")

        target_modalities = {
            str(value).strip().lower()
            for value in list(case.get("target_modalities") or [])
            if str(value).strip()
        }
        invalid_modalities = sorted(target_modalities - _ALLOWED_MODALITIES)
        if invalid_modalities:
            errors.append(
                f"{prefix}: unsupported target modalities {invalid_modalities}"
            )
        if not target_modalities:
            blockers.append(f"{prefix}: missing target_modalities")
        for modality in target_modalities & _ALLOWED_MODALITIES:
            modality_counts[modality] += 1

        annotation = dict(case.get("annotation") or {})
        annotator_count = annotation.get("annotator_count")
        try:
            annotator_count_value = int(annotator_count)
        except (TypeError, ValueError):
            annotator_count_value = 0
        if annotator_count_value < int(requirements["minimum_annotators_per_case"]):
            blockers.append(
                f"{prefix}: requires at least "
                f"{requirements['minimum_annotators_per_case']} annotators"
            )
        if bool(requirements["require_adjudication"]) and not bool(
            annotation.get("adjudicated")
        ):
            blockers.append(f"{prefix}: adjudication is required")

        assets = [dict(row or {}) for row in list(case.get("assets") or [])]
        if not assets:
            errors.append(f"{prefix}: assets must be non-empty")
        by_id: dict[str, dict[str, Any]] = {}
        for asset_index, asset in enumerate(assets):
            asset_prefix = f"{prefix}.assets[{asset_index}]"
            asset_id = str(asset.get("id") or "").strip()
            if not asset_id:
                errors.append(f"{asset_prefix}: missing id")
                continue
            if asset_id in by_id:
                errors.append(f"{asset_prefix}: duplicate asset id {asset_id!r}")
                continue
            by_id[asset_id] = asset

            kind = _kind(asset)
            if kind not in _ALLOWED_MODALITIES:
                errors.append(f"{asset_prefix}: unsupported modality {kind!r}")

            raw_sha = str(asset.get("sha256") or "").strip().lower()
            if raw_sha and not _SHA256_RE.fullmatch(raw_sha):
                errors.append(f"{asset_prefix}: sha256 must be 64 lowercase hex chars")
            if not raw_sha:
                blockers.append(f"{asset_prefix}: missing content sha256")
            elif asset_id in asset_fingerprints and asset_fingerprints[asset_id] != raw_sha:
                errors.append(
                    f"{asset_prefix}: asset id {asset_id!r} maps to multiple sha256 values"
                )
            elif raw_sha:
                asset_fingerprints[asset_id] = raw_sha

            raw_path = str(asset.get("path") or "").strip()
            if not raw_path:
                blockers.append(f"{asset_prefix}: missing corpus-relative path")
            elif Path(raw_path).is_absolute():
                blockers.append(
                    f"{asset_prefix}: path must be corpus-relative for a frozen manifest"
                )

            if verify_files and raw_path and raw_sha:
                if base_dir is None:
                    errors.append(
                        f"{asset_prefix}: base_dir is required when verify_files=True"
                    )
                else:
                    path = Path(raw_path)
                    materialized = path if path.is_absolute() else base_dir / path
                    key = (str(materialized), raw_sha)
                    if key not in verified_files:
                        if not materialized.is_file():
                            verified_files[key] = False
                        else:
                            try:
                                verified_files[key] = _sha256_file(materialized) == raw_sha
                            except OSError:
                                verified_files[key] = False
                    if not verified_files[key]:
                        file_hash_failures += 1
                        errors.append(
                            f"{asset_prefix}: file missing or sha256 mismatch for {raw_path!r}"
                        )

        relevant = [dict(row or {}) for row in list(case.get("relevant") or [])]
        if not relevant:
            errors.append(f"{prefix}: relevant must be non-empty")
        relevant_ids: set[str] = set()
        case_has_temporal_truth = False
        for rel_index, truth in enumerate(relevant):
            truth_prefix = f"{prefix}.relevant[{rel_index}]"
            asset_id = str(truth.get("asset_id") or "").strip()
            if not asset_id or asset_id not in by_id:
                errors.append(f"{truth_prefix}: asset_id must reference a case asset")
                continue
            relevant_ids.add(asset_id)
            if not _scope_eligible(by_id[asset_id]):
                errors.append(
                    f"{truth_prefix}: relevant asset {asset_id!r} is scope-ineligible"
                )
            start = truth.get("start")
            end = truth.get("end")
            if (start is None) != (end is None):
                errors.append(f"{truth_prefix}: temporal truth needs both start and end")
            if start is not None and end is not None:
                case_has_temporal_truth = True
                try:
                    start_value = float(start)
                    end_value = float(end)
                except (TypeError, ValueError):
                    errors.append(f"{truth_prefix}: start/end must be numeric")
                    continue
                if start_value < 0 or end_value <= start_value:
                    errors.append(f"{truth_prefix}: invalid temporal interval")
                duration = (by_id[asset_id].get("meta") or {}).get("duration")
                if duration is not None:
                    try:
                        duration_value = float(duration)
                    except (TypeError, ValueError):
                        errors.append(
                            f"{truth_prefix}: referenced asset duration must be numeric"
                        )
                    else:
                        if end_value > duration_value + 1e-6:
                            errors.append(
                                f"{truth_prefix}: interval exceeds asset duration"
                            )

        if case_has_temporal_truth:
            temporal_cases += 1

        forbidden = {
            str(value).strip()
            for value in list(case.get("forbidden_asset_ids") or [])
            if str(value).strip()
        }
        if forbidden:
            scope_negative_cases += 1
        overlap = sorted(relevant_ids & forbidden)
        if overlap:
            errors.append(f"{prefix}: relevant/forbidden overlap {overlap}")
        unknown_forbidden = sorted(forbidden - set(by_id))
        if unknown_forbidden:
            errors.append(
                f"{prefix}: forbidden ids must reference case assets {unknown_forbidden}"
            )
        for asset_id in sorted(forbidden & set(by_id)):
            if _scope_eligible(by_id[asset_id]):
                errors.append(
                    f"{prefix}: forbidden scope asset {asset_id!r} must be "
                    "scope_eligible=false"
                )

        if target_modalities:
            relevant_kinds = {
                _kind(by_id[asset_id])
                for asset_id in relevant_ids
                if asset_id in by_id
            }
            if not (relevant_kinds & target_modalities):
                errors.append(
                    f"{prefix}: no relevant asset matches target_modalities"
                )

        raw_top_k = case.get("top_k")
        if raw_top_k is not None:
            try:
                top_k = int(raw_top_k)
            except (TypeError, ValueError):
                errors.append(f"{prefix}: top_k must be an integer")
            else:
                if top_k <= 0:
                    errors.append(f"{prefix}: top_k must be positive")

    if str(dataset.get("name") or "") != QUALITY_PROTOCOL["name"]:
        blockers.append(
            f"dataset name must be {QUALITY_PROTOCOL['name']!r} for quality evidence"
        )
    if str(dataset.get("protocol_version") or "") != QUALITY_PROTOCOL["protocol_version"]:
        blockers.append(
            f"protocol_version must be {QUALITY_PROTOCOL['protocol_version']!r}"
        )
    if str(dataset.get("evidence_class") or "") != "human-annotated-heldout":
        blockers.append("evidence_class must be 'human-annotated-heldout'")
    if str(dataset.get("split") or "") != "heldout":
        blockers.append("split must be 'heldout'")
    if bool(requirements["require_frozen_manifest"]) and not bool(dataset.get("frozen")):
        blockers.append("frozen=true is required")
    if not str(dataset.get("frozen_at") or "").strip():
        blockers.append("frozen_at is required")
    if str(dataset.get("annotation_guideline_version") or "") != QUALITY_PROTOCOL[
        "annotation_guideline_version"
    ]:
        blockers.append(
            "annotation_guideline_version does not match the frozen protocol"
        )
    heldout_policy = dict(dataset.get("heldout_policy") or {})
    if bool(requirements["require_heldout_development_exclusion"]) and not bool(
        heldout_policy.get("development_excluded")
    ):
        blockers.append("heldout_policy.development_excluded=true is required")

    if len(cases) < int(requirements["minimum_cases"]):
        blockers.append(
            f"requires at least {requirements['minimum_cases']} cases; got {len(cases)}"
        )
    if len(source_groups) < int(requirements["minimum_unique_source_groups"]):
        blockers.append(
            "requires at least "
            f"{requirements['minimum_unique_source_groups']} unique source_groups; "
            f"got {len(source_groups)}"
        )
    for modality in sorted(_ALLOWED_MODALITIES):
        count = modality_counts[modality]
        minimum = int(requirements["minimum_cases_per_target_modality"])
        if count < minimum:
            blockers.append(
                f"requires at least {minimum} target cases for {modality}; got {count}"
            )
    if temporal_cases < int(requirements["minimum_temporal_cases"]):
        blockers.append(
            f"requires at least {requirements['minimum_temporal_cases']} temporal cases; "
            f"got {temporal_cases}"
        )
    if scope_negative_cases < int(requirements["minimum_scope_negative_cases"]):
        blockers.append(
            "requires at least "
            f"{requirements['minimum_scope_negative_cases']} scope-negative cases; "
            f"got {scope_negative_cases}"
        )

    hashes_verified = bool(verify_files) and file_hash_failures == 0 and not any(
        "missing content sha256" in blocker for blocker in blockers
    )
    if bool(requirements["require_file_hash_verification"]) and not hashes_verified:
        blockers.append("file hash verification must succeed in the benchmark run")
    if not verify_files:
        warnings.append(
            "file hashes were not verified; this run cannot support a measured quality claim"
        )

    return {
        "protocol": QUALITY_PROTOCOL["name"],
        "protocol_version": QUALITY_PROTOCOL["protocol_version"],
        "corpus_digest": canonical_corpus_digest(dataset),
        "cases": len(cases),
        "unique_source_groups": len(source_groups),
        "target_modality_cases": modality_counts,
        "temporal_cases": temporal_cases,
        "scope_negative_cases": scope_negative_cases,
        "files_verified": hashes_verified,
        "structurally_valid": not errors,
        "strict_quality_eligible": not errors and not blockers,
        "errors": errors,
        "warnings": warnings,
        "quality_blockers": blockers,
    }


def assert_protocol_matches(payload: dict[str, Any]) -> None:
    if dict(payload or {}) != QUALITY_PROTOCOL:
        raise ValueError("protocol.json does not match QUALITY_PROTOCOL")
