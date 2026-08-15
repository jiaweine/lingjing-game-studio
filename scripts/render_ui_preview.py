from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
from worldforge.runtime import EventStore

report=json.loads((ROOT/'outputs/e2e_report.json').read_text())
sid=report['session_id']
store=EventStore(ROOT/'outputs/runtime/worldforge.db')
events=store.list_events(sid)
world=[e for e in events if e.event_type=='world.state'][-1].payload
planner=[e for e in events if e.event_type=='planner.candidates'][-1].payload
branch=[e for e in events if e.event_type=='counterfactual.evaluated'][-1].payload
bench=json.loads((ROOT/'outputs/local_benchmark.json').read_text())['rows']
complete=[e for e in events if e.event_type=='run.completed'][-1].payload
skills=complete['skills']
plugins=[e for e in events if e.event_type=='run.started'][0].payload['plugins']
scenario=[e for e in events if e.event_type=='run.started'][0].payload['scenario']

html=(ROOT/'frontend/index.html').read_text()
css=(ROOT/'frontend/app.css').read_text()
html=html.replace('<link rel="stylesheet" href="/assets/app.css" />',f'<style>{css}</style>').replace('<script type="module" src="/assets/app.js"></script>','')
payload={"world":world,"planner":planner,"branch":branch,"bench":bench,"skills":skills,"plugins":plugins,"scenario":scenario,"events":[e.model_dump() for e in events[-10:]],"summary":report['summary']}

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu'])
    page=browser.new_page(viewport={'width':1600,'height':1100})
    page.set_content(html,wait_until='load')
    page.evaluate('''(d)=>{
      const $=id=>document.getElementById(id); const cap=s=>String(s||'').replaceAll('_',' ').replace(/\\b\\w/g,x=>x.toUpperCase()); const fmt=(n,k=1)=>Number(n||0).toFixed(k);
      $('scenarioSelect').innerHTML=`<option>${d.scenario.name}</option>`; $('scenarioDesc').textContent=`${d.scenario.difficulty} · ${d.scenario.description}`; $('missionTitle').textContent=d.scenario.name; $('runBadge').textContent=String(d.summary.outcome).toUpperCase(); $('runBadge').className='run-badge done';
      const s=d.world.state,b=d.world.belief; $('playerHpText').textContent=`${s.player_hp} / ${s.player_max_hp}`; $('playerHpBar').style.width=`${100*s.player_hp/s.player_max_hp}%`; $('enemyHpText').textContent=`${s.enemy_hp} / ${s.enemy_max_hp}`; $('enemyHpBar').style.width=`${100*s.enemy_hp/s.enemy_max_hp}%`; $('atkVal').textContent=s.attack; $('armorVal').textContent=s.armor; $('energyVal').textContent=s.energy; $('goldVal').textContent=s.gold; $('scoreVal').textContent=fmt(s.score); $('tickVal').textContent=s.tick; $('threatVal').textContent=fmt(s.threat,2); $('threatFill').style.width=`${100*s.threat}%`; $('stateVal').textContent=cap(s.outcome||'live'); $('goalText').textContent=d.scenario.goal.primary; $('beliefText').textContent=`${b.enemy_behavior} · U=${fmt(b.uncertainty,2)}`; $('lastActionText').textContent=cap(s.last_action); $('traceHash').textContent=d.events[d.events.length-1].hash.slice(0,10);
      $('pluginList').innerHTML=d.plugins.map(p=>`<div class='plugin'><span></span><b>${p.name}</b><em>${p.capability}</em></div>`).join('');
      $('skillCount').textContent=`${d.skills.filter(x=>x.status==='active').length} ACTIVE`; $('skillList').innerHTML=d.skills.map(x=>`<div class='skill-card'><div class='skill-title'>${x.name}<span>v${x.version}</span></div><div class='skill-rate'>${Math.round(x.success_rate*100)}%</div><div class='skill-desc'>${x.description}</div></div>`).join('');
      const votes=d.planner.council||[],by={}; votes.forEach(v=>{if(!by[v.agent]||v.score>by[v.agent].score)by[v.agent]=v}); $('councilGrid').innerHTML=Object.values(by).map(v=>`<div class='agent-card'><div class='agent-top'><div class='agent-name'>${v.agent}</div><div class='agent-choice'>${cap(v.action)}</div></div><div class='agent-score'>${v.score>=0?'+':''}${fmt(v.score,2)}</div><div class='agent-reason'>${v.reason}</div></div>`).join('');
      const bs=d.branch.branches||[],best=Math.max(...bs.map(x=>x.score)),cx=380,cy=58,y=205,gap=680/Math.max(1,bs.length); let svg=`<circle cx='${cx}' cy='${cy}' r='34' class='node-root'/><text x='${cx}' y='${cy-3}' text-anchor='middle' class='node-title'>WORLD</text><text x='${cx}' y='${cy+12}' text-anchor='middle' class='node-score'>t=${s.tick}</text>`; bs.forEach((x,i)=>{const xx=40+gap/2+i*gap,q=x.score===best;svg+=`<line x1='${cx}' y1='${cy+34}' x2='${xx}' y2='${y-34}' class='branch-link ${q?'best':''}'/><rect x='${xx-62}' y='${y-34}' rx='10' width='124' height='68' class='node-branch ${q?'best':''}'/><text x='${xx}' y='${y-8}' text-anchor='middle' class='node-title'>${cap(x.first_action)}</text><text x='${xx}' y='${y+8}' text-anchor='middle' class='node-score ${q?'best':''}'>U ${fmt(x.score,1)} · S ${Math.round(x.survival*100)}%</text><text x='${xx}' y='${y+22}' text-anchor='middle' class='node-score'>Pwin ${Math.round(x.success_probability*100)}%</text>`}); $('branchSvg').innerHTML=svg; $('decisionConfidence').textContent=`${bs.length} BRANCHES`; $('decisionRationale').textContent='Actual E2E trace: exact-state branches were simulated without mutating canonical world state.';
      $('traceList').innerHTML=d.events.map(e=>`<div class='trace-item'><div class='trace-seq'>#${String(e.seq).padStart(3,'0')}</div><div class='trace-type ${e.event_type.startsWith('decision')?'decision':e.event_type.startsWith('runtime')?'runtime':e.event_type.startsWith('evolution')?'evolution':''}'>${e.event_type}</div><div class='trace-summary'>recorded E2E runtime event</div><div class='trace-hash'>${e.hash.slice(0,9)}</div></div>`).join('');
      const mx=Math.max(...d.bench.map(r=>r.success_rate)); $('benchmarkChart').innerHTML=d.bench.map(r=>`<div class='bench-col ${r.success_rate===mx?'best':''}'><div class='bench-value'>${Math.round(r.success_rate*100)}%</div><div class='bench-label'>${r.harness}</div><div class='bench-bar-wrap'><div class='bench-bar' style='height:${100*r.success_rate}%'></div></div><div class='bench-meta'>score ${fmt(r.avg_score)} · local</div></div>`).join('');
    }''',payload)
    page.screenshot(path=str(ROOT/'outputs/ui_preview.png'),full_page=True)
    browser.close()
print(ROOT/'outputs/ui_preview.png')
