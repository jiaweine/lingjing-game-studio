# Boss Shield Bug Repro sample

This sample is a deterministic **real Unity project fixture** for exercising Lingjing's engine-observation path. It is intentionally small: one Boss cube, one deterministic attack, one structured probe and one independent Lingjing-side contract.

## Setup

1. Install/import the **Boss Shield Bug Repro** sample from the Lingjing Game Adapter package.
2. Run **Lingjing → Demo → Create Boss Shield Repro Scene**.
3. Open the generated `Assets/LingjingBossShieldDemo/BossShieldDemo.unity`.
4. Select **BossShieldFixture**.

## Reproduce the Bug

Leave **Fixed Behavior** disabled and press Play.

After ~0.35 seconds:

- shield is active;
- the deterministic attack still subtracts 120 HP;
- the cube turns red;
- Unity logs `BUG reproduced`;
- probe `demo.boss_shield.damage_gate` reports:
  - `observed_state=damage_applied_while_shielded`
  - `observed_value=120`.

Use **Lingjing → Game Adapter Setup → Preview evidence** or the Lingjing workspace's **导入引擎证据** action while still in Play Mode.

## Observe the fixed behavior

Stop Play Mode, enable **Fixed Behavior** on **BossShieldFixture**, then press Play again.

The same deterministic attack now:

- keeps HP unchanged;
- turns the cube green;
- logs `FIX behavior observed`;
- reports `observed_state=damage_blocked_while_shielded`;
- reports `observed_value=0`.

Import engine evidence again and compare the two runs in the same Lingjing task.

## Authority boundary

The sample only emits observations. It does not declare its own pass/fail rule.

Lingjing owns the independent contract for `demo.boss_shield.damage_gate`:

- `damage_applied_while_shielded` → observed Bug reproduction condition;
- `damage_blocked_while_shielded` → observed fixed condition.

Even a contract match remains an evaluation of **external engine observation**. It does not grant the Unity adapter canonical or verifier authority and does not automatically mark the whole task as verified.
