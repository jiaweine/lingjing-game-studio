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

        assert status["status"] == "completed"
        assert provenance == stored["meta"]["provenance"]
        assert provenance["source_revision"] == "test-source-sha"
        assert provenance["policy"]["generation"] >= 1
        assert provenance["harness"]["genome_id"]
        assert provenance["skill_bank"]["count"] >= 1
        assert len(provenance["combined_fingerprint"]) == 64

        serialized = repr(provenance)
        assert "workspace-secret" not in serialized
        assert "user-secret" not in serialized

    asyncio.run(exercise())
