from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# API wiring: searchable lifecycle list, reusable job scheduling, control router.
replace_once(
    "worldforge/api/app.py",
    "from worldforge.product import (\n    ConversationStore, ProductAnalyzer, extract_video_frames, probe_media,\n)\n",
    "from worldforge.product import (\n    ConversationStore, ProductAnalyzer, extract_video_frames, probe_media,\n)\nfrom worldforge.product.control import build_control_router\n",
)
replace_once(
    "worldforge/api/app.py",
    '''@app.get("/api/conversations")\ndef conversation_list(\n    limit: int = Query(default=30, ge=1, le=100),\n    principal: Principal = Depends(require_principal),\n):\n    return product_store.list_conversations(\n        limit, workspace_id=principal.workspace_id\n    )\n''',
    '''@app.get("/api/conversations")\ndef conversation_list(\n    limit: int = Query(default=30, ge=1, le=100),\n    q: str | None = Query(default=None, max_length=120),\n    archived: bool = Query(default=False),\n    principal: Principal = Depends(require_principal),\n):\n    return product_store.list_conversations(\n        limit,\n        workspace_id=principal.workspace_id,\n        query=q,\n        archived=archived,\n    )\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''    except Exception as exc:\n        logger.exception(\n            "analysis job failed",\n            extra={"conversation_id": conversation_id},\n        )\n        await _product_emit(\n            conversation_id,\n            workspace_id,\n            "answer.error",\n            {"message": "处理过程中出现问题", "detail": repr(exc)},\n        )\n        raise\n\n\n@app.post("/api/conversations/{conversation_id}/messages")\n''',
    '''    except Exception as exc:\n        if job_id:\n            try:\n                if product_store.get_job(job_id, workspace_id=workspace_id)["status"] == "cancelled":\n                    return False\n            except KeyError:\n                return False\n        logger.exception(\n            "analysis job failed",\n            extra={"conversation_id": conversation_id},\n        )\n        await _product_emit(\n            conversation_id,\n            workspace_id,\n            "answer.error",\n            {"message": "处理过程中出现问题", "detail": repr(exc), "job_id": job_id},\n        )\n        raise\n\n\nasync def _schedule_product_job(job, background_tasks: BackgroundTasks, principal: Principal):\n    if settings.queue_mode == "external":\n        return\n\n    async def work():\n        claimed = product_store.claim_job("api-inprocess", job_id=job["id"])\n        if not claimed:\n            return\n        payload = claimed["payload"]\n        assets = []\n        for asset_id in payload.get("asset_ids", []):\n            try:\n                assets.append(product_store.get_asset(asset_id, workspace_id=claimed["workspace_id"]))\n            except KeyError:\n                continue\n        try:\n            await _run_analysis_job(\n                conversation_id=claimed["conversation_id"],\n                workspace_id=claimed["workspace_id"],\n                text=str(payload.get("text", "")),\n                provider_key=str(payload.get("provider", "auto")),\n                history=list(payload.get("history", [])),\n                assets=assets,\n                job_id=claimed["id"],\n            )\n        except Exception as exc:\n            product_store.fail_job(claimed["id"], repr(exc), max_attempts=1)\n\n    background_tasks.add_task(work)\n\n\n@app.post("/api/conversations/{conversation_id}/messages")\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''    async def work():\n        try:\n            with product_store.engine.begin() as connection:\n                claimed = connection.execute(\n                    product_store.jobs.update()\n                    .where(\n                        (product_store.jobs.c.id == job["id"])\n                        & (product_store.jobs.c.status == "queued")\n                    )\n                    .values(\n                        status="running",\n                        worker_id="api-inprocess",\n                        claimed_at=time.time(),\n                        attempts=1,\n                    )\n                )\n            if claimed.rowcount == 0:\n                return\n            await _run_analysis_job(\n                conversation_id=conversation_id,\n                workspace_id=principal.workspace_id,\n                text=req.content,\n                provider_key=req.provider,\n                history=history,\n                assets=assets,\n                job_id=job["id"],\n            )\n        except Exception as exc:\n            product_store.fail_job(job["id"], repr(exc), max_attempts=1)\n\n    background_tasks.add_task(work)\n''',
    '''    await _schedule_product_job(job, background_tasks, principal)\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''    return cancelled\n\n\n@app.websocket("/ws/conversations/{conversation_id}")\n''',
    '''    return cancelled\n\n\napp.include_router(\n    build_control_router(\n        store=product_store,\n        storage=storage,\n        require_principal=require_principal,\n        session_response=_session_response,\n        schedule_retry=_schedule_product_job,\n    )\n)\n\n\n@app.websocket("/ws/conversations/{conversation_id}")\n''',
)

