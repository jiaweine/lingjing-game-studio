from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import random
import statistics
import uuid
from typing import Iterable

from worldforge.envs import BalanceLabEnv, get_scenario, list_scenarios
from worldforge.models import GameAction

from .counterfactual import CounterfactualBrancher
from .harness_genome import (
    HarnessGenome,
    HarnessGenomeStore,
    LinearGate,
    SpecialistGene,
    state_features,
)
from .memory import EpisodicMemory, OutcomeRecord
from .planner import AdaptivePlanner
from .recursive import RecursiveAgentScheduler
from .skill_bank import SkillBank
from .verifier import StateVerifier


@dataclass(frozen=True)
class EvolutionEvidence:
    """AHE-style falsifiable diagnosis distilled from a real Runtime trajectory."""

    where: str
    why: str
    feature_priorities: dict[str, float]
    summary: str
    prediction: str

    @property
    def cell(self) -> str:
        return f"{self.where}::{self.why}"


@dataclass(frozen=True)
class EpisodeMetrics:
    scenario_id: str
    seed: int
    objective: float
    quality: float
    safety: float
    efficiency: float
    success: float
    final_score: float
    operations: float


@dataclass(frozen=True)
class GenomeEvaluation:
    objective: float
    quality: float
    safety: float
    efficiency: float
    operations: float
    episodes: tuple[EpisodeMetrics, ...]


@dataclass
class EvolutionCandidate:
    genome: HarnessGenome
    operator: str
    evidence: EvolutionEvidence
    train: GenomeEvaluation | None = None
    heldout: GenomeEvaluation | None = None
    novelty: float = 0.0
    paired_gain: float = 0.0
    lower_bound: float = -1.0
    accepted: bool = False


@dataclass
class EvolutionResult:
    baseline: HarnessGenome
    champion: HarnessGenome
    evidence: EvolutionEvidence
    candidates: list[EvolutionCandidate]
    promoted: bool
    archive_cell: str
    train_gain: float
    heldout_gain: float
    lower_bound: float

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline.card(),
            "champion": self.champion.card(),
            "evidence": {
                "where": self.evidence.where,
                "why": self.evidence.why,
                "feature_priorities": dict(self.evidence.feature_priorities),
                "summary": self.evidence.summary,
                "prediction": self.evidence.prediction,
            },
            "promoted": self.promoted,
            "archive_cell": self.archive_cell,
            "train_gain": round(self.train_gain, 6),
            "heldout_gain": round(self.heldout_gain, 6),
            "lower_bound": round(self.lower_bound, 6),
            "candidates": [
                {
                    "genome": candidate.genome.card(),
                    "operator": candidate.operator,
                    "novelty": round(candidate.novelty, 6),
                    "paired_gain": round(candidate.paired_gain, 6),
                    "lower_bound": round(candidate.lower_bound, 6),
                    "accepted": candidate.accepted,
                    "train": (
                        None
                        if candidate.train is None
                        else {
                            "objective": round(candidate.train.objective, 6),
                            "quality": round(candidate.train.quality, 6),
                            "safety": round(candidate.train.safety, 6),
                            "efficiency": round(candidate.train.efficiency, 6),
                            "operations": round(candidate.train.operations, 3),
                        }
                    ),
                    "heldout": (
                        None
                        if candidate.heldout is None
                        else {
                            "objective": round(candidate.heldout.objective, 6),
                            "quality": round(candidate.heldout.quality, 6),
                            "safety": round(candidate.heldout.safety, 6),
                            "efficiency": round(candidate.heldout.efficiency, 6),
                            "operations": round(candidate.heldout.operations, 3),
                        }
                    ),
                }
                for candidate in self.candidates
            ],
        }


@dataclass
class EvolutionConfig:
    """Frozen evaluation/search budget; these values are not part of task policy."""

    population: int = 8
    train_seeds: tuple[int, ...] = (11, 23)
    heldout_seeds: tuple[int, ...] = (37, 51)
    eval_width_cap: int = 3
    eval_horizon_cap: int = 2
    eval_rollout_cap: int = 2
    min_train_gain: float = 0.001
    heldout_tolerance: float = 0.003
    safety_tolerance: float = 0.0
    max_operation_ratio: float = 1.35
    bootstrap_samples: int = 512
    confidence_quantile: float = 0.10
    seed: int = 20260818


