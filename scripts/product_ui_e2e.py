from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
README_ASSETS = ROOT / "docs" / "assets" / "readme"
OUT.mkdir(exist_ok=True)
README_ASSETS.mkdir(parents=True, exist_ok=True)

html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
css = (ROOT / "frontend/app.css").read_text(encoding="utf-8")
js = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
html = html.replace('<link rel="stylesheet" href="/assets/app.css" />', f"<style>{css}</style>")
html = html.replace('<script type="module" src="/assets/app.js"></script>', "")

mock_script = r'''
<script>
window.__E2E={session:false,conversations:[],assets:[],sockets:[],assetData:{},assetSeq:0};
const jsonResponse=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}}));
const kindFor=(file)=>file.type.startsWith('image/')?'image':file.type.startsWith('video/')?'video':file.type.startsWith('audio/')?'audio':(file.type.startsWith('text/')||/\.(log|txt|json|csv|ya?ml|xml)$/i.test(file.name))?'text':'file';
const fixAssetImages=()=>document.querySelectorAll('img').forEach(img=>{for(const [id,src] of Object.entries(window.__E2E.assetData)){if(img.getAttribute('src')?.includes(id)){img.src=src;break}}});
new MutationObserver(fixAssetImages).observe(document.documentElement,{childList:true,subtree:true});
window.fetch=async function(url,opt={}){
  const u=String(url),method=String(opt.method||'GET').toUpperCase(),S=window.__E2E;
  if(u.endsWith('/api/config')) return jsonResponse({environment:'production',auth_required:true,max_upload_mb:120,storage:'s3',queue_mode:'external',version:'3.0.0'});
  if(u.endsWith('/api/auth/me')) return S.session?jsonResponse(S.session):jsonResponse({detail:'请先登录'},401);
  if(u.endsWith('/api/auth/register')&&method==='POST'){
    S.session={authenticated:true,user:{id:'user-e2e',email:'qa@studio.com',role:'owner'},workspace:{id:'ws-e2e',name:'Nebula Studio',slug:'nebula-studio',plan:'team'},access_token:'mock',token_type:'bearer'};
    return jsonResponse(S.session);
  }
  if(u.endsWith('/api/auth/login')&&method==='POST') return jsonResponse(S.session||{detail:'邮箱或密码错误'},S.session?200:401);
  if(u.endsWith('/api/auth/logout')){S.session=false;return jsonResponse({ok:true})}
  if(u.endsWith('/api/providers')) return jsonResponse([
    {key:'auto',name:'自动选择',vendor:'Lingjing',configured:true,multimodal:true,note:'按任务能力自动路由'},
    {key:'demo',name:'内置演示',vendor:'Lingjing',configured:true,multimodal:true,note:'本地验证链路'},
    {key:'openai',name:'OpenAI',vendor:'OpenAI',configured:false,multimodal:true,supports_video:true,note:'图像与关键帧多模态分析'},
    {key:'anthropic',name:'Claude',vendor:'Anthropic',configured:false,multimodal:true,note:'图像输入与文本推理'},
    {key:'gemini',name:'Gemini',vendor:'Google',configured:false,multimodal:true,supports_video:true,supports_audio:true,note:'图像、视频与音频输入'}
  ]);
  if(u.endsWith('/api/health')) return jsonResponse({status:'ok',storage:'s3',queue:'external'});
  if(u.endsWith('/api/conversations')&&method==='GET') return jsonResponse(S.conversations);
  if(u.endsWith('/api/conversations')&&method==='POST'){
    const body=JSON.parse(opt.body||'{}');
    const c={id:'cv-e2e',workspace_id:'ws-e2e',created_by:'user-e2e',title:body.title||'战斗录像复盘',scene:body.scene||'battle_review',created_at:1,updated_at:1,messages:[],assets:[],events:[]};
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
    const log=S.assets.find(a=>a.meta?.kind==='text');
    const answer={id:'msg-answer',conversation_id:'cv-e2e',role:'assistant',content:'### 结论\n这次异常更像是高爆发阶段的资源衔接问题，而不是单一伤害数值失控。关键路径在进入第二阶段后再次出现防御资源不足，具备稳定复现条件。\n\n### 证据\n1. 60 秒附近 shield 归零，随后承伤快速抬升。\n2. 同条件复核中异常路径再次出现。\n3. 截图、日志和复核结果已关联到右侧「关键发现」。\n\n### 建议\n优先检查减伤持续时间、Boss 技能冷却与资源恢复窗口是否发生叠加。',created_at:3,payload:{provider:'内置演示',evidence:[{type:'text',label:'战斗日志',title:'battle.log · 关键时间窗',asset_id:log?.id},{type:'replay',label:'复核结果',title:'同条件复核 12 步 · 异常路径已复现'}],suggestions:['把异常时间段单独展开','继续核对伤害配置','生成回归测试清单']}};
    setTimeout(()=>{
      const events=[
        {id:1,type:'progress',payload:{step:'素材整理',detail:'已读取当前任务素材并建立分析上下文',percent:12}},
        {id:2,type:'progress',payload:{step:'定位问题',detail:'正在定位异常时间段与资源变化',percent:36}},
        {id:3,type:'progress',payload:{step:'场景复核',detail:'正在用一致条件复核关键路径',percent:58}},
        {id:4,type:'progress',payload:{step:'交叉核对',detail:'正在对照素材、日志与复核结果',percent:78}},
        {id:5,type:'progress',payload:{step:'形成结论',detail:'已整理关键发现与下一步建议',percent:100}},
        {id:6,type:'answer.ready',payload:{message:answer,result:answer.payload}}
      ];
      events.forEach((ev,i)=>setTimeout(()=>S.sockets.forEach(ws=>ws.onmessage&&ws.onmessage({data:JSON.stringify(ev)})),i*45));
    },30);
    return jsonResponse({status:'queued',message:{id:'msg-user',role:'user',content:body.content},job_id:'job-e2e'});
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

report = {"checks": {}, "errors": []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("pageerror", lambda exc: report["errors"].append(str(exc)))
    page.set_content(html, wait_until="load")

    page.wait_for_selector("#authModal:not([hidden])")
    page.screenshot(path=str(README_ASSETS / "auth.png"), full_page=True)
    report["checks"]["auth_gate"] = True

    page.click('[data-auth-tab="register"]')
    page.fill("#registerName", "QA Lead")
    page.fill("#registerWorkspace", "Nebula Studio")
    page.fill("#registerEmail", "qa@studio.com")
    page.fill("#registerPassword", "a-strong-password-123")
    page.click("#registerForm .auth-submit")
    page.wait_for_function("document.querySelector('#authModal').hidden===true && document.querySelector('#workspaceName').textContent==='Nebula Studio'")
    report["checks"]["register_workspace"] = True
    page.screenshot(path=str(README_ASSETS / "workspace-empty.png"), full_page=True)

    frame = Image.new("RGB", (1280, 720), "#181b22")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((50, 50, 1230, 670), outline="#6f5cf1", width=8)
    draw.rectangle((220, 180, 1060, 540), fill="#252a35")
    draw.ellipse((560, 280, 720, 440), fill="#6f5cf1")
    buf = BytesIO(); frame.save(buf, format="PNG")
    page.set_input_files("#fileInput", [
        {"name":"boss_frame.png","mimeType":"image/png","buffer":buf.getvalue()},
        {"name":"phase2.wav","mimeType":"audio/wav","buffer":b"RIFF"+b"0"*320},
        {"name":"battle.log","mimeType":"text/plain","buffer":b"59.8 damage=120\n60.0 shield=0\n60.2 hp=18"},
    ])
    page.wait_for_function("document.querySelectorAll('#pendingAssets .pending-asset').length===3")
    report["checks"]["upload_interaction"] = True

    page.fill("#messageInput", "帮我复盘这场 Boss 战，重点看 60 秒之后为什么会突然掉血，并给我一个可执行的排查顺序。")
    page.click("#sendBtn")
    page.wait_for_function("document.querySelectorAll('#messageList .msg.assistant').length===1", timeout=10000)
    page.wait_for_function("document.querySelector('#taskPercent').textContent==='100%'")
    report["checks"]["realtime_answer"] = True
    report["checks"]["evidence_panel"] = page.locator("#evidenceList .evidence-card").count() == 2
    report["checks"]["suggestions"] = page.locator("#suggestionList button").count() == 3

    page.screenshot(path=str(README_ASSETS / "workspace-saas.png"), full_page=True)
    page.set_viewport_size({"width": 1720, "height": 1040})
    page.screenshot(path=str(README_ASSETS / "workspace.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})

    page.click('[data-panel="evidence"]')
    page.wait_for_function("document.querySelector('#panel-evidence').classList.contains('active')")
    page.screenshot(path=str(README_ASSETS / "evidence.png"), full_page=True)
    page.click('[data-panel="progress"]')

    page.click("#providerBtn")
    page.wait_for_selector("#providerModal:not([hidden])")
    report["checks"]["provider_modal"] = page.locator("#providerGrid .provider-card").count() >= 4
    page.screenshot(path=str(README_ASSETS / "providers.png"), full_page=True)
    page.click(".modal-close")

    report["workspace"] = page.locator("#workspaceName").inner_text()
    report["message_count"] = page.locator("#messageList .msg").count()
    report["evidence_count"] = page.locator("#evidenceList .evidence-card").count()
    report["page_errors"] = list(report["errors"])
    browser.close()

# Build a hero image only from the real screenshots captured above. No mock artwork.
canvas = Image.new("RGB", (1600, 930), "#14161b")
primary = Image.open(README_ASSETS / "workspace-empty.png").convert("RGB")
secondary = Image.open(README_ASSETS / "workspace.png").convert("RGB")
primary.thumbnail((1220, 760), Image.Resampling.LANCZOS)
secondary.thumbnail((640, 410), Image.Resampling.LANCZOS)
primary = ImageOps.expand(primary, border=2, fill="#2a2e37")
secondary = ImageOps.expand(secondary, border=2, fill="#2a2e37")
canvas.paste(primary, (90, 125))
canvas.paste(secondary, (930, 500))
canvas.save(README_ASSETS / "cover.png", optimize=True)

report["ok"] = all(report["checks"].values()) and not report["errors"]
(OUT / "product_ui_e2e.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
