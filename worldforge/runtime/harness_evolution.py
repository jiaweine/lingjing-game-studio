from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import random
import statistics
import uuid

from .harness_genome import HarnessGenome, HarnessGenomeStore, SpecialistGene


@dataclass(frozen=True)
class EvolutionEvidence:
    """Falsifiable trajectory diagnosis used to focus harness search."""

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
                    "train": self._evaluation_dict(candidate.train),
                    "heldout": self._evaluation_dict(candidate.heldout),
                }
                for candidate in self.candidates
            ],
        }

    @staticmethod
    def _evaluation_dict(evaluation: GenomeEvaluation | None) -> dict | None:
        if evaluation is None:
            return None
        return {
            "objective": round(evaluation.objective, 6),
            "quality": round(evaluation.quality, 6),
            "safety": round(evaluation.safety, 6),
            "efficiency": round(evaluation.efficiency, 6),
            "operations": round(evaluation.operations, 3),
        }


@dataclass
class EvolutionConfig:
    """Frozen evaluation/search budget; these values are not task policy."""

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


class HarnessEvaluator:
    """Frozen evaluation interface. Domain evaluators implement one episode."""

    def __init__(self, config: EvolutionConfig):
        self.config = config

    def evaluate(
        self,
        genome: HarnessGenome,
        cases: list[tuple[str, int]],
    ) -> GenomeEvaluation:
        episodes = tuple(
            self._episode(genome, scenario_id, seed)
            for scenario_id, seed in cases
        )
        if not episodes:
            raise ValueError("Harness evaluation requires at least one case")
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
        raise NotImplementedError


class HarnessMutator:
    """Reusable structural/meta mutation primitives over the current HarnessGenome schema."""

    def __init__(self, rng: random.Random):
        self.rng = rng

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
        multiplier = 1.0 + (.06 if signal > 0 else .03) * signal
        genome.mutation_policy.sigma = max(
            .03,
            min(.65, genome.mutation_policy.sigma * multiplier),
        )

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
            child.gate.weights[feature] = (
                child.gate.weights.get(feature, 0.0)
                + sign * sigma * priority
            )
        for weights in child.action_feature_weights.values():
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
        dynamic = [
            gene
            for gene in genome.specialists
            if gene.enabled and gene.mode == "dynamic"
        ]
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

        for field in (
            "features",
            "belief",
            "memory",
            "planner",
            "search",
            "utility",
        ):
            if self.rng.random() < .5:
                setattr(child, field, getattr(right, field).model_copy(deep=True))

        for skill_id, skill in right.skills.items():
            if self.rng.random() < .5:
                child.skills[skill_id] = skill.model_copy(deep=True)

        left_ids = {gene.gene_id for gene in child.specialists}
        for gene in right.specialists:
            if gene.gene_id not in left_ids and self.rng.random() < .5:
                child.specialists.append(gene.model_copy(deep=True))
        return child

    def _meta_mutation(self, genome: HarnessGenome, sign: float) -> None:
        policy = genome.mutation_policy
        names = list(policy.operator_logits)
        if not names:
            return
        target = self.rng.choice(names)
        policy.operator_logits[target] = max(
            -3.0,
            min(3.0, policy.operator_logits[target] + sign * policy.sigma),
        )
        policy.exploration = max(
            .0,
            min(1.0, policy.exploration * (1.0 + sign * .08)),
        )
        policy.temperature = max(
            .02,
            min(2.0, policy.temperature * (1.0 + sign * .06)),
        )


class SemanticQDArchive:
    """Persistent WHERE × WHY Pareto archive for complementary harness elites."""

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
                if key != "generation":
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
    """Generic evolution infrastructure; vertical search policy lives in harness_search.py."""

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
        raise NotImplementedError(
            "Use the vertical HarnessEvolutionEngine from harness_search.py"
        )

    @staticmethod
    def _pareto_frontier(
        candidates: list[EvolutionCandidate],
    ) -> list[EvolutionCandidate]:
        frontier: list[EvolutionCandidate] = []
        for candidate in candidates:
            if candidate.heldout is None:
                continue
            dominated = False
            for other in candidates:
                if other is candidate or other.heldout is None:
                    continue
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
