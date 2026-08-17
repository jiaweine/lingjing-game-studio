from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:180]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- Store: trust state belongs to the latest delivered result -----------------
replace_once(
    "worldforge/product/store.py",
    '''    def update_conversation(self, conversation_id: str, *, workspace_id: str, title: str | None = None, assigned_to: str | None | object = ..., pinned: bool | None = None, status: str | None = None) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        values: dict[str, Any] = {"updated_at": time.time()}\n''',
    '''    def update_conversation(self, conversation_id: str, *, workspace_id: str, title: str | None = None, assigned_to: str | None | object = ..., pinned: bool | None = None, status: str | None = None) -> dict[str, Any]:\n        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n        if conversation["status"] == "waiting_approval" and status is None:\n            raise ValueError("删除确认处理中，不能修改任务")\n        values: dict[str, Any] = {"updated_at": time.time()}\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''            if status not in {"active", "waiting_approval", "blocked", "verified"}:\n''',
    '''            if status not in {"active", "review", "waiting_approval", "blocked", "verified", "stopped"}:\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''    def archive_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n''',
    '''    def archive_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n        if conversation["status"] == "waiting_approval":\n            raise ValueError("删除确认处理中，不能归档任务")\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''    def restore_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        with self.engine.begin() as connection:\n''',
    '''    def restore_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n        if conversation["status"] == "waiting_approval":\n            raise ValueError("删除确认处理中，不能恢复任务")\n        with self.engine.begin() as connection:\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''    def add_asset(self, conversation_id: str | None, *, name: str, mime: str, path: str, size: int, meta: dict[str, Any], workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID, storage_backend: str = "local") -> dict[str, Any]:\n        if conversation_id:\n            self.get_conversation(conversation_id, workspace_id=workspace_id)\n        asset_id, now = _id("asset"), time.time()\n''',
    '''    def add_asset(self, conversation_id: str | None, *, name: str, mime: str, path: str, size: int, meta: dict[str, Any], workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID, storage_backend: str = "local") -> dict[str, Any]:\n        if conversation_id:\n            conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n            if conversation.get("archived_at") is not None:\n                raise ValueError("已归档任务需要先恢复，才能添加素材")\n            if conversation["status"] == "waiting_approval":\n                raise ValueError("删除确认处理中，不能添加素材")\n        asset_id, now = _id("asset"), time.time()\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''        if conversation.get("archived_at") is not None:\n            raise ValueError("已归档任务需要先恢复，才能继续执行")\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n''',
    '''        if conversation.get("archived_at") is not None:\n            raise ValueError("已归档任务需要先恢复，才能继续执行")\n        if conversation["status"] == "waiting_approval":\n            raise ValueError("删除确认处理中，不能继续执行")\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="verified", updated_at=now))\n''',
    '''            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="review", updated_at=now))\n''',
)

