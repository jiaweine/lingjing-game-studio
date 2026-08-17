from __future__ import annotations

import json
import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
README_ASSETS = ROOT / "docs" / "assets" / "readme"
OUT.mkdir(exist_ok=True)
README_ASSETS.mkdir(parents=True, exist_ok=True)

html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
css = (ROOT / "frontend/app.css").read_text(encoding="utf-8")
js = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
html = html.replace(
    '<link rel="stylesheet" href="/assets/app.css" />',
    f"<style>{css}</style>",
)
html = html.replace('<script type="module" src="/assets/app.js"></script>', "")

mock_script = r'''
<script>
window.__E2E={
  session:false,conversations:[],assets:[],sockets:[],assetData:{},assetSeq:0,
  timers:[],feedback:{},invites:[],approval:null,productEvents:[],jobId:null
};
const jsonResponse=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}}));
const kindFor=(file)=>file.type.startsWith('image/')?'image':file.type.startsWith('video/')?'video':file.type.startsWith('audio/')?'audio':(file.type.startsWith('text/')||/\.(log|txt|json|csv|ya?ml|xml)$/i.test(file.name))?'text':'file';
const fixAssetImages=()=>document.querySelectorAll('img').forEach(img=>{for(const [id,src] of Object.entries(window.__E2E.assetData)){if(img.getAttribute('src')?.includes(id)){img.src=src;break}}});
new MutationObserver(fixAssetImages).observe(document.documentElement,{childList:true,subtree:true});
const member=()=>({id:'user-e2e',email:'qa@studio.com',name:'QA Lead',status:'active',role:'owner',created_at:1});
const metrics=()=>({task_count:1,active_tasks:1,first_task_completion_rate:1,avg_time_to_verified_seconds:4.2,interruption_rate:.5,failure_rate:0,recovery_rate:1,continuation_rate:0,manual_intervention_rate:1,evidence_open_rate:1,result_adoption_rate:1,human_verified_feedback_rate:Object.values(window.__E2E.feedback).some(x=>x.human_verified)?1:0});
const gate=()=>{
  const rows=Object.values(window.__E2E.feedback),verified=rows.filter(x=>x.human_verified),incorrect=rows.filter(x=>x.verdict==='incorrect');
  const approved=verified.length>0&&incorrect.length===0;
  return {approved,human_verified:verified.length,correct:rows.filter(x=>x.verdict==='correct').length,incorrect:incorrect.length,feedback_count:rows.length,reason:approved?'已有人类验证且无错误反馈':'需要人工验证，且不能存在错误反馈'};
};
const answer=()=>{
  const S=window.__E2E,log=S.assets.find(a=>a.meta?.kind==='text');
  return {id:'msg-answer',conversation_id:'cv-e2e',role:'assistant',content:'### 结论\n异常具备稳定复现条件，核心窗口集中在第二阶段进入后的防御资源真空期。\n\n### 关键发现\n1. 60 秒附近 shield 归零后，承伤在极短时间内连续抬升。\n2. 同条件再次核验时，异常路径可以重复出现。\n3. 当前证据更支持资源覆盖与技能时序叠加，而不是单一伤害参数失控。\n\n### 下一步\n优先核对减伤持续时间、Boss 技能冷却与资源恢复窗口，再用同一条件做修复后回归。',created_at:3,payload:{
    evidence:[{id:'ev-log',type:'text',label:'战斗日志',title:'battle.log · 关键时间窗',asset_id:log?.id},{id:'ev-replay',type:'replay',label:'复核结果',title:'同条件复核 · 异常路径再次出现'}],
    deliverables:[
      {type:'reproduction_card',title:'问题复现卡',summary:'把异常窗口固化成可交接的复现入口。',items:['使用当前素材作为复现基线','锁定异常阶段与资源窗口','修复后按同条件再次执行'],evidence_ids:['ev-log','ev-replay']},
      {type:'regression_checklist',title:'回归检查清单',summary:'覆盖触发条件、邻近条件与修复后复核。',items:['原触发条件不再复现','相邻时间窗无新增异常','资源与伤害变化符合预期'],evidence_ids:['ev-log','ev-replay']},
      {type:'evidence_pack',title:'证据包',summary:'保留本次判断使用的素材索引与主动复核结果。',items:['battle.log · 关键时间窗','同条件复核 · 异常路径再次出现'],evidence_ids:['ev-log','ev-replay']}
    ],
    suggestions:['把异常时间段单独展开','继续核对伤害配置','生成回归测试清单']
  }};
};
const clearTimers=()=>{window.__E2E.timers.forEach(clearTimeout);window.__E2E.timers=[]};
const emitRun=(jobId,startId=1)=>{
  const S=window.__E2E,c=S.conversations[0],a=answer();S.jobId=jobId;
  const events=[
    {id:startId,type:'progress',payload:{step:'读取任务上下文',detail:'已关联当前素材，正在建立执行上下文',percent:12,job_id:jobId}},
    {id:startId+1,type:'progress',payload:{step:'定位异常窗口',detail:'正在锁定掉血变化与资源变化发生的时间段',percent:36,job_id:jobId}},
    {id:startId+2,type:'progress',payload:{step:'尝试稳定复现',detail:'正在用一致初始条件检查不同触发路径',percent:58,job_id:jobId}},
    {id:startId+3,type:'progress',payload:{step:'交叉核对证据',detail:'正在对照截图、日志与重复结果',percent:78,job_id:jobId}},
    {id:startId+4,type:'progress',payload:{step:'验证结论',detail:'已完成复核，正在整理证据与下一步',percent:100,job_id:jobId}},
    {id:startId+5,type:'answer.ready',payload:{job_id:jobId,message:a,result:a.payload}}
  ];
  events.forEach((ev,i)=>S.timers.push(setTimeout(()=>{
    if(ev.type==='answer.ready'){c.messages=[...(c.messages||[]).filter(m=>m.role!=='assistant'),a];c.status='verified';c.job={id:jobId,status:'completed'};}
    S.sockets.forEach(ws=>ws.onmessage&&ws.onmessage({data:JSON.stringify(ev)}));
  },120+i*250)));
};
window.fetch=async function(url,opt={}){
  const raw=String(url),method=String(opt.method||'GET').toUpperCase(),S=window.__E2E;
  const U=new URL(raw,location.href),u=U.pathname;
  if(u==='/api/config') return jsonResponse({auth_required:true});
  if(u==='/api/auth/me') return S.session?jsonResponse(S.session):jsonResponse({detail:'请先登录'},401);
  if(u==='/api/auth/register'&&method==='POST'){
    S.session={authenticated:true,user:{id:'user-e2e',email:'qa@studio.com',name:'QA Lead',role:'owner'},workspace:{id:'ws-e2e',name:'Nebula Studio',slug:'nebula-studio',plan:'team'},access_token:'mock',token_type:'bearer'};
    return jsonResponse(S.session);
  }
  if(u==='/api/auth/login'&&method==='POST') return jsonResponse(S.session||{detail:'邮箱或密码错误'},S.session?200:401);
  if(u==='/api/auth/logout'){S.session=false;return jsonResponse({ok:true})}
  if(u==='/api/health') return jsonResponse({status:'ok'});
  if(u==='/api/workspaces'&&method==='GET') return jsonResponse([{id:'ws-e2e',name:'Nebula Studio',slug:'nebula-studio',plan:'team',role:'owner',created_at:1}]);
  if(u==='/api/workspace/members'&&method==='GET') return jsonResponse([member()]);
  if(u==='/api/workspace/invites'&&method==='GET') return jsonResponse(S.invites);
  if(u==='/api/workspace/invites'&&method==='POST'){
    const body=JSON.parse(opt.body||'{}'),invite={id:'invite-e2e',workspace_id:'ws-e2e',token:'invite-token-e2e',email:body.email||null,role:body.role||'member',status:'pending',created_by:'user-e2e',created_at:4,expires_at:9999999999};S.invites=[invite,...S.invites];return jsonResponse(invite);
  }
  if(u.startsWith('/api/workspace/invites/')&&method==='DELETE'){S.invites=S.invites.filter(x=>x.id!==u.split('/').pop());return jsonResponse({status:'revoked'})}
  if(u==='/api/metrics') return jsonResponse(metrics());
  if(u==='/api/quality-gate') return jsonResponse(gate());
  if(u==='/api/product-events'&&method==='POST'){S.productEvents.push(JSON.parse(opt.body||'{}'));return jsonResponse({ok:true})}
  if(u==='/api/conversations'&&method==='GET'){
    const archived=U.searchParams.get('archived')==='true',q=(U.searchParams.get('q')||'').toLowerCase();
    return jsonResponse(S.conversations.filter(c=>(Boolean(c.archived_at)===archived)&&(!q||c.title.toLowerCase().includes(q))));
  }
  if(u==='/api/conversations'&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');
    const c={id:'cv-e2e',workspace_id:'ws-e2e',created_by:'user-e2e',assigned_to:'user-e2e',title:body.title||'战斗问题复现',scene:body.scene||'battle_review',status:'active',pinned:0,archived_at:null,created_at:1,updated_at:1,messages:[],assets:[],events:[],job:null};
    S.conversations=[c];return jsonResponse(c);
  }
  const controlMatch=u.match(/^\/api\/conversations\/([^/]+)\/control$/);
  if(controlMatch&&method==='GET') return jsonResponse({approvals:S.approval?[S.approval]:[],feedback:Object.values(S.feedback),quality_gate:gate()});
  const archiveMatch=u.match(/^\/api\/conversations\/([^/]+)\/(archive|restore)$/);
  if(archiveMatch&&method==='POST'){
    const c=S.conversations.find(x=>x.id===archiveMatch[1]);
    c.archived_at=archiveMatch[2]==='archive'?Date.now()/1000:null;return jsonResponse(c);
  }
  const deleteRequest=u.match(/^\/api\/conversations\/([^/]+)\/delete-request$/);
  if(deleteRequest&&method==='POST'){
    S.approval={id:'approval-e2e',workspace_id:'ws-e2e',conversation_id:deleteRequest[1],action:'conversation.delete',status:'pending',reason:'永久删除任务及其素材需要显式确认',requested_by:'user-e2e',created_at:5,payload:{}};S.conversations[0].status='waiting_approval';return jsonResponse(S.approval);
  }
  const approvalResolve=u.match(/^\/api\/approvals\/([^/]+)\/resolve$/);
  if(approvalResolve&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');S.approval={...S.approval,status:body.approved?'approved':'rejected',resolved_by:'user-e2e',resolved_at:6};S.conversations[0].status='active';return jsonResponse(S.approval);
  }
  const convMatch=u.match(/^\/api\/conversations\/([^/]+)$/);
  if(convMatch&&method==='GET'){
    const c=S.conversations.find(x=>x.id===convMatch[1]);
    return c?jsonResponse({...c,messages:c.messages||[],assets:S.assets,events:c.events||[]}):jsonResponse({detail:'任务不存在'},404);
  }
  if(convMatch&&method==='PATCH'){
    const c=S.conversations.find(x=>x.id===convMatch[1]),body=JSON.parse(opt.body||'{}');Object.assign(c,body,{updated_at:Date.now()/1000});return jsonResponse(c);
  }
  if(u==='/api/assets'&&method==='POST'){
    const file=opt.body instanceof FormData?opt.body.get('file'):null,kind=kindFor(file),id='asset-'+(++S.assetSeq),meta={kind};
    if(kind==='image'){meta.width=1280;meta.height=720;S.assetData[id]=URL.createObjectURL(file)}
    if(kind==='audio') meta.duration=18.4;
    if(kind==='text') Object.assign(meta,{lines:3,preview:'59.8 damage=120\n60.0 shield=0\n60.2 hp=18'});
    const a={id,workspace_id:'ws-e2e',conversation_id:'cv-e2e',name:file?.name||'asset',mime:file?.type||'application/octet-stream',size:file?.size||42,meta,created_at:2,url:'/api/assets/'+id+'/file'};S.assets.push(a);return jsonResponse(a);
  }
  if(u.match(/^\/api\/conversations\/[^/]+\/messages$/)&&method==='POST'){
    const body=JSON.parse(opt.body||'{}'),c=S.conversations[0];
    const user={id:'msg-user',conversation_id:'cv-e2e',role:'user',content:body.content,created_at:2.5,payload:{asset_ids:body.asset_ids||[]}};
    c.messages=[user];c.job={id:'job-e2e',status:'queued'};c.status='active';clearTimers();emitRun('job-e2e',1);
    return jsonResponse({status:'queued',message:user,job_id:'job-e2e'});
  }
  if(u==='/api/jobs/job-e2e/cancel'&&method==='POST'){clearTimers();S.conversations[0].job={id:'job-e2e',status:'cancelled'};S.conversations[0].status='stopped';return jsonResponse({id:'job-e2e',status:'cancelled'})}
  if(u==='/api/jobs/job-e2e/retry'&&method==='POST'){clearTimers();S.conversations[0].job={id:'job-e2e-retry',status:'queued'};S.conversations[0].status='active';emitRun('job-e2e-retry',20);return jsonResponse({status:'queued',job_id:'job-e2e-retry'})}
  const feedbackMatch=u.match(/^\/api\/messages\/([^/]+)\/feedback$/);
  if(feedbackMatch&&method==='PUT'){
    const body=JSON.parse(opt.body||'{}'),row={message_id:feedbackMatch[1],user_id:'user-e2e',workspace_id:'ws-e2e',conversation_id:'cv-e2e',...body,evidence_useful:body.evidence_useful==null?null:(body.evidence_useful?1:0),human_verified:body.human_verified?1:0,created_at:7,updated_at:7};S.feedback[row.message_id]=row;return jsonResponse(row);
  }
  return jsonResponse({detail:'not mocked: '+method+' '+u},404);
};
class MockWebSocket{
  constructor(url){this.url=url;this.readyState=1;window.__E2E.sockets.push(this);setTimeout(()=>this.onopen&&this.onopen(),0)}
  close(){this.readyState=3;window.__E2E.sockets=window.__E2E.sockets.filter(x=>x!==this);this.onclose&&this.onclose()}
}
window.WebSocket=MockWebSocket;
</script>
'''

