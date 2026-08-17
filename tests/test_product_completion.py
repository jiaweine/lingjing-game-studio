from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from worldforge.api.app import app
from worldforge.product.analyzer import ProductAnalyzer
from worldforge.product.store import ConversationStore
from worldforge.runtime.evolver import FailureDrivenEvolver
from worldforge.runtime.skill_bank import SkillBank
from worldforge.storage import LocalObjectStorage


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _store_owner(store: ConversationStore):
    return store.create_user_workspace(
        email=_email("owner"),
        name="Owner",
        password_hash="hashed",
        workspace_name="Product Lab",
    )


def test_task_lifecycle_retry_guard_and_metrics(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    workspace_id = owner["workspace_id"]
    user_id = owner["user_id"]
    conversation = store.create_conversation(
        "Boss 秒杀复现",
        workspace_id=workspace_id,
        created_by=user_id,
    )

    assert store.list_conversations(workspace_id=workspace_id, query="秒杀")[0]["id"] == conversation["id"]
    updated = store.update_conversation(conversation["id"], workspace_id=workspace_id, pinned=True, title="Boss 二阶段秒杀")
    assert updated["pinned"] == 1

    first = store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "复现", "asset_ids": []})
    with pytest.raises(ValueError, match="已有执行"):
        store.enqueue_job(workspace_id=workspace_id, conversation_id=conversation["id"], payload={"text": "重复", "asset_ids": []})
    claimed = store.claim_job("test-worker", job_id=first["id"])
    assert claimed and claimed["status"] == "running"
    store.cancel_job(first["id"], workspace_id=workspace_id)
    assert store.get_conversation(conversation["id"], workspace_id=workspace_id)["status"] == "stopped"

    retry = store.retry_job(first["id"], workspace_id=workspace_id)
    with pytest.raises(ValueError, match="最新|执行"):
        store.retry_job(first["id"], workspace_id=workspace_id)
    with pytest.raises(ValueError, match="执行"):
        store.archive_conversation(conversation["id"], workspace_id=workspace_id)

    claimed_retry = store.claim_job("test-worker", job_id=retry["id"])
    assert claimed_retry and claimed_retry["status"] == "running"
    message = store.complete_job_answer(
        retry["id"],
        workspace_id=workspace_id,
        content="已验证",
        payload={"evidence": [], "deliverables": []},
    )
    assert message

    store.add_message(conversation["id"], "user", "继续检查", workspace_id=workspace_id)
    store.record_product_event(workspace_id=workspace_id, user_id=user_id, conversation_id=conversation["id"], name="evidence.open")
    store.record_product_event(workspace_id=workspace_id, user_id=user_id, conversation_id=conversation["id"], name="evidence.open")
    store.record_product_event(workspace_id=workspace_id, user_id=user_id, conversation_id=conversation["id"], name="result.copy")
    store.upsert_feedback(
        workspace_id=workspace_id,
        user_id=user_id,
        message_id=message["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
    )

    metrics = store.product_metrics(workspace_id=workspace_id)
    assert metrics["first_task_completion_rate"] == 1.0
    assert metrics["recovery_rate"] == 1.0
    assert metrics["evidence_open_rate"] == 1.0
    assert metrics["result_adoption_rate"] == 1.0
    assert metrics["manual_intervention_rate"] == 1.0
    assert 0 <= metrics["interruption_rate"] <= 1
    assert metrics["avg_time_to_verified_seconds"] is not None

    archived = store.archive_conversation(conversation["id"], workspace_id=workspace_id)
    assert archived["archived_at"] is not None
    assert store.list_conversations(workspace_id=workspace_id) == []
    assert store.list_conversations(workspace_id=workspace_id, archived=True)[0]["id"] == conversation["id"]
    assert store.restore_conversation(conversation["id"], workspace_id=workspace_id)["archived_at"] is None


def test_feedback_gate_is_human_verified_and_vetoable(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    other = store.create_user_workspace(
        email=_email("reviewer"),
        name="Reviewer",
        password_hash="hashed",
        workspace_name="Other Lab",
    )
    store.add_member_by_email(owner["workspace_id"], other["email"], "member")
    conversation = store.create_conversation(
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    answer = store.add_message(
        conversation["id"],
        "assistant",
        "结论",
        {"evidence": []},
        workspace_id=owner["workspace_id"],
    )

    store.upsert_feedback(
        workspace_id=owner["workspace_id"],
        user_id=owner["user_id"],
        message_id=answer["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
    )
    assert store.feedback_gate(conversation["id"], workspace_id=owner["workspace_id"])["approved"] is True

    store.upsert_feedback(
        workspace_id=owner["workspace_id"],
        user_id=other["user_id"],
        message_id=answer["id"],
        verdict="incorrect",
        evidence_useful=False,
        human_verified=False,
        note="证据不足",
    )
    gate = store.feedback_gate(conversation["id"], workspace_id=owner["workspace_id"])
    assert gate["approved"] is False
    assert gate["incorrect"] == 1


def test_human_feedback_vetoes_runtime_evolution():
    bank = SkillBank()
    evolver = FailureDrivenEvolver(bank)
    signal = evolver.attribute(
        outcome="defeat",
        min_hp=10,
        farm_count=0,
        invalid_actions=0,
        last_action="heavy_attack",
    )
    assert signal is not None
    patch = evolver.evolve(
        signal,
        lambda candidate: 1.05 if candidate is not None else 1.0,
        human_approved=False,
    )
    assert patch.accepted is False
    assert "human feedback gate" in patch.reason


def test_structured_deliverables_are_scene_specific():
    analyzer = object.__new__(ProductAnalyzer)
    evidence = [{"id": "ev-1", "title": "battle.log"}]
    battle = analyzer._deliverables("battle_review", evidence, None)
    balance = analyzer._deliverables("balance", evidence, None)
    assert {row["type"] for row in battle} >= {"reproduction_card", "regression_checklist", "evidence_pack"}
    assert {row["type"] for row in balance} >= {"risk_register", "tuning_plan", "evidence_pack"}
    assert all(row["evidence_ids"] == ["ev-1"] for row in battle)


def test_local_storage_delete_removes_object(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    storage.put_bytes("ws/task/file.txt", b"evidence", "text/plain")
    assert storage.get_bytes("ws/task/file.txt") == b"evidence"
    storage.delete("ws/task/file.txt")
    assert not (tmp_path / "objects" / "ws" / "task" / "file.txt").exists()


def test_invite_registration_role_management_and_delete_approval():
    owner_client = TestClient(app)
    owner_email = _email("api-owner")
    owner_registration = owner_client.post(
        "/api/auth/register",
        json={
            "email": owner_email,
            "password": "strong-password-123",
            "name": "API Owner",
            "workspace_name": f"Studio {uuid.uuid4().hex[:6]}",
        },
    )
    assert owner_registration.status_code == 200
    owner_session = owner_registration.json()

    invite_response = owner_client.post(
        "/api/workspace/invites",
        json={"role": "member", "email": None},
    )
    assert invite_response.status_code == 200
    invite = invite_response.json()

    member_client = TestClient(app)
    member_email = _email("api-member")
    member_registration = member_client.post(
        "/api/auth/register",
        json={
            "email": member_email,
            "password": "strong-password-456",
            "name": "API Member",
            "workspace_name": "unused-for-invite",
            "invite_token": invite["token"],
        },
    )
    assert member_registration.status_code == 200
    member_session = member_registration.json()
    assert member_session["workspace"]["id"] == owner_session["workspace"]["id"]
    assert member_session["user"]["role"] == "member"

    members = owner_client.get("/api/workspace/members")
    assert members.status_code == 200
    member = next(row for row in members.json() if row["email"] == member_email)
    role_change = owner_client.patch(
        f"/api/workspace/members/{member['id']}",
        json={"role": "viewer"},
    )
    assert role_change.status_code == 200
    forbidden = member_client.patch(
        f"/api/workspace/members/{member['id']}",
        json={"role": "admin"},
    )
    assert forbidden.status_code == 403

    conversation = owner_client.post(
        "/api/conversations",
        json={"title": "受保护删除任务", "scene": "regression"},
    ).json()
    approval = owner_client.post(
        f"/api/conversations/{conversation['id']}/delete-request",
        json={},
    )
    assert approval.status_code == 200
    pending = approval.json()
    blocked_delete = owner_client.delete(
        f"/api/conversations/{conversation['id']}?approval_id={pending['id']}"
    )
    assert blocked_delete.status_code == 409

    resolved = owner_client.post(
        f"/api/approvals/{pending['id']}/resolve",
        json={"approved": True},
    )
    assert resolved.status_code == 200
    deleted = owner_client.delete(
        f"/api/conversations/{conversation['id']}?approval_id={pending['id']}"
    )
    assert deleted.status_code == 200
    assert owner_client.get(f"/api/conversations/{conversation['id']}").status_code == 404



def test_role_demotion_and_removal_take_effect_without_relogin():
    owner_client = TestClient(app)
    owner_registration = owner_client.post(
        "/api/auth/register",
        json={
            "email": _email("fresh-owner"),
            "password": "strong-password-owner",
            "name": "Fresh Owner",
            "workspace_name": f"Fresh Studio {uuid.uuid4().hex[:6]}",
        },
    )
    assert owner_registration.status_code == 200

    invite = owner_client.post(
        "/api/workspace/invites",
        json={"role": "admin", "email": None},
    ).json()
    admin_client = TestClient(app)
    admin_email = _email("fresh-admin")
    admin_registration = admin_client.post(
        "/api/auth/register",
        json={
            "email": admin_email,
            "password": "strong-password-admin",
            "name": "Fresh Admin",
            "workspace_name": "unused",
            "invite_token": invite["token"],
        },
    )
    assert admin_registration.status_code == 200
    assert admin_client.post(
        "/api/workspace/invites", json={"role": "member", "email": None}
    ).status_code == 200

    member = next(
        row for row in owner_client.get("/api/workspace/members").json()
        if row["email"] == admin_email
    )
    demoted = owner_client.patch(
        f"/api/workspace/members/{member['id']}", json={"role": "viewer"}
    )
    assert demoted.status_code == 200
    # Same cookie/JWT: require_principal must refresh current membership.
    assert admin_client.post(
        "/api/workspace/invites", json={"role": "member", "email": None}
    ).status_code == 403
    assert admin_client.get("/api/conversations").status_code == 200
    assert admin_client.post(
        "/api/conversations", json={"title": "viewer cannot write", "scene": "regression"}
    ).status_code == 403
    owner_task = owner_client.post(
        "/api/conversations", json={"title": "owner task", "scene": "regression"}
    ).json()
    assert admin_client.post(
        f"/api/conversations/{owner_task['id']}/messages",
        json={"content": "viewer should not execute", "asset_ids": [], "provider": "auto"},
    ).status_code == 403

    removed = owner_client.delete(f"/api/workspace/members/{member['id']}")
    assert removed.status_code == 200
    # Removal invalidates the old workspace session immediately.
    assert admin_client.get("/api/conversations").status_code == 401



@pytest.mark.asyncio
async def test_product_analyzer_enables_evolution_only_after_human_gate():
    class Summary:
        steps = 4
        outcome = "success"

        def model_dump(self):
            return {"steps": self.steps, "outcome": self.outcome}

    class Engine:
        def __init__(self):
            self.calls = []

        async def run(self, config, **kwargs):
            self.calls.append((config, kwargs))
            return Summary()

    class Providers:
        @staticmethod
        def choose(provider_key, assets):
            return None

    async def sink(event_type, payload):
        return None

    engine = Engine()
    analyzer = ProductAnalyzer(engine, Providers())
    await analyzer.run(
        text="战斗异常需要复核",
        assets=[],
        provider_key="auto",
        sink=sink,
        human_feedback_gate=False,
    )
    config, kwargs = engine.calls[-1]
    assert config.enable_evolution is False
    assert kwargs["session_meta"]["human_feedback_gate"] is False

    await analyzer.run(
        text="战斗异常需要复核",
        assets=[],
        provider_key="auto",
        sink=sink,
        human_feedback_gate=True,
    )
    config, kwargs = engine.calls[-1]
    assert config.enable_evolution is True
    assert kwargs["session_meta"]["human_feedback_gate"] is True



def test_read_only_member_cannot_be_task_assignee(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets", seed_dev_identity=False)
    owner = _store_owner(store)
    viewer_workspace = store.create_user_workspace(
        email=_email("viewer-assignee"),
        name="Viewer",
        password_hash="hashed",
        workspace_name="Viewer Home",
    )
    store.add_member_by_email(owner["workspace_id"], viewer_workspace["email"], "viewer")
    conversation = store.create_conversation(
        workspace_id=owner["workspace_id"], created_by=owner["user_id"]
    )
    with pytest.raises(ValueError, match="可执行任务"):
        store.update_conversation(
            conversation["id"],
            workspace_id=owner["workspace_id"],
            assigned_to=viewer_workspace["user_id"],
        )
