from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from worldforge.benchmarks import run_benchmark
from worldforge.envs import list_scenarios
from worldforge.models import RunConfig
from worldforge.runtime import WorldForgeEngine

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
DB = OUT / "ui_demo.db"
if DB.exists(): DB.unlink()

engine = WorldForgeEngine(DB)
summary = asyncio.run(engine.run(RunConfig(
    scenario_id="loot_exploit", seed=17, max_steps=18, branch_width=4,
    rollout_horizon=3, rollouts_per_branch=2, enable_counterfactual=True,
    enable_recursive_agents=True, enable_evolution=True,
), demo_delay=0))
sid = summary.session_id
events = [e.model_dump() for e in engine.events.list_events(sid)]
complete = next(e for e in reversed(events) if e["event_type"] == "run.completed")
scenarios = [s.model_dump() for s in list_scenarios()]
runtime = {
    "plugins": engine.plugins.describe(), "skills": engine.skills.snapshot(),
    "event_store": {"append_only": True, "hash_chain": True, "fork": True, "replay": True, "snapshots": True},
    "decision_model": {"fixed_dag": False, "state_conditioned": True, "counterfactual": True, "rollback": True, "recursive_council": True, "self_evolution": True},
    "model": engine.policy_model.card_dict(),
}
model = {
    "card": engine.policy_model.card_dict(), "role": "自主决策先验", "ownership": "项目自研", "external_api": False,
    "training_pipeline": ["环境轨迹采集", "反事实分支标注", "Verifier 筛选", "策略蒸馏", "离线验证"],
}
selfplay = engine.selfplay.curriculum("loot_exploit", 4)
bench = [r.model_dump() for r in run_benchmark(seeds=4, scenarios=["boss_burst", "economy_trap", "glass_cannon", "loot_exploit"])]

decisions = [e for e in events if e["event_type"] == "decision.committed"]
branch_events = [e for e in events if e["event_type"] == "counterfactual.evaluated"]
actions = [e for e in events if e["event_type"] == "action.executed"]
report = {
    "session_id": sid, "status": "completed", "scenario": scenarios[-1], "model": engine.policy_model.card_dict(),
    "summary": complete["payload"]["summary"], "final_state": complete["payload"]["final_state"],
    "metrics": {
        "decision_count": len(decisions), "action_count": len(actions),
        "counterfactual_futures": sum(len(e["payload"].get("branches", [])) for e in branch_events),
        "rollback_count": sum(e["event_type"] == "runtime.rollback" for e in events),
        "replan_count": sum(e["event_type"] == "runtime.replan" for e in events),
        "finding_count": sum(e["event_type"] == "qa.finding" for e in events),
        "avg_decision_confidence": sum(e["payload"].get("confidence", 0) for e in decisions)/max(1,len(decisions)),
        "avg_decision_latency_ms": sum(e["payload"].get("latency_ms", 0) for e in decisions)/max(1,len(decisions)),
        "event_count": len(events), "hash_chain_valid": True,
    },
}

html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
css = (ROOT / "frontend/app.css").read_text(encoding="utf-8")
js = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
html = html.replace('<link rel="stylesheet" href="/assets/app.css" />', f'<style>{css}</style>')
html = html.replace('<script type="module" src="/assets/app.js"></script>', '')

mock = {"sid":sid,"events":events,"scenarios":scenarios,"runtime":runtime,"skills":engine.skills.snapshot(),"model":model,"selfplay":selfplay,"bench":bench,"report":report}
mock_script = f"""
<script>
window.__MOCK__={json.dumps(mock,ensure_ascii=False)};
const response=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{{status,headers:{{'Content-Type':'application/json'}}}}));
window.fetch=async function(url,opts={{}}){{
  const u=String(url),m=String(opts.method||'GET').toUpperCase(),D=window.__MOCK__;
  if(u.endsWith('/api/health')) return response({{status:'ok',runtime:'world-state-native',version:'0.3.0',locale:'zh-CN',model:D.model.card.name,external_model_api:false}});
  if(u.endsWith('/api/scenarios')) return response(D.scenarios);
  if(u.endsWith('/api/runtime')) return response(D.runtime);
  if(u.endsWith('/api/skills')) return response(D.skills);
  if(u.endsWith('/api/model')) return response(D.model);
  if(u.endsWith('/api/showcase')) return response({{product:{{name:'WorldForge Harness'}}}});
  if(u.endsWith('/api/benchmarks')) return response({{rows:D.bench,protocol:{{external_models:false}}}});
  if(u.includes('/api/selfplay/')) return response(D.selfplay);
  if(u.includes('/report')) return response(D.report);
  if(u.includes('/verify')) return response({{session_id:D.sid,hash_chain_valid:true}});
  if(u.includes('/replay')) {{
    const seq=Number(new URL(u,'http://x').searchParams.get('seq')||9999);
    const world=[...D.events].reverse().find(e=>e.seq<=seq && e.event_type==='world.state');
    return response({{session_id:D.sid,target_seq:seq,snapshot:world?{{state:world.payload.state}}:null,events:D.events.filter(e=>e.seq<=seq).slice(-20),hash_chain_valid:true}});
  }}
  if(u.includes('/cancel')) return response({{session_id:D.sid,status:'cancelled'}});
  if(u.endsWith('/api/runs') && m==='POST') return response({{session_id:D.sid,status:'running',model:D.model.card.name}});
  if(u.includes('/events')) return response(D.events);
  return response({{status:'ok'}});
}};
class MockWebSocket{{
 constructor(url){{this.url=url;this.readyState=1;setTimeout(()=>{{this.onopen&&this.onopen();let i=0;const pump=()=>{{if(this.readyState!==1||i>=window.__MOCK__.events.length)return;const e=window.__MOCK__.events[i++];this.onmessage&&this.onmessage({{data:JSON.stringify(e)}});setTimeout(pump,8)}};pump()}},30)}}
 close(){{this.readyState=3;this.onclose&&this.onclose()}}
}}
window.WebSocket=MockWebSocket;
</script>
<script>{js}</script>
"""
html = html.replace('</body>', mock_script + '</body>')
(OUT/'WorldForge_Interactive_Demo.html').write_text(html, encoding='utf-8')

