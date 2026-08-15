from __future__ import annotations
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from worldforge.api.app import app
ASSETS=ROOT/'outputs/demo_assets'
report={'checks':{},'conversation_id':None,'events':[],'errors':[]}
with TestClient(app) as c:
    health=c.get('/api/health'); assert health.status_code==200
    report['checks']['backend_health']=True
    providers=c.get('/api/providers').json(); assert len(providers)>=7
    report['checks']['provider_gateway']=True
    conv=c.post('/api/conversations',json={'title':'Boss 战多模态复盘','scene':'battle_review'}).json(); cid=conv['id']; report['conversation_id']=cid
    ids=[]
    for fn,mime in [('boss_frame.png','image/png'),('boss_replay.mp4','video/mp4'),('battle.log','text/plain')]:
        with (ASSETS/fn).open('rb') as f:r=c.post('/api/assets',files={'file':(fn,f,mime)},data={'conversation_id':cid})
        assert r.status_code==200, r.text; d=r.json(); ids.append(d['id'])
        if fn.endswith('.mp4'): assert len(d['meta'].get('keyframes',[]))>=1
    report['checks']['multimodal_ingest']=True
    r=c.post(f'/api/conversations/{cid}/messages',json={'content':'帮我复盘这场 Boss 战，重点看 1 分钟之后为什么会突然掉血，并给研发一个可执行的排查顺序。','asset_ids':ids,'provider':'demo'}); assert r.status_code==200, r.text
    data=c.get(f'/api/conversations/{cid}').json(); assert len(data['messages'])==2; assert data['messages'][-1]['role']=='assistant'; assert len(data['messages'][-1]['payload'].get('evidence',[]))>=4
    report['checks']['conversation_analysis']=True; report['answer']=data['messages'][-1]['content']; first_context=data['messages'][-1]['payload'].get('context',{}); assert first_context.get('task_assets')==3
    report['events']=[x['type'] for x in data['events']]; assert report['events'][-1]=='answer.ready'; report['checks']['task_event_chain']=True
    with c.websocket_connect(f'/ws/conversations/{cid}') as ws:
        first=ws.receive_json(); assert first['type']=='message.accepted'; seen=[first['type']]
        for _ in range(20):
            ev=ws.receive_json(); seen.append(ev.get('type'))
            if ev.get('type')=='answer.ready': break
        assert 'progress' in seen and 'answer.ready' in seen
    report['checks']['websocket_history']=True
    r=c.post(f'/api/conversations/{cid}/messages',json={'content':'那减伤覆盖和技能冷却是不是撞在同一个时间窗？','asset_ids':[],'provider':'demo'}); assert r.status_code==200, r.text
    data=c.get(f'/api/conversations/{cid}').json(); assert len(data['messages'])==4; follow=data['messages'][-1]; assert follow['role']=='assistant'; assert follow['payload'].get('intent')=='battle_review'; assert follow['payload'].get('context',{}).get('history_messages')==2; assert follow['payload'].get('context',{}).get('task_assets')==3; assert len(follow['payload'].get('evidence',[]))>=4
    report['checks']['followup_context']=True; report['provider_count']=len(providers); report['asset_count']=len(data['assets']); report['message_count']=len(data['messages']); report['evidence_count']=len(data['messages'][-1]['payload'].get('evidence',[]))
out=ROOT/'outputs/product_backend_e2e.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
