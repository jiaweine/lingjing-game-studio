from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import uuid

from worldforge.models import BeliefState, GoalState, WorldState


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
    """State-conditioned in-house specialist deliberation.

    Specialists are created only when the current state requires them. They return bounded
    action preferences; the Runtime planner remains the sole authority that combines advice,
    policy prior, memory, skills, counterfactual evidence and verification.
    """

    def deliberate(
        self, state: WorldState, belief: BeliefState, goal: GoalState
    ) -> TaskNode:
        root = TaskNode(
            self._id(), "Coordinator", "Choose the next robust action", 0,
            conclusion="dynamic specialists completed", confidence=.8,
        )
        factories = [self._combat_agent, self._progress_agent]
        if state.threat > .45 or state.player_hp < state.player_max_hp * .62:
            factories.append(self._risk_agent)
        if belief.uncertainty > .38 and state.discovered_enemy_attack is None:
            factories.append(self._uncertainty_agent)
        if any(tag in state.tags for tag in ("economy", "exploit-test")):
            factories.append(self._economy_agent)

        with ThreadPoolExecutor(max_workers=max(1, len(factories))) as executor:
            futures = [
                executor.submit(factory, state, belief, goal)
                for factory in factories
            ]
            root.children = [future.result() for future in futures]

        root.confidence = round(
            sum(child.confidence for child in root.children) / max(1, len(root.children)), 3
        )
        return root

    # Backward-compatible name for callers that used analyze().
    def analyze(self, state: WorldState, belief: BeliefState, goal: GoalState) -> TaskNode:
        return self.deliberate(state, belief, goal)

    def aggregate_bias(self, tree: TaskNode, *, cap: float = 4.5) -> dict[str, float]:
        aggregate: dict[str, float] = {}

        def walk(node: TaskNode) -> None:
            for action, value in node.action_bias.items():
                aggregate[action] = aggregate.get(action, 0.0) + value * node.confidence
            for child in node.children:
                walk(child)

        walk(tree)
        return {
            action: round(max(-cap, min(cap, value)), 4)
            for action, value in aggregate.items()
        }

    def _combat_agent(self, state, belief, goal):
        ratio = state.enemy_hp / max(1, state.enemy_max_hp)
        bias = {"attack": 1.0}
        conclusion = "maintain pressure"
        if ratio < .45 and state.energy >= 2:
            bias.update({"heavy_attack": 2.6, "cast": 2.2})
            conclusion = "finish window is open"
        elif state.energy < 2:
            bias.update({"attack": 1.2, "defend": .35})
            conclusion = "rebuild energy without stalling"
        return TaskNode(
            self._id(), "CombatSpecialist", "Estimate finish windows", 1,
            conclusion=conclusion, confidence=.78, action_bias=bias,
        )

    def _risk_agent(self, state, belief, goal):
        hp = state.player_hp / max(1, state.player_max_hp)
        bias: dict[str, float] = {}
        if hp < .32:
            bias = {"heal": 3.4, "defend": 2.8, "retreat": .6, "heavy_attack": -2.1, "cast": -1.7}
            conclusion = "preserve a recovery path before committing"
        else:
            bias = {"defend": state.threat * 1.6, "heavy_attack": -.5 * state.threat}
            conclusion = "risk is manageable with a survival margin"
        return TaskNode(
            self._id(), "RiskSpecialist", "Bound catastrophic downside", 1,
            conclusion=conclusion, confidence=.86, action_bias=bias,
        )

    def _uncertainty_agent(self, state, belief, goal):
        return TaskNode(
            self._id(), "MechanicsSpecialist", "Resolve hidden mechanics", 1,
            conclusion="reduce uncertainty before irreversible commitment",
            confidence=.82,
            action_bias={"scout": 3.2, "defend": .4},
        )

    def _economy_agent(self, state, belief, goal):
        bias = {"farm": .45, "buy_armor": 1.2, "buy_blade": .8}
        conclusion = "spend only when it improves the remaining horizon"
        if "exploit-test" in state.tags:
            bias["farm"] = -2.6
            conclusion = "avoid optimizing a suspicious reward loop"
        return TaskNode(
            self._id(), "EconomySpecialist", "Audit resource trajectory", 1,
            conclusion=conclusion, confidence=.76, action_bias=bias,
        )

    def _progress_agent(self, state, belief, goal):
        remaining = max(1, goal.max_steps - state.tick)
        urgency = min(2.4, 7.0 / remaining)
        return TaskNode(
            self._id(), "ProgressSpecialist", "Protect long-horizon goal progress", 1,
            conclusion=f"{remaining} decision steps remain",
            confidence=.74,
            action_bias={
                "attack": .6 + urgency,
                "heavy_attack": .5 + urgency,
                "cast": .45 + urgency,
                "farm": -urgency if remaining < 6 else 0.0,
                "scout": -.8 if state.tick > 4 else 0.0,
            },
        )

    @staticmethod
    def _id() -> str:
        return f"sa-{uuid.uuid4().hex[:7]}"
