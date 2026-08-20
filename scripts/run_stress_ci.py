from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "stress-result.json"

COMMANDS = [
    [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_stress_hardening.py",
        "tests/test_fanout.py",
        "tests/test_production_config.py",
        "tests/test_product_metrics.py",
        "tests/test_worker_queue.py",
        "tests/test_migrations.py",
        "tests/test_context_bounds.py",
    ],
    [sys.executable, "scripts/stress_smoke.py"],
]


def main() -> int:
    rows = []
    overall = 0
    for command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output = completed.stdout or ""
        rows.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "output_tail": output[-12000:],
            }
        )
        if completed.returncode != 0:
            overall = completed.returncode or 1
        print(output, end="")

    payload = {
        "ok": overall == 0,
        "python": sys.version,
        "commands": rows,
    }
    RESULT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
