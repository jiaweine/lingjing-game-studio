from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.memory_ingestion_load_benchmark import run_load_protocol


ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_multiworker_load_smoke_completes_without_duplicate_or_lost_ingestion():
    result = run_load_protocol(events=12, workers=2)
    assert result["evidence_class"] == "sqlite-concurrency-mechanism-smoke"
    assert result["quality_claim"] == "none-load-protocol-only"
    assert result["events"] == 12
    assert result["completed_receipts"] == 12
    assert result["proposal_rows"] == 12
    assert result["failed_receipts"] == 0
    assert result["duplicate_receipt_ids"] == 0
    assert result["analysis_jobs_retained"] == 0
    assert result["complete"] is True


def test_external_database_requires_explicit_disposable_confirmation():
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "memory_ingestion_load_benchmark.py"),
            "--database-url",
            "postgresql://example.invalid/never-connect",
            "--events",
            "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode != 0
    assert "--confirm-disposable-database" in (completed.stderr + completed.stdout)


def test_load_benchmark_cli_emits_machine_readable_protocol_result():
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "memory_ingestion_load_benchmark.py"),
            "--events",
            "8",
            "--workers",
            "2",
            "--require-complete",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["benchmark"] == "memory-ingestion-multiworker-load-v1"
    assert payload["complete"] is True
    assert payload["evidence_class"] == "sqlite-concurrency-mechanism-smoke"