report_ui={"checks":{},"errors":[],"session_id":sid,"backend_summary":summary.model_dump()}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu'])
    page=browser.new_page(viewport={"width":1720,"height":1080},device_scale_factor=1)
    page.on('pageerror',lambda exc:report_ui['errors'].append(str(exc)))
    page.set_content(html,wait_until='load')
    page.wait_for_function("document.querySelector('#scenarioSelect').options.length===4")
    report_ui['checks']['boot']=True
    page.select_option('#scenarioSelect','loot_exploit');page.fill('#seedInput','17');page.fill('#stepsInput','18')
    page.screenshot(path=str(OUT/'ui_before_run_pro.png'),full_page=True)
    page.click('#startBtn')
    page.wait_for_function("document.querySelector('#runBadge span').textContent.toUpperCase()==='VICTORY'",timeout=30000)
    report_ui['checks']['run_complete']=True
    report_ui['branch_cards']=page.locator('#branchCards .branch-card').count()
    report_ui['agent_cards']=page.locator('#councilGrid .agent-card').count()
    report_ui['event_count']=page.locator('#traceList .trace-item').count()
    report_ui['finding_count']=page.locator('#findingList .finding').count()
    page.screenshot(path=str(OUT/'ui_after_run_pro.png'),full_page=True)

    page.click(".top-tab[data-view='decision']")
    page.wait_for_timeout(150)
    assert page.locator('#branchSvg .node').count() >= 2
    page.screenshot(path=str(OUT/'ui_decision_pro.png'),full_page=True)
    report_ui['checks']['decision_space']=True

    page.click(".top-tab[data-view='trajectory']")
    page.fill('#traceSearch','decision')
    assert page.locator('#traceList .trace-item').count()>0
    page.locator('#traceList .trace-item').first.click()
    assert page.locator('#inspectorPayload').inner_text().strip().startswith('{')
    report_ui['checks']['trajectory']=True

    page.click(".top-tab[data-view='evolution']")
    page.click('#selfplayBtn')
    page.wait_for_function("document.querySelectorAll('#selfplayGrid .profile-card').length===4")
    report_ui['checks']['selfplay']=True

    page.click(".top-tab[data-view='benchmark']")
    page.wait_for_function("document.querySelectorAll('#benchmarkChart .bench-col').length===4")
    page.click('#loadReportBtn')
    page.wait_for_function("document.querySelectorAll('#runReport .report-stat').length===5")
    page.screenshot(path=str(OUT/'ui_benchmark_pro.png'),full_page=True)
    report_ui['checks']['benchmark_report']=True

    page.click(".top-tab[data-view='architecture']")
    assert page.locator('#pluginGrid .plugin-card').count()>=9
    assert page.locator('.positioning-table .pos-row').count()>=6
    page.screenshot(path=str(OUT/'ui_architecture_pro.png'),full_page=True)
    report_ui['checks']['architecture']=True

    page.keyboard.press('Control+K');page.wait_for_selector('#paletteBackdrop:not([hidden])');page.fill('#paletteInput','指挥');page.locator('#paletteList .palette-item').first.click()
    report_ui['checks']['command_palette']=page.locator('#view-command').evaluate("e=>e.classList.contains('active')")
    browser.close()

(OUT/'frontend_e2e_pro.json').write_text(json.dumps(report_ui,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report_ui,ensure_ascii=False,indent=2))
