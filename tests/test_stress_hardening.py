from __future__ import annotations

import asyncio
import importlib
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from worldforge.api.manager import RunManager
from worldforge.models import BenchmarkRequest, RunConfig, RunSummary, RuntimeEvent
from worldforge.observability import SlidingWindowRateLimiter
from worldforge.product.analyzer import ProductAnalyzer


def test_runtime_request_work_is_bounded():
    with pytest.raises(ValidationError):
        RunConfig(max_steps=65)
    with pytest.raises(ValidationError):
        RunConfig(max_steps=0)
    with pytest.raises(ValidationError):
        BenchmarkRequest(scenarios=[f"scenario-{index}" for index in range(17)])


def test_rate_limiter_key_cardinality_is_bounded():
    limiter = SlidingWindowRateLimiter(
        10,
        max_keys=128,
        sweep_interval_seconds=3600,
    )
    for index in range(2_000):
        limiter.check(f"workspace:user:host-{index}")
    assert limiter.tracked_keys == 128


def test_rate_limiter_still_enforces_repeated_key():
    limiter = SlidingWindowRateLimiter(2, max_keys=128)
    limiter.check("same-key")
    limiter.check("same-key")
    with pytest.raises(HTTPException) as exc:
        limiter.check("same-key")
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


class _NoProviderRegistry:
    def choose(self, preferred, assets):
        return None


