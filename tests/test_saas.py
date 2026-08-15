from __future__ import annotations

import uuid
import pytest
from fastapi.testclient import TestClient
from worldforge.api.app import app
from worldforge.product.store import ConversationStore
from worldforge.security import Principal,create_access_token,decode_access_token,hash_password,verify_password
from worldforge.settings import settings

def test_password_and_token_roundtrip():
    encoded=hash_password("correct-horse-battery");assert verify_password(encoded,"correct-horse-battery") is True;assert verify_password(encoded,"wrong-password") is False;p=Principal(user_id="u1",workspace_id="w1",email="dev@example.com",role="owner");token=create_access_token(p,settings);restored=decode_access_token(token,settings);assert restored.user_id=="u1";assert restored.workspace_id=="w1"

def test_store_enforces_workspace_isolation(tmp_path):
    store=ConversationStore(tmp_path/"product.db",tmp_path/"assets");a=store.create_user_workspace(email="a@example.com",name="A",password_hash="x",workspace_name="Studio A");b=store.create_user_workspace(email="b@example.com",name="B",password_hash="x",workspace_name="Studio B");conv=store.create_conversation("Secret A",workspace_id=a["workspace_id"],created_by=a["user_id"]);assert store.get_conversation(conv["id"],workspace_id=a["workspace_id"])["title"]=="Secret A"
    with pytest.raises(KeyError):store.get_conversation(conv["id"],workspace_id=b["workspace_id"])

def _register(client,prefix):
    email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com";r=client.post("/api/auth/register",json={"email":email,"password":"a-strong-password-123","name":prefix,"workspace_name":f"{prefix} Workspace"});assert r.status_code==200,r.text;return r.json()

def test_api_tenant_isolation_and_audit():
    owner_a=TestClient(app);owner_b=TestClient(app);session_a=_register(owner_a,"alpha");session_b=_register(owner_b,"beta");assert session_a["workspace"]["id"]!=session_b["workspace"]["id"];conv=owner_a.post("/api/conversations",json={"title":"Alpha only","scene":"battle_review"});assert conv.status_code==200;cid=conv.json()["id"];assert owner_a.get(f"/api/conversations/{cid}").status_code==200;assert owner_b.get(f"/api/conversations/{cid}").status_code==404;audit=owner_a.get("/api/audit").json();actions={x["action"] for x in audit};assert "auth.register" in actions;assert "conversation.create" in actions

def test_security_headers_and_readiness():
    c=TestClient(app);r=c.get("/api/health");assert r.status_code==200;assert r.headers["x-content-type-options"]=="nosniff";assert r.headers["x-frame-options"]=="DENY";assert "content-security-policy" in r.headers;ready=c.get("/api/health/ready");assert ready.status_code==200;assert ready.json()["checks"]["database"] is True
