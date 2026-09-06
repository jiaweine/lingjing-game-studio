from __future__ import annotations

import json

from worldforge.benchmarks import run_context_benchmark


def main() -> None:
    result = run_context_benchmark()
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.passed:
        raise SystemExit("Context correctness benchmark failed")


if __name__ == "__main__":
    main()
