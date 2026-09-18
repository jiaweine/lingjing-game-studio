import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "unity" / "com.lingjing.game-adapter"


def test_unity_package_manifest_is_installable_editor_package():
    manifest = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "com.lingjing.game-adapter"
    assert manifest["version"] == "0.3.0"
    assert manifest["unity"] == "2021.3"
    assert manifest["samples"] == [
        {
            "displayName": "Boss Shield Bug Repro",
            "description": (
                "Deterministic fixture for validating Lingjing probe observations "
                "and independent Bug/Fix contract evaluation."
            ),
            "path": "Samples~/BossShieldBug",
        }
    ]

    assembly = json.loads(
        (PACKAGE / "Editor" / "Lingjing.GameAdapter.Editor.asmdef").read_text(
            encoding="utf-8"
        )
    )
    assert assembly["includePlatforms"] == ["Editor"]
    assert assembly["allowUnsafeCode"] is False
    assert assembly["references"] == ["Lingjing.GameAdapter"]

    runtime_assembly = json.loads(
        (PACKAGE / "Runtime" / "Lingjing.GameAdapter.asmdef").read_text(
            encoding="utf-8"
        )
    )
    assert runtime_assembly["includePlatforms"] == []
    assert runtime_assembly["allowUnsafeCode"] is False


def test_unity_bridge_remains_loopback_read_only_and_serves_evidence():
    server = (PACKAGE / "Editor" / "LingjingAdapterServer.cs").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:{port}/" in server
    assert 'public bool supports_dry_run = true;' in server
    assert 'public bool supports_screenshots = true;' in server
    assert 'public bool mutating_actions = false;' in server
    assert 'path == "/v1/adapter/capabilities"' in server
    assert 'path == "/v1/adapter/execute"' in server
    assert 'const string evidencePrefix = "/v1/adapter/evidence/";' in server
    assert "LingjingEvidenceCache.Capture(request.evidence_requests)" in server
    assert 'status = "dry-run"' in server
    assert "mutating actions are disabled" in server
    assert 'status = "succeeded"' not in server


def test_unity_evidence_cache_is_bounded_main_thread_memory_only_and_redacts_credentials():
    evidence = (PACKAGE / "Editor" / "LingjingEvidenceCache.cs").read_text(
        encoding="utf-8"
    )

    assert "EditorApplication.update += OnEditorUpdate;" in evidence
    assert "Application.logMessageReceivedThreaded += OnLog;" in evidence
    assert "ConcurrentQueue<CaptureRequest>" in evidence
    assert "MaxEvidenceItems = 12" in evidence
    assert "EvidenceTtl = TimeSpan.FromMinutes(10)" in evidence
    assert "MaxLogEvidenceChars = 64 * 1024" in evidence
    assert "SnapshotRefreshIntervalSeconds = 0.5" in evidence
    assert "if (now >= _nextSnapshotRefreshAt)" in evidence
    assert "request.Completed.Dispose();" in evidence
    assert "AuthorizationValue" in evidence
    assert "(?:[A-Za-z]+\\s+)?" in evidence
    assert '"authorization=[REDACTED]"' in evidence
    assert '"$1=[REDACTED]"' in evidence
    assert "Camera.main" in evidence
    assert "1280.0 / sourceWidth" in evidence
    assert "720.0 / sourceHeight" in evidence
    assert "File.WriteAllBytes" not in evidence
    assert "Application.persistentDataPath" not in evidence


def test_unity_setup_exposes_evidence_preview_and_retrieval_conformance():
    window = (PACKAGE / "Editor" / "LingjingAdapterWindow.cs").read_text(
        encoding="utf-8"
    )
    conformance = (ROOT / "scripts" / "game_adapter_conformance.py").read_text(
        encoding="utf-8"
    )

    assert 'GUILayout.Button("Preview evidence")' in window
    assert '" --execute-dry-run --fetch-evidence"' in window
    assert 'parser.add_argument("--fetch-evidence", action="store_true")' in conformance
    assert 'allowed_prefix = f"{base}/v1/adapter/evidence/"' in conformance
    assert "hashlib.sha256(body).hexdigest()" in conformance
    assert '"same_adapter_origin_required": True' in conformance


def test_unity_package_docs_do_not_claim_project_verification():
    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")

    assert "external-engine-observation-unverified" in readme
    assert "does **not** prove that a game bug has been reproduced" in readme
    assert "mutating_actions=false" in readme
    assert "127.0.0.1" in readme


def test_unity_runtime_probe_reports_observation_without_self_verification():
    probe = (PACKAGE / "Runtime" / "LingjingProbeState.cs").read_text(
        encoding="utf-8"
    )
    evidence = (PACKAGE / "Editor" / "LingjingEvidenceCache.cs").read_text(
        encoding="utf-8"
    )

    assert "public sealed class LingjingProbeState" in probe
    assert "public void SetObservation" in probe
    assert "verified" not in probe.lower()
    assert "MaxProbeEntries = 32" in evidence
    assert "FindObjectsOfType<LingjingProbeState>(true)" in evidence
    assert "probe_id" in evidence
    assert "observed_state" in evidence
    assert "observed_value" in evidence
    assert "note = Redact(" in evidence


def test_unity_package_contains_deterministic_boss_shield_repro_sample():
    fixture = (
        PACKAGE / "Samples~" / "BossShieldBug" / "BossShieldBugFixture.cs"
    ).read_text(encoding="utf-8")
    builder = (
        PACKAGE
        / "Samples~"
        / "BossShieldBug"
        / "Editor"
        / "BossShieldDemoSceneBuilder.cs"
    ).read_text(encoding="utf-8")
    sample_readme = (
        PACKAGE / "Samples~" / "BossShieldBug" / "README.md"
    ).read_text(encoding="utf-8")

    assert 'Configure("demo.boss_shield.damage_gate")' in fixture
    assert '"damage_applied_while_shielded"' in fixture
    assert '"damage_blocked_while_shielded"' in fixture
    assert "attackDamage = 120f" in fixture
    assert "FixedBehavior" in fixture
    assert 'MenuItem("Lingjing/Demo/Create Boss Shield Repro Scene")' in builder
    assert "BossShieldDemo.unity" in builder
    assert "external engine observation" in sample_readme.lower()
