from __future__ import annotations

import asyncio

from sqlalchemy import create_engine

from worldforge.integrations.game_adapter import (
    FrozenKernelGameAdapterGateway,
    GameAdapterError,
    GameAdapterRequest,
    SqlGameAdapterReplayStore,
    SyntheticContractAdapter,
)


def test_same_ticket_fan_in_dispatches_at_most_once(tmp_path):
    database = tmp_path / "adapter-replay.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    replay_store = SqlGameAdapterReplayStore(engine, auto_create_schema=True)
    gateway = FrozenKernelGameAdapterGateway(
        "0123456789abcdef0123456789abcdef",
        replay_store=replay_store,
    )
    adapter = SyntheticContractAdapter()
    action = {"kind": "inspect", "target": "boss"}
    scope = {"build_ref": "1.4.7", "branch_ref": "release"}
    evidence_requests = ("logs", "screenshot")
    ticket = gateway.issue_ticket(
        adapter_id="synthetic-contract",
        action_id="fan-in-action",
        action=action,
        scope=scope,
        dry_run=True,
        evidence_requests=evidence_requests,
        ttl_seconds=30,
        now=1000.0,
    )
    request = GameAdapterRequest(
        action_id="fan-in-action",
        action=action,
        scope=scope,
        evidence_requests=evidence_requests,
        dry_run=True,
        ticket=ticket,
    )

    async def race():
        return await asyncio.gather(
            *(gateway.execute(adapter, request, now=1001.0) for _ in range(32)),
            return_exceptions=True,
        )

    results = asyncio.run(race())
    successes = [row for row in results if not isinstance(row, BaseException)]
    failures = [row for row in results if isinstance(row, BaseException)]

    # Under SQLite fan-in some losers may fail because the durable replay store is briefly
    # locked instead of reaching the unique-key replay rejection. Both outcomes are fail-closed;
    # the authority invariant is that only one request can reach the external adapter.
    assert len(successes) == 1
    assert len(failures) == 31
    assert all(isinstance(row, GameAdapterError) for row in failures)
    assert adapter.calls == 1
