from __future__ import annotations

"""In-house WorldForge decision policy and group-relative optimizer.

The policy is a local decision prior. It never owns tool permission, execution, rollback,
verification, memory, or task completion. Those remain Runtime responsibilities.
"""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import copy
import json
from typing import Iterable

import numpy as np

from worldforge.models import ActionKind, BeliefState, GoalState, WorldState

ACTION_ORDER = list(ActionKind)
ACTION_INDEX = {action.value: index for index, action in enumerate(ACTION_ORDER)}


def state_features(state: WorldState, belief: BeliefState, goal: GoalState) -> np.ndarray:
    hp = state.player_hp / max(1.0, state.player_max_hp)
    enemy_hp = state.enemy_hp / max(1.0, state.enemy_max_hp)
    remain = max(0.0, goal.max_steps - state.tick) / max(1.0, goal.max_steps)
    score_gap = (goal.target_score - state.score) / max(30.0, abs(goal.target_score))
    return np.asarray([
        hp,
        enemy_hp,
        1.0 - enemy_hp,
        remain,
        state.energy / max(1.0, state.max_energy),
        min(2.0, state.gold / 50.0),
        state.attack / 32.0,
        state.armor / 14.0,
        state.enemy_attack / 35.0,
        state.enemy_variance / 15.0,
        state.threat,
        belief.uncertainty,
        float(np.clip(score_gap, -2.5, 2.5)),
        max(0.0, goal.min_health_ratio - hp),
        state.combo / 5.0,
        state.healing_potions / 3.0,
        1.0 if state.discovered_enemy_attack is not None else 0.0,
        1.0 if "economy" in state.tags else 0.0,
        1.0 if "exploit-test" in state.tags else 0.0,
        1.0 if "boss" in state.tags else 0.0,
        1.0 if "glass-cannon" in state.tags else 0.0,
    ], dtype=np.float64)


@dataclass
class PolicyCard:
    name: str = "WorldForge Policy"
    generation: int = 1
    owner: str = "WorldForge"
    model_type: str = "counterfactual-distilled policy MLP"
    optimizer: str = "group-relative clipped policy optimization"
    parameters: int = 0
    training_states: int = 0
    validation_top1: float = 0.0
    validation_top3: float = 0.0
    trained_on: str = "verified game trajectories"
    external_api: bool = False


@dataclass
class PolicyGroup:
    state: WorldState
    belief: BeliefState
    goal: GoalState
    rewards: dict[str, float]


