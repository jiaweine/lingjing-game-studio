from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from typing import Any

from worldforge.benchmarks.multimodal_corpus import (
    QUALITY_PROTOCOL,
    canonical_corpus_digest,
    validate_corpus,
)


WORKSPACE_VERSION = "1.0"
_ALLOWED_MODALITIES = {"text", "image", "video", "audio"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TEXT_SUFFIXES = {
    ".txt", ".log", ".md", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv",
    ".xml", ".ini", ".cfg", ".toml", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".shader", ".usf", ".ush",
}
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac",
}


@dataclass(frozen=True)
class AssetDiscovery:
    catalog: list[dict[str, Any]]
    report: dict[str, Any]


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _kind_for_path(path: Path, mime: str) -> str | None:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or path.suffix.lower() in _TEXT_SUFFIXES:
        return "text"
    return None


def _mime_for_path(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return "text/plain"
    return "application/octet-stream"


def _safe_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def discover_assets(
    corpus_root: Path,
    *,
    asset_dir: str = "assets",
) -> AssetDiscovery:
    root = Path(corpus_root).resolve()
    assets_root = (root / asset_dir).resolve()
    try:
        assets_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("asset_dir must resolve inside corpus_root") from exc
    if not assets_root.is_dir():
        raise ValueError(f"asset directory does not exist: {assets_root}")

    catalog: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    by_sha: dict[str, list[str]] = {}
    modality_counts = {kind: 0 for kind in sorted(_ALLOWED_MODALITIES)}

    for path in sorted(assets_root.rglob("*"), key=lambda row: row.as_posix()):
        if path.is_symlink():
            skipped.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "reason": "symlink-not-allowed",
                }
            )
            continue
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(assets_root).parts):
            skipped.append(
                {"path": _safe_relative(path, root), "reason": "hidden-path"}
            )
            continue

        suffix = path.suffix.lower()
        if suffix not in _TEXT_SUFFIXES and suffix not in _MEDIA_SUFFIXES:
            skipped.append(
                {"path": _safe_relative(path, root), "reason": "unsupported-extension"}
            )
            continue

        mime = _mime_for_path(path)
        kind = _kind_for_path(path, mime)
        if kind is None:
            skipped.append(
                {"path": _safe_relative(path, root), "reason": "unsupported-modality"}
            )
            continue

        relative_path = _safe_relative(path, root)
        sha256 = _sha256_file(path)
        stable_key = f"{relative_path}\0{sha256}".encode("utf-8")
        asset_id = "asset-" + hashlib.sha256(stable_key).hexdigest()[:20]
        catalog.append(
            {
                "id": asset_id,
                "name": path.name,
                "mime": mime,
                "path": relative_path,
                "sha256": sha256,
                "size": int(path.stat().st_size),
                "meta": {"kind": kind},
            }
        )
        modality_counts[kind] += 1
        by_sha.setdefault(sha256, []).append(relative_path)

    duplicate_groups = [
        {"sha256": sha, "paths": paths}
        for sha, paths in sorted(by_sha.items())
        if len(paths) > 1
    ]
    catalog_digest = _canonical_digest(catalog)
    return AssetDiscovery(
        catalog=catalog,
        report={
            "workspace_version": WORKSPACE_VERSION,
            "asset_dir": Path(asset_dir).as_posix(),
            "assets": len(catalog),
            "modality_counts": modality_counts,
            "duplicate_content_groups": duplicate_groups,
            "skipped": skipped,
            "catalog_digest": catalog_digest,
            "evidence_claim": "none-asset-inventory-only",
        },
    )


def new_workspace(
    corpus_root: Path,
    *,
    asset_dir: str = "assets",
) -> dict[str, Any]:
    discovery = discover_assets(corpus_root, asset_dir=asset_dir)
    return {
        "workspace_version": WORKSPACE_VERSION,
        "name": QUALITY_PROTOCOL["name"],
        "protocol_version": QUALITY_PROTOCOL["protocol_version"],
        "annotation_guideline_version": QUALITY_PROTOCOL[
            "annotation_guideline_version"
        ],
        "evidence_class": "annotation-in-progress",
        "split": "annotation-draft",
        "frozen": False,
        "heldout_policy": {
            "development_excluded": False,
            "notes": (
                "Set development_excluded=true only after source-group split and "
                "development exclusion are independently verified."
            ),
        },
        "asset_catalog": discovery.catalog,
        "asset_catalog_report": discovery.report,
        "cases": [],
    }


def workspace_digest(workspace: dict[str, Any]) -> str:
    payload = copy.deepcopy(dict(workspace or {}))
    payload.pop("workspace_digest", None)
    return _canonical_digest(payload)


