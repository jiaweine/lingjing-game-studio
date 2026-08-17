from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Product store: one active execution per task, retry only the latest terminal run,
# and never archive a task while it is executing.
replace_once(
    "worldforge/product/store.py",
    '''    def archive_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        now = time.time()\n''',
    '''    def archive_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise ValueError("执行中的任务需要先停止，才能归档")\n        now = time.time()\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''    def enqueue_job(self, *, workspace_id: str, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:\n        self.get_conversation(conversation_id, workspace_id=workspace_id)\n        job_id, now = _id("job"), time.time()\n''',
    '''    def enqueue_job(self, *, workspace_id: str, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:\n        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)\n        if conversation.get("archived_at") is not None:\n            raise ValueError("已归档任务需要先恢复，才能继续执行")\n        latest = self.latest_job(conversation_id, workspace_id=workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise ValueError("当前任务已有执行正在进行")\n        job_id, now = _id("job"), time.time()\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''    def retry_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:\n        source = self.get_job(job_id, workspace_id=workspace_id)\n        if source["status"] not in {"failed", "cancelled"}:\n            raise ValueError("只有失败或已停止的执行可以重试")\n        return self.enqueue_job(workspace_id=workspace_id, conversation_id=source["conversation_id"], payload=source["payload"])\n''',
    '''    def retry_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:\n        source = self.get_job(job_id, workspace_id=workspace_id)\n        if source["status"] not in {"failed", "cancelled"}:\n            raise ValueError("只有失败或已停止的执行可以重试")\n        latest = self.latest_job(source["conversation_id"], workspace_id=workspace_id)\n        if not latest or latest["id"] != source["id"]:\n            raise ValueError("只能重新执行当前任务最新一次失败或已停止的执行")\n        return self.enqueue_job(workspace_id=workspace_id, conversation_id=source["conversation_id"], payload=source["payload"])\n''',
)

# Replace the metrics tail with conversation-deduplicated rates and true
# time-to-first-verified / manual-intervention measures.
store_path = ROOT / "worldforge/product/store.py"
store_text = store_path.read_text(encoding="utf-8")
marker = "    def product_metrics(self, *, workspace_id: str) -> dict[str, Any]:\n"
if marker not in store_text:
    raise RuntimeError("product_metrics marker missing")
