from dataclasses import dataclass

from worldforge.runtime.report_metrics import compute_verifier_metrics


@dataclass
class Event:
    event_type: str
    payload: dict


def test_verifier_coverage_tracks_only_actions_with_verification():
    events = [
        Event("action.executed", {"verification": {"recommendation": "accept"}}),
        Event("action.executed", {"verification": {"recommendation": "rollback"}}),
        Event("action.executed", {}),
        Event("world.state", {}),
    ]

    metrics = compute_verifier_metrics(events)

    assert metrics["action_count"] == 3
    assert metrics["verified_action_count"] == 2
    assert metrics["verifier_coverage"] == 0.6667
    assert metrics["verified_accept_rate"] == 0.5


def test_verifier_metrics_do_not_report_perfect_coverage_for_unverified_actions():
    metrics = compute_verifier_metrics([
        {"event_type": "action.executed", "payload": {}},
        {"event_type": "action.executed", "payload": {"verification": {}}},
    ])

    assert metrics == {
        "action_count": 2,
        "verified_action_count": 0,
        "verifier_coverage": 0.0,
        "verified_accept_rate": 0.0,
    }


def test_verifier_acceptance_denominator_is_verified_actions():
    metrics = compute_verifier_metrics([
        {
            "event_type": "action.executed",
            "payload": {"verification": {"recommendation": "continue"}},
        },
        {
            "event_type": "action.executed",
            "payload": {"verification": {"recommendation": "proceed"}},
        },
        {"event_type": "action.executed", "payload": {}},
    ])

    assert metrics["verifier_coverage"] == 0.6667
    assert metrics["verified_accept_rate"] == 1.0