# Delete approval preserves the task's prior status, and approval remains a lock
# until the destructive action is consumed. An approved request is reusable after
# a transient storage failure instead of spawning duplicate approvals.
replace_once(
    "worldforge/product/store.py",
    '''    def create_approval(self, *, workspace_id: str, conversation_id: str, action: str, requested_by: str, reason: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        with self.engine.connect() as connection:\n            existing = connection.execute(select(self.approval_requests).where(and_(self.approval_requests.c.workspace_id == workspace_id, self.approval_requests.c.conversation_id == conversation_id, self.approval_requests.c.action == action, self.approval_requests.c.status == "pending")).order_by(self.approval_requests.c.created_at.desc()).limit(1)).first()\n        if existing:\n            return self._json_row(existing)\n        approval_id, now = _id("approval"), time.time()\n        with self.engine.begin() as connection:\n            connection.execute(insert(self.approval_requests).values(id=approval_id, workspace_id=workspace_id, conversation_id=conversation_id, action=action, status="pending", reason=reason, payload=json.dumps(payload or {}, ensure_ascii=False), requested_by=requested_by, created_at=now))\n            connection.execute(update(self.conversations).where(self.conversations.c.id == conversation_id).values(status="waiting_approval", updated_at=now))\n        return self.get_approval(approval_id, workspace_id=workspace_id)\n''',
    '''    def create_approval(self, *, workspace_id: str, conversation_id: str, action: str, requested_by: str, reason: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:\n        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n        with self.engine.connect() as connection:\n            existing = connection.execute(select(self.approval_requests).where(and_(self.approval_requests.c.workspace_id == workspace_id, self.approval_requests.c.conversation_id == conversation_id, self.approval_requests.c.action == action, self.approval_requests.c.status.in_(("pending", "approved")))).order_by(self.approval_requests.c.created_at.desc()).limit(1)).first()\n        if existing:\n            return self._json_row(existing)\n        approval_id, now = _id("approval"), time.time()\n        approval_payload = dict(payload or {})\n        approval_payload.setdefault("previous_status", conversation["status"])\n        with self.engine.begin() as connection:\n            connection.execute(insert(self.approval_requests).values(id=approval_id, workspace_id=workspace_id, conversation_id=conversation_id, action=action, status="pending", reason=reason, payload=json.dumps(approval_payload, ensure_ascii=False), requested_by=requested_by, created_at=now))\n            connection.execute(update(self.conversations).where(self.conversations.c.id == conversation_id).values(status="waiting_approval", updated_at=now))\n        return self.get_approval(approval_id, workspace_id=workspace_id)\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''            status = "approved" if approved else "rejected"\n            connection.execute(update(self.approval_requests).where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.status == "pending")).values(status=status, resolved_by=user_id, resolved_at=now))\n            connection.execute(update(self.conversations).where(self.conversations.c.id == approval["conversation_id"]).values(status="active", updated_at=now))\n''',
    '''            status = "approved" if approved else "rejected"\n            connection.execute(update(self.approval_requests).where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.status == "pending")).values(status=status, resolved_by=user_id, resolved_at=now))\n            next_status = "waiting_approval" if approved else str((approval.get("payload") or {}).get("previous_status") or "active")\n            connection.execute(update(self.conversations).where(self.conversations.c.id == approval["conversation_id"]).values(status=next_status, updated_at=now))\n''',
)

# Feedback gate is scoped to the latest assistant result. Historical errors do
# not permanently veto a later corrected result; partial verification does not
# unlock evolution.
replace_once(
    "worldforge/product/store.py",
    '''    def feedback_gate(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        rows = self.list_feedback(conversation_id, workspace_id=workspace_id)\n        verified = [row for row in rows if row.get("human_verified")]\n        incorrect = [row for row in rows if row.get("verdict") == "incorrect"]\n        correct = [row for row in rows if row.get("verdict") == "correct"]\n        approved = bool(verified) and not incorrect\n        return {"approved": approved, "human_verified": len(verified), "correct": len(correct), "incorrect": len(incorrect), "feedback_count": len(rows), "reason": "已有人类验证且无错误反馈" if approved else "需要人工验证，且不能存在错误反馈"}\n''',
    '''    def feedback_gate(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        with self.engine.connect() as connection:\n            latest = connection.execute(select(self.messages.c.id).where(and_(self.messages.c.conversation_id == conversation_id, self.messages.c.role == "assistant")).order_by(self.messages.c.created_at.desc(), self.messages.c.id.desc()).limit(1)).first()\n            if not latest:\n                return {"approved": False, "message_id": None, "task_status": "active", "human_verified": 0, "correct": 0, "incorrect": 0, "feedback_count": 0, "reason": "尚无可人工复核的交付结果"}\n            message_id = latest[0]\n            rows = [self._dict(row) for row in connection.execute(select(self.result_feedback).where(and_(self.result_feedback.c.workspace_id == workspace_id, self.result_feedback.c.conversation_id == conversation_id, self.result_feedback.c.message_id == message_id)).order_by(self.result_feedback.c.updated_at.desc())).fetchall()]\n        verified_correct = [row for row in rows if row.get("human_verified") and row.get("verdict") == "correct"]\n        incorrect = [row for row in rows if row.get("verdict") == "incorrect"]\n        correct = [row for row in rows if row.get("verdict") == "correct"]\n        approved = bool(verified_correct) and not incorrect\n        task_status = "verified" if approved else ("blocked" if incorrect else "review")\n        if approved:\n            reason = "最新交付已人工确认正确，且无错误反馈"\n        elif incorrect:\n            reason = "最新交付存在错误反馈，需要修正后重新验证"\n        else:\n            reason = "最新交付需要人工确认正确后才能通过质量门"\n        return {"approved": approved, "message_id": message_id, "task_status": task_status, "human_verified": len(verified_correct), "correct": len(correct), "incorrect": len(incorrect), "feedback_count": len(rows), "reason": reason}\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''        return self.get_feedback(message_id, user_id=user_id, workspace_id=workspace_id) or {}\n\n    def get_feedback''',
    '''        feedback = self.get_feedback(message_id, user_id=user_id, workspace_id=workspace_id) or {}\n        gate = self.feedback_gate(message["conversation_id"], workspace_id=workspace_id)\n        if gate.get("message_id") == message_id:\n            conversation = self.get_conversation(message["conversation_id"], workspace_id=workspace_id)\n            if conversation["status"] != "waiting_approval":\n                with self.engine.begin() as connection:\n                    connection.execute(update(self.conversations).where(and_(self.conversations.c.id == message["conversation_id"], self.conversations.c.workspace_id == workspace_id)).values(status=gate["task_status"], updated_at=time.time()))\n        return feedback\n\n    def get_feedback''',
)