store_prefix = store_text.split(marker, 1)[0]
store_metrics = r'''    def product_metrics(self, *, workspace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            conversations = [self._dict(row) for row in connection.execute(select(self.conversations).where(self.conversations.c.workspace_id == workspace_id)).fetchall()]
            jobs = [self._dict(row) for row in connection.execute(select(self.jobs).where(self.jobs.c.workspace_id == workspace_id)).fetchall()]
            message_rows = connection.execute(select(self.messages.c.conversation_id, self.messages.c.role).select_from(self.messages.join(self.conversations, self.messages.c.conversation_id == self.conversations.c.id)).where(self.conversations.c.workspace_id == workspace_id)).fetchall()
            events = [self._dict(row) for row in connection.execute(select(self.product_events).where(self.product_events.c.workspace_id == workspace_id)).fetchall()]
            feedback = [self._dict(row) for row in connection.execute(select(self.result_feedback).where(self.result_feedback.c.workspace_id == workspace_id)).fetchall()]

        jobs_by_conversation: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            jobs_by_conversation.setdefault(job["conversation_id"], []).append(job)

        completed_ids = {
            conversation_id
            for conversation_id, rows in jobs_by_conversation.items()
            if any(row["status"] == "completed" for row in rows)
        }
        task_ids_with_jobs = set(jobs_by_conversation)
        failed_or_cancelled = {
            conversation_id
            for conversation_id, rows in jobs_by_conversation.items()
            if any(row["status"] in {"failed", "cancelled"} for row in rows)
        }
        recovered = failed_or_cancelled & completed_ids

        first_verified_durations: list[float] = []
        for conversation_id in completed_ids:
            rows = jobs_by_conversation[conversation_id]
            started_at = min(float(row["created_at"]) for row in rows)
            completed_at = min(
                float(row["completed_at"])
                for row in rows
                if row["status"] == "completed" and row.get("completed_at") is not None
            )
            first_verified_durations.append(max(0.0, completed_at - started_at))

        user_turns: dict[str, int] = {}
        for row in message_rows:
            if row.role == "user":
                user_turns[row.conversation_id] = user_turns.get(row.conversation_id, 0) + 1
        continued_ids = {conversation_id for conversation_id, count in user_turns.items() if count >= 2}

        evidence_open_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] == "evidence.open" and event.get("conversation_id") in completed_ids
        }
        adoption_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] in {"result.copy", "deliverable.copy"} and event.get("conversation_id") in completed_ids
        }
        explicit_intervention_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] in {"task.retry", "task.handoff"} and event.get("conversation_id")
        }
        manual_intervention_ids = failed_or_cancelled | explicit_intervention_ids
        verified_feedback = sum(1 for row in feedback if row.get("human_verified"))

        job_denominator = max(1, len(task_ids_with_jobs))
        completed_denominator = max(1, len(completed_ids))
        feedback_denominator = max(1, len(feedback))
        return {
            "task_count": len(conversations),
            "active_tasks": sum(1 for row in conversations if row.get("archived_at") is None),
            "first_task_completion_rate": round(len(completed_ids) / job_denominator, 4),
            "avg_time_to_verified_seconds": round(sum(first_verified_durations) / len(first_verified_durations), 2) if first_verified_durations else None,
            "interruption_rate": round(sum(1 for job in jobs if job["status"] == "cancelled") / max(1, len(jobs)), 4),
            "failure_rate": round(sum(1 for job in jobs if job["status"] == "failed") / max(1, len(jobs)), 4),
            "recovery_rate": round(len(recovered) / max(1, len(failed_or_cancelled)), 4),
            "continuation_rate": round(len(continued_ids) / max(1, len(user_turns)), 4),
            "manual_intervention_rate": round(len(manual_intervention_ids & task_ids_with_jobs) / job_denominator, 4),
            "evidence_open_rate": round(len(evidence_open_ids) / completed_denominator, 4),
            "result_adoption_rate": round(len(adoption_ids) / completed_denominator, 4),
            "human_verified_feedback_rate": round(verified_feedback / feedback_denominator, 4),
        }
'''
store_path.write_text(store_prefix + marker + store_metrics.split(marker, 1)[1], encoding="utf-8")

# Registration can consume an invite directly instead of creating an unwanted
# personal workspace first.
replace_once(
    "worldforge/api/app.py",
    '''class RegisterRequest(BaseModel):\n    email: str = Field(min_length=5, max_length=320)\n    password: str = Field(min_length=10, max_length=256)\n    name: str = Field(default="", max_length=120)\n    workspace_name: str = Field(\n        default="我的游戏团队", min_length=1, max_length=120\n    )\n''',
    '''class RegisterRequest(BaseModel):\n    email: str = Field(min_length=5, max_length=320)\n    password: str = Field(min_length=10, max_length=256)\n    name: str = Field(default="", max_length=120)\n    workspace_name: str = Field(\n        default="我的游戏团队", min_length=1, max_length=120\n    )\n    invite_token: str | None = Field(default=None, max_length=96)\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''        row = product_store.create_user_workspace(\n            email=req.email,\n            name=req.name,\n            password_hash=hash_password(req.password),\n            workspace_name=req.workspace_name,\n        )\n''',
    '''        if req.invite_token:\n            row = product_store.create_user_from_invite(\n                token=req.invite_token,\n                email=req.email,\n                name=req.name,\n                password_hash=hash_password(req.password),\n            )\n        else:\n            row = product_store.create_user_workspace(\n                email=req.email,\n                name=req.name,\n                password_hash=hash_password(req.password),\n                workspace_name=req.workspace_name,\n            )\n''',
)

