from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name, marker",
    [
        ("memory_benchmark.py", "cross_conversation_recall"),
        ("memory_identity_benchmark.py", "false_merge_rate"),
    ],
)
def test_memory_benchmark_cli_entrypoints(script_name: str, marker: str):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert marker in completed.stdout
