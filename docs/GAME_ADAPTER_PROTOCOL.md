# Lingjing GameAdapter protocol v1

The GameAdapter boundary lets a Unity, Unreal or custom-engine bridge execute an explicitly authorized action and return engine observations without becoming a source of canonical WorldForge truth.

This repository contains the protocol, reference HTTP client, Frozen Kernel ticket gateway, durable replay-store implementation, synthetic conformance tests, and a conservative **Unity Editor package** under `integrations/unity/com.lingjing.game-adapter`. The Unity package removes the need to implement the transport contract before first connection and now includes bounded read-only Editor evidence capture for logs, scene state and camera-frame screenshots. It remains non-mutating and does **not** by itself prove that a real game bug was reproduced or fixed. The repository still does not ship an Unreal plugin or a checked-in real-project Bug/Fix proof fixture.

## Authority model

The adapter is an **actuator and evidence source**, not a verifier and not a canonical-state writer.

Every execution request must carry a short-lived Frozen Kernel HMAC ticket bound to:

- adapter id;
- action id;
- exact canonical action payload;
- `dry_run` mode;
- ordered evidence requests;
- project/build scope digest;
- issue/expiry timestamps;
- a one-use nonce.

The exact request semantics are reduced to a canonical SHA-256 `request_digest`, and that digest is included in the HMAC signature. Keeping the same action id and scope is therefore not enough to reuse an authorization for a different target, payload, evidence request, or to upgrade a dry-run into a mutating execution. Request-digest verification occurs before capability checks and before any adapter dispatch.

The gateway consumes the ticket before dispatch. A timeout is therefore treated as ambiguous: the caller must make a new explicit kernel decision and issue a new ticket instead of silently retrying a potentially mutating request.

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

Adapters may additionally expose adapter-owned evidence locators. The first-party Unity package uses:

```text
GET /v1/adapter/evidence/<id>
```

Those evidence URLs are still adapter observations. Fetching bytes and matching their SHA-256 does not grant verifier authority.

The capabilities response declares engine identity and supported evidence/operation classes. Mutating execution is disabled unless the bridge explicitly declares `mutating_actions=true`. Dry-run is independently declared with `supports_dry_run`.

A request contains:

```json
{
  "action_id": "...",
  "action": {"kind": "..."},
  "scope": {"build_ref": "...", "branch_ref": "..."},
  "evidence_requests": ["logs", "snapshot", "screenshot"],
  "dry_run": true,
  "ticket": {
    "ticket_id": "...",
    "adapter_id": "...",
    "action_id": "...",
    "scope_digest": "...",
    "request_digest": "...",
    "issued_at": 0,
    "expires_at": 0,
    "nonce": "...",
    "signature": "..."
  }
}
```

The bridge receives the signed request envelope, but the Frozen Kernel gateway is the component that validates the ticket before dispatch. The ticket cannot be moved onto a modified request because action payload, `dry_run`, evidence requests and scope are all bound into the signed digests.

For snapshot-capable adapters, a successful or dry-run result must return both before/after snapshot digests. The result also echoes the adapter id, action id and ticket id; all three are checked before evidence is accepted.

## Unity package

The first-party Unity package lives at:

```text
integrations/unity/com.lingjing.game-adapter
```

It can be added through Unity Package Manager using the repository Git URL with that package path. In Unity, open **Lingjing → Game Adapter Setup** to start the local bridge, optionally generate a bearer token, test `/v1/adapter/capabilities`, preview local evidence, copy the endpoint, and copy a matching conformance command.

Version `0.3.0` remains intentionally conservative:

- a small runtime `LingjingProbeState` component reports observations only and has no networking/authority;
- the HTTP bridge remains Editor-only and binds only to `127.0.0.1`;
- optional bearer token;
- `supports_dry_run=true`;
- `supports_logs=true`;
- `supports_snapshot=true`;
- `supports_screenshots=true`;
- `mutating_actions=false`;
- log evidence is bounded and common secret-shaped values are redacted;
- scene/editor snapshot evidence is stable enough to produce before/after digests;
- snapshots may include at most 32 bounded `LingjingProbeState` observations from loaded scene objects;
- project probes do not carry pass/fail rules; Lingjing evaluates only pre-registered contracts independently;
- screenshot evidence is a bounded PNG camera frame, not a guarantee of every GameView overlay;
- evidence bytes live only in a short-lived Editor memory cache and are fetched through the same loopback/auth boundary;
- external observation only, never verifier truth.

Unity APIs are touched on the Editor main thread. The HTTP worker queues evidence capture and waits for that main-thread work rather than calling Scene/Camera APIs directly from a background thread.

This package is now a productized activation **and evidence acquisition** path and includes a deterministic checked-in Boss Shield Bug/Fix source fixture plus an independent Lingjing-side probe contract. The repository CI does not run a licensed Unity Editor, so the checked-in source fixture is not itself evidence that Unity compiled or executed it. Explicitly governed mutating project action handlers and captured real Unity execution evidence remain follow-up work.

## Conformance

Synthetic contract + durable replay smoke:

```bash
python scripts/game_adapter_conformance.py \
  --execute-dry-run \
  --durable-replay-smoke \
  --require-conformance
```

This is only protocol/mechanism evidence. The durable replay smoke uses two independently constructed SQLAlchemy engines/gateways sharing one disposable SQLite store and requires the second gateway to reject the already-consumed ticket before a second dispatch. It is not production database or engine performance evidence.

Probe a real bridge without executing an action:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030
```

Run a non-mutating live dry-run and verify that returned evidence bytes are retrievable from the same adapter origin:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030 \
  --execute-dry-run \
  --fetch-evidence \
  --signing-secret "$LINGJING_GAME_ADAPTER_SIGNING_SECRET" \
  --build-ref build-1.4.7 \
  --branch-ref release \
  --require-conformance
```

Add `--require-screenshot` when the capture environment must contain an available game/scene camera frame.

The fetch probe rejects evidence locators outside the configured adapter `/v1/adapter/evidence/` prefix and recomputes SHA-256 locally. A successful live conformance result is still labeled `external-adapter-contract-probe-not-project-verification`. It proves the bridge speaks the contract and its evidence bytes match the accepted digests; it still does not prove a real game task succeeded.

## What remains external

The repository now includes a Unity bridge for first connection/read-only evidence acquisition, structured runtime probe observations, a deterministic Boss Shield repro source fixture, and an independent Lingjing-side probe contract. A probe contract outcome classifies the returned external observation; it does not by itself make the whole task verified. Real Unity/Unreal **project verification** still requires captured execution in a real Unity environment, explicitly governed project actions where needed, and the task-level Frozen Kernel verification decision. Unreal still requires an external bridge/plugin. None of those observations become verified facts until they pass the same Frozen Kernel verifier/evidence gates as every other execution source.
