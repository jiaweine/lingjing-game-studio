from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldforge.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]


def test_readme_runtime_api_matches_registered_routes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    app_source = (ROOT / "worldforge" / "api" / "app.py").read_text(
        encoding="utf-8"
    )

    documented = (
        "POST /api/runs",
        "GET /api/runs/{id}",
        "GET /api/runs/{id}/events",
        "WS /ws/runs/{id}",
        "POST /api/runs/{id}/cancel",
    )
    registered = (
        '@app.post("/api/runs")',
        '@app.get("/api/runs/{session_id}")',
        '@app.get("/api/runs/{session_id}/events")',
        '@app.websocket("/ws/runs/{session_id}")',
        '@app.post("/api/runs/{session_id}/cancel")',
    )

    assert all(route in readme for route in documented)
    assert all(route in app_source for route in registered)
    assert "GET /runs/{id}/stream" not in readme
    assert "SSE 订阅实时事件" not in readme


def test_ci_compiles_domains_and_checks_migration_round_trip():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "compileall -q worldforge domains migrations scripts tests" in workflow
    assert "compileall -q worldforge domains migrations scripts tests" in makefile
    assert "alembic downgrade base" in workflow
    assert "python scripts/product_fullstack_ui_e2e.py" in workflow
    assert "pytest -q tests/test_postgres_job_leases.py" in workflow


def test_production_compose_shares_runtime_and_configures_job_leases():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert compose.count("runtime_data:/app/outputs/runtime") == 2
    assert "WORLDFORGE_JOB_LEASE_SECONDS" in compose
    assert "WORLDFORGE_JOB_HEARTBEAT_SECONDS" in compose


def test_heartbeat_interval_must_be_shorter_than_lease(monkeypatch):
    monkeypatch.setenv("WORLDFORGE_JOB_LEASE_SECONDS", "30")
    monkeypatch.setenv("WORLDFORGE_JOB_HEARTBEAT_SECONDS", "30")

    with pytest.raises(RuntimeError, match="must be shorter"):
        load_settings()


def test_human_readable_benchmark_metrics_match_reviewed_snapshot():
    snapshot = json.loads(
        (ROOT / "docs" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    report = (ROOT / "BUILD_REPORT.md").read_text(encoding="utf-8")
    benchmark_doc = (ROOT / "docs" / "BENCHMARKING.md").read_text(
        encoding="utf-8"
    )

    assert snapshot["protocol_id"] in readme
    assert snapshot["protocol_id"] in report
    assert snapshot["protocol_id"] in benchmark_doc
    assert (
        f'| Candidate genomes | **{snapshot["candidate_genomes"]}** |' in readme
    )
    assert (
        f'| Passed promotion gate | **{snapshot["accepted_candidates"]}** |'
        in readme
    )
    assert (
        f'| Promoted generation | **{snapshot["promoted_generation"]}** |'
        in readme
    )
    assert f'+{snapshot["sealed_heldout_gain"]:.6f}' in readme
    assert f'{snapshot["heldout_quality"]:.6f}' in readme
    assert str(snapshot["candidate_genomes"]) in report
    assert str(snapshot["candidate_genomes"]) in benchmark_doc