class TraceReflector:
    """Converts trajectory evidence into semantic edit pressure without hand-authored roles."""

    _focus = (
        "hp_missing",
        "threat",
        "uncertainty",
        "urgency",
        "finish_window",
        "economy",
        "exploit",
    )

    @classmethod
    def diagnose(
        cls,
        *,
        state,
        belief,
        goal,
        outcome: str | None,
        anomalies: Iterable[str],
        invalid_actions: int,
        action_counts: dict[str, int],
    ) -> EvolutionEvidence:
        features = state_features(state, belief, goal)
        priorities = {
            name: max(0.0, float(features.get(name, 0.0)))
            for name in cls._focus
        }
        anomaly_list = sorted(set(anomalies))
        if anomaly_list:
            why = "anomaly:" + "+".join(anomaly_list)
            priorities["exploit"] = max(1.0, priorities.get("exploit", 0.0))
        elif invalid_actions:
            why = "invalid-action"
        else:
            why = outcome or "low-yield"

        if action_counts:
            total = max(1, sum(action_counts.values()))
            concentration = max(action_counts.values()) / total
            priorities["urgency"] += max(0.0, concentration - 1.0 / len(action_counts))

        where = max(priorities, key=priorities.get)
        ranked = sorted(priorities.items(), key=lambda item: item[1], reverse=True)
        summary = ", ".join(f"{name}={value:.3f}" for name, value in ranked[:4])
        prediction = (
            f"A mutation aligned with {where} should improve held-out objective "
            f"without reducing verifier safety for pathology {why}."
        )
        return EvolutionEvidence(where, why, priorities, summary, prediction)


class HarnessEvaluator:
    """Deterministic shadow arena used by the frozen promotion gate."""

    def __init__(self, config: EvolutionConfig):
        self.config = config

    def evaluate(
        self,
        genome: HarnessGenome,
        cases: list[tuple[str, int]],
    ) -> GenomeEvaluation:
        episodes = tuple(self._episode(genome, scenario_id, seed) for scenario_id, seed in cases)
        return GenomeEvaluation(
            objective=statistics.mean(item.objective for item in episodes),
            quality=statistics.mean(item.quality for item in episodes),
            safety=statistics.mean(item.safety for item in episodes),
            efficiency=statistics.mean(item.efficiency for item in episodes),
            operations=statistics.mean(item.operations for item in episodes),
            episodes=episodes,
        )

    def _episode(
        self,
        genome: HarnessGenome,
        scenario_id: str,
        seed: int,
    ) -> EpisodeMetrics:
        with HarnessGenomeStore.use(genome):
            scenario = get_scenario(scenario_id)
            env = BalanceLabEnv()
            state = env.reset(scenario, seed)
            goal = scenario.goal.model_copy(deep=True)
            skills = SkillBank()
            memory = EpisodicMemory()
            verifier = StateVerifier()
            planner = AdaptivePlanner(skills, memory)
            scheduler = RecursiveAgentScheduler()
            brancher = CounterfactualBrancher(planner, verifier)
            violations = 0
            operations = 0.0

            for _ in range(goal.max_steps):
                if state.terminal:
                    break
                before = state.model_copy(deep=True)
                belief = planner.make_belief(state)
                tree = scheduler.deliberate(state, belief, goal)
                specialist_bias = scheduler.aggregate_bias(tree)
                ranked = planner.rank(
                    state,
                    env.legal_actions(state),
                    goal,
                    extra_bias=specialist_bias,
                )
                if not ranked.candidates:
                    break

                width, horizon, rollouts = genome.search.allocate(
                    uncertainty=belief.uncertainty,
                    threat=state.threat,
                    width_cap=self.config.eval_width_cap,
                    horizon_cap=self.config.eval_horizon_cap,
                    rollout_cap=self.config.eval_rollout_cap,
                )
                branches = brancher.evaluate(
                    env,
                    ranked.candidates,
                    goal,
                    width=width,
                    horizon=horizon,
                    rollouts=rollouts,
                )
                operations += max(1.0, len(branches) * horizon * rollouts)
                selected = branches[0].first_action if branches else ranked.candidates[0]
                state, reward, _, info = env.step(
                    GameAction(
                        kind=selected,
                        rationale="harness-evolution-shadow-arena",
                        source="harness-evolution",
                    )
                )
                verification = verifier.verify(before, state, info, goal, env.anomalies)
                violations += len(verification.violations)
                memory.add(
                    OutcomeRecord(
                        scenario_id,
                        memory.signature(before),
                        selected.value,
                        reward,
                        bool(state.outcome == "victory"),
                    )
                )

            if not state.terminal:
                state.terminal = True
                state.outcome = "timeout"

            hp = max(0.0, state.player_hp / max(1, state.player_max_hp))
            progress = max(0.0, min(1.0, 1.0 - state.enemy_hp / max(1, state.enemy_max_hp)))
            success = 1.0 if state.outcome == "victory" else 0.0
            score_term = (math.tanh(state.score / 100.0) + 1.0) / 2.0
            quality = success * .55 + progress * .20 + hp * .15 + score_term * .10
            safety = max(0.0, 1.0 - violations / max(1.0, state.tick + 1.0))
            efficiency = 1.0 / (1.0 + operations / max(1.0, state.tick * 8.0))
            objective = quality * .60 + safety * .25 + efficiency * .15
            return EpisodeMetrics(
                scenario_id=scenario_id,
                seed=seed,
                objective=objective,
                quality=quality,
                safety=safety,
                efficiency=efficiency,
                success=success,
                final_score=state.score,
                operations=operations,
            )


