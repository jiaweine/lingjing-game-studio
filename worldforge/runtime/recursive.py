from __future__ import annotations

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
    confidence: float = 0.5
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
            "children": [c.to_dict() for c in self.children],
        }


class RecursiveAgentScheduler:
    """State-conditioned sub-agent decomposition.

    Unlike a fixed multi-agent graph, the scheduler only spawns specialists when the current world
    state justifies them. Children can be nested one level deeper for uncertainty, survival or
    economy stress. This keeps deliberation adaptive instead of paying a fixed orchestration cost.
    """

    def analyze(self, state: WorldState, belief: BeliefState, goal: GoalState) -> TaskNode:
        root = TaskNode(self._id(), "Coordinator", "Decide next safe high-value action", 0,
                        conclusion="dynamic specialist set constructed", confidence=.8)
        combat = TaskNode(self._id(), "CombatAgent", "Estimate progress and finish windows", 1,
                          conclusion=self._combat(state), confidence=.77)
        risk = TaskNode(self._id(), "RiskAgent", "Estimate terminal and variance risk", 1,
                        conclusion=self._risk(state, belief), confidence=.82)
        root.children.extend([combat, risk])

        if belief.uncertainty > .45:
            probe = TaskNode(self._id(), "MechanicsProbe", "Resolve hidden enemy mechanics", 2,
                             conclusion="prefer information-gathering action before irreversible commitment", confidence=.74)
            risk.children.append(probe)
        if state.threat > .62 or state.player_hp < state.player_max_hp * .4:
            audit = TaskNode(self._id(), "SurvivalAudit", "Find actions that preserve a recovery path", 2,
                             conclusion="require post-action survival margin and rollbackable branch", confidence=.86)
            risk.children.append(audit)
        if any(t in state.tags for t in ["economy","exploit-test"]):
            econ = TaskNode(self._id(), "EconomyAgent", "Audit resource curve and exploit incentives", 1,
                            conclusion=self._economy(state), confidence=.73)
            root.children.append(econ)
            if "exploit-test" in state.tags:
                econ.children.append(TaskNode(self._id(), "ExploitProbe", "Stress repeated reward loops", 2,
                                               conclusion="flag abnormal reward acceleration instead of optimizing it", confidence=.88))
        root.confidence = round(sum(c.confidence for c in root.children)/max(1,len(root.children)),3)
        return root

    @staticmethod
    def _combat(state: WorldState) -> str:
        ratio=state.enemy_hp/max(1,state.enemy_max_hp)
        if ratio < .4 and state.energy>=2: return "enemy is in a potential burst window"
        if state.energy<2: return "build energy while maintaining progress"
        return "damage options available; compare with risk-adjusted branches"

    @staticmethod
    def _risk(state: WorldState, belief: BeliefState) -> str:
        hp=state.player_hp/max(1,state.player_max_hp)
        if hp < .3: return "critical survival margin; avoid irreversible action without branch evidence"
        if belief.uncertainty>.55: return "high latent-mechanic uncertainty; favor information or robust action"
        return "risk envelope acceptable for controlled progress"

    @staticmethod
    def _economy(state: WorldState) -> str:
        if state.gold > 30: return "capital available; compare upgrade value against delayed inflation"
        return "resource constrained; farm only when progress budget allows"

    @staticmethod
    def _id() -> str:
        return f"sa-{uuid.uuid4().hex[:7]}"
