from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import uuid

from worldforge.models import BeliefState, GoalState, WorldState

from .harness_genome import HarnessGenomeStore, state_features


@dataclass
class TaskNode:
    node_id: str
    role: str
    objective: str
    depth: int
    status: str = "completed"
    conclusion: str = ""
    confidence: float = .5
    action_bias: dict[str, float] = field(default_factory=dict)
    children: list["TaskNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "objective": self.objective,
            "depth": self.depth,
            "status": self.status,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "action_bias": dict(self.action_bias),
            "children": [child.to_dict() for child in self.children],
        }


class RecursiveAgentScheduler:
    """Interprets the dynamic specialist topology encoded by HarnessGenome.

    There are no role-specific Python branches here. New specialists can be created, split,
    disabled or reweighted by the evolution engine without editing Runtime source code.
    """

    def deliberate(
        self, state: WorldState, belief: BeliefState, goal: GoalState
    ) -> TaskNode:
        genome = HarnessGenomeStore.current()
        features = state_features(state, belief, goal)
        genes = [
            gene
            for gene in genome.specialists
            if gene.enabled and gene.mode == "dynamic"
        ]
        root = TaskNode(
            self._id(),
            "Coordinator",
            "Route world evidence through the current harness topology",
            0,
            conclusion=f"genome={genome.genome_id}",
            confidence=1.0,
        )

        def build(gene):
            activation = gene.activation(features)
            actions = set(gene.action_bias) | set(gene.action_feature_weights)
            action_bias = {
                action: round(gene.raw_score(action, features), 4)
                for action in actions
            }
            return TaskNode(
                self._id(),
                gene.role,
                f"Apply specialist gene {gene.gene_id}",
                1,
                conclusion=(
                    f"activation={activation:.3f}; generation={genome.generation}"
                ),
                confidence=round(gene.confidence * activation, 4),
                action_bias=action_bias,
            )

        if genes:
            with ThreadPoolExecutor(max_workers=len(genes)) as executor:
                root.children = list(executor.map(build, genes))
            root.confidence = round(
                sum(child.confidence for child in root.children)
                / max(1, len(root.children)),
                4,
            )
        return root

    def analyze(self, state: WorldState, belief: BeliefState, goal: GoalState) -> TaskNode:
        return self.deliberate(state, belief, goal)

    def aggregate_bias(self, tree: TaskNode, *, cap: float | None = None) -> dict[str, float]:
        aggregate: dict[str, float] = {}

        def walk(node: TaskNode) -> None:
            for action, value in node.action_bias.items():
                aggregate[action] = aggregate.get(action, 0.0) + value * node.confidence
            for child in node.children:
                walk(child)

        walk(tree)
        bound = (
            HarnessGenomeStore.current().planner.specialist_cap
            if cap is None
            else cap
        )
        return {
            action: round(max(-bound, min(bound, value)), 4)
            for action, value in aggregate.items()
        }

    @staticmethod
    def _id() -> str:
        return f"sa-{uuid.uuid4().hex[:7]}"
