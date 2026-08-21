import uuid

from worldforge.api.app import _run_report, manager


def test_api_run_report_uses_real_verifier_coverage(tmp_path):
    session_id = f"wf-report-coverage-{uuid.uuid4().hex}"
    store = manager.engine.events
    store.create_session(session_id, meta={})
    store.append(
        session_id,
        "run.started",
        {"scenario": {"id": "coverage"}, "policy": {"name": "test"}},
    )
    store.append(
        session_id,
        "action.executed",
        {"verification": {"recommendation": "accept"}},
    )
    store.append(session_id, "action.executed", {})

    report = _run_report(session_id)

    assert report["metrics"]["action_count"] == 2
    assert report["metrics"]["verified_action_count"] == 1
    assert report["metrics"]["verifier_coverage"] == 0.5
    assert report["metrics"]["verified_accept_rate"] == 1.0