# Structured, scene-specific deliverables; provider routing stays internal.
replace_once("worldforge/product/analyzer.py", '        route_label = "自动路由"\n', "")
replace_once(
    "worldforge/product/analyzer.py",
    '''            "intent": intent,\n            "provider": route_label,\n            "evidence": evidence,\n''',
    '''            "intent": intent,\n            "evidence": evidence,\n            "deliverables": self._deliverables(intent, evidence, runtime_result),\n''',
)
replace_once(
    "worldforge/product/analyzer.py",
    '''    def _progress_detail(self, intent):\n''',
    '''    def _deliverables(self, intent, evidence, runtime_result):\n        evidence_ids = [item.get("id") for item in evidence if item.get("id")]\n        evidence_pack = {\n            "type": "evidence_pack",\n            "title": "证据包",\n            "summary": "保留本次判断使用的素材索引与主动复核结果，方便团队复查。",\n            "items": [item.get("title", "") for item in evidence if item.get("title")],\n            "evidence_ids": evidence_ids,\n        }\n        if intent == "battle_review":\n            return [\n                {\n                    "type": "reproduction_card",\n                    "title": "问题复现卡",\n                    "summary": "把当前异常窗口固化成可交接的复现入口。",\n                    "items": ["使用当前素材作为复现基线", "锁定异常阶段与资源窗口", "修复后按同条件再次执行"],\n                    "evidence_ids": evidence_ids,\n                },\n                {\n                    "type": "regression_checklist",\n                    "title": "回归检查清单",\n                    "summary": "覆盖触发条件、邻近条件与修复后复核。",\n                    "items": ["原触发条件不再复现", "相邻时间窗无新增异常", "资源与伤害变化符合预期"],\n                    "evidence_ids": evidence_ids,\n                },\n                evidence_pack,\n            ]\n        if intent == "balance":\n            return [\n                {\n                    "type": "risk_register",\n                    "title": "数值风险清单",\n                    "summary": "把高波动组合与需要继续验证的边界分开记录。",\n                    "items": ["优先复核极端收益组合", "检查资源曲线断点", "覆盖不同玩家策略"],\n                    "evidence_ids": evidence_ids,\n                },\n                {\n                    "type": "tuning_plan",\n                    "title": "调参验证方案",\n                    "summary": "每次调整都保留前后对照和回归条件。",\n                    "items": ["一次只改变一个主要变量", "保留调整前基线", "重新检查极端与常规打法"],\n                    "evidence_ids": evidence_ids,\n                },\n                evidence_pack,\n            ]\n        if intent == "regression":\n            return [\n                {\n                    "type": "reproduction_card",\n                    "title": "回归复现卡",\n                    "summary": "把历史异常、当前复现条件和验证基线放在同一交付物。",\n                    "items": ["复用当前异常条件", "核对版本差异", "修复后重复同条件验证"],\n                    "evidence_ids": evidence_ids,\n                },\n                {\n                    "type": "release_checklist",\n                    "title": "发布前检查项",\n                    "summary": "只有关键路径重新通过后才适合关闭问题。",\n                    "items": ["原问题不可复现", "关键邻接路径通过", "证据与结果已人工复核"],\n                    "evidence_ids": evidence_ids,\n                },\n                evidence_pack,\n            ]\n        if intent == "npc":\n            return [\n                {\n                    "type": "behavior_checklist",\n                    "title": "角色行为检查表",\n                    "summary": "将目标切换、连续交互和上下文一致性变成可重复检查项。",\n                    "items": ["连续交互保持上下文", "冲突指令下行为可解释", "目标切换没有异常跳变"],\n                    "evidence_ids": evidence_ids,\n                },\n                evidence_pack,\n            ]\n        return [\n            {\n                "type": "action_brief",\n                "title": "研发行动摘要",\n                "summary": "把当前结论转成可继续执行和复核的团队任务。",\n                "items": ["保留当前基线", "补齐最高不确定性证据", "完成后再次核验结论"],\n                "evidence_ids": evidence_ids,\n            },\n            evidence_pack,\n        ]\n\n    def _progress_detail(self, intent):\n''',
)

# Human feedback can veto evolution; regression/KL gates still remain mandatory.
(ROOT / "worldforge/runtime/evolver.py").write_text('''from __future__ import annotations\n\nimport uuid\nfrom dataclasses import dataclass\n\nfrom worldforge.models import EvolutionPatch\n\n\n@dataclass\nclass FailureSignal:\n    category: str\n    skill_id: str\n    action: str\n    delta: float\n    reason: str\n\n\nclass FailureDrivenEvolver:\n    def __init__(self, skill_bank):\n        self.skill_bank = skill_bank\n\n    def attribute(self, *, outcome, min_hp, farm_count, invalid_actions, last_action):\n        if invalid_actions > 0:\n            return FailureSignal("execution", "survival_guard", "defend", .35, "invalid action occurred under risk")\n        if outcome == "defeat" and min_hp < 30:\n            return FailureSignal("survival", "survival_guard", "defend", .55, "terminal failure followed a high-risk state")\n        if farm_count >= 4:\n            return FailureSignal("economy", "economy_guard", "farm", -.45, "repetitive farming created low-value loop")\n        if outcome != "victory" and last_action:\n            return FailureSignal("progress", "burst_window", "heavy_attack", .25, "run timed out without enough progress")\n        return None\n\n    def evolve(self, signal, regression_eval, *, human_approved: bool = True):\n        before = self.skill_bank.skills[signal.skill_id].model_copy(deep=True)\n        candidate = self.skill_bank.propose_patch(signal.skill_id, signal.action, signal.delta, signal.reason)\n        baseline = regression_eval(None)\n        score = regression_eval(candidate)\n        accepted = human_approved and score >= baseline - .01 and score >= baseline + .005\n        if accepted:\n            self.skill_bank.accept(candidate)\n        reason = signal.reason if human_approved else f"{signal.reason}; blocked by human feedback gate"\n        return EvolutionPatch(\n            patch_id=f"patch-{uuid.uuid4().hex[:8]}",\n            reason=reason,\n            target_skill_id=signal.skill_id,\n            before=before,\n            after=candidate,\n            regression_before=round(baseline, 4),\n            regression_after=round(score, 4),\n            accepted=accepted,\n        )\n''', encoding="utf-8")
replace_once(
    "worldforge/runtime/engine.py",
    '''        evolved = False\n        # Policy / Skill / global Memory are shared learning state. Commit those\n''',
    '''        evolved = False\n        human_feedback_gate = bool((session_meta or {}).get("human_feedback_gate", True))\n        # Policy / Skill / global Memory are shared learning state. Commit those\n''',
)
replace_once(
    "worldforge/runtime/engine.py",
    '''                    patch = self.evolver.evolve(\n                        signal,\n                        lambda candidate: self._regression_eval(\n                            config.scenario_id, candidate, self.policy_model\n                        ),\n                    )\n''',
    '''                    patch = self.evolver.evolve(\n                        signal,\n                        lambda candidate: self._regression_eval(\n                            config.scenario_id, candidate, self.policy_model\n                        ),\n                        human_approved=human_feedback_gate,\n                    )\n''',
)
replace_once(
    "worldforge/runtime/engine.py",
    '''                        optimization["updates"] > 0\n                        and candidate_score >= baseline + .001\n                        and optimization["mean_kl"] <= self.policy_optimizer.kl_limit\n''',
    '''                        human_feedback_gate\n                        and optimization["updates"] > 0\n                        and candidate_score >= baseline + .001\n                        and optimization["mean_kl"] <= self.policy_optimizer.kl_limit\n''',
)
replace_once(
    "worldforge/runtime/engine.py",
    '''                        "accepted": policy_accepted,\n                        "generation": (\n''',
    '''                        "accepted": policy_accepted,\n                        "human_feedback_gate": human_feedback_gate,\n                        "generation": (\n''',
)

