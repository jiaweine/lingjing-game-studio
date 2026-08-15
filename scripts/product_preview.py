from __future__ import annotations
import base64, json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from worldforge.api.app import product_store, providers

convs=product_store.list_conversations(80)
conv=None
for c in convs:
    d={**c,'messages':product_store.list_messages(c['id']),'assets':product_store.list_assets(c['id']),'events':product_store.list_events(c['id'])}
    if any(m['role']=='assistant' for m in d['messages']): conv=d; break
if not conv: raise SystemExit('no answered conversation')
prov=providers.list()
asset_data={}
for a in conv['assets']:
    if a['mime'].startswith('image/'):
        b=Path(a['path']).read_bytes(); asset_data[a['id']]=f"data:{a['mime']};base64,{base64.b64encode(b).decode()}"
    elif a['mime'].startswith('video/') and a.get('meta',{}).get('keyframes'):
        fp=Path(a['meta']['keyframes'][0]); b=fp.read_bytes(); asset_data[a['id']]=f"data:image/jpeg;base64,{base64.b64encode(b).decode()}"

html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
css=(ROOT/'frontend/app.css').read_text(encoding='utf-8')
js=(ROOT/'frontend/app.js').read_text(encoding='utf-8')
html=html.replace('<link rel="stylesheet" href="/assets/app.css" />',f'<style>{css}</style>').replace('<script type="module" src="/assets/app.js"></script>','')
mock={'conv':conv,'providers':prov,'asset_data':asset_data}
script=f'''<script>
window.__MOCK__={json.dumps(mock,ensure_ascii=False)};
window.__ASSET_DATA__=window.__MOCK__.asset_data||{{}};
const jsonResponse=(o,s=200)=>Promise.resolve(new Response(JSON.stringify(o),{{status:s,headers:{{'Content-Type':'application/json'}}}}));
const fixImgs=()=>document.querySelectorAll('img').forEach(img=>{{for(const [id,src] of Object.entries(window.__ASSET_DATA__)){{if(img.getAttribute('src')?.includes(id)){{img.src=src;break}}}}}});
new MutationObserver(fixImgs).observe(document.documentElement,{{childList:true,subtree:true}});
function emit(type,payload,delay){{setTimeout(()=>window.__mockSocket?.onmessage?.({{data:JSON.stringify({{id:Date.now()+delay,type,payload,created_at:Date.now()/1000}})}}),delay)}}
window.fetch=async function(url,opt={{}}){{const u=String(url),D=window.__MOCK__,m=String(opt.method||'GET').toUpperCase();
 if(u.endsWith('/api/providers'))return jsonResponse(D.providers);
 if(u.endsWith('/api/conversations')&&m==='GET')return jsonResponse([D.conv]);
 if(u.includes('/api/conversations/')&&m==='GET'&&!u.includes('/messages'))return jsonResponse(D.conv);
 if(u.endsWith('/api/conversations')&&m==='POST')return jsonResponse(D.conv);
 if(u.endsWith('/api/assets')&&m==='POST'){{const f=opt.body.get('file'),id='asset-demo-'+Math.random().toString(16).slice(2,10),mime=f.type||'application/octet-stream',kind=mime.startsWith('image/')?'image':mime.startsWith('video/')?'video':mime.startsWith('audio/')?'audio':mime.startsWith('text/')||f.name.endsWith('.log')?'text':'file'; if(kind==='image')window.__ASSET_DATA__[id]=URL.createObjectURL(f); const a={{id,name:f.name,mime,size:f.size,meta:{{kind}},url:'/api/assets/'+id+'/file'}}; return jsonResponse(a)}}
 if(u.includes('/messages')&&m==='POST'){{
   const base=D.conv.messages.find(x=>x.role==='assistant');
   emit('progress',{{step:'素材整理',detail:'正在读取本次素材并建立上下文',percent:12}},120);
   emit('progress',{{step:'定位问题',detail:'正在定位异常时间段与关键事件',percent:36}},420);
   emit('progress',{{step:'场景复核',detail:'正在用一致条件复核关键路径',percent:58}},760);
   emit('progress',{{step:'交叉核对',detail:'正在对照素材、日志与复核结果',percent:78}},1050);
   emit('progress',{{step:'形成结论',detail:'已整理关键发现与下一步建议',percent:100}},1320);
   emit('answer.ready',{{message:{{...base,id:'msg-demo-'+Date.now(),created_at:Date.now()/1000}},result:base.payload}},1540);
   return jsonResponse({{status:'accepted',message:D.conv.messages[0]}})
 }}
 return jsonResponse({{status:'ok'}});
}};
class MockWS{{constructor(){{this.readyState=1;window.__mockSocket=this;setTimeout(()=>this.onopen&&this.onopen(),10)}}close(){{this.readyState=3}}}}
window.WebSocket=MockWS;
</script><script>{js}</script>'''
html=html.replace('</body>',script+'</body>')
out=ROOT/'outputs'; (out/'Lingjing_Interactive_Demo.html').write_text(html,encoding='utf-8')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu'])
    page=browser.new_page(viewport={'width':1720,'height':1040},device_scale_factor=1)
    errs=[];page.on('pageerror',lambda e:errs.append(str(e)))
    page.set_content(html,wait_until='load');page.wait_for_function("document.querySelectorAll('#messageList .msg').length>=2");page.wait_for_timeout(100)
    page.screenshot(path=str(out/'lingjing_after.png'),full_page=True)
    page.click(".right-tab[data-panel='evidence']");page.screenshot(path=str(out/'lingjing_evidence.png'),full_page=True)
    page.click('#providerBtn');page.wait_for_selector('#providerModal:not([hidden])');page.screenshot(path=str(out/'lingjing_providers.png'),full_page=True);page.click('.modal-close')
    before=page.locator('#messageList .msg.assistant').count();page.fill('#messageInput','继续看一下减伤覆盖和技能冷却是不是撞在同一时间窗。');page.click('#sendBtn')
    page.wait_for_function(f"document.querySelectorAll('#messageList .msg.assistant').length>{before}",timeout=10000)
    report={'errors':errs,'messages':page.locator('#messageList .msg').count(),'assets':page.locator('#assetList .asset-card').count(),'providers':page.locator('#providerGrid .provider-card').count(),'evidence':page.locator('#evidenceList .evidence-card').count(),'followup_chat':True}
    browser.close()
(out/'frontend_product_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
