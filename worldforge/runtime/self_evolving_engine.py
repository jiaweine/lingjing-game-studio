from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from worldforge.envs import get_scenario
from worldforge.models import RunConfig, WorldState

from .engine import WorldForgeEngine as FrozenWorldForgeEngine
from .harness_genome import HarnessGenome, HarnessGenomeStore
from .harness_reflection import TraceReflector
from .harness_search import HarnessEvolutionEngine
from .plugin import PluginDescriptor


class SelfEvolvingWorldForgeEngine(FrozenWorldForgeEngine):
    """Product Runtime: frozen execution kernel + self-evolving harness.

    The base engine keeps canonical-state ownership, sandboxing, verification, rollback,
    event integrity and bounded policy optimization. This outer harness owns the evolvable
    program surface and its independent shadow-arena promotion loop.
    """

    def __init__(self, db_path: str | Path, **kwargs) -> None:
        db_path = Path(db_path)
        self.harness_path = db_path.with_name("worldforge_harness.json")
        HarnessGenomeStore.configure(self.harness_path)
        super().__init__(db_path, **kwargs)

        self.harness_evolver = HarnessEvolutionEngine(
            archive_path=db_path.with_name("worldforge_harness_archive.json")
        )
        self._mount_harness_genome(HarnessGenomeStore.snapshot())
        self.plugins.mount(
            PluginDescriptor(
                "harness-evolution",
                "evolution",
                dependencies=("harness-genome", "state-verifier"),
                metadata={
                    "semantic_qd": True,
                    "pareto_selection": True,
                    "heldout_gate": True,
                    "topology_search": True,
                    "skill_mutation": True,
                    "memory_mutation": True,
                    "meta_mutation": True,
                    "compare_and_swap_promotion": True,
                },
            ),
            self.harness_evolver,
        )

    def _mount_harness_genome(self, genome: HarnessGenome) -> None:
        self.plugins.mount(
            PluginDescriptor(
                "harness-genome",
                "harness",
                dependencies=("world-state", "state-verifier"),
                metadata={
                    "self_evolving": True,
                    "frozen_kernel": True,
                    "task_generation_pinning": True,
                    **genome.card(),
                },
            ),
            genome,
        )

    async def run(self, config: RunConfig, **kwargs):
        # A worker refreshes durable state only at the task boundary, then pins that exact
        # generation for the whole canonical trajectory. Another worker may promote meanwhile,
        # but this task never changes phenotype halfway through execution.
        baseline = HarnessGenomeStore.snapshot()
        self._mount_harness_genome(baseline)
        with HarnessGenomeStore.use(baseline):
            summary = await super().run(config, **kwargs)
            if not config.enable_evolution:
                return summary

            session_id = summary.session_id
            events = self.events.list_events(session_id)
            completed = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "run.completed"
                ),
                None,
            )
            if completed is None:
                return summary

            final_state_payload = completed.payload.get("final_state")
            if not final_state_payload:
                return summary
            state = WorldState.model_validate(final_state_payload)
            findings = list(completed.payload.get("findings") or [])
            action_counts: Counter[str] = Counter()
            for event in events:
                if event.event_type == "action.executed":
                    action = event.payload.get("action")
                    if action:
                        action_counts[action] += 1

            should_evolve = bool(
                summary.outcome != "victory"
                or findings
                or summary.recovery_events
                or summary.invalid_actions
            )
            if not should_evolve:
                return summary

            scenario = get_scenario(config.scenario_id)
            goal = scenario.goal.model_copy(deep=True)
            goal.max_steps = config.max_steps
            belief = self.planner.make_belief(state)
            evidence = TraceReflector.diagnose(
                state=state,
                belief=belief,
                goal=goal,
                outcome=summary.outcome,
                anomalies=findings,
                invalid_actions=summary.invalid_actions,
                action_counts=dict(action_counts),
            )

        await self._emit(
            session_id,
            "harness.evolution.started",
            {
                "baseline": baseline.card(),
                "evidence": {
                    "where": evidence.where,
                    "why": evidence.why,
                    "summary": evidence.summary,
                    "prediction": evidence.prediction,
                },
            },
            kwargs.get("sink"),
        )

        async with self._evolution_lock:
            result = await asyncio.to_thread(
                self.harness_evolver.evolve,
                evidence,
                baseline=baseline,
            )

        summary.evolved = bool(summary.evolved or result.promoted)
        await self._emit(
            session_id,
            "harness.evolution",
            result.to_dict(),
            kwargs.get("sink"),
        )

        gate_passed = any(candidate.accepted for candidate in result.candidates)
        if gate_passed and not result.promoted:
            current = HarnessGenomeStore.snapshot()
            self._mount_harness_genome(current)
            await self._emit(
                session_id,
                "harness.promotion.stale",
                {
                    "evaluated_baseline": baseline.card(),
                    "rejected_champion": result.champion.card(),
                    "current": current.card(),
                    "reason": "durable baseline advanced before compare-and-swap promotion",
                },
                kwargs.get("sink"),
            )
        elif result.promoted:
            current = HarnessGenomeStore.snapshot()
            self._mount_harness_genome(current)
            await self._emit(
                session_id,
                "harness.promoted",
                {
                    "previous": baseline.card(),
                    "current": current.card(),
                    "archive_cell": result.archive_cell,
                    "heldout_gain": result.heldout_gain,
                    "lower_bound": result.lower_bound,
                },
                kwargs.get("sink"),
            )
        return summary
