from __future__ import annotations

from dataclasses import dataclass
import math
import random
import statistics
import uuid

from worldforge.envs import list_scenarios

from .game_harness_evaluator import GameHarnessEvaluator
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
    """Frozen search/admission protocol for the game-R&D harness.

    Search hyperparameters and admission rules live outside HarnessGenome on purpose: the
    harness can evolve its executable behavior, but cannot rewrite its evaluator, confidence
    protocol, safety floor, or resource ceiling.
    """

    refinement_rounds: int = 2
    elite_fraction: float = 0.5
    refinements_per_elite: int = 1
    stability_penalty: float = 0.30
    plateau_rounds: int = 2
    plateau_sigma_growth: float = 1.8
    max_sigma: float = 0.85
    trust_region_elites: int = 4
    trust_region_alphas: tuple[float, ...] = (0.25, 0.50, 0.75)
    trust_region_bisection_steps: int = 7
    min_heldout_gain: float = 0.0
    min_lower_bound: float = 0.0
    quality_tolerance: float = 0.0
    efficiency_tolerance: float = 0.02

    # Frozen game-R&D evaluation protocol. Findings are evidence; unsafe execution is not.
    quality_success_weight: float = 0.50
    quality_progress_weight: float = 0.18
    quality_health_weight: float = 0.10
    quality_score_weight: float = 0.07
    quality_diagnostic_weight: float = 0.15
    operation_normalizer: float = 8.0
    objective_quality_weight: float = 0.60
    objective_safety_weight: float = 0.25
    objective_efficiency_weight: float = 0.15