# Customer UI: task lifecycle, approval, collaboration, structured deliverables.
replace_once(
    "frontend/index.html",
    '''    <div class="workspace-switch workspace-static" aria-label="当前工作空间">\n      <span class="workspace-beacon"></span>\n      <span class="workspace-copy">\n        <small>工作空间</small>\n        <b id="workspaceName">本地演示空间</b>\n      </span>\n    </div>\n''',
    '''    <button class="workspace-switch" id="workspaceSwitchBtn" type="button" aria-label="切换工作空间" aria-expanded="false">\n      <span class="workspace-beacon"></span>\n      <span class="workspace-copy">\n        <small>工作空间</small>\n        <b id="workspaceName">本地演示空间</b>\n      </span>\n      <span class="workspace-caret">⌄</span>\n    </button>\n    <div class="workspace-menu" id="workspaceMenu" hidden></div>\n''',
)
replace_once(
    "frontend/index.html",
    '''      <div class="side-title row">\n        <span>最近任务</span>\n        <button class="icon-button" id="refreshConvBtn" type="button" title="刷新">↻</button>\n      </div>\n      <div id="conversationList" class="conversation-list"></div>\n''',
    '''      <div class="side-title row">\n        <span>任务</span>\n        <button class="icon-button" id="refreshConvBtn" type="button" title="刷新">↻</button>\n      </div>\n      <div class="task-index-tools">\n        <input id="taskSearch" type="search" placeholder="搜索任务" aria-label="搜索任务" />\n        <div class="task-scope">\n          <button class="active" data-task-scope="active" type="button">进行中</button>\n          <button data-task-scope="archived" type="button">已归档</button>\n        </div>\n      </div>\n      <div id="conversationList" class="conversation-list"></div>\n''',
)
replace_once(
    "frontend/index.html",
    '''      <div class="head-actions">\n        <button class="ghost-button" id="copyLinkBtn" type="button">复制链接</button>\n      </div>\n    </div>\n''',
    '''      <div class="head-actions">\n        <button class="ghost-button" id="retryTaskBtn" type="button" hidden>重新执行</button>\n        <button class="ghost-button" id="renameTaskBtn" type="button">重命名</button>\n        <button class="ghost-button" id="pinTaskBtn" type="button">置顶</button>\n        <button class="ghost-button" id="archiveTaskBtn" type="button">归档</button>\n        <button class="ghost-button danger-text" id="deleteTaskBtn" type="button">删除</button>\n        <button class="ghost-button" id="copyLinkBtn" type="button">复制链接</button>\n      </div>\n    </div>\n\n    <div class="approval-card" id="approvalCard" hidden></div>\n''',
)
replace_once(
    "frontend/index.html",
    '''      <button class="right-tab" data-panel="evidence" type="button">证据</button>\n      <button class="right-tab" data-panel="assets" type="button">素材</button>\n''',
    '''      <button class="right-tab" data-panel="evidence" type="button">证据</button>\n      <button class="right-tab" data-panel="deliverables" type="button">交付</button>\n      <button class="right-tab" data-panel="assets" type="button">素材</button>\n      <button class="right-tab" data-panel="team" type="button">团队</button>\n''',
)
replace_once(
    "frontend/index.html",
    '''      <section class="right-panel" id="panel-assets">\n''',
    '''      <section class="right-panel" id="panel-deliverables">\n        <div class="panel-intro">\n          <span class="eyebrow">研发交付</span>\n          <b>可直接交接的结果</b>\n          <p>复现卡、回归清单、风险项和证据包会随任务结果一起保留。</p>\n        </div>\n        <div id="deliverableList" class="deliverable-list"><div class="empty-side">完成一次任务后显示。</div></div>\n      </section>\n\n      <section class="right-panel" id="panel-assets">\n''',
)
replace_once(
    "frontend/index.html",
    '''      </section>\n    </div>\n  </aside>\n</div>\n''',
    '''      </section>\n\n      <section class="right-panel" id="panel-team">\n        <div class="panel-intro">\n          <span class="eyebrow">协作与质量</span>\n          <b>团队控制台</b>\n          <p>任务负责人、成员权限、邀请链接和结果质量门都在工作空间内管理。</p>\n        </div>\n        <label class="assignee-field"><span>任务负责人</span><select id="assigneeSelect"></select></label>\n        <div id="memberList" class="member-list"></div>\n        <form id="inviteForm" class="invite-form">\n          <input id="inviteEmail" type="email" placeholder="成员邮箱（可选）" />\n          <select id="inviteRole"><option value="member">成员</option><option value="viewer">只读</option><option value="admin">管理员</option></select>\n          <button type="submit">创建邀请链接</button>\n        </form>\n        <div id="inviteList" class="invite-list"></div>\n        <div class="panel-label">产品结果</div>\n        <div id="qualityGate" class="quality-gate"></div>\n        <div id="metricGrid" class="metric-grid"></div>\n      </section>\n    </div>\n  </aside>\n</div>\n''',
)

