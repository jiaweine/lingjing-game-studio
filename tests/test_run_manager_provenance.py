import asyncio

from worldforge.api.manager import RunManager
from worldforge.models import RunConfig


def test_managed_run_persists_and_exposes_runtime_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLD_FORGE_SOURCE_REVISION", "test-source-sha")

    async def exercise():
        manager = RunManager(tmp_path / "runtime")
        session_id = await manager.start(
            RunConfig(
                scenario_id="boss_burst",
                seed=29,
                max_steps=1,
                enable_counterfactual=False,
                enable_recursive_agents=False,
                enable_evolution=False,
            ),
            workspace_id="workspace-secret",
            user_id="user-secret",
        )
        await manager.tasks[session_id]

        status = manager.status(session_id)
        stored = manager.engine.events.session_meta(session_id)
        provenance = status["provenance"]
        started = next(
            event
            for event in manager.engine.events.list_events(session_id)
            if event.event_type == "run.started"
        )
        harness_plugin = next(
            plugin
            for plugin in started.payload["plugins"]
            if plugin["name"] == "harness-genome"
        )

        assert status["status"] == "completed"
        assert provenance == stored["meta"]["provenance"]
        assert provenance["schema_version"] == 2
        assert provenance["source_revision"] == "test-source-sha"
        assert provenance["kernel"]["class"] == "worldforge.runtime.engine.WorldForgeEngine"
        assert provenance["runtime_wrapper"]["class"].endswith(
            ".SelfEvolvingWorldForgeEngine"
        )
        assert provenance["kernel"]["fingerprint"] != provenance["runtime_wrapper"]["fingerprint"]
        assert provenance["policy"]["generation"] >= 1
        assert provenance["harness"]["genome_id"]
        assert provenance["harness"]["genome_id"] == harness_plugin["metadata"]["genome_id"]
        assert provenance["harness"]["generation"] == harness_plugin["metadata"]["generation"]
        assert provenance["skill_bank"]["count"] >= 1
        assert len(provenance["combined_fingerprint"]) == 64

        serialized = repr(provenance)
        assert "workspace-secret" not in serialized
        assert "user-secret" not in serialized

    asyncio.run(exercise())
