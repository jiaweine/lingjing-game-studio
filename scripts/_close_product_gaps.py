from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# The browser E2E document is loaded as about:blank. Resolve relative API paths
# against a stable synthetic HTTP origin so the mock can boot before auth.
replace_once(
    "scripts/product_ui_e2e.py",
    "  const U=new URL(raw,location.href),u=U.pathname;\n",
    "  const U=new URL(raw,'http://e2e.local'),u=U.pathname;\n",
)

# Bridge structured product feedback into the runtime evolution gate. A first run
# is read/verify only. Candidate evolution is enabled only on a later run after a
# previous result in the same task has passed the human quality gate.
replace_once(
    "worldforge/product/analyzer.py",
    "    async def run(self, *, text, assets, provider_key, sink, history=None):\n",
    "    async def run(self, *, text, assets, provider_key, sink, history=None, human_feedback_gate=False):\n",
)
replace_once(
    "worldforge/product/analyzer.py",
    '''                    enable_evolution=False,\n                ),\n                demo_delay=0,\n            )\n''',
    '''                    enable_evolution=bool(human_feedback_gate),\n                ),\n                demo_delay=0,\n                session_meta={"human_feedback_gate": bool(human_feedback_gate)},\n            )\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''        prepared = _materialize_assets(assets)\n        result = await product_analyzer.run(\n            text=text,\n            assets=prepared,\n            provider_key=provider_key,\n            sink=sink,\n            history=history,\n        )\n''',
    '''        prepared = _materialize_assets(assets)\n        quality_gate = product_store.feedback_gate(\n            conversation_id, workspace_id=workspace_id\n        )\n        result = await product_analyzer.run(\n            text=text,\n            assets=prepared,\n            provider_key=provider_key,\n            sink=sink,\n            history=history,\n            human_feedback_gate=bool(quality_gate["approved"]),\n        )\n''',
)

# Invitation UX: new invited users register directly into the destination
# workspace; existing users still log in and accept the invite. Never leave a
# consumed/expired invite token in the address bar or ask an invited user to
# invent a workspace name that the backend will ignore.
replace_once(
    "frontend/app.js",
    '''function showAuthModal() {\n  if ($("authModal")) $("authModal").hidden = false;\n}\n''',
    '''function configureInviteAuth() {\n  const invited = Boolean(new URLSearchParams(location.search).get("invite"));\n  const registerTab = document.querySelector('[data-auth-tab="register"]');\n  if (registerTab) registerTab.textContent = invited ? "接受邀请" : "创建空间";\n  const workspaceInput = $("registerWorkspace");\n  const workspaceField = workspaceInput?.closest("label");\n  if (workspaceField) workspaceField.hidden = invited;\n  if (workspaceInput) workspaceInput.required = !invited;\n  const submit = $("registerForm")?.querySelector(".auth-submit");\n  if (submit) submit.textContent = invited ? "注册并加入" : "创建并进入";\n}\n\nfunction clearInviteFromUrl() {\n  const url = new URL(location.href);\n  if (!url.searchParams.has("invite")) return;\n  url.searchParams.delete("invite");\n  history.replaceState(null, "", url);\n}\n\nfunction showAuthModal() {\n  configureInviteAuth();\n  if ($("authModal")) $("authModal").hidden = false;\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''  $("registerForm").onsubmit = async event => {\n    event.preventDefault();\n    try {\n      const session = await api("/api/auth/register", {\n        method: "POST",\n        body: JSON.stringify({\n          name: $("registerName").value.trim(),\n          workspace_name: $("registerWorkspace").value.trim(),\n          invite_token: new URLSearchParams(location.search).get("invite") || null,\n          email: $("registerEmail").value.trim(),\n          password: $("registerPassword").value,\n        }),\n      });\n      applySession(session);\n      hideAuthModal();\n      await maybeAcceptInvite();\n      toast("工作空间已创建");\n      await bootWorkspace();\n    } catch (error) {\n      toast(error.message);\n    }\n  };\n''',
    '''  $("registerForm").onsubmit = async event => {\n    event.preventDefault();\n    const inviteToken = new URLSearchParams(location.search).get("invite") || null;\n    try {\n      const session = await api("/api/auth/register", {\n        method: "POST",\n        body: JSON.stringify({\n          name: $("registerName").value.trim(),\n          workspace_name: $("registerWorkspace").value.trim() || "受邀工作空间",\n          invite_token: inviteToken,\n          email: $("registerEmail").value.trim(),\n          password: $("registerPassword").value,\n        }),\n      });\n      applySession(session);\n      hideAuthModal();\n      if (inviteToken) clearInviteFromUrl();\n      else await maybeAcceptInvite();\n      toast(inviteToken ? "已加入工作空间" : "工作空间已创建");\n      await bootWorkspace();\n    } catch (error) {\n      toast(error.message);\n    }\n  };\n''',
)
replace_once(
    "frontend/app.js",
    '''    applySession(session);\n    const url = new URL(location.href);\n    url.searchParams.delete("invite");\n    history.replaceState(null, "", url);\n    toast("已加入工作空间");\n  } catch (error) {\n    if (!/已失效|已使用/.test(error.message)) toast(error.message);\n  }\n}\n''',
    '''    applySession(session);\n    clearInviteFromUrl();\n    toast("已加入工作空间");\n  } catch (error) {\n    if (/已失效|已使用/.test(error.message)) {\n      clearInviteFromUrl();\n      return;\n    }\n    toast(error.message);\n  }\n}\n''',
)