# App state and task index.
replace_once(
    "frontend/app.js",
    '''  session: null,\n  config: null,\n};\n''',
    '''  session: null,\n  config: null,\n  scope: "active",\n  members: [],\n  workspaces: [],\n  control: null,\n  feedback: {},\n  metrics: null,\n  gate: null,\n};\n''',
)
replace_once(
    "frontend/app.js",
    '''async function loadConversations() {\n  const rows = await api("/api/conversations");\n''',
    '''async function loadConversations() {\n  const query = $("taskSearch")?.value.trim() || "";\n  const archived = state.scope === "archived";\n  const rows = await api(`/api/conversations?limit=100&archived=${archived}&q=${encodeURIComponent(query)}`);\n  if (query) trackProductEvent("task.search", null, {query_length: query.length});\n''',
)
replace_once(
    "frontend/app.js",
    '''            <b>${esc(conversation.title)}</b>\n            <small>${esc(SCENE_NAME[conversation.scene] || conversation.scene)}</small>\n''',
    '''            <b>${conversation.pinned ? "⌁ " : ""}${esc(conversation.title)}</b>\n            <small>${esc(SCENE_NAME[conversation.scene] || conversation.scene)} · ${taskStatusLabel(conversation.status)}</small>\n''',
)
replace_once(
    "frontend/app.js",
    '''  renderConversation();\n  connectConversation();\n  await loadConversations();\n}\n\nfunction renderConversation() {\n''',
    '''  await loadConversationControl();\n  await loadTeamPanel();\n  renderConversation();\n  connectConversation();\n  await loadConversations();\n}\n\nfunction renderConversation() {\n''',
)
replace_once(
    "frontend/app.js",
    '''  renderEventHistory();\n\n  const lastAnswer = [...state.messages].reverse().find(message => message.role === "assistant");\n  if (lastAnswer?.payload) {\n    renderEvidence(lastAnswer.payload.evidence || []);\n    renderSuggestions(lastAnswer.payload.suggestions || []);\n  }\n''',
    '''  renderEventHistory();\n  renderTaskActions();\n  renderApproval();\n\n  const lastAnswer = [...state.messages].reverse().find(message => message.role === "assistant");\n  if (lastAnswer?.payload) {\n    renderEvidence(lastAnswer.payload.evidence || []);\n    renderDeliverables(lastAnswer.payload.deliverables || []);\n    renderSuggestions(lastAnswer.payload.suggestions || []);\n  } else {\n    renderDeliverables([]);\n  }\n  renderFeedbackState();\n''',
)
replace_once(
    "frontend/app.js",
    '''    <article class="msg assistant">\n''',
    '''    <article class="msg assistant" data-message-id="${esc(message.id)}">\n''',
)
replace_once(
    "frontend/app.js",
    '''        <div class="answer-foot">\n          <button class="answer-action" type="button" data-copy-result>复制结果</button>\n        </div>\n''',
    '''        <div class="answer-foot">\n          <button class="answer-action" type="button" data-copy-result>复制结果</button>\n          <span class="answer-feedback-label">结果反馈</span>\n          <button class="answer-action feedback-action" type="button" data-feedback="correct">正确</button>\n          <button class="answer-action feedback-action" type="button" data-feedback="partial">部分正确</button>\n          <button class="answer-action feedback-action" type="button" data-feedback="incorrect">有错误</button>\n          <button class="answer-action verify-action" type="button" data-human-verify>人工已验证</button>\n        </div>\n''',
)
replace_once(
    "frontend/app.js",
    '''function renderSuggestions(rows = []) {\n''',
    '''function renderDeliverables(rows = []) {\n  const box = $("deliverableList");\n  if (!box) return;\n  box.innerHTML = rows.length ? rows.map((item, index) => `\n    <article class="deliverable-card">\n      <div class="deliverable-head"><span>${String(index + 1).padStart(2, "0")}</span><b>${esc(item.title || "研发交付")}</b></div>\n      <p>${esc(item.summary || "")}</p>\n      <ul>${(item.items || []).map(value => `<li>${esc(value)}</li>`).join("")}</ul>\n      <div class="deliverable-foot">\n        <small>${(item.evidence_ids || []).length} 条证据关联</small>\n        <button type="button" data-copy-deliverable="${index}">复制</button>\n      </div>\n    </article>\n  `).join("") : '<div class="empty-side">完成一次任务后显示。</div>';\n  box.querySelectorAll("[data-copy-deliverable]").forEach(button => {\n    button.onclick = async () => {\n      const item = rows[Number(button.dataset.copyDeliverable)];\n      const text = [item.title, item.summary, ...(item.items || []).map(value => `- ${value}`)].filter(Boolean).join("\\n");\n      await navigator.clipboard?.writeText(text);\n      trackProductEvent("deliverable.copy", state.conversation?.id, {type: item.type});\n      toast("交付物已复制");\n    };\n  });\n}\n\nfunction renderSuggestions(rows = []) {\n''',
)