# ---- API: reject writes before they create storage/database side effects --------
replace_once(
    "worldforge/api/app.py",
    '''    if conversation_id:\n        try:\n            product_store.get_conversation(\n                conversation_id, workspace_id=principal.workspace_id\n            )\n        except KeyError:\n            raise HTTPException(404, "任务不存在")\n\n    filename = _safe_filename(file.filename)\n''',
    '''    if conversation_id:\n        try:\n            conversation = product_store.get_conversation(\n                conversation_id, workspace_id=principal.workspace_id\n            )\n        except KeyError:\n            raise HTTPException(404, "任务不存在")\n        if conversation.get("archived_at") is not None:\n            raise HTTPException(409, "已归档任务需要先恢复，才能添加素材")\n        if conversation["status"] == "waiting_approval":\n            raise HTTPException(409, "删除确认处理中，不能添加素材")\n\n    filename = _safe_filename(file.filename)\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''    if conversation.get("archived_at") is not None:\n        raise HTTPException(409, "已归档任务需要先恢复，才能继续执行")\n    latest = product_store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n''',
    '''    if conversation.get("archived_at") is not None:\n        raise HTTPException(409, "已归档任务需要先恢复，才能继续执行")\n    if conversation["status"] == "waiting_approval":\n        raise HTTPException(409, "删除确认处理中，不能继续执行")\n    latest = product_store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n''',
)

# ---- Control API: map lifecycle locks to conflicts, not generic bad requests ----
replace_once(
    "worldforge/product/control.py",
    '''        except ValueError as exc:\n            raise HTTPException(400, str(exc)) from exc\n        action = "task.handoff"''',
    '''        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from exc\n        action = "task.handoff"''',
)
replace_once(
    "worldforge/product/control.py",
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        audit(request, principal, "conversation.restore", "conversation", conversation_id)\n''',
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from exc\n        audit(request, principal, "conversation.restore", "conversation", conversation_id)\n''',
)

# ---- Frontend: system completion is "awaiting review", not "verified" ----------
replace_once(
    "frontend/app.js",
    '''  return {active: "进行中", waiting_approval: "等待确认", blocked: "受阻", verified: "已验证", stopped: "已停止"}[status] || "进行中";\n''',
    '''  return {active: "进行中", review: "待复核", waiting_approval: "等待确认", blocked: "需修正", verified: "已验证", stopped: "已停止"}[status] || "进行中";\n''',
)
replace_once(
    "frontend/app.js",
    '''  if (event.type === "answer.ready") {\n    if (state.conversation?.job) state.conversation.job.status = "completed";\n''',
    '''  if (event.type === "answer.ready") {\n    if (state.conversation?.job) state.conversation.job.status = "completed";\n    if (state.conversation) state.conversation.status = "review";\n''',
)
replace_once(
    "frontend/app.js",
    '''    $("taskState").textContent = "验证完成";\n''',
    '''    $("taskState").textContent = "等待人工复核";\n''',
)
replace_once(
    "frontend/app.js",
    '''    $("taskStateHint").textContent = "结果已整理，证据与下一步都已保留。";\n''',
    '''    $("taskStateHint").textContent = "系统复核已完成；人工确认正确后才会标记为已验证。";\n''',
)