html = html.replace("</body>", mock_script + f"<script>{js}</script></body>")

report = {"checks": {}, "errors": [], "screenshots": {}}
with sync_playwright() as playwright:
    executable = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
    if executable:
        launch_args["executable_path"] = executable
    browser = playwright.chromium.launch(**launch_args)
    page = browser.new_page(viewport={"width": 1920, "height": 1200}, device_scale_factor=2)
    page.on("pageerror", lambda exc: report["errors"].append(str(exc)))
    page.set_content(html, wait_until="load")

    def shot(name):
        path = README_ASSETS / name
        page.screenshot(path=str(path), full_page=False)
        with Image.open(path) as image:
            report["screenshots"][name] = list(image.size)
            assert image.size == (3840, 2400)

    page.wait_for_selector("#authModal:not([hidden])")
    shot("auth.png")
    report["checks"]["auth_gate"] = True

    page.click('[data-auth-tab="register"]')
    page.fill("#registerName", "QA Lead")
    page.fill("#registerWorkspace", "Nebula Studio")
    page.fill("#registerEmail", "qa@studio.com")
    page.fill("#registerPassword", "a-strong-password-123")
    page.click("#registerForm .auth-submit")
    page.wait_for_function("document.querySelector('#authModal').hidden===true && document.querySelector('#workspaceName').textContent==='Nebula Studio'")
    shot("workspace-empty.png")
    report["checks"]["register_workspace"] = True
    report["checks"]["task_lifecycle_controls"] = all(page.locator(selector).count() == 1 for selector in ["#taskSearch", "#renameTaskBtn", "#pinTaskBtn", "#archiveTaskBtn", "#deleteTaskBtn"])

    frame = Image.new("RGB", (1280, 720), "#10151d")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((50, 50, 1230, 670), outline="#8b7cff", width=8)
    draw.rectangle((220, 180, 1060, 540), fill="#252b37")
    draw.ellipse((560, 280, 720, 440), fill="#8b7cff")
    buffer = BytesIO()
    frame.save(buffer, format="PNG")

    page.set_input_files("#fileInput", [
        {"name": "boss_frame.png", "mimeType": "image/png", "buffer": buffer.getvalue()},
        {"name": "phase2.wav", "mimeType": "audio/wav", "buffer": b"RIFF" + b"0" * 320},
        {"name": "battle.log", "mimeType": "text/plain", "buffer": b"59.8 damage=120\n60.0 shield=0\n60.2 hp=18"},
    ])
    page.wait_for_function("document.querySelectorAll('#pendingAssets .pending-asset').length===3")
    shot("upload.png")
    report["checks"]["upload_interaction"] = True

    page.fill("#messageInput", "复现这个 Boss 第二阶段偶发秒杀问题，找出稳定触发条件，验证后给我回归清单。")
    page.click("#sendBtn")
    page.wait_for_function("document.querySelector('#taskPercent').textContent==='36%'", timeout=10000)
    shot("task-running.png")
    report["checks"]["running_state"] = True
    report["checks"]["stop_control"] = page.locator("#stopBtn").is_visible()
    report["checks"]["no_dead_controls"] = page.locator("#providerBtn,#providerModal,#settingsBtn,#moreBtn").count() == 0

    # Stop is a real terminal state, then retry reuses persisted task context.
    page.click("#stopBtn")
    page.wait_for_function("document.querySelector('#taskState').textContent==='已停止' && !document.querySelector('#retryTaskBtn').hidden")
    report["checks"]["stop_terminal_state"] = True
    page.click("#retryTaskBtn")
    page.wait_for_function("document.querySelector('#thinkingStep').textContent==='重新执行'")
    report["checks"]["retry_control"] = True

    page.wait_for_function("document.querySelectorAll('#messageList .msg.assistant').length===1", timeout=12000)
    page.wait_for_function("document.querySelector('#taskPercent').textContent==='100%'")
    shot("workspace.png")
    shot("cover.png")
    report["checks"]["realtime_result"] = True
    report["checks"]["evidence_panel"] = page.locator("#evidenceList .evidence-card").count() == 2
    report["checks"]["suggestions"] = page.locator("#suggestionList button").count() == 3

    page.click('[data-panel="deliverables"]')
    page.wait_for_function("document.querySelector('#panel-deliverables').classList.contains('active')")
    report["checks"]["structured_deliverables"] = page.locator("#deliverableList .deliverable-card").count() == 3

    page.click('[data-panel="evidence"]')
    page.wait_for_function("document.querySelector('#panel-evidence').classList.contains('active')")
    shot("evidence.png")

    # Structured result feedback and the human quality gate are real API-backed state.
    page.click('[data-feedback="correct"]')
    page.click('[data-evidence-useful]')
    page.click('[data-human-verify]')
    page.wait_for_function("document.querySelector('[data-feedback=\"correct\"]').classList.contains('active') && document.querySelector('[data-evidence-useful]').classList.contains('active') && document.querySelector('[data-human-verify]').classList.contains('active')")
    report["checks"]["result_feedback"] = True

    page.click('[data-panel="team"]')
    page.wait_for_function("document.querySelector('#panel-team').classList.contains('active') && document.querySelectorAll('#memberList .member-row').length===1")
    page.wait_for_function("document.querySelector('#qualityGate').textContent.includes('人工质量门已通过')")
    report["checks"]["team_collaboration"] = page.locator("#assigneeSelect").count() == 1 and page.locator("#inviteForm").is_visible()
    report["checks"]["quality_gate"] = "人工质量门已通过" in page.locator("#qualityGate").inner_text()
    report["checks"]["product_metrics"] = page.locator("#metricGrid > div").count() == 8

    page.fill("#inviteEmail", "designer@studio.com")
    page.select_option("#inviteRole", "member")
    page.click("#inviteForm button[type=submit]")
    page.wait_for_function("document.querySelectorAll('#inviteList .invite-row').length===1")
    report["checks"]["workspace_invite"] = True

    page.click('[data-panel="assets"]')
    page.wait_for_function("document.querySelector('#panel-assets').classList.contains('active')")
    report["checks"]["multimodal_assets"] = page.locator("#assetList .asset-card").count() == 3
    shot("multimodal.png")

    # Pin, archive and restore exercise the customer-visible lifecycle endpoints.
    page.click("#pinTaskBtn")
    page.wait_for_function("document.querySelector('#pinTaskBtn').textContent==='取消置顶'")
    page.click("#archiveTaskBtn")
    page.wait_for_function("document.querySelector('#archiveTaskBtn').textContent==='恢复' && document.querySelector('#messageInput').disabled===true")
    report["checks"]["archive_guard"] = True
    page.click("#archiveTaskBtn")
    page.wait_for_function("document.querySelector('#archiveTaskBtn').textContent==='归档' && document.querySelector('#messageInput').disabled===false")

    page.fill("#taskSearch", "战斗")
    page.wait_for_timeout(300)
    report["checks"]["task_search"] = page.locator("#conversationList .conv-item").count() == 1
    page.fill("#taskSearch", "")

    # Permanent deletion is not a fake confirm(): it enters persistent approval first.
    page.click("#deleteTaskBtn")
    page.wait_for_function("document.querySelector('#approvalCard').hidden===false")
    report["checks"]["dangerous_action_approval"] = "永久删除" in page.locator("#approvalCard").inner_text()
    page.click("[data-approval-reject]")
    page.wait_for_function("document.querySelector('#approvalCard').hidden===true")

    report["workspace"] = page.locator("#workspaceName").inner_text()
    report["message_count"] = page.locator("#messageList .msg").count()
    report["evidence_count"] = page.locator("#evidenceList .evidence-card").count()
    browser.close()

report["ok"] = all(report["checks"].values()) and not report["errors"]
(OUT / "product_ui_e2e.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