# Product control helpers inserted before bindUI.
replace_once(
    "frontend/app.js",
    '''function bindUI() {\n''',
    r'''function taskStatusLabel(status) {
  return {active: "进行中", waiting_approval: "等待确认", blocked: "受阻", verified: "已验证"}[status] || "进行中";
}

function isManager() {
  return ["owner", "admin"].includes(state.session?.user?.role);
}

async function trackProductEvent(name, conversationId = state.conversation?.id, payload = {}) {
  try {
    await api("/api/product-events", {method: "POST", body: JSON.stringify({name, conversation_id: conversationId || null, payload})});
  } catch {}
}

async function loadConversationControl() {
  if (!state.conversation?.id) return;
  try {
    state.control = await api(`/api/conversations/${state.conversation.id}/control`);
    state.gate = state.control.quality_gate || null;
    state.feedback = {};
    for (const row of state.control.feedback || []) {
      if (row.user_id === state.session?.user?.id) state.feedback[row.message_id] = row;
    }
  } catch {
    state.control = {approvals: [], feedback: [], quality_gate: null};
    state.gate = null;
  }
}

function renderTaskActions() {
  if (!state.conversation) return;
  const jobStatus = state.conversation.job?.status;
  $("retryTaskBtn").hidden = !["failed", "cancelled"].includes(jobStatus);
  $("pinTaskBtn").textContent = state.conversation.pinned ? "取消置顶" : "置顶";
  $("archiveTaskBtn").textContent = state.conversation.archived_at ? "恢复" : "归档";
  $("deleteTaskBtn").hidden = !isManager();
}

function pendingDeleteApproval() {
  return (state.control?.approvals || []).find(row => row.action === "conversation.delete" && row.status === "pending");
}

function renderApproval() {
  const card = $("approvalCard");
  if (!card) return;
  const approval = pendingDeleteApproval();
  card.hidden = !approval;
  if (!approval) return;
  const controls = isManager() ? `
    <div class="approval-actions">
      <button type="button" data-approval-reject>取消删除</button>
      <button class="danger" type="button" data-approval-confirm>确认永久删除</button>
    </div>` : '<small>等待工作空间管理员确认。</small>';
  card.innerHTML = `<div><span class="eyebrow">需要确认</span><b>永久删除任务和任务素材</b><p>${esc(approval.reason || "此操作不可恢复。")}</p></div>${controls}`;
  card.querySelector("[data-approval-reject]")?.addEventListener("click", () => resolveDeleteApproval(approval, false));
  card.querySelector("[data-approval-confirm]")?.addEventListener("click", () => resolveDeleteApproval(approval, true));
}

async function requestDeleteTask() {
  if (!state.conversation) return;
  try {
    const approval = await api(`/api/conversations/${state.conversation.id}/delete-request`, {method: "POST", body: "{}"});
    state.control = state.control || {approvals: []};
    state.control.approvals = [approval, ...(state.control.approvals || []).filter(row => row.id !== approval.id)];
    state.conversation.status = "waiting_approval";
    renderConversation();
    toast("删除请求已进入确认状态");
  } catch (error) { toast(error.message); }
}

async function resolveDeleteApproval(approval, approved) {
  try {
    const resolved = await api(`/api/approvals/${approval.id}/resolve`, {method: "POST", body: JSON.stringify({approved})});
    if (!approved) {
      await loadConversationControl();
      state.conversation.status = "active";
      renderConversation();
      toast("已取消删除");
      return;
    }
    await api(`/api/conversations/${state.conversation.id}?approval_id=${encodeURIComponent(resolved.id)}`, {method: "DELETE"});
    state.ws?.close();
    state.conversation = null;
    state.messages = [];
    state.assets = [];
    state.control = null;
    toast("任务及其素材已永久删除");
    await bootWorkspace();
  } catch (error) { toast(error.message); }
}

async function retryCurrentTask() {
  const jobId = state.conversation?.job?.id;
  if (!jobId) return;
  try {
    const response = await api(`/api/jobs/${jobId}/retry`, {method: "POST", body: "{}"});
    state.conversation.job = {id: response.job_id, status: response.status || "queued"};
    state.progress = [];
    setBusy(true, response.job_id);
    $("thinkingCard").hidden = false;
    $("thinkingStep").textContent = "重新执行";
    $("thinkingDetail").textContent = "正在从上一次任务上下文恢复执行";
    renderConversation();
    toast("已重新执行");
  } catch (error) { toast(error.message); }
}

async function renameCurrentTask() {
  if (!state.conversation) return;
  const value = window.prompt("任务名称", state.conversation.title || "");
  if (!value?.trim() || value.trim() === state.conversation.title) return;
  try {
    state.conversation = {...state.conversation, ...(await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({title: value.trim()})}))};
    renderConversation();
    await loadConversations();
  } catch (error) { toast(error.message); }
}

async function togglePinTask() {
  if (!state.conversation) return;
  try {
    state.conversation = {...state.conversation, ...(await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({pinned: !Boolean(state.conversation.pinned)})}))};
    renderConversation();
    await loadConversations();
  } catch (error) { toast(error.message); }
}

async function toggleArchiveTask() {
  if (!state.conversation) return;
  const restoring = Boolean(state.conversation.archived_at);
  try {
    const row = await api(`/api/conversations/${state.conversation.id}/${restoring ? "restore" : "archive"}`, {method: "POST", body: "{}"});
    state.conversation = {...state.conversation, ...row};
    renderConversation();
    await loadConversations();
    toast(restoring ? "任务已恢复" : "任务已归档");
  } catch (error) { toast(error.message); }
}

async function saveFeedback(messageId, patch) {
  const previous = state.feedback[messageId] || {verdict: "partial", evidence_useful: null, human_verified: 0, note: ""};
  const payload = {...previous, ...patch};
  try {
    const row = await api(`/api/messages/${messageId}/feedback`, {
      method: "PUT",
      body: JSON.stringify({
        verdict: payload.verdict,
        evidence_useful: payload.evidence_useful,
        human_verified: Boolean(payload.human_verified),
        note: payload.note || "",
      }),
    });
    state.feedback[messageId] = row;
    state.gate = await api(`/api/quality-gate?conversation_id=${encodeURIComponent(state.conversation.id)}`);
    renderFeedbackState();
    renderTeamPanel();
    toast(Boolean(row.human_verified) ? "已标记人工验证" : "结果反馈已记录");
  } catch (error) { toast(error.message); }
}

function renderFeedbackState() {
  $("messageList").querySelectorAll(".msg.assistant[data-message-id]").forEach(article => {
    const messageId = article.dataset.messageId;
    const feedback = state.feedback[messageId];
    article.querySelectorAll("[data-feedback]").forEach(button => button.classList.toggle("active", feedback?.verdict === button.dataset.feedback));
    article.querySelector("[data-human-verify]")?.classList.toggle("active", Boolean(feedback?.human_verified));
  });
}

async function loadWorkspaces() {
  try {
    state.workspaces = await api("/api/workspaces");
    renderWorkspaceMenu();
  } catch (error) { toast(error.message); }
}

function renderWorkspaceMenu() {
  const menu = $("workspaceMenu");
  if (!menu) return;
  menu.innerHTML = state.workspaces.map(row => `<button type="button" data-workspace-id="${esc(row.id)}" class="${row.id === state.session?.workspace?.id ? "active" : ""}"><b>${esc(row.name)}</b><small>${esc(row.role)}</small></button>`).join("") || '<div class="empty-side">没有其他工作空间。</div>';
  menu.querySelectorAll("[data-workspace-id]").forEach(button => {
    button.onclick = async () => {
      if (button.dataset.workspaceId === state.session?.workspace?.id) { menu.hidden = true; return; }
      try {
        const session = await api(`/api/workspaces/${button.dataset.workspaceId}/switch`, {method: "POST", body: "{}"});
        applySession(session);
        menu.hidden = true;
        state.conversation = null;
        state.ws?.close();
        await bootWorkspace();
      } catch (error) { toast(error.message); }
    };
  });
}

async function maybeAcceptInvite() {
  const token = new URLSearchParams(location.search).get("invite");
  if (!token || !state.session) return;
  try {
    const session = await api(`/api/invites/${encodeURIComponent(token)}/accept`, {method: "POST", body: "{}"});
    applySession(session);
    const url = new URL(location.href);
    url.searchParams.delete("invite");
    history.replaceState(null, "", url);
    toast("已加入工作空间");
  } catch (error) {
    if (!/已失效|已使用/.test(error.message)) toast(error.message);
  }
}

async function loadTeamPanel() {
  try {
    state.members = await api("/api/workspace/members");
  } catch { state.members = []; }
  if (isManager()) {
    try { state.metrics = await api("/api/metrics"); } catch { state.metrics = null; }
    try { state.invites = await api("/api/workspace/invites"); } catch { state.invites = []; }
  } else {
    state.metrics = null;
    state.invites = [];
  }
  renderTeamPanel();
}

function renderTeamPanel() {
  const assignee = $("assigneeSelect");
  if (!assignee) return;
  assignee.innerHTML = '<option value="">未指定</option>' + state.members.map(member => `<option value="${esc(member.id)}">${esc(member.name || member.email)}</option>`).join("");
  assignee.value = state.conversation?.assigned_to || "";
  assignee.disabled = !state.conversation;

  $("memberList").innerHTML = state.members.length ? state.members.map(member => {
    const controls = isManager() ? `<select data-member-role="${esc(member.id)}"><option value="owner">所有者</option><option value="admin">管理员</option><option value="member">成员</option><option value="viewer">只读</option></select><button type="button" data-remove-member="${esc(member.id)}">移除</button>` : `<em>${esc(member.role)}</em>`;
    return `<div class="member-row"><span class="member-avatar">${esc((member.name || member.email || "成").slice(0, 1).toUpperCase())}</span><div><b>${esc(member.name || member.email)}</b><small>${esc(member.email)}</small></div><div class="member-controls">${controls}</div></div>`;
  }).join("") : '<div class="empty-side">还没有团队成员。</div>';
  $("memberList").querySelectorAll("[data-member-role]").forEach(select => {
    const member = state.members.find(row => row.id === select.dataset.memberRole);
    select.value = member?.role || "member";
    select.onchange = () => updateMemberRole(select.dataset.memberRole, select.value);
  });
  $("memberList").querySelectorAll("[data-remove-member]").forEach(button => button.onclick = () => removeMember(button.dataset.removeMember));
  $("inviteForm").hidden = !isManager();
  $("inviteList").innerHTML = (state.invites || []).filter(row => row.status === "pending").map(row => `<div class="invite-row"><div><b>${esc(row.email || "通用邀请")}</b><small>${esc(row.role)}</small></div><button type="button" data-copy-invite="${esc(row.token)}">复制链接</button><button type="button" data-revoke-invite="${esc(row.id)}">撤销</button></div>`).join("") || (isManager() ? '<div class="empty-side">没有待使用邀请。</div>' : "");
  $("inviteList").querySelectorAll("[data-copy-invite]").forEach(button => button.onclick = () => copyInvite(button.dataset.copyInvite));
  $("inviteList").querySelectorAll("[data-revoke-invite]").forEach(button => button.onclick = () => revokeInvite(button.dataset.revokeInvite));

  const gate = state.gate;
  $("qualityGate").innerHTML = gate ? `<div class="gate-state ${gate.approved ? "approved" : "pending"}"><b>${gate.approved ? "人工质量门已通过" : "等待人工质量门"}</b><small>${esc(gate.reason || "")}</small></div>` : '<div class="empty-side">任务结果产生后显示质量门。</div>';
  const metrics = state.metrics;
  $("metricGrid").innerHTML = metrics ? [
    ["任务完成", `${Math.round((metrics.first_task_completion_rate || 0) * 100)}%`],
    ["失败恢复", `${Math.round((metrics.recovery_rate || 0) * 100)}%`],
    ["证据打开", `${Math.round((metrics.evidence_open_rate || 0) * 100)}%`],
    ["结果采纳", `${Math.round((metrics.result_adoption_rate || 0) * 100)}%`],
  ].map(([label, value]) => `<div><b>${value}</b><small>${label}</small></div>`).join("") : "";
}

async function createInvite(event) {
  event.preventDefault();
  try {
    const invite = await api("/api/workspace/invites", {method: "POST", body: JSON.stringify({email: $("inviteEmail").value.trim() || null, role: $("inviteRole").value})});
    state.invites = [invite, ...(state.invites || [])];
    $("inviteEmail").value = "";
    renderTeamPanel();
    await copyInvite(invite.token);
  } catch (error) { toast(error.message); }
}

async function copyInvite(token) {
  const url = new URL(location.href);
  url.search = "";
  url.searchParams.set("invite", token);
  await navigator.clipboard?.writeText(url.toString());
  toast("邀请链接已复制");
}

async function revokeInvite(inviteId) {
  try {
    await api(`/api/workspace/invites/${inviteId}`, {method: "DELETE"});
    state.invites = (state.invites || []).filter(row => row.id !== inviteId);
    renderTeamPanel();
  } catch (error) { toast(error.message); }
}

async function updateMemberRole(userId, role) {
  try {
    await api(`/api/workspace/members/${userId}`, {method: "PATCH", body: JSON.stringify({role})});
    await loadTeamPanel();
  } catch (error) { toast(error.message); await loadTeamPanel(); }
}

async function removeMember(userId) {
  try {
    await api(`/api/workspace/members/${userId}`, {method: "DELETE"});
    await loadTeamPanel();
  } catch (error) { toast(error.message); }
}

async function assignTask(userId) {
  if (!state.conversation) return;
  try {
    const row = await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({assigned_to: userId || null})});
    state.conversation = {...state.conversation, ...row};
    trackProductEvent("task.handoff", state.conversation.id, {assigned: Boolean(userId)});
    toast("负责人已更新");
  } catch (error) { toast(error.message); await loadTeamPanel(); }
}

function bindUI() {
''',
)

