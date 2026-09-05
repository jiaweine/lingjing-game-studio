from __future__ import annotations

import json
import math
import re
import time
import uuid
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    delete,
    insert,
    or_,
    select,
    text as sql_text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .compiler import _search_tokens

_MEMORY_KINDS = {
    "fact",
    "constraint",
    "decision",
    "hypothesis",
    "procedure",
    "gotcha",
    "episode",
    "preference",
    "resource",
}
_MEMORY_STATES = {"active", "disputed", "retracted"}
_RELATIONS = {
    "related_to",
    "depends_on",
    "contradicts",
    "supports",
    "derived_from",
    "applies_with",
}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _slug(value: str) -> str:
    normalized = re.sub(
        r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", str(value).strip()
    ).strip("-").lower()
    return normalized[:72] or f"project-{uuid.uuid4().hex[:8]}"


def _memory_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    if not normalized:
        raise ValueError("memory_key 不能为空")
    if len(normalized) > 240:
        raise ValueError("memory_key 过长")
    return normalized


class MemoryConflict(RuntimeError):
    pass


class ProjectMemoryStore:
    """Authoritative, project-scoped, versioned long-term memory.

    Raw conversation/event/asset data remains the ultimate evidence source. This store is a
    governed semantic materialization: every memory version has provenance, explicit project
    scope and optional build/branch/commit/environment validity. Versions are append-only;
    a small head table advances through compare-and-swap so concurrent workers cannot silently
    overwrite each other.

    Embeddings are deliberately absent here. They belong to a rebuildable derived retrieval
    index. Correctness, revision history, deletion and access control must not depend on a
    vector database being healthy.
    """

    def __init__(self, engine: Engine, *, auto_create_schema: bool = False) -> None:
        self.engine = engine
        self.metadata = MetaData()
        self._define_tables()
        if auto_create_schema:
            self.metadata.create_all(self.engine)

    def _define_tables(self) -> None:
        self.projects = Table(
            "context_projects",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("name", String(200), nullable=False),
            Column("slug", String(96), nullable=False),
            Column("external_ref", Text, nullable=True),
            Column("default_branch", String(160), nullable=True),
            Column("status", String(32), nullable=False, default="active", index=True),
            Column("created_by", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
            UniqueConstraint(
                "workspace_id",
                "slug",
                name="uq_context_projects_workspace_slug",
            ),
        )
        self.project_conversations = Table(
            "context_project_conversations",
            self.metadata,
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("project_id", String(64), primary_key=True),
            Column("conversation_id", String(64), primary_key=True, index=True),
            Column("bound_by", String(64), nullable=False),
            Column("bound_at", Float, nullable=False),
            UniqueConstraint(
                "workspace_id",
                "conversation_id",
                name="uq_context_project_conversation_workspace",
            ),
        )
        self.items = Table(
            "context_memory_items",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("project_id", String(64), nullable=False, index=True),
            Column("memory_key", String(240), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("kind", String(48), nullable=False),
            Column("content", Text, nullable=False),
            Column("value_json", Text, nullable=False, default="{}"),
            Column("state", String(32), nullable=False),
            Column("confidence", Float, nullable=False, default=1.0),
            Column("importance", Float, nullable=False, default=0.5),
            Column("pinned", Integer, nullable=False, default=0),
            Column("build_ref", String(160), nullable=True),
            Column("branch_ref", String(200), nullable=True),
            Column("commit_ref", String(160), nullable=True),
            Column("environment_ref", String(160), nullable=True),
            Column("valid_from", Float, nullable=True),
            Column("valid_to", Float, nullable=True),
            Column("expires_at", Float, nullable=True, index=True),
            Column("source_type", String(48), nullable=False),
            Column("source_id", String(128), nullable=False),
            Column("source_excerpt", Text, nullable=False, default=""),
            Column("supersedes_id", String(64), nullable=True),
            Column("created_by", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            UniqueConstraint(
                "project_id",
                "memory_key",
                "revision",
                name="uq_context_memory_revision",
            ),
        )
        self.heads = Table(
            "context_memory_heads",
            self.metadata,
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("project_id", String(64), primary_key=True),
            Column("memory_key", String(240), primary_key=True),
            Column("memory_id", String(64), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("state", String(32), nullable=False, index=True),
            Column("updated_at", Float, nullable=False),
        )
        self.relations = Table(
            "context_memory_relations",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False),
            Column("project_id", String(64), nullable=False, index=True),
            Column("from_key", String(240), nullable=False),
            Column("relation", String(64), nullable=False),
            Column("to_key", String(240), nullable=False),
            Column("source_id", String(128), nullable=True),
            Column("created_by", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            UniqueConstraint(
                "project_id",
                "from_key",
                "relation",
                "to_key",
                name="uq_context_memory_relation",
            ),
        )
        self.usage = Table(
            "context_memory_usage",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workspace_id", String(64), nullable=False),
            Column("project_id", String(64), nullable=False, index=True),
            Column("memory_id", String(64), nullable=False, index=True),
            Column("memory_key", String(240), nullable=False),
            Column("conversation_id", String(64), nullable=True),
            Column("reason", String(96), nullable=False),
            Column("score", Float, nullable=True),
            Column("used_at", Float, nullable=False),
        )

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row._mapping if hasattr(row, "_mapping") else row)

    @staticmethod
    def _decode_item(row: Any) -> dict[str, Any]:
        data = ProjectMemoryStore._dict(row)
        try:
            data["value"] = json.loads(data.pop("value_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data["value"] = {}
            data.pop("value_json", None)
        data["pinned"] = bool(data.get("pinned"))
        return data

    @staticmethod
    def _bounded01(value: float, name: str) -> float:
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError(f"{name} 必须在 0..1")
        return number

    def _require_member(self, connection, workspace_id: str, actor_id: str) -> None:
        row = connection.execute(
            sql_text(
                "SELECT 1 FROM memberships "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id LIMIT 1"
            ),
            {"workspace_id": workspace_id, "user_id": actor_id},
        ).first()
        if row is None:
            raise PermissionError("用户不属于该工作区")

    def _require_project(self, connection, workspace_id: str, project_id: str) -> dict[str, Any]:
        row = connection.execute(
            select(self.projects).where(
                and_(
                    self.projects.c.id == project_id,
                    self.projects.c.workspace_id == workspace_id,
                )
            )
        ).first()
        if row is None:
            raise KeyError(project_id)
        project = self._dict(row)
        if project.get("status") != "active":
            raise ValueError("项目记忆空间不可用")
        return project

    def create_project(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        name: str,
        external_ref: str | None = None,
        default_branch: str | None = None,
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValueError("项目名不能为空")
        now = time.time()
        project_id = _id("project")
        slug = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            connection.execute(
                insert(self.projects).values(
                    id=project_id,
                    workspace_id=workspace_id,
                    name=name[:200],
                    slug=slug,
                    external_ref=(str(external_ref)[:4000] if external_ref else None),
                    default_branch=(str(default_branch)[:160] if default_branch else None),
                    status="active",
                    created_by=actor_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            row = connection.execute(
                select(self.projects).where(self.projects.c.id == project_id)
            ).first()
        return self._dict(row)

    def get_project(
        self, *, workspace_id: str, actor_id: str, project_id: str
    ) -> dict[str, Any]:
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            return self._require_project(connection, workspace_id, project_id)

    def list_projects(
        self, *, workspace_id: str, actor_id: str
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            rows = connection.execute(
                select(self.projects)
                .where(
                    and_(
                        self.projects.c.workspace_id == workspace_id,
                        self.projects.c.status == "active",
                    )
                )
                .order_by(self.projects.c.updated_at.desc())
            ).all()
        return [self._dict(row) for row in rows]

    def bind_conversation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            conversation = connection.execute(
                sql_text(
                    "SELECT workspace_id FROM conversations WHERE id = :conversation_id LIMIT 1"
                ),
                {"conversation_id": conversation_id},
            ).first()
            if conversation is None:
                raise KeyError(conversation_id)
            if str(conversation[0]) != workspace_id:
                raise PermissionError("会话不属于该工作区")
            existing = connection.execute(
                select(self.project_conversations).where(
                    and_(
                        self.project_conversations.c.workspace_id == workspace_id,
                        self.project_conversations.c.conversation_id == conversation_id,
                    )
                )
            ).first()
            if existing:
                row = self._dict(existing)
                if row["project_id"] != project_id:
                    raise ValueError("会话已绑定到另一个项目；需要显式解绑/迁移")
                return row
            connection.execute(
                insert(self.project_conversations).values(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    bound_by=actor_id,
                    bound_at=now,
                )
            )
            row = connection.execute(
                select(self.project_conversations).where(
                    and_(
                        self.project_conversations.c.project_id == project_id,
                        self.project_conversations.c.conversation_id == conversation_id,
                    )
                )
            ).first()
        return self._dict(row)

    def project_for_conversation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            mapping = connection.execute(
                select(self.project_conversations).where(
                    and_(
                        self.project_conversations.c.workspace_id == workspace_id,
                        self.project_conversations.c.conversation_id == conversation_id,
                    )
                )
            ).first()
            if not mapping:
                return None
            project_id = self._dict(mapping)["project_id"]
            return self._require_project(connection, workspace_id, project_id)

    def _write_memory_once(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_key: str,
        kind: str,
        content: str,
        value: dict[str, Any],
        state: str,
        confidence: float,
        importance: float,
        pinned: bool,
        build_ref: str | None,
        branch_ref: str | None,
        commit_ref: str | None,
        environment_ref: str | None,
        valid_from: float | None,
        valid_to: float | None,
        expires_at: float | None,
        source_type: str,
        source_id: str,
        source_excerpt: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            head_row = connection.execute(
                select(self.heads).where(
                    and_(
                        self.heads.c.project_id == project_id,
                        self.heads.c.memory_key == memory_key,
                    )
                )
            ).first()
            head = self._dict(head_row) if head_row else None
            revision = int(head["revision"]) + 1 if head else 1
            memory_id = _id("memory")
            supersedes_id = str(head["memory_id"]) if head else None
            connection.execute(
                insert(self.items).values(
                    id=memory_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    memory_key=memory_key,
                    revision=revision,
                    kind=kind,
                    content=content,
                    value_json=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    state=state,
                    confidence=confidence,
                    importance=importance,
                    pinned=1 if pinned else 0,
                    build_ref=build_ref,
                    branch_ref=branch_ref,
                    commit_ref=commit_ref,
                    environment_ref=environment_ref,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    expires_at=expires_at,
                    source_type=source_type,
                    source_id=source_id,
                    source_excerpt=source_excerpt,
                    supersedes_id=supersedes_id,
                    created_by=actor_id,
                    created_at=now,
                )
            )
            if head:
                result = connection.execute(
                    update(self.heads)
                    .where(
                        and_(
                            self.heads.c.project_id == project_id,
                            self.heads.c.memory_key == memory_key,
                            self.heads.c.revision == int(head["revision"]),
                            self.heads.c.memory_id == head["memory_id"],
                        )
                    )
                    .values(
                        workspace_id=workspace_id,
                        memory_id=memory_id,
                        revision=revision,
                        state=state,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise MemoryConflict(memory_key)
            else:
                connection.execute(
                    insert(self.heads).values(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        memory_key=memory_key,
                        memory_id=memory_id,
                        revision=revision,
                        state=state,
                        updated_at=now,
                    )
                )
            row = connection.execute(
                select(self.items).where(self.items.c.id == memory_id)
            ).first()
        return self._decode_item(row)

    def put_memory(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_key: str,
        kind: str,
        content: str,
        value: dict[str, Any] | None = None,
        state: str = "active",
        confidence: float = 1.0,
        importance: float = 0.5,
        pinned: bool = False,
        build_ref: str | None = None,
        branch_ref: str | None = None,
        commit_ref: str | None = None,
        environment_ref: str | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        expires_at: float | None = None,
        source_type: str = "user",
        source_id: str,
        source_excerpt: str = "",
        max_retries: int = 4,
    ) -> dict[str, Any]:
        key = _memory_key(memory_key)
        kind = str(kind or "").strip().lower()
        state = str(state or "").strip().lower()
        if kind not in _MEMORY_KINDS:
            raise ValueError(f"不支持的 memory kind: {kind}")
        if state not in _MEMORY_STATES:
            raise ValueError(f"不支持的 memory state: {state}")
        content = str(content or "").strip()
        if not content:
            raise ValueError("memory content 不能为空")
        if len(content) > 20000:
            raise ValueError("memory content 过长")
        if not str(source_type or "").strip() or not str(source_id or "").strip():
            raise ValueError("memory provenance 不能为空")
        confidence = self._bounded01(confidence, "confidence")
        importance = self._bounded01(importance, "importance")
        if valid_from is not None and valid_to is not None and valid_to <= valid_from:
            raise ValueError("valid_to 必须晚于 valid_from")
        kwargs = dict(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            memory_key=key,
            kind=kind,
            content=content,
            value=dict(value or {}),
            state=state,
            confidence=confidence,
            importance=importance,
            pinned=bool(pinned),
            build_ref=(str(build_ref)[:160] if build_ref else None),
            branch_ref=(str(branch_ref)[:200] if branch_ref else None),
            commit_ref=(str(commit_ref)[:160] if commit_ref else None),
            environment_ref=(str(environment_ref)[:160] if environment_ref else None),
            valid_from=valid_from,
            valid_to=valid_to,
            expires_at=expires_at,
            source_type=str(source_type)[:48],
            source_id=str(source_id)[:128],
            source_excerpt=str(source_excerpt or "")[:4000],
        )
        last_error: Exception | None = None
        for _attempt in range(max(1, int(max_retries))):
            try:
                return self._write_memory_once(**kwargs)
            except (IntegrityError, MemoryConflict) as exc:
                last_error = exc
                continue
        raise MemoryConflict(f"memory CAS failed: {key}") from last_error

    def current_memory(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_key: str,
        include_inactive: bool = False,
    ) -> dict[str, Any] | None:
        key = _memory_key(memory_key)
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            statement = (
                select(self.items)
                .select_from(
                    self.heads.join(self.items, self.heads.c.memory_id == self.items.c.id)
                )
                .where(
                    and_(
                        self.heads.c.workspace_id == workspace_id,
                        self.heads.c.project_id == project_id,
                        self.heads.c.memory_key == key,
                    )
                )
            )
            if not include_inactive:
                statement = statement.where(self.heads.c.state == "active")
            row = connection.execute(statement).first()
        return self._decode_item(row) if row else None

    def memory_history(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_key: str,
    ) -> list[dict[str, Any]]:
        key = _memory_key(memory_key)
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            rows = connection.execute(
                select(self.items)
                .where(
                    and_(
                        self.items.c.workspace_id == workspace_id,
                        self.items.c.project_id == project_id,
                        self.items.c.memory_key == key,
                    )
                )
                .order_by(self.items.c.revision.asc())
            ).all()
        return [self._decode_item(row) for row in rows]

    def set_memory_state(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_key: str,
        state: str,
        source_type: str,
        source_id: str,
        source_excerpt: str = "",
    ) -> dict[str, Any]:
        current = self.current_memory(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            memory_key=memory_key,
            include_inactive=True,
        )
        if current is None:
            raise KeyError(memory_key)
        return self.put_memory(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            memory_key=current["memory_key"],
            kind=current["kind"],
            content=current["content"],
            value=current.get("value") or {},
            state=state,
            confidence=float(current["confidence"]),
            importance=float(current["importance"]),
            pinned=bool(current["pinned"]),
            build_ref=current.get("build_ref"),
            branch_ref=current.get("branch_ref"),
            commit_ref=current.get("commit_ref"),
            environment_ref=current.get("environment_ref"),
            valid_from=current.get("valid_from"),
            valid_to=current.get("valid_to"),
            expires_at=current.get("expires_at"),
            source_type=source_type,
            source_id=source_id,
            source_excerpt=source_excerpt,
        )

    @staticmethod
    def _scope_predicate(column, supplied: str | None):
        if supplied is None:
            # No identity means no right to consume identity-specific memory. This is a
            # deliberate anti-staleness rule, not a retrieval-quality compromise.
            return column.is_(None)
        return or_(column.is_(None), column == supplied)

    def list_current_memories(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        build_ref: str | None = None,
        branch_ref: str | None = None,
        commit_ref: str | None = None,
        environment_ref: str | None = None,
        at_time: float | None = None,
        kinds: set[str] | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        moment = time.time() if at_time is None else float(at_time)
        with self.engine.connect() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            conditions = [
                self.heads.c.workspace_id == workspace_id,
                self.heads.c.project_id == project_id,
                self.heads.c.state == "active",
                self.items.c.state == "active",
                self._scope_predicate(self.items.c.build_ref, build_ref),
                self._scope_predicate(self.items.c.branch_ref, branch_ref),
                self._scope_predicate(self.items.c.commit_ref, commit_ref),
                self._scope_predicate(self.items.c.environment_ref, environment_ref),
                or_(self.items.c.valid_from.is_(None), self.items.c.valid_from <= moment),
                or_(self.items.c.valid_to.is_(None), self.items.c.valid_to > moment),
                or_(self.items.c.expires_at.is_(None), self.items.c.expires_at > moment),
            ]
            if kinds:
                normalized = {str(kind).lower() for kind in kinds}
                unknown = normalized - _MEMORY_KINDS
                if unknown:
                    raise ValueError(f"不支持的 memory kinds: {sorted(unknown)}")
                conditions.append(self.items.c.kind.in_(sorted(normalized)))
            rows = connection.execute(
                select(self.items)
                .select_from(
                    self.heads.join(self.items, self.heads.c.memory_id == self.items.c.id)
                )
                .where(and_(*conditions))
                .order_by(
                    self.items.c.pinned.desc(),
                    self.items.c.importance.desc(),
                    self.items.c.confidence.desc(),
                    self.items.c.created_at.desc(),
                )
                .limit(max(1, min(2000, int(limit))))
            ).all()
        return [self._decode_item(row) for row in rows]

    def search_memories(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        query: str,
        build_ref: str | None = None,
        branch_ref: str | None = None,
        commit_ref: str | None = None,
        environment_ref: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 24,
        candidate_limit: int = 1000,
    ) -> list[dict[str, Any]]:
        query_tokens = _search_tokens(query)
        candidates = self.list_current_memories(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            build_ref=build_ref,
            branch_ref=branch_ref,
            commit_ref=commit_ref,
            environment_ref=environment_ref,
            kinds=kinds,
            limit=candidate_limit,
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        normalized_query = str(query or "").strip().lower()
        for row in candidates:
            content = f"{row['memory_key']} {row['content']}"
            tokens = _search_tokens(content)
            overlap = (
                len(query_tokens & tokens) / max(1, len(query_tokens))
                if query_tokens
                else 0.0
            )
            exact_key = 1.0 if normalized_query and normalized_query in row["memory_key"] else 0.0
            score = (
                0.58 * overlap
                + 0.16 * exact_key
                + 0.10 * float(row["confidence"])
                + 0.08 * float(row["importance"])
                + 0.08 * (1.0 if row["pinned"] else 0.0)
            )
            if score > 0.08 or row["pinned"]:
                enriched = dict(row)
                enriched["retrieval_score"] = round(score, 6)
                scored.append((score, enriched))
        scored.sort(key=lambda pair: (pair[0], pair[1]["revision"]), reverse=True)
        return [row for _score, row in scored[: max(1, min(100, int(top_k)))]]

    def add_relation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        from_key: str,
        relation: str,
        to_key: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        relation = str(relation or "").strip().lower()
        if relation not in _RELATIONS:
            raise ValueError(f"不支持的 relation: {relation}")
        from_key = _memory_key(from_key)
        to_key = _memory_key(to_key)
        if from_key == to_key:
            raise ValueError("memory relation 不能自环")
        now = time.time()
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            for key in (from_key, to_key):
                head = connection.execute(
                    select(self.heads.c.memory_id).where(
                        and_(
                            self.heads.c.project_id == project_id,
                            self.heads.c.memory_key == key,
                        )
                    )
                ).first()
                if head is None:
                    raise KeyError(key)
            relation_id = _id("relation")
            try:
                connection.execute(
                    insert(self.relations).values(
                        id=relation_id,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        from_key=from_key,
                        relation=relation,
                        to_key=to_key,
                        source_id=(str(source_id)[:128] if source_id else None),
                        created_by=actor_id,
                        created_at=now,
                    )
                )
            except IntegrityError:
                row = connection.execute(
                    select(self.relations).where(
                        and_(
                            self.relations.c.project_id == project_id,
                            self.relations.c.from_key == from_key,
                            self.relations.c.relation == relation,
                            self.relations.c.to_key == to_key,
                        )
                    )
                ).first()
                return self._dict(row)
            row = connection.execute(
                select(self.relations).where(self.relations.c.id == relation_id)
            ).first()
        return self._dict(row)

    def record_usage(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        memory_id: str,
        conversation_id: str | None,
        reason: str,
        score: float | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            row = connection.execute(
                select(self.items.c.memory_key).where(
                    and_(
                        self.items.c.id == memory_id,
                        self.items.c.workspace_id == workspace_id,
                        self.items.c.project_id == project_id,
                    )
                )
            ).first()
            if row is None:
                raise KeyError(memory_id)
            connection.execute(
                insert(self.usage).values(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    memory_id=memory_id,
                    memory_key=str(row[0]),
                    conversation_id=conversation_id,
                    reason=str(reason or "retrieval")[:96],
                    score=(float(score) if score is not None else None),
                    used_at=time.time(),
                )
            )

    def delete_project_memory(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
    ) -> int:
        """Hard-delete governed semantic memory while leaving raw source evidence untouched."""
        with self.engine.begin() as connection:
            self._require_member(connection, workspace_id, actor_id)
            self._require_project(connection, workspace_id, project_id)
            connection.execute(
                delete(self.usage).where(self.usage.c.project_id == project_id)
            )
            connection.execute(
                delete(self.relations).where(self.relations.c.project_id == project_id)
            )
            connection.execute(
                delete(self.heads).where(self.heads.c.project_id == project_id)
            )
            result = connection.execute(
                delete(self.items).where(self.items.c.project_id == project_id)
            )
        return int(result.rowcount or 0)