# Job failures are terminal only after retries are exhausted. Intermediate
# external-worker attempts stay queued without lying to the customer.
replace_once(
    "worldforge/api/app.py",
    '''        logger.exception(\n            "analysis job failed",\n            extra={"conversation_id": conversation_id},\n        )\n        await _product_emit(\n            conversation_id,\n            workspace_id,\n            "answer.error",\n            {"message": "处理过程中出现问题", "detail": repr(exc), "job_id": job_id},\n        )\n        raise\n\n\nasync def _schedule_product_job(job, background_tasks: BackgroundTasks, principal: Principal):\n''',
    '''        logger.exception(\n            "analysis job attempt failed",\n            extra={"conversation_id": conversation_id, "job_id": job_id},\n        )\n        raise\n\n\nasync def _fail_product_job(job_id: str, error: str, *, max_attempts: int = 3):\n    failed = product_store.fail_job(job_id, error, max_attempts=max_attempts)\n    if failed and failed["status"] == "failed":\n        await _product_emit(\n            failed["conversation_id"],\n            failed["workspace_id"],\n            "answer.error",\n            {"message": "处理过程中出现问题", "detail": error, "job_id": job_id},\n        )\n    return failed\n\n\nasync def _schedule_product_job(job, background_tasks: BackgroundTasks, principal: Principal):\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''        except Exception as exc:\n            product_store.fail_job(claimed["id"], repr(exc), max_attempts=1)\n''',
    '''        except Exception as exc:\n            await _fail_product_job(claimed["id"], repr(exc), max_attempts=1)\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''    try:\n        product_store.get_conversation(\n            conversation_id, workspace_id=principal.workspace_id\n        )\n    except KeyError:\n        raise HTTPException(404, "任务不存在")\n\n    history = product_store.list_messages(\n''',
    '''    try:\n        conversation = product_store.get_conversation(\n            conversation_id, workspace_id=principal.workspace_id\n        )\n    except KeyError:\n        raise HTTPException(404, "任务不存在")\n    if conversation.get("archived_at") is not None:\n        raise HTTPException(409, "已归档任务需要先恢复，才能继续执行")\n    latest = product_store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n    if latest and latest["status"] in {"queued", "running"}:\n        raise HTTPException(409, "当前任务已有执行正在进行")\n\n    history = product_store.list_messages(\n''',
)

# Worker delegates terminal/retry behavior to the same helper as in-process jobs.
replace_once(
    "worldforge/worker.py",
    '''from worldforge.api.app import _run_analysis_job, product_store\n''',
    '''from worldforge.api.app import _fail_product_job, _run_analysis_job, product_store\n''',
)
replace_once(
    "worldforge/worker.py",
    '''        except Exception as exc:\n            product_store.fail_job(job["id"], repr(exc))\n''',
    '''        except Exception as exc:\n            await _fail_product_job(job["id"], repr(exc))\n''',
)

