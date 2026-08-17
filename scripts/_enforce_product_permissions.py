from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Viewer is a real read-only role, not just a label in the team panel.
replace_once(
    "worldforge/api/app.py",
    '''    rate_limiter.check(f"{principal.workspace_id}:{principal.user_id}:{host}")\n    return principal\n\n\n@app.middleware("http")\n''',
    '''    rate_limiter.check(f"{principal.workspace_id}:{principal.user_id}:{host}")\n    return principal\n\n\nasync def require_editor(\n    principal: Principal = Depends(require_principal),\n) -> Principal:\n    if principal.role == "viewer":\n        raise HTTPException(403, "只读成员不能修改任务")\n    return principal\n\n\n@app.middleware("http")\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''def conversation_create(\n    req: ConversationCreate,\n    request: Request,\n    principal: Principal = Depends(require_principal),\n):\n''',
    '''def conversation_create(\n    req: ConversationCreate,\n    request: Request,\n    principal: Principal = Depends(require_editor),\n):\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''async def asset_upload(\n    request: Request,\n    file: UploadFile = File(...),\n    conversation_id: str | None = Form(default=None),\n    principal: Principal = Depends(require_principal),\n):\n''',
    '''async def asset_upload(\n    request: Request,\n    file: UploadFile = File(...),\n    conversation_id: str | None = Form(default=None),\n    principal: Principal = Depends(require_editor),\n):\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''async def conversation_message(\n    conversation_id: str,\n    req: ChatRequest,\n    background_tasks: BackgroundTasks,\n    request: Request,\n    principal: Principal = Depends(require_principal),\n):\n''',
    '''async def conversation_message(\n    conversation_id: str,\n    req: ChatRequest,\n    background_tasks: BackgroundTasks,\n    request: Request,\n    principal: Principal = Depends(require_editor),\n):\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''async def job_cancel(\n    job_id: str,\n    request: Request,\n    principal: Principal = Depends(require_principal),\n):\n''',
    '''async def job_cancel(\n    job_id: str,\n    request: Request,\n    principal: Principal = Depends(require_editor),\n):\n''',
)

# Control router protects every task mutation. Manager-only routes retain their
# stricter checks on top of this baseline.
replace_once(
    "worldforge/product/control.py",
    '''    def require_manager(principal: Principal) -> None:\n        if principal.role not in {"owner", "admin"}:\n            raise HTTPException(403, "只有工作空间管理员可以执行此操作")\n\n    def audit''',
    '''    def require_editor(principal: Principal) -> None:\n        if principal.role == "viewer":\n            raise HTTPException(403, "只读成员不能修改任务")\n\n    def require_manager(principal: Principal) -> None:\n        if principal.role not in {"owner", "admin"}:\n            raise HTTPException(403, "只有工作空间管理员可以执行此操作")\n\n    def audit''',
)
for signature in [
    '''    ):\n        try:\n            row = store.update_conversation(\n''',
    '''    ):\n        try:\n            row = store.archive_conversation(conversation_id, workspace_id=principal.workspace_id)\n''',
    '''    ):\n        try:\n            row = store.restore_conversation(conversation_id, workspace_id=principal.workspace_id)\n''',
    '''    ):\n        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise HTTPException(409, "执行中的任务需要先停止，才能请求永久删除")\n''',
    '''    ):\n        try:\n            job = store.retry_job(job_id, workspace_id=principal.workspace_id)\n''',
    '''    ):\n        try:\n            feedback = store.upsert_feedback(\n''',
]:
    if signature not in (ROOT / "worldforge/product/control.py").read_text(encoding="utf-8"):
        raise RuntimeError(f"control mutation target missing: {signature[:100]!r}")
    replacement = signature.replace("    ):\n", "    ):\n        require_editor(principal)\n", 1)
    replace_once("worldforge/product/control.py", signature, replacement)

# A task owner must be able to execute the task; read-only members cannot be the
# assignee even though they can still inspect it.
replace_once(
    "worldforge/product/store.py",
    '''        if assigned_to is not ...:\n            if assigned_to is not None and not self.get_membership(workspace_id, str(assigned_to)):\n                raise ValueError("负责人必须是当前工作空间成员")\n            values["assigned_to"] = assigned_to\n''',
    '''        if assigned_to is not ...:\n            if assigned_to is not None:\n                membership = self.get_membership(workspace_id, str(assigned_to))\n                if not membership or membership["role"] == "viewer":\n                    raise ValueError("负责人必须是可执行任务的工作空间成员")\n            values["assigned_to"] = assigned_to\n''',
)

