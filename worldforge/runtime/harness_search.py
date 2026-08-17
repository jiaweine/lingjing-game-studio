from __future__ import annotations

import math
import random
import uuid

from .harness_evolution import (
    EvolutionEvidence,
    HarnessEvolutionEngine as BaseHarnessEvolutionEngine,
    HarnessMutator,
)
from .harness_genome import HarnessGenome


class GameHarnessMutator(HarnessMutator):
    """Vertical adaptation of the generic program-search mutator.

    In addition to topology/gate/recombination/meta mutation, game R&D harnesses evolve:
    representation scales, continual-memory retrieval, and reusable skill behavior.
    No action-specific failure rule is encoded in the mutation operator; evidence features
    determine which part of the genome receives edit pressure.
    """

    def propose(
        self,
        parent: HarnessGenome,
        evidence: EvolutionEvidence,
        *,
        peers: list[HarnessGenome] | None = None,
        direction: float | None = None,
    ) -> tuple[HarnessGenome, str]:
        peers = peers or []
        operator = self._sample_operator(parent, allow_recombine=bool(peers))
        child = parent.model_copy(deep=True)
        child.genome_id = f"hg-{uuid.uuid4().hex[:10]}"
        child.generation = parent.generation + 1
        child.parent_ids = [parent.genome_id]
        child.origin = operator
        sign = direction if direction is not None else self.rng.choice((-1.0, 1.0))

        if operator == "parameter_jitter":
            self._parameter_jitter(child, sign)
        elif operator == "gate_mutation":
            self._gate_mutation(child, evidence, sign)
        elif operator == "skill_mutation":
            self._skill_mutation(child, evidence, sign)
        elif operator == "memory_mutation":
            self._memory_mutation(child, evidence, sign)
        elif operator == "specialist_split":
            self._split_specialist(child, evidence, sign)
        elif operator == "specialist_prune":
            self._prune_specialist(child, evidence)
        elif operator == "recombine" and peers:
            child = self._recombine(parent, self.rng.choice(peers))
        elif operator == "meta_mutation":
            self._meta_mutation(child, sign)
        else:
            self._parameter_jitter(child, sign)
            operator = "parameter_jitter"
            child.origin = operator
        return child, operator

    def _sample_operator(self, genome: HarnessGenome, *, allow_recombine: bool) -> str:
        """Temperature-softmax exploitation with an independent exploration mixture."""
        items = [
            (name, value)
            for name, value in genome.mutation_policy.operator_logits.items()
            if allow_recombine or name != "recombine"
        ]
        names = [name for name, _ in items]
        exploration = max(0.0, min(1.0, genome.mutation_policy.exploration))
        if self.rng.random() < exploration:
            return self.rng.choice(names)
        temperature = max(.02, genome.mutation_policy.temperature)
        maximum = max(value for _, value in items)
        weights = [math.exp((value - maximum) / temperature) for _, value in items]
        return self.rng.choices(names, weights=weights, k=1)[0]

    def _parameter_jitter(self, genome: HarnessGenome, sign: float) -> None:
        groups = [
            genome.features,
            genome.belief,
            genome.memory,
            genome.planner,
            genome.search,
            genome.utility,
        ]
        group = self.rng.choice(groups)
        fields = []
        for name in type(group).model_fields:
            value = getattr(group, name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                fields.append(name)
        if not fields:
            if group is genome.features and genome.features.scales:
                name = self.rng.choice(list(genome.features.scales))
                value = genome.features.scales[name]
                genome.features.scales[name] = self._positive_jitter(
                    value, genome.mutation_policy.sigma, sign
                )
            elif group is genome.memory and genome.memory.feature_weights:
                name = self.rng.choice(list(genome.memory.feature_weights))
                value = genome.memory.feature_weights[name]
                genome.memory.feature_weights[name] = max(
                    0.0,
                    value
                    + sign
                    * max(.05, abs(value))
                    * genome.mutation_policy.sigma
                    * self.rng.uniform(.5, 1.5),
                )
            return
        name = self.rng.choice(fields)
        value = float(getattr(group, name))
        scale = max(.05, abs(value)) * genome.mutation_policy.sigma
        mutated = value + sign * scale * self.rng.uniform(.5, 1.5)
        if any(token in name for token in ("temperature", "scale", "cap", "decay")):
            mutated = max(.001, mutated)
        setattr(group, name, mutated)

    def _gate_mutation(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        sigma = genome.mutation_policy.sigma
        active_skills = [
            (skill_id, gene)
            for skill_id, gene in genome.skills.items()
            if gene.enabled
        ]
        if active_skills and self.rng.random() < .35:
            _, skill = self.rng.choice(active_skills)
            gate = skill.gate
        else:
            gene = self._choose_gene(genome, evidence)
            if gene is None:
                return
            gate = gene.gate
        feature = self._sample_evidence_feature(evidence)
        gate.weights[feature] = gate.weights.get(feature, 0.0) + sign * sigma
        gate.threshold += self.rng.uniform(-sigma, sigma) * .5
        gate.temperature = max(.02, gate.temperature * (1.0 + sign * sigma * .2))

    def _skill_mutation(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        active = [gene for gene in genome.skills.values() if gene.enabled]
        if not active:
            return
        skill = self.rng.choice(active)
        sigma = genome.mutation_policy.sigma
        if skill.action_bias and self.rng.random() < .7:
            action = self.rng.choice(list(skill.action_bias))
            value = skill.action_bias[action]
            skill.action_bias[action] = value + sign * max(.1, abs(value)) * sigma
        else:
            feature = self._sample_evidence_feature(evidence)
            skill.gate.weights[feature] = (
                skill.gate.weights.get(feature, 0.0) + sign * sigma
            )
        skill.reliability = max(
            .05,
            min(2.0, skill.reliability * (1.0 + sign * sigma * .25)),
        )

    def _memory_mutation(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        gene = genome.memory
        sigma = genome.mutation_policy.sigma
        overlap = [
            feature
            for feature in evidence.feature_priorities
            if feature in gene.feature_weights
        ]
        if overlap:
            weights = [evidence.feature_priorities[name] + .05 for name in overlap]
            feature = self.rng.choices(overlap, weights=weights, k=1)[0]
        else:
            feature = self.rng.choice(list(gene.feature_weights))
        value = gene.feature_weights[feature]
        gene.feature_weights[feature] = max(
            0.0, value + sign * max(.05, abs(value)) * sigma
        )
        if self.rng.random() < .5:
            gene.similarity_temperature = self._positive_jitter(
                gene.similarity_temperature, sigma, sign
            )
        else:
            gene.recency_decay = max(
                0.0,
                gene.recency_decay
                + sign * max(.0005, gene.recency_decay) * sigma,
            )

    def _sample_evidence_feature(self, evidence: EvolutionEvidence) -> str:
        names = list(evidence.feature_priorities)
        weights = [max(.01, evidence.feature_priorities[name]) for name in names]
        return self.rng.choices(names, weights=weights, k=1)[0]

    def _positive_jitter(self, value: float, sigma: float, sign: float) -> float:
        return max(
            .001,
            value + sign * max(.05, abs(value)) * sigma * self.rng.uniform(.5, 1.5),
        )


class HarnessEvolutionEngine(BaseHarnessEvolutionEngine):
    """Game R&D optimized self-evolving search engine."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mutator = GameHarnessMutator(random.Random(self.config.seed))
