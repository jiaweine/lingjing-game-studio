from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "product_fullstack_ui_e2e.json"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"temporary API exited early:\n{output}")
        try:
            with urllib.request.urlopen(f"{url}/api/health/live", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("temporary API did not become ready within 30 seconds")


def run() -> dict:
    report = {"checks": {}, "errors": []}
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="lingjing-fullstack-ui-") as temp_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "WORLDFORGE_ENV": "development",
                "WORLDFORGE_AUTH_MODE": "dev",
                "WORLDFORGE_AUTO_CREATE_SCHEMA": "1",
                "WORLDFORGE_QUEUE_MODE": "inprocess",
                "WORLDFORGE_DATA": str(Path(temp_dir) / "runtime"),
                "WORLDFORGE_JOB_LEASE_SECONDS": "20",
                "WORLDFORGE_JOB_HEARTBEAT_SECONDS": "5",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
                "DEEPSEEK_API_KEY": "",
                "DASHSCOPE_API_KEY": "",
                "ARK_API_KEY": "",
                "CUSTOM_BASE_URL": "",
                "LOCAL_OMNI_BASE_URL": "",
            }
        )
        environment.pop("DATABASE_URL", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "worldforge.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until_ready(base_url, process)
            with sync_playwright() as playwright:
                executable = (
                    shutil.which("chromium")
                    or shutil.which("chromium-browser")
                    or shutil.which("google-chrome")
                )
                launch_args = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-gpu"],
                }
                if executable:
                    launch_args["executable_path"] = executable
                browser = playwright.chromium.launch(**launch_args)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: report["errors"].append(str(error)))

                def record_console_error(message) -> None:
                    location = message.location or {}
                    if message.type != "error":
                        return
                    if str(location.get("url", "")).endswith("/favicon.ico"):
                        return
                    report["errors"].append(message.text)

                page.on("console", record_console_error)
                page.goto(base_url, wait_until="networkidle")
                page.wait_for_function(
                    "document.querySelector('#authModal').hidden === true"
                )
                report["checks"]["real_dev_session"] = True

                page.fill(
                    "#messageInput",
                    "检查 NPC 连续对话中的目标切换，并给出可验证步骤。",
                )
                page.click("#sendBtn")
                page.wait_for_selector(
                    "#messageList .msg.assistant .result-trust", timeout=30_000
                )
                trust_text = page.locator(".result-trust").inner_text()
                answer_text = page.locator(
                    "#messageList .msg.assistant .msg-content"
                ).inner_text()
                report["checks"]["durable_job_to_websocket_result"] = (
                    page.locator("#taskState").inner_text() == "等待人工复核"
                )
                report["checks"]["demo_claim_is_explicit"] = (
                    "演示分析" in trust_text
                    and "非真实游戏结论" in trust_text
                    and "演示分析（非真实游戏结论）" in answer_text
                )

                page.once(
                    "dialog",
                    lambda dialog: dialog.accept(
                        "Build 2026.08.21，连续对话 10 轮，核对目标切换日志与画面结果。"
                    ),
                )
                page.click("[data-human-verify]")
                page.wait_for_function(
                    "document.querySelector('[data-human-verify]').classList.contains('active')"
                )
                page.click('[data-panel="team"]')
                page.wait_for_function(
                    "document.querySelector('#qualityGate').textContent.includes('人工质量门已通过')"
                )
                report["checks"]["human_verification_requires_note"] = True

                conversation_id = page.evaluate(
                    "new URL(location.href).searchParams.get('conversation')"
                )
                server_state = page.evaluate(
                    """async conversationId => {
                      const conversation = await fetch(`/api/conversations/${conversationId}`).then(r => r.json());
                      const gate = await fetch(`/api/quality-gate?conversation_id=${encodeURIComponent(conversationId)}`).then(r => r.json());
                      return {conversation, gate};
                    }""",
                    conversation_id,
                )
                job = server_state["conversation"]["job"]
                result_payload = server_state["conversation"]["messages"][-1]["payload"]
                report["checks"]["lease_token_not_exposed"] = (
                    job["status"] == "completed" and "lease_token" not in job
                )
                report["checks"]["server_trust_contract"] = (
                    result_payload["analysis_mode"] == "demo"
                    and result_payload["claim_status"] == "hypothesis_only"
                    and result_payload["verification_status"] == "not_verified"
                )
                report["checks"]["server_quality_gate"] = (
                    server_state["gate"]["approved"] is True
                    and server_state["gate"]["verification_basis"]
                    == "human_attestation"
                )
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    report["ok"] = all(report["checks"].values()) and not report["errors"]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    report = run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
