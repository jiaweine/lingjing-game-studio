from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from typing import Any, Iterable

from sqlalchemy import and_, select

from worldforge.product.store import ConversationStore
from worldforge.storage import ObjectStorage


SOURCE_INVENTORY_VERSION = "1.0"
SOURCE_INVENTORY_NAME = "source_inventory.json"
_ALLOWED_KINDS = {"text", "image", "video", "audio"}
_SCOPE_META_KEYS = {
    "build_ref", "build", "version",
    "branch_ref", "branch",
    "commit_ref", "commit", "sha", "git_sha",
    "environment_ref", "environment", "env",
}
_MEDIA_META_KEYS = {
    "kind", "duration", "bit_rate", "has_audio", "width", "height", "fps",
    "sample_rate", "channels", "chars", "lines",
}
_OBJECTIVE_META_KEYS = _SCOPE_META_KEYS | _MEDIA_META_KEYS
_FORBIDDEN_LABEL_KEYS = {
    "query", "relevant", "forbidden_asset_ids", "target_modalities", "source_group",
    "annotation", "scope_eligible", "_context",
}
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_ids(values: Iterable[str] | None) -> list[str]:
    return sorted({str(value).strip() for value in list(values or []) if str(value).strip()})


def _asset_kind(row: dict[str, Any]) -> str | None:
    meta = dict(row.get("meta") or {})
    kind = str(meta.get("kind") or "").strip().lower()
    if kind in _ALLOWED_KINDS:
        return kind
    mime = str(row.get("mime") or "").strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or mime in {
        "application/json", "application/xml", "text/csv",
    }:
        return "text"
    suffix = Path(str(row.get("name") or "")).suffix.lower()
    if suffix in {
        ".txt", ".log", ".md", ".json", ".jsonl", ".yaml", ".yml", ".csv",
        ".tsv", ".xml", ".ini", ".cfg", ".toml", ".py", ".js", ".ts", ".tsx",
        ".jsx", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".shader", ".usf",
        ".ush",
    }:
        return "text"
    return None


def _objective_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(meta[key])
        for key in sorted(_OBJECTIVE_META_KEYS)
        if key in meta and meta[key] is not None
    }


def _safe_filename(asset_id: str, original_name: str, mime: str, kind: str) -> str:
    source_name = Path(original_name or "source").name
    suffix = Path(source_name).suffix.lower()
    if not suffix:
        suffix = mimetypes.guess_extension(mime or "") or (".txt" if kind == "text" else "")
    stem = Path(source_name).stem or "source"
    stem = _SAFE_COMPONENT_RE.sub("-", stem).strip("-._")[:80] or "source"
    safe_id = _SAFE_COMPONENT_RE.sub("-", asset_id).strip("-._")[:96] or "asset"
    return f"{safe_id}__{stem}{suffix[:16]}"


def _json_asset(row: Any) -> dict[str, Any]:
    data = dict(row._mapping if hasattr(row, "_mapping") else row)
    raw_meta = data.get("meta")
    if isinstance(raw_meta, str):
        try:
            data["meta"] = json.loads(raw_meta or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"asset {data.get('id')!r} has invalid meta JSON") from exc
    else:
        data["meta"] = dict(raw_meta or {})
    return data


def select_product_assets(
    store: ConversationStore,
    *,
    workspace_id: str,
    conversation_ids: Iterable[str] | None = None,
    asset_ids: Iterable[str] | None = None,
    all_workspace_assets: bool = False,
) -> list[dict[str, Any]]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    conversations = _clean_ids(conversation_ids)
    explicit_assets = _clean_ids(asset_ids)
    if all_workspace_assets and (conversations or explicit_assets):
        raise ValueError("all_workspace_assets cannot be combined with conversation_ids/asset_ids")
    if not all_workspace_assets and not conversations and not explicit_assets:
        raise ValueError(
            "select at least one conversation_id/asset_id or set all_workspace_assets=true"
        )

    for conversation_id in conversations:
        try:
            store.get_conversation(conversation_id, workspace_id=workspace_id)
        except KeyError as exc:
            raise ValueError(
                f"conversation {conversation_id!r} is not in workspace {workspace_id!r}"
            ) from exc

    selected: dict[str, dict[str, Any]] = {}
    if all_workspace_assets or conversations:
        statement = select(store.assets).where(store.assets.c.workspace_id == workspace_id)
        if conversations:
            statement = statement.where(store.assets.c.conversation_id.in_(conversations))
        statement = statement.order_by(store.assets.c.created_at, store.assets.c.id)
        with store.engine.connect() as connection:
            for row in connection.execute(statement).fetchall():
                asset = _json_asset(row)
                selected[str(asset["id"])] = asset

    for asset_id in explicit_assets:
        try:
            asset = store.get_asset(asset_id, workspace_id=workspace_id)
        except KeyError as exc:
            raise ValueError(
                f"asset {asset_id!r} is not in workspace {workspace_id!r}"
            ) from exc
        selected[str(asset["id"])] = asset

    return [selected[key] for key in sorted(selected)]


