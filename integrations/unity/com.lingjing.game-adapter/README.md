# Lingjing Game Adapter for Unity

This package is the first productized Unity activation path for Lingjing GameAdapter v1. It runs an **Editor-only loopback HTTP bridge** so a Unity project can answer the existing GameAdapter capabilities and non-mutating dry-run contract without asking the user to implement the protocol from scratch.

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
5. Click **Test connection**. The window should show a capabilities payload with:
   - `engine=unity`
   - `protocol_version=1.0`
   - `supports_dry_run=true`
   - `mutating_actions=false`
6. Copy the endpoint, normally `http://127.0.0.1:9030`.
7. From the Lingjing repository, run the conformance command displayed in the Unity window.

Example without a bearer token:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030 \
  --execute-dry-run \
  --signing-secret "$LINGJING_GAME_ADAPTER_SIGNING_SECRET" \
  --build-ref build-1.4.7 \
  --branch-ref release \
  --require-conformance
```

When a bearer token is configured, add `--token "..."` or set `LINGJING_GAME_ADAPTER_TOKEN`.

## What this package proves

A successful dry-run proves that the Unity Editor project can speak the GameAdapter v1 transport contract and return an external-engine observation through the Frozen Kernel gateway.

It does **not** prove that a game bug has been reproduced or that a fix works. The bridge result is still classified by Lingjing as:

```text
external-engine-observation-unverified
```

An independent Verifier must still inspect evidence before a Bug/Fix task can become verified.

## Current capability boundary

This activation package intentionally starts conservative:

- binds only to `127.0.0.1`;
- optional bearer-token protection;
- Editor-only;
- supports dry-run execution;
- returns a stable project/editor snapshot digest;
- returns a project-scoped log-style evidence locator;
- advertises `mutating_actions=false`;
- does not execute arbitrary game mutations;
- does not grant itself verifier status;
- does not expose the bridge to the LAN or Internet.

The next phase is to add project-specific evidence providers (screenshots, runtime logs, snapshots) and explicitly governed action handlers. Those handlers must continue to be invoked through Frozen Kernel tickets and independent verification; this package must not become a side door around the existing GameAdapter authority model.

## Troubleshooting

If **Start local bridge** fails, check whether the selected port is already in use and choose another port. On managed machines, local HTTP listener policy may also restrict editor processes.

If the Python conformance command returns `401`, ensure the same bearer token shown in the Unity window is passed through `--token` or `LINGJING_GAME_ADAPTER_TOKEN`.

If capabilities work but dry-run fails, confirm the command includes a signing secret. The signing secret is used by the Lingjing Frozen Kernel gateway to issue and validate the short-lived execution ticket; it is not required by this Unity package itself.
