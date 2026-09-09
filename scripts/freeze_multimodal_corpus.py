from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_workspace import freeze_workspace


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--corpus-root")
    parser.add_argument("--frozen-at", required=True)
    parser.add_argument("--output", default="manifest.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace_path = Path(args.workspace).resolve()
    corpus_root = (
        Path(args.corpus_root).resolve()
        if args.corpus_root
        else workspace_path.parent
    )
    workspace = dict(json.loads(workspace_path.read_text(encoding="utf-8")))
    manifest, report = freeze_workspace(
        workspace,
        corpus_root=corpus_root,
        frozen_at=args.frozen_at,
    )

    output = Path(args.output)
    if not output.is_absolute():
        output = corpus_root / output
    output = output.resolve()
    if output.parent != corpus_root:
        raise SystemExit(
            "manifest output must be a direct child of corpus root because "
            "asset paths are manifest-relative"
        )
    if output.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing file: {output}")

    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "manifest": str(output),
        "corpus_digest": report["corpus_digest"],
        "cases": report["cases"],
        "target_modality_cases": report["target_modality_cases"],
        "temporal_cases": report["temporal_cases"],
        "scope_negative_cases": report["scope_negative_cases"],
        "files_verified": report["files_verified"],
        "strict_quality_eligible": report["strict_quality_eligible"],
        "evidence_claim": "corpus-protocol-eligible-not-model-quality",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
