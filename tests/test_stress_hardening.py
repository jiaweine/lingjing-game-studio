from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException
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
