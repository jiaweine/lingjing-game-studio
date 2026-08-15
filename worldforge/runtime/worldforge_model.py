from __future__ import annotations

"""WorldForge-M1: in-house game decision prior.

This model is deliberately local and self-contained. It never calls a third-party API.
It is trained by distilling decisions produced from verified counterfactual rollouts inside
BalanceLab. The Harness remains responsible for planning, branching, tool execution,
verification, rollback and skill evolution; the model is only one decision signal.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import json
from typing import Iterable

import numpy as np

from worldforge.models import ActionKind, BeliefState, GoalState, WorldState

ACTION_ORDER = list(ActionKind)
ACTION_INDEX = {a.value: i for i, a in enumerate(ACTION_ORDER)}


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
class ModelCard:
    name: str = "WorldForge-M1"
    version: str = "2.0"
    owner: str = "WorldForge Research"
    model_type: str = "Counterfactual-distilled policy MLP"
    parameters: int = 0
    training_states: int = 0
    validation_top1: float = 0.0
    validation_top3: float = 0.0
    trained_on: str = "BalanceLab verified counterfactual trajectories"
    external_api: bool = False
    locale: str = "zh-CN"


class WorldForgeM1:
    def __init__(self, W1: np.ndarray | None = None, b1: np.ndarray | None = None,
                 W2: np.ndarray | None = None, b2: np.ndarray | None = None,
                 *, mean: np.ndarray | None = None, scale: np.ndarray | None = None,
                 card: ModelCard | None = None, hidden: int = 64) -> None:
        d = len(state_features(
            WorldState(),
            BeliefState(enemy_attack_low=10, enemy_attack_high=20),
            GoalState(primary="probe"),
        ))
        rng = np.random.default_rng(20260815)
        h = hidden if W1 is None else int(np.asarray(W1).shape[1])
        self.W1 = np.asarray(W1, dtype=np.float64) if W1 is not None else rng.normal(0, .08, size=(d, h))
        self.b1 = np.asarray(b1, dtype=np.float64) if b1 is not None else np.zeros(h)
        self.W2 = np.asarray(W2, dtype=np.float64) if W2 is not None else rng.normal(0, .08, size=(h, len(ACTION_ORDER)))
        self.b2 = np.asarray(b2, dtype=np.float64) if b2 is not None else np.zeros(len(ACTION_ORDER))
        self.mean = np.asarray(mean, dtype=np.float64) if mean is not None else np.zeros(d)
        self.scale = np.asarray(scale, dtype=np.float64) if scale is not None else np.ones(d)
        self.scale = np.where(self.scale < 1e-8, 1.0, self.scale)
        self.card = card or ModelCard()
        self.card.parameters = int(self.W1.size + self.b1.size + self.W2.size + self.b2.size)

    def _forward(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mean) / self.scale
        h = np.tanh(z @ self.W1 + self.b1)
        return h @ self.W2 + self.b2

    def raw_logits(self, state: WorldState, belief: BeliefState, goal: GoalState) -> np.ndarray:
        return self._forward(state_features(state, belief, goal))

    def rank(self, state: WorldState, belief: BeliefState, goal: GoalState, legal: Iterable[str]) -> dict[str, float]:
        logits = self.raw_logits(state, belief, goal)
        allowed = [ActionKind(a) for a in legal]
        if not allowed:
            return {}
        vals = np.asarray([logits[ACTION_INDEX[a.value]] for a in allowed])
        mu, sigma = float(vals.mean()), float(vals.std())
        sigma = sigma if sigma > 1e-6 else 1.0
        return {a.value: round(float((logits[ACTION_INDEX[a.value]] - mu) / sigma), 4) for a in allowed}

    def confidence(self, normalized_scores: dict[str, float]) -> float:
        vals = sorted(normalized_scores.values(), reverse=True)
        if not vals:
            return 0.0
        margin = vals[0] - (vals[1] if len(vals) > 1 else 0.0)
        return float(max(.50, min(.97, .60 + .10 * margin)))

    def explain(self, state: WorldState, belief: BeliefState, goal: GoalState, action: ActionKind) -> list[dict]:
        hp = state.player_hp / max(1, state.player_max_hp)
        enemy = state.enemy_hp / max(1, state.enemy_max_hp)
        signals = [
            ("生存余量", (hp - goal.min_health_ratio) * 2.0),
            ("击杀窗口", (1 - enemy) * (1.35 if action in (ActionKind.ATTACK, ActionKind.HEAVY_ATTACK, ActionKind.CAST) else .4)),
            ("信息不确定性", belief.uncertainty * (1.2 if action == ActionKind.SCOUT else -.22)),
            ("资源可用性", state.energy / max(1, state.max_energy) + min(1.0, state.gold / 45)),
            ("环境风险", -state.threat * (1.1 if action in (ActionKind.HEAVY_ATTACK, ActionKind.CAST) else .35)),
        ]
        return [
            {"factor": k, "contribution": round(float(v), 4)}
            for k, v in sorted(signals, key=lambda x: abs(x[1]), reverse=True)
        ]

    def card_dict(self) -> dict:
        return asdict(self.card)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "format": "worldforge-mlp-v2",
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
    def load(cls, path: str | Path) -> "WorldForgeM1":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if not all(k in d for k in ("W1", "b1", "W2", "b2")):
            raise ValueError("legacy/incompatible WorldForge-M1 model file")
        return cls(
            np.asarray(d["W1"]), np.asarray(d["b1"]), np.asarray(d["W2"]), np.asarray(d["b2"]),
            mean=np.asarray(d["mean"]), scale=np.asarray(d["scale"]), card=ModelCard(**d["card"]),
        )

    @classmethod
    def load_or_bootstrap(cls, path: str | Path) -> "WorldForgeM1":
        path = Path(path)
        if path.exists():
            try:
                return cls.load(path)
            except (ValueError, KeyError, TypeError):
                return cls(card=ModelCard(version="bootstrap", trained_on="deterministic bootstrap weights"))
        return cls(card=ModelCard(version="bootstrap", trained_on="deterministic bootstrap weights"))


def train_mlp(X: np.ndarray, y: np.ndarray, legal_masks: np.ndarray, *, hidden: int = 64,
              epochs: int = 500, lr: float = .028, l2: float = 2e-4, seed: int = 42):
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
            h = np.tanh(x @ W1 + b1)
            logits = h @ W2 + b2
            logits = np.where(mask, logits, -1e9)
            logits -= logits.max(1, keepdims=True)
            p = np.exp(logits); p /= p.sum(1, keepdims=True)
            g = p
            g[np.arange(len(ii)), yy] -= 1
            g /= len(ii)
            g = np.where(mask, g, 0.0)
            dW2 = h.T @ g + l2 * W2; db2 = g.sum(0)
            dh = (g @ W2.T) * (1 - h * h)
            dW1 = x.T @ dh + l2 * W1; db1 = dh.sum(0)
            W2 -= eta * dW2; b2 -= eta * db2
            W1 -= eta * dW1; b1 -= eta * db1
    return W1, b1, W2, b2, mean, scale
