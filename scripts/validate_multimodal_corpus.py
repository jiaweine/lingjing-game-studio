from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_corpus import (
    assert_protocol_matches,
    validate_corpus,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset")
    parser.add_argument(
        "--protocol",
        default="benchmarks/game-rd-mm-v1/protocol.json",
    )
    parser.add_argument("--verify-files", action="store_true")
    parser.add_argument("--require-quality-eligible", action="store_true")
    parser.add_argument("--protocol-only", action="store_true")
    args = parser.parse_args()

    protocol_path = Path(args.protocol)
    protocol_payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    assert_protocol_matches(protocol_payload)

    if args.protocol_only:
        print(
            json.dumps(
                {
                    "protocol": protocol_payload["name"],
                    "protocol_version": protocol_payload["protocol_version"],
                    "protocol_matches_code": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not args.dataset:
        raise SystemExit("--dataset is required unless --protocol-only is used")

    dataset_path = Path(args.dataset).resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    report = validate_corpus(
        dataset,
        base_dir=dataset_path.parent,
        verify_files=args.verify_files,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report["structurally_valid"]:
        raise SystemExit("corpus has structural errors")
    if args.require_quality_eligible and not report["strict_quality_eligible"]:
        raise SystemExit("corpus is not eligible for measured quality claims")


if __name__ == "__main__":
    main()