def stage_product_assets(
    store: ConversationStore,
    storage: ObjectStorage,
    *,
    corpus_root: Path,
    workspace_id: str,
    conversation_ids: Iterable[str] | None = None,
    asset_ids: Iterable[str] | None = None,
    all_workspace_assets: bool = False,
    managed_asset_dir: str = "assets/lingjing",
    inventory_name: str = SOURCE_INVENTORY_NAME,
    force_inventory: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(corpus_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    managed_root = (root / managed_asset_dir).resolve()
    inventory_path = (root / inventory_name).resolve()
    try:
        managed_root.relative_to(root)
        inventory_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("managed_asset_dir and inventory_name must stay inside corpus_root") from exc

    rows = select_product_assets(
        store,
        workspace_id=workspace_id,
        conversation_ids=conversation_ids,
        asset_ids=asset_ids,
        all_workspace_assets=all_workspace_assets,
    )
    exported: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    modality_counts = {kind: 0 for kind in sorted(_ALLOWED_KINDS)}

    for row in rows:
        kind = _asset_kind(row)
        if kind is None:
            skipped.append(
                {
                    "source_asset_id": str(row.get("id") or ""),
                    "reason": "unsupported-benchmark-modality",
                }
            )
            continue
        backend = str(row.get("storage_backend") or "").strip()
        if backend != str(getattr(storage, "name", "") or ""):
            raise ValueError(
                f"asset {row.get('id')!r} uses storage backend {backend!r}, "
                f"but exporter is configured for {getattr(storage, 'name', None)!r}"
            )
        object_key = str(row.get("path") or "").strip()
        if not object_key:
            raise ValueError(f"asset {row.get('id')!r} has no storage object key")
        data = storage.get_bytes(object_key)
        if int(row.get("size") or 0) != len(data):
            raise ValueError(
                f"asset {row.get('id')!r} size mismatch: db={row.get('size')} bytes={len(data)}"
            )
        sha256 = _sha256_bytes(data)
        filename = _safe_filename(
            str(row.get("id") or "asset"),
            str(row.get("name") or "source"),
            str(row.get("mime") or "application/octet-stream"),
            kind,
        )
        destination = (managed_root / filename).resolve()
        try:
            destination.relative_to(managed_root)
        except ValueError as exc:
            raise ValueError("staged asset path escaped managed asset directory") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination.read_bytes()
            if _sha256_bytes(existing) != sha256:
                raise ValueError(
                    f"refusing to overwrite changed staged asset: {destination}"
                )
        else:
            temp_path = destination.with_suffix(destination.suffix + ".tmp")
            temp_path.write_bytes(data)
            temp_path.replace(destination)

        meta = dict(row.get("meta") or {})
        relative_path = destination.relative_to(root).as_posix()
        exported.append(
            {
                "source_asset_id": str(row.get("id") or ""),
                "source_workspace_id": str(row.get("workspace_id") or workspace_id),
                "source_conversation_id": (
                    str(row.get("conversation_id")) if row.get("conversation_id") else None
                ),
                "storage_backend": backend,
                "source_object_key": object_key,
                "original_name": str(row.get("name") or ""),
                "mime": str(row.get("mime") or "application/octet-stream"),
                "kind": kind,
                "size": len(data),
                "sha256": sha256,
                "staged_path": relative_path,
                "source_created_at": float(row.get("created_at") or 0.0),
                "objective_meta": _objective_meta(meta),
            }
        )
        modality_counts[kind] += 1

    selection = {
        "all_workspace_assets": bool(all_workspace_assets),
        "conversation_ids": _clean_ids(conversation_ids),
        "asset_ids": _clean_ids(asset_ids),
    }
    inventory: dict[str, Any] = {
        "schema_version": SOURCE_INVENTORY_VERSION,
        "source": "lingjing-product-store",
        "workspace_id": str(workspace_id),
        "managed_asset_dir": Path(managed_asset_dir).as_posix().rstrip("/"),
        "selection": selection,
        "assets": exported,
        "skipped": skipped,
        "annotation_labels_emitted": False,
        "evidence_claim": "none-source-staging-only",
    }
    inventory["source_inventory_digest"] = _canonical_digest(inventory)

    if inventory_path.exists() and not force_inventory:
        existing = json.loads(inventory_path.read_text(encoding="utf-8"))
        if existing != inventory:
            raise ValueError(
                f"refusing to replace a different source inventory: {inventory_path}; "
                "use a fresh corpus root or force_inventory=true"
            )
    else:
        temp_inventory = inventory_path.with_suffix(inventory_path.suffix + ".tmp")
        temp_inventory.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_inventory.replace(inventory_path)

    report = {
        "workspace_id": str(workspace_id),
        "selected_product_assets": len(rows),
        "staged_assets": len(exported),
        "skipped_assets": len(skipped),
        "modality_counts": modality_counts,
        "managed_asset_dir": inventory["managed_asset_dir"],
        "inventory": str(inventory_path),
        "source_inventory_digest": inventory["source_inventory_digest"],
        "annotation_labels_emitted": False,
        "evidence_claim": "none-source-staging-only",
    }
    return inventory, report


def attach_source_inventory(
    workspace: dict[str, Any],
    *,
    corpus_root: Path,
    inventory_name: str = SOURCE_INVENTORY_NAME,
) -> dict[str, Any]:
    root = Path(corpus_root).resolve()
    inventory_path = (root / inventory_name).resolve()
    try:
        inventory_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source inventory must stay inside corpus_root") from exc
    if not inventory_path.exists():
        return workspace

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if str(inventory.get("schema_version") or "") != SOURCE_INVENTORY_VERSION:
        raise ValueError("unsupported source inventory schema_version")
    if str(inventory.get("source") or "") != "lingjing-product-store":
        raise ValueError("unsupported source inventory origin")
    if inventory.get("annotation_labels_emitted") is not False:
        raise ValueError("source inventory must not contain benchmark annotation labels")
    expected_digest = str(inventory.get("source_inventory_digest") or "")
    digest_payload = copy.deepcopy(inventory)
    digest_payload.pop("source_inventory_digest", None)
    if expected_digest != _canonical_digest(digest_payload):
        raise ValueError("source inventory digest mismatch")

    managed_dir = str(inventory.get("managed_asset_dir") or "").strip().strip("/")
    if not managed_dir:
        raise ValueError("source inventory managed_asset_dir is required")
    managed_root = (root / managed_dir).resolve()
    try:
        managed_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("source inventory managed_asset_dir escapes corpus_root") from exc

    inventory_rows: dict[str, dict[str, Any]] = {}
    source_ids: set[str] = set()
    for index, raw in enumerate(list(inventory.get("assets") or [])):
        row = dict(raw or {})
        forbidden = sorted(_FORBIDDEN_LABEL_KEYS & set(row))
        if forbidden:
            raise ValueError(
                f"source inventory asset[{index}] contains forbidden label fields: {forbidden}"
            )
        source_id = str(row.get("source_asset_id") or "").strip()
        staged_path = str(row.get("staged_path") or "").strip()
        if not source_id or source_id in source_ids:
            raise ValueError(f"source inventory asset[{index}] has invalid/duplicate source_asset_id")
        source_ids.add(source_id)
        if not staged_path or Path(staged_path).is_absolute() or staged_path in inventory_rows:
            raise ValueError(f"source inventory asset[{index}] has invalid/duplicate staged_path")
        if not staged_path.startswith(managed_dir + "/"):
            raise ValueError(f"source inventory asset[{index}] is outside managed_asset_dir")
        objective = dict(row.get("objective_meta") or {})
        unknown_meta = sorted(set(objective) - _OBJECTIVE_META_KEYS)
        if unknown_meta:
            raise ValueError(
                f"source inventory asset[{index}] has non-objective meta keys: {unknown_meta}"
            )
        inventory_rows[staged_path] = row

    result = copy.deepcopy(dict(workspace or {}))
    catalog = [dict(row or {}) for row in list(result.get("asset_catalog") or [])]
    catalog_by_path = {str(row.get("path") or ""): row for row in catalog}
    missing = sorted(set(inventory_rows) - set(catalog_by_path))
    if missing:
        raise ValueError(f"source inventory references missing staged assets: {missing[:3]}")
    unmanaged = sorted(
        path
        for path in catalog_by_path
        if path.startswith(managed_dir + "/") and path not in inventory_rows
    )
    if unmanaged:
        raise ValueError(
            f"managed export directory contains assets outside source inventory: {unmanaged[:3]}"
        )

    merged = 0
    for staged_path, source in inventory_rows.items():
        asset = catalog_by_path[staged_path]
        if str(asset.get("sha256") or "") != str(source.get("sha256") or ""):
            raise ValueError(f"source inventory hash drift for {staged_path!r}")
        if int(asset.get("size") or 0) != int(source.get("size") or 0):
            raise ValueError(f"source inventory size drift for {staged_path!r}")
        meta = dict(asset.get("meta") or {})
        objective = dict(source.get("objective_meta") or {})
        for key in sorted(_OBJECTIVE_META_KEYS):
            if key in objective:
                meta[key] = copy.deepcopy(objective[key])
        meta["source_provenance"] = {
            "source": "lingjing-product-store",
            "source_asset_id": str(source.get("source_asset_id") or ""),
            "source_workspace_id": str(source.get("source_workspace_id") or ""),
            "source_conversation_id": source.get("source_conversation_id"),
            "storage_backend": str(source.get("storage_backend") or ""),
            "original_name": str(source.get("original_name") or ""),
            "source_inventory_digest": expected_digest,
        }
        asset["meta"] = meta
        merged += 1

    report = dict(result.get("asset_catalog_report") or {})
    report["source_inventory"] = {
        "present": True,
        "source": "lingjing-product-store",
        "workspace_id": str(inventory.get("workspace_id") or ""),
        "managed_asset_dir": managed_dir,
        "source_assets": len(inventory_rows),
        "merged_assets": merged,
        "source_inventory_digest": expected_digest,
        "annotation_labels_emitted": False,
        "evidence_claim": "none-source-staging-only",
    }
    result["asset_catalog"] = catalog
    result["asset_catalog_report"] = report
    result["source_inventory"] = copy.deepcopy(report["source_inventory"])
    return result
