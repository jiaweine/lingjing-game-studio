from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_annotation_readiness import (
    annotation_readiness_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit human annotation progress for game-rd-mm-v1. "
            "This command never generates benchmark labels."
        )
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output")
    parser.add_argument("--require-annotation-complete", action="store_true")
    parser.add_argument("--require-protocol-coverage", action="store_true")
    parser.add_argument("--require-ready-for-freeze", action="store_true")
    args = parser.parse_args()

    workspace_path = Path(args.workspace).resolve()
    workspace = json.loads(workspace_path.read_text(encoding="utf-8"))
    report = annotation_readiness_report(workspace)

    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = workspace_path.parent / output
        _write_json(output.resolve(), report)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    coverage = dict(report["coverage"])
    if args.require_annotation_complete and not coverage["annotation_complete"]:
        raise SystemExit(2)
    if args.require_protocol_coverage and not coverage["protocol_coverage_ready"]:
        raise SystemExit(3)
    if args.require_ready_for_freeze and not coverage["ready_for_strict_freeze_attempt"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
