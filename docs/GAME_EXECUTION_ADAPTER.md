# Real-Game Execution Adapter

Lingjing's internal WorldForge scenarios are **synthetic evidence**. They are useful for hypothesis testing, harness evaluation, and counterfactual search, but they are not proof that a user's issue was reproduced inside the user's game build.

The `GameExecutionAdapter` contract is the boundary for real-game execution. Evidence emitted through this contract is allowed to use the `reproduced` provenance because it comes from an explicitly loaded external game build.

## Evidence provenance

| Provenance | Meaning | May claim real reproduction? |
| --- | --- | --- |
| `observed` | User-provided video, image, log, telemetry, or configuration | No |
| `synthetic` | Internal WorldForge scenario or other simulated experiment | No |
| `reproduced` | Observation captured by a `GameExecutionAdapter` from a loaded game build | Only when explicit reproduction assertions pass |
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

## Reproduction orchestration

`GameReproductionService` turns the adapter primitives into one bounded, auditable reproduction attempt:

1. validate that the adapter engine matches the requested build;
2. load the exact `GameBuildRef`;
3. reset with the requested deterministic seed when supported;
4. create a baseline checkpoint;
5. execute the ordered action trace;
6. capture the final observation;
7. evaluate explicit assertions;
8. close runner resources even when execution fails.

The distinction between **real-runtime evidence** and a **verified issue reproduction** is intentional:

- `executed_not_verified` — the real build was executed, but no explicit assertion was supplied;
- `not_reproduced` — one or more explicit assertions failed;
- `verified` — at least one assertion exists and all assertions passed.

Therefore a real game frame/log can have `reproduced` provenance without the product claiming that the reported bug itself was reproduced. The stronger product claim requires `claim_status=verified`.

## Build identity

A real reproduction should retain at least:

- game engine;
- build identifier;
- product version when available;
- source revision/commit when available;
- deterministic seed when supported;
- action/input sequence;
- checkpoint/save-state identity;
- relevant logs, metrics, frames, and videos;
- assertion count and assertion outcomes.

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

Only after this path can reliably reproduce real issues should Lingjing promote a finding from `synthetic` hypothesis support to a verified real-game reproduction claim.

## Non-goals of the current contract

This change does **not** claim that Unity, Unreal, Godot, mobile emulators, consoles, or proprietary engines are already integrated. It establishes the stable boundary, orchestration semantics, and evidence rules those integrations must satisfy.