class WorldForgePolicy:
    def __init__(
        self,
        W1: np.ndarray | None = None,
        b1: np.ndarray | None = None,
        W2: np.ndarray | None = None,
        b2: np.ndarray | None = None,
        *,
        mean: np.ndarray | None = None,
        scale: np.ndarray | None = None,
        card: PolicyCard | None = None,
        hidden: int = 64,
    ) -> None:
        d = len(state_features(
            WorldState(),
            BeliefState(enemy_attack_low=10, enemy_attack_high=20),
            GoalState(primary="probe"),
        ))
        rng = np.random.default_rng(20260817)
        h = hidden if W1 is None else int(np.asarray(W1).shape[1])
        self.W1 = np.asarray(W1, dtype=np.float64) if W1 is not None else rng.normal(0, .08, size=(d, h))
        self.b1 = np.asarray(b1, dtype=np.float64) if b1 is not None else np.zeros(h)
        self.W2 = np.asarray(W2, dtype=np.float64) if W2 is not None else rng.normal(0, .08, size=(h, len(ACTION_ORDER)))
        self.b2 = np.asarray(b2, dtype=np.float64) if b2 is not None else np.zeros(len(ACTION_ORDER))
        self.mean = np.asarray(mean, dtype=np.float64) if mean is not None else np.zeros(d)
        self.scale = np.asarray(scale, dtype=np.float64) if scale is not None else np.ones(d)
        self.scale = np.where(self.scale < 1e-8, 1.0, self.scale)
        self.card = card or PolicyCard()
        self.card.parameters = int(self.W1.size + self.b1.size + self.W2.size + self.b2.size)

    def clone(self) -> "WorldForgePolicy":
        return WorldForgePolicy(
            self.W1.copy(), self.b1.copy(), self.W2.copy(), self.b2.copy(),
            mean=self.mean.copy(), scale=self.scale.copy(), card=copy.deepcopy(self.card),
        )

    def _normalized(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.scale

    def _forward(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        z = self._normalized(x)
        hidden = np.tanh(z @ self.W1 + self.b1)
        return z, hidden, hidden @ self.W2 + self.b2

    def raw_logits(self, state: WorldState, belief: BeliefState, goal: GoalState) -> np.ndarray:
        return self._forward(state_features(state, belief, goal))[2]

    def probabilities(
        self,
        state: WorldState,
        belief: BeliefState,
        goal: GoalState,
        legal: Iterable[str],
    ) -> dict[str, float]:
        allowed = [ActionKind(action) for action in legal]
        if not allowed:
            return {}
        logits = self.raw_logits(state, belief, goal)
        values = np.asarray([logits[ACTION_INDEX[action.value]] for action in allowed], dtype=np.float64)
        values -= values.max()
        p = np.exp(values)
        p /= max(1e-12, p.sum())
        return {action.value: float(p[index]) for index, action in enumerate(allowed)}

    def rank(
        self,
        state: WorldState,
        belief: BeliefState,
        goal: GoalState,
        legal: Iterable[str],
    ) -> dict[str, float]:
        allowed = [ActionKind(action) for action in legal]
        if not allowed:
            return {}
        logits = self.raw_logits(state, belief, goal)
        values = np.asarray([logits[ACTION_INDEX[action.value]] for action in allowed])
        mu, sigma = float(values.mean()), float(values.std())
        sigma = sigma if sigma > 1e-6 else 1.0
        return {
            action.value: round(float((logits[ACTION_INDEX[action.value]] - mu) / sigma), 4)
            for action in allowed
        }

    @staticmethod
    def confidence(normalized_scores: dict[str, float]) -> float:
        values = sorted(normalized_scores.values(), reverse=True)
        if not values:
            return 0.0
        margin = values[0] - (values[1] if len(values) > 1 else 0.0)
        return float(max(.50, min(.97, .60 + .10 * margin)))

    @staticmethod
    def explain(
        state: WorldState,
        belief: BeliefState,
        goal: GoalState,
        action: ActionKind,
    ) -> list[dict]:
        hp = state.player_hp / max(1, state.player_max_hp)
        enemy = state.enemy_hp / max(1, state.enemy_max_hp)
        signals = [
            ("survival_margin", (hp - goal.min_health_ratio) * 2.0),
            ("finish_window", (1 - enemy) * (1.35 if action in (ActionKind.ATTACK, ActionKind.HEAVY_ATTACK, ActionKind.CAST) else .4)),
            ("uncertainty", belief.uncertainty * (1.2 if action == ActionKind.SCOUT else -.22)),
            ("resources", state.energy / max(1, state.max_energy) + min(1.0, state.gold / 45)),
            ("environment_risk", -state.threat * (1.1 if action in (ActionKind.HEAVY_ATTACK, ActionKind.CAST) else .35)),
        ]
        return [
            {"factor": key, "contribution": round(float(value), 4)}
            for key, value in sorted(signals, key=lambda item: abs(item[1]), reverse=True)
        ]

    def card_dict(self) -> dict:
        return asdict(self.card)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "format": "worldforge-policy",
            "feature_count": int(self.W1.shape[0]),
            "W1": self.W1.tolist(),
            "b1": self.b1.tolist(),
            "W2": self.W2.tolist(),
            "b2": self.b2.tolist(),
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "card": asdict(self.card),
        }, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "WorldForgePolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "worldforge-policy":
            raise ValueError("incompatible WorldForge policy file")
        if not all(key in data for key in ("W1", "b1", "W2", "b2", "mean", "scale", "card")):
            raise ValueError("incomplete WorldForge policy file")
        return cls(
            np.asarray(data["W1"]), np.asarray(data["b1"]),
            np.asarray(data["W2"]), np.asarray(data["b2"]),
            mean=np.asarray(data["mean"]), scale=np.asarray(data["scale"]),
            card=PolicyCard(**data["card"]),
        )

    @classmethod
    def load_or_bootstrap(cls, path: str | Path) -> "WorldForgePolicy":
        path = Path(path)
        if path.exists():
            try:
                return cls.load(path)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass
        return cls(card=PolicyCard(trained_on="deterministic bootstrap prior"))


class GroupRelativePolicyOptimizer:
    """Small local GRPO-style optimizer for verified counterfactual groups.

    Each decision state forms a group of alternative actions scored by the Runtime verifier.
    Rewards are centered and scaled within that group. Updates use a clipped probability ratio
    against a frozen policy and stop when the empirical KL trust region is exceeded.
    """

    def __init__(
        self,
        *,
        learning_rate: float = .0035,
        clip_ratio: float = .18,
        kl_limit: float = .035,
        epochs: int = 6,
        l2: float = 1e-5,
    ) -> None:
        self.learning_rate = learning_rate
        self.clip_ratio = clip_ratio
        self.kl_limit = kl_limit
        self.epochs = epochs
        self.l2 = l2

    def optimize(
        self,
        policy: WorldForgePolicy,
        groups: list[PolicyGroup],
    ) -> tuple[WorldForgePolicy, dict]:
        usable = [group for group in groups if len(group.rewards) >= 2]
        if not usable:
            return policy.clone(), {
                "groups": 0, "updates": 0, "epochs": 0, "mean_kl": 0.0,
                "mean_advantage": 0.0, "stopped_by_kl": False,
            }

        candidate = policy.clone()
        old_probabilities = [
            policy.probabilities(group.state, group.belief, group.goal, group.rewards.keys())
            for group in usable
        ]
        updates = 0
        stopped = False
        advantage_magnitudes: list[float] = []
        completed_epochs = 0

        for epoch in range(self.epochs):
            dW1 = np.zeros_like(candidate.W1)
            db1 = np.zeros_like(candidate.b1)
            dW2 = np.zeros_like(candidate.W2)
            db2 = np.zeros_like(candidate.b2)
            active_terms = 0

            for group, old_probs in zip(usable, old_probabilities):
                actions = list(group.rewards)
                rewards = np.asarray([group.rewards[action] for action in actions], dtype=np.float64)
                advantages = rewards - rewards.mean()
                scale = rewards.std()
                if scale > 1e-8:
                    advantages /= scale
                advantages = np.clip(advantages, -3.0, 3.0)

                x = state_features(group.state, group.belief, group.goal)
                z, hidden, logits = candidate._forward(x)
                indices = [ACTION_INDEX[action] for action in actions]
                selected_logits = logits[indices]
                selected_logits = selected_logits - selected_logits.max()
                probs = np.exp(selected_logits)
                probs /= max(1e-12, probs.sum())

                grad_logits = np.zeros_like(candidate.b2)
                for local_index, (action, advantage) in enumerate(zip(actions, advantages)):
                    old_p = max(1e-9, old_probs[action])
                    ratio = float(probs[local_index] / old_p)
                    clipped = (
                        (advantage >= 0 and ratio > 1 + self.clip_ratio)
                        or (advantage < 0 and ratio < 1 - self.clip_ratio)
                    )
                    advantage_magnitudes.append(abs(float(advantage)))
                    if clipped:
                        continue
                    coeff = float(advantage * ratio)
                    local_grad = -probs.copy()
                    local_grad[local_index] += 1.0
                    for prob_index, action_index in enumerate(indices):
                        grad_logits[action_index] += coeff * local_grad[prob_index]
                    active_terms += 1

                if not np.any(grad_logits):
                    continue
                dW2 += np.outer(hidden, grad_logits)
                db2 += grad_logits
                hidden_grad = (candidate.W2 @ grad_logits) * (1.0 - hidden * hidden)
                dW1 += np.outer(z, hidden_grad)
                db1 += hidden_grad

            if active_terms == 0:
                break
            factor = self.learning_rate / active_terms
            candidate.W2 += factor * (dW2 - self.l2 * candidate.W2)
            candidate.b2 += factor * db2
            candidate.W1 += factor * (dW1 - self.l2 * candidate.W1)
            candidate.b1 += factor * db1
            updates += active_terms
            completed_epochs = epoch + 1

            mean_kl = self._mean_kl(policy, candidate, usable)
            if not np.isfinite(mean_kl) or mean_kl > self.kl_limit:
                stopped = True
                # Keep the previous safe policy if a step escapes the trust region.
                candidate = policy.clone()
                completed_epochs = epoch
                break

        candidate.card = replace(
            candidate.card,
            generation=policy.card.generation + 1,
            training_states=policy.card.training_states + len(usable),
            trained_on="verified counterfactual groups",
        )
        candidate.card.parameters = int(candidate.W1.size + candidate.b1.size + candidate.W2.size + candidate.b2.size)
        mean_kl = self._mean_kl(policy, candidate, usable)
        return candidate, {
            "groups": len(usable),
            "updates": updates,
            "epochs": completed_epochs,
            "mean_kl": round(float(mean_kl), 6),
            "mean_advantage": round(float(np.mean(advantage_magnitudes)) if advantage_magnitudes else 0.0, 6),
            "stopped_by_kl": stopped,
        }

    @staticmethod
    def _mean_kl(
        old: WorldForgePolicy,
        new: WorldForgePolicy,
        groups: list[PolicyGroup],
    ) -> float:
        values = []
        for group in groups:
            actions = list(group.rewards)
            p = old.probabilities(group.state, group.belief, group.goal, actions)
            q = new.probabilities(group.state, group.belief, group.goal, actions)
            values.append(sum(
                p[action] * np.log(max(1e-12, p[action]) / max(1e-12, q[action]))
                for action in actions
            ))
        return float(np.mean(values)) if values else 0.0


def train_supervised(
    X: np.ndarray,
    y: np.ndarray,
    legal_masks: np.ndarray,
    *,
    hidden: int = 64,
    epochs: int = 500,
    lr: float = .028,
    l2: float = 2e-4,
    seed: int = 42,
):
    """Bootstrap the local prior from verified labels before online group-relative updates."""
    rng = np.random.default_rng(seed)
    mean = X.mean(0)
    scale = X.std(0)
    scale[scale < 1e-8] = 1.0
    Z = (X - mean) / scale
    n, d = Z.shape
    k = len(ACTION_ORDER)
    W1 = rng.normal(0, .10, (d, hidden)); b1 = np.zeros(hidden)
    W2 = rng.normal(0, .10, (hidden, k)); b2 = np.zeros(k)
    batch = min(160, n)
    for epoch in range(epochs):
        order = rng.permutation(n)
        eta = lr * (0.25 + 0.75 * (1 - epoch / max(1, epochs)))
        for start in range(0, n, batch):
            ii = order[start:start + batch]
            x = Z[ii]; yy = y[ii]; mask = legal_masks[ii]
            hidden_values = np.tanh(x @ W1 + b1)
            logits = hidden_values @ W2 + b2
            logits = np.where(mask, logits, -1e9)
            logits -= logits.max(1, keepdims=True)
            probability = np.exp(logits); probability /= probability.sum(1, keepdims=True)
            gradient = probability
            gradient[np.arange(len(ii)), yy] -= 1
            gradient /= len(ii)
            gradient = np.where(mask, gradient, 0.0)
            dW2 = hidden_values.T @ gradient + l2 * W2; db2 = gradient.sum(0)
            dh = (gradient @ W2.T) * (1 - hidden_values * hidden_values)
            dW1 = x.T @ dh + l2 * W1; db1 = dh.sum(0)
            W2 -= eta * dW2; b2 -= eta * db2
            W1 -= eta * dW1; b1 -= eta * db1
    return W1, b1, W2, b2, mean, scale