# Bind controls and feedback.
replace_once(
    "frontend/app.js",
    '''  $("newTaskBtn").onclick = () => newConversation(state.scene);\n  $("refreshConvBtn").onclick = loadConversations;\n''',
    '''  $("newTaskBtn").onclick = () => newConversation(state.scene);\n  $("refreshConvBtn").onclick = loadConversations;\n  let searchTimer;\n  $("taskSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConversations, 180); };\n  document.querySelectorAll("[data-task-scope]").forEach(button => {\n    button.onclick = () => {\n      state.scope = button.dataset.taskScope;\n      document.querySelectorAll("[data-task-scope]").forEach(item => item.classList.toggle("active", item === button));\n      loadConversations();\n    };\n  });\n  $("retryTaskBtn").onclick = retryCurrentTask;\n  $("renameTaskBtn").onclick = renameCurrentTask;\n  $("pinTaskBtn").onclick = togglePinTask;\n  $("archiveTaskBtn").onclick = toggleArchiveTask;\n  $("deleteTaskBtn").onclick = requestDeleteTask;\n  $("workspaceSwitchBtn").onclick = async () => {\n    const menu = $("workspaceMenu");\n    menu.hidden = !menu.hidden;\n    $("workspaceSwitchBtn").setAttribute("aria-expanded", String(!menu.hidden));\n    if (!menu.hidden) await loadWorkspaces();\n  };\n  document.addEventListener("click", event => {\n    if (!$("workspaceMenu").hidden && !$("workspaceMenu").contains(event.target) && !$("workspaceSwitchBtn").contains(event.target)) {\n      $("workspaceMenu").hidden = true;\n      $("workspaceSwitchBtn").setAttribute("aria-expanded", "false");\n    }\n  });\n  $("assigneeSelect").onchange = event => assignTask(event.target.value);\n  $("inviteForm").onsubmit = createInvite;\n''',
)
replace_once(
    "frontend/app.js",
    '''  $("messageList").addEventListener("click", event => {\n    const button = event.target.closest("[data-copy-result]");\n    if (!button) return;\n    const article = button.closest(".msg");\n    const text = article?.querySelector(".msg-content")?.innerText || "";\n    navigator.clipboard?.writeText(text);\n    toast("结果已复制");\n  });\n''',
    '''  $("messageList").addEventListener("click", event => {\n    const article = event.target.closest(".msg.assistant[data-message-id]");\n    if (!article) return;\n    const messageId = article.dataset.messageId;\n    const copy = event.target.closest("[data-copy-result]");\n    if (copy) {\n      const text = article.querySelector(".msg-content")?.innerText || "";\n      navigator.clipboard?.writeText(text);\n      trackProductEvent("result.copy", state.conversation?.id);\n      toast("结果已复制");\n      return;\n    }\n    const feedback = event.target.closest("[data-feedback]");\n    if (feedback) { saveFeedback(messageId, {verdict: feedback.dataset.feedback}); return; }\n    if (event.target.closest("[data-human-verify]")) {\n      const previous = state.feedback[messageId];\n      saveFeedback(messageId, {verdict: previous?.verdict || "correct", human_verified: !Boolean(previous?.human_verified)});\n    }\n  });\n''',
)
replace_once(
    "frontend/app.js",
    '''      document.querySelectorAll(".right-panel").forEach(panel => {\n        panel.classList.toggle(\n          "active",\n          panel.id === `panel-${button.dataset.panel}`\n        );\n      });\n''',
    '''      document.querySelectorAll(".right-panel").forEach(panel => {\n        panel.classList.toggle(\n          "active",\n          panel.id === `panel-${button.dataset.panel}`\n        );\n      });\n      if (button.dataset.panel === "evidence") trackProductEvent("evidence.open", state.conversation?.id);\n      if (button.dataset.panel === "team") loadTeamPanel();\n''',
)
replace_once(
    "frontend/app.js",
    '''    renderEvidence(result.evidence || []);\n    renderSuggestions(result.suggestions || []);\n''',
    '''    renderEvidence(result.evidence || []);\n    renderDeliverables(result.deliverables || []);\n    renderSuggestions(result.suggestions || []);\n    loadConversationControl().then(() => { renderFeedbackState(); renderTeamPanel(); });\n''',
)
# Accept invite after either existing session or auth submit.
replace_once(
    "frontend/app.js",
    '''    applySession(session);\n    hideAuthModal();\n    return true;\n''',
    '''    applySession(session);\n    hideAuthModal();\n    await maybeAcceptInvite();\n    return true;\n''',
)
replace_once(
    "frontend/app.js",
    '''      applySession(session);\n      hideAuthModal();\n      toast("登录成功");\n      await bootWorkspace();\n''',
    '''      applySession(session);\n      hideAuthModal();\n      await maybeAcceptInvite();\n      toast("登录成功");\n      await bootWorkspace();\n''',
)
replace_once(
    "frontend/app.js",
    '''      applySession(session);\n      hideAuthModal();\n      toast("工作空间已创建");\n      await bootWorkspace();\n''',
    '''      applySession(session);\n      hideAuthModal();\n      await maybeAcceptInvite();\n      toast("工作空间已创建");\n      await bootWorkspace();\n''',
)