# The customer UI mirrors server truth: viewers can inspect tasks, evidence,
# deliverables, team state and quality state, but no write controls are offered.
replace_once(
    "frontend/app.js",
    '''function isManager() {\n  return ["owner", "admin"].includes(state.session?.user?.role);\n}\n''',
    '''function canEdit() {\n  return state.session?.user?.role !== "viewer";\n}\n\nfunction isManager() {\n  return ["owner", "admin"].includes(state.session?.user?.role);\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''function applySession(session) {\n  state.session = session;\n  $("workspaceName").textContent = session?.workspace?.name || "本地演示空间";\n  const email = session?.user?.email || "demo@local";\n  $("userAvatar").textContent = (email.split("@")[0].slice(0, 1) || "游").toUpperCase();\n  $("userAvatar").title = `${email} · ${session?.user?.role || "member"}`;\n}\n''',
    '''function applySession(session) {\n  state.session = session;\n  $("workspaceName").textContent = session?.workspace?.name || "本地演示空间";\n  const email = session?.user?.email || "demo@local";\n  $("userAvatar").textContent = (email.split("@")[0].slice(0, 1) || "游").toUpperCase();\n  $("userAvatar").title = `${email} · ${session?.user?.role || "member"}`;\n  $("newTaskBtn").disabled = !canEdit();\n  $("newTaskBtn").title = canEdit() ? "新建任务" : "只读成员不能新建任务";\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''  const jobStatus = state.conversation.job?.status;\n  $("retryTaskBtn").hidden = !["failed", "cancelled"].includes(jobStatus);\n  $("pinTaskBtn").textContent = state.conversation.pinned ? "取消置顶" : "置顶";\n  $("archiveTaskBtn").textContent = state.conversation.archived_at ? "恢复" : "归档";\n  $("deleteTaskBtn").hidden = !isManager();\n  const archived = Boolean(state.conversation.archived_at);\n  $("messageInput").disabled = archived;\n  $("sendBtn").disabled = state.busy || archived;\n  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived; });\n''',
    '''  const jobStatus = state.conversation.job?.status;\n  const editable = canEdit();\n  $("retryTaskBtn").hidden = !editable || !["failed", "cancelled"].includes(jobStatus);\n  $("renameTaskBtn").hidden = !editable;\n  $("pinTaskBtn").hidden = !editable;\n  $("archiveTaskBtn").hidden = !editable;\n  $("pinTaskBtn").textContent = state.conversation.pinned ? "取消置顶" : "置顶";\n  $("archiveTaskBtn").textContent = state.conversation.archived_at ? "恢复" : "归档";\n  $("deleteTaskBtn").hidden = !isManager();\n  const archived = Boolean(state.conversation.archived_at);\n  $("messageInput").disabled = archived || !editable;\n  $("messageInput").placeholder = editable ? "描述你要完成的研发任务…" : "只读成员可以查看任务，但不能修改或执行";\n  $("sendBtn").disabled = state.busy || archived || !editable;\n  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived || !editable; });\n''',
)
replace_once(
    "frontend/app.js",
    '''  assignee.value = state.conversation?.assigned_to || "";\n  assignee.disabled = !state.conversation;\n''',
    '''  assignee.value = state.conversation?.assigned_to || "";\n  assignee.disabled = !state.conversation || !canEdit();\n''',
)
replace_once(
    "frontend/app.js",
    '''    article.querySelectorAll("[data-feedback]").forEach(button => button.classList.toggle("active", feedback?.verdict === button.dataset.feedback));\n    article.querySelector("[data-evidence-useful]")?.classList.toggle("active", feedback?.evidence_useful === 1 || feedback?.evidence_useful === true);\n    article.querySelector("[data-human-verify]")?.classList.toggle("active", Boolean(feedback?.human_verified));\n''',
    '''    article.querySelectorAll("[data-feedback]").forEach(button => {\n      button.classList.toggle("active", feedback?.verdict === button.dataset.feedback);\n      button.disabled = !canEdit();\n    });\n    const evidenceButton = article.querySelector("[data-evidence-useful]");\n    if (evidenceButton) {\n      evidenceButton.classList.toggle("active", feedback?.evidence_useful === 1 || feedback?.evidence_useful === true);\n      evidenceButton.disabled = !canEdit();\n    }\n    const verifyButton = article.querySelector("[data-human-verify]");\n    if (verifyButton) {\n      verifyButton.classList.toggle("active", Boolean(feedback?.human_verified));\n      verifyButton.disabled = !canEdit();\n    }\n''',
)
replace_once(
    "frontend/app.js",
    '''      newConversation(state.scene);\n    }\n  });\n}\n\nasync function bootWorkspace() {\n''',
    '''      if (canEdit()) newConversation(state.scene);\n    }\n  });\n}\n\nasync function bootWorkspace() {\n''',
)
replace_once(
    "frontend/app.js",
    '''  if (rows.length) {\n    await openConversation(rows[0].id);\n  } else {\n    await newConversation("battle_review");\n  }\n}\n''',
    '''  if (rows.length) {\n    await openConversation(rows[0].id);\n  } else if (canEdit()) {\n    await newConversation("battle_review");\n  } else {\n    state.conversation = null;\n    state.messages = [];\n    state.assets = [];\n    toast("当前工作空间还没有任务");\n  }\n}\n''',
)

