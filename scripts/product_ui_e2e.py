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
html = html.replace(
    '<script type="module" src="/assets/app.js"></script>',
    "",
)

mock_script = r'''
<script>
window.__E2E={session:false,conversations:[],assets:[],sockets:[],assetData:{},assetSeq:0};
const jsonResponse=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}}));
const kindFor=(file)=>file.type.startsWith('image/')?'image':file.type.startsWith('video/')?'video':file.type.startsWith('audio/')?'audio':(file.type.startsWith('text/')||/\.(log|txt|json|csv|ya?ml|xml)$/i.test(file.name))?'text':'file';
const fixAssetImages=()=>document.querySelectorAll('img').forEach(img=>{for(const [id,src] of Object.entries(window.__E2E.assetData)){if(img.getAttribute('src')?.includes(id)){img.src=src;break}}});
new MutationObserver(fixAssetImages).observe(document.documentElement,{childList:true,subtree:true});
window.fetch=async function(url,opt={}){
  const u=String(url),method=String(opt.method||'GET').toUpperCase(),S=window.__E2E;
  if(u.endsWith('/api/config')) return jsonResponse({environment:'production',auth_required:true,max_upload_mb:120,storage:'s3',queue_mode:'external',version:'1.0.0'});
  if(u.endsWith('/api/auth/me')) return S.session?jsonResponse(S.session):jsonResponse({detail:'请先登录'},401);
  if(u.endsWith('/api/auth/register')&&method==='POST'){
    S.session={authenticated:true,user:{id:'user-e2e',email:'qa@studio.com',role:'owner'},workspace:{id:'ws-e2e',name:'Nebula Studio',slug:'nebula-studio',plan:'team'},access_token:'mock',token_type:'bearer'};
    return jsonResponse(S.session);
  }
  if(u.endsWith('/api/auth/login')&&method==='POST') return jsonResponse(S.session||{detail:'邮箱或密码错误'},S.session?200:401);
  if(u.endsWith('/api/auth/logout')){S.session=false;return jsonResponse({ok:true})}
  if(u.endsWith('/api/providers')) return jsonResponse([
    {key:'auto',name:'自动选择',vendor:'Lingjing',configured:true,multimodal:true,note:'按输入类型、可用性与成本自动路由'},
    {key:'demo',name:'内置演示',vendor:'Lingjing',configured:true,multimodal:true,note:'本地验证任务链路'},
    {key:'openai',name:'OpenAI',vendor:'OpenAI',configured:false,multimodal:true,supports_video:true,note:'图像与关键帧多模态推理'},
    {key:'anthropic',name:'Claude',vendor:'Anthropic',configured:false,multimodal:true,note:'图像输入与文本推理'},
    {key:'deepseek',name:'DeepSeek',vendor:'DeepSeek',configured:false,multimodal:false,note:'文本推理服务'},
    {key:'gemini',name:'Gemini',vendor:'Google',configured:false,multimodal:true,supports_video:true,supports_audio:true,note:'图像、视频与音频输入'}
  ]);
  if(u.endsWith('/api/health')) return jsonResponse({status:'ok',storage:'s3',queue:'external'});
  if(u.endsWith('/api/conversations')&&method==='GET') return jsonResponse(S.conversations);
  if(u.endsWith('/api/conversations')&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');
    const c={id:'cv-e2e',workspace_id:'ws-e2e',created_by:'user-e2e',title:body.title||'战斗问题复现',scene:body.scene||'battle_review',created_at:1,updated_at:1,messages:[],assets:[],events:[]};
    S.conversations=[c];return jsonResponse(c);
  }
  const convMatch=u.match(/\/api\/conversations\/([^/]+)$/);
  if(convMatch&&method==='GET'){
    const c=S.conversations.find(x=>x.id===convMatch[1]);
    return c?jsonResponse({...c,messages:c.messages||[],assets:S.assets,events:c.events||[]}):jsonResponse({detail:'任务不存在'},404);
  }
  if(u.endsWith('/api/assets')&&method==='POST'){
    const file=opt.body instanceof FormData?opt.body.get('file'):null;
    const kind=kindFor(file),id='asset-'+(++S.assetSeq),meta={kind};
    if(kind==='image'){meta.width=1280;meta.height=720;S.assetData[id]=URL.createObjectURL(file)}
    if(kind==='audio') meta.duration=18.4;
    if(kind==='text') Object.assign(meta,{lines:3,preview:'59.8 damage=120\n60.0 shield=0\n60.2 hp=18'});
    const a={id,workspace_id:'ws-e2e',conversation_id:'cv-e2e',name:file?.name||'asset',mime:file?.type||'application/octet-stream',size:file?.size||42,meta,created_at:2,url:'/api/assets/'+id+'/file'};
    S.assets.push(a);return jsonResponse(a);
  }
  if(u.match(/\/api\/conversations\/[^/]+\/messages$/)&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');
    const c=S.conversations[0];
    const user={id:'msg-user',conversation_id:'cv-e2e',role:'user',content:body.content,created_at:2.5,payload:{asset_ids:body.asset_ids||[]}};
    c.messages=[user];
    const log=S.assets.find(a=>a.meta?.kind==='text');
    const answer={id:'msg-answer',conversation_id:'cv-e2e',role:'assistant',content:'### 结论\n异常具备稳定复现条件，核心窗口集中在第二阶段进入后的防御资源真空期。\n\n### 关键发现\n1. 60 秒附近 shield 归零后，承伤在极短时间内连续抬升。\n2. 同条件再次核验时，异常路径可以重复出现。\n3. 当前证据更支持资源覆盖与技能时序叠加，而不是单一伤害参数失控。\n\n### 下一步\n优先核对减伤持续时间、Boss 技能冷却与资源恢复窗口，再用同一条件做修复后回归。',created_at:3,payload:{provider:'内置演示',evidence:[{type:'text',label:'战斗日志',title:'battle.log · 关键时间窗',asset_id:log?.id},{type:'replay',label:'复核结果',title:'同条件复核 · 异常路径再次出现'}],suggestions:['把异常时间段单独展开','继续核对伤害配置','生成回归测试清单']}};
    setTimeout(()=>{
      const events=[
        {id:1,type:'progress',payload:{step:'读取任务上下文',detail:'已关联当前素材，正在建立执行上下文',percent:12}},
        {id:2,type:'progress',payload:{step:'定位异常窗口',detail:'正在锁定掉血变化与资源变化发生的时间段',percent:36}},
        {id:3,type:'progress',payload:{step:'尝试稳定复现',detail:'正在用一致初始条件检查不同触发路径',percent:58}},
        {id:4,type:'progress',payload:{step:'交叉核对证据',detail:'正在对照截图、日志与重复结果',percent:78}},
        {id:5,type:'progress',payload:{step:'验证结论',detail:'已完成复核，正在整理证据与下一步',percent:100}},
        {id:6,type:'answer.ready',payload:{message:answer,result:answer.payload}}
      ];
      events.forEach((ev,i)=>setTimeout(()=>S.sockets.forEach(ws=>ws.onmessage&&ws.onmessage({data:JSON.stringify(ev)})),i*220));
    },80);
    return jsonResponse({status:'queued',message:user,job_id:'job-e2e'});
  }
  return jsonResponse({detail:'not mocked: '+u},404);
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
    executable = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
    if executable:
        launch_args["executable_path"] = executable
    browser = playwright.chromium.launch(**launch_args)
    page = browser.new_page(
        viewport={"width": 1920, "height": 1200},
        device_scale_factor=2,
    )
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
    page.wait_for_function(
        "document.querySelector('#authModal').hidden===true && "
        "document.querySelector('#workspaceName').textContent==='Nebula Studio'"
    )
    shot("workspace-empty.png")
    report["checks"]["register_workspace"] = True

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
    page.wait_for_function(
        "document.querySelectorAll('#pendingAssets .pending-asset').length===3"
    )
    shot("upload.png")
    report["checks"]["upload_interaction"] = True

    page.fill(
        "#messageInput",
        "复现这个 Boss 第二阶段偶发秒杀问题，找出稳定触发条件，验证后给我回归清单。",
    )
    page.click("#sendBtn")
    page.wait_for_function(
        "document.querySelector('#taskPercent').textContent==='36%'",
        timeout=10000,
    )
    shot("task-running.png")
    report["checks"]["running_state"] = True

    page.wait_for_function(
        "document.querySelectorAll('#messageList .msg.assistant').length===1",
        timeout=12000,
    )
    page.wait_for_function(
        "document.querySelector('#taskPercent').textContent==='100%'"
    )
    shot("workspace.png")
    shot("cover.png")
    report["checks"]["realtime_result"] = True
    report["checks"]["evidence_panel"] = (
        page.locator("#evidenceList .evidence-card").count() == 2
    )
    report["checks"]["suggestions"] = (
        page.locator("#suggestionList button").count() == 3
    )

    page.click('[data-panel="evidence"]')
    page.wait_for_function(
        "document.querySelector('#panel-evidence').classList.contains('active')"
    )
    shot("evidence.png")

    page.click('[data-panel="assets"]')
    page.wait_for_function(
        "document.querySelector('#panel-assets').classList.contains('active')"
    )
    report["checks"]["multimodal_assets"] = (
        page.locator("#assetList .asset-card").count() == 3
    )
    shot("multimodal.png")

    report["workspace"] = page.locator("#workspaceName").inner_text()
    report["message_count"] = page.locator("#messageList .msg").count()
    report["evidence_count"] = page.locator("#evidenceList .evidence-card").count()
    browser.close()

report["ok"] = all(report["checks"].values()) and not report["errors"]
(OUT / "product_ui_e2e.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2))