def validate_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    payload = dict(workspace or {})
    errors: list[str] = []
    blockers: list[str] = []

    if str(payload.get("workspace_version") or "") != WORKSPACE_VERSION:
        errors.append(f"workspace_version must be {WORKSPACE_VERSION!r}")
    if str(payload.get("name") or "") != QUALITY_PROTOCOL["name"]:
        errors.append(f"name must be {QUALITY_PROTOCOL['name']!r}")
    if str(payload.get("protocol_version") or "") != QUALITY_PROTOCOL["protocol_version"]:
        errors.append(
            f"protocol_version must be {QUALITY_PROTOCOL['protocol_version']!r}"
        )
    if str(payload.get("annotation_guideline_version") or "") != QUALITY_PROTOCOL[
        "annotation_guideline_version"
    ]:
        errors.append("annotation_guideline_version does not match protocol")

    catalog = [dict(row or {}) for row in list(payload.get("asset_catalog") or [])]
    asset_ids: set[str] = set()
    paths: set[str] = set()
    for index, asset in enumerate(catalog):
        prefix = f"asset_catalog[{index}]"
        asset_id = str(asset.get("id") or "").strip()
        path = str(asset.get("path") or "").strip()
        sha = str(asset.get("sha256") or "").strip().lower()
        kind = str((asset.get("meta") or {}).get("kind") or "").strip().lower()
        if not asset_id:
            errors.append(f"{prefix}: missing id")
        elif asset_id in asset_ids:
            errors.append(f"{prefix}: duplicate id {asset_id!r}")
        asset_ids.add(asset_id)
        if not path:
            errors.append(f"{prefix}: missing path")
        elif Path(path).is_absolute():
            errors.append(f"{prefix}: path must be corpus-relative")
        elif path in paths:
            errors.append(f"{prefix}: duplicate path {path!r}")
        paths.add(path)
        if not _SHA256_RE.fullmatch(sha):
            errors.append(f"{prefix}: invalid sha256")
        if kind not in _ALLOWED_MODALITIES:
            errors.append(f"{prefix}: unsupported modality {kind!r}")

    case_ids: set[str] = set()
    cases = [dict(row or {}) for row in list(payload.get("cases") or [])]
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            errors.append(f"{prefix}: missing id")
        elif case_id in case_ids:
            errors.append(f"{prefix}: duplicate id {case_id!r}")
        case_ids.add(case_id)
        if not str(case.get("query") or "").strip():
            blockers.append(f"{prefix}: query is not annotated")
        if not str(case.get("source_group") or "").strip():
            blockers.append(f"{prefix}: source_group is not annotated")

        targets = {
            str(value).strip().lower()
            for value in list(case.get("target_modalities") or [])
            if str(value).strip()
        }
        if not targets:
            blockers.append(f"{prefix}: target_modalities is not annotated")
        invalid = sorted(targets - _ALLOWED_MODALITIES)
        if invalid:
            errors.append(f"{prefix}: unsupported target_modalities {invalid}")

        candidates = [
            str(value).strip()
            for value in list(case.get("candidate_asset_ids") or [])
            if str(value).strip()
        ]
        if not candidates:
            blockers.append(f"{prefix}: candidate_asset_ids is empty")
        if len(candidates) != len(set(candidates)):
            errors.append(f"{prefix}: candidate_asset_ids contains duplicates")
        unknown_candidates = sorted(set(candidates) - asset_ids)
        if unknown_candidates:
            errors.append(f"{prefix}: unknown candidate ids {unknown_candidates}")

        forbidden = {
            str(value).strip()
            for value in list(case.get("forbidden_asset_ids") or [])
            if str(value).strip()
        }
        unknown_forbidden = sorted(forbidden - set(candidates))
        if unknown_forbidden:
            errors.append(
                f"{prefix}: forbidden ids must be candidate assets {unknown_forbidden}"
            )

        relevant = [dict(row or {}) for row in list(case.get("relevant") or [])]
        if not relevant:
            blockers.append(f"{prefix}: relevant labels are missing")
        relevant_ids = {
            str(row.get("asset_id") or "").strip()
            for row in relevant
            if str(row.get("asset_id") or "").strip()
        }
        unknown_relevant = sorted(relevant_ids - set(candidates))
        if unknown_relevant:
            errors.append(
                f"{prefix}: relevant ids must be candidate assets {unknown_relevant}"
            )
        overlap = sorted(relevant_ids & forbidden)
        if overlap:
            errors.append(f"{prefix}: relevant/forbidden overlap {overlap}")

        annotation = dict(case.get("annotation") or {})
        try:
            annotator_count = int(annotation.get("annotator_count") or 0)
        except (TypeError, ValueError):
            annotator_count = 0
        if annotator_count < 2:
            blockers.append(f"{prefix}: requires at least two annotators")
        if not bool(annotation.get("adjudicated")):
            blockers.append(f"{prefix}: adjudication is incomplete")

    if not bool((payload.get("heldout_policy") or {}).get("development_excluded")):
        blockers.append("heldout_policy.development_excluded is not verified")

    return {
        "workspace_version": WORKSPACE_VERSION,
        "workspace_digest": workspace_digest(payload),
        "assets": len(catalog),
        "cases": len(cases),
        "structurally_valid": not errors,
        "freeze_ready": not errors and not blockers,
        "errors": errors,
        "freeze_blockers": blockers,
        "evidence_claim": "none-annotation-workspace",
    }


