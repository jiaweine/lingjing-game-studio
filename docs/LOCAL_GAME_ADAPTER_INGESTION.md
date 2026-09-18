# Local GameAdapter evidence ingestion

This product path connects a Lingjing backend running on the **same workstation** as a local Unity GameAdapter bridge and imports verified engine-observation bytes into the current conversation's Assets.

It is intentionally not a cloud-to-desktop tunnel.

## Product flow

1. Start the Unity bridge from **Lingjing → Game Adapter Setup**.
2. Open a Lingjing Bug/Fix task on the same machine.
3. In **本地 Unity / GameAdapter**, keep or enter the loopback endpoint, normally `http://127.0.0.1:9030`.
4. Enter the optional bearer token for the current request.
5. Click **测试连接**.
6. Click **导入引擎证据**.
7. Lingjing issues a Frozen Kernel dry-run ticket, captures the requested read-only observations, independently downloads and SHA-verifies each evidence object, then registers all evidence assets as one batch.
8. Imported assets are labeled **Unity 引擎证据 · 未验证**.
9. The newly imported assets are staged into the next message context, so a subsequent **用此版本重新验证修复** run actually consumes them. Existing manually staged assets are preserved.

The bearer token is request-transient. It is not written to task metadata, asset metadata, audit payloads or events.

## Network boundary

The ingestion API accepts only an origin of the form:

```text
http://127.0.0.1:<port>
```

with ports `1024-65535`.

It rejects:

- `localhost` or arbitrary hostnames;
- LAN / private / public IPs;
- HTTPS endpoints;
- URL credentials;
- endpoint paths, queries or fragments;
- evidence locators outside the exact configured loopback origin and `/v1/adapter/evidence/<32-hex-id>` path.

Redirect following is disabled for evidence downloads.

These restrictions are deliberate SSRF defenses and also document the deployment model honestly.

## Deployment gate

In development, the local bridge path is enabled by default.

In production it is disabled by default. A self-hosted production deployment that intentionally runs Lingjing and Unity on the same machine can opt in with:

```bash
WORLDFORGE_ALLOW_LOCAL_GAME_ADAPTER=1
LINGJING_GAME_ADAPTER_SIGNING_SECRET=<at-least-16-byte-secret>
```

A hosted Lingjing SaaS backend cannot reach the user's Unity Editor through the server's own `127.0.0.1`. Hosted support requires a future outbound local runner/relay.

## Evidence integrity

The capture endpoint:

- requests only `logs`, `snapshot` and/or `screenshot`;
- rejects evidence kinds that were not requested;
- requires a valid SHA-256 from the adapter observation;
- downloads evidence with redirects disabled;
- caps one evidence object at 6 MiB and a capture batch at 12 MiB;
- recomputes SHA-256 locally;
- checks the optional `X-Lingjing-Sha256` header;
- checks MIME type against the accepted objective metadata when present;
- writes object bytes first and registers asset rows in one database transaction;
- cleans the just-written objects when registration fails.

Imported asset provenance stays:

```text
source_type = game-adapter
evidence_class = external-engine-observation-unverified
verifier_status = not-run
canonical_write_allowed = false
```

Engine evidence does not become project truth merely because its bytes and digest are genuine. Known structured probes may be evaluated against Lingjing-owned contracts, but that contract result is still an evaluation of external observation. The task-level Verifier still decides whether the evidence supports reproduction or fix verification.

## Governed probe evaluation

Unity package 0.3.0 can include bounded `LingjingProbeState` observations inside the snapshot. The project reports only observed state/value; it does not provide the acceptance rule.

Lingjing evaluates only pre-registered contracts. The built-in demo contract is:

```text
probe_id: demo.boss_shield.damage_gate
damage_applied_while_shielded + value > 0.01 -> reproduced
damage_blocked_while_shielded + value ~= 0 -> passed
otherwise -> unknown
duplicate governed probe rows -> ambiguous
```

The result is stored as `engine.probe.evaluated` with `authority=contract-evaluation-only`. Unknown project-defined probe IDs receive no automatic verdict.

## Current limitation

This path proves product-side ingestion for a local/self-hosted deployment and includes a checked-in deterministic Boss Shield source fixture. It does not yet provide:

- an outbound desktop runner for hosted SaaS;
- governed game-mutating reproduction actions;
- captured CI evidence from a licensed Unity Editor running the demo fixture;
- automatic promotion of a probe-contract result into the task-level verifier decision.

Those are separate milestones and must not be inferred from a successful connection or evidence import.
