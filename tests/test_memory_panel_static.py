from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_memory_panel_is_loaded_before_main_app():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    memory_tag = '<script type="module" src="/assets/memory_panel.js"></script>'
    app_tag = '<script type="module" src="/assets/app.js"></script>'
    assert memory_tag in html
    assert app_tag in html
    assert html.index(memory_tag) < html.index(app_tag)


def test_memory_panel_reauthorizes_workspace_role_on_every_refresh():
    javascript = (ROOT / "frontend" / "memory_panel.js").read_text(encoding="utf-8")
    assert 'memoryState.session = await memoryApi("/api/auth/me");' in javascript
    assert 'if (!memoryState.session)' not in javascript


def test_memory_panel_javascript_syntax_when_node_is_available():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed in this local test environment")
    subprocess.run(
        [node, "--check", str(ROOT / "frontend" / "memory_panel.js")],
        check=True,
        capture_output=True,
        text=True,
    )
