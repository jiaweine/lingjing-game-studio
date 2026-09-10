from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from worldforge.product.job_recovery import (
    DEFAULT_STALE_AFTER_SECONDS,
    fail_stale_running_jobs,
    list_stale_running_jobs,
)
from worldforge.product.store import ConversationStore


def _public_job(row: dict, *, now: float) -> dict:
    claimed_at = row.get("claimed_at")
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "conversation_id": row["conversation_id"],
        "status": row["status"],
        "attempts": int(row.get("attempts") or 0),
        "worker_id": row.get("worker_id"),
        "claimed_at": claimed_at,
        "age_seconds": None
        if claimed_at is None
        else round(max(0.0, now - float(claimed_at)), 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect stale running analysis jobs and, only with --apply, fail them "
            "without replaying model execution or side effects."
        )
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="Explicit SQLAlchemy database URL; no implicit production database is selected.",
    )
    parser.add_argument(
        "--workspace-id",
        default=None,
        help="Optional workspace scope. Omit to inspect all workspaces in the selected DB.",
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=float,
        default=float(DEFAULT_STALE_AFTER_SECONDS),
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Fail fenced stale candidates. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--reason",
        default="operator reconciliation: stale running analysis job failed without replay",
        help="Persisted last_error for jobs changed by --apply.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = time.time()
    asset_dir = Path(tempfile.gettempdir()) / "worldforge-analysis-recovery"
    store = ConversationStore(
        database_url=args.database_url,
        asset_dir=asset_dir,
        auto_create_schema=False,
        seed_dev_identity=False,
    )

    candidates = list_stale_running_jobs(
        store,
        stale_after_seconds=args.stale_after_seconds,
        now=now,
        workspace_id=args.workspace_id,
        limit=args.limit,
    )
    changed = []
    if args.apply:
        changed = fail_stale_running_jobs(
            store,
            stale_after_seconds=args.stale_after_seconds,
            now=now,
            workspace_id=args.workspace_id,
            limit=args.limit,
            reason=args.reason,
        )

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "automatic_replay": False,
        "database_selected_explicitly": True,
        "workspace_id": args.workspace_id,
        "stale_after_seconds": float(args.stale_after_seconds),
        "candidate_count": len(candidates),
        "changed_count": len(changed),
        "candidates": [_public_job(row, now=now) for row in candidates],
        "changed": [_public_job(row, now=now) for row in changed],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
