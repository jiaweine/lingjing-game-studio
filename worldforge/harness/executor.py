from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import Counter
from typing import Awaitable, Callable

from worldforge.envs import get_scenario
from worldforge.models import RunConfig, RuntimeEvent
from worldforge.runtime.engine import WorldForgeEngine

from .schemas import MissionPlan, MissionResult, MissionSpec, MissionStatus, PlannedStep, ToolEvidence

EventSink = Callable[[RuntimeEvent], Awaitable[None]]


class MissionExecutor:
    """Durable conversational mission executor for game-development tasks.

    The language model is not the runtime. Planning depth, tool budget, deterministic call IDs,
    idempotent replay, simulation execution and verification live here. Provider models may later
    enrich a plan or synthesize the report, but they cannot bypass this controller.
    """

    def __init__(self, engine: WorldForgeEngine) -> None:
        self.engine = engine
        self.events = engine.events

    async def run(
        self,
        spec: MissionSpec,
        *,
        mission_id: str | None = None,
        sink: EventSink | None = None,
    ) -> MissionResult:
        mission_id = mission_id or f"mission-{uuid.uuid4().hex[:12]}"
        previous = self.events.list_events(mission_id)
        completed = next((e for e in reversed(previous) if e.event_type == "mission.completed"), None)
        if completed and completed.payload.get("result"):
            return MissionResult.model_validate(completed.payload["result"])

        if not self.events.session_meta(mission_id):
            self.events.create_session(
                mission_id,
                meta={"kind": "mission", "goal": spec.goal, "scene": spec.scene, "spec": spec.model_dump(mode="json")},
            )
        await self._emit(
            mission_id,
            "mission.started" if not previous else "mission.resumed",
            {"goal": spec.goal, "scene": spec.scene, "approval_mode": spec.approval_mode.value},
            sink,
        )

        started = time.monotonic()
        plan = self.plan(spec, mission_id)
        await self._emit(mission_id, "mission.planned", plan.model_dump(), sink)

        evidence: list[ToolEvidence] = []
        child_sessions: list[str] = []
        calls_used = 0

        async def execute(step: PlannedStep) -> ToolEvidence:
            nonlocal calls_used
            if calls_used >= spec.budget.max_tool_calls:
                return ToolEvidence(
                    call_id=self._call_id(mission_id, step),
                    step_id=step.step_id,
                    tool=step.tool,
                    ok=False,
                    output={"error": "tool_budget_exhausted"},
                )
            calls_used += 1
            if time.monotonic() - started > spec.budget.max_runtime_seconds:
                return ToolEvidence(
                    call_id=self._call_id(mission_id, step),
                    step_id=step.step_id,
                    tool=step.tool,
                    ok=False,
                    output={"error": "mission_time_budget_exhausted"},
                )
            return await self._execute_step(mission_id, spec, step, child_sessions, sink)

        # Observation is deliberately first so later work is grounded in the actual scenario contract.
        inspect_steps = [s for s in plan.steps if s.tool == "game.inspect"]
        for step in inspect_steps:
            evidence.append(await execute(step))

        sim_steps = [s for s in plan.steps if s.tool == "game.simulate"]
        semaphore = asyncio.Semaphore(spec.budget.max_parallelism)

        async def bounded(step: PlannedStep):
            async with semaphore:
                return await execute(step)

        if sim_steps:
            evidence.extend(await asyncio.gather(*(bounded(step) for step in sim_steps)))

        verify_steps = [s for s in plan.steps if s.tool == "game.verify"]
        for step in verify_steps:
            evidence.append(await execute(step))

        ok_evidence = [e for e in evidence if e.ok]
        failed = [e for e in evidence if not e.ok]
        outcome_counts = Counter(
            str(e.output.get("summary", {}).get("outcome"))
            for e in ok_evidence
            if e.tool == "game.simulate" and e.output.get("summary")
        )
        verified_children = [
            e.output
            for e in ok_evidence
            if e.tool == "game.verify"
        ]
        verification = {
            "all_required_tools_succeeded": not failed,
            "event_chain_valid": self.events.verify_chain(mission_id),
            "child_sessions_verified": all(x.get("all_valid", False) for x in verified_children) if verified_children else False,
            "failed_steps": [e.step_id for e in failed],
        }
        status = MissionStatus.COMPLETED if verification["all_required_tools_succeeded"] and verification["child_sessions_verified"] else MissionStatus.FAILED
        result = MissionResult(
            mission_id=mission_id,
            status=status,
            goal=spec.goal,
            plan=plan,
            evidence=evidence,
            child_sessions=sorted(set(child_sessions)),
            verification=verification,
            summary={
                "scenario_id": self._scenario_id(spec),
                "runs": len([e for e in evidence if e.tool == "game.simulate" and e.ok]),
                "outcomes": dict(outcome_counts),
                "risk": plan.risk,
                "tool_calls": calls_used,
            },
        )
        await self._emit(
            mission_id,
            "mission.completed" if status == MissionStatus.COMPLETED else "mission.failed",
            {"result": result.model_dump(mode="json")},
            sink,
        )
        return result

    def plan(self, spec: MissionSpec, mission_id: str) -> MissionPlan:
        scenario_id = self._scenario_id(spec)
        risk, reasons = self._risk(spec)
        run_count = {"low": 1, "medium": 2, "high": 3, "critical": 4}[risk]
        run_count = min(run_count, max(1, spec.budget.max_agents - 2))
        steps: list[PlannedStep] = []

        inspect = self._step(
            mission_id,
            "observer",
            "WorldObserver",
            "Read the scenario contract and establish authoritative world facts",
            "game.inspect",
            {"scenario_id": scenario_id},
        )
        steps.append(inspect)

        sim_ids: list[str] = []
        for idx in range(run_count):
            seed = spec.seed + idx * 17
            step = self._step(
                mission_id,
                f"simulate-{idx}",
                "ExecutionAgent" if idx == 0 else "CounterfactualAgent",
                "Execute an isolated scenario run and collect verifier evidence",
                "game.simulate",
                {
                    "scenario_id": scenario_id,
                    "seed": seed,
                    "max_steps": min(24, max(4, spec.budget.rollout_horizon * 5)),
                },
                depends_on=[inspect.step_id],
                parallel_group="simulation",
            )
            steps.append(step)
            sim_ids.append(step.step_id)

        steps.append(
            self._step(
                mission_id,
                "verify",
                "VerifierAgent",
                "Verify child traces and reject an execution result that lacks evidence",
                "game.verify",
                {},
                depends_on=sim_ids,
            )
        )
        return MissionPlan(risk=risk, rationale=reasons, steps=steps)

    async def _execute_step(
        self,
        mission_id: str,
        spec: MissionSpec,
        step: PlannedStep,
        child_sessions: list[str],
        sink: EventSink | None,
    ) -> ToolEvidence:
        call_id = self._call_id(mission_id, step)
        prior = self._completed_tool(mission_id, call_id)
        if prior is not None:
            output = prior.payload.get("output", {})
            if output.get("child_session"):
                child_sessions.append(output["child_session"])
            return ToolEvidence(
                call_id=call_id,
                step_id=step.step_id,
                tool=step.tool,
                ok=bool(prior.payload.get("ok")),
                output=output,
                reused=True,
                elapsed_ms=0.0,
            )

        await self._emit(
            mission_id,
            "agent.started",
            {"step_id": step.step_id, "role": step.role, "objective": step.objective},
            sink,
        )
        await self._emit(
            mission_id,
            "tool.requested",
            {"call_id": call_id, "step_id": step.step_id, "tool": step.tool, "arguments": step.arguments},
            sink,
        )
        started = time.perf_counter()
        ok = True
        try:
            if step.tool == "game.inspect":
                scenario = get_scenario(str(step.arguments["scenario_id"]))
                output = {"scenario": scenario.model_dump(mode="json")}
            elif step.tool == "game.simulate":
                child_session = self._child_session_id(mission_id, step)
                child_sessions.append(child_session)
                child_completed = next(
                    (e for e in reversed(self.events.list_events(child_session)) if e.event_type == "run.completed"),
                    None,
                )
                if child_completed:
                    summary = child_completed.payload.get("summary", {})
                else:
                    run = await self.engine.run(
                        RunConfig(
                            scenario_id=str(step.arguments["scenario_id"]),
                            seed=int(step.arguments["seed"]),
                            max_steps=int(step.arguments["max_steps"]),
                            branch_width=spec.budget.branch_width,
                            rollout_horizon=spec.budget.rollout_horizon,
                            rollouts_per_branch=spec.budget.rollouts_per_branch,
                            enable_evolution=False,
                        ),
                        session_id=child_session,
                        demo_delay=0,
                    )
                    summary = run.model_dump(mode="json")
                output = {
                    "child_session": child_session,
                    "summary": summary,
                    "hash_chain_valid": self.events.verify_chain(child_session),
                }
            elif step.tool == "game.verify":
                checks = [
                    {
                        "session_id": sid,
                        "hash_chain_valid": self.events.verify_chain(sid),
                        "completed": any(e.event_type == "run.completed" for e in self.events.list_events(sid)),
                    }
                    for sid in sorted(set(child_sessions))
                ]
                output = {
                    "checks": checks,
                    "all_valid": bool(checks) and all(x["hash_chain_valid"] and x["completed"] for x in checks),
                }
                ok = bool(output["all_valid"])
            else:  # pragma: no cover - plan schema prevents this
                ok = False
                output = {"error": "unknown_tool"}
        except Exception as exc:  # Runtime captures the failure as evidence instead of losing the task.
            ok = False
            output = {"error": type(exc).__name__, "detail": str(exc)[:240]}

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        await self._emit(
            mission_id,
            "tool.completed",
            {"call_id": call_id, "step_id": step.step_id, "tool": step.tool, "ok": ok, "output": output, "elapsed_ms": elapsed_ms},
            sink,
        )
        await self._emit(
            mission_id,
            "agent.completed" if ok else "agent.failed",
            {"step_id": step.step_id, "role": step.role, "call_id": call_id},
            sink,
        )
        return ToolEvidence(
            call_id=call_id,
            step_id=step.step_id,
            tool=step.tool,
            ok=ok,
            output=output,
            elapsed_ms=elapsed_ms,
        )

    async def _emit(self, mission_id: str, event_type: str, payload: dict, sink: EventSink | None):
        event = self.events.append(mission_id, event_type, payload)
        if sink:
            await sink(event)
        return event

    def _completed_tool(self, mission_id: str, call_id: str):
        return next(
            (
                e
                for e in reversed(self.events.list_events(mission_id))
                if e.event_type == "tool.completed" and e.payload.get("call_id") == call_id
            ),
            None,
        )

    @staticmethod
    def _scenario_id(spec: MissionSpec) -> str:
        if spec.scenario_id:
            return spec.scenario_id
        text = f"{spec.scene} {spec.goal}".lower()
        if any(k in text for k in ("经济", "economy", "资源")):
            return "economy_trap"
        if any(k in text for k in ("漏洞", "exploit", "刷", "回归", "bug")):
            return "loot_exploit"
        if any(k in text for k in ("玻璃", "glass", "极端", "波动", "平衡")):
            return "glass_cannon"
        return "boss_burst"

    @staticmethod
    def _risk(spec: MissionSpec) -> tuple[str, list[str]]:
        text = f"{spec.scene} {spec.goal}".lower()
        score = 0
        reasons: list[str] = []
        critical_terms = ("崩溃", "丢档", "支付", "线上", "发布", " exploit", "漏洞", "critical")
        high_terms = ("回归", "复现", "平衡", "胜率", "boss", "异常", "版本", "数值")
        for term in critical_terms:
            if term.strip() in text:
                score += 3
                reasons.append(f"high-impact signal: {term.strip()}")
        for term in high_terms:
            if term in text:
                score += 1
        if len(spec.goal) > 500:
            score += 1
            reasons.append("long mission specification")
        if spec.scene in {"regression", "balance"}:
            score += 1
        if score >= 7:
            risk = "critical"
        elif score >= 4:
            risk = "high"
        elif score >= 2:
            risk = "medium"
        else:
            risk = "low"
        reasons.insert(0, f"owned risk kernel score={score}")
        return risk, reasons

    @staticmethod
    def _step(
        mission_id: str,
        key: str,
        role: str,
        objective: str,
        tool: str,
        arguments: dict,
        *,
        depends_on: list[str] | None = None,
        parallel_group: str | None = None,
    ) -> PlannedStep:
        step_id = "step-" + hashlib.sha256(f"{mission_id}|{key}|{role}|{tool}".encode()).hexdigest()[:12]
        return PlannedStep(
            step_id=step_id,
            role=role,
            objective=objective,
            tool=tool,
            arguments=arguments,
            depends_on=depends_on or [],
            parallel_group=parallel_group,
        )

    @staticmethod
    def _call_id(mission_id: str, step: PlannedStep) -> str:
        raw = f"{mission_id}|{step.step_id}|{step.tool}|{sorted(step.arguments.items())}".encode()
        return "call-" + hashlib.sha256(raw).hexdigest()[:16]

    @staticmethod
    def _child_session_id(mission_id: str, step: PlannedStep) -> str:
        return "wf-child-" + hashlib.sha256(f"{mission_id}|{step.step_id}".encode()).hexdigest()[:14]
