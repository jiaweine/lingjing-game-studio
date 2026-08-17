from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    delete,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

DEMO_WORKSPACE_ID = "workspace-demo"
DEMO_USER_ID = "user-demo"


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.strip()).strip("-").lower()
    return value[:36] or f"workspace-{uuid.uuid4().hex[:6]}"


class ConversationStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        asset_dir: str | Path = "assets",
        *,
        database_url: str | None = None,
        auto_create_schema: bool = True,
        seed_dev_identity: bool = True,
    ) -> None:
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        if database_url:
            url = database_url
        else:
            path = Path(db_path or "product.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.as_posix()}"
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(url, **kwargs)
        self.metadata = MetaData()
        self._define_tables()
        if auto_create_schema:
            self.metadata.create_all(self.engine)
        if seed_dev_identity:
            self.ensure_dev_identity()

    def _define_tables(self) -> None:
        self.users = Table(
            "users",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("email", String(320), nullable=False, unique=True),
            Column("name", String(120), nullable=False),
            Column("password_hash", Text, nullable=False),
            Column("status", String(32), nullable=False, default="active"),
            Column("created_at", Float, nullable=False),
        )
        self.workspaces = Table(
            "workspaces",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("name", String(160), nullable=False),
            Column("slug", String(80), nullable=False, unique=True),
            Column("plan", String(32), nullable=False, default="team"),
            Column("created_at", Float, nullable=False),
        )
        self.memberships = Table(
            "memberships",
            self.metadata,
            Column("workspace_id", String(64), primary_key=True),
            Column("user_id", String(64), primary_key=True),
            Column("role", String(32), nullable=False),
            Column("created_at", Float, nullable=False),
        )
        self.conversations = Table(
            "conversations",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("created_by", String(64), nullable=False),
            Column("assigned_to", String(64), nullable=True, index=True),
            Column("title", String(240), nullable=False),
            Column("scene", String(80), nullable=False),
            Column("status", String(32), nullable=False, default="active", index=True),
            Column("pinned", Integer, nullable=False, default=0),
            Column("archived_at", Float, nullable=True, index=True),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        self.messages = Table(
            "messages",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("role", String(24), nullable=False),
            Column("content", Text, nullable=False),
            Column("payload", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
        )
        self.assets = Table(
            "assets",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("created_by", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=True, index=True),
            Column("name", String(512), nullable=False),
            Column("mime", String(160), nullable=False),
            Column("path", Text, nullable=False),
            Column("storage_backend", String(32), nullable=False, default="local"),
            Column("size", BigInteger, nullable=False),
            Column("meta", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
        )
        self.task_events = Table(
            "task_events",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("type", String(96), nullable=False),
            Column("payload", Text, nullable=False),
            Column("created_at", Float, nullable=False),
        )
        self.audit_logs = Table(
            "audit_logs",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workspace_id", String(64), nullable=True, index=True),
            Column("user_id", String(64), nullable=True),
            Column("request_id", String(64), nullable=False, index=True),
            Column("action", String(120), nullable=False),
            Column("resource_type", String(80), nullable=True),
            Column("resource_id", String(96), nullable=True),
            Column("payload", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
        )
        self.jobs = Table(
            "analysis_jobs",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("status", String(32), nullable=False, index=True),
            Column("payload", Text, nullable=False),
            Column("attempts", Integer, nullable=False, default=0),
            Column("worker_id", String(96), nullable=True),
            Column("last_error", Text, nullable=True),
            Column("created_at", Float, nullable=False),
            Column("available_at", Float, nullable=False),
            Column("claimed_at", Float, nullable=True),
            Column("completed_at", Float, nullable=True),
        )
        self.workspace_invites = Table(
            "workspace_invites",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("token", String(96), nullable=False, unique=True, index=True),
            Column("email", String(320), nullable=True),
            Column("role", String(32), nullable=False),
            Column("status", String(32), nullable=False, index=True),
            Column("created_by", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            Column("expires_at", Float, nullable=False),
            Column("accepted_by", String(64), nullable=True),
            Column("accepted_at", Float, nullable=True),
        )
        self.approval_requests = Table(
            "approval_requests",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("action", String(96), nullable=False),
            Column("status", String(32), nullable=False, index=True),
            Column("reason", Text, nullable=False, default=""),
            Column("payload", Text, nullable=False, default="{}"),
            Column("requested_by", String(64), nullable=False),
            Column("resolved_by", String(64), nullable=True),
            Column("created_at", Float, nullable=False),
            Column("resolved_at", Float, nullable=True),
        )
        self.result_feedback = Table(
            "result_feedback",
            self.metadata,
            Column("message_id", String(64), primary_key=True),
            Column("user_id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("verdict", String(32), nullable=False),
            Column("evidence_useful", Integer, nullable=True),
            Column("human_verified", Integer, nullable=False, default=0),
            Column("note", Text, nullable=False, default=""),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        self.product_events = Table(
            "product_events",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("user_id", String(64), nullable=False),
            Column("conversation_id", String(64), nullable=True, index=True),
            Column("name", String(96), nullable=False, index=True),
            Column("payload", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
        )

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row._mapping if hasattr(row, "_mapping") else row)

    @staticmethod
    def _json_row(row: Any, field: str = "payload") -> dict[str, Any]:
        data = ConversationStore._dict(row)
        data[field] = json.loads(data.get(field) or "{}")
        return data

    def ensure_dev_identity(self) -> None:
        now = time.time()
        with self.engine.begin() as connection:
            if connection.execute(select(self.workspaces.c.id).where(self.workspaces.c.id == DEMO_WORKSPACE_ID)).first() is None:
                connection.execute(insert(self.workspaces).values(id=DEMO_WORKSPACE_ID, name="本地演示空间", slug="local-demo", plan="dev", created_at=now))
            if connection.execute(select(self.users.c.id).where(self.users.c.id == DEMO_USER_ID)).first() is None:
                connection.execute(insert(self.users).values(id=DEMO_USER_ID, email="demo@local.lingjing", name="本地用户", password_hash="!dev-only", status="active", created_at=now))
            exists = connection.execute(select(self.memberships.c.user_id).where(and_(self.memberships.c.workspace_id == DEMO_WORKSPACE_ID, self.memberships.c.user_id == DEMO_USER_ID))).first()
            if exists is None:
                connection.execute(insert(self.memberships).values(workspace_id=DEMO_WORKSPACE_ID, user_id=DEMO_USER_ID, role="owner", created_at=now))

    def create_user_workspace(self, *, email: str, name: str, password_hash: str, workspace_name: str) -> dict[str, Any]:
        now = time.time()
        user_id, workspace_id = _id("user"), _id("ws")
        email = email.strip().lower()
        slug = f"{_slug(workspace_name)}-{uuid.uuid4().hex[:5]}"
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.users).values(id=user_id, email=email, name=name.strip() or email.split("@")[0], password_hash=password_hash, status="active", created_at=now))
                connection.execute(insert(self.workspaces).values(id=workspace_id, name=workspace_name.strip() or "我的工作区", slug=slug, plan="team", created_at=now))
                connection.execute(insert(self.memberships).values(workspace_id=workspace_id, user_id=user_id, role="owner", created_at=now))
        except IntegrityError as exc:
            raise ValueError("该邮箱已注册") from exc
        return {"user_id": user_id, "workspace_id": workspace_id, "email": email, "name": name, "role": "owner"}

    def create_user_from_invite(self, *, token: str, email: str, name: str, password_hash: str) -> dict[str, Any]:
        invite = self.get_invite(token)
        email = email.strip().lower()
        if invite["status"] != "pending" or invite["expires_at"] <= time.time():
            raise ValueError("邀请已失效")
        if invite.get("email") and invite["email"].lower() != email:
            raise ValueError("该邀请仅限指定邮箱")
        user_id = _id("user")
        now = time.time()
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.users).values(id=user_id, email=email, name=name.strip() or email.split("@")[0], password_hash=password_hash, status="active", created_at=now))
                connection.execute(insert(self.memberships).values(workspace_id=invite["workspace_id"], user_id=user_id, role=invite["role"], created_at=now))
                result = connection.execute(update(self.workspace_invites).where(and_(self.workspace_invites.c.id == invite["id"], self.workspace_invites.c.status == "pending")).values(status="accepted", accepted_by=user_id, accepted_at=now))
                if result.rowcount != 1:
                    raise ValueError("邀请已失效")
        except IntegrityError as exc:
            raise ValueError("该邮箱已注册，请登录后接受邀请") from exc
        return {"user_id": user_id, "workspace_id": invite["workspace_id"], "email": email, "name": name, "role": invite["role"]}

    def get_user_auth(self, email: str) -> dict[str, Any] | None:
        email = email.strip().lower()
        with self.engine.connect() as connection:
            row = connection.execute(select(self.users).where(self.users.c.email == email)).first()
            if not row:
                return None
            user = self._dict(row)
            membership = connection.execute(select(self.memberships).where(self.memberships.c.user_id == user["id"]).order_by(self.memberships.c.created_at)).first()
            if not membership:
                return None
            member = self._dict(membership)
        user.update({"workspace_id": member["workspace_id"], "role": member["role"]})
        return user

    def get_user(self, user_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.users).where(self.users.c.id == user_id)).first()
        if not row:
            raise KeyError(user_id)
        return self._dict(row)

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.workspaces).where(self.workspaces.c.id == workspace_id)).first()
        if not row:
            raise KeyError(workspace_id)
        return self._dict(row)

    def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.memberships).where(and_(self.memberships.c.workspace_id == workspace_id, self.memberships.c.user_id == user_id))).first()
        return self._dict(row) if row else None

    def list_user_workspaces(self, user_id: str) -> list[dict[str, Any]]:
        statement = select(self.workspaces, self.memberships.c.role).select_from(self.memberships.join(self.workspaces, self.memberships.c.workspace_id == self.workspaces.c.id)).where(self.memberships.c.user_id == user_id).order_by(self.memberships.c.created_at)
        with self.engine.connect() as connection:
            return [self._dict(row) for row in connection.execute(statement).fetchall()]

    def list_members(self, workspace_id: str) -> list[dict[str, Any]]:
        statement = select(self.users.c.id, self.users.c.email, self.users.c.name, self.users.c.status, self.memberships.c.role, self.memberships.c.created_at).select_from(self.memberships.join(self.users, self.memberships.c.user_id == self.users.c.id)).where(self.memberships.c.workspace_id == workspace_id).order_by(self.memberships.c.created_at)
        with self.engine.connect() as connection:
            return [self._dict(row) for row in connection.execute(statement).fetchall()]

    def add_member_by_email(self, workspace_id: str, email: str, role: str = "member") -> dict[str, Any]:
        email = email.strip().lower()
        if role not in {"admin", "member", "viewer"}:
            raise ValueError("成员角色无效")
        with self.engine.begin() as connection:
            user = connection.execute(select(self.users).where(self.users.c.email == email)).first()
            if not user:
                raise KeyError(email)
            user_data = self._dict(user)
            existing = connection.execute(select(self.memberships).where(and_(self.memberships.c.workspace_id == workspace_id, self.memberships.c.user_id == user_data["id"]))).first()
            if not existing:
                connection.execute(insert(self.memberships).values(workspace_id=workspace_id, user_id=user_data["id"], role=role, created_at=time.time()))
        return self.get_membership(workspace_id, user_data["id"]) or {}

    def set_member_role(self, workspace_id: str, user_id: str, role: str) -> dict[str, Any]:
        if role not in {"owner", "admin", "member", "viewer"}:
            raise ValueError("成员角色无效")
        current = self.get_membership(workspace_id, user_id)
        if not current:
            raise KeyError(user_id)
        if current["role"] == "owner" and role != "owner" and self._owner_count(workspace_id) <= 1:
            raise ValueError("工作空间必须至少保留一位所有者")
        with self.engine.begin() as connection:
            connection.execute(update(self.memberships).where(and_(self.memberships.c.workspace_id == workspace_id, self.memberships.c.user_id == user_id)).values(role=role))
        return self.get_membership(workspace_id, user_id) or {}

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        current = self.get_membership(workspace_id, user_id)
        if not current:
            raise KeyError(user_id)
        if current["role"] == "owner" and self._owner_count(workspace_id) <= 1:
            raise ValueError("不能移除工作空间最后一位所有者")
        with self.engine.begin() as connection:
            connection.execute(delete(self.memberships).where(and_(self.memberships.c.workspace_id == workspace_id, self.memberships.c.user_id == user_id)))
            connection.execute(update(self.conversations).where(and_(self.conversations.c.workspace_id == workspace_id, self.conversations.c.assigned_to == user_id)).values(assigned_to=None, updated_at=time.time()))

    def _owner_count(self, workspace_id: str) -> int:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.memberships.c.user_id).where(and_(self.memberships.c.workspace_id == workspace_id, self.memberships.c.role == "owner"))).fetchall()
        return len(rows)

    def create_invite(self, *, workspace_id: str, created_by: str, email: str | None = None, role: str = "member", ttl_hours: int = 168) -> dict[str, Any]:
        if role not in {"admin", "member", "viewer"}:
            raise ValueError("邀请角色无效")
        now = time.time()
        invite_id = _id("invite")
        token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
        with self.engine.begin() as connection:
            connection.execute(insert(self.workspace_invites).values(id=invite_id, workspace_id=workspace_id, token=token, email=(email or "").strip().lower() or None, role=role, status="pending", created_by=created_by, created_at=now, expires_at=now + max(1, ttl_hours) * 3600))
        return self.get_invite(token)

    def get_invite(self, token: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.workspace_invites).where(self.workspace_invites.c.token == token)).first()
        if not row:
            raise KeyError(token)
        return self._dict(row)

    def list_invites(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.workspace_invites).where(self.workspace_invites.c.workspace_id == workspace_id).order_by(self.workspace_invites.c.created_at.desc())).fetchall()
        return [self._dict(row) for row in rows]

    def revoke_invite(self, invite_id: str, workspace_id: str) -> dict[str, Any]:
        with self.engine.begin() as connection:
            result = connection.execute(update(self.workspace_invites).where(and_(self.workspace_invites.c.id == invite_id, self.workspace_invites.c.workspace_id == workspace_id, self.workspace_invites.c.status == "pending")).values(status="revoked"))
            if result.rowcount != 1:
                raise KeyError(invite_id)
            row = connection.execute(select(self.workspace_invites).where(self.workspace_invites.c.id == invite_id)).first()
        return self._dict(row)

    def accept_invite(self, token: str, user_id: str) -> dict[str, Any]:
        invite = self.get_invite(token)
        user = self.get_user(user_id)
        now = time.time()
        if invite["status"] != "pending" or invite["expires_at"] <= now:
            raise ValueError("邀请已失效")
        if invite.get("email") and invite["email"].lower() != user["email"].lower():
            raise ValueError("该邀请仅限指定邮箱")
        with self.engine.begin() as connection:
            existing = connection.execute(select(self.memberships).where(and_(self.memberships.c.workspace_id == invite["workspace_id"], self.memberships.c.user_id == user_id))).first()
            if not existing:
                connection.execute(insert(self.memberships).values(workspace_id=invite["workspace_id"], user_id=user_id, role=invite["role"], created_at=now))
            result = connection.execute(update(self.workspace_invites).where(and_(self.workspace_invites.c.id == invite["id"], self.workspace_invites.c.status == "pending")).values(status="accepted", accepted_by=user_id, accepted_at=now))
            if result.rowcount != 1:
                raise ValueError("邀请已失效")
        return self.get_membership(invite["workspace_id"], user_id) or {}

    def create_conversation(self, title: str = "新的分析任务", scene: str = "battle_review", *, workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID) -> dict[str, Any]:
        conversation_id, now = _id("cv"), time.time()
        with self.engine.begin() as connection:
            connection.execute(insert(self.conversations).values(id=conversation_id, workspace_id=workspace_id, created_by=created_by, assigned_to=created_by, title=title, scene=scene, status="active", pinned=0, archived_at=None, created_at=now, updated_at=now))
        return self.get_conversation(conversation_id, workspace_id=workspace_id)

    def list_conversations(self, limit: int = 50, *, workspace_id: str = DEMO_WORKSPACE_ID, query: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
        statement = select(self.conversations).where(self.conversations.c.workspace_id == workspace_id)
        statement = statement.where(self.conversations.c.archived_at.is_not(None) if archived else self.conversations.c.archived_at.is_(None))
        if query and query.strip():
            statement = statement.where(self.conversations.c.title.ilike(f"%{query.strip()[:120]}%"))
        statement = statement.order_by(self.conversations.c.pinned.desc(), self.conversations.c.updated_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).fetchall()
        return [self._dict(row) for row in rows]

    def get_conversation(self, conversation_id: str, *, workspace_id: str = DEMO_WORKSPACE_ID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))).first()
        if not row:
            raise KeyError(conversation_id)
        return self._dict(row)

    def touch(self, conversation_id: str, *, title: str | None = None, workspace_id: str = DEMO_WORKSPACE_ID) -> None:
        values: dict[str, Any] = {"updated_at": time.time()}
        if title:
            values["title"] = title
        with self.engine.begin() as connection:
            result = connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(**values))
            if result.rowcount == 0:
                raise KeyError(conversation_id)

    def update_conversation(self, conversation_id: str, *, workspace_id: str, title: str | None = None, assigned_to: str | None | object = ..., pinned: bool | None = None, status: str | None = None) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)
        if conversation["status"] == "waiting_approval" and status is None:
            raise ValueError("删除确认处理中，不能修改任务")
        values: dict[str, Any] = {"updated_at": time.time()}
        if title is not None:
            values["title"] = title.strip()[:240] or "未命名任务"
        if assigned_to is not ...:
            if assigned_to is not None:
                membership = self.get_membership(workspace_id, str(assigned_to))
                if not membership or membership["role"] == "viewer":
                    raise ValueError("负责人必须是可执行任务的工作空间成员")
            values["assigned_to"] = assigned_to
        if pinned is not None:
            values["pinned"] = 1 if pinned else 0
        if status is not None:
            if status not in {"active", "review", "waiting_approval", "blocked", "verified", "stopped"}:
                raise ValueError("任务状态无效")
            values["status"] = status
        with self.engine.begin() as connection:
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(**values))
        return self.get_conversation(conversation_id, workspace_id=workspace_id)

    def archive_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)
        if conversation["status"] == "waiting_approval":
            raise ValueError("删除确认处理中，不能归档任务")
        latest = self.latest_job(conversation_id, workspace_id=workspace_id)
        if latest and latest["status"] in {"queued", "running"}:
            raise ValueError("执行中的任务需要先停止，才能归档")
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(archived_at=now, updated_at=now))
        return self.get_conversation(conversation_id, workspace_id=workspace_id)

    def restore_conversation(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id, workspace_id=workspace_id)
        if conversation["status"] == "waiting_approval":
            raise ValueError("删除确认处理中，不能恢复任务")
        with self.engine.begin() as connection:
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(archived_at=None, updated_at=time.time()))
        return self.get_conversation(conversation_id, workspace_id=workspace_id)

    def add_message(self, conversation_id: str, role: str, content: str, payload: dict[str, Any] | None = None, *, workspace_id: str = DEMO_WORKSPACE_ID) -> dict[str, Any]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        message_id, now = _id("msg"), time.time()
        payload = payload or {}
        with self.engine.begin() as connection:
            connection.execute(insert(self.messages).values(id=message_id, conversation_id=conversation_id, role=role, content=content, payload=json.dumps(payload, ensure_ascii=False), created_at=now))
            connection.execute(update(self.conversations).where(self.conversations.c.id == conversation_id).values(updated_at=now))
        return {"id": message_id, "conversation_id": conversation_id, "role": role, "content": content, "payload": payload, "created_at": now}

    def create_message_job(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        content: str,
        asset_ids: list[str],
        job_payload: dict[str, Any],
        title_if_first: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now, message_id, job_id = time.time(), _id("msg"), _id("job")
        message_payload = {"asset_ids": list(asset_ids)}
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.conversations)
                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                .with_for_update()
            ).first()
            if not row:
                raise KeyError(conversation_id)
            conversation = self._dict(row)
            if conversation.get("archived_at") is not None:
                raise ValueError("已归档任务需要先恢复，才能继续执行")
            if conversation["status"] == "waiting_approval":
                raise ValueError("删除确认处理中，不能继续执行")
            active = connection.execute(
                select(self.jobs.c.id).where(
                    and_(
                        self.jobs.c.workspace_id == workspace_id,
                        self.jobs.c.conversation_id == conversation_id,
                        self.jobs.c.status.in_(("queued", "running")),
                    )
                ).limit(1)
            ).first()
            if active:
                raise ValueError("当前任务已有执行正在进行")
            message = {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": "user",
                "content": content,
                "payload": message_payload,
                "created_at": now,
            }
            connection.execute(
                insert(self.messages).values(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=content,
                    payload=json.dumps(message_payload, ensure_ascii=False),
                    created_at=now,
                )
            )
            connection.execute(
                insert(self.jobs).values(
                    id=job_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    status="queued",
                    payload=json.dumps(job_payload, ensure_ascii=False),
                    attempts=0,
                    created_at=now,
                    available_at=now,
                )
            )
            values: dict[str, Any] = {"status": "active", "updated_at": now}
            if title_if_first:
                values["title"] = title_if_first.strip().replace("\n", " ")[:36] or "新的研发任务"
            connection.execute(
                update(self.conversations)
                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                .values(**values)
            )
            connection.execute(
                insert(self.task_events).values(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    type="message.accepted",
                    payload=json.dumps({"message_id": message_id, "asset_count": len(job_payload.get("asset_ids", asset_ids))}, ensure_ascii=False),
                    created_at=now,
                )
            )
        return message, self.get_job(job_id, workspace_id=workspace_id)

    def get_message(self, message_id: str, *, workspace_id: str) -> dict[str, Any]:
        statement = select(self.messages).select_from(self.messages.join(self.conversations, self.messages.c.conversation_id == self.conversations.c.id)).where(and_(self.messages.c.id == message_id, self.conversations.c.workspace_id == workspace_id))
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if not row:
            raise KeyError(message_id)
        return self._json_row(row)

    def list_messages(self, conversation_id: str, *, workspace_id: str = DEMO_WORKSPACE_ID) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.messages).where(self.messages.c.conversation_id == conversation_id).order_by(self.messages.c.created_at, self.messages.c.id)).fetchall()
        return [self._json_row(row) for row in rows]

    def add_asset(self, conversation_id: str | None, *, name: str, mime: str, path: str, size: int, meta: dict[str, Any], workspace_id: str = DEMO_WORKSPACE_ID, created_by: str = DEMO_USER_ID, storage_backend: str = "local") -> dict[str, Any]:
        asset_id, now = _id("asset"), time.time()
        with self.engine.begin() as connection:
            if conversation_id:
                row = connection.execute(
                    select(self.conversations)
                    .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                    .with_for_update()
                ).first()
                if not row:
                    raise KeyError(conversation_id)
                conversation = self._dict(row)
                if conversation.get("archived_at") is not None:
                    raise ValueError("已归档任务需要先恢复，才能添加素材")
                if conversation["status"] == "waiting_approval":
                    raise ValueError("删除确认处理中，不能添加素材")
            connection.execute(insert(self.assets).values(id=asset_id, workspace_id=workspace_id, created_by=created_by, conversation_id=conversation_id, name=name, mime=mime, path=path, storage_backend=storage_backend, size=size, meta=json.dumps(meta, ensure_ascii=False), created_at=now))
            if conversation_id:
                connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(updated_at=now))
        return self.get_asset(asset_id, workspace_id=workspace_id)

    def get_asset(self, asset_id: str, *, workspace_id: str = DEMO_WORKSPACE_ID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.assets).where(and_(self.assets.c.id == asset_id, self.assets.c.workspace_id == workspace_id))).first()
        if not row:
            raise KeyError(asset_id)
        return self._json_row(row, "meta")

    def list_assets(self, conversation_id: str, *, workspace_id: str = DEMO_WORKSPACE_ID) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.assets).where(and_(self.assets.c.conversation_id == conversation_id, self.assets.c.workspace_id == workspace_id)).order_by(self.assets.c.created_at)).fetchall()
        return [self._json_row(row, "meta") for row in rows]

    def add_event(self, conversation_id: str, type_: str, payload: dict[str, Any], *, workspace_id: str = DEMO_WORKSPACE_ID) -> dict[str, Any]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        now = time.time()
        with self.engine.begin() as connection:
            result = connection.execute(insert(self.task_events).values(workspace_id=workspace_id, conversation_id=conversation_id, type=type_, payload=json.dumps(payload, ensure_ascii=False), created_at=now))
            event_id = result.inserted_primary_key[0]
        return {"id": event_id, "workspace_id": workspace_id, "conversation_id": conversation_id, "type": type_, "payload": payload, "created_at": now}

    def list_events(self, conversation_id: str, after_id: int = 0, *, workspace_id: str = DEMO_WORKSPACE_ID) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.task_events).where(and_(self.task_events.c.conversation_id == conversation_id, self.task_events.c.workspace_id == workspace_id, self.task_events.c.id > after_id)).order_by(self.task_events.c.id)).fetchall()
        return [self._json_row(row) for row in rows]

    def add_audit(self, *, request_id: str, action: str, workspace_id: str | None = None, user_id: str | None = None, resource_type: str | None = None, resource_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(insert(self.audit_logs).values(workspace_id=workspace_id, user_id=user_id, request_id=request_id, action=action, resource_type=resource_type, resource_id=resource_id, payload=json.dumps(payload or {}, ensure_ascii=False), created_at=time.time()))

    def list_audit(self, *, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.audit_logs).where(self.audit_logs.c.workspace_id == workspace_id).order_by(self.audit_logs.c.id.desc()).limit(limit)).fetchall()
        return [self._json_row(row) for row in rows]

    def enqueue_job(self, *, workspace_id: str, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id, now = _id("job"), time.time()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.conversations)
                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                .with_for_update()
            ).first()
            if not row:
                raise KeyError(conversation_id)
            conversation = self._dict(row)
            if conversation.get("archived_at") is not None:
                raise ValueError("已归档任务需要先恢复，才能继续执行")
            if conversation["status"] == "waiting_approval":
                raise ValueError("删除确认处理中，不能继续执行")
            active = connection.execute(
                select(self.jobs.c.id).where(
                    and_(
                        self.jobs.c.workspace_id == workspace_id,
                        self.jobs.c.conversation_id == conversation_id,
                        self.jobs.c.status.in_(("queued", "running")),
                    )
                ).limit(1)
            ).first()
            if active:
                raise ValueError("当前任务已有执行正在进行")
            connection.execute(insert(self.jobs).values(id=job_id, workspace_id=workspace_id, conversation_id=conversation_id, status="queued", payload=json.dumps(payload, ensure_ascii=False), attempts=0, created_at=now, available_at=now))
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="active", updated_at=now))
        return self.get_job(job_id, workspace_id=workspace_id)

    def get_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.jobs).where(and_(self.jobs.c.id == job_id, self.jobs.c.workspace_id == workspace_id))).first()
        if not row:
            raise KeyError(job_id)
        return self._json_row(row)

    def latest_job(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.jobs).where(and_(self.jobs.c.conversation_id == conversation_id, self.jobs.c.workspace_id == workspace_id)).order_by(self.jobs.c.created_at.desc()).limit(1)).first()
        return self._json_row(row) if row else None

    def claim_job(self, worker_id: str, job_id: str | None = None) -> dict[str, Any] | None:
        now = time.time()
        with self.engine.begin() as connection:
            statement = select(self.jobs).where(and_(self.jobs.c.status == "queued", self.jobs.c.available_at <= now))
            if job_id:
                statement = statement.where(self.jobs.c.id == job_id)
            row = connection.execute(statement.order_by(self.jobs.c.created_at).limit(1)).first()
            if not row:
                return None
            job = self._json_row(row)
            result = connection.execute(update(self.jobs).where(and_(self.jobs.c.id == job["id"], self.jobs.c.status == "queued")).values(status="running", worker_id=worker_id, claimed_at=now, attempts=int(job.get("attempts") or 0) + 1))
            if result.rowcount != 1:
                return None
        return self.get_job(job["id"], workspace_id=job["workspace_id"])

    def cancel_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            row = connection.execute(select(self.jobs.c.conversation_id).where(and_(self.jobs.c.id == job_id, self.jobs.c.workspace_id == workspace_id))).first()
            if not row:
                raise KeyError(job_id)
            result = connection.execute(update(self.jobs).where(and_(self.jobs.c.id == job_id, self.jobs.c.workspace_id == workspace_id, self.jobs.c.status.in_(("queued", "running")))).values(status="cancelled", completed_at=now))
            if result.rowcount:
                connection.execute(update(self.conversations).where(self.conversations.c.id == row[0]).values(status="stopped", updated_at=now))
        return self.get_job(job_id, workspace_id=workspace_id)

    def retry_job(self, job_id: str, *, workspace_id: str) -> dict[str, Any]:
        source = self.get_job(job_id, workspace_id=workspace_id)
        if source["status"] not in {"failed", "cancelled"}:
            raise ValueError("只有失败或已停止的执行可以重试")
        latest = self.latest_job(source["conversation_id"], workspace_id=workspace_id)
        if not latest or latest["id"] != source["id"]:
            raise ValueError("只能重新执行当前任务最新一次失败或已停止的执行")
        return self.enqueue_job(workspace_id=workspace_id, conversation_id=source["conversation_id"], payload=source["payload"])

    def complete_job_answer(self, job_id: str, *, workspace_id: str, content: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        now, message_id = time.time(), _id("msg")
        with self.engine.begin() as connection:
            job = connection.execute(select(self.jobs.c.conversation_id).where(and_(self.jobs.c.id == job_id, self.jobs.c.workspace_id == workspace_id, self.jobs.c.status == "running"))).first()
            if not job:
                return None
            conversation_id = job[0]
            result = connection.execute(update(self.jobs).where(and_(self.jobs.c.id == job_id, self.jobs.c.workspace_id == workspace_id, self.jobs.c.status == "running")).values(status="completed", completed_at=now, last_error=None))
            if result.rowcount != 1:
                return None
            message = {"id": message_id, "conversation_id": conversation_id, "role": "assistant", "content": content, "payload": payload, "created_at": now}
            connection.execute(insert(self.messages).values(id=message_id, conversation_id=conversation_id, role="assistant", content=content, payload=json.dumps(payload, ensure_ascii=False), created_at=now))
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="review", updated_at=now))
            event_payload = {"job_id": job_id, "message": message, "result": payload}
            connection.execute(insert(self.task_events).values(workspace_id=workspace_id, conversation_id=conversation_id, type="answer.ready", payload=json.dumps(event_payload, ensure_ascii=False), created_at=now))
        return message

    def fail_job(self, job_id: str, error: str, max_attempts: int = 3) -> dict[str, Any] | None:
        now = time.time()
        with self.engine.begin() as connection:
            row = connection.execute(select(self.jobs).where(self.jobs.c.id == job_id)).first()
            if not row:
                return None
            job = self._json_row(row)
            if job["status"] in {"completed", "cancelled", "failed"}:
                return job
            if int(job.get("attempts") or 0) < max_attempts:
                connection.execute(update(self.jobs).where(and_(self.jobs.c.id == job_id, self.jobs.c.status == "running")).values(status="queued", worker_id=None, claimed_at=None, last_error=error[:8000], available_at=now + min(30, 2 ** max(0, int(job.get("attempts") or 1) - 1))))
            else:
                result = connection.execute(update(self.jobs).where(and_(self.jobs.c.id == job_id, self.jobs.c.status == "running")).values(status="failed", last_error=error[:8000], completed_at=now))
                if result.rowcount:
                    connection.execute(update(self.conversations).where(self.conversations.c.id == job["conversation_id"]).values(status="blocked", updated_at=now))
        return self.get_job(job_id, workspace_id=job["workspace_id"])

    def create_approval(self, *, workspace_id: str, conversation_id: str, action: str, requested_by: str, reason: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        approval_id, now = _id("approval"), time.time()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.conversations)
                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                .with_for_update()
            ).first()
            if not row:
                raise KeyError(conversation_id)
            conversation = self._dict(row)
            existing = connection.execute(
                select(self.approval_requests).where(
                    and_(
                        self.approval_requests.c.workspace_id == workspace_id,
                        self.approval_requests.c.conversation_id == conversation_id,
                        self.approval_requests.c.action == action,
                        self.approval_requests.c.status.in_(("pending", "approved")),
                    )
                ).order_by(self.approval_requests.c.created_at.desc()).limit(1)
            ).first()
            if existing:
                return self._json_row(existing)
            if action == "conversation.delete":
                active = connection.execute(
                    select(self.jobs.c.id).where(
                        and_(
                            self.jobs.c.workspace_id == workspace_id,
                            self.jobs.c.conversation_id == conversation_id,
                            self.jobs.c.status.in_(("queued", "running")),
                        )
                    ).limit(1)
                ).first()
                if active:
                    raise ValueError("执行中的任务需要先停止，才能请求永久删除")
            approval_payload = dict(payload or {})
            approval_payload.setdefault("previous_status", conversation["status"])
            connection.execute(insert(self.approval_requests).values(id=approval_id, workspace_id=workspace_id, conversation_id=conversation_id, action=action, status="pending", reason=reason, payload=json.dumps(approval_payload, ensure_ascii=False), requested_by=requested_by, created_at=now))
            connection.execute(update(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)).values(status="waiting_approval", updated_at=now))
        return self.get_approval(approval_id, workspace_id=workspace_id)

    def get_approval(self, approval_id: str, *, workspace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.approval_requests).where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.workspace_id == workspace_id))).first()
        if not row:
            raise KeyError(approval_id)
        return self._json_row(row)

    def list_approvals(self, conversation_id: str, *, workspace_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.approval_requests).where(and_(self.approval_requests.c.conversation_id == conversation_id, self.approval_requests.c.workspace_id == workspace_id)).order_by(self.approval_requests.c.created_at.desc())).fetchall()
        return [self._json_row(row) for row in rows]

    def resolve_approval(self, approval_id: str, *, workspace_id: str, user_id: str, approved: bool) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            row = connection.execute(select(self.approval_requests).where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.workspace_id == workspace_id))).first()
            if not row:
                raise KeyError(approval_id)
            approval = self._json_row(row)
            if approval["status"] != "pending":
                return approval
            status = "approved" if approved else "rejected"
            connection.execute(update(self.approval_requests).where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.status == "pending")).values(status=status, resolved_by=user_id, resolved_at=now))
            next_status = "waiting_approval" if approved else str((approval.get("payload") or {}).get("previous_status") or "active")
            connection.execute(update(self.conversations).where(self.conversations.c.id == approval["conversation_id"]).values(status=next_status, updated_at=now))
        return self.get_approval(approval_id, workspace_id=workspace_id)

    def delete_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: str,
        approval_id: str,
        request_id: str,
        user_id: str,
        asset_object_count: int = 0,
    ) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            conversation_row = connection.execute(
                select(self.conversations)
                .where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id))
                .with_for_update()
            ).first()
            if not conversation_row:
                raise KeyError(conversation_id)
            conversation = self._dict(conversation_row)
            approval_row = connection.execute(
                select(self.approval_requests)
                .where(and_(self.approval_requests.c.id == approval_id, self.approval_requests.c.workspace_id == workspace_id))
                .with_for_update()
            ).first()
            if not approval_row:
                raise KeyError(approval_id)
            approval = self._json_row(approval_row)
            if (
                approval["conversation_id"] != conversation_id
                or approval["action"] != "conversation.delete"
                or approval["status"] != "approved"
                or conversation["status"] != "waiting_approval"
            ):
                raise ValueError("删除审批尚未通过、已失效或不匹配当前任务")
            active = connection.execute(
                select(self.jobs.c.id).where(
                    and_(
                        self.jobs.c.workspace_id == workspace_id,
                        self.jobs.c.conversation_id == conversation_id,
                        self.jobs.c.status.in_(("queued", "running")),
                    )
                ).limit(1)
            ).first()
            if active:
                raise ValueError("执行中的任务需要先停止，才能永久删除")

            asset_rows = connection.execute(
                select(self.assets).where(
                    and_(self.assets.c.workspace_id == workspace_id, self.assets.c.conversation_id == conversation_id)
                )
            ).fetchall()
            assets = [self._json_row(row, "meta") for row in asset_rows]
            message_ids = [
                row[0]
                for row in connection.execute(
                    select(self.messages.c.id).where(self.messages.c.conversation_id == conversation_id)
                ).fetchall()
            ]
            connection.execute(
                insert(self.audit_logs).values(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    request_id=request_id,
                    action="conversation.delete",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    payload=json.dumps({"asset_objects": asset_object_count}, ensure_ascii=False),
                    created_at=time.time(),
                )
            )
            if message_ids:
                connection.execute(delete(self.result_feedback).where(self.result_feedback.c.message_id.in_(message_ids)))
            connection.execute(delete(self.product_events).where(and_(self.product_events.c.workspace_id == workspace_id, self.product_events.c.conversation_id == conversation_id)))
            connection.execute(delete(self.task_events).where(and_(self.task_events.c.workspace_id == workspace_id, self.task_events.c.conversation_id == conversation_id)))
            connection.execute(delete(self.jobs).where(and_(self.jobs.c.workspace_id == workspace_id, self.jobs.c.conversation_id == conversation_id)))
            connection.execute(delete(self.messages).where(self.messages.c.conversation_id == conversation_id))
            connection.execute(delete(self.assets).where(and_(self.assets.c.workspace_id == workspace_id, self.assets.c.conversation_id == conversation_id)))
            connection.execute(delete(self.approval_requests).where(and_(self.approval_requests.c.workspace_id == workspace_id, self.approval_requests.c.conversation_id == conversation_id)))
            connection.execute(delete(self.conversations).where(and_(self.conversations.c.id == conversation_id, self.conversations.c.workspace_id == workspace_id)))
        return assets

    def upsert_feedback(self, *, workspace_id: str, user_id: str, message_id: str, verdict: str, evidence_useful: bool | None = None, human_verified: bool = False, note: str = "") -> dict[str, Any]:
        if verdict not in {"correct", "partial", "incorrect"}:
            raise ValueError("反馈类型无效")
        if not self.get_membership(workspace_id, user_id):
            raise ValueError("用户不属于当前工作空间")
        message = self.get_message(message_id, workspace_id=workspace_id)
        if message["role"] != "assistant":
            raise ValueError("只能评价任务交付结果")
        now = time.time()
        values = {"verdict": verdict, "evidence_useful": None if evidence_useful is None else int(evidence_useful), "human_verified": int(human_verified), "note": note.strip()[:2000], "updated_at": now}
        with self.engine.begin() as connection:
            existing = connection.execute(select(self.result_feedback.c.message_id).where(and_(self.result_feedback.c.message_id == message_id, self.result_feedback.c.user_id == user_id))).first()
            if existing:
                connection.execute(update(self.result_feedback).where(and_(self.result_feedback.c.message_id == message_id, self.result_feedback.c.user_id == user_id)).values(**values))
            else:
                connection.execute(insert(self.result_feedback).values(message_id=message_id, user_id=user_id, workspace_id=workspace_id, conversation_id=message["conversation_id"], created_at=now, **values))
        feedback = self.get_feedback(message_id, user_id=user_id, workspace_id=workspace_id) or {}
        gate = self.feedback_gate(message["conversation_id"], workspace_id=workspace_id)
        if gate.get("message_id") == message_id:
            conversation = self.get_conversation(message["conversation_id"], workspace_id=workspace_id)
            if conversation["status"] != "waiting_approval":
                with self.engine.begin() as connection:
                    connection.execute(update(self.conversations).where(and_(self.conversations.c.id == message["conversation_id"], self.conversations.c.workspace_id == workspace_id)).values(status=gate["task_status"], updated_at=time.time()))
        return feedback

    def get_feedback(self, message_id: str, *, user_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.result_feedback).where(and_(self.result_feedback.c.message_id == message_id, self.result_feedback.c.user_id == user_id, self.result_feedback.c.workspace_id == workspace_id))).first()
        return self._dict(row) if row else None

    def list_feedback(self, conversation_id: str, *, workspace_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.result_feedback).where(and_(self.result_feedback.c.conversation_id == conversation_id, self.result_feedback.c.workspace_id == workspace_id)).order_by(self.result_feedback.c.updated_at.desc())).fetchall()
        return [self._dict(row) for row in rows]

    def feedback_gate(self, conversation_id: str, *, workspace_id: str) -> dict[str, Any]:
        self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.connect() as connection:
            latest = connection.execute(select(self.messages.c.id).where(and_(self.messages.c.conversation_id == conversation_id, self.messages.c.role == "assistant")).order_by(self.messages.c.created_at.desc(), self.messages.c.id.desc()).limit(1)).first()
            if not latest:
                return {"approved": False, "message_id": None, "task_status": "active", "human_verified": 0, "correct": 0, "incorrect": 0, "feedback_count": 0, "reason": "尚无可人工复核的交付结果"}
            message_id = latest[0]
            rows = [self._dict(row) for row in connection.execute(select(self.result_feedback).where(and_(self.result_feedback.c.workspace_id == workspace_id, self.result_feedback.c.conversation_id == conversation_id, self.result_feedback.c.message_id == message_id)).order_by(self.result_feedback.c.updated_at.desc())).fetchall()]
        verified_correct = [row for row in rows if row.get("human_verified") and row.get("verdict") == "correct"]
        incorrect = [row for row in rows if row.get("verdict") == "incorrect"]
        correct = [row for row in rows if row.get("verdict") == "correct"]
        approved = bool(verified_correct) and not incorrect
        task_status = "verified" if approved else ("blocked" if incorrect else "review")
        if approved:
            reason = "最新交付已人工确认正确，且无错误反馈"
        elif incorrect:
            reason = "最新交付存在错误反馈，需要修正后重新验证"
        else:
            reason = "最新交付需要人工确认正确后才能通过质量门"
        return {"approved": approved, "message_id": message_id, "task_status": task_status, "human_verified": len(verified_correct), "correct": len(correct), "incorrect": len(incorrect), "feedback_count": len(rows), "reason": reason}

    def record_product_event(self, *, workspace_id: str, user_id: str, name: str, conversation_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        if conversation_id:
            self.get_conversation(conversation_id, workspace_id=workspace_id)
        with self.engine.begin() as connection:
            connection.execute(insert(self.product_events).values(workspace_id=workspace_id, user_id=user_id, conversation_id=conversation_id, name=name, payload=json.dumps(payload or {}, ensure_ascii=False), created_at=time.time()))

    def product_metrics(self, *, workspace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            conversations = [self._dict(row) for row in connection.execute(select(self.conversations).where(self.conversations.c.workspace_id == workspace_id)).fetchall()]
            jobs = [self._dict(row) for row in connection.execute(select(self.jobs).where(self.jobs.c.workspace_id == workspace_id)).fetchall()]
            message_rows = connection.execute(select(self.messages.c.conversation_id, self.messages.c.role).select_from(self.messages.join(self.conversations, self.messages.c.conversation_id == self.conversations.c.id)).where(self.conversations.c.workspace_id == workspace_id)).fetchall()
            events = [self._dict(row) for row in connection.execute(select(self.product_events).where(self.product_events.c.workspace_id == workspace_id)).fetchall()]
            feedback = [self._dict(row) for row in connection.execute(select(self.result_feedback).where(self.result_feedback.c.workspace_id == workspace_id)).fetchall()]

        jobs_by_conversation: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            jobs_by_conversation.setdefault(job["conversation_id"], []).append(job)

        completed_ids = {
            conversation_id
            for conversation_id, rows in jobs_by_conversation.items()
            if any(row["status"] == "completed" for row in rows)
        }
        task_ids_with_jobs = set(jobs_by_conversation)
        failed_or_cancelled = {
            conversation_id
            for conversation_id, rows in jobs_by_conversation.items()
            if any(row["status"] in {"failed", "cancelled"} for row in rows)
        }
        recovered = failed_or_cancelled & completed_ids

        first_result_durations: list[float] = []
        for conversation_id in completed_ids:
            rows = jobs_by_conversation[conversation_id]
            started_at = min(float(row["created_at"]) for row in rows)
            completed_at = min(
                float(row["completed_at"])
                for row in rows
                if row["status"] == "completed" and row.get("completed_at") is not None
            )
            first_result_durations.append(max(0.0, completed_at - started_at))

        user_turns: dict[str, int] = {}
        for row in message_rows:
            if row.role == "user":
                user_turns[row.conversation_id] = user_turns.get(row.conversation_id, 0) + 1
        continued_ids = {conversation_id for conversation_id, count in user_turns.items() if count >= 2}

        evidence_open_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] == "evidence.open" and event.get("conversation_id") in completed_ids
        }
        adoption_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] in {"result.copy", "deliverable.copy"} and event.get("conversation_id") in completed_ids
        }
        explicit_intervention_ids = {
            event["conversation_id"]
            for event in events
            if event["name"] in {"task.retry", "task.handoff"} and event.get("conversation_id")
        }
        manual_intervention_ids = failed_or_cancelled | explicit_intervention_ids
        verified_feedback = sum(1 for row in feedback if row.get("human_verified"))

        job_denominator = max(1, len(task_ids_with_jobs))
        completed_denominator = max(1, len(completed_ids))
        feedback_denominator = max(1, len(feedback))
        return {
            "task_count": len(conversations),
            "active_tasks": sum(1 for row in conversations if row.get("archived_at") is None),
            "first_task_completion_rate": round(len(completed_ids) / job_denominator, 4),
            "avg_time_to_first_result_seconds": round(sum(first_result_durations) / len(first_result_durations), 2) if first_result_durations else None,
            "interruption_rate": round(sum(1 for job in jobs if job["status"] == "cancelled") / max(1, len(jobs)), 4),
            "failure_rate": round(sum(1 for job in jobs if job["status"] == "failed") / max(1, len(jobs)), 4),
            "recovery_rate": round(len(recovered) / max(1, len(failed_or_cancelled)), 4),
            "continuation_rate": round(len(continued_ids) / max(1, len(user_turns)), 4),
            "manual_intervention_rate": round(len(manual_intervention_ids & task_ids_with_jobs) / job_denominator, 4),
            "evidence_open_rate": round(len(evidence_open_ids) / completed_denominator, 4),
            "result_adoption_rate": round(len(adoption_ids) / completed_denominator, 4),
            "human_verified_feedback_rate": round(verified_feedback / feedback_denominator, 4),
        }
