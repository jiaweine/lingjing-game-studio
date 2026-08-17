from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:180]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"start marker not found: {path}: {start!r}")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"end marker not found: {path}: {end!r}")
    target.write_text(text[:left] + replacement + text[right:], encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Multimodal task context persists across follow-up turns.
# The user message only displays explicitly selected assets, while the queued
# analysis receives every asset already attached to the task plus any newly
# selected workspace-owned asset.
# ---------------------------------------------------------------------------
replace_once(
    "worldforge/api/app.py",
    '''    history = product_store.list_messages(conversation_id, workspace_id=principal.workspace_id)\n    assets = product_store.list_assets(conversation_id, workspace_id=principal.workspace_id)\n    asset_by_id = {asset["id"]: asset for asset in assets}\n    selected_assets = []\n    for asset_id in req.asset_ids:\n        asset = asset_by_id.get(asset_id)\n        if asset is None:\n            try:\n                asset = product_store.get_asset(asset_id, workspace_id=principal.workspace_id)\n            except KeyError:\n                continue\n        selected_assets.append(asset)\n\n    job_payload = {\n        "text": req.content,\n        "provider": req.provider,\n        "history": history,\n        "asset_ids": [asset["id"] for asset in selected_assets],\n    }\n    try:\n        user_message, job = product_store.create_message_job(\n            workspace_id=principal.workspace_id,\n            conversation_id=conversation_id,\n            content=req.content,\n            asset_ids=[asset["id"] for asset in selected_assets],\n''',
    '''    history = product_store.list_messages(conversation_id, workspace_id=principal.workspace_id)\n    context_assets = product_store.list_assets(conversation_id, workspace_id=principal.workspace_id)\n    context_by_id = {asset["id"]: asset for asset in context_assets}\n    selected_asset_ids: list[str] = []\n    for asset_id in req.asset_ids:\n        asset = context_by_id.get(asset_id)\n        if asset is None:\n            try:\n                asset = product_store.get_asset(asset_id, workspace_id=principal.workspace_id)\n            except KeyError:\n                continue\n            context_assets.append(asset)\n            context_by_id[asset["id"]] = asset\n        if asset["id"] not in selected_asset_ids:\n            selected_asset_ids.append(asset["id"])\n\n    context_asset_ids = list(dict.fromkeys(asset["id"] for asset in context_assets))\n    job_payload = {\n        "text": req.content,\n        "provider": req.provider,\n        "history": history,\n        "asset_ids": context_asset_ids,\n    }\n    try:\n        user_message, job = product_store.create_message_job(\n            workspace_id=principal.workspace_id,\n            conversation_id=conversation_id,\n            content=req.content,\n            asset_ids=selected_asset_ids,\n''',
)
replace_once(
    "worldforge/api/app.py",
    '''        payload={"job_id": job["id"], "asset_count": len(selected_assets)},\n''',
    '''        payload={"job_id": job["id"], "asset_count": len(context_asset_ids)},\n''',
)
replace_once(
    "worldforge/product/store.py",
    '''                    payload=json.dumps({"message_id": message_id, "asset_count": len(asset_ids)}, ensure_ascii=False),\n''',
    '''                    payload=json.dumps({"message_id": message_id, "asset_count": len(job_payload.get("asset_ids", asset_ids))}, ensure_ascii=False),\n''',
)

# ---------------------------------------------------------------------------
# 2. Permanent deletion is one database transaction after idempotent object
# cleanup. The approval is never consumed separately, so a DB failure leaves the
# approved request retryable instead of stranding the task.
# ---------------------------------------------------------------------------
replace_between(
    "worldforge/product/store.py",
    "    def consume_approval(self, approval_id: str, *, workspace_id: str, conversation_id: str, action: str) -> bool:\n",
    "    def upsert_feedback(self, *, workspace_id: str, user_id: str, message_id: str, verdict: str, evidence_useful: bool | None = None, human_verified: bool = False, note: str = \"\") -> dict[str, Any]:\n",
    '''    def delete_conversation(\n        self,\n        conversation_id: str,\n        *,\n        workspace_id: str,\n        approval_id: str,\n        request_id: str,\n        user_id: str,\n        asset_object_count: int = 0,\n    ) -> list[dict[str, Any]]:\n        with self.engine.begin() as connection:\n            conversation_row = connection.execute(\n                select(self.conversations)\n                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                .with_for_update()\n            ).first()\n            if not conversation_row:\n                raise KeyError(conversation_id)\n            conversation = self._dict(conversation_row)\n            approval_row = connection.execute(\n                select(self.approval_requests)\n                .where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.workspace_id == workspace_id))\n                .with_for_update()\n            ).first()\n            if not approval_row:\n                raise KeyError(approval_id)\n            approval = self._json_row(approval_row)\n            if (\n                approval["conversation_id"] != conversation_id\n                or approval["action"] != "conversation.delete"\n                or approval["status"] != "approved"\n                or conversation["status"] != "waiting_approval"\n            ):\n                raise ValueError("删除审批尚未通过、已失效或不匹配当前任务")\n            active = connection.execute(\n                select(self.jobs.c.id).where(\n                    and_(\n                        self.jobs.c.workspace_id == workspace_id,\n                        self.jobs.c.conversation_id == conversation_id,\n                        self.jobs.c.status.in_(("queued", "running")),\n                    )\n                ).limit(1)\n            ).first()\n            if active:\n                raise ValueError("执行中的任务需要先停止，才能永久删除")\n\n            asset_rows = connection.execute(\n                select(self.assets).where(\n                    and_(self.assets.c.workspace_id == workspace_id, self.assets.c.conversation_id == conversation_id)\n                )\n            ).fetchall()\n            assets = [self._json_row(row, "meta") for row in asset_rows]\n            message_ids = [\n                row[0]\n                for row in connection.execute(\n                    select(self.messages.c.id).where(self.messages.c.conversation_id == conversation_id)\n                ).fetchall()\n            ]\n            connection.execute(\n                insert(self.audit_logs).values(\n                    workspace_id=workspace_id,\n                    user_id=user_id,\n                    request_id=request_id,\n                    action="conversation.delete",\n                    resource_type="conversation",\n                    resource_id=conversation_id,\n                    payload=json.dumps({"asset_objects": asset_object_count}, ensure_ascii=False),\n                    created_at=time.time(),\n                )\n            )\n            if message_ids:\n                connection.execute(delete(self.result_feedback).where(self.result_feedback.c.message_id.in_(message_ids)))\n            connection.execute(delete(self.product_events).where(and_(self.product_events.c.workspace_id == workspace_id, self.product_events.c.conversation_id == conversation_id)))\n            connection.execute(delete(self.task_events).where(and_(self.task_events.c.workspace_id == workspace_id, self.task_events.c.conversation_id == conversation_id)))\n            connection.execute(delete(self.jobs).where(and_(self.jobs.c.workspace_id == workspace_id, self.jobs.c.conversation_id == conversation_id)))\n            connection.execute(delete(self.messages).where(self.messages.c.conversation_id == conversation_id))\n            connection.execute(delete(self.assets).where(and_(self.assets.c.workspace_id == workspace_id, self.assets.c.conversation_id == conversation_id)))\n            connection.execute(delete(self.approval_requests).where(and_(self.approval_requests.c.workspace_id == workspace_id, self.approval_requests.c.conversation_id == conversation_id)))\n            connection.execute(delete(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)))\n        return assets\n\n''',
)

replace_once(
    "worldforge/product/control.py",
    '''        require_editor(principal)\n        latest = store.latest_job(conversation_id, workspace_id=principal.workspace_id)\n        if latest and latest["status"] in {"queued", "running"}:\n            raise HTTPException(409, "执行中的任务需要先停止，才能请求永久删除")\n        try:\n            approval = store.create_approval(\n''',
    '''        require_editor(principal)\n        try:\n            approval = store.create_approval(\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        audit(request, principal, "approval.request", "approval", approval["id"], {"action": "conversation.delete"})\n''',
    '''        except KeyError as exc:\n            raise HTTPException(404, "任务不存在") from exc\n        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from exc\n        audit(request, principal, "approval.request", "approval", approval["id"], {"action": "conversation.delete"})\n''',
)
replace_once(
    "worldforge/product/control.py",
    '''        if not store.consume_approval(approval_id, workspace_id=principal.workspace_id, conversation_id=conversation_id, action="conversation.delete"):\n            raise HTTPException(409, "删除审批已被使用")\n        store.delete_conversation(conversation_id, workspace_id=principal.workspace_id)\n        audit(request, principal, "conversation.delete", "conversation", conversation_id, {"asset_objects": len(keys)})\n        return {"ok": True}\n''',
    '''        try:\n            store.delete_conversation(\n                conversation_id,\n                workspace_id=principal.workspace_id,\n                approval_id=approval_id,\n                request_id=getattr(request.state, "request_id", "product-control"),\n                user_id=principal.user_id,\n                asset_object_count=len(keys),\n            )\n        except KeyError as exc:\n            raise HTTPException(404, "任务或审批不存在") from exc\n        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from exc\n        return {"ok": True}\n''',
)

# ---------------------------------------------------------------------------
# 3. Product metric name matches the new human-verification semantics.
# This duration measures first delivered result, not human verified state.
# ---------------------------------------------------------------------------
store_path = ROOT / "worldforge/product/store.py"
store_text = store_path.read_text(encoding="utf-8")
store_text = store_text.replace("first_verified_durations", "first_result_durations")
store_text = store_text.replace('"avg_time_to_verified_seconds"', '"avg_time_to_first_result_seconds"')
store_path.write_text(store_text, encoding="utf-8")
replace_once(
    "frontend/app.js",
    '''    ["首次可核验", metrics.avg_time_to_verified_seconds == null ? "—" : `${Math.round(metrics.avg_time_to_verified_seconds)}s`],\n''',
    '''    ["首次交付", metrics.avg_time_to_first_result_seconds == null ? "—" : `${Math.round(metrics.avg_time_to_first_result_seconds)}s`],\n''',
)

# ---------------------------------------------------------------------------
# 4. Browser/backend E2E must fail the process when their report is not green.
# Also make the browser mock preserve server-side task status after feedback and
# preserve prior status through delete rejection.
# ---------------------------------------------------------------------------
replace_once(
    "scripts/product_ui_e2e.py",
    "avg_time_to_verified_seconds:4.2",
    "avg_time_to_first_result_seconds:4.2",
)
replace_once(
    "scripts/product_ui_e2e.py",
    '''    S.approval={id:'approval-e2e',workspace_id:'ws-e2e',conversation_id:deleteRequest[1],action:'conversation.delete',status:'pending',reason:'永久删除任务及其素材需要显式确认',requested_by:'user-e2e',created_at:5,payload:{}};S.conversations[0].status='waiting_approval';return jsonResponse(S.approval);\n''',
    '''    const previousStatus=S.conversations[0].status;S.approval={id:'approval-e2e',workspace_id:'ws-e2e',conversation_id:deleteRequest[1],action:'conversation.delete',status:'pending',reason:'永久删除任务及其素材需要显式确认',requested_by:'user-e2e',created_at:5,payload:{previous_status:previousStatus}};S.conversations[0].status='waiting_approval';return jsonResponse(S.approval);\n''',
)
replace_once(
    "scripts/product_ui_e2e.py",
    '''    const body=JSON.parse(opt.body||'{}');S.approval={...S.approval,status:body.approved?'approved':'rejected',resolved_by:'user-e2e',resolved_at:6};S.conversations[0].status='active';return jsonResponse(S.approval);\n''',
    '''    const body=JSON.parse(opt.body||'{}');S.approval={...S.approval,status:body.approved?'approved':'rejected',resolved_by:'user-e2e',resolved_at:6};S.conversations[0].status=body.approved?'waiting_approval':(S.approval.payload?.previous_status||'active');return jsonResponse(S.approval);\n''',
)
replace_once(
    "scripts/product_ui_e2e.py",
    '''    const body=JSON.parse(opt.body||'{}'),row={message_id:feedbackMatch[1],user_id:'user-e2e',workspace_id:'ws-e2e',conversation_id:'cv-e2e',...body,evidence_useful:body.evidence_useful==null?null:(body.evidence_useful?1:0),human_verified:body.human_verified?1:0,created_at:7,updated_at:7};S.feedback[row.message_id]=row;return jsonResponse(row);\n''',
    '''    const body=JSON.parse(opt.body||'{}'),row={message_id:feedbackMatch[1],user_id:'user-e2e',workspace_id:'ws-e2e',conversation_id:'cv-e2e',...body,evidence_useful:body.evidence_useful==null?null:(body.evidence_useful?1:0),human_verified:body.human_verified?1:0,created_at:7,updated_at:7};S.feedback[row.message_id]=row;S.conversations[0].status=gate().task_status;return jsonResponse(row);\n''',
)
replace_once(
    "scripts/product_ui_e2e.py",
    '''print(json.dumps(report, ensure_ascii=False, indent=2))\n''',
    '''print(json.dumps(report, ensure_ascii=False, indent=2))\nif not report["ok"]:\n    raise SystemExit(1)\n''',
)
replace_once(
    "scripts/product_backend_e2e.py",
    '''    assert data["messages"][-1]["payload"].get("context", {}).get("history_messages") == 2\n    report["checks"]["followup_context"] = True\n''',
    '''    assert data["messages"][-1]["payload"].get("context", {}).get("history_messages") == 2\n    assert data["messages"][-1]["payload"].get("context", {}).get("task_assets") == len(asset_ids)\n    report["checks"]["followup_context"] = True\n''',
)
replace_once(
    "scripts/product_backend_e2e.py",
    '''print(json.dumps(report, ensure_ascii=False, indent=2))\n''',
    '''print(json.dumps(report, ensure_ascii=False, indent=2))\nif not report["ok"]:\n    raise SystemExit(1)\n''',
)

# ---------------------------------------------------------------------------
# 5. Regression tests: metric contract, actual-context count, atomic approved
# deletion and no obsolete consume_approval production path.
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_product_completion.py",
    '''    assert metrics["avg_time_to_verified_seconds"] is not None\n''',
    '''    assert metrics["avg_time_to_first_result_seconds"] is not None\n''',
)
test_path = ROOT / "tests/test_product_completion.py"
test_text = test_path.read_text(encoding="utf-8")
append = r'''


def test_message_accepted_counts_full_task_context(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    first = store.add_asset(conversation["id"], name="a.log", mime="text/plain", path="a", size=1, meta={"kind": "text"}, workspace_id=workspace_id, created_by=user_id)
    second = store.add_asset(conversation["id"], name="b.png", mime="image/png", path="b", size=1, meta={"kind": "image"}, workspace_id=workspace_id, created_by=user_id)
    _, job = store.create_message_job(
        workspace_id=workspace_id,
        conversation_id=conversation["id"],
        content="继续分析",
        asset_ids=[],
        job_payload={"text": "继续分析", "asset_ids": [first["id"], second["id"]]},
    )
    event = store.list_events(conversation["id"], workspace_id=workspace_id)[-1]
    assert event["type"] == "message.accepted"
    assert event["payload"]["asset_count"] == 2
    assert job["payload"]["asset_ids"] == [first["id"], second["id"]]


def test_approved_delete_is_validated_and_deleted_in_one_db_transaction(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    store.add_asset(conversation["id"], name="x.log", mime="text/plain", path="x", size=1, meta={"kind": "text"}, workspace_id=workspace_id, created_by=user_id)
    approval = store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    with pytest.raises(ValueError, match="尚未通过|失效"):
        store.delete_conversation(conversation["id"], workspace_id=workspace_id, approval_id=approval["id"], request_id="req-before", user_id=user_id)
    store.resolve_approval(approval["id"], workspace_id=workspace_id, user_id=user_id, approved=True)
    assets = store.delete_conversation(conversation["id"], workspace_id=workspace_id, approval_id=approval["id"], request_id="req-delete", user_id=user_id, asset_object_count=1)
    assert len(assets) == 1
    with pytest.raises(KeyError):
        store.get_conversation(conversation["id"], workspace_id=workspace_id)
    audits = store.list_audit(workspace_id=workspace_id)
    assert any(row["action"] == "conversation.delete" and row["resource_id"] == conversation["id"] for row in audits)
'''
if "test_message_accepted_counts_full_task_context" not in test_text:
    test_path.write_text(test_text + append, encoding="utf-8")

# README tells the truth about the transaction and persistent multimodal context.
replace_once(
    "README.md",
    '''任务完成与最终结果采用确定性的状态提交：**完成状态、assistant 交付和 `answer.ready` 事件作为同一事务提交**。如果停止先发生，就不会再补写一个“迟到的成功结果”。停止后的执行可以用原任务上下文重新执行；产品没有伪造“暂停”，因为当前分析任务无法保证外部推理调用可以从任意指令点无损续跑。\n''',
    '''任务提交与完成都采用确定性的状态提交：**用户输入、排队 Job 与 `message.accepted` 同事务落库；Job 完成、assistant 交付与 `answer.ready` 也同事务提交**。如果停止先发生，就不会再补写一个“迟到的成功结果”；如果任务已经进入删除确认，也不会再夹进新的执行。停止后的执行可以用原任务上下文重新执行；后续追问会自动继承任务内已有多模态素材。产品没有伪造“暂停”，因为当前分析任务无法保证外部推理调用可以从任意指令点无损续跑。\n''',
)

# Delivery mechanics do not survive the verified product commit.
for relative in [
    "scripts/_final_verification_integrity.py",
    ".github/workflows/product-verification-integrity.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
