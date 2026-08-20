from __future__ import annotations

from collections.abc import Iterable
from typing import Any


ACCEPT_RECOMMENDATIONS = {"accept", "continue", "proceed"}


def _event_type(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("event_type", ""))
    return str(getattr(event, "event_type", ""))


def _payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        value = event.get("payload", {})
    else:
        value = getattr(event, "payload", {})
    return value if isinstance(value, dict) else {}


def compute_verifier_metrics(events: Iterable[Any]) -> dict[str, float | int]:
    """Compute verifier metrics from durable runtime events.

    Coverage means the fraction of executed actions that actually carry a
    verifier result. Acceptance is measured only across actions that were
    covered by the verifier; uncovered actions therefore cannot make the
    acceptance rate look artificially good or bad.
    """
    actions = [event for event in events if _event_type(event) == "action.executed"]
    covered = []
    accepted = 0

    for event in actions:
        verification = _payload(event).get("verification")
        if not isinstance(verification, dict) or not verification:
            continue
        covered.append(event)
        if verification.get("recommendation") in ACCEPT_RECOMMENDATIONS:
            accepted += 1

    action_count = len(actions)
    verified_action_count = len(covered)
    return {
        "action_count": action_count,
        "verified_action_count": verified_action_count,
        "verifier_coverage": round(
            verified_action_count / action_count if action_count else 0.0,
            4,
        ),
        "verified_accept_rate": round(
            accepted / verified_action_count if verified_action_count else 0.0,
            4,
        ),
    }