def compile_workspace(
    workspace: dict[str, Any],
    *,
    freeze: bool,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    validation = validate_workspace(workspace)
    if not validation["structurally_valid"]:
        raise ValueError("; ".join(validation["errors"][:5]))

    payload = dict(workspace or {})
    catalog = {
        str(row.get("id") or ""): copy.deepcopy(dict(row or {}))
        for row in list(payload.get("asset_catalog") or [])
    }
    cases: list[dict[str, Any]] = []
    for raw_case in list(payload.get("cases") or []):
        case = dict(raw_case or {})
        candidate_ids = [
            str(value).strip()
            for value in list(case.get("candidate_asset_ids") or [])
            if str(value).strip()
        ]
        forbidden = {
            str(value).strip()
            for value in list(case.get("forbidden_asset_ids") or [])
            if str(value).strip()
        }
        assets: list[dict[str, Any]] = []
        for asset_id in candidate_ids:
            asset = copy.deepcopy(catalog[asset_id])
            meta = dict(asset.get("meta") or {})
            context = dict(meta.get("_context") or {})
            context["scope_eligible"] = asset_id not in forbidden
            meta["_context"] = context
            asset["meta"] = meta
            assets.append(asset)
        cases.append(
            {
                "id": str(case.get("id") or ""),
                "query": str(case.get("query") or ""),
                "source_group": str(case.get("source_group") or ""),
                "target_modalities": list(case.get("target_modalities") or []),
                "annotation": dict(case.get("annotation") or {}),
                "assets": assets,
                "relevant": copy.deepcopy(list(case.get("relevant") or [])),
                "forbidden_asset_ids": sorted(forbidden),
                **(
                    {"top_k": int(case["top_k"])}
                    if case.get("top_k") is not None
                    else {}
                ),
            }
        )

    if freeze:
        if not frozen_at or not str(frozen_at).strip():
            raise ValueError("frozen_at is required when freeze=True")
        try:
            datetime.fromisoformat(str(frozen_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("frozen_at must be an ISO-8601 timestamp") from exc

    return {
        "name": QUALITY_PROTOCOL["name"],
        "protocol_version": QUALITY_PROTOCOL["protocol_version"],
        "annotation_guideline_version": QUALITY_PROTOCOL[
            "annotation_guideline_version"
        ],
        "evidence_class": "human-annotated-heldout" if freeze else "annotation-in-progress",
        "split": "heldout" if freeze else "annotation-draft",
        "frozen": bool(freeze),
        **({"frozen_at": str(frozen_at)} if freeze else {}),
        "heldout_policy": copy.deepcopy(dict(payload.get("heldout_policy") or {})),
        "workspace_digest": validation["workspace_digest"],
        "cases": cases,
    }


def freeze_workspace(
    workspace: dict[str, Any],
    *,
    corpus_root: Path,
    frozen_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    authoring = validate_workspace(workspace)
    if not authoring["freeze_ready"]:
        details = "; ".join(
            list(authoring["errors"])[:3] + list(authoring["freeze_blockers"])[:5]
        )
        raise ValueError(f"annotation workspace is not freeze-ready: {details}")

    manifest = compile_workspace(workspace, freeze=True, frozen_at=frozen_at)
    report = validate_corpus(
        manifest,
        base_dir=Path(corpus_root).resolve(),
        verify_files=True,
    )
    if not report["strict_quality_eligible"]:
        details = "; ".join(
            list(report["errors"])[:3] + list(report["quality_blockers"])[:5]
        )
        raise ValueError(f"compiled corpus is not quality-eligible: {details}")
    manifest["corpus_digest"] = canonical_corpus_digest(manifest)
    return manifest, report
