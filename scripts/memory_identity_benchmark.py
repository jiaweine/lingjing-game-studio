from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.memory_identity_eval import run_memory_identity_benchmark


def main() -> None:
    result = run_memory_identity_benchmark()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
