# Real-Game Execution Adapter

Lingjing's internal WorldForge scenarios are **synthetic evidence**. They are useful for hypothesis testing, harness evaluation, and counterfactual search, but they are not proof that a user's issue was reproduced inside the user's game build.

The `GameExecutionAdapter` contract is the boundary for real-game execution. Evidence emitted through this contract is allowed to use the `reproduced` provenance because it comes from an explicitly loaded external game build.

## Evidence provenance

| Provenance | Meaning | May claim real reproduction? |
| --- | --- | --- |
| `observed` | User-provided video, image, log, telemetry, or configuration | No |
| `synthetic` | Internal WorldForge scenario or other simulated experiment | No |
| `reproduced` | Observation captured by a `GameExecutionAdapter` from a loaded game build | Yes, within the adapter's recorded build/seed/input scope |
| `inferred` | Model or rule-based conclusion derived from evidence | No |

## Contract

`worldforge.integrations.game_execution.GameExecutionAdapter` defines the minimum engine-neutral surface:

- `load_build(build)` — load or launch an identified game build.
- `reset(seed=...)` — reset to a deterministic baseline when supported.
- `perform_action(action)` — inject one player/test action and return a real observation.
- `observe()` — capture state, logs, telemetry, frame/video evidence.
- `checkpoint()` / `restore()` — support reproducible search and rollback.
- `verify_condition(assertion, expected=...)` — verify one concrete condition against the running build.
- `close()` — release runner processes and resources.

Every `ExecutionObservation` produced by this boundary is explicitly marked `reproduced`. Synthetic experiments should never be wrapped in this adapter merely to obtain a stronger provenance label.

## Build identity

A real reproduction should retain at least:

- game engine;
- build identifier;
- product version when available;
- source revision/commit when available;
- deterministic seed when supported;
- action/input sequence;
- checkpoint/save-state identity;
- relevant logs, metrics, frames, and videos.

This information is required for replayability and for comparing a failing build with a candidate fix.

## Recommended first implementation: Unity

The first production adapter should stay narrow and deep rather than attempting multiple engines at once. A Unity runner should eventually provide:

1. build launch and health detection;
2. deterministic seed/config injection;
3. input/action bridge;
4. save/checkpoint restore;
5. structured state and telemetry export;
6. player/editor log collection;
7. screenshot/video capture;
8. assertion RPC for verifier checks;
9. process timeout/crash handling;
10. build/source revision metadata.

Only after this path can reliably reproduce real issues should Lingjing promote a finding from `synthetic` hypothesis support to `reproduced` evidence.

## Non-goals of the current contract

This change does **not** claim that Unity, Unreal, Godot, mobile emulators, consoles, or proprietary engines are already integrated. It establishes the stable boundary and evidence semantics those integrations must satisfy.
