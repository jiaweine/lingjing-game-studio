from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_product_export import stage_product_assets
from worldforge.product.store import ConversationStore
from worldforge.storage import LocalObjectStorage, S3ObjectStorage


def _default_data_dir() -> Path:
    return Path(os.getenv("WORLDFORGE_DATA", ROOT / "outputs" / "runtime")).resolve()


def _default_database_url(data_dir: Path) -> str:
    return os.getenv("DATABASE_URL", f"sqlite:///{(data_dir / 'product.db').as_posix()}")


def _build_storage(args: argparse.Namespace, data_dir: Path):
    if args.storage_backend == "local":
        object_root = Path(args.object_root or (data_dir / "objects")).resolve()
        return LocalObjectStorage(object_root)
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise SystemExit("S3_BUCKET is required for --storage-backend s3")
    return S3ObjectStorage(
        bucket=bucket,
        region=os.getenv("S3_REGION") or None,
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        access_key=os.getenv("S3_ACCESS_KEY") or None,
        secret_key=os.getenv("S3_SECRET_KEY") or None,
    )


def main() -> None:
    data_dir = _default_data_dir()
    parser = argparse.ArgumentParser(
        description=(
            "Read Lingjing product assets into a private multimodal benchmark staging directory. "
            "This command emits no retrieval labels."
        )
    )
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--conversation-id", action="append", default=[])
    parser.add_argument("--asset-id", action="append", default=[])
    parser.add_argument("--all-workspace-assets", action="store_true")
    parser.add_argument("--database-url", default=_default_database_url(data_dir))
    parser.add_argument(
        "--storage-backend",
        choices=["local", "s3"],
        default=os.getenv("WORLDFORGE_STORAGE_BACKEND", "local").strip().lower(),
    )
    parser.add_argument("--object-root", default=None)
    parser.add_argument("--managed-asset-dir", default="assets/lingjing")
    parser.add_argument("--inventory", default="source_inventory.json")
    parser.add_argument("--force-inventory", action="store_true")
    args = parser.parse_args()

    if not args.all_workspace_assets and not args.conversation_id and not args.asset_id:
        parser.error(
            "select --conversation-id/--asset-id, or explicitly use --all-workspace-assets"
        )

    storage = _build_storage(args, data_dir)
    store = ConversationStore(
        data_dir / "product.db",
        data_dir / "assets",
        database_url=args.database_url,
        auto_create_schema=False,
        seed_dev_identity=False,
    )
    _inventory, report = stage_product_assets(
        store,
        storage,
        corpus_root=Path(args.corpus_root),
        workspace_id=args.workspace_id,
        conversation_ids=args.conversation_id,
        asset_ids=args.asset_id,
        all_workspace_assets=args.all_workspace_assets,
        managed_asset_dir=args.managed_asset_dir,
        inventory_name=args.inventory,
        force_inventory=args.force_inventory,
    )
    report["next_step"] = (
        "run scaffold_multimodal_corpus.py; then author queries/relevance/temporal/scope labels "
        "independently of retrieval output"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