class GameHarnessMutator(HarnessMutator):
    """Vertical program mutator for game-R&D harnesses.

    Representation, memory, skills, specialist topology, gates, planner/search/utility and the
    mutation policy are searchable. The frozen verifier, evaluator and promotion gate are not.
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

    def propose_pair(
        self,
        parent: HarnessGenome,
        evidence: EvolutionEvidence,
        *,
        peers: list[HarnessGenome] | None = None,
        sigma_scale: float = 1.0,
    ) -> tuple[tuple[HarnessGenome, str], tuple[HarnessGenome, str]]:
        """True antithetic pair: same sampled edit plan, opposite numerical direction."""
        scaled = parent.model_copy(deep=True)
        scaled.mutation_policy.sigma = min(
            0.95,
            max(0.01, scaled.mutation_policy.sigma * sigma_scale),
        )
        state = self.rng.getstate()
        plus = self.propose(scaled, evidence, peers=peers, direction=1.0)
        end_state = self.rng.getstate()
        self.rng.setstate(state)
        minus = self.propose(scaled, evidence, peers=peers, direction=-1.0)
        self.rng.setstate(end_state)
        return plus, minus

    def _sample_operator(self, genome: HarnessGenome, *, allow_recombine: bool) -> str:
        """Learned softmax exploitation with an independent uniform exploration mixture."""
        items = sorted(
            (
                (name, value)
                for name, value in genome.mutation_policy.operator_logits.items()
                if allow_recombine or name != "recombine"
            ),
            key=lambda item: item[0],
        )
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
        fields = sorted(
            name
            for name in type(group).model_fields
            if isinstance(getattr(group, name), (int, float))
            and not isinstance(getattr(group, name), bool)
        )
        if not fields:
            if group is genome.features and genome.features.scales:
                name = self.rng.choice(sorted(genome.features.scales))
                genome.features.scales[name] = self._positive_jitter(
                    genome.features.scales[name], genome.mutation_policy.sigma, sign
                )
            elif group is genome.memory and genome.memory.feature_weights:
                name = self.rng.choice(sorted(genome.memory.feature_weights))
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
            genome.skills[skill_id]
            for skill_id in sorted(genome.skills)
            if genome.skills[skill_id].enabled
        ]
        if active_skills and self.rng.random() < .35:
            gate = self.rng.choice(active_skills).gate
        else:
            gene = self._choose_gene(genome, evidence)
            if gene is None:
                return
            gate = gene.gate
        feature = self._sample_evidence_feature(evidence)
        gate.weights[feature] = gate.weights.get(feature, 0.0) + sign * sigma
        gate.threshold += sign * self.rng.uniform(.25, .75) * sigma
        gate.temperature = max(.02, gate.temperature * (1.0 + sign * sigma * .2))

    def _skill_mutation(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        active = [
            genome.skills[skill_id]
            for skill_id in sorted(genome.skills)
            if genome.skills[skill_id].enabled
        ]
        if not active:
            return
        skill = self.rng.choice(active)
        sigma = genome.mutation_policy.sigma
        if skill.action_bias and self.rng.random() < .7:
            action = self.rng.choice(sorted(skill.action_bias))
            value = skill.action_bias[action]
            skill.action_bias[action] = value + sign * max(.1, abs(value)) * sigma
        else:
            feature = self._sample_evidence_feature(evidence)
            skill.gate.weights[feature] = skill.gate.weights.get(feature, 0.0) + sign * sigma
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
        overlap = sorted(
            feature
            for feature in evidence.feature_priorities
            if feature in gene.feature_weights
        )
        if overlap:
            weights = [evidence.feature_priorities[name] + .05 for name in overlap]
            feature = self.rng.choices(overlap, weights=weights, k=1)[0]
        else:
            feature = self.rng.choice(sorted(gene.feature_weights))
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
        names = sorted(evidence.feature_priorities)
        weights = [max(.01, evidence.feature_priorities[name]) for name in names]
        return self.rng.choices(names, weights=weights, k=1)[0]

    @staticmethod
    def _positive_jitter(value: float, sigma: float, sign: float) -> float:
        return max(.001, value + sign * max(.05, abs(value)) * sigma)


class HarnessEvolutionEngine(BaseHarnessEvolutionEngine):
    """Behavior-sensitive self-evolving search for game R&D.

    Broad antithetic exploration is followed by train-only stable-elite refinement and a
    minimum-effective-edit trust region. Held-out cases stay sealed until the entire search
    trajectory is frozen. Promotion requires non-regressive paired held-out evidence and no
    quality/safety regression.
    """

    def __init__(self, *args, **kwargs) -> None:
        raw = kwargs.get("config")
        if raw is None:
            kwargs["config"] = GameEvolutionConfig()
        elif not isinstance(raw, GameEvolutionConfig):
            kwargs["config"] = GameEvolutionConfig(**raw.__dict__)
        super().__init__(*args, **kwargs)
        self.mutator = GameHarnessMutator(random.Random(self.config.seed))
        self.evaluator = GameHarnessEvaluator(self.config)

    @staticmethod
    def _evaluation_signature(evaluation) -> tuple:
        return tuple(
            (
                episode.scenario_id,
                episode.seed,
                round(episode.objective, 9),
                round(episode.quality, 9),
                round(episode.safety, 9),
                round(episode.efficiency, 9),
                episode.success,
                round(episode.final_score, 6),
                round(episode.operations, 3),
            )
            for episode in evaluation.episodes
        )

    def _train_rank(self, candidate: EvolutionCandidate) -> tuple[float, float, float, float, float]:
        assert candidate.train is not None
        values = [episode.objective for episode in candidate.train.episodes]
        spread = statistics.pstdev(values) if len(values) > 1 else 0.0
        stable_objective = candidate.train.objective - self.config.stability_penalty * spread
        return (
            stable_objective,
            min(values) if values else candidate.train.objective,
            candidate.train.safety,
            candidate.train.efficiency,
            candidate.novelty,
        )

    def _candidate(
        self,
        genome: HarnessGenome,
        operator: str,
        evidence: EvolutionEvidence,
        baseline: HarnessGenome,
        train_cases,
    ) -> EvolutionCandidate:
        candidate = EvolutionCandidate(
            genome=genome,
            operator=operator,
            evidence=evidence,
            novelty=genome_distance(baseline, genome),
        )
        candidate.train = self.evaluator.evaluate(genome, train_cases)
        return candidate

    @staticmethod
    def _blend_value(left, right, alpha: float):
        if isinstance(left, bool) or isinstance(right, bool):
            return right
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) + (float(right) - float(left)) * alpha
        if isinstance(left, dict) and isinstance(right, dict):
            keys = sorted(set(left) | set(right), key=str)
            return {
                key: HarnessEvolutionEngine._blend_value(
                    left.get(key, right.get(key)), right.get(key, left.get(key)), alpha
                )
                for key in keys
            }
        if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            return [
                HarnessEvolutionEngine._blend_value(a, b, alpha)
                for a, b in zip(left, right)
            ]
        return right

    def _blend_genomes(
        self,
        baseline: HarnessGenome,
        candidate: HarnessGenome,
        alpha: float,
    ) -> HarnessGenome | None:
        if len(baseline.specialists) != len(candidate.specialists):
            return None
        if [gene.gene_id for gene in baseline.specialists] != [
            gene.gene_id for gene in candidate.specialists
        ]:
            return None
        if set(baseline.skills) != set(candidate.skills):
            return None
        payload = self._blend_value(baseline.model_dump(), candidate.model_dump(), alpha)
        payload["genome_id"] = f"hg-{uuid.uuid4().hex[:10]}"
        payload["generation"] = candidate.generation
        payload["parent_ids"] = [baseline.genome_id, candidate.genome_id]
        payload["origin"] = f"trust-region:{candidate.origin}:{alpha:.4f}"
        return HarnessGenome.model_validate(payload)

    def _minimum_effective_edit(
        self,
        baseline: HarnessGenome,
        elite: EvolutionCandidate,
        evidence: EvolutionEvidence,
        train_cases,
        baseline_train,
        baseline_signature,
    ) -> list[EvolutionCandidate]:
        """Train-only bisection to cross an argmax behavior boundary with minimal edit size."""
        assert elite.train is not None
        if elite.train.objective < baseline_train.objective + self.config.min_train_gain:
            return []
        if self._evaluation_signature(elite.train) == baseline_signature:
            return []
        if self._blend_genomes(baseline, elite.genome, 1.0) is None:
            return []

        low = 0.0
        high = 1.0
        best: EvolutionCandidate | None = None
        probes: list[EvolutionCandidate] = []
        for _ in range(max(1, self.config.trust_region_bisection_steps)):
            alpha = (low + high) / 2.0
            blended = self._blend_genomes(baseline, elite.genome, alpha)
            if blended is None:
                break
            probe = self._candidate(
                blended,
                f"boundary:{alpha:.4f}:{elite.operator}",
                evidence,
                baseline,
                train_cases,
            )
            probes.append(probe)
            assert probe.train is not None
            effective = (
                self._evaluation_signature(probe.train) != baseline_signature
                and probe.train.objective
                >= baseline_train.objective + self.config.min_train_gain
            )
            if effective:
                best = probe
                high = alpha
            else:
                low = alpha

        if best is None:
            return probes
        # Keep the minimal effective probe plus its nearest tested neighbor. This remains
        # train-only selection; held-out data has not been touched yet.
        near = sorted(
            probes,
            key=lambda item: abs(float(item.operator.split(":")[1]) - high),
        )[:2]
        selected: list[EvolutionCandidate] = []
        for item in [best, *near]:
            if not any(existing is item for existing in selected):
                selected.append(item)
        return selected

    def _champion_rank(self, candidate: EvolutionCandidate) -> tuple:
        assert candidate.train is not None and candidate.heldout is not None
        stable = self._train_rank(candidate)
        return (
            round(candidate.lower_bound, 12),
            round(candidate.heldout.objective, 12),
            round(candidate.heldout.safety, 12),
            round(candidate.heldout.quality, 12),
            round(candidate.heldout.efficiency, 12),
            round(stable[0], 12),
            round(stable[1], 12),
            -round(candidate.novelty, 12),
            -candidate.genome.generation,
            candidate.operator,
        )

    def evolve(
        self,
        evidence: EvolutionEvidence,
        *,
        baseline: HarnessGenome | None = None,
    ) -> EvolutionResult:
        baseline = (baseline or HarnessGenomeStore.current()).model_copy(deep=True)
        scenarios = sorted(scenario.scenario_id for scenario in list_scenarios())
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
        baseline_signature = self._evaluation_signature(baseline_train)
        archive_peers = sorted(
            self.archive.peer_genomes(evidence.cell),
            key=lambda genome: genome.genome_id,
        )

        candidates: list[EvolutionCandidate] = []
        population = max(2, self.config.population)
        pair_count = int(math.ceil(population / 2))
        search_rounds = 1 if population < 4 else max(1, self.config.plateau_rounds + 1)

        sigma_scale = 1.0
        for plateau_round in range(search_rounds):
            round_candidates: list[EvolutionCandidate] = []
            for _ in range(pair_count):
                plus, minus = self.mutator.propose_pair(
                    baseline,
                    evidence,
                    peers=archive_peers,
                    sigma_scale=sigma_scale,
                )
                for genome, operator in (plus, minus):
                    round_candidates.append(
                        self._candidate(
                            genome,
                            f"es{plateau_round}:{operator}",
                            evidence,
                            baseline,
                            train_cases,
                        )
                    )
            candidates.extend(round_candidates[:population])
            informative = sum(
                self._evaluation_signature(candidate.train) != baseline_signature
                for candidate in round_candidates
                if candidate.train is not None
            )
            if informative >= max(2, len(round_candidates) // 3):
                break
            sigma_scale = min(
                self.config.max_sigma / max(.01, baseline.mutation_policy.sigma),
                sigma_scale * self.config.plateau_sigma_growth,
            )

        if population >= 4:
            for round_index in range(max(0, self.config.refinement_rounds)):
                ranked = sorted(candidates, key=self._train_rank, reverse=True)
                elite_count = max(
                    1,
                    min(
                        len(ranked),
                        int(math.ceil(population * max(.05, min(1.0, self.config.elite_fraction)))),
                    ),
                )
                elites = ranked[:elite_count]
                refined: list[EvolutionCandidate] = []
                for elite in elites:
                    peers = archive_peers + [item.genome for item in elites if item is not elite]
                    for _ in range(max(1, self.config.refinements_per_elite)):
                        plus, minus = self.mutator.propose_pair(
                            elite.genome,
                            evidence,
                            peers=peers,
                        )
                        pair = [
                            self._candidate(
                                genome,
                                f"refine{round_index}:{operator}",
                                evidence,
                                baseline,
                                train_cases,
                            )
                            for genome, operator in (plus, minus)
                        ]
                        refined.append(max(pair, key=self._train_rank))
                candidates.extend(refined)

            ranked = sorted(candidates, key=self._train_rank, reverse=True)
            numerical_elites = [
                item
                for item in ranked
                if len(item.genome.specialists) == len(baseline.specialists)
                and [gene.gene_id for gene in item.genome.specialists]
                == [gene.gene_id for gene in baseline.specialists]
                and set(item.genome.skills) == set(baseline.skills)
            ][: max(1, self.config.trust_region_elites)]

            # Coarse train-only trust-region probes.
            for elite in numerical_elites:
                assert elite.train is not None
                if elite.train.objective <= baseline_train.objective:
                    continue
                for alpha in self.config.trust_region_alphas:
                    blended = self._blend_genomes(baseline, elite.genome, alpha)
                    if blended is None:
                        continue
                    candidates.append(
                        self._candidate(
                            blended,
                            f"trust:{alpha:.2f}:{elite.operator}",
                            evidence,
                            baseline,
                            train_cases,
                        )
                    )

            # Then bisect the behavior boundary using train only. This is the key protection
            # against discrete argmax plateaus: take the smallest edit that is measurably useful.
            for elite in numerical_elites:
                candidates.extend(
                    self._minimum_effective_edit(
                        baseline,
                        elite,
                        evidence,
                        train_cases,
                        baseline_train,
                        baseline_signature,
                    )
                )

        # The search trajectory is frozen here. Held-out cases have not influenced generation,
        # ranking, refinement, trust-region scaling or boundary selection.
        operation_limit = baseline_heldout.operations * self.config.max_operation_ratio
        for index, candidate in enumerate(candidates):
            candidate.heldout = self.evaluator.evaluate(candidate.genome, heldout_cases)
            candidate.paired_gain, candidate.lower_bound = _paired_bootstrap_lower_bound(
                baseline_heldout,
                candidate.heldout,
                samples=self.config.bootstrap_samples,
                quantile=self.config.confidence_quantile,
                seed=self.config.seed + index + candidate.genome.generation,
            )
            assert candidate.train is not None and candidate.heldout is not None
            train_gain = candidate.train.objective - baseline_train.objective
            heldout_gain = candidate.heldout.objective - baseline_heldout.objective
            candidate.accepted = (
                train_gain >= self.config.min_train_gain
                and heldout_gain >= self.config.min_heldout_gain
                and candidate.lower_bound >= self.config.min_lower_bound
                and candidate.heldout.safety
                >= baseline_heldout.safety - self.config.safety_tolerance
                and candidate.heldout.quality
                >= baseline_heldout.quality - self.config.quality_tolerance
                and candidate.heldout.efficiency
                >= baseline_heldout.efficiency - self.config.efficiency_tolerance
                and candidate.heldout.operations
                <= max(operation_limit, baseline_heldout.operations + 1.0)
            )

        eligible = [candidate for candidate in candidates if candidate.accepted]
        pool = self._pareto_frontier(eligible or candidates)
        champion_eval = max(pool, key=self._champion_rank)
        promoted = bool(eligible)
        selected = champion_eval.genome if promoted else baseline

        assert champion_eval.train is not None and champion_eval.heldout is not None
        train_gain = champion_eval.train.objective - baseline_train.objective
        heldout_gain = champion_eval.heldout.objective - baseline_heldout.objective

        for candidate in self._pareto_frontier(candidates):
            self.archive.add(evidence.cell, candidate)

        # The promoted object is byte-for-byte the object that passed held-out evaluation.
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
            lower_bound=champion_eval.lower_bound,
        )
