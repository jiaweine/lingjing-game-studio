# Lingjing GameAdapter protocol v1

The GameAdapter boundary lets a Unity, Unreal or custom-engine bridge execute an explicitly authorized action and return engine observations without becoming a source of canonical WorldForge truth.

This repository contains the protocol, reference HTTP client, Frozen Kernel ticket gateway, durable replay-store implementation and synthetic conformance tests. It does **not** ship a Unity package, Unreal plugin, or evidence from a real game project.

## Authority model

The adapter is an **actuator and evidence source**, not a verifier and not a canonical-state writer.

Every execution request must carry a short-lived Frozen Kernel HMAC ticket bound to:

- adapter id;
- action id;
- project/build scope digest;
- issue/expiry timestamps;
- a one-use nonce.

The gateway consumes the ticket before dispatch. A timeout is therefore treated as ambiguous: the caller must make a new explicit kernel decision instead of silently retrying a potentially mutating request.

Adapter output is always returned as:

```text
canonical_write_allowed = false
verifier_status = not-run
evidence_class = external-engine-observation-unverified
```

A later Frozen Kernel verifier may inspect the observation and make a separate authoritative verification decision. The adapter cannot grant itself that status.

## Replay protection

`FrozenKernelGameAdapterGateway` accepts a `GameAdapterReplayStore` and consumes both the signed `ticket_id` and nonce before calling the engine bridge.

The default `InMemoryGameAdapterReplayStore` is intentionally process-local. It is appropriate for local development and conformance runs where only one kernel process can dispatch work.

Multi-process deployments should use `SqlGameAdapterReplayStore` with the same shared SQL database used by the kernel control plane (or another shared SQL database with the same schema). Alembic revision `20260909_0007` creates:

```text
game_adapter_ticket_replays
  ticket_id    PRIMARY KEY
  nonce        UNIQUE
  expires_at
  consumed_at
```

The primary-key/unique insert is the atomic consume operation across workers. Expired rows may be pruned because ticket expiry is verified before replay-store consumption; deleting an expired row cannot make the expired signed ticket valid again.

Example after migrations are applied:

```python
from sqlalchemy import create_engine
from worldforge.integrations import (
    FrozenKernelGameAdapterGateway,
    SqlGameAdapterReplayStore,
)

engine = create_engine(database_url, pool_pre_ping=True)
replay_store = SqlGameAdapterReplayStore(engine)
gateway = FrozenKernelGameAdapterGateway(
    signing_secret,
    replay_store=replay_store,
)
```

A replay-store/database error is fail-closed: the gateway does not dispatch the external action when it cannot atomically record ticket consumption. `auto_create_schema=True` exists only for isolated/disposable integration tests; production schema ownership remains Alembic.

## Provenance defense

Remote evidence may include objective media metadata such as MIME type, byte size, temporal interval, frame id or engine object id. Arbitrary remote provenance is discarded. In particular an engine bridge cannot send `source_type=verifier` or `verified=true` and have that become trusted provenance.

The gateway injects:

```json
{
  "source_type": "game-adapter",
  "adapter_id": "...",
  "engine": "unity|unreal|custom",
  "ticket_id": "...",
  "verified": false
}
```

Evidence SHA-256 values, when supplied, must be lowercase 64-character hex strings.

## HTTP bridge contract

A bridge process exposes:

```text
GET  /v1/adapter/capabilities
POST /v1/adapter/execute
```

The capabilities response declares engine identity and supported evidence/operation classes. Mutating execution is disabled unless the bridge explicitly declares `mutating_actions=true`. Dry-run is independently declared with `supports_dry_run`.

A request contains:

```json
{
  "action_id": "...",
  "action": {"kind": "..."},
  "scope": {"build_ref": "...", "branch_ref": "..."},
  "evidence_requests": ["logs", "screenshot"],
  "dry_run": true,
  "ticket": {"...": "Frozen Kernel ticket"}
}
```

For snapshot-capable adapters, a successful or dry-run result must return both before/after snapshot digests. The result also echoes the adapter id, action id and ticket id; all three are checked before evidence is accepted.

## Conformance

Synthetic contract smoke:

```bash
python scripts/game_adapter_conformance.py \
  --execute-dry-run \
  --require-conformance
```

This is only protocol/mechanism evidence.

Probe a real bridge without executing an action:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030
```

Run a non-mutating live dry-run only when the bridge is prepared for it:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030 \
  --execute-dry-run \
  --signing-secret "$LINGJING_GAME_ADAPTER_SIGNING_SECRET" \
  --build-ref build-1.4.7 \
  --branch-ref release \
  --require-conformance
```

A successful live conformance result is labeled `external-adapter-contract-probe-not-project-verification`. It proves the bridge speaks the contract; it still does not prove a real game task succeeded.

## What remains external

To claim real Unity/Unreal execution evidence, an actual engine-side bridge/plugin and a real project/capture environment must be supplied outside this repository. Those results then need to pass the same Frozen Kernel verifier/evidence gates as every other execution source.
