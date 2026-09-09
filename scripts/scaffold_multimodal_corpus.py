from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_product_export import attach_source_inventory
from worldforge.benchmarks.multimodal_workspace import new_workspace, validate_workspace


def _write_json(path: Path, payload: dict, *, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--asset-dir", default="assets")
    parser.add_argument("--source-inventory", default="source_inventory.json")
    parser.add_argument("--output", default="workspace.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).resolve()
    workspace = new_workspace(corpus_root, asset_dir=args.asset_dir)
    workspace = attach_source_inventory(
        workspace,
        corpus_root=corpus_root,
        inventory_name=args.source_inventory,
    )
    validation = validate_workspace(workspace)
    output = Path(args.output)
    if not output.is_absolute():
        output = corpus_root / output
    output = output.resolve()
    try:
        output.relative_to(corpus_root)
    except ValueError as exc:
        raise SystemExit("workspace output must stay inside corpus root") from exc
    _write_json(output, workspace, force=args.force)

    report = {
        "workspace": str(output),
        "workspace_version": workspace["workspace_version"],
        "assets": validation["assets"],
        "cases": validation["cases"],
        "asset_catalog_report": workspace["asset_catalog_report"],
        "source_inventory": workspace.get("source_inventory"),
        "freeze_ready": validation["freeze_ready"],
        "evidence_claim": "none-annotation-workspace",
        "next_step": (
            "author human queries/relevance/scope labels in workspace cases; "
            "do not use retrieval output to create held-out labels"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
