import asyncio

from worldforge.api.manager import RunManager, resolve_managed_run_config
from worldforge.models import RunConfig


def test_managed_run_disables_implicit_evolution_without_changing_model_default():
    original = RunConfig()

    assert original.enable_evolution is True
    assert "enable_evolution" not in original.model_fields_set

    effective = resolve_managed_run_config(original)

    assert effective.enable_evolution is False
    assert original.enable_evolution is True


def test_managed_run_honors_explicit_evolution_opt_in_and_opt_out():
    enabled = RunConfig(enable_evolution=True)
    disabled = RunConfig(enable_evolution=False)

    assert resolve_managed_run_config(enabled).enable_evolution is True
    assert resolve_managed_run_config(disabled).enable_evolution is False


def test_managed_run_persists_effective_evolution_setting(tmp_path):
    async def exercise():
        manager = RunManager(tmp_path / "runtime")
        session_id = await manager.start(
            RunConfig(
                scenario_id="boss_burst",
                seed=29,
                max_steps=1,
                enable_counterfactual=False,
                enable_recursive_agents=False,
            )
        )
        await manager.tasks[session_id]

        started = next(
            event
            for event in manager.engine.events.list_events(session_id)
            if event.event_type == "run.started"
        )
        assert started.payload["config"]["enable_evolution"] is False
        assert manager.status(session_id)["provenance"]["config_fingerprint"]

    asyncio.run(exercise())
