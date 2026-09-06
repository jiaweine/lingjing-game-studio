from __future__ import annotations

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
MEMORY_JS = (ROOT / "frontend" / "memory_panel.js").read_text(encoding="utf-8")
IDENTITY_JS = (ROOT / "frontend" / "memory_identity_panel.js").read_text(encoding="utf-8")

MOCK = r'''
<script>
const S = {
  session: {authenticated:true,user:{id:'user-e2e',email:'qa@studio.com',name:'QA Lead',role:'owner'},workspace:{id:'ws-e2e',name:'Nebula Studio'}},
  projects: [{id:'project-atlas',name:'Atlas',default_branch:'release',external_ref:null}],
  project: null,
  heads: [
    {id:'mem-general',memory_key:'release.regression.required',revision:1,kind:'constraint',content:'发布前必须运行全套回归。',state:'active',confidence:1,importance:.9,pinned:false,build_ref:null,branch_ref:null,commit_ref:null,environment_ref:null,source_type:'user_confirmed',source_id:'proposal:general'},
    {id:'mem-shield-147-r1',memory_key:'combat.shield.cooldown',revision:1,kind:'fact',content:'build 1.4.7 护盾冷却已确认是 6 秒。',state:'active',confidence:1,importance:.8,pinned:false,build_ref:'1.4.7',branch_ref:'release',commit_ref:null,environment_ref:null,source_type:'user_confirmed',source_id:'proposal:old'},
    {id:'mem-shield-200-r2',memory_key:'combat.shield.cooldown',revision:2,kind:'fact',content:'build 2.0.0 护盾冷却已撤回。',state:'retracted',confidence:1,importance:.8,pinned:false,build_ref:'2.0.0',branch_ref:'release',commit_ref:null,environment_ref:null,source_type:'user_api',source_id:'api:retract'}
  ],
  proposals: [
    {id:'proposal-update',project_id:'project-atlas',conversation_id:'cv-e2e',message_id:'msg-update',suggested_key:'proposal.fact.abc123',kind:'fact',content:'已确认 build 1.4.7 护盾冷却是 5 秒。',build_ref:'1.4.7',branch_ref:'release',commit_ref:null,environment_ref:null,extractor_version:'deterministic-user-memory-v1',status:'pending'},
    {id:'proposal-reject',project_id:'project-atlas',conversation_id:'cv-e2e',message_id:'msg-reject',suggested_key:'proposal.decision.def456',kind:'decision',content:'我们决定采用实验方案 B。',build_ref:null,branch_ref:'release',commit_ref:null,environment_ref:null,extractor_version:'deterministic-user-memory-v1',status:'pending'}
  ],
  history: {
    'combat.shield.cooldown|1.4.7|release': [
      {id:'mem-shield-147-r1',memory_key:'combat.shield.cooldown',revision:1,kind:'fact',content:'build 1.4.7 护盾冷却已确认是 6 秒。',state:'active',build_ref:'1.4.7',branch_ref:'release',commit_ref:null,environment_ref:null,source_type:'user_confirmed',source_id:'proposal:old'}
    ]
  }
};
const response=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}}));
const scopeKey=row=>`${row.memory_key}|${row.build_ref||''}|${row.branch_ref||''}`;
window.fetch=async function(url,opt={}){
  const U=new URL(String(url),'http://e2e.local'),p=U.pathname,method=String(opt.method||'GET').toUpperCase();
  if(p==='/api/auth/me') return response(S.session);
  if(p==='/api/projects'&&method==='GET') return response(S.projects);
  if(p==='/api/projects'&&method==='POST'){
    const body=JSON.parse(opt.body||'{}'),project={id:'project-new',name:body.name,default_branch:null,external_ref:null};S.projects.push(project);return response(project);
  }
  if(p==='/api/conversations/cv-e2e/project'&&method==='GET') return response({project:S.project});
  const bind=p.match(/^\/api\/projects\/([^/]+)\/conversations\/cv-e2e$/);
  if(bind&&method==='POST'){S.project=S.projects.find(row=>row.id===bind[1]);return response({project_id:S.project.id,conversation_id:'cv-e2e'});}
  if(p==='/api/projects/project-atlas/memory-heads'&&method==='GET') return response(S.heads);
  if(p==='/api/projects/project-atlas/memory-proposals'&&method==='GET') return response(S.proposals.filter(row=>row.status==='pending'));
  const identity=p.match(/^\/api\/projects\/project-atlas\/memory-proposals\/([^/]+)\/identity-suggestions$/);
  if(identity&&method==='GET'){
    const proposal=S.proposals.find(row=>row.id===identity[1]);
    if(!proposal||proposal.status!=='pending') return response({detail:'proposal unavailable'},409);
    if(proposal.id==='proposal-update') return response({
      proposal_id:proposal.id,proposal_suggested_key:proposal.suggested_key,read_only:true,
      candidate_heads_evaluated:2,candidate_heads_truncated:false,mode:'deterministic-memory-identity-v1',
      proposal_kind:'fact',abstained:false,recommended_key:'combat.shield.cooldown',best_score:.861,
      score_margin:.861,threshold:.72,margin_threshold:.1,
      candidates:[{memory_key:'combat.shield.cooldown',score:.861,recommended:true,reasons:['strong-stable-token-overlap','same-value-stripped-skeleton','scope:exact'],best_head_id:'mem-shield-147-r1',best_revision:1,scope_relation:'exact'}]
    });
    return response({
      proposal_id:proposal.id,proposal_suggested_key:proposal.suggested_key,read_only:true,
      candidate_heads_evaluated:1,candidate_heads_truncated:false,mode:'deterministic-memory-identity-v1',
      proposal_kind:proposal.kind,abstained:true,recommended_key:null,best_score:.42,score_margin:.42,
      threshold:.72,margin_threshold:.1,candidates:[]
    });
  }
  const approve=p.match(/^\/api\/projects\/project-atlas\/memory-proposals\/([^/]+)\/approve$/);
  if(approve&&method==='POST'){
    const proposal=S.proposals.find(row=>row.id===approve[1]),body=JSON.parse(opt.body||'{}');
    if(!proposal||proposal.status!=='pending') return response({detail:'proposal unavailable'},409);
    const key=body.memory_key;
    const index=S.heads.findIndex(row=>row.memory_key===key&&(row.build_ref||null)===(proposal.build_ref||null)&&(row.branch_ref||null)===(proposal.branch_ref||null));
    const previous=index>=0?S.heads[index]:null;
    const revision=previous?previous.revision+1:1;
    const memory={id:`mem-approved-${revision}`,memory_key:key,revision,kind:proposal.kind,content:proposal.content,state:'active',confidence:1,importance:.7,pinned:false,build_ref:proposal.build_ref||null,branch_ref:proposal.branch_ref||null,commit_ref:proposal.commit_ref||null,environment_ref:proposal.environment_ref||null,source_type:'user_confirmed',source_id:`proposal:${proposal.id}`};
    if(previous){
      const h=scopeKey(previous);S.history[h]=[...(S.history[h]||[]),memory];S.heads[index]=memory;
    }else{S.heads.push(memory);S.history[scopeKey(memory)]=[memory];}
    proposal.status='approved';proposal.approved_memory_id=memory.id;return response({proposal,memory});
  }
  const reject=p.match(/^\/api\/projects\/project-atlas\/memory-proposals\/([^/]+)\/reject$/);
  if(reject&&method==='POST'){
    const proposal=S.proposals.find(row=>row.id===reject[1]);if(!proposal) return response({detail:'missing'},404);proposal.status='rejected';return response(proposal);
  }
  if(p==='/api/projects/project-atlas/memory-state'&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');
    const index=S.heads.findIndex(row=>row.memory_key===body.memory_key&&(row.build_ref||null)===(body.build_ref||null)&&(row.branch_ref||null)===(body.branch_ref||null));
    if(index<0) return response({detail:'missing'},404);
    const previous=S.heads[index],memory={...previous,id:`mem-state-${previous.revision+1}`,revision:previous.revision+1,state:body.state,source_type:'user_api',source_id:'api:memory-panel'};
    S.heads[index]=memory;const h=scopeKey(previous);S.history[h]=[...(S.history[h]||[previous]),memory];return response(memory);
  }
  if(p==='/api/projects/project-atlas/memory-history'&&method==='GET'){
    const key=`${U.searchParams.get('memory_key')||''}|${U.searchParams.get('build_ref')||''}|${U.searchParams.get('branch_ref')||''}`;
    return response(S.history[key]||[]);
  }
  return response({detail:`not mocked: ${method} ${p}`},404);
};
</script>
'''

