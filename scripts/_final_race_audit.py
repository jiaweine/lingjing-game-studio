from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"start marker not found in {path}: {start!r}")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"end marker not found in {path}: {end!r}")
    target.write_text(text[:left] + replacement + text[right:], encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found: {path}: {old[:180]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# A user turn is one durable product action: user message, queued job and the
# accepted event commit together. A competing request cannot leave a message
# without an execution, and a delete approval cannot slip between them.
store_path = "worldforge/product/store.py"
store = (ROOT / store_path).read_text(encoding="utf-8")
anchor = "    def get_message(self, message_id: str, *, workspace_id: str) -> dict[str, Any]:\n"
if "def create_message_job(" not in store:
    method = '''    def create_message_job(\n        self,\n        *,\n        workspace_id: str,\n        conversation_id: str,\n        content: str,\n        asset_ids: list[str],\n        job_payload: dict[str, Any],\n        title_if_first: str | None = None,\n    ) -> tuple[dict[str, Any], dict[str, Any]]:\n        now, message_id, job_id = time.time(), _id("msg"), _id("job")\n        message_payload = {"asset_ids": list(asset_ids)}\n        with self.engine.begin() as connection:\n            row = connection.execute(\n                select(self.conversations)\n                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                .with_for_update()\n            ).first()\n            if not row:\n                raise KeyError(conversation_id)\n            conversation = self._dict(row)\n            if conversation.get("archived_at") is not None:\n                raise ValueError("已归档任务需要先恢复，才能继续执行")\n            if conversation["status"] == "waiting_approval":\n                raise ValueError("删除确认处理中，不能继续执行")\n            active = connection.execute(\n                select(self.jobs.c.id).where(\n                    and_(\n                        self.jobs.c.workspace_id == workspace_id,\n                        self.jobs.c.conversation_id == conversation_id,\n                        self.jobs.c.status.in_(("queued", "running")),\n                    )\n                ).limit(1)\n            ).first()\n            if active:\n                raise ValueError("当前任务已有执行正在进行")\n            message = {\n                "id": message_id,\n                "conversation_id": conversation_id,\n                "role": "user",\n                "content": content,\n                "payload": message_payload,\n                "created_at": now,\n            }\n            connection.execute(\n                insert(self.messages).values(\n                    id=message_id,\n                    conversation_id=conversation_id,\n                    role="user",\n                    content=content,\n                    payload=json.dumps(message_payload, ensure_ascii=False),\n                    created_at=now,\n                )\n            )\n            connection.execute(\n                insert(self.jobs).values(\n                    id=job_id,\n                    workspace_id=workspace_id,\n                    conversation_id=conversation_id,\n                    status="queued",\n                    payload=json.dumps(job_payload, ensure_ascii=False),\n                    attempts=0,\n                    created_at=now,\n                    available_at=now,\n                )\n            )\n            values: dict[str, Any] = {"status": "active", "updated_at": now}\n            if title_if_first:\n                values["title"] = title_if_first.strip().replace("\\n", " ")[:36] or "新的研发任务"\n            connection.execute(\n                update(self.conversations)\n                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                .values(**values)\n            )\n            connection.execute(\n                insert(self.task_events).values(\n                    workspace_id=workspace_id,\n                    conversation_id=conversation_id,\n                    type="message.accepted",\n                    payload=json.dumps({"message_id": message_id, "asset_count": len(asset_ids)}, ensure_ascii=False),\n                    created_at=now,\n                )\n            )\n        return message, self.get_job(job_id, workspace_id=workspace_id)\n\n'''
    (ROOT / store_path).write_text(store.replace(anchor, method + anchor, 1), encoding="utf-8")

# Retry and other internal enqueue paths use the same task-row serialization.
replace_between(
    store_path,
    "    def enqueue_job(self, *, workspace_id: str, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:\n",
    "    def get_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:\n",
    '''    def enqueue_job(self, *, workspace_id: str, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:\n        job_id, now = _id("job"), time.time()\n        with self.engine.begin() as connection:\n            row = connection.execute(\n                select(self.conversations)\n                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                .with_for_update()\n            ).first()\n            if not row:\n                raise KeyError(conversation_id)\n            conversation = self._dict(row)\n            if conversation.get("archived_at") is not None:\n                raise ValueError("已归档任务需要先恢复，才能继续执行")\n            if conversation["status"] == "waiting_approval":\n                raise ValueError("删除确认处理中，不能继续执行")\n            active = connection.execute(\n                select(self.jobs.c.id).where(\n                    and_(\n                        self.jobs.c.workspace_id == workspace_id,\n                        self.jobs.c.conversation_id == conversation_id,\n                        self.jobs.c.status.in_(("queued", "running")),\n                    )\n                ).limit(1)\n            ).first()\n            if active:\n                raise ValueError("当前任务已有执行正在进行")\n            connection.execute(insert(self.jobs).values(id=job_id, workspace_id=workspace_id, conversation_id=conversation_id, status="queued", payload=json.dumps(payload, ensure_ascii=False), attempts=0, created_at=now, available_at=now))\n            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="active", updated_at=now))\n        return self.get_job(job_id, workspace_id=workspace_id)\n\n''',
)

# Asset metadata registration serializes against delete approval. The API cleans
# any already-uploaded object if this final registration loses the race.
replace_between(
    store_path,
    "    def add_asset(self, conversation_id: str | None, *, name: str, mime: str, path: str, size: int, meta: dict[str, Any], workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID, storage_backend: str = \"local\") -> dict[str, Any]:\n",
    "    def get_asset(self, asset_id: str, *, workspace_id: str = DEMO_WORKSPACE_ID) -> dict[str, Any]:\n",
    '''    def add_asset(self, conversation_id: str | None, *, name: str, mime: str, path: str, size: int, meta: dict[str, Any], workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID, storage_backend: str = "local") -> dict[str, Any]:\n        asset_id, now = _id("asset"), time.time()\n        with self.engine.begin() as connection:\n            if conversation_id:\n                row = connection.execute(\n                    select(self.conversations)\n                    .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                    .with_for_update()\n                ).first()\n                if not row:\n                    raise KeyError(conversation_id)\n                conversation = self._dict(row)\n                if conversation.get("archived_at") is not None:\n                    raise ValueError("已归档任务需要先恢复，才能添加素材")\n                if conversation["status"] == "waiting_approval":\n                    raise ValueError("删除确认处理中，不能添加素材")\n            connection.execute(insert(self.assets).values(id=asset_id, workspace_id=workspace_id, created_by=created_by, conversation_id=conversation_id, name=name, mime=mime, path=path, storage_backend=storage_backend, size=size, meta=json.dumps(meta, ensure_ascii=False), created_at=now))\n            if conversation_id:\n                connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(updated_at=now))\n        return self.get_asset(asset_id, workspace_id=workspace_id)\n\n''',
)

# Approval acquisition locks the same task row and refuses to begin while an
# execution is active. This closes approval-vs-enqueue check/use races.
replace_between(
    store_path,
    "    def create_approval(self, *, workspace_id: str, conversation_id: str, action: str, requested_by: str, reason: str = \"\", payload: dict[str, Any] | None = None) -> dict[str, Any]:\n",
    "    def get_approval(self, approval_id: str, *, workspace_id: str) -> dict[str, Any]:\n",
    '''    def create_approval(self, *, workspace_id: str, conversation_id: str, action: str, requested_by: str, reason: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:\n        approval_id, now = _id("approval"), time.time()\n        with self.engine.begin() as connection:\n            row = connection.execute(\n                select(self.conversations)\n                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))\n                .with_for_update()\n            ).first()\n            if not row:\n                raise KeyError(conversation_id)\n            conversation = self._dict(row)\n            existing = connection.execute(\n                select(self.approval_requests).where(\n                    and_(\n                        self.approval_requests.c.workspace_id == workspace_id,\n                        self.approval_requests.c.conversation_id == conversation_id,\n                        self.approval_requests.c.action == action,\n                        self.approval_requests.c.status.in_(("pending", "approved")),\n                    )\n                ).order_by(self.approval_requests.c.created_at.desc()).limit(1)\n            ).first()\n            if existing:\n                return self._json_row(existing)\n            if action == "conversation.delete":\n                active = connection.execute(\n                    select(self.jobs.c.id).where(\n                        and_(\n                            self.jobs.c.workspace_id == workspace_id,\n                            self.jobs.c.conversation_id == conversation_id,\n                            self.jobs.c.status.in_(("queued", "running")),\n                        )\n                    ).limit(1)\n                ).first()\n                if active:\n                    raise ValueError("执行中的任务需要先停止，才能请求永久删除")\n            approval_payload = dict(payload or {})\n            approval_payload.setdefault("previous_status", conversation["status"])\n            connection.execute(insert(self.approval_requests).values(id=approval_id, workspace_id=workspace_id, conversation_id=conversation_id, action=action, status="pending", reason=reason, payload=json.dumps(approval_payload, ensure_ascii=False), requested_by=requested_by, created_at=now))\n            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="waiting_approval", updated_at=now))\n        return self.get_approval(approval_id, workspace_id=workspace_id)\n\n''',
)

# API user-turn path now calls the atomic store operation. It still resolves and
# validates workspace-owned assets before creating durable task state.
app_path = "worldforge/api/app.py"
replace_between(
    app_path,
    '@app.post("/api/conversations/{conversation_id}/messages")\n',
    '@app.get("/api/jobs/{job_id}")\n',
    '''@app.post("/api/conversations/{conversation_id}/messages")\nasync def conversation_message(\n    conversation_id: str,\n    req: ChatRequest,\n    background_tasks: BackgroundTasks,\n    request: Request,\n    principal: Principal = Depends(require_editor),\n):\n    try:\n        conversation = product_store.get_conversation(conversation_id, workspace_id=principal.workspace_id)\n    except KeyError:\n        raise HTTPException(404, "任务不存在")\n    if conversation.get("archived_at") is not None:\n        raise HTTPException(409, "已归档任务需要先恢复，才能继续执行")\n    if conversation["status"] == "waiting_approval":\n        raise HTTPException(409, "删除确认处理中，不能继续执行")\n\n    history = product_store.list_messages(conversation_id, workspace_id=principal.workspace_id)\n    assets = product_store.list_assets(conversation_id, workspace_id=principal.workspace_id)\n    asset_by_id = {asset["id"]: asset for asset in assets}\n    selected_assets = []\n    for asset_id in req.asset_ids:\n        asset = asset_by_id.get(asset_id)\n        if asset is None:\n            try:\n                asset = product_store.get_asset(asset_id, workspace_id=principal.workspace_id)\n            except KeyError:\n                continue\n        selected_assets.append(asset)\n\n    job_payload = {\n        "text": req.content,\n        "provider": req.provider,\n        "history": history,\n        "asset_ids": [asset["id"] for asset in selected_assets],\n    }\n    try:\n        user_message, job = product_store.create_message_job(\n            workspace_id=principal.workspace_id,\n            conversation_id=conversation_id,\n            content=req.content,\n            asset_ids=[asset["id"] for asset in selected_assets],\n            job_payload=job_payload,\n            title_if_first=req.content if not history else None,\n        )\n    except KeyError as exc:\n        raise HTTPException(404, "任务不存在") from exc\n    except ValueError as exc:\n        raise HTTPException(409, str(exc)) from exc\n\n    product_store.add_audit(\n        request_id=request.state.request_id,\n        action="message.create",\n        workspace_id=principal.workspace_id,\n        user_id=principal.user_id,\n        resource_type="conversation",\n        resource_id=conversation_id,\n        payload={"job_id": job["id"], "asset_count": len(selected_assets)},\n    )\n\n    if settings.queue_mode == "external":\n        return {"status": "queued", "message": user_message, "job_id": job["id"]}\n    await _schedule_product_job(job, background_tasks, principal)\n    return {"status": "accepted", "message": user_message, "job_id": job["id"]}\n\n\n''',
)

# If the final DB registration is rejected (for example because deletion became
# pending during a long video upload), remove every object already written.
replace_once(
    app_path,
    '''    row = product_store.add_asset(\n        conversation_id,\n        name=filename,\n        mime=mime,\n        path=object_key,\n        size=size,\n        meta=meta,\n        workspace_id=principal.workspace_id,\n        created_by=principal.user_id,\n        storage_backend=storage.name,\n    )\n''',
    '''    try:\n        row = product_store.add_asset(\n            conversation_id,\n            name=filename,\n            mime=mime,\n            path=object_key,\n            size=size,\n            meta=meta,\n            workspace_id=principal.workspace_id,\n            created_by=principal.user_id,\n            storage_backend=storage.name,\n        )\n    except Exception as exc:\n        for key in [object_key, *frame_keys]:\n            try:\n                storage.delete(key)\n            except Exception:\n                logger.exception("failed to clean rejected asset upload", extra={"object_key": key})\n        if isinstance(exc, ValueError):\n            raise HTTPException(409, str(exc)) from exc\n        raise\n''',
)

# Regression coverage for atomic task acceptance and approval/execution exclusion.
test_path = ROOT / "tests/test_product_completion.py"
test_text = test_path.read_text(encoding="utf-8")
append = r'''


def test_user_turn_is_atomic_and_does_not_leave_orphan_message(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    message, job = store.create_message_job(
        workspace_id=workspace_id,
        conversation_id=conversation["id"],
        content="检查首帧卡顿",
        asset_ids=[],
        job_payload={"text": "检查首帧卡顿", "asset_ids": []},
        title_if_first="检查首帧卡顿",
    )
    assert message["role"] == "user"
    assert job["status"] == "queued"
    assert [event["type"] for event in store.list_events(conversation["id"], workspace_id=workspace_id)] == ["message.accepted"]
    with pytest.raises(ValueError, match="已有执行"):
        store.create_message_job(
            workspace_id=workspace_id,
            conversation_id=conversation["id"],
            content="重复提交",
            asset_ids=[],
            job_payload={"text": "重复提交", "asset_ids": []},
        )
    assert [row["content"] for row in store.list_messages(conversation["id"], workspace_id=workspace_id)] == ["检查首帧卡顿"]


def test_delete_approval_and_execution_are_mutually_exclusive(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id, user_id = owner["workspace_id"], owner["user_id"]
    conversation = store.create_conversation(workspace_id=workspace_id, created_by=user_id)
    job = store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "running"})
    assert job["status"] == "queued"
    with pytest.raises(ValueError, match="先停止"):
        store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    store.cancel_job(job["id"], workspace_id=workspace_id)
    approval = store.create_approval(workspace_id=workspace_id, conversation_id=conversation["id"], action="conversation.delete", requested_by=user_id)
    assert approval["status"] == "pending"
    with pytest.raises(ValueError, match="删除确认"):
        store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "must not start"})
'''
if "test_user_turn_is_atomic_and_does_not_leave_orphan_message" not in test_text:
    test_path.write_text(test_text + append, encoding="utf-8")

for relative in [
    "scripts/_final_race_audit.py",
    ".github/workflows/product-final-audit.yml",
]:
    (ROOT / relative).unlink(missing_ok=True)
