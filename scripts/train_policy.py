from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from worldforge.envs import BalanceLabEnv, list_scenarios
from worldforge.models import GameAction
from worldforge.runtime import (
    AdaptivePlanner, CounterfactualBrancher, EpisodicMemory, SkillBank, StateVerifier,
)
from worldforge.runtime.policy import (
    ACTION_INDEX, ACTION_ORDER, PolicyCard, WorldForgePolicy, state_features,
    train_supervised,
)
from worldforge.settings import settings


def build_dataset(seeds: int):
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    verifier = StateVerifier()
    brancher = CounterfactualBrancher(planner, verifier)
    features = []
    labels = []
    masks = []

    for scenario in list_scenarios():
        for sample in range(seeds):
            env = BalanceLabEnv()
            state = env.reset(scenario, 101 + sample * 37)
            for _ in range(min(scenario.goal.max_steps, 12)):
                if state.terminal:
                    break
                belief = planner.make_belief(state)
                legal = env.legal_actions(state)
                ranked = planner.rank(state, legal, scenario.goal)
                branches = brancher.evaluate(
                    env,
                    ranked.candidates,
                    scenario.goal,
                    width=min(4, len(ranked.candidates)),
                    horizon=3,
                    rollouts=2,
                )
                if not branches:
                    break
                best = branches[0].first_action
                features.append(state_features(state, belief, scenario.goal))
                labels.append(ACTION_INDEX[best.value])
                masks.append([
                    action.value in legal for action in ACTION_ORDER
                ])
                state, _, done, _ = env.step(
                    GameAction(kind=best, source="dataset-builder")
                )
                if done:
                    break

    return (
        np.asarray(features, dtype=np.float64),
        np.asarray(labels, dtype=np.int64),
        np.asarray(masks, dtype=bool),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Train the local WorldForge decision prior from verified trajectories."
    )
    parser.add_argument("--seeds", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=420)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(settings.data_dir) / "worldforge_policy.json",
    )
    args = parser.parse_args()

    X, y, masks = build_dataset(args.seeds)
    if not len(X):
        raise SystemExit("No training samples generated.")

    W1, b1, W2, b2, mean, scale = train_supervised(
        X, y, masks, epochs=args.epochs
    )
    policy = WorldForgePolicy(
        W1, b1, W2, b2,
        mean=mean,
        scale=scale,
        card=PolicyCard(
            training_states=len(X),
            trained_on="verified counterfactual trajectories",
        ),
    )
    policy.save(args.output)
    print(
        f"saved {policy.card.name} with {len(X)} states to {args.output}"
    )


if __name__ == "__main__":
    main()