HTML = f'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>
:root{{--line:#dfe4ec;--ink:#172033;--ink-2:#4f5c70;--ink-3:#7b8798;--accent:#315de8;--accent-soft:#eef2ff;--panel-soft:#f7f9fc;--success:#148a65;--success-soft:#e3f4ec;--warning:#b8781b;--warning-soft:#fff4de;--danger:#c84c55;--danger-soft:#fdebed}}
.right-tabs{{display:flex;gap:4px}}.right-tab{{padding:8px;border:0}}.right-tab.active{{font-weight:700}}.right-panel{{display:none}}.right-panel.active{{display:block}}#toast{{position:fixed;bottom:10px;left:10px}}#toast.show{{display:block}}
</style></head>
<body>
<h1 id="conversationTitle">Shield follow-up</h1>
<div id="messageList"></div>
<aside class="rightbar">
  <div class="right-tabs">
    <button class="right-tab active" data-panel="progress">执行</button>
    <button class="right-tab" data-panel="evidence">证据</button>
    <button class="right-tab" data-panel="deliverables">交付</button>
    <button class="right-tab" data-panel="assets">素材</button>
    <button class="right-tab" data-panel="team">团队</button>
  </div>
  <div class="right-scroll">
    <section class="right-panel active" id="panel-progress">progress</section>
    <section class="right-panel" id="panel-evidence">evidence</section>
    <section class="right-panel" id="panel-deliverables">deliverables</section>
    <section class="right-panel" id="panel-assets">assets</section>
    <section class="right-panel" id="panel-team">team</section>
  </div>
</aside>
<div id="toast"></div>
{MOCK}
<script>{MEMORY_JS}</script>
<script>{IDENTITY_JS}</script>
</body></html>'''

report = {"checks": {}, "errors": []}
with sync_playwright() as playwright:
    executable = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
    if executable:
        launch_args["executable_path"] = executable
    browser = playwright.chromium.launch(**launch_args)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda exc: report["errors"].append(str(exc)))
    page.route("http://e2e.local/**", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
    page.goto("http://e2e.local/?conversation=cv-e2e", wait_until="load")

    page.wait_for_selector('[data-panel="memory"]')
    report["checks"]["sixth_memory_tab"] = page.locator('[data-panel="memory"]').count() == 1

    page.click('[data-panel="memory"]')
    page.wait_for_function("document.querySelector('#panel-memory').classList.contains('active')")
    page.wait_for_function("document.querySelector('#memoryPanelBody').textContent.includes('未绑定项目')")
    report["checks"]["explicit_unbound_state"] = "不会根据任务标题" in page.locator("#memoryPanelBody").inner_text()

    page.click("#memoryBindBtn")
    page.wait_for_function("document.querySelector('#memoryPanelBody').textContent.includes('Atlas') && document.querySelectorAll('.memory-proposal').length===2")
    page.wait_for_function("document.querySelectorAll('[data-memory-identity-check]').length===2")
    report["checks"]["explicit_project_binding"] = True
    report["checks"]["scope_complete_heads"] = (
        "build=1.4.7" in page.locator("#memoryPanelBody").inner_text()
        and "build=2.0.0" in page.locator("#memoryPanelBody").inner_text()
        and "撤回" in page.locator("#memoryPanelBody").inner_text()
    )

    update = page.locator('.memory-proposal[data-proposal-id="proposal-update"]')
    key_input = update.locator('[data-proposal-key="proposal-update"]')
    original_key = key_input.input_value()
    assert original_key == "proposal.fact.abc123"
    update.locator('[data-memory-identity-check="proposal-update"]').click()
    page.wait_for_function("document.querySelector('.memory-proposal[data-proposal-id=\"proposal-update\"] [data-memory-identity-result]').textContent.includes('combat.shield.cooldown')")
    report["checks"]["identity_suggestion_visible"] = True
    report["checks"]["identity_does_not_autofill"] = key_input.input_value() == original_key
    update.locator('[data-memory-identity-adopt="proposal-update"]').click()
    page.wait_for_function("document.querySelector('[data-proposal-key=\"proposal-update\"]').value==='combat.shield.cooldown'")
    report["checks"]["identity_requires_explicit_adopt"] = True

    update.locator('[data-memory-approve="proposal-update"]').click()
    page.wait_for_function("document.querySelector('#memoryPanelBody').textContent.includes('已确认 build 1.4.7 护盾冷却是 5 秒') && document.querySelector('#memoryPanelBody').textContent.includes('rev 2')")
    report["checks"]["approve_into_existing_revision_chain"] = True

    reject = page.locator('.memory-proposal[data-proposal-id="proposal-reject"]')
    reject.locator('[data-memory-reject="proposal-reject"]').click()
    page.wait_for_function("document.querySelectorAll('.memory-proposal').length===0")
    report["checks"]["proposal_reject_removes_pending"] = True

    current = page.locator(".memory-item").filter(has_text="已确认 build 1.4.7 护盾冷却是 5 秒")
    current.locator('[data-memory-state="disputed"]').click()
    page.wait_for_function("Array.from(document.querySelectorAll('.memory-item')).some(node=>node.textContent.includes('护盾冷却是 5 秒')&&node.textContent.includes('争议')&&node.textContent.includes('rev 3'))")
    report["checks"]["memory_state_governance"] = True

    current = page.locator(".memory-item").filter(has_text="已确认 build 1.4.7 护盾冷却是 5 秒")
    current.locator("[data-memory-history]").click()
    page.wait_for_function("Array.from(document.querySelectorAll('.memory-history-row')).some(node=>node.textContent.includes('6 秒')) && Array.from(document.querySelectorAll('.memory-history-row')).some(node=>node.textContent.includes('5 秒'))")
    report["checks"]["revision_history_visible"] = True

    # Workspace membership can change without reloading the page. A governance refresh must
    # re-authorize the current role so stale owner controls do not remain visible to a viewer.
    page.evaluate("""
      S.session = {...S.session, user: {...S.session.user, role: 'viewer'}};
      S.proposals.push({
        id:'proposal-viewer',project_id:'project-atlas',conversation_id:'cv-e2e',
        message_id:'msg-viewer',suggested_key:'proposal.constraint.viewer',kind:'constraint',
        content:'发布前必须重新验证 viewer 场景。',build_ref:null,branch_ref:'release',
        commit_ref:null,environment_ref:null,extractor_version:'deterministic-user-memory-v1',
        status:'pending'
      });
    """)
    page.click("#memoryRefreshBtn")
    page.wait_for_function("document.querySelector('#memoryPanelBody').textContent.includes('只读成员可以查看 proposal')")
    page.wait_for_function("document.querySelectorAll('[data-memory-identity-check]').length===1")
    report["checks"]["workspace_role_reauthorized"] = (
        page.locator('[data-memory-approve]').count() == 0
        and page.locator('[data-memory-reject]').count() == 0
        and page.locator('[data-memory-state]').count() == 0
        and page.locator('.memory-proposal[data-proposal-id="proposal-viewer"]').count() == 1
    )
    report["checks"]["viewer_can_read_identity_suggestion"] = page.locator(
        '.memory-proposal[data-proposal-id="proposal-viewer"] [data-memory-identity-check]'
    ).count() == 1

    browser.close()

report["ok"] = all(report["checks"].values()) and not report["errors"]
(OUT / "memory_ui_e2e.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
if not report["ok"]:
    raise SystemExit(1)