class HarnessMutator:
    """ADAS/Promptbreeder/GEPA-inspired program and meta-mutation over HarnessGenome."""

    def __init__(self, rng: random.Random):
        self.rng = rng

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

    def reinforce_operator(
        self,
        genome: HarnessGenome,
        operator: str,
        gain: float,
    ) -> None:
        logits = genome.mutation_policy.operator_logits
        if operator not in logits:
            return
        signal = math.tanh(gain * 20.0)
        logits[operator] = max(-3.0, min(3.0, logits[operator] + .18 * signal))
        if signal > 0:
            genome.mutation_policy.sigma = max(
                .03,
                min(.65, genome.mutation_policy.sigma * (1.0 + .06 * signal)),
            )
        else:
            genome.mutation_policy.sigma = max(
                .03,
                min(.65, genome.mutation_policy.sigma * (1.0 + .03 * signal)),
            )

    def _sample_operator(self, genome: HarnessGenome, *, allow_recombine: bool) -> str:
        items = [
            (name, value)
            for name, value in genome.mutation_policy.operator_logits.items()
            if allow_recombine or name != "recombine"
        ]
        temperature = max(.05, genome.mutation_policy.temperature)
        maximum = max(value for _, value in items)
        weights = [math.exp((value - maximum) / temperature) for _, value in items]
        exploration = max(0.0, genome.mutation_policy.exploration)
        weights = [weight + exploration for weight in weights]
        return self.rng.choices([name for name, _ in items], weights=weights, k=1)[0]

    def _parameter_jitter(self, genome: HarnessGenome, sign: float) -> None:
        groups = [genome.belief, genome.planner, genome.search, genome.utility]
        group = self.rng.choice(groups)
        fields = []
        for name in type(group).model_fields:
            value = getattr(group, name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                fields.append(name)
        if not fields:
            return
        name = self.rng.choice(fields)
        value = float(getattr(group, name))
        scale = max(.05, abs(value)) * genome.mutation_policy.sigma
        mutated = value + sign * scale * self.rng.uniform(.5, 1.5)
        if "temperature" in name or "scale" in name or "cap" in name:
            mutated = max(.05, mutated)
        setattr(group, name, mutated)

    def _gene_relevance(
        self,
        gene: SpecialistGene,
        evidence: EvolutionEvidence,
    ) -> float:
        relevance = 0.0
        for feature, priority in evidence.feature_priorities.items():
            relevance += abs(gene.gate.weights.get(feature, 0.0)) * priority
            for weights in gene.action_feature_weights.values():
                relevance += abs(weights.get(feature, 0.0)) * priority * .5
        return relevance + max(0.01, gene.confidence) * .05

    def _choose_gene(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        *,
        dynamic_only: bool = False,
    ) -> SpecialistGene | None:
        genes = [
            gene
            for gene in genome.specialists
            if gene.enabled and (not dynamic_only or gene.mode == "dynamic")
        ]
        if not genes:
            return None
        weights = [self._gene_relevance(gene, evidence) for gene in genes]
        return self.rng.choices(genes, weights=weights, k=1)[0]

    def _gate_mutation(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        sigma = genome.mutation_policy.sigma
        if genome.skill_gates and self.rng.random() < .35:
            skill_id = self.rng.choice(list(genome.skill_gates))
            gate = genome.skill_gates[skill_id]
        else:
            gene = self._choose_gene(genome, evidence)
            if gene is None:
                return
            gate = gene.gate
        features = list(evidence.feature_priorities)
        weights = [evidence.feature_priorities[name] + .05 for name in features]
        feature = self.rng.choices(features, weights=weights, k=1)[0]
        gate.weights[feature] = gate.weights.get(feature, 0.0) + sign * sigma
        gate.threshold += self.rng.uniform(-sigma, sigma) * .5
        gate.temperature = max(.05, gate.temperature * (1.0 + sign * sigma * .2))

    def _split_specialist(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
        sign: float,
    ) -> None:
        parent = self._choose_gene(genome, evidence, dynamic_only=True)
        if parent is None:
            parent = self._choose_gene(genome, evidence)
        if parent is None:
            return
        child = parent.model_copy(deep=True)
        child.gene_id = f"sg-{uuid.uuid4().hex[:8]}"
        child.role = f"{parent.role}.variant"
        child.mode = "dynamic"
        child.confidence = max(.1, min(1.0, parent.confidence * .9))
        sigma = genome.mutation_policy.sigma
        for feature, priority in evidence.feature_priorities.items():
            if priority <= 0:
                continue
            child.gate.weights[feature] = child.gate.weights.get(feature, 0.0) + sign * sigma * priority
        for action, weights in child.action_feature_weights.items():
            for feature in list(weights):
                weights[feature] += self.rng.uniform(-sigma, sigma)
            if evidence.where not in weights:
                weights[evidence.where] = sign * sigma
        genome.specialists.append(child)

    def _prune_specialist(
        self,
        genome: HarnessGenome,
        evidence: EvolutionEvidence,
    ) -> None:
        dynamic = [gene for gene in genome.specialists if gene.enabled and gene.mode == "dynamic"]
        if len(dynamic) <= 1:
            return
        victim = min(dynamic, key=lambda gene: self._gene_relevance(gene, evidence))
        victim.enabled = False

    def _recombine(
        self,
        left: HarnessGenome,
        right: HarnessGenome,
    ) -> HarnessGenome:
        child = left.model_copy(deep=True)
        child.genome_id = f"hg-{uuid.uuid4().hex[:10]}"
        child.generation = max(left.generation, right.generation) + 1
        child.parent_ids = [left.genome_id, right.genome_id]
        child.origin = "recombine"
        if self.rng.random() < .5:
            child.search = right.search.model_copy(deep=True)
        if self.rng.random() < .5:
            child.utility = right.utility.model_copy(deep=True)
        if self.rng.random() < .5:
            child.planner = right.planner.model_copy(deep=True)
        left_ids = {gene.gene_id for gene in child.specialists}
        for gene in right.specialists:
            if gene.gene_id not in left_ids and self.rng.random() < .5:
                child.specialists.append(gene.model_copy(deep=True))
        return child

    def _meta_mutation(self, genome: HarnessGenome, sign: float) -> None:
        policy = genome.mutation_policy
        names = list(policy.operator_logits)
        target = self.rng.choice(names)
        policy.operator_logits[target] = max(
            -3.0,
            min(3.0, policy.operator_logits[target] + sign * policy.sigma),
        )
        policy.exploration = max(.02, min(1.5, policy.exploration * (1.0 + sign * .08)))
        policy.temperature = max(.05, min(2.0, policy.temperature * (1.0 + sign * .06)))


class SemanticQDArchive:
    """Persistent WHERE x WHY archive, adapted from gated semantic QD."""

    def __init__(self, path: str | Path | None = None, max_per_cell: int = 3):
        self.path = Path(path) if path else None
        self.max_per_cell = max_per_cell
        self.cells: dict[str, list[dict]] = {}
        if self.path and self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                self.cells = dict(payload.get("cells", {}))
            except (ValueError, json.JSONDecodeError):
                self.cells = {}

    def peer_genomes(self, cell: str) -> list[HarnessGenome]:
        peers = []
        for item in self.cells.get(cell, []):
            try:
                peers.append(HarnessGenome.model_validate(item["genome"]))
            except (KeyError, ValueError):
                continue
        return peers

    def add(self, cell: str, candidate: EvolutionCandidate) -> None:
        if candidate.train is None or candidate.heldout is None:
            return
        entries = self.cells.setdefault(cell, [])
        entries.append(
            {
                "genome": candidate.genome.model_dump(mode="json"),
                "objective": candidate.heldout.objective,
                "safety": candidate.heldout.safety,
                "efficiency": candidate.heldout.efficiency,
                "novelty": candidate.novelty,
            }
        )
        entries[:] = self._pareto_entries(entries)[: self.max_per_cell]
        self._save()

    @staticmethod
    def _pareto_entries(entries: list[dict]) -> list[dict]:
        frontier: list[dict] = []
        for candidate in entries:
            dominated = False
            for other in entries:
                if other is candidate:
                    continue
                not_worse = (
                    other["objective"] >= candidate["objective"]
                    and other["safety"] >= candidate["safety"]
                    and other["efficiency"] >= candidate["efficiency"]
                )
                strictly_better = (
                    other["objective"] > candidate["objective"]
                    or other["safety"] > candidate["safety"]
                    or other["efficiency"] > candidate["efficiency"]
                )
                if not_worse and strictly_better:
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        return sorted(
            frontier,
            key=lambda item: (
                item["objective"] + item["safety"] + item["efficiency"],
                item.get("novelty", 0.0),
            ),
            reverse=True,
        )

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"cells": self.cells}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _numeric_vector(genome: HarnessGenome) -> list[float]:
    values: list[float] = []

    def walk(value) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            values.append(float(value))
        elif isinstance(value, dict):
            for key in sorted(value):
                if key in {"generation"}:
                    continue
                walk(value[key])
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(genome.model_dump(mode="python"))
    return values


def genome_distance(left: HarnessGenome, right: HarnessGenome) -> float:
    a = _numeric_vector(left)
    b = _numeric_vector(right)
    length = min(len(a), len(b))
    if length == 0:
        return float(abs(len(a) - len(b)))
    mse = sum((a[index] - b[index]) ** 2 for index in range(length)) / length
    topology = abs(len(left.specialists) - len(right.specialists)) * .1
    return math.sqrt(mse) + topology


def _paired_bootstrap_lower_bound(
    baseline: GenomeEvaluation,
    candidate: GenomeEvaluation,
    *,
    samples: int,
    quantile: float,
    seed: int,
) -> tuple[float, float]:
    baseline_map = {
        (episode.scenario_id, episode.seed): episode.objective
        for episode in baseline.episodes
    }
    differences = [
        episode.objective - baseline_map[(episode.scenario_id, episode.seed)]
        for episode in candidate.episodes
        if (episode.scenario_id, episode.seed) in baseline_map
    ]
    if not differences:
        return candidate.objective - baseline.objective, -1.0
    mean_gain = statistics.mean(differences)
    rng = random.Random(seed)
    bootstrap = []
    for _ in range(max(1, samples)):
        draw = [rng.choice(differences) for _ in differences]
        bootstrap.append(statistics.mean(draw))
    bootstrap.sort()
    index = min(
        len(bootstrap) - 1,
        max(0, int(round((len(bootstrap) - 1) * quantile))),
    )
    return mean_gain, bootstrap[index]


class HarnessEvolutionEngine:
    """Self-evolves the executable harness while keeping the Runtime kernel frozen.

    Design synthesis:
    - ADAS: the agent topology itself is searchable.
    - Promptbreeder: the mutation policy is part of the genotype and adapts itself.
    - GEPA: population search keeps complementary Pareto candidates.
    - AHE/GSME: edits are evidence-linked and credited only by deterministic evaluation.
    - Adaptive Auto-Harness: archive persists across an open-ended task stream.
    """

    def __init__(
        self,
        *,
        config: EvolutionConfig | None = None,
        archive_path: str | Path | None = None,
    ) -> None:
        self.config = config or EvolutionConfig()
        self.rng = random.Random(self.config.seed)
        self.mutator = HarnessMutator(self.rng)
        self.evaluator = HarnessEvaluator(self.config)
        self.archive = SemanticQDArchive(archive_path)

    def evolve(
        self,
        evidence: EvolutionEvidence,
        *,
        baseline: HarnessGenome | None = None,
    ) -> EvolutionResult:
        baseline = (baseline or HarnessGenomeStore.current()).model_copy(deep=True)
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
        peers = self.archive.peer_genomes(evidence.cell)

        candidates: list[EvolutionCandidate] = []
        population = max(2, self.config.population)
        for index in range(population):
            direction = 1.0 if index % 2 == 0 else -1.0
            genome, operator = self.mutator.propose(
                baseline,
                evidence,
                peers=peers,
                direction=direction,
            )
            candidate = EvolutionCandidate(
                genome=genome,
                operator=operator,
                evidence=evidence,
                novelty=genome_distance(baseline, genome),
            )
            candidate.train = self.evaluator.evaluate(genome, train_cases)
            candidate.heldout = self.evaluator.evaluate(genome, heldout_cases)
            candidate.paired_gain, candidate.lower_bound = _paired_bootstrap_lower_bound(
                baseline_heldout,
                candidate.heldout,
                samples=self.config.bootstrap_samples,
                quantile=self.config.confidence_quantile,
                seed=self.config.seed + index + genome.generation,
            )
            self.mutator.reinforce_operator(
                candidate.genome,
                operator,
                candidate.train.objective - baseline_train.objective,
            )
            candidates.append(candidate)

        frontier = self._pareto_frontier(candidates)
        champion = max(
            frontier,
            key=lambda candidate: (
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
            and heldout_gain >= -self.config.heldout_tolerance
            and champion.lower_bound >= -self.config.heldout_tolerance
            and champion.heldout.safety
            >= baseline_heldout.safety - self.config.safety_tolerance
            and champion.heldout.operations <= max(operation_limit, baseline_heldout.operations + 1.0)
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

    @staticmethod
    def _pareto_frontier(
        candidates: list[EvolutionCandidate],
    ) -> list[EvolutionCandidate]:
        frontier: list[EvolutionCandidate] = []
        for candidate in candidates:
            assert candidate.heldout is not None
            dominated = False
            for other in candidates:
                if other is candidate:
                    continue
                assert other.heldout is not None
                not_worse = (
                    other.heldout.objective >= candidate.heldout.objective
                    and other.heldout.safety >= candidate.heldout.safety
                    and other.heldout.efficiency >= candidate.heldout.efficiency
                )
                strictly_better = (
                    other.heldout.objective > candidate.heldout.objective
                    or other.heldout.safety > candidate.heldout.safety
                    or other.heldout.efficiency > candidate.heldout.efficiency
                )
                if not_worse and strictly_better:
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        return frontier or list(candidates)
