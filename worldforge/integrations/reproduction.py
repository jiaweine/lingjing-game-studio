from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .game_execution import (
    EvidenceProvenance,
    ExecutionAction,
    ExecutionCheckpoint,
    ExecutionObservation,
    GameBuildRef,
    GameExecutionAdapter,
    VerificationResult,
)


@dataclass(frozen=True)
class AssertionSpec:
    assertion: str
    expected: Any = None


@dataclass(frozen=True)
class ReproductionRequest:
    build: GameBuildRef
    actions: tuple[ExecutionAction, ...] = ()
    assertions: tuple[AssertionSpec, ...] = ()
    seed: int | None = None


@dataclass(frozen=True)
class ObservationRecord:
    stage: str
    observation: ExecutionObservation
    action_index: int | None = None
    action: ExecutionAction | None = None


@dataclass
class ReproductionResult:
    build: GameBuildRef
    adapter_name: str
    adapter_engine: str
    seed: int | None
    baseline_checkpoint: ExecutionCheckpoint
    observations: list[ObservationRecord] = field(default_factory=list)
    assertions: tuple[AssertionSpec, ...] = ()
    verifications: list[VerificationResult] = field(default_factory=list)
    provenance: EvidenceProvenance = EvidenceProvenance.REPRODUCED

    @property
    def all_assertions_passed(self) -> bool:
        return bool(self.verifications) and all(
            verification.passed for verification in self.verifications
        )

    @property
    def claim_status(self) -> str:
        if not self.verifications:
            return "executed_not_verified"
        return "verified" if self.all_assertions_passed else "not_reproduced"

    def evidence_context(self) -> dict[str, Any]:
        """Return stable provenance metadata suitable for evidence packs/audit logs."""
        action_trace = [
            {
                "index": record.action_index,
                "name": record.action.name,
                "arguments": dict(record.action.arguments),
            }
            for record in self.observations
            if record.action is not None
        ]
        assertion_results = [
            {
                "assertion": spec.assertion,
                "expected": spec.expected,
                "passed": verification.passed,
                "details": dict(verification.details),
            }
            for spec, verification in zip(
                self.assertions,
                self.verifications,
                strict=True,
            )
        ]
        return {
            "provenance": self.provenance.value,
            "claim_status": self.claim_status,
            "adapter": self.adapter_name,
            "engine": self.adapter_engine,
            "build_id": self.build.build_id,
            "build_version": self.build.version,
            "source_revision": self.build.source_revision,
            "seed": self.seed,
            "action_count": len(action_trace),
            "action_trace": action_trace,
            "assertion_count": len(assertion_results),
            "assertions_passed": sum(
                1 for result in assertion_results if result["passed"]
            ),
            "assertion_results": assertion_results,
            "checkpoint_id": self.baseline_checkpoint.checkpoint_id,
        }


class GameReproductionService:
    """Execute a bounded reproduction plan against one explicit external game build.

    `reproduced` provenance means the evidence came from a loaded game runtime. It does
    not by itself mean that the reported issue was reproduced. That stronger claim is
    represented by `claim_status == "verified"`, which requires at least one explicit
    assertion and all assertions to pass.
    """

    async def run(
        self,
        adapter: GameExecutionAdapter,
        request: ReproductionRequest,
    ) -> ReproductionResult:
        if not request.build.build_id.strip():
            raise ValueError("build_id must not be empty")
        if adapter.engine.strip().lower() != request.build.engine.strip().lower():
            raise ValueError(
                f"adapter engine {adapter.engine!r} does not match build engine "
                f"{request.build.engine!r}"
            )

        try:
            await adapter.load_build(request.build)
            baseline = await adapter.reset(seed=request.seed)
            checkpoint = await adapter.checkpoint()

            observations = [
                ObservationRecord(stage="baseline", observation=baseline)
            ]
            for index, action in enumerate(request.actions):
                observation = await adapter.perform_action(action)
                observations.append(
                    ObservationRecord(
                        stage="action",
                        observation=observation,
                        action_index=index,
                        action=action,
                    )
                )

            final_observation = await adapter.observe()
            observations.append(
                ObservationRecord(stage="final", observation=final_observation)
            )

            verifications = [
                await adapter.verify_condition(
                    spec.assertion,
                    expected=spec.expected,
                )
                for spec in request.assertions
            ]

            return ReproductionResult(
                build=request.build,
                adapter_name=adapter.name,
                adapter_engine=adapter.engine,
                seed=request.seed,
                baseline_checkpoint=checkpoint,
                observations=observations,
                assertions=request.assertions,
                verifications=verifications,
            )
        finally:
            await adapter.close()
