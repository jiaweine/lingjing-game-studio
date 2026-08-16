from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from worldforge.models import BeliefState, GoalState, WorldState


@dataclass
class TaskNode:
    node_id: str
    role: str
    objective: str
    depth: int
    status: str = "queued"
    conclusion: str = ""
    confidence: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    children: list["TaskNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "objective": self.objective,
            "depth": self.depth,
            "status": self.status,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "elapsed_ms": self.elapsed_ms,
            "children": [c.to_dict() for c in self.children],
        }


class RecursiveAgentScheduler:
    """Deterministic, state-conditioned specialist executor.

    This is intentionally an owned algorithm rather than a collection of role prompts. Every node
    receives a bounded slice of world state, executes an independent analysis function, returns
    evidence, and may cause deeper specialists to be spawned after the first observations arrive.
    IDs are deterministic for a given session/tick/role/path so retries and trace replay can match
    the same logical node.
    """

    async def analyze(
        self,
        state: WorldState,
        belief: BeliefState,
        goal: GoalState,
        *,
        session_id: str = "local",
        tick: int | None = None,
    ) -> TaskNode:
        tick = state.tick if tick is None else tick
        root = self._node(session_id, tick, "Coordinator", "Build a verified specialist view", 0, "root")
        root.status = "running"

        first_wave = [
            self._node(session_id, tick, "CombatAgent", "Estimate progress and finish windows", 1, "combat"),
            self._node(session_id, tick, "RiskAgent", "Estimate terminal, variance and recovery risk", 1, "risk"),
        ]
        if any(tag in state.tags for tag in ("economy", "exploit-test")):
            first_wave.append(
                self._node(session_id, tick, "EconomyAgent", "Audit resource curve and exploit incentives", 1, "economy")
            )
        root.children.extend(first_wave)

        await asyncio.gather(
            *(self._execute(node, self._handler(node.role), state, belief, goal) for node in first_wave)
        )

        # Re-plan after observing the first wave. These nodes do not exist unless evidence warrants it.
        dynamic: list[tuple[TaskNode, TaskNode]] = []
        risk_node = next(n for n in first_wave if n.role == "RiskAgent")
        if belief.uncertainty > 0.45:
            probe = self._node(session_id, tick, "MechanicsProbe", "Resolve hidden enemy mechanics", 2, "risk/mechanics")
            risk_node.children.append(probe)
            dynamic.append((risk_node, probe))
        if state.threat > 0.62 or state.player_hp < state.player_max_hp * 0.4:
            survival = self._node(session_id, tick, "SurvivalAudit", "Find actions that preserve a recovery path", 2, "risk/survival")
            risk_node.children.append(survival)
            dynamic.append((risk_node, survival))
        economy_node = next((n for n in first_wave if n.role == "EconomyAgent"), None)
        if economy_node and "exploit-test" in state.tags:
            exploit = self._node(session_id, tick, "ExploitProbe", "Stress repeated reward loops", 2, "economy/exploit")
            economy_node.children.append(exploit)
            dynamic.append((economy_node, exploit))

        if dynamic:
            await asyncio.gather(
                *(self._execute(node, self._handler(node.role), state, belief, goal) for _, node in dynamic)
            )

        leaves = [n for n in self._walk(root) if n is not root and n.status == "completed"]
        root.confidence = round(sum(n.confidence for n in leaves) / max(1, len(leaves)), 3)
        root.evidence = {
            "specialists": len(leaves),
            "dynamic_specialists": len(dynamic),
            "uncertainty": round(belief.uncertainty, 4),
            "risk_signals": sum(1 for n in leaves if n.evidence.get("risk") in {"high", "critical"}),
        }
        root.conclusion = "specialist evidence collected and dynamic follow-up completed"
        root.status = "completed"
        return root

    async def _execute(
        self,
        node: TaskNode,
        handler: Callable[[WorldState, BeliefState, GoalState], tuple[str, float, dict[str, Any]]],
        state: WorldState,
        belief: BeliefState,
        goal: GoalState,
    ) -> None:
        node.status = "running"
        started = time.perf_counter()
        try:
            # Give every specialist its own scheduling turn/context instead of computing a label tree.
            await asyncio.sleep(0)
            conclusion, confidence, evidence = handler(
                state.model_copy(deep=True), belief.model_copy(deep=True), goal.model_copy(deep=True)
            )
            node.conclusion = conclusion
            node.confidence = round(max(0.0, min(1.0, confidence)), 3)
            node.evidence = evidence
            node.status = "completed"
        except Exception as exc:  # pragma: no cover - defensive trace path
            node.status = "failed"
            node.conclusion = "specialist execution failed"
            node.evidence = {"error": type(exc).__name__}
            node.confidence = 0.0
        finally:
            node.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    def _handler(self, role: str):
        return {
            "CombatAgent": self._combat,
            "RiskAgent": self._risk,
            "EconomyAgent": self._economy,
            "MechanicsProbe": self._mechanics,
            "SurvivalAudit": self._survival,
            "ExploitProbe": self._exploit,
        }[role]

    @staticmethod
    def _combat(state: WorldState, belief: BeliefState, goal: GoalState):
        enemy_ratio = state.enemy_hp / max(1, state.enemy_max_hp)
        burst_ready = state.energy >= 2
        if enemy_ratio < 0.4 and burst_ready:
            conclusion = "enemy is in a potential burst window"
        elif not burst_ready:
            conclusion = "build energy while maintaining progress"
        else:
            conclusion = "damage options available; compare against risk-adjusted branches"
        return conclusion, 0.78, {
            "enemy_ratio": round(enemy_ratio, 4),
            "energy": state.energy,
            "burst_ready": burst_ready,
            "risk": "medium" if state.threat > goal.risk_tolerance else "low",
        }

    @staticmethod
    def _risk(state: WorldState, belief: BeliefState, goal: GoalState):
        hp_ratio = state.player_hp / max(1, state.player_max_hp)
        risk_score = min(1.0, state.threat * 0.58 + (1 - hp_ratio) * 0.42 + belief.uncertainty * 0.2)
        if hp_ratio < 0.3:
            conclusion = "critical survival margin; require a recovery path before irreversible action"
            risk = "critical"
        elif belief.uncertainty > 0.55:
            conclusion = "hidden-mechanic uncertainty is high; prefer robust or information-gathering action"
            risk = "high"
        else:
            conclusion = "risk envelope is acceptable for controlled progress"
            risk = "medium" if risk_score > goal.risk_tolerance else "low"
        return conclusion, 0.84, {
            "hp_ratio": round(hp_ratio, 4),
            "threat": round(state.threat, 4),
            "uncertainty": round(belief.uncertainty, 4),
            "risk_score": round(risk_score, 4),
            "risk": risk,
        }

    @staticmethod
    def _economy(state: WorldState, belief: BeliefState, goal: GoalState):
        constrained = state.gold < 22
        conclusion = (
            "capital available; compare upgrade value with delayed combat progress"
            if not constrained
            else "resource constrained; farm only when the progress budget allows"
        )
        return conclusion, 0.75, {
            "gold": state.gold,
            "attack": state.attack,
            "armor": state.armor,
            "constrained": constrained,
            "risk": "high" if "exploit-test" in state.tags else "low",
        }

    @staticmethod
    def _mechanics(state: WorldState, belief: BeliefState, goal: GoalState):
        known = state.discovered_enemy_attack is not None
        return (
            "enemy attack range is observed" if known else "mechanic remains latent; scout evidence has high information value",
            0.76,
            {
                "known": known,
                "enemy_attack_low": belief.enemy_attack_low,
                "enemy_attack_high": belief.enemy_attack_high,
                "risk": "high" if not known else "low",
            },
        )

    @staticmethod
    def _survival(state: WorldState, belief: BeliefState, goal: GoalState):
        hp_ratio = state.player_hp / max(1, state.player_max_hp)
        recovery_assets = state.healing_potions + int(state.energy >= 2)
        return (
            "recovery path exists" if recovery_assets else "no immediate recovery asset; preserve rollbackable actions",
            0.88,
            {"hp_ratio": round(hp_ratio, 4), "recovery_assets": recovery_assets, "risk": "critical" if not recovery_assets else "high"},
        )

    @staticmethod
    def _exploit(state: WorldState, belief: BeliefState, goal: GoalState):
        return (
            "stress repeated reward acquisition and flag acceleration instead of optimizing it",
            0.9,
            {"tagged_for_exploit_probe": True, "gold": state.gold, "risk": "high"},
        )

    @staticmethod
    def _node(session_id: str, tick: int, role: str, objective: str, depth: int, path: str) -> TaskNode:
        raw = f"{session_id}|{tick}|{role}|{path}".encode()
        node_id = f"sa-{hashlib.sha256(raw).hexdigest()[:12]}"
        return TaskNode(node_id=node_id, role=role, objective=objective, depth=depth)

    @staticmethod
    def _walk(root: TaskNode):
        stack = [root]
        while stack:
            node = stack.pop(0)
            yield node
            stack[0:0] = node.children
