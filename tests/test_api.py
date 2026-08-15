from fastapi.testclient import TestClient
from worldforge.api.app import app

def test_health_and_runtime():
    c=TestClient(app);health=c.get('/api/health');assert health.status_code==200;assert health.json()['product']=='灵境游戏工作台';data=c.get('/api/runtime').json();assert data['decision_model']['counterfactual'] is True;assert len(data['plugins'])>=5

def test_scenarios():
    c=TestClient(app);r=c.get('/api/scenarios');assert r.status_code==200 and len(r.json())>=4

def test_provider_gateway_and_product_info():
    c=TestClient(app);providers=c.get('/api/providers').json();keys={x['key'] for x in providers};assert {'auto','demo','deepseek','qwen','doubao','openai','anthropic','gemini'}<=keys;product=c.get('/api/product').json();assert product['name']=='灵境游戏工作台';assert '视频' in product['accepted'] and len(product['scenes'])>=5

def test_conversation_roundtrip():
    c=TestClient(app);conv=c.post('/api/conversations',json={'title':'测试任务','scene':'battle_review'}).json();r=c.get(f"/api/conversations/{conv['id']}");assert r.status_code==200;assert r.json()['title']=='测试任务'
