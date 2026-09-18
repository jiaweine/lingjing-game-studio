# Lingjing Game Adapter for Unity

This package is the first-party Unity activation and **read-only evidence** path for Lingjing GameAdapter v1. It runs an Editor-only loopback HTTP bridge so a Unity project can answer the existing GameAdapter contract, capture bounded engine observations, and return them through Frozen Kernel tickets without asking the user to implement a bridge from scratch.

Current package version: `0.2.0`.

## Install

In Unity Package Manager choose **Add package from git URL…** and use:

```text
https://github.com/jiaweine/lingjing-game-studio.git?path=/integrations/unity/com.lingjing.game-adapter#main
```

For development before the branch is merged, replace `#main` with the branch or commit you want to test.

The package targets Unity `2021.3+` and only adds an Editor assembly.

## First connection

1. Open **Lingjing → Game Adapter Setup**.
2. Keep the default loopback port `9030`, or choose another local port.
3. Optionally generate a bearer token. This is recommended when other local processes should not be able to probe the bridge.
4. Click **Start local bridge**.
5. Click **Test connection**. The capabilities payload should include:
   - `engine=unity`
   - `protocol_version=1.0`
   - `supports_dry_run=true`
   - `supports_logs=true`
   - `supports_snapshot=true`
   - `supports_screenshots=true`
   - `mutating_actions=false`
6. Click **Preview evidence**. Lingjing attempts to capture current Unity logs, an Editor/Scene snapshot and a PNG camera frame. If no screenshot-capable camera exists, the preview explains how to make one available.
7. Copy the endpoint, normally `http://127.0.0.1:9030`.
8. From the Lingjing repository, run the conformance command displayed in the Unity window.

Example without a bearer token:

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

When a bearer token is configured, add `--token "..."` or set `LINGJING_GAME_ADAPTER_TOKEN`.

`--fetch-evidence` downloads only locators under the same configured adapter endpoint and recomputes SHA-256. Use `--require-screenshot` when your project/capture environment must prove that a camera frame is available.

## Evidence available in 0.2.0

### Unity Console log slice

The package records log messages emitted after the Editor package initializes. The evidence payload is bounded to the most recent entries and roughly 64 KiB. Stack traces are intentionally not exported by this provider. Common `token`, `secret`, `password`, `authorization` and API-key-shaped values are redacted before evidence is stored.

This is a diagnostic safety layer, not a substitute for avoiding secrets in application logs.

### Editor / Scene snapshot

The snapshot is a small JSON observation containing the active scene identity, scene path, root count, load state, Play/Edit state, pause state, product name and Unity version. Its SHA-256 is also used as the adapter before/after snapshot digest.

Because the bridge remains non-mutating, a normal dry-run should usually have equal before/after digests. A changed digest is an observation to investigate, not an automatic failure verdict.

### PNG camera frame

In Play Mode the provider prefers `Camera.main`; otherwise it uses the active Scene view camera. The provider performs a bounded read-only render with a maximum output of `1280×720` and returns PNG bytes.

The PNG is a **camera frame**, not a guarantee that every GameView overlay, native window or platform compositor element is present.

## Evidence retrieval boundary

Evidence bytes are held only in a short-lived Editor memory cache:

- maximum 12 evidence items;
- approximately 10-minute TTL;
- no package-side file persistence;
- retrieved through `GET /v1/adapter/evidence/<id>`;
- protected by the same optional bearer token as capabilities/execute;
- response includes `X-Lingjing-Sha256`;
- `Cache-Control: no-store`.

The HTTP bridge still binds only to `127.0.0.1`.

## What this package proves

A successful live dry-run with `--fetch-evidence` proves that the Unity Editor project can speak GameAdapter v1 and that the evidence bytes returned by the adapter are retrievable and match the SHA-256 accepted by the Lingjing gateway.

It does **not** prove that a game bug has been reproduced or that a fix works. The bridge result is still classified by Lingjing as:

```text
external-engine-observation-unverified
```

An independent Verifier must still inspect the observation before a Bug/Fix task can become verified.

## Current capability boundary

The package intentionally remains conservative:

- binds only to `127.0.0.1`;
- optional bearer-token protection;
- Editor-only;
- dry-run only;
- captures read-only logs, snapshot and camera-frame evidence;
- advertises `mutating_actions=false`;
- does not execute arbitrary game mutations;
- does not grant itself verifier status;
- does not expose the bridge to the LAN or Internet.

This slice improves **real project evidence acquisition**, but it is not yet a complete `Bug → automated reproduction actions → fix verification` Unity integration. Project-specific governed action handlers and a checked-in reproducible real-project/demo fixture remain follow-up work. Those handlers must continue to require Frozen Kernel tickets and independent verification; the Unity package must not become a side door around the authority model.

## Troubleshooting

If **Start local bridge** fails, check whether the selected port is already in use and choose another port. On managed machines, local HTTP listener policy may also restrict editor processes.

If **Preview evidence** reports no screenshot, open a Scene view or enter Play Mode with a tagged `Camera.main`.

If the Python conformance command returns `401`, ensure the same bearer token shown in the Unity window is passed through `--token` or `LINGJING_GAME_ADAPTER_TOKEN`.

If execution returns an evidence-capture timeout, ensure the Unity Editor is responsive and not blocked by a modal window or script compilation. Evidence capture is deliberately marshalled to the Editor main thread rather than calling Unity APIs from the HTTP worker thread.

If capabilities work but dry-run fails before capture, confirm the command includes a signing secret. The signing secret is used by the Lingjing Frozen Kernel gateway to issue and validate the short-lived execution ticket; it is not required by the Unity package itself.
