from __future__ import annotations

from dataclasses import dataclass
import math
import random
import uuid

from worldforge.envs import list_scenarios

from .harness_evolution import (
    EvolutionCandidate,
    EvolutionConfig,
    EvolutionEvidence,
    EvolutionResult,
    HarnessEvolutionEngine as BaseHarnessEvolutionEngine,
    HarnessMutator,
    _paired_bootstrap_lower_bound,
    genome_distance,
)
from .harness_genome import HarnessGenome, HarnessGenomeStore


@dataclass
class GameEvolutionConfig(EvolutionConfig):
    """Search schedule and frozen promotion protocol for the game-R&D harness.

    These values govern *how* candidates are searched and admitted; they are not task-policy
    constants. The executable task policy itself lives in HarnessGenome and remains evolvable.
    """

    refinement_rounds: int = 2
    elite_fraction: float = 0.5
    refinements_per_elite: int = 1
    min_heldout_gain: float = 0.0
    min_lower_bound: float = 0.0
    quality_tolerance: float = 0.0
    efficiency_tolerance: float = 0.02


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
    """Game-R&D optimized self-evolving search with independent positive-gain promotion.

    Search is deliberately staged. A broad antithetic population first explores around the
    active genome. Train-set elites then become parents for local refinement. Held-out cases
    are never used to generate candidates; they are evaluated only after search is complete.
    Promotion requires positive held-out evidence, not merely tolerance of a regression.
    """

    def __init__(self, *args, **kwargs) -> None:
        if kwargs.get("config") is None:
            kwargs["config"] = GameEvolutionConfig()
        super().__init__(*args, **kwargs)
        self.mutator = GameHarnessMutator(random.Random(self.config.seed))

    def _schedule(self) -> GameEvolutionConfig:
        if isinstance(self.config, GameEvolutionConfig):
            return self.config
        payload = self.config.__dict__.copy()
        return GameEvolutionConfig(**payload)

    @staticmethod
    def _train_rank(candidate: EvolutionCandidate) -> tuple[float, float, float, float]:
        assert candidate.train is not None
        return (
            candidate.train.objective,
            candidate.train.safety,
            candidate.train.efficiency,
            candidate.novelty,
        )

    def evolve(
        self,
        evidence: EvolutionEvidence,
        *,
        baseline: HarnessGenome | None = None,
    ) -> EvolutionResult:
        baseline = (baseline or HarnessGenomeStore.current()).model_copy(deep=True)
        schedule = self._schedule()
        scenarios = [scenario.scenario_id for scenario in list_scenarios()]
        train_cases = [
            (scenario_id, seed)
            for scenario_id in scenarios
            for seed in self.config.train_seeds
        ]
        heldout_cases = [
            (scenario_id, seed)
            for scenario_id in scenarios
            for seed in self.config.heldout_seeds
        ]
        baseline_train = self.evaluator.evaluate(baseline, train_cases)
        baseline_heldout = self.evaluator.evaluate(baseline, heldout_cases)
        archive_peers = self.archive.peer_genomes(evidence.cell)

        candidates: list[EvolutionCandidate] = []
        population = max(2, self.config.population)
        for index in range(population):
            direction = 1.0 if index % 2 == 0 else -1.0
            genome, operator = self.mutator.propose(
                baseline,
                evidence,
                peers=archive_peers,
                direction=direction,
            )
            candidate = EvolutionCandidate(
                genome=genome,
                operator=operator,
                evidence=evidence,
                novelty=genome_distance(baseline, genome),
            )
            candidate.train = self.evaluator.evaluate(genome, train_cases)
            candidates.append(candidate)

        # Small unit-test/search budgets intentionally skip refinement. Production-sized
        # populations use successive train-only refinement, so held-out data stays a judge.
        if population >= 4:
            for round_index in range(max(0, schedule.refinement_rounds)):
                ranked = sorted(candidates, key=self._train_rank, reverse=True)
                elite_count = max(
                    1,
                    min(
                        len(ranked),
                        int(math.ceil(population * max(.05, min(1.0, schedule.elite_fraction)))),
                    ),
                )
                elites = ranked[:elite_count]
                refined: list[EvolutionCandidate] = []
                for elite_index, elite in enumerate(elites):
                    peers = archive_peers + [
                        item.genome for item in elites if item is not elite
                    ]
                    for refinement_index in range(max(1, schedule.refinements_per_elite)):
                        direction = (
                            1.0
                            if (round_index + elite_index + refinement_index) % 2 == 0
                            else -1.0
                        )
                        genome, operator = self.mutator.propose(
                            elite.genome,
                            evidence,
                            peers=peers,
                            direction=direction,
                        )
                        candidate = EvolutionCandidate(
                            genome=genome,
                            operator=f"refine:{operator}",
                            evidence=evidence,
                            novelty=genome_distance(baseline, genome),
                        )
                        candidate.train = self.evaluator.evaluate(genome, train_cases)
                        refined.append(candidate)
                candidates.extend(refined)

        # Held-out evaluation happens only after the search trajectory is frozen.
        for index, candidate in enumerate(candidates):
            candidate.heldout = self.evaluator.evaluate(candidate.genome, heldout_cases)
            candidate.paired_gain, candidate.lower_bound = _paired_bootstrap_lower_bound(
                baseline_heldout,
                candidate.heldout,
                samples=self.config.bootstrap_samples,
                quantile=self.config.confidence_quantile,
                seed=self.config.seed + index + candidate.genome.generation,
            )
            assert candidate.train is not None
            base_operator = candidate.operator.removeprefix("refine:")
            self.mutator.reinforce_operator(
                candidate.genome,
                base_operator,
                candidate.train.objective - baseline_train.objective,
            )

        frontier = self._pareto_frontier(candidates)
        champion = max(
            frontier,
            key=lambda candidate: (
                candidate.lower_bound,
                candidate.heldout.objective if candidate.heldout else -1.0,
                candidate.train.objective if candidate.train else -1.0,
                candidate.novelty,
            ),
        )
        assert champion.train is not None and champion.heldout is not None
        train_gain = champion.train.objective - baseline_train.objective
        heldout_gain = champion.heldout.objective - baseline_heldout.objective
        operation_limit = baseline_heldout.operations * self.config.max_operation_ratio
        champion.accepted = (
            train_gain >= self.config.min_train_gain
            and heldout_gain >= schedule.min_heldout_gain
            and champion.lower_bound >= schedule.min_lower_bound
            and champion.heldout.safety
            >= baseline_heldout.safety - self.config.safety_tolerance
            and champion.heldout.quality
            >= baseline_heldout.quality - schedule.quality_tolerance
            and champion.heldout.efficiency
            >= baseline_heldout.efficiency - schedule.efficiency_tolerance
            and champion.heldout.operations
            <= max(operation_limit, baseline_heldout.operations + 1.0)
        )

        for candidate in frontier:
            self.archive.add(evidence.cell, candidate)

        promoted = champion.accepted
        selected = champion.genome if promoted else baseline
        if promoted:
            HarnessGenomeStore.promote(selected)

        return EvolutionResult(
            baseline=baseline,
            champion=selected,
            evidence=evidence,
            candidates=candidates,
            promoted=promoted,
            archive_cell=evidence.cell,
            train_gain=train_gain,
            heldout_gain=heldout_gain,
            lower_bound=champion.lower_bound,
        )