# Pending delete is a real lock. An already-approved request survives reload and
# can retry the destructive action if object storage cleanup previously failed.
replace_once(
    "frontend/app.js",
    '''  const archived = Boolean(state.conversation.archived_at);\n  $("messageInput").disabled = archived || !editable;\n  $("messageInput").placeholder = editable ? "描述你要完成的研发任务…" : "只读成员可以查看任务，但不能修改或执行";\n  $("sendBtn").disabled = state.busy || archived || !editable;\n  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived || !editable; });\n}\n\nfunction pendingDeleteApproval() {\n  return (state.control?.approvals || []).find(row => row.action === "conversation.delete" && row.status === "pending");\n}\n''',
    '''  const archived = Boolean(state.conversation.archived_at);\n  const approvalLocked = state.conversation.status === "waiting_approval";\n  $("renameTaskBtn").hidden = !editable || approvalLocked;\n  $("pinTaskBtn").hidden = !editable || approvalLocked;\n  $("archiveTaskBtn").hidden = !editable || approvalLocked;\n  $("deleteTaskBtn").hidden = !isManager() || approvalLocked;\n  $("messageInput").disabled = archived || approvalLocked || !editable;\n  $("messageInput").placeholder = !editable ? "只读成员可以查看任务，但不能修改或执行" : (approvalLocked ? "删除确认处理中，任务已锁定" : "描述你要完成的研发任务…");\n  $("sendBtn").disabled = state.busy || archived || approvalLocked || !editable;\n  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived || approvalLocked || !editable; });\n}\n\nfunction deleteApproval() {\n  return (state.control?.approvals || []).find(row => row.action === "conversation.delete" && ["pending", "approved"].includes(row.status));\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''  const approval = pendingDeleteApproval();\n  card.hidden = !approval;\n  if (!approval) return;\n  const controls = isManager() ? `\n    <div class="approval-actions">\n      <button type="button" data-approval-reject>取消删除</button>\n      <button class="danger" type="button" data-approval-confirm>确认永久删除</button>\n    </div>` : '<small>等待工作空间管理员确认。</small>';\n  card.innerHTML = `<div><span class="eyebrow">需要确认</span><b>永久删除任务和任务素材</b><p>${esc(approval.reason || "此操作不可恢复。")}</p></div>${controls}`;\n  card.querySelector("[data-approval-reject]")?.addEventListener("click", () => resolveDeleteApproval(approval, false));\n  card.querySelector("[data-approval-confirm]")?.addEventListener("click", () => resolveDeleteApproval(approval, true));\n''',
    '''  const approval = deleteApproval();\n  card.hidden = !approval;\n  if (!approval) return;\n  const approved = approval.status === "approved";\n  const controls = isManager() ? (approved ? `\n    <div class="approval-actions">\n      <button class="danger" type="button" data-approval-delete>重试永久删除</button>\n    </div>` : `\n    <div class="approval-actions">\n      <button type="button" data-approval-reject>取消删除</button>\n      <button class="danger" type="button" data-approval-confirm>确认永久删除</button>\n    </div>`) : '<small>等待工作空间管理员确认。</small>';\n  card.innerHTML = `<div><span class="eyebrow">${approved ? "删除已确认" : "需要确认"}</span><b>永久删除任务和任务素材</b><p>${esc(approved ? "确认已经持久化；如果上次清理存储失败，可以安全重试。" : (approval.reason || "此操作不可恢复。"))}</p></div>${controls}`;\n  card.querySelector("[data-approval-reject]")?.addEventListener("click", () => resolveDeleteApproval(approval, false));\n  card.querySelector("[data-approval-confirm]")?.addEventListener("click", () => resolveDeleteApproval(approval, true));\n  card.querySelector("[data-approval-delete]")?.addEventListener("click", () => executeApprovedDelete(approval));\n''',
)
replace_once(
    "frontend/app.js",
    '''async function resolveDeleteApproval(approval, approved) {\n  try {\n    const resolved = await api(`/api/approvals/${approval.id}/resolve`, {method: "POST", body: JSON.stringify({approved})});\n    if (!approved) {\n      await loadConversationControl();\n      state.conversation.status = "active";\n      renderConversation();\n      toast("已取消删除");\n      return;\n    }\n    await api(`/api/conversations/${state.conversation.id}?approval_id=${encodeURIComponent(resolved.id)}`, {method: "DELETE"});\n    state.ws?.close();\n    state.conversation = null;\n    state.messages = [];\n    state.assets = [];\n    state.control = null;\n    toast("任务及其素材已永久删除");\n    await bootWorkspace();\n  } catch (error) { toast(error.message); }\n}\n''',
    '''async function resolveDeleteApproval(approval, approved) {\n  try {\n    const resolved = await api(`/api/approvals/${approval.id}/resolve`, {method: "POST", body: JSON.stringify({approved})});\n    state.control = state.control || {approvals: []};\n    state.control.approvals = [resolved, ...(state.control.approvals || []).filter(row => row.id !== resolved.id)];\n    if (!approved) {\n      state.conversation.status = resolved.payload?.previous_status || "active";\n      renderConversation();\n      await loadConversations();\n      toast("已取消删除");\n      return;\n    }\n    state.conversation.status = "waiting_approval";\n    renderConversation();\n    await executeApprovedDelete(resolved);\n  } catch (error) { toast(error.message); }\n}\n\nasync function executeApprovedDelete(approval) {\n  try {\n    await api(`/api/conversations/${state.conversation.id}?approval_id=${encodeURIComponent(approval.id)}`, {method: "DELETE"});\n    state.ws?.close();\n    state.conversation = null;\n    state.messages = [];\n    state.assets = [];\n    state.control = null;\n    toast("任务及其素材已永久删除");\n    await bootWorkspace();\n  } catch (error) {\n    state.control = state.control || {approvals: []};\n    state.control.approvals = [approval, ...(state.control.approvals || []).filter(row => row.id !== approval.id)];\n    if (state.conversation) state.conversation.status = "waiting_approval";\n    renderConversation();\n    toast(error.message);\n  }\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''    state.feedback[messageId] = row;\n    state.gate = await api(`/api/quality-gate?conversation_id=${encodeURIComponent(state.conversation.id)}`);\n    renderFeedbackState();\n    renderTeamPanel();\n''',
    '''    state.feedback[messageId] = row;\n    state.gate = await api(`/api/quality-gate?conversation_id=${encodeURIComponent(state.conversation.id)}`);\n    if (state.gate?.message_id === messageId && state.conversation.status !== "waiting_approval") {\n      state.conversation.status = state.gate.task_status || "review";\n    }\n    renderFeedbackState();\n    renderTeamPanel();\n    await loadConversations();\n''',
)
replace_once(
    "frontend/app.js",
    '''  if (!state.conversation) await newConversation(state.scene);\n  for (const file of files) {\n''',
    '''  if (!state.conversation) await newConversation(state.scene);\n  if (state.conversation?.archived_at) { toast("请先恢复已归档任务，再添加素材"); return; }\n  if (state.conversation?.status === "waiting_approval") { toast("删除确认处理中，不能添加素材"); return; }\n  for (const file of files) {\n''',
)
replace_once(
    "frontend/app.js",
    '''  if (state.conversation?.archived_at) { toast("请先恢复已归档任务，再继续执行"); return; }\n\n  setBusy(true);\n''',
    '''  if (state.conversation?.archived_at) { toast("请先恢复已归档任务，再继续执行"); return; }\n  if (state.conversation?.status === "waiting_approval") { toast("删除确认处理中，不能继续执行"); return; }\n\n  setBusy(true);\n''',
)