# README names the permission contract explicitly.
replace_once(
    "README.md",
    "| **可治理** | 永久删除先进入持久化审批状态；成员角色、任务负责人、邀请与交接都有服务端权限和审计 |",
    "| **可治理** | 永久删除先进入持久化审批状态；成员角色、任务负责人、邀请与交接都有服务端权限和审计；只读成员的写接口在服务端被拒绝 |",
)

# Extend the existing role freshness test to cover viewer task writes, and add a
# store-level assignment guard for read-only members.
test_path = ROOT / "tests/test_product_completion.py"
test_text = test_path.read_text(encoding="utf-8")
replace_once(
    "tests/test_product_completion.py",
    '''    assert admin_client.post(\n        "/api/workspace/invites", json={"role": "member", "email": None}\n    ).status_code == 403\n\n    removed = owner_client.delete(f"/api/workspace/members/{member['id']}")\n''',
    '''    assert admin_client.post(\n        "/api/workspace/invites", json={"role": "member", "email": None}\n    ).status_code == 403\n    assert admin_client.get("/api/conversations").status_code == 200\n    assert admin_client.post(\n        "/api/conversations", json={"title": "viewer cannot write", "scene": "regression"}\n    ).status_code == 403\n    owner_task = owner_client.post(\n        "/api/conversations", json={"title": "owner task", "scene": "regression"}\n    ).json()\n    assert admin_client.post(\n        f"/api/conversations/{owner_task['id']}/messages",\n        json={"content": "viewer should not execute", "asset_ids": [], "provider": "auto"},\n    ).status_code == 403\n\n    removed = owner_client.delete(f"/api/workspace/members/{member['id']}")\n''',
)
append = r'''


def test_read_only_member_cannot_be_task_assignee(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    viewer_workspace = store.create_user_workspace(
        email=_email("viewer-assignee"),
        name="Viewer",
        password_hash="hashed",
        workspace_name="Viewer Home",
    )
    store.add_member_by_email(owner["workspace_id"], viewer_workspace["email"], "viewer")
    conversation = store.create_conversation(
        workspace_id=owner["workspace_id"], created_by=owner["user_id"]
    )
    with pytest.raises(ValueError, match="可执行任务"):
        store.update_conversation(
            conversation["id"],
            workspace_id=owner["workspace_id"],
            assigned_to=viewer_workspace["user_id"],
        )
'''
if "test_read_only_member_cannot_be_task_assignee" not in test_text:
    # Use the text after the replacement above from disk.
    current = test_path.read_text(encoding="utf-8")
    test_path.write_text(current + append, encoding="utf-8")

for relative in [
    "scripts/_enforce_product_permissions.py",
    ".github/workflows/product-permission-verification.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
