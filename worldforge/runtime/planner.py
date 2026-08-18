from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
from statistics import pstdev

from worldforge.models import ActionKind, AgentVote, BeliefState

from .harness_genome import HarnessGenomeStore, state_features


@dataclass
class PlannerOutput:
    candidates: list
    votes: list
    aggregate: dict


class AdaptivePlanner:
    """Frozen planner interpreter for the active HarnessGenome."""

    def __init__(self, skills, memory, policy_model=None):
        self.skills = skills
        self.memory = memory
        self.policy_model = policy_model

    def make_belief(self, state):
        gene = HarnessGenomeStore.current().belief
        if state.discovered_enemy_attack is not None:
            low = high = state.discovered_enemy_attack
            uncertainty = gene.observed_uncertainty
            behavior = "observed"
        else:
            low = max(1, state.enemy_attack - state.enemy_variance)
            high = state.enemy_attack + state.enemy_variance
            uncertainty = min(
                gene.uncertainty_cap,
                gene.latent_base + state.enemy_variance / max(1e-6, gene.variance_scale),
            )
            behavior = "latent"
        return BeliefState(
            enemy_attack_low=low,
            enemy_attack_high=high,
            enemy_behavior=behavior,
            uncertainty=uncertainty,
        )

    @staticmethod
    def _epistemic_adjustment(action, scores, state, belief):
        """One data-driven equation for every action; no action policy lives in Python."""
        gene = HarnessGenomeStore.current().planner
        disagreement = pstdev(scores) if len(scores) > 1 else 0.0
        tension = min(gene.disagreement_cap, disagreement) * belief.uncertainty
        name = action.value
        base = gene.epistemic_base.get(name, gene.epistemic_base.get("*", 0.0))
        tension_gain = gene.epistemic_tension.get(
            name, gene.epistemic_tension.get("*", 0.0)
        )
        threat_gain = gene.epistemic_threat_tension.get(
            name, gene.epistemic_threat_tension.get("*", 0.0)
        )
        return base + tension * tension_gain + tension * state.threat * threat_gain

    def rank(
        self,
        state,
        legal,
        goal,
        *,
        extra_bias: dict[str, float] | None = None,
    ):
        kinds = [ActionKind(value) for value in legal]
        if not kinds:
            return PlannerOutput([], [], {})

        genome = HarnessGenomeStore.current()
        planner_gene = genome.planner
        belief = self.make_belief(state)
        features = state_features(state, belief, goal)
        votes: list[AgentVote] = []
        aggregate = {action.value: 0.0 for action in kinds}
        score_rows = {action.value: [] for action in kinds}
        signature = self.memory.signature(state)
        core_specialists = [
            gene
            for gene in genome.specialists
            if gene.enabled and gene.mode == "core"
        ]

        def evaluate(gene, action):
            score = gene.score(action.value, features)
            return AgentVote(
                agent=gene.role,
                action=action,
                score=round(score, 4),
                reason=(
                    f"genome={genome.genome_id}; gene={gene.gene_id}; "
                    f"activation={gene.activation(features):.3f}"
                ),
            )

        if core_specialists:
            with ThreadPoolExecutor(max_workers=len(core_specialists)) as executor:
                futures = [
                    executor.submit(evaluate, gene, action)
                    for gene in core_specialists
                    for action in kinds
                ]
                for future in futures:
                    vote = future.result()
                    votes.append(vote)
                    aggregate[vote.action.value] += vote.score
                    score_rows[vote.action.value].append(vote.score)

        policy_scores = (
            self.policy_model.rank(
                state,
                belief,
                goal,
                [action.value for action in kinds],
            )
            if self.policy_model
            else {}
        )
        extra_bias = extra_bias or {}
        for action in kinds:
            memory_prior = self.memory.prior(signature, action.value)
            aggregate[action.value] += (
                self.skills.bias(
                    state,
                    action.value,
                    belief.uncertainty,
                    goal=goal,
                )
                * planner_gene.skill_weight
                + math.tanh(memory_prior / max(1e-6, planner_gene.memory_scale))
                * planner_gene.memory_weight
                + policy_scores.get(action.value, 0.0) * planner_gene.policy_weight
                + extra_bias.get(action.value, 0.0) * planner_gene.specialist_weight
                + self._epistemic_adjustment(
                    action,
                    score_rows[action.value],
                    state,
                    belief,
                )
            )
            if state.last_action == action.value:
                aggregate[action.value] -= planner_gene.repeat_penalties.get(
                    action.value, 0.0
                )

        ordered = sorted(
            kinds,
            key=lambda action: aggregate[action.value],
            reverse=True,
        )
        return PlannerOutput(
            ordered,
            votes,
            {key: round(value, 4) for key, value in aggregate.items()},
        )
