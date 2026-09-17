import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "unity" / "com.lingjing.game-adapter"


def test_unity_package_manifest_is_installable_editor_package():
    manifest = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "com.lingjing.game-adapter"
    assert manifest["version"] == "0.1.0"
    assert manifest["unity"] == "2021.3"

    assembly = json.loads(
        (PACKAGE / "Editor" / "Lingjing.GameAdapter.Editor.asmdef").read_text(
            encoding="utf-8"
        )
    )
    assert assembly["includePlatforms"] == ["Editor"]
    assert assembly["allowUnsafeCode"] is False


def test_unity_activation_bridge_remains_loopback_and_non_mutating():
    server = (PACKAGE / "Editor" / "LingjingAdapterServer.cs").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:{port}/" in server
    assert 'public bool supports_dry_run = true;' in server
    assert 'public bool mutating_actions = false;' in server
    assert 'path == "/v1/adapter/capabilities"' in server
    assert 'path == "/v1/adapter/execute"' in server
    assert 'status = "dry-run"' in server
    assert "Unity activation package is dry-run only" in server
    assert 'status = "succeeded"' not in server


def test_unity_package_docs_do_not_claim_project_verification():
    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")

    assert "external-engine-observation-unverified" in readme
    assert "does **not** prove that a game bug has been reproduced" in readme
    assert "mutating_actions=false" in readme
    assert "127.0.0.1" in readme
