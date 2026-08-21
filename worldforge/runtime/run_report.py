from __future__ import annotations

import statistics
from typing import Any, Callable, Iterable

from .report_metrics import compute_verifier_metrics


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


def build_run_report(
    session_id: str,
    events: Iterable[Any],
    *,
    policy_fallback: dict[str, Any] | None = None,
    hash_chain_valid: bool | Callable[[], bool] = False,
) -> dict[str, Any]:
    """Build the stable run-report payload independently from the HTTP layer."""
    rows = list(events)
    if not rows:
        raise KeyError(session_id)

    by_type: dict[str, list[Any]] = {}
    for event in rows:
        by_type.setdefault(_event_type(event), []).append(event)

    started = by_type.get("run.started", [None])[0]
    completed = by_type.get("run.completed", [None])[-1]
    decisions = by_type.get("decision.committed", [])
    branches = by_type.get("counterfactual.evaluated", [])
    findings = by_type.get("qa.finding", [])
    rollbacks = by_type.get("runtime.rollback", [])
    replans = by_type.get("runtime.replan", [])
    policy_events = by_type.get("policy.prior", [])

    latencies = [
        float(_payload(event).get("latency_ms", 0))
        for event in decisions
        if _payload(event).get("latency_ms") is not None
    ]
    confidences = [
        float(_payload(event).get("confidence", 0))
        for event in decisions
    ]
    branch_count = sum(
        len(_payload(event).get("branches", []))
        for event in branches
    )
    verifier_metrics = compute_verifier_metrics(rows)

    completed_payload = _payload(completed) if completed is not None else {}
    started_payload = _payload(started) if started is not None else {}
    policy = started_payload.get("policy") or policy_fallback or {}
    chain_valid = (
        bool(hash_chain_valid())
        if callable(hash_chain_valid)
        else bool(hash_chain_valid)
    )

    return {
        "session_id": session_id,
        "status": "completed" if completed is not None else "running",
        "scenario": started_payload.get("scenario", {}),
        "policy": policy,
        "summary": completed_payload.get("summary"),
        "final_state": completed_payload.get("final_state"),
        "metrics": {
            "decision_count": len(decisions),
            "action_count": verifier_metrics["action_count"],
            "verified_action_count": verifier_metrics["verified_action_count"],
            "counterfactual_futures": branch_count,
            "rollback_count": len(rollbacks),
            "replan_count": len(replans),
            "finding_count": len(findings),
            "verifier_coverage": verifier_metrics["verifier_coverage"],
            "verified_accept_rate": verifier_metrics["verified_accept_rate"],
            "avg_decision_confidence": (
                round(statistics.mean(confidences), 4)
                if confidences else 0.0
            ),
            "avg_decision_latency_ms": (
                round(statistics.mean(latencies), 2)
                if latencies else 0.0
            ),
            "policy_decision_frames": len(policy_events),
            "event_count": len(rows),
            "hash_chain_valid": chain_valid,
        },
        "findings": [_payload(event) for event in findings],
        "evolution": [
            _payload(event) for event in by_type.get("evolution.patch", [])
        ] + [
            _payload(event) for event in by_type.get("policy.optimization", [])
        ] + [
            _payload(event) for event in by_type.get("harness.evolution", [])
        ],
    }
