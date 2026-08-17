from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one match, got {text.count(old)}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Associate progress with the execution job and make result commit atomic with job completion.
replace_once(
    "worldforge/product/store.py",
    ''' def cancel_job(self,job_id,*,workspace_id):
  with self.engine.begin() as c:c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id)&(self.jobs.c.status.in_((\'queued\',\'running\')))).values(status=\'cancelled\',completed_at=time.time()))
  return self.get_job(job_id,workspace_id=workspace_id)
 def claim_job(self,worker_id):''',
    ''' def cancel_job(self,job_id,*,workspace_id):
  with self.engine.begin() as c:c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id)&(self.jobs.c.status.in_((\'queued\',\'running\')))).values(status=\'cancelled\',completed_at=time.time()))
  return self.get_job(job_id,workspace_id=workspace_id)
 def complete_job_answer(self,job_id,*,workspace_id,content,payload):
  now=time.time();mid=_id('msg')
  with self.engine.begin() as c:
   job=c.execute(select(self.jobs.c.conversation_id).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id)&(self.jobs.c.status=='running'))).first()
   if not job:return None
   cid=job[0];result=c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id)&(self.jobs.c.status=='running')).values(status='completed',completed_at=now,last_error=None))
   if result.rowcount!=1:return None
   message={'id':mid,'conversation_id':cid,'role':'assistant','content':content,'payload':payload,'created_at':now}
   c.execute(insert(self.messages).values(id=mid,conversation_id=cid,role='assistant',content=content,payload=json.dumps(payload,ensure_ascii=False),created_at=now));c.execute(update(self.conversations).where((self.conversations.c.id==cid)&(self.conversations.c.workspace_id==workspace_id)).values(updated_at=now));event_payload={'message':message,'result':payload};c.execute(insert(self.task_events).values(workspace_id=workspace_id,conversation_id=cid,type='answer.ready',payload=json.dumps(event_payload,ensure_ascii=False),created_at=now))
  return message
 def claim_job(self,worker_id):''',
)
replace_once(
    "worldforge/product/store.py",
    ''' def finish_job(self,job_id):
  with self.engine.begin() as c:c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.status=='running')).values(status='completed',completed_at=time.time(),last_error=None))
''',
    "",
)

replace_once(
    "worldforge/api/app.py",
    '''        async def sink(type_, payload):
            ensure_active()
            await _product_emit(
                conversation_id, workspace_id, type_, payload
            )''',
    '''        async def sink(type_, payload):
            ensure_active()
            event_payload = (
                {**payload, "job_id": job_id} if job_id else payload
            )
            await _product_emit(
                conversation_id, workspace_id, type_, event_payload
            )''',
)
replace_once(
    "worldforge/api/app.py",
    '''        ensure_active()
        message = product_store.add_message(
            conversation_id,
            "assistant",
            result["answer"],
            result,
            workspace_id=workspace_id,
        )
        await _product_emit(
            conversation_id,
            workspace_id,
            "answer.ready",
            {"message": message, "result": result},
        )
        return True''',
    '''        if job_id:
            message = product_store.complete_job_answer(
                job_id,
                workspace_id=workspace_id,
                content=result["answer"],
                payload=result,
            )
            return message is not None
        message = product_store.add_message(
            conversation_id,
            "assistant",
            result["answer"],
            result,
            workspace_id=workspace_id,
        )
        await _product_emit(
            conversation_id,
            workspace_id,
            "answer.ready",
            {"message": message, "result": result},
        )
        return True''',
)
replace_once(
    "worldforge/api/app.py",
    '''            completed = await _run_analysis_job(
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
                text=req.content,
                provider_key=req.provider,
                history=history,
                assets=assets,
                job_id=job["id"],
            )
            if completed:
                product_store.finish_job(job["id"])''',
    '''            await _run_analysis_job(
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
                text=req.content,
                provider_key=req.provider,
                history=history,
                assets=assets,
                job_id=job["id"],
            )''',
)

replace_once(
    "worldforge/worker.py",
    '''            completed = await _run_analysis_job(
                conversation_id=job["conversation_id"],
                workspace_id=job["workspace_id"],
                text=str(payload.get("text", "")),
                provider_key=str(payload.get("provider", "auto")),
                history=list(payload.get("history", [])),
                assets=assets,
                job_id=job["id"],
            )
            if completed:
                product_store.finish_job(job["id"])''',
    '''            await _run_analysis_job(
                conversation_id=job["conversation_id"],
                workspace_id=job["workspace_id"],
                text=str(payload.get("text", "")),
                provider_key=str(payload.get("provider", "auto")),
                history=list(payload.get("history", [])),
                assets=assets,
                job_id=job["id"],
            )''',
)