# Control API: customers cannot forge task status; admins cannot seize owner role;
# destructive operations are blocked while a job is active; storage must be
# removed successfully before metadata is permanently deleted.
replace_once("worldforge/product/control.py", "import time\n", "")
replace_once("worldforge/product/control.py", "    status: str | None = None\n", "")
replace_once(
    "worldforge/product/control.py",
    '''        require_manager(principal)\n        try:\n            row = store.set_member_role(principal.workspace_id, user_id, req.role)\n''',
    '''        require_manager(principal)\n        current = store.get_membership(principal.workspace_id, user_id)\n        if principal.role != "owner" and current and (current["role"] == "owner" or req.role == "owner"):\n            raise HTTPException(403, "只有所有者可以变更所有者角色")\n        try:\n            row = store.set_member_role(principal.workspace_id, user_id, req.role)\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        require_manager(principal)\n        try:\n            store.remove_member(principal.workspace_id, user_id)\n''',
    '''        require_manager(principal)\n        current = store.get_membership(principal.workspace_id, user_id)\n        if principal.role != "owner" and current and current["role"] == "owner":\n            raise HTTPException(403, "只有所有者可以移除所有者")\n        try:\n            store.remove_member(principal.workspace_id, user_id)\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''                assigned_to=req.assigned_to if "assigned_to" in req.model_fields_set else ...,\n                pinned=req.pinned,\n                status=req.status,\n''',
    '''                assigned_to=req.assigned_to if "assigned_to" in req.model_fields_set else ...,\n                pinned=req.pinned,\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        audit(request, principal, "conversation.archive", "conversation", conversation_id)\n''',
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from exc\n        audit(request, principal, "conversation.archive", "conversation", conversation_id)\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''    ):\n        try:\n            approval = store.create_approval(\n''',
    '''    ):\n        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise HTTPException(409, "执行中的任务需要先停止，才能请求永久删除")\n        try:\n            approval = store.create_approval(\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        if approval["conversation_id"] != conversation_id or approval["action"] != "conversation.delete" or approval["status"] != "approved":\n            raise HTTPException(409, "删除审批尚未通过或不匹配当前任务")\n        assets = store.list_assets(conversation_id, workspace_id=principal.workspace_id)\n''',
    '''        if approval["conversation_id"] != conversation_id or approval["action"] != "conversation.delete" or approval["status"] != "approved":\n            raise HTTPException(409, "删除审批尚未通过或不匹配当前任务")\n        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise HTTPException(409, "执行中的任务需要先停止，才能永久删除")\n        assets = store.list_assets(conversation_id, workspace_id=principal.workspace_id)\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        if not store.consume_approval(approval_id, workspace_id=principal.workspace_id, conversation_id=conversation_id, action="conversation.delete"):\n            raise HTTPException(409, "删除审批已被使用")\n        store.delete_conversation(conversation_id, workspace_id=principal.workspace_id)\n        for key in dict.fromkeys(keys):\n            try:\n                storage.delete(key)\n            except Exception:\n                # Database deletion remains authoritative. Orphan cleanup can be retried\n                # from the audit trail without restoring a deleted customer task.\n                pass\n        audit(request, principal, "conversation.delete", "conversation", conversation_id, {"asset_objects": len(keys)})\n''',
    '''        for key in dict.fromkeys(keys):\n            try:\n                storage.delete(key)\n            except Exception as exc:\n                audit(request, principal, "conversation.delete.storage_failed", "conversation", conversation_id, {"object_key": key, "error": repr(exc)})\n                raise HTTPException(503, "素材清理失败，任务尚未删除；可以稍后重试") from exc\n        if not store.consume_approval(approval_id, workspace_id=principal.workspace_id, conversation_id=conversation_id, action="conversation.delete"):\n            raise HTTPException(409, "删除审批已被使用")\n        store.delete_conversation(conversation_id, workspace_id=principal.workspace_id)\n        audit(request, principal, "conversation.delete", "conversation", conversation_id, {"asset_objects": len(keys)})\n''',
)

