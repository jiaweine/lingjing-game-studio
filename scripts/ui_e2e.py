from __future__ import annotations
import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from worldforge.envs import list_scenarios
from worldforge.runtime import EventStore, WorldForgeEngine

report_backend=json.loads((ROOT/'outputs/e2e_report.json').read_text())
sid=report_backend['session_id']
store=EventStore(ROOT/'outputs/runtime/worldforge.db')
events=[e.model_dump() for e in store.list_events(sid)]
engine=WorldForgeEngine(ROOT/'outputs/runtime/ui_mock.db')
scenarios=[s.model_dump() for s in list_scenarios()]
runtime={
    'plugins': engine.plugins.describe(),
    'skills': engine.skills.snapshot(),
    'event_store': {'append_only':True,'hash_chain':True,'fork':True,'replay':True},
    'decision_model': {'fixed_dag':False,'counterfactual':True,'rollback':True,'recursive_council':True},
}
complete=next(e for e in reversed(events) if e['event_type']=='run.completed')
skills=complete['payload']['skills']
selfplay={
    'scenario':'economy_trap','hardest_profile':'conservative','failure_signature':'survival','priority':.5,
    'population':[
      {'profile':'aggressive','success_rate':1.0,'avg_score':100.6,'failure_signature':'success'},
      {'profile':'conservative','success_rate':.5,'avg_score':29.1,'failure_signature':'survival'},
      {'profile':'economist','success_rate':.5,'avg_score':33.5,'failure_signature':'survival'},
      {'profile':'explorer','success_rate':.5,'avg_score':30.1,'failure_signature':'survival'},
    ],
    'next_focus':'stress conservative trajectories with counterfactual risk and verifier coverage'
}
bench={'rows':report_backend['benchmark'],'note':'Local deterministic BalanceLab benchmark'}
mock={'session_id':sid,'scenarios':scenarios,'runtime':runtime,'skills':skills,'events':events,'selfplay':selfplay,'bench':bench}

html=(ROOT/'frontend/index.html').read_text()
css=(ROOT/'frontend/app.css').read_text()
js=(ROOT/'frontend/app.js').read_text()
html=html.replace('<link rel="stylesheet" href="/assets/app.css" />',f'<style>{css}</style>')
html=html.replace('<script type="module" src="/assets/app.js"></script>','')
mock_js=f"""
<script>
window.__MOCK__={json.dumps(mock,ensure_ascii=False)};
const realResponse=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{{status,headers:{{'Content-Type':'application/json'}}}}));
window.fetch=async function(url,opts={{}}){{
 const u=String(url),m=String(opts.method||'GET').toUpperCase(),D=window.__MOCK__;
 if(u.endsWith('/api/scenarios')) return realResponse(D.scenarios);
 if(u.endsWith('/api/runtime')) return realResponse(D.runtime);
 if(u.endsWith('/api/skills')) return realResponse(D.skills);
 if(u.includes('/api/selfplay/')) return realResponse(D.selfplay);
 if(u.endsWith('/api/benchmarks')) return realResponse(D.bench);
 if(u.includes('/verify')) return realResponse({{session_id:D.session_id,hash_chain_valid:true}});
 if(u.includes('/cancel')) return realResponse({{session_id:D.session_id,status:'cancelled'}});
 if(u.endsWith('/api/runs') && m==='POST') return realResponse({{session_id:D.session_id,status:'running'}});
 return realResponse({{status:'ok'}});
}};
class MockWebSocket{{
 constructor(url){{this.url=url;this.readyState=1;setTimeout(()=>{{this.onopen&&this.onopen();let i=0;const pump=()=>{{if(i>=window.__MOCK__.events.length) return; const e=window.__MOCK__.events[i++];this.onmessage&&this.onmessage({{data:JSON.stringify(e)}});setTimeout(pump,3)}};pump()}},5)}}
 close(){{this.readyState=3;this.onclose&&this.onclose()}}
}}
window.WebSocket=MockWebSocket;
</script>
<script>{js}</script>
"""
html=html.replace('</body>',mock_js+'</body>')

report={'started_at':time.time(),'checks':{}}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu'])
    page=browser.new_page(viewport={'width':1720,'height':1120})
    errors=[]; page.on('pageerror',lambda exc:errors.append(str(exc)))
    page.set_content(html,wait_until='load')
    page.wait_for_function("document.querySelector('#scenarioSelect').options.length >= 4")
    assert page.locator('#pluginList .plugin').count()>=8
    page.select_option('#scenarioSelect','economy_trap');page.fill('#seedInput','17');page.fill('#stepsInput','18')
    page.locator('#branchRange').evaluate("el=>{el.value=5;el.dispatchEvent(new Event('input',{bubbles:true}))}")
    assert page.locator('#branchValue').inner_text()=='5';report['checks']['mission_controls']=True

    page.click('#startBtn')
    page.wait_for_function("['VICTORY','DEFEAT','TIMEOUT'].includes(document.querySelector('#runBadge span').textContent.toUpperCase())",timeout=30000)
    assert page.locator('#branchCards .branch-card').count()>=2
    assert page.locator('#councilGrid .agent-card').count()>=2
    assert int(page.locator('#eventCountBadge').inner_text().split()[0])>20
    report['checks']['live_event_rendering']=True

    page.click('#verifyBtn');page.wait_for_function("document.querySelector('#traceMetric').textContent==='VALID'");report['checks']['verify_chain_button']=True
    if page.locator('#branchCards .branch-card').count()>1:
        page.locator('#branchCards .branch-card').nth(1).click();assert 'Inspecting' in page.locator('#decisionRationale').inner_text()
    report['checks']['branch_inspection']=True

    page.keyboard.press('Control+K');page.wait_for_selector('#paletteBackdrop:not([hidden])');page.fill('#paletteInput','Trajectory');page.locator('#paletteList .palette-item').first.click();assert page.locator('#view-trajectory').evaluate("e=>e.classList.contains('active')");report['checks']['command_palette']=True
    page.fill('#traceSearch','decision');assert page.locator('#traceList .trace-item').count()>0;page.locator('#traceList .trace-item').first.click();assert page.locator('#inspectorPayload').inner_text().strip().startswith('{');page.fill('#traceSearch','')
    page.locator('#replayRange').evaluate("el=>{el.value=Math.floor(Number(el.max)/2);el.dispatchEvent(new Event('input',{bubbles:true}))}");assert '#' in page.locator('#replayLabel').inner_text();report['checks']['trajectory_search_replay']=True

    page.click(".top-tab[data-view='evolution']");page.click('#selfplayBtn');page.wait_for_function("document.querySelectorAll('#selfplayGrid .profile-card').length===4");report['checks']['selfplay_ui']=True
    page.click(".top-tab[data-view='benchmark']");page.click('#benchmarkBtn');page.wait_for_function("document.querySelectorAll('#benchmarkChart .bench-col').length===4");report['checks']['benchmark_ui']=True
    page.click(".top-tab[data-view='command']");page.screenshot(path=str(ROOT/'outputs/ui_preview_v2.png'),full_page=True);report['checks']['screenshot']=True
    report['page_errors']=errors;assert not errors,errors
    browser.close()
report['finished_at']=time.time();(ROOT/'outputs/ui_e2e_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False));print(json.dumps(report,indent=2,ensure_ascii=False))