# New control surfaces use the existing light visual language; no ambient motion added.
css = ROOT / "frontend/app.css"
css.write_text(css.read_text(encoding="utf-8") + r'''

/* Product lifecycle and collaboration */
.workspace-switch { position: relative; }
.workspace-caret { color: var(--muted, #7b8495); font-size: 13px; }
.workspace-menu { position: fixed; z-index: 80; top: 58px; left: 164px; width: 260px; max-height: 320px; overflow: auto; padding: 8px; border: 1px solid #e2e6ee; border-radius: 14px; background: rgba(255,255,255,.98); box-shadow: 0 18px 50px rgba(32,42,64,.14); }
.workspace-menu button { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 10px 11px; border: 0; border-radius: 10px; background: transparent; text-align: left; cursor: pointer; }
.workspace-menu button:hover,.workspace-menu button.active { background: #f2f4f8; }
.workspace-menu small { color: #8a93a3; }
.task-index-tools { display: grid; gap: 8px; margin: 8px 0 10px; }
.task-index-tools input { width: 100%; border: 1px solid #e1e5ec; background: #fff; border-radius: 9px; padding: 8px 10px; outline: none; }
.task-index-tools input:focus { border-color: #b5bdd0; box-shadow: 0 0 0 3px rgba(91,106,140,.08); }
.task-scope { display: grid; grid-template-columns: 1fr 1fr; padding: 3px; background: #eceff4; border-radius: 9px; }
.task-scope button { border: 0; border-radius: 7px; padding: 6px 8px; background: transparent; color: #737d8d; cursor: pointer; font-size: 12px; }
.task-scope button.active { background: #fff; color: #252b36; box-shadow: 0 1px 4px rgba(31,39,55,.08); }
.danger-text { color: #ad4050 !important; }
.approval-card { margin: 0 24px 12px; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; gap: 18px; border: 1px solid #efcfd4; border-radius: 13px; background: #fff8f9; }
.approval-card b { display: block; margin: 3px 0 4px; color: #702c38; }
.approval-card p { margin: 0; color: #895c64; font-size: 12px; }
.approval-actions { display: flex; gap: 8px; flex-shrink: 0; }
.approval-actions button { border: 1px solid #dddfe5; background: #fff; border-radius: 9px; padding: 8px 10px; cursor: pointer; }
.approval-actions button.danger { border-color: #d77c89; background: #a73f50; color: #fff; }
.deliverable-list { display: grid; gap: 10px; }
.deliverable-card { border: 1px solid #e4e7ed; background: #fff; border-radius: 13px; padding: 12px; }
.deliverable-head { display: flex; align-items: center; gap: 8px; }
.deliverable-head span { font-size: 10px; color: #8b94a4; }
.deliverable-card p { margin: 7px 0; color: #677181; font-size: 12px; line-height: 1.55; }
.deliverable-card ul { margin: 0; padding-left: 18px; color: #3f4856; font-size: 12px; line-height: 1.65; }
.deliverable-foot { margin-top: 9px; display: flex; justify-content: space-between; align-items: center; }
.deliverable-foot small { color: #929aa8; }
.deliverable-foot button { border: 0; background: #eef1f6; border-radius: 7px; padding: 5px 8px; cursor: pointer; }
.answer-feedback-label { color: #9aa2af; font-size: 11px; margin-left: auto; }
.feedback-action.active,.verify-action.active { background: #e8f1ed; border-color: #b8d5c8; color: #356452; }
.assignee-field { display: grid; gap: 5px; margin-bottom: 13px; font-size: 12px; color: #747e8e; }
.assignee-field select,.invite-form input,.invite-form select { border: 1px solid #e0e4eb; border-radius: 9px; background: #fff; padding: 8px; }
.member-list,.invite-list { display: grid; gap: 8px; margin-bottom: 13px; }
.member-row,.invite-row { display: flex; gap: 9px; align-items: center; border: 1px solid #e8eaf0; border-radius: 10px; padding: 8px; background: #fff; }
.member-row > div:nth-child(2),.invite-row > div { min-width: 0; flex: 1; }
.member-row b,.invite-row b { display: block; font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
.member-row small,.invite-row small { color: #9098a6; font-size: 10px; }
.member-avatar { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; background: #eef1f5; color: #596476; font-weight: 700; }
.member-controls { display: flex; gap: 5px; align-items: center; }
.member-controls select,.member-controls button,.invite-row button { border: 0; background: #f0f2f6; border-radius: 7px; padding: 5px 7px; font-size: 10px; cursor: pointer; }
.invite-form { display: grid; grid-template-columns: 1fr auto; gap: 7px; margin-bottom: 10px; }
.invite-form input { grid-column: 1 / -1; }
.invite-form button { border: 0; border-radius: 9px; background: #252c38; color: #fff; padding: 8px; cursor: pointer; }
.quality-gate { margin-bottom: 10px; }
.gate-state { display: grid; gap: 3px; border-radius: 10px; padding: 10px; border: 1px solid #e4e7ed; }
.gate-state.approved { background: #f3faf6; border-color: #cfe3d8; }
.gate-state.pending { background: #fffaf1; border-color: #eadfc3; }
.gate-state small { color: #7c8593; }
.metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.metric-grid div { display: grid; gap: 2px; border: 1px solid #e8eaf0; border-radius: 10px; padding: 9px; background: #fff; }
.metric-grid b { font-size: 17px; }
.metric-grid small { color: #8f97a5; }
@media (max-width: 1150px) { .head-actions { flex-wrap: wrap; justify-content: flex-end; } .workspace-menu { left: 118px; } }
''', encoding="utf-8")

