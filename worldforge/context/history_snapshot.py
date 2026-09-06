from __future__ import annotations

import hashlib
from typing import Any


def _message_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row.get("id") or "").encode("utf-8", errors="ignore"))
        digest.update(b"\x00")
        digest.update(str(row.get("role") or "").encode("utf-8", errors="ignore"))
        digest.update(b"\x00")
        digest.update(str(row.get("content") or "").encode("utf-8", errors="ignore"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def build_history_snapshot(history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Create an O(1)-size boundary for immutable append-only conversation history."""
    rows = list(history or [])
    return {
        "mode": "append-only-history-boundary-v1",
        "count": len(rows),
        "last_message_id": str(rows[-1].get("id") or "") if rows else None,
        "digest": _message_digest(rows),
    }


def materialize_history_snapshot(
    store,
    *,
    conversation_id: str,
    workspace_id: str,
    snapshot: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize exactly the enqueue-time prefix or return no history on invalidation.

    We never substitute a newer prefix when the boundary cannot be proven. This makes retry
    deterministic while allowing governed deletion to invalidate old queued context.
    """
    data = dict(snapshot or {})
    try:
        count = max(0, int(data.get("count") or 0))
    except (TypeError, ValueError):
        count = 0
    expected_last = str(data.get("last_message_id") or "")
    expected_digest = str(data.get("digest") or "")
    if count == 0:
        empty = []
        valid = expected_digest in {"", _message_digest(empty)}
        return empty, {
            "history_snapshot_valid": valid,
            "history_snapshot_count": 0,
            "history_snapshot_invalidated": not valid,
        }

    current = store.list_messages(conversation_id, workspace_id=workspace_id)
    if len(current) < count:
        return [], {
            "history_snapshot_valid": False,
            "history_snapshot_count": count,
            "history_snapshot_invalidated": True,
            "history_snapshot_reason": "prefix-shorter-than-snapshot",
        }
    prefix = list(current[:count])
    actual_last = str(prefix[-1].get("id") or "") if prefix else ""
    actual_digest = _message_digest(prefix)
    valid = actual_last == expected_last and actual_digest == expected_digest
    if not valid:
        return [], {
            "history_snapshot_valid": False,
            "history_snapshot_count": count,
            "history_snapshot_invalidated": True,
            "history_snapshot_reason": "boundary-or-digest-mismatch",
        }
    return prefix, {
        "history_snapshot_valid": True,
        "history_snapshot_count": count,
        "history_snapshot_invalidated": False,
    }


def history_from_job_payload(
    store,
    *,
    conversation_id: str,
    workspace_id: str,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Backward-compatible reader for old jobs that still carry copied history."""
    if "history_snapshot" in payload:
        return materialize_history_snapshot(
            store,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            snapshot=payload.get("history_snapshot"),
        )
    legacy = list(payload.get("history", []) or [])
    return legacy, {
        "history_snapshot_valid": True,
        "history_snapshot_count": len(legacy),
        "history_snapshot_invalidated": False,
        "history_snapshot_legacy_payload": True,
    }