# Team controls mirror backend owner/admin rules and never offer a read-only
# member as task assignee.
replace_once(
    "frontend/app.js",
    '''function isManager() {\n  return ["owner", "admin"].includes(state.session?.user?.role);\n}\n''',
    '''function isManager() {\n  return ["owner", "admin"].includes(state.session?.user?.role);\n}\n\nfunction isOwner() {\n  return state.session?.user?.role === "owner";\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''  assignee.innerHTML = '<option value="">未指定</option>' + state.members.map(member => `<option value="${esc(member.id)}">${esc(member.name || member.email)}</option>`).join("");\n  assignee.value = state.conversation?.assigned_to || "";\n  assignee.disabled = !state.conversation || !canEdit();\n\n  $("memberList").innerHTML = state.members.length ? state.members.map(member => {\n    const controls = isManager() ? `<select data-member-role="${esc(member.id)}"><option value="owner">所有者</option><option value="admin">管理员</option><option value="member">成员</option><option value="viewer">只读</option></select><button type="button" data-remove-member="${esc(member.id)}">移除</button>` : `<em>${esc(member.role)}</em>`;\n''',
    '''  assignee.innerHTML = '<option value="">未指定</option>' + state.members.filter(member => member.role !== "viewer").map(member => `<option value="${esc(member.id)}">${esc(member.name || member.email)}</option>`).join("");\n  assignee.value = state.conversation?.assigned_to || "";\n  assignee.disabled = !state.conversation || !canEdit() || state.conversation.status === "waiting_approval";\n\n  $("memberList").innerHTML = state.members.length ? state.members.map(member => {\n    const canManageTarget = isManager() && (isOwner() || member.role !== "owner");\n    const roleOptions = `${isOwner() ? '<option value="owner">所有者</option>' : ''}<option value="admin">管理员</option><option value="member">成员</option><option value="viewer">只读</option>`;\n    const controls = canManageTarget ? `<select data-member-role="${esc(member.id)}">${roleOptions}</select>${member.id === state.session?.user?.id ? "" : `<button type="button" data-remove-member="${esc(member.id)}">移除</button>`}` : `<em>${member.role === "owner" ? "所有者" : esc(member.role)}</em>`;\n''',
)

