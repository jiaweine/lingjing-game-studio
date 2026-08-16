from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MissionStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalMode(str, Enum):
    REVIEW = "review"
    SAFE_AUTO = "safe_auto"
    FULL_AUTO = "full_auto"


class ExecutionBudget(BaseModel):
    max_agents: int = Field(default=5, ge=1, le=12)
    max_tool_calls: int = Field(default=12, ge=1, le=64)
    max_parallelism: int = Field(default=3, ge=1, le=8)
    max_runtime_seconds: float = Field(default=90.0, ge=5.0, le=1800.0)
    branch_width: int = Field(default=3, ge=1, le=8)
    rollout_horizon: int = Field(default=3, ge=1, le=6)
    rollouts_per_branch: int = Field(default=2, ge=1, le=8)


class MissionSpec(BaseModel):
    goal: str = Field(min_length=2, max_length=12000)
    scenario_id: str | None = None
    scene: str = Field(default="general", max_length=80)
    seed: int = 29
    approval_mode: ApprovalMode = ApprovalMode.SAFE_AUTO
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannedStep(BaseModel):
    step_id: str
    role: str
    objective: str
    tool: Literal["game.inspect", "game.simulate", "game.verify"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None


class MissionPlan(BaseModel):
    risk: Literal["low", "medium", "high", "critical"]
    rationale: list[str] = Field(default_factory=list)
    steps: list[PlannedStep] = Field(default_factory=list)


class ToolEvidence(BaseModel):
    call_id: str
    step_id: str
    tool: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    reused: bool = False
    elapsed_ms: float = 0.0


class MissionResult(BaseModel):
    mission_id: str
    status: MissionStatus
    goal: str
    plan: MissionPlan
    evidence: list[ToolEvidence] = Field(default_factory=list)
    child_sessions: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
