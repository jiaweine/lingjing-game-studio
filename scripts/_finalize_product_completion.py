from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "worldforge/product/store.py",
    'connection.execute(update(self.conversations).where(self.conversations.c.id == row[0]).values(status="active", updated_at=now))',
    'connection.execute(update(self.conversations).where(self.conversations.c.id == row[0]).values(status="stopped", updated_at=now))',
)
replace_once(
    "frontend/app.js",
    '''  document.querySelector(".task-state-card").className = "task-state-card cancelled";\n}\n''',
    '''  document.querySelector(".task-state-card").className = "task-state-card cancelled";\n  renderTaskActions();\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''    document.querySelector(".task-state-card").className = "task-state-card error";\n    toast(event.payload?.message || "执行失败");\n''',
    '''    document.querySelector(".task-state-card").className = "task-state-card error";\n    renderTaskActions();\n    toast(event.payload?.message || "执行失败");\n''',
)
replace_once(
    "frontend/app.js",
    '''  return {active: "进行中", waiting_approval: "等待确认", blocked: "受阻", verified: "已验证"}[status] || "进行中";\n''',
    '''  return {active: "进行中", waiting_approval: "等待确认", blocked: "受阻", verified: "已验证", stopped: "已停止"}[status] || "进行中";\n''',
)
replace_once(
    "tests/test_product_completion.py",
    '''    store.cancel_job(first["id"], workspace_id=workspace_id)\n\n    retry = store.retry_job(first["id"], workspace_id=workspace_id)\n''',
    '''    store.cancel_job(first["id"], workspace_id=workspace_id)\n    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "stopped"\n\n    retry = store.retry_job(first["id"], workspace_id=workspace_id)\n''',
)

tests = ROOT / "tests/test_product_completion.py"
text = tests.read_text(encoding="utf-8")
append = r'''


def test_role_demotion_and_removal_take_effect_without_relogin():
    owner_client = TestClient(app)
    owner_registration = owner_client.post(
        "/api/auth/register",
        json={
            "email": _email("fresh-owner"),
            "password": "strong-password-owner",
            "name": "Fresh Owner",
            "workspace_name": f"Fresh Studio {uuid.uuid4().hex[:6]}",
        },
    )
    assert owner_registration.status_code == 200

    invite = owner_client.post(
        "/api/workspace/invites",
        json={"role": "admin", "email": None},
    ).json()
    admin_client = TestClient(app)
    admin_email = _email("fresh-admin")
    admin_registration = admin_client.post(
        "/api/auth/register",
        json={
            "email": admin_email,
            "password": "strong-password-admin",
            "name": "Fresh Admin",
            "workspace_name": "unused",
            "invite_token": invite["token"],
        },
    )
    assert admin_registration.status_code == 200
    assert admin_client.post(
        "/api/workspace/invites", json={"role": "member", "email": None}
    ).status_code == 200

    member = next(
        row for row in owner_client.get("/api/workspace/members").json()
        if row["email"] == admin_email
    )
    demoted = owner_client.patch(
        f"/api/workspace/members/{member['id']}", json={"role": "viewer"}
    )
    assert demoted.status_code == 200
    # Same cookie/JWT: require_principal must refresh current membership.
    assert admin_client.post(
        "/api/workspace/invites", json={"role": "member", "email": None}
    ).status_code == 403

    removed = owner_client.delete(f"/api/workspace/members/{member['id']}")
    assert removed.status_code == 200
    # Removal invalidates the old workspace session immediately.
    assert admin_client.get("/api/conversations").status_code == 401
'''
if "test_role_demotion_and_removal_take_effect_without_relogin" not in text:
    tests.write_text(text + append, encoding="utf-8")

for relative in [
    "scripts/_finalize_product_completion.py",
    ".github/workflows/product-final-verification.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
