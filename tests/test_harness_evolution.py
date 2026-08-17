import random

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import GoalState, WorldState
from worldforge.runtime import (
    AdaptivePlanner,
    CounterfactualBrancher,
    EpisodicMemory,
    EvolutionConfig,
    EvolutionEvidence,
    GameHarnessMutator,
    HarnessEvolutionEngine,
    HarnessGenomeStore,
    SelfEvolvingWorldForgeEngine,
    SkillBank,
    StateVerifier,
    WorldForgeEngine,
)


def _evidence():
    return EvolutionEvidence(
        where="uncertainty",
        why="timeout",
        feature_priorities={
            "uncertainty": 1.0,
            "urgency": 0.8,
            "threat": 0.5,
            "hp_missing": 0.2,
        },
        summary="uncertainty=1.0, urgency=0.8",
        prediction="reduce timeout without safety regression",
    )


def _force_operator(genome, operator):
    for name in genome.mutation_policy.operator_logits:
        genome.mutation_policy.operator_logits[name] = -20.0
    genome.mutation_policy.operator_logits[operator] = 20.0
    genome.mutation_policy.exploration = 0.0


def test_runtime_entrypoint_is_self_evolving():
    assert WorldForgeEngine is SelfEvolvingWorldForgeEngine


def test_specialist_topology_is_data_not_python_roles():
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    state = WorldState()
    goal = GoalState(primary="test")

    no_specialists = baseline.model_copy(deep=True)
    no_specialists.specialists = []
    with HarnessGenomeStore.use(no_specialists):
        planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
        ranked = planner.rank(state, ["attack", "defend"], goal)
        assert ranked.votes == []

    with HarnessGenomeStore.use(baseline):
        planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
        ranked = planner.rank(state, ["attack", "defend"], goal)
        assert ranked.votes
        assert {vote.agent for vote in ranked.votes} <= {
            gene.role for gene in baseline.specialists if gene.mode == "core"
        }


def test_mutation_policy_can_evolve_specialist_topology():
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    _force_operator(baseline, "specialist_split")
    before = len(baseline.specialists)
    child, operator = GameHarnessMutator(random.Random(7)).propose(
        baseline,
        _evidence(),
        direction=1.0,
    )
    assert operator == "specialist_split"
    assert len(child.specialists) == before + 1
    assert child.generation == baseline.generation + 1
    assert child.parent_ids == [baseline.genome_id]


def test_skill_behavior_is_part_of_evolvable_genome():
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    _force_operator(baseline, "skill_mutation")
    before = {
        key: value.model_dump()
        for key, value in baseline.skills.items()
    }
    child, operator = GameHarnessMutator(random.Random(13)).propose(
        baseline,
        _evidence(),
        direction=1.0,
    )
    assert operator == "skill_mutation"
    assert {key: value.model_dump() for key, value in child.skills.items()} != before


def test_memory_retrieval_policy_is_evolvable():
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    _force_operator(baseline, "memory_mutation")
    before = baseline.memory.model_dump()
    child, operator = GameHarnessMutator(random.Random(17)).propose(
        baseline,
        _evidence(),
        direction=1.0,
    )
    assert operator == "memory_mutation"
    assert child.memory.model_dump() != before


def test_mutation_policy_is_self_referential():
    HarnessGenomeStore.configure(None)
    genome = HarnessGenomeStore.current().model_copy(deep=True)
    mutator = GameHarnessMutator(random.Random(11))
    before = genome.mutation_policy.operator_logits["gate_mutation"]
    mutator.reinforce_operator(genome, "gate_mutation", gain=.1)
    assert genome.mutation_policy.operator_logits["gate_mutation"] > before


def test_counterfactual_budget_is_allocated_by_genome():
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    scenario = get_scenario("boss_burst")
    env = BalanceLabEnv()
    state = env.reset(scenario, 7)
    planner = AdaptivePlanner(SkillBank(), EpisodicMemory())
    verifier = StateVerifier()
    brancher = CounterfactualBrancher(planner, verifier)
    ranked = planner.rank(state, env.legal_actions(state), scenario.goal)

    narrow = baseline.model_copy(deep=True)
    narrow.search.width_base = 1.0
    narrow.search.width_uncertainty_gain = 0.0
    narrow.search.width_threat_gain = 0.0
    with HarnessGenomeStore.use(narrow):
        results = brancher.evaluate(
            env,
            ranked.candidates,
            scenario.goal,
            width=4,
            horizon=1,
            rollouts=1,
        )
        assert len(results) == 1

    wider = baseline.model_copy(deep=True)
    wider.search.width_base = 3.0
    wider.search.width_uncertainty_gain = 0.0
    wider.search.width_threat_gain = 0.0
    with HarnessGenomeStore.use(wider):
        results = brancher.evaluate(
            env,
            ranked.candidates,
            scenario.goal,
            width=4,
            horizon=1,
            rollouts=1,
        )
        assert len(results) == min(3, len(ranked.candidates))


def test_shadow_evolution_builds_evaluated_population(tmp_path):
    HarnessGenomeStore.configure(None)
    baseline = HarnessGenomeStore.current().model_copy(deep=True)
    engine = HarnessEvolutionEngine(
        config=EvolutionConfig(
            population=2,
            train_seeds=(11,),
            heldout_seeds=(37,),
            eval_width_cap=1,
            eval_horizon_cap=1,
            eval_rollout_cap=1,
            bootstrap_samples=16,
            seed=19,
        ),
        archive_path=tmp_path / "archive.json",
    )
    result = engine.evolve(_evidence(), baseline=baseline)
    assert len(result.candidates) == 2
    assert all(candidate.train is not None for candidate in result.candidates)
    assert all(candidate.heldout is not None for candidate in result.candidates)
    assert all(
        candidate.genome.generation == baseline.generation + 1
        for candidate in result.candidates
    )
    assert (tmp_path / "archive.json").exists()
