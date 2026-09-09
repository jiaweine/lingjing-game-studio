from __future__ import annotations

import asyncio

from worldforge.models import RunConfig, RunSummary, WorldState
from worldforge.runtime.engine import WorldForgeEngine as FrozenWorldForgeEngine
from worldforge.runtime.event_store import EventStore
from worldforge.runtime.self_evolving_engine import SelfEvolvingWorldForgeEngine


def test_event_store_narrow_trace_readers_ignore_unrelated_payloads(tmp_path):
    store = EventStore(tmp_path / "trace.db")
    session_id = "wf-trace-summary"
    store.create_session(session_id)
    store.append(
        session_id,
        "counterfactual.evaluated",
        {"branches": [{"blob": "x" * 20000} for _ in range(4)]},
    )
    store.append(session_id, "action.executed", {"action": "attack", "state": {"hp": 90}})
    store.append(session_id, "action.executed", {"action": "defend", "state": {"hp": 95}})
    store.append(session_id, "action.executed", {"action": "attack", "state": {"hp": 80}})
    completed = store.append(
        session_id,
        "run.completed",
        {"summary": {"status": "completed"}, "final_state": {}, "findings": []},
    )

    latest = store.latest_event_of_type(session_id, "run.completed")
    assert latest is not None
    assert latest.seq == completed.seq
    assert store.payload_value_counts(
        session_id,
        "action.executed",
        "action",
    ) == {"attack": 2, "defend": 1}
    assert store.payload_value_counts(
        session_id,
        "counterfactual.evaluated",
        "action",
    ) == {}


def test_self_evolving_wrapper_does_not_materialize_complete_trace(tmp_path, monkeypatch):
    engine = SelfEvolvingWorldForgeEngine(tmp_path / "wrapper.db")
    session_id = "wf-wrapper-narrow"

    async def fake_kernel_run(self, config, **kwargs):
        current_session_id = str(kwargs.get("session_id") or session_id)
        self.events.create_session(current_session_id, meta={"scenario": config.scenario_id})
        self.events.append(current_session_id, "run.started", {})
        self.events.append(
            current_session_id,
            "counterfactual.evaluated",
            {"branches": [{"blob": "y" * 20000} for _ in range(4)]},
        )
        self.events.append(current_session_id, "action.executed", {"action": "attack"})
        self.events.append(current_session_id, "action.executed", {"action": "attack"})
        self.events.append(current_session_id, "action.executed", {"action": "defend"})
        self.events.append(
            current_session_id,
            "run.completed",
            {
                "summary": {"status": "completed"},
                "final_state": WorldState(terminal=True, outcome="victory").model_dump(),
                "findings": [],
            },
        )
        return RunSummary(
            session_id=current_session_id,
            scenario_id=config.scenario_id,
            status="completed",
            outcome="victory",
            steps=3,
            score=100.0,
            invalid_actions=0,
            recovery_events=0,
            started_at=1.0,
            finished_at=2.0,
        )

    monkeypatch.setattr(FrozenWorldForgeEngine, "run", fake_kernel_run)

    observed_counts: dict[str, int] = {}
    real_counts = engine.events.payload_value_counts

    def tracked_counts(session, event_type, payload_key):
        result = real_counts(session, event_type, payload_key)
        observed_counts.update(result)
        return result

    monkeypatch.setattr(engine.events, "payload_value_counts", tracked_counts)

    def forbid_full_trace(*_args, **_kwargs):
        raise AssertionError("self-evolving wrapper must not materialize the complete trace")

    monkeypatch.setattr(engine.events, "list_events", forbid_full_trace)

    summary = asyncio.run(
        engine.run(
            RunConfig(
                scenario_id="boss_burst",
                seed=7,
                max_steps=3,
                rollouts_per_branch=1,
                enable_evolution=True,
            ),
            session_id=session_id,
            demo_delay=0,
        )
    )

    assert summary.status == "completed"
    assert summary.outcome == "victory"
    assert observed_counts == {"attack": 2, "defend": 1}
