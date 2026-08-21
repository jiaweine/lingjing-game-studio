import subprocess
import sys


def _run(code: str):
    return subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )


def test_importing_api_manager_does_not_eagerly_initialize_fastapi_app():
    result = _run(
        "import sys; import worldforge.api.manager; "
        "assert 'worldforge.api.app' not in sys.modules; print('lazy-ok')"
    )
    assert "lazy-ok" in result.stdout


def test_package_app_export_remains_compatible():
    result = _run(
        "from fastapi import FastAPI; from worldforge.api import app; "
        "assert isinstance(app, FastAPI); print(app.title)"
    )
    assert "灵境游戏研发执行工作台 API" in result.stdout