class _FailIfRunEngine:
    def __init__(self):
        self.calls = 0

    async def run(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("normal product analysis must not run BalanceLab")


def test_auto_analysis_without_provider_does_not_fabricate_or_run_balancelab():
    async def exercise():
        engine = _FailIfRunEngine()
        analyzer = ProductAnalyzer(engine, _NoProviderRegistry())
        events = []

        async def sink(type_, payload):
            events.append((type_, payload))

        result = await analyzer.run(
            text="Boss 二阶段会不会有秒杀 bug？",
            assets=[],
            provider_key="auto",
            sink=sink,
            history=[],
        )
        assert engine.calls == 0
        assert result["runtime"] is None
        assert result["context"]["analysis_grounded"] is False
        assert "证据不足" in result["answer"]
        assert "高爆发阶段的资源衔接问题" not in result["answer"]
        assert result["deliverables"][0]["type"] == "validation_plan"
        assert not any(item.get("type") == "replay" for item in result["evidence"])
        assert events[-1][1]["percent"] == 100

    asyncio.run(exercise())


class _DemoEngine:
    def __init__(self):
        self.configs = []

    async def run(self, config, **kwargs):
        self.configs.append(config)
        return RunSummary(
            session_id="wf-demo",
            scenario_id=config.scenario_id,
            status="completed",
            outcome="victory",
            steps=3,
            score=10,
            started_at=time.time(),
            finished_at=time.time(),
        )


def test_demo_self_check_is_not_user_evidence_or_evolution_credit():
    async def exercise():
        engine = _DemoEngine()
        analyzer = ProductAnalyzer(engine, _NoProviderRegistry())

        async def sink(type_, payload):
            return None

        result = await analyzer.run(
            text="复现这个战斗问题",
            assets=[
                {
                    "id": "asset-1",
                    "name": "battle.log",
                    "mime": "text/plain",
                    "meta": {"kind": "text", "preview": "damage=999"},
                }
            ],
            provider_key="demo",
            sink=sink,
            history=[],
            human_feedback_gate=True,
        )
        assert len(engine.configs) == 1
        assert engine.configs[0].enable_evolution is False
        assert result["runtime"]["scope"] == "internal_balance_lab"
        assert result["runtime"]["user_evidence"] is False
        assert result["context"]["analysis_grounded"] is False
        assert len(result["evidence"]) == 1
        assert result["evidence"][0]["asset_id"] == "asset-1"
        assert "不是你的游戏环境" in result["answer"]

    asyncio.run(exercise())


class _MemoryEventStore:
    def __init__(self):
        self.rows = {}

    def append(self, session_id, event_type, payload):
        rows = self.rows.setdefault(session_id, [])
        event = RuntimeEvent(
            session_id=session_id,
            seq=len(rows) + 1,
            event_type=event_type,
            payload=payload,
            ts=time.time(),
            hash=f"hash-{len(rows) + 1}",
            prev_hash=f"hash-{len(rows)}" if rows else "",
        )
        rows.append(event)
        return event

    def list_events(self, session_id, after_seq=0):
        return [event for event in self.rows.get(session_id, []) if event.seq > after_seq]


class _FastEngine:
    def __init__(self):
        self.events = _MemoryEventStore()

    async def run(self, config, *, session_id, sink, session_meta):
        completed = self.events.append(
            session_id,
            "run.completed",
            {"scenario_id": config.scenario_id},
        )
        await sink(completed)
        return RunSummary(
            session_id=session_id,
            scenario_id=config.scenario_id,
            status="completed",
            outcome="victory",
            steps=1,
            score=1,
            started_at=time.time(),
            finished_at=time.time(),
        )


def test_run_manager_reclaims_finished_tasks_and_bounds_summaries(tmp_path):
    async def exercise():
        manager = RunManager(tmp_path, summary_limit=2)
        manager.engine = _FastEngine()
        session_ids = []
        for seed in range(3):
            session_ids.append(await manager.start(RunConfig(seed=seed, max_steps=1)))
        for _ in range(100):
            if not manager.tasks:
                break
            await asyncio.sleep(0)

        assert manager.tasks == {}
        assert len(manager.summaries) == 2
        assert session_ids[0] not in manager.summaries
        assert manager.status(session_ids[0])["status"] == "completed"
        assert (await manager.cancel(session_ids[0]))["status"] == "completed"

        queue = manager.subscribe(session_ids[-1])
        assert session_ids[-1] in manager.queues
        manager.unsubscribe(session_ids[-1], queue)
        assert session_ids[-1] not in manager.queues
        assert manager.subscriber_count == 0

    asyncio.run(exercise())


def test_run_manager_bounds_websocket_subscribers(tmp_path):
    manager = RunManager(
        tmp_path,
        max_subscribers_per_run=1,
        max_subscribers_total=1,
    )
    first = manager.subscribe("wf-test")
    assert manager.subscriber_count == 1
    with pytest.raises(RuntimeError, match="subscribers"):
        manager.subscribe("wf-test")
    with pytest.raises(RuntimeError, match="subscribers"):
        manager.subscribe("wf-other")
    manager.unsubscribe("wf-test", first)
    assert manager.subscriber_count == 0
    assert "wf-test" not in manager.queues


def test_run_report_uses_real_verification_coverage_and_failure_status(monkeypatch):
    api_app = importlib.import_module("worldforge.api.app")
    session_id = "wf-report-test"
    events = [
        RuntimeEvent(
            session_id=session_id,
            seq=1,
            event_type="run.started",
            payload={"scenario": {"scenario_id": "boss_burst"}, "policy": {"name": "test"}},
            ts=1,
            hash="h1",
            prev_hash="",
        ),
        RuntimeEvent(
            session_id=session_id,
            seq=2,
            event_type="action.executed",
            payload={"verification": {"recommendation": "accept"}},
            ts=2,
            hash="h2",
            prev_hash="h1",
        ),
        RuntimeEvent(
            session_id=session_id,
            seq=3,
            event_type="action.executed",
            payload={},
            ts=3,
            hash="h3",
            prev_hash="h2",
        ),
        RuntimeEvent(
            session_id=session_id,
            seq=4,
            event_type="run.failed",
            payload={"error": "boom"},
            ts=4,
            hash="h4",
            prev_hash="h3",
        ),
    ]

    class _Events:
        def list_events(self, value):
            assert value == session_id
            return events

        def verify_chain(self, value):
            return value == session_id

    class _Policy:
        def card_dict(self):
            return {"name": "fallback"}

    fake_manager = SimpleNamespace(
        engine=SimpleNamespace(events=_Events(), policy_model=_Policy())
    )
    monkeypatch.setattr(api_app, "manager", fake_manager)
    report = api_app._run_report(session_id)
    assert report["status"] == "failed"
    assert report["metrics"]["verifier_coverage"] == 0.5
    assert report["metrics"]["verified_accept_rate"] == 1.0


def test_viewer_cannot_trigger_expensive_runtime_compute():
    api_app = importlib.import_module("worldforge.api.app")
    owner_client = TestClient(api_app.app)
    owner_email = f"stress-owner-{uuid.uuid4().hex[:10]}@example.com"
    registration = owner_client.post(
        "/api/auth/register",
        json={
            "email": owner_email,
            "password": "strong-password-123",
            "name": "Stress Owner",
            "workspace_name": f"Stress {uuid.uuid4().hex[:6]}",
        },
    )
    assert registration.status_code == 200

    invite_response = owner_client.post(
        "/api/workspace/invites",
        json={"role": "member", "email": None},
    )
    assert invite_response.status_code == 200

    viewer_client = TestClient(api_app.app)
    viewer_email = f"stress-viewer-{uuid.uuid4().hex[:10]}@example.com"
    viewer_registration = viewer_client.post(
        "/api/auth/register",
        json={
            "email": viewer_email,
            "password": "strong-password-456",
            "name": "Stress Viewer",
            "workspace_name": "unused",
            "invite_token": invite_response.json()["token"],
        },
    )
    assert viewer_registration.status_code == 200

    members = owner_client.get("/api/workspace/members").json()
    viewer = next(row for row in members if row["email"] == viewer_email)
    assert owner_client.patch(
        f"/api/workspace/members/{viewer['id']}",
        json={"role": "viewer"},
    ).status_code == 200

    assert viewer_client.post(
        "/api/runs",
        json={"scenario_id": "boss_burst", "max_steps": 1},
    ).status_code == 403
    assert viewer_client.get(
        "/api/selfplay/boss_burst?seeds=2"
    ).status_code == 403
    assert viewer_client.post(
        "/api/benchmarks",
        json={"seeds": 4},
    ).status_code == 403


def test_external_worker_lease_requeues_then_fails_after_attempt_budget(tmp_path):
    from worldforge.product.store import ConversationStore
    from worldforge.worker import requeue_expired_jobs, renew_job_lease

    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = store.create_user_workspace(
        email=f"lease-{uuid.uuid4().hex[:10]}@example.com",
        name="Lease Owner",
        password_hash="hashed",
        workspace_name="Lease Lab",
    )
    conversation = store.create_conversation(
        "lease recovery",
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    job = store.enqueue_job(
        workspace_id=owner["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "recover", "asset_ids": []},
    )

    claimed = store.claim_job("worker-a", job_id=job["id"])
    assert claimed and claimed["attempts"] == 1
    assert renew_job_lease(store, job["id"], "wrong-worker", now=100.0) is False
    assert renew_job_lease(store, job["id"], "worker-a", now=100.0) is True

    first = requeue_expired_jobs(
        store,
        lease_seconds=30,
        max_attempts=3,
        now=200.0,
    )
    assert first == {"requeued": 1, "failed": 0}
    recovered = store.get_job(job["id"], workspace_id=owner["workspace_id"])
    assert recovered["status"] == "queued"
    assert recovered["worker_id"] is None
    assert recovered["claimed_at"] is None

    claimed = store.claim_job("worker-b", job_id=job["id"])
    assert claimed and claimed["attempts"] == 2
    assert renew_job_lease(store, job["id"], "worker-b", now=100.0) is True
    second = requeue_expired_jobs(
        store,
        lease_seconds=30,
        max_attempts=3,
        now=200.0,
    )
    assert second == {"requeued": 1, "failed": 0}

    claimed = store.claim_job("worker-c", job_id=job["id"])
    assert claimed and claimed["attempts"] == 3
    assert renew_job_lease(store, job["id"], "worker-c", now=100.0) is True
    final = requeue_expired_jobs(
        store,
        lease_seconds=30,
        max_attempts=3,
        now=200.0,
    )
    assert final == {"requeued": 0, "failed": 1}
    failed = store.get_job(job["id"], workspace_id=owner["workspace_id"])
    assert failed["status"] == "failed"
    assert failed["worker_id"] is None
    assert failed["claimed_at"] is None
    assert failed["completed_at"] == 200.0
    assert "lease expired" in failed["last_error"]
    assert store.get_conversation(
        conversation["id"], workspace_id=owner["workspace_id"]
    )["status"] == "blocked"


def test_external_reaper_never_steals_api_inprocess_job(tmp_path):
    from worldforge.product.store import ConversationStore
    from worldforge.worker import requeue_expired_jobs, renew_job_lease

    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = store.create_user_workspace(
        email=f"inprocess-{uuid.uuid4().hex[:10]}@example.com",
        name="Inprocess Owner",
        password_hash="hashed",
        workspace_name="Inprocess Lab",
    )
    conversation = store.create_conversation(
        "inprocess lease",
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    job = store.enqueue_job(
        workspace_id=owner["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "keep", "asset_ids": []},
    )
    assert store.claim_job("api-inprocess", job_id=job["id"])
    assert renew_job_lease(store, job["id"], "api-inprocess", now=100.0)

    result = requeue_expired_jobs(
        store,
        lease_seconds=30,
        max_attempts=1,
        now=10_000.0,
    )
    assert result == {"requeued": 0, "failed": 0}
    current = store.get_job(job["id"], workspace_id=owner["workspace_id"])
    assert current["status"] == "running"
    assert current["worker_id"] == "api-inprocess"