# README accurately reflects the newly enforced product lifecycle.
replace_once(
    "README.md",
    "**给出目标和素材，系统持续执行、复核、留证；需要时随时停止。**",
    "**给出目标和素材，系统持续执行、复核、留证；需要时停止、重试、交接、归档，并对不可逆操作显式确认。**",
)
replace_once(
    "README.md",
    '''| **可隔离** | 用户、工作空间、任务、素材、运行记录与审计边界在服务端校验，不依赖前端隐藏 |\n''',
    '''| **可隔离** | 用户、工作空间、任务、素材、运行记录与审计边界在服务端校验，不依赖前端隐藏 |\n| **可治理** | 永久删除先进入持久化审批状态；成员角色、任务负责人、邀请与交接都有服务端权限和审计 |\n| **可闭环** | 交付结果可以标记正确性、证据价值与人工验证；质量门作为演进的显式否决条件，而不是把点赞直接变成策略 |\n''',
)
replace_once(
    "README.md",
    '''任务完成与最终结果采用确定性的状态提交：**完成状态、assistant 交付和 `answer.ready` 事件作为同一事务提交**。如果停止先发生，就不会再补写一个“迟到的成功结果”。\n''',
    '''任务完成与最终结果采用确定性的状态提交：**完成状态、assistant 交付和 `answer.ready` 事件作为同一事务提交**。如果停止先发生，就不会再补写一个“迟到的成功结果”。停止后的执行可以用原任务上下文重新执行；产品没有伪造“暂停”，因为当前分析任务无法保证外部推理调用可以从任意指令点无损续跑。\n\n任务本身具备搜索、重命名、置顶、归档/恢复、负责人交接与受审批保护的永久删除。交付会进一步沉淀为复现卡、回归清单、风险清单、调参验证方案或证据包，而不是只停在一段回答。\n''',
)
replace_once(
    "README.md",
    '''### Verification + Evolution\n\nVerifier 独立检查状态不变量、非法动作、灾难性风险和异常奖励循环。成功与失败轨迹进入 Memory / Skill；策略更新受 group-relative reward、KL trust region 与 Regression Gate 约束。\n\n> **可验证轨迹 → 候选更新 → 回归评估 → 通过才提交。**\n''',
    '''### Verification + Evolution\n\nVerifier 独立检查状态不变量、非法动作、灾难性风险和异常奖励循环。成功与失败轨迹进入 Memory / Skill；策略更新受 group-relative reward、KL trust region、Regression Gate 与 Human Feedback Gate 共同约束。客户对结果的反馈会先进入结构化质量状态，不会直接修改策略；存在错误反馈或缺少人工验证时，可以显式否决候选演进。\n\n> **可验证轨迹 → 候选更新 → 回归评估 + 人工质量门 → 通过才提交。**\n''',
)
replace_once(
    "README.md",
    '''UI E2E 会真实覆盖注册、工作空间、素材上传、执行状态、停止控制、实时结果、证据、后续建议与多模态素材，并生成 README Gallery。截图以 `1920×1200` viewport、device scale `2` 采集，输出为 **3840×2400 PNG**。\n''',
    '''UI E2E 会真实覆盖注册、工作空间、素材上传、执行状态、停止/重试控制、任务生命周期、结构化交付、结果反馈、团队协作、实时结果、证据与多模态素材，并生成 README Gallery。截图以 `1920×1200` viewport、device scale `2` 采集，输出为 **3840×2400 PNG**。\n''',
)

# Remove this temporary patch and its trigger from the resulting product commit.
for relative in ["scripts/_apply_product_completion.py", ".github/workflows/product-completion.yml"]:
    (ROOT / relative).unlink(missing_ok=True)
