from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import create_engine

from worldforge.integrations.game_adapter import (
    FrozenKernelGameAdapterGateway,
    GameAdapterError,
    GameAdapterRequest,
    SqlGameAdapterReplayStore,
    SyntheticContractAdapter,
)


def _request(gateway, adapter, *, now=1000.0, action_id="act-1", scope=None):
    scope = dict(scope or {"build_ref": "1.4.7", "branch_ref": "release"})
    ticket = gateway.issue_ticket(
        adapter_id=adapter._capabilities.adapter_id,
        action_id=action_id,
        scope=scope,
        ttl_seconds=30,
        now=now,
    )
    return GameAdapterRequest(
        action_id=action_id,
        action={"kind": "inspect", "target": "boss"},
        scope=scope,
        evidence_requests=("logs", "screenshot"),
        dry_run=True,
        ticket=ticket,
    )


def test_adapter_observation_is_never_canonical_or_verifier_authority():
    gateway = FrozenKernelGameAdapterGateway("0123456789abcdef0123456789abcdef")
    adapter = SyntheticContractAdapter()
    request = _request(gateway, adapter)

    observation = asyncio.run(gateway.execute(adapter, request, now=1001.0))

    assert observation.status == "dry-run"
    assert observation.canonical_write_allowed is False
    assert observation.verifier_status == "not-run"
    assert observation.evidence_class == "external-engine-observation-unverified"
    assert len(observation.evidence) == 1
    evidence = observation.evidence[0]
    assert evidence["provenance"]["source_type"] == "game-adapter"
    assert evidence["provenance"]["verified"] is False
    assert evidence["provenance"]["adapter_id"] == "synthetic-contract"
    # The synthetic remote tried to forge source_type=verifier in metadata; the gateway only
    # carries allowlisted objective metadata and injects its own non-verifier provenance.
    assert "source_type" not in evidence["meta"]


def test_adapter_ticket_is_scope_bound_and_tampering_is_rejected_before_dispatch():
    gateway = FrozenKernelGameAdapterGateway("0123456789abcdef0123456789abcdef")
    adapter = SyntheticContractAdapter()
    request = _request(gateway, adapter)
    tampered = replace(request, scope={"build_ref": "2.0.0", "branch_ref": "release"})

    with pytest.raises(GameAdapterError, match="scope mismatch"):
        asyncio.run(gateway.execute(adapter, tampered, now=1001.0))
    assert adapter.calls == 0


def test_adapter_ticket_replay_and_expiry_are_fail_closed():
    gateway = FrozenKernelGameAdapterGateway("0123456789abcdef0123456789abcdef")
    adapter = SyntheticContractAdapter()
    request = _request(gateway, adapter)

    asyncio.run(gateway.execute(adapter, request, now=1001.0))
    assert adapter.calls == 1
    with pytest.raises(GameAdapterError, match="replay"):
        asyncio.run(gateway.execute(adapter, request, now=1002.0))
    assert adapter.calls == 1

    expired = _request(gateway, adapter, now=2000.0, action_id="act-expired")
    with pytest.raises(GameAdapterError, match="expired"):
        asyncio.run(gateway.execute(adapter, expired, now=2031.0))
    assert adapter.calls == 1


def test_durable_replay_store_rejects_ticket_across_gateway_instances(tmp_path):
    database = tmp_path / "adapter-replay.sqlite3"
    first_engine = create_engine(f"sqlite:///{database.as_posix()}")
    second_engine = create_engine(f"sqlite:///{database.as_posix()}")
    first_store = SqlGameAdapterReplayStore(first_engine, auto_create_schema=True)
    second_store = SqlGameAdapterReplayStore(second_engine)
    secret = "0123456789abcdef0123456789abcdef"
    first_gateway = FrozenKernelGameAdapterGateway(secret, replay_store=first_store)
    second_gateway = FrozenKernelGameAdapterGateway(secret, replay_store=second_store)
    adapter = SyntheticContractAdapter()
    request = _request(first_gateway, adapter, action_id="durable-act")

    observation = asyncio.run(first_gateway.execute(adapter, request, now=1001.0))
    assert observation.status == "dry-run"
    assert adapter.calls == 1

    with pytest.raises(GameAdapterError, match="replay"):
        asyncio.run(second_gateway.execute(adapter, request, now=1002.0))
    assert adapter.calls == 1


def test_durable_replay_store_database_failure_is_fail_closed(tmp_path):
    database = tmp_path / "missing-replay-schema.sqlite3"
    store = SqlGameAdapterReplayStore(create_engine(f"sqlite:///{database.as_posix()}"))
    gateway = FrozenKernelGameAdapterGateway(
        "0123456789abcdef0123456789abcdef",
        replay_store=store,
    )
    adapter = SyntheticContractAdapter()
    request = _request(gateway, adapter, action_id="missing-schema")

    with pytest.raises(GameAdapterError, match="replay store unavailable"):
        asyncio.run(gateway.execute(adapter, request, now=1001.0))
    assert adapter.calls == 0


def test_adapter_requires_kernel_ticket_and_mutation_capability():
    gateway = FrozenKernelGameAdapterGateway("0123456789abcdef0123456789abcdef")
    adapter = SyntheticContractAdapter()
    no_ticket = GameAdapterRequest(
        action_id="missing-ticket",
        action={"kind": "inspect"},
        scope={},
        dry_run=True,
        ticket=None,
    )
    with pytest.raises(GameAdapterError, match="requires a Frozen Kernel ticket"):
        asyncio.run(gateway.execute(adapter, no_ticket))

    request = _request(gateway, adapter, action_id="mutating")
    mutating = replace(request, dry_run=False)
    with pytest.raises(GameAdapterError, match="mutating action capability"):
        asyncio.run(gateway.execute(adapter, mutating, now=1001.0))
    assert adapter.calls == 0