# Frontend: invitation registration, archived-task truthfulness, evidence-value
# feedback, and job-scoped terminal restoration after retry.
replace_once(
    "frontend/app.js",
    '''      workspace_name: $("registerWorkspace").value.trim(),\n    };\n''',
    '''      workspace_name: $("registerWorkspace").value.trim(),\n      invite_token: new URLSearchParams(location.search).get("invite") || null,\n    };\n''',
)
replace_once(
    "frontend/app.js",
    '''  state.pending = [];\n  setBusy(false);\n  state.ws?.close();\n''',
    '''  state.pending = [];\n  state.control = null;\n  state.feedback = {};\n  state.gate = null;\n  setBusy(false);\n  state.ws?.close();\n''',
)
replace_once(
    "frontend/app.js",
    '''  const terminal = [...state.events].reverse().find(event =>\n    ["answer.cancelled", "answer.error"].includes(event.type)\n  );\n''',
    '''  const terminal = [...state.events].reverse().find(event =>\n    ["answer.cancelled", "answer.error"].includes(event.type)\n    && (!job?.id || event.payload?.job_id === job.id)\n  );\n''',
)
replace_once(
    "frontend/app.js",
    '''  if (!content || state.busy) return;\n  if (!state.conversation) await newConversation(state.scene);\n\n  setBusy(true);\n''',
    '''  if (!content || state.busy) return;\n  if (!state.conversation) await newConversation(state.scene);\n  if (state.conversation?.archived_at) { toast("请先恢复已归档任务，再继续执行"); return; }\n\n  setBusy(true);\n''',
)
replace_once(
    "frontend/app.js",
    '''  $("deleteTaskBtn").hidden = !isManager();\n}\n''',
    '''  $("deleteTaskBtn").hidden = !isManager();\n  const archived = Boolean(state.conversation.archived_at);\n  $("messageInput").disabled = archived;\n  $("sendBtn").disabled = state.busy || archived;\n  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived; });\n}\n''',
)
replace_once(
    "frontend/app.js",
    '''          <button class="answer-action feedback-action" type="button" data-feedback="incorrect">有错误</button>\n          <button class="answer-action verify-action" type="button" data-human-verify>人工已验证</button>\n''',
    '''          <button class="answer-action feedback-action" type="button" data-feedback="incorrect">有错误</button>\n          <button class="answer-action evidence-action" type="button" data-evidence-useful>证据有用</button>\n          <button class="answer-action verify-action" type="button" data-human-verify>人工已验证</button>\n''',
)
replace_once(
    "frontend/app.js",
    '''    article.querySelectorAll("[data-feedback]").forEach(button => button.classList.toggle("active", feedback?.verdict === button.dataset.feedback));\n    article.querySelector("[data-human-verify]")?.classList.toggle("active", Boolean(feedback?.human_verified));\n''',
    '''    article.querySelectorAll("[data-feedback]").forEach(button => button.classList.toggle("active", feedback?.verdict === button.dataset.feedback));\n    article.querySelector("[data-evidence-useful]")?.classList.toggle("active", feedback?.evidence_useful === 1 || feedback?.evidence_useful === true);\n    article.querySelector("[data-human-verify]")?.classList.toggle("active", Boolean(feedback?.human_verified));\n''',
)
replace_once(
    "frontend/app.js",
    '''    const feedback = event.target.closest("[data-feedback]");\n    if (feedback) { saveFeedback(messageId, {verdict: feedback.dataset.feedback}); return; }\n    if (event.target.closest("[data-human-verify]")) {\n''',
    '''    const feedback = event.target.closest("[data-feedback]");\n    if (feedback) { saveFeedback(messageId, {verdict: feedback.dataset.feedback}); return; }\n    if (event.target.closest("[data-evidence-useful]")) {\n      const previous = state.feedback[messageId];\n      saveFeedback(messageId, {verdict: previous?.verdict || "correct", evidence_useful: !(previous?.evidence_useful === 1 || previous?.evidence_useful === true)});\n      return;\n    }\n    if (event.target.closest("[data-human-verify]")) {\n''',
)
replace_once(
    "frontend/app.js",
    '''    ["任务完成", `${Math.round((metrics.first_task_completion_rate || 0) * 100)}%`],\n    ["失败恢复", `${Math.round((metrics.recovery_rate || 0) * 100)}%`],\n    ["证据打开", `${Math.round((metrics.evidence_open_rate || 0) * 100)}%`],\n    ["结果采纳", `${Math.round((metrics.result_adoption_rate || 0) * 100)}%`],\n''',
    '''    ["任务完成", `${Math.round((metrics.first_task_completion_rate || 0) * 100)}%`],\n    ["首次可核验", metrics.avg_time_to_verified_seconds == null ? "—" : `${Math.round(metrics.avg_time_to_verified_seconds)}s`],\n    ["执行中断", `${Math.round((metrics.interruption_rate || 0) * 100)}%`],\n    ["失败恢复", `${Math.round((metrics.recovery_rate || 0) * 100)}%`],\n    ["人工介入", `${Math.round((metrics.manual_intervention_rate || 0) * 100)}%`],\n    ["继续任务", `${Math.round((metrics.continuation_rate || 0) * 100)}%`],\n    ["证据打开", `${Math.round((metrics.evidence_open_rate || 0) * 100)}%`],\n    ["结果采纳", `${Math.round((metrics.result_adoption_rate || 0) * 100)}%`],\n''',
)

# Keep the README claim exact: feedback gate is a veto input, not autonomous learning.
replace_once(
    "README.md",
    "客户对结果的反馈会先进入结构化质量状态，不会直接修改策略；存在错误反馈或缺少人工验证时，可以显式否决候选演进。",
    "客户对结果的反馈会先进入结构化质量状态，不会直接修改策略；存在错误反馈或缺少人工验证时，Human Feedback Gate 会作为候选演进的否决输入。",
)

for relative in ["scripts/_harden_product_completion.py", ".github/workflows/product-hardening.yml"]:
    (ROOT / relative).unlink(missing_ok=True)