# README describes the actual closed loop, not an aspirational direct-learning
# claim: verified human feedback unlocks candidate evolution on a later run, and
# regression/KL gates still retain final veto power.
replace_once(
    "README.md",
    "客户对结果的反馈会先进入结构化质量状态，不会直接修改策略；存在错误反馈或缺少人工验证时，Human Feedback Gate 会作为候选演进的否决输入。",
    "客户对结果的反馈会先进入结构化质量状态，不会直接修改策略；存在错误反馈或缺少人工验证时，Human Feedback Gate 会作为候选演进的否决输入。只有已有交付通过人工质量门后，同一任务的后续执行才允许启用候选演进；Regression Gate 与 KL trust region 仍必须同时通过。",
)

# Regression coverage for the actual feedback-to-runtime bridge.
test_path = ROOT / "tests/test_product_completion.py"
test_text = test_path.read_text(encoding="utf-8")
append = r'''


@pytest.mark.asyncio
async def test_product_analyzer_enables_evolution_only_after_human_gate():
    class Summary:
        steps = 4
        outcome = "success"

        def model_dump(self):
            return {"steps": self.steps, "outcome": self.outcome}

    class Engine:
        def __init__(self):
            self.calls = []

        async def run(self, config, **kwargs):
            self.calls.append((config, kwargs))
            return Summary()

    class Providers:
        @staticmethod
        def choose(provider_key, assets):
            return None

    async def sink(event_type, payload):
        return None

    engine = Engine()
    analyzer = ProductAnalyzer(engine, Providers())
    await analyzer.run(
        text="战斗异常需要复核",
        assets=[],
        provider_key="auto",
        sink=sink,
        human_feedback_gate=False,
    )
    config, kwargs = engine.calls[-1]
    assert config.enable_evolution is False
    assert kwargs["session_meta"]["human_feedback_gate"] is False

    await analyzer.run(
        text="战斗异常需要复核",
        assets=[],
        provider_key="auto",
        sink=sink,
        human_feedback_gate=True,
    )
    config, kwargs = engine.calls[-1]
    assert config.enable_evolution is True
    assert kwargs["session_meta"]["human_feedback_gate"] is True
'''
if "test_product_analyzer_enables_evolution_only_after_human_gate" not in test_text:
    test_path.write_text(test_text + append, encoding="utf-8")

# These files are only branch-side delivery mechanics. They must never survive
# into the product commit or main history after squash merge.
for relative in [
    "scripts/_close_product_gaps.py",
    "scripts/_finalize_product_completion.py",
    ".github/workflows/product-final-verification.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