# README matches the actual trust semantics.
replace_once(
    "README.md",
    '''| **可闭环** | 交付结果可以标记正确性、证据价值与人工验证；质量门作为演进的显式否决条件，而不是把点赞直接变成策略 |\n''',
    '''| **可闭环** | 最新交付可以标记正确性、证据价值与人工验证；只有“人工确认正确且无错误反馈”才通过质量门，历史错误不会永久污染后续修正结果 |\n''',
)
replace_once(
    "README.md",
    '''任务本身具备搜索、重命名、置顶、归档/恢复、负责人交接与受审批保护的永久删除。交付会进一步沉淀为复现卡、回归清单、风险清单、调参验证方案或证据包，而不是只停在一段回答。\n''',
    '''任务本身具备搜索、重命名、置顶、归档/恢复、负责人交接与受审批保护的永久删除。系统完成交付后先进入“待复核”，只有最新交付被人工确认正确且不存在错误反馈，任务才进入“已验证”；错误反馈会把任务标记为“需修正”。永久删除审批会锁定任务并记住审批前状态，取消时原样恢复。交付会进一步沉淀为复现卡、回归清单、风险清单、调参验证方案或证据包，而不是只停在一段回答。\n''',
)

# ---- Regression tests ----------------------------------------------------------
test_path = ROOT / "tests/test_product_completion.py"
test_text = test_path.read_text(encoding="utf-8")
append = r'''


def test_quality_gate_only_uses_latest_result_and_requires_verified_correct(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)

    old = store.add_message(conversation["id"], "assistant", "旧结果", {}, workspace_id=workspace_id)
    store.upsert_feedback(workspace_id=workspace_id, user_id=user_id, message_id=old["id"], verdict="incorrect", human_verified=False)
    assert store.feedback_gate(conversation["id"], workspace_id=workspace_id)["approved"] is False

    latest = store.add_message(conversation["id"], "assistant", "修正结果", {}, workspace_id=workspace_id)
    store.upsert_feedback(workspace_id=workspace_id, user_id=user_id, message_id=latest["id"], verdict="partial", human_verified=True)
    partial_gate = store.feedback_gate(conversation["id"], workspace_id=workspace_id)
    assert partial_gate["message_id"] == latest["id"]
    assert partial_gate["approved"] is False
    assert partial_gate["task_status"] == "review"

    store.upsert_feedback(workspace_id=workspace_id, user_id=user_id, message_id=latest["id"], verdict="correct", human_verified=True)
    gate = store.feedback_gate(conversation["id"], workspace_id=workspace_id)
    assert gate["approved"] is True
    assert gate["incorrect"] == 0
    assert gate["task_status"] == "verified"
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "verified"


def test_feedback_error_marks_latest_result_for_correction(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    answer = store.add_message(conversation["id"], "assistant", "结果", {}, workspace_id=workspace_id)
    store.upsert_feedback(workspace_id=workspace_id, user_id=user_id, message_id=answer["id"], verdict="incorrect", human_verified=False)
    gate = store.feedback_gate(conversation["id"], workspace_id=workspace_id)
    assert gate["task_status"] == "blocked"
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "blocked"


def test_delete_approval_restores_prior_status_and_locks_task(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    store.update_conversation(conversation["id"], workspace_id=workspace_id, status="verified")

    approval = store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    assert approval["payload"]["previous_status"] == "verified"
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "waiting_approval"
    with pytest.raises(ValueError, match="删除确认"):
        store.update_conversation(conversation["id"], workspace_id=workspace_id, title="不应修改")
    with pytest.raises(ValueError, match="删除确认"):
        store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "blocked"})
    with pytest.raises(ValueError, match="删除确认"):
        store.add_asset(conversation["id"], name="x.log", mime="text/plain", path="x", size=1, meta={}, workspace_id=workspace_id, created_by=user_id)

    rejected = store.resolve_approval(approval["id"], workspace_id=workspace_id, user_id=user_id, approved=False)
    assert rejected["status"] == "rejected"
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "verified"

    second = store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    approved = store.resolve_approval(second["id"], workspace_id=workspace_id, user_id=user_id, approved=True)
    assert approved["status"] == "approved"
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "waiting_approval"
    same = store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    assert same["id"] == approved["id"]


def test_completed_job_is_review_until_human_verification(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    job = store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "verify"})
    assert store.claim_job("worker", job_id=job["id"])
    message = store.complete_job_answer(job["id"], workspace_id=workspace_id, content="结果", payload={})
    assert message
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "review"
'''
if "test_quality_gate_only_uses_latest_result_and_requires_verified_correct" not in test_text:
    test_path.write_text(test_text + append, encoding="utf-8")

