from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    ATTACK = "attack"
    HEAVY_ATTACK = "heavy_attack"
    DEFEND = "defend"
    HEAL = "heal"
    FARM = "farm"
    BUY_BLADE = "buy_blade"
    BUY_ARMOR = "buy_armor"
    SCOUT = "scout"
    CAST = "cast"
    RETREAT = "retreat"


class GameAction(BaseModel):
    kind: ActionKind
    rationale: str = ""
    confidence: float = .5
    source: str = "planner"


class WorldState(BaseModel):
    tick: int = 0
    stage: int = 1
    player_hp: int = 100
    player_max_hp: int = 100
    enemy_hp: int = 110
    enemy_max_hp: int = 110
    energy: int = 3
    max_energy: int = 6
    gold: int = 20
    attack: int = 17
    armor: int = 4
    enemy_attack: int = 16
    enemy_variance: int = 6
    threat: float = .35
    score: float = 0.
    combo: int = 0
    healing_potions: int = 1
    discovered_enemy_attack: int | None = None
    terminal: bool = False
    outcome: str | None = None
    last_action: str | None = None
    tags: list[str] = Field(default_factory=list)


class GoalState(BaseModel):
    primary: str
    min_health_ratio: float = .15
    target_score: float = 80.
    max_steps: int = 18
    risk_tolerance: float = .45


class BeliefState(BaseModel):
    enemy_attack_low: int
    enemy_attack_high: int
    enemy_behavior: str = "unknown"
    exploit_suspected: bool = False
    uncertainty: float = .6
    notes: list[str] = Field(default_factory=list)


class BranchResult(BaseModel):
    branch_id: str
    first_action: ActionKind
    rollout_actions: list[str]
    score: float
    expected_score: float = 0.
    downside_score: float = 0.
    success_probability: float = 0.
    survival: float
    terminal: bool
    outcome: str | None = None
    violations: list[str] = Field(default_factory=list)
    final_state: WorldState


class AgentVote(BaseModel):
    agent: str
    action: ActionKind
    score: float
    reason: str


class DecisionFrame(BaseModel):
    tick: int
    candidates: list[ActionKind]
    votes: list[AgentVote]
    branch_results: list[BranchResult] = Field(default_factory=list)
    selected: ActionKind
    rationale: str
    confidence: float


class Skill(BaseModel):
    skill_id: str
    name: str
    generation: int = 1
    description: str
    trigger: str
    action_bias: dict[str, float] = Field(default_factory=dict)
    evidence_count: int = 0
    success_rate: float = .5
    status: Literal["active", "candidate", "retired"] = "active"
    parent_generation: int | None = None


class RuntimeEvent(BaseModel):
    session_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    ts: float
    hash: str
    prev_hash: str


class RunConfig(BaseModel):
    scenario_id: str = "boss_burst"
    seed: int = 7
    max_steps: int = 18
    branch_width: int = Field(default=4, ge=1, le=8)
    rollout_horizon: int = Field(default=3, ge=1, le=6)
    rollouts_per_branch: int = Field(default=3, ge=1, le=8)
    enable_counterfactual: bool = True
    enable_recursive_agents: bool = True
    enable_evolution: bool = True


class RunSummary(BaseModel):
    session_id: str
    scenario_id: str
    status: str
    outcome: str | None = None
    steps: int = 0
    score: float = 0.
    invalid_actions: int = 0
    recovery_events: int = 0
    branches_evaluated: int = 0
    skills_used: int = 0
    evolved: bool = False
    started_at: float
    finished_at: float | None = None


class ScenarioSpec(BaseModel):
    scenario_id: str
    name: str
    description: str
    difficulty: str
    state: WorldState
    goal: GoalState
    hidden: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRequest(BaseModel):
    seeds: int = Field(default=24, ge=4, le=200)
    scenarios: list[str] | None = None


class BenchmarkRow(BaseModel):
    harness: str
    success_rate: float
    avg_score: float
    avg_steps: float
    invalid_action_rate: float
    recovery_rate: float
    avg_decision_ops: float


class EvolutionPatch(BaseModel):
    patch_id: str
    reason: str
    target_skill_id: str
    before: Skill
    after: Skill
    regression_before: float
    regression_after: float
    accepted: bool