# Frontend restores only the current/latest execution state instead of mixing turns.
replace_once(
    "frontend/app.js",
    '''function renderEventHistory() {
  const progress = state.events.filter(event => event.type === "progress");
  if (progress.length) {
    state.progress = progress.map(event => event.payload);
    renderProgress();
  } else if (!state.messages.length) {
    state.progress = [];
    renderProgress();
  }
  const terminal = [...state.events].reverse().find(event =>
    ["answer.cancelled", "answer.error"].includes(event.type)
  );
  if (terminal?.type === "answer.cancelled") markCancelled();
  if (terminal?.type === "answer.error") {
    setBusy(false);
    $("taskState").textContent = "执行中断";
    $("taskStateHint").textContent = "本次执行没有完成，可以重试或补充要求。";
    document.querySelector(".task-state-card").className = "task-state-card error";
  }
}''',
    '''function renderEventHistory() {
  const job = state.conversation?.job;
  const allProgress = state.events.filter(event => event.type === "progress");
  const taggedProgress = job?.id
    ? allProgress.filter(event => event.payload?.job_id === job.id)
    : [];
  const useTagged = Boolean(
    job?.id && (["queued", "running"].includes(job.status) || taggedProgress.length)
  );
  const progress = useTagged ? taggedProgress : allProgress;
  state.progress = progress.map(event => event.payload);
  renderProgress();

  if (["queued", "running"].includes(job?.status) && !progress.length) {
    $("taskState").textContent = job.status === "queued" ? "等待执行" : "准备执行";
    $("taskStateHint").textContent = "任务已接收，正在准备执行上下文。";
    document.querySelector(".task-state-card").className = "task-state-card running";
    return;
  }
  if (state.busy) return;

  const terminal = [...state.events].reverse().find(event =>
    ["answer.cancelled", "answer.error"].includes(event.type)
  );
  if (terminal?.type === "answer.cancelled") markCancelled();
  if (terminal?.type === "answer.error") {
    setBusy(false);
    $("taskState").textContent = "执行中断";
    $("taskStateHint").textContent = "本次执行没有完成，可以重试或补充要求。";
    document.querySelector(".task-state-card").className = "task-state-card error";
  }
}''',
)
replace_once(
    "frontend/app.js",
    '''  if (event.type === "progress") {
    state.progress.push(event.payload);''',
    '''  if (event.type === "progress") {
    if (state.conversation?.job) state.conversation.job.status = "running";
    state.progress.push(event.payload);''',
)
replace_once(
    "frontend/app.js",
    '''  if (event.type === "answer.ready") {
    setBusy(false);''',
    '''  if (event.type === "answer.ready") {
    if (state.conversation?.job) state.conversation.job.status = "completed";
    setBusy(false);''',
)
replace_once(
    "frontend/app.js",
    '''  if (event.type === "answer.cancelled") {
    markCancelled();
    toast("已停止当前任务");
    return;
  }''',
    '''  if (event.type === "answer.cancelled") {
    const wasBusy = state.busy;
    if (state.conversation?.job) state.conversation.job.status = "cancelled";
    markCancelled();
    if (wasBusy) toast("已停止当前任务");
    return;
  }''',
)
replace_once(
    "frontend/app.js",
    '''  if (event.type === "answer.error") {
    setBusy(false);''',
    '''  if (event.type === "answer.error") {
    if (state.conversation?.job) state.conversation.job.status = "failed";
    setBusy(false);''',
)
replace_once(
    "frontend/app.js",
    '''    setBusy(true, response.job_id || null);
    const serverMessage = response.message;''',
    '''    if (response.job_id) {
      state.conversation.job = {
        id: response.job_id,
        status: response.status === "queued" ? "queued" : "running",
      };
    }
    setBusy(true, response.job_id || null);
    const serverMessage = response.message;''',
)
replace_once(
    "frontend/app.js",
    '''    if (job.status === "cancelled") {
      markCancelled();
      toast("已停止当前任务");
    }''',
    '''    if (job.status === "cancelled") {
      if (state.conversation?.job) state.conversation.job.status = "cancelled";
      markCancelled();
      toast("已停止当前任务");
    }''',
)

# Tests: cancellation cannot be overwritten by a late completion; successful completion is atomic.
replace_once(
    "tests/test_saas.py",
    '''def test_cancelled_job_is_terminal(tmp_path):
    store=ConversationStore(tmp_path/"jobs.db",tmp_path/"assets");conv=store.create_conversation("Job");job=store.enqueue_job(workspace_id=conv["workspace_id"],conversation_id=conv["id"],payload={});store.cancel_job(job["id"],workspace_id=conv["workspace_id"]);store.finish_job(job["id"]);store.fail_job(job["id"],"late failure");assert store.get_job(job["id"],workspace_id=conv["workspace_id"])["status"]=="cancelled"
''',
    '''def test_cancelled_job_is_terminal(tmp_path):
    store=ConversationStore(tmp_path/"jobs.db",tmp_path/"assets");conv=store.create_conversation("Job");job=store.enqueue_job(workspace_id=conv["workspace_id"],conversation_id=conv["id"],payload={});claimed=store.claim_job("test");assert claimed and claimed["id"]==job["id"];store.cancel_job(job["id"],workspace_id=conv["workspace_id"]);assert store.complete_job_answer(job["id"],workspace_id=conv["workspace_id"],content="late",payload={}) is None;store.fail_job(job["id"],"late failure");assert store.get_job(job["id"],workspace_id=conv["workspace_id"])["status"]=="cancelled";assert store.list_messages(conv["id"],workspace_id=conv["workspace_id"])==[]

def test_job_completion_commits_answer_and_event_together(tmp_path):
    store=ConversationStore(tmp_path/"complete.db",tmp_path/"assets");conv=store.create_conversation("Job");job=store.enqueue_job(workspace_id=conv["workspace_id"],conversation_id=conv["id"],payload={});store.claim_job("test");message=store.complete_job_answer(job["id"],workspace_id=conv["workspace_id"],content="done",payload={"evidence":[]});assert message and message["content"]=="done";assert store.get_job(job["id"],workspace_id=conv["workspace_id"])["status"]=="completed";events=store.list_events(conv["id"],workspace_id=conv["workspace_id"]);assert events[-1]["type"]=="answer.ready";assert events[-1]["payload"]["message"]["id"]==message["id"]
''',
)

# Browser mock mirrors job-tagged progress so reload rendering is covered by the same data contract.
for percent in (12, 36, 58, 78, 100):
    replace_once(
        "scripts/product_ui_e2e.py",
        f"percent:{percent}}}}}",
        f"percent:{percent},job_id:'job-e2e'}}}}",
    )

Path(".github/workflows/finalize-control.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