# Browser E2E: verify review -> verified transition and persistent approval lock.
e2e_path = ROOT / "scripts/product_ui_e2e.py"
e2e = e2e_path.read_text(encoding="utf-8")
e2e = e2e.replace("c.status='verified';c.job={id:jobId,status:'completed'};", "c.status='review';c.job={id:jobId,status:'completed'};")
e2e = e2e.replace(
    "const approved=verified.length>0&&incorrect.length===0;\n  return {approved,human_verified:verified.length,correct:rows.filter(x=>x.verdict==='correct').length,incorrect:incorrect.length,feedback_count:rows.length,reason:approved?'已有人类验证且无错误反馈':'需要人工验证，且不能存在错误反馈'};",
    "const verifiedCorrect=rows.filter(x=>x.human_verified&&x.verdict==='correct'),approved=verifiedCorrect.length>0&&incorrect.length===0;\n  return {approved,message_id:'msg-answer',task_status:approved?'verified':(incorrect.length?'blocked':'review'),human_verified:verifiedCorrect.length,correct:rows.filter(x=>x.verdict==='correct').length,incorrect:incorrect.length,feedback_count:rows.length,reason:approved?'最新交付已人工确认正确，且无错误反馈':(incorrect.length?'最新交付存在错误反馈，需要修正后重新验证':'最新交付需要人工确认正确后才能通过质量门')};",
)
e2e = e2e.replace(
    '''    report["checks"]["realtime_result"] = True\n    report["checks"]["evidence_panel"]''',
    '''    report["checks"]["realtime_result"] = True\n    report["checks"]["awaiting_human_review"] = page.locator("#taskState").inner_text() == "等待人工复核"\n    report["checks"]["evidence_panel"]''',
)
e2e = e2e.replace(
    '''    report["checks"]["quality_gate"] = "人工质量门已通过" in page.locator("#qualityGate").inner_text()\n    report["checks"]["product_metrics"]''',
    '''    report["checks"]["quality_gate"] = "人工质量门已通过" in page.locator("#qualityGate").inner_text()\n    report["checks"]["human_verified_task_status"] = page.locator("#conversationList").inner_text().find("已验证") >= 0\n    report["checks"]["product_metrics"]''',
)
e2e = e2e.replace(
    '''    report["checks"]["dangerous_action_approval"] = "永久删除" in page.locator("#approvalCard").inner_text()\n    page.click("[data-approval-reject]")\n''',
    '''    report["checks"]["dangerous_action_approval"] = "永久删除" in page.locator("#approvalCard").inner_text()\n    report["checks"]["approval_locks_task"] = page.locator("#messageInput").is_disabled() and page.locator("#archiveTaskBtn").is_hidden()\n    page.click("[data-approval-reject]")\n''',
)
e2e_path.write_text(e2e, encoding="utf-8")

# Delivery mechanics must not survive the verified product commit.
for relative in [
    "scripts/_final_product_audit.py",
    ".github/workflows/product-final-audit.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
