from dataclasses import dataclass

import pytest

from worldforge.runtime.run_report import build_run_report


@dataclass
class Event:
    event_type: str
    payload: dict


def test_run_report_uses_real_verifier_coverage_and_keeps_harness_evolution():
    events = [
        Event(
            "run.started",
            {
                "scenario": {"id": "boss_burst"},
                "policy": {"name": "WorldForge Policy", "generation": 3},
            },
        ),
        Event("decision.committed", {"confidence": 0.8, "latency_ms": 12.0}),
        Event("action.executed", {"verification": {"recommendation": "accept"}}),
        Event("action.executed", {"verification": {"recommendation": "rollback"}}),
        Event("action.executed", {}),
        Event("counterfactual.evaluated", {"branches": [{}, {}, {}]}),
        Event("runtime.rollback", {"reason": ["bad"]}),
        Event("runtime.replan", {"alternative": "defend"}),
        Event("qa.finding", {"severity": "high"}),
        Event("policy.optimization", {"accepted": True}),
        Event("harness.evolution", {"promoted": True}),
        Event(
            "run.completed",
            {
                "summary": {"status": "completed"},
                "final_state": {"outcome": "victory"},
            },
        ),
    ]

    report = build_run_report(
        "wf-test",
        events,
        hash_chain_valid=True,
    )

    assert report["status"] == "completed"
    assert report["metrics"]["action_count"] == 3
    assert report["metrics"]["verified_action_count"] == 2
    assert report["metrics"]["verifier_coverage"] == 0.6667
    assert report["metrics"]["verified_accept_rate"] == 0.5
    assert report["metrics"]["counterfactual_futures"] == 3
    assert report["metrics"]["hash_chain_valid"] is True
    assert report["evolution"] == [
        {"accepted": True},
        {"promoted": True},
    ]


def test_run_report_uses_policy_fallback_and_callable_hash_check():
    events = [Event("run.started", {"scenario": {}})]
    called = []

    report = build_run_report(
        "wf-running",
        events,
        policy_fallback={"name": "fallback"},
        hash_chain_valid=lambda: called.append(True) or True,
    )

    assert report["status"] == "running"
    assert report["policy"] == {"name": "fallback"}
    assert report["metrics"]["action_count"] == 0
    assert report["metrics"]["verifier_coverage"] == 0.0
    assert called == [True]


def test_run_report_rejects_empty_event_stream():
    with pytest.raises(KeyError, match="wf-missing"):
        build_run_report("wf-missing", [])
