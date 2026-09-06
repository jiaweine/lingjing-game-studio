from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    insert,
    select,
    text as sql_text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .project_memory import (
    MemoryConflict,
    ProjectMemoryStore,
    _MEMORY_KINDS,
    _clean_scope,
    _id,
    _memory_key,
    _scope_key,
)
from .project_packet import ProjectScopeSnapshot

_EXTRACTOR_VERSION = "deterministic-user-memory-v2"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;])|(?<=[.!?])\s+|\n+")

# Proposal extraction deliberately optimizes for precision over recall. Soft imperative
# language such as "keep" / bare "use" is excluded because it commonly describes only the
# current task rather than a durable project rule.
_CONSTRAINT_MARKERS = (
    "必须",
    "禁止",
    "务必",
    "只允许",
    "不得",
    "must",
    "do not",
    "don't",
    "never",
    "only allow",
    "is required",
)
_DECISION_MARKERS = (
    "决定",
    "确定采用",
    "确定改为",
    "确定改成",
    "选择",
    "锁定为",
    "定为",
    "decided",
    "we chose",
    "we've chosen",
    "switch to",
    "we will use",
)
_VERIFIED_MARKERS = (
    "已经确认",
    "已确认",
    "确认过",
    "已经验证",
    "已验证",
    "验证通过",
    "confirmed",
    "verified",
    "validated",
)
_UNCERTAIN_MARKERS = (
    "待确认",
    "待验证",
    "未确认",
    "未验证",
    "尚未确认",
    "尚未验证",
    "不确定",
    "可能",
    "也许",
    "猜测",
    "假设",
    "疑似",
    "unconfirmed",
    "unverified",
    "uncertain",
    "maybe",
    "possibly",
    "hypothesis",
)
_QUESTION_PREFIXES = (
    "是否",
    "是不是",
    "能否",
    "可否",
    "要不要",
    "需不需要",
    "有没有",
    "should ",
    "could ",
    "can ",
    "is it ",
    "are we ",
    "do we ",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _sentences(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in _SENTENCE_SPLIT_RE.split(value)
        if re.sub(r"\s+", " ", part).strip()
    ][:40]


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _question_like(sentence: str) -> bool:
    value = _normalize(sentence)
    return (
        sentence.rstrip().endswith(("?", "？"))
        or value.startswith(_QUESTION_PREFIXES)
        or "吗？" in sentence
        or "吗?" in sentence
    )


def _proposal_kind(sentence: str) -> str | None:
    normalized = _normalize(sentence)
    if not normalized or len(normalized) < 4 or _question_like(sentence):
        return None
    if _contains(normalized, _UNCERTAIN_MARKERS):
        return None
    # Explicit confirmation wins over decision/constraint wording because it records a
    # user-asserted fact. It remains user-confirmed memory, never verifier evidence.
    if _contains(normalized, _VERIFIED_MARKERS):
        return "fact"
    if _contains(normalized, _DECISION_MARKERS):
        return "decision"
    if _contains(normalized, _CONSTRAINT_MARKERS):
        return "constraint"
    return None


def _fingerprint(kind: str, content: str, scope: ProjectScopeSnapshot) -> str:
    payload = {
        "kind": kind,
        "content": _normalize(content),
        "scope": scope.to_dict(),
        "extractor": _EXTRACTOR_VERSION,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _suggested_key(kind: str, content: str) -> str:
    digest = hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()[:16]
    return f"proposal.{kind}.{digest}"


class MemoryConsolidator:
    """Persist reviewable user-memory proposals; never auto-promote text to memory."""

    def __init__(
        self,
        engine: Engine,
        project_store: ProjectMemoryStore,
        *,
        auto_create_schema: bool = True,
    ) -> None:
        self.engine = engine
        self.project_store = project_store
        self.metadata = MetaData()
        self.proposals = Table(
            "context_memory_proposals",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), nullable=False, index=True),
            Column("project_id", String(64), nullable=False, index=True),
            Column("conversation_id", String(64), nullable=False, index=True),
            Column("message_id", String(64), nullable=False, index=True),
            Column("fingerprint", String(64), nullable=False),
            Column("suggested_key", String(240), nullable=False),
            Column("kind", String(48), nullable=False),
            Column("content", Text, nullable=False),
            Column("build_ref", String(160), nullable=True),
            Column("branch_ref", String(200), nullable=True),
            Column("commit_ref", String(160), nullable=True),
            Column("environment_ref", String(160), nullable=True),
            Column("extractor_version", String(64), nullable=False),
            Column("status", String(32), nullable=False, index=True),
            Column("created_by", String(64), nullable=False),
            Column("created_at", Float, nullable=False),
            Column("reviewed_by", String(64), nullable=True),
            Column("reviewed_at", Float, nullable=True),
            Column("approved_memory_id", String(64), nullable=True),
            Column("review_note", Text, nullable=False, default=""),
            UniqueConstraint(
                "project_id",
                "message_id",
                "fingerprint",
                name="uq_context_memory_proposal_source",
            ),
        )
        if auto_create_schema:
            self.metadata.create_all(self.engine)

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row._mapping if hasattr(row, "_mapping") else row)

    def _require_bound_conversation(
        self,
        connection,
        *,
        workspace_id: str,
        project_id: str,
        conversation_id: str,
    ) -> None:
        mapping = connection.execute(
            select(self.project_store.project_conversations).where(
                and_(
                    self.project_store.project_conversations.c.workspace_id
                    == workspace_id,
                    self.project_store.project_conversations.c.project_id == project_id,
                    self.project_store.project_conversations.c.conversation_id
                    == conversation_id,
                )
            )
        ).first()
        if mapping is None:
            raise PermissionError("会话未绑定到该项目")

    def _require_authoritative_user_message(
        self,
        connection,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> None:
        source = connection.execute(
            sql_text(
                "SELECT role, content FROM messages "
                "WHERE id = :message_id AND conversation_id = :conversation_id LIMIT 1"
            ),
            {"message_id": message_id, "conversation_id": conversation_id},
        ).first()
        if source is None:
            raise KeyError(message_id)
        if str(source[0]) != "user":
            raise ValueError("长期记忆 proposal 只能来自权威 user message")
        if str(source[1]) != str(content):
            raise ValueError("proposal content 必须与权威 user message 完全一致")

    def propose_user_message(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        scope: ProjectScopeSnapshot,
        max_retries: int = 4,
    ) -> list[dict[str, Any]]:
        """Derive conservative proposals from an authoritative persisted user message."""
        candidates: list[dict[str, Any]] = []
        for sentence in _sentences(str(content)[:12000]):
            kind = _proposal_kind(sentence)
            if kind is None:
                continue
            text = sentence[:1200]
            candidates.append(
                {
                    "fingerprint": _fingerprint(kind, text, scope),
                    "suggested_key": _suggested_key(kind, text),
                    "kind": kind,
                    "content": text,
                }
            )
        if not candidates:
            return []

        last_error: Exception | None = None
        for _attempt in range(max(1, int(max_retries))):
            try:
                now = time.time()
                with self.engine.begin() as connection:
                    self.project_store._require_member(
                        connection, workspace_id, actor_id
                    )
                    self.project_store._require_project(
                        connection, workspace_id, project_id
                    )
                    self._require_bound_conversation(
                        connection,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        conversation_id=conversation_id,
                    )
                    self._require_authoritative_user_message(
                        connection,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        content=content,
                    )
                    existing = {
                        str(row[0])
                        for row in connection.execute(
                            select(self.proposals.c.fingerprint).where(
                                and_(
                                    self.proposals.c.project_id == project_id,
                                    self.proposals.c.message_id == message_id,
                                )
                            )
                        ).all()
                    }
                    for candidate in candidates:
                        if candidate["fingerprint"] in existing:
                            continue
                        connection.execute(
                            insert(self.proposals).values(
                                id=_id("proposal"),
                                workspace_id=workspace_id,
                                project_id=project_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                fingerprint=candidate["fingerprint"],
                                suggested_key=candidate["suggested_key"],
                                kind=candidate["kind"],
                                content=candidate["content"],
                                build_ref=scope.build_ref,
                                branch_ref=scope.branch_ref,
                                commit_ref=scope.commit_ref,
                                environment_ref=scope.environment_ref,
                                extractor_version=_EXTRACTOR_VERSION,
                                status="pending",
                                created_by=actor_id,
                                created_at=now,
                                review_note="",
                            )
                        )
                break
            except IntegrityError as exc:
                last_error = exc
                continue
        else:
            raise MemoryConflict("memory proposal dedupe CAS failed") from last_error

        return self.list_proposals(
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
            message_id=message_id,
            status=None,
            limit=40,
        )

    def list_proposals(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        status: str | None = "pending",
        conversation_id: str | None = None,
        message_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            self.project_store._require_member(connection, workspace_id, actor_id)
            self.project_store._require_project(connection, workspace_id, project_id)
            statement = select(self.proposals).where(
                and_(
                    self.proposals.c.workspace_id == workspace_id,
                    self.proposals.c.project_id == project_id,
                )
            )
            if status is not None:
                status = str(status).strip().lower()
                if status not in {"pending", "approved", "rejected"}:
                    raise ValueError("不支持的 proposal status")
                statement = statement.where(self.proposals.c.status == status)
            if conversation_id:
                statement = statement.where(
                    self.proposals.c.conversation_id == conversation_id
                )
            if message_id:
                statement = statement.where(self.proposals.c.message_id == message_id)
            rows = connection.execute(
                statement.order_by(self.proposals.c.created_at.desc()).limit(
                    max(1, min(500, int(limit)))
                )
            ).all()
        return [self._dict(row) for row in rows]

    def _approved_result(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            self.project_store._require_member(connection, workspace_id, actor_id)
            self.project_store._require_project(connection, workspace_id, project_id)
            proposal_row = connection.execute(
                select(self.proposals).where(
                    and_(
                        self.proposals.c.id == proposal_id,
                        self.proposals.c.workspace_id == workspace_id,
                        self.proposals.c.project_id == project_id,
                    )
                )
            ).first()
            if proposal_row is None:
                raise KeyError(proposal_id)
            proposal = self._dict(proposal_row)
            if proposal["status"] != "approved":
                return None
            memory_id = proposal.get("approved_memory_id")
            memory_row = (
                connection.execute(
                    select(self.project_store.items).where(
                        self.project_store.items.c.id == memory_id
                    )
                ).first()
                if memory_id
                else None
            )
            return {
                "proposal": proposal,
                "memory": (
                    self.project_store._decode_item(memory_row)
                    if memory_row is not None
                    else None
                ),
            }

    def approve_proposal(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        proposal_id: str,
        memory_key: str | None = None,
        content: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically promote one reviewed proposal into an authoritative memory revision."""
        now = time.time()
        try:
            with self.engine.begin() as connection:
                self.project_store._require_member(connection, workspace_id, actor_id)
                self.project_store._require_project(connection, workspace_id, project_id)
                proposal_row = connection.execute(
                    select(self.proposals)
                    .where(
                        and_(
                            self.proposals.c.id == proposal_id,
                            self.proposals.c.workspace_id == workspace_id,
                            self.proposals.c.project_id == project_id,
                        )
                    )
                    .with_for_update()
                ).first()
                if proposal_row is None:
                    raise KeyError(proposal_id)
                proposal = self._dict(proposal_row)
                if proposal["status"] == "approved":
                    memory_id = proposal.get("approved_memory_id")
                    memory_row = (
                        connection.execute(
                            select(self.project_store.items).where(
                                self.project_store.items.c.id == memory_id
                            )
                        ).first()
                        if memory_id
                        else None
                    )
                    return {
                        "proposal": proposal,
                        "memory": (
                            self.project_store._decode_item(memory_row)
                            if memory_row is not None
                            else None
                        ),
                    }
                if proposal["status"] != "pending":
                    raise ValueError("只有 pending proposal 可以批准")

                key = _memory_key(memory_key or proposal["suggested_key"])
                memory_content = str(content or proposal["content"]).strip()
                if not memory_content:
                    raise ValueError("memory content 不能为空")
                if len(memory_content) > 20000:
                    raise ValueError("memory content 过长")
                kind = str(proposal["kind"]).strip().lower()
                if kind not in _MEMORY_KINDS:
                    raise ValueError(f"不支持的 memory kind: {kind}")
                build_ref, branch_ref, commit_ref, environment_ref = _clean_scope(
                    proposal.get("build_ref"),
                    proposal.get("branch_ref"),
                    proposal.get("commit_ref"),
                    proposal.get("environment_ref"),
                )
                scope_key = _scope_key(
                    build_ref, branch_ref, commit_ref, environment_ref
                )

                head_row = connection.execute(
                    select(self.project_store.heads)
                    .where(
                        and_(
                            self.project_store.heads.c.project_id == project_id,
                            self.project_store.heads.c.memory_key == key,
                            self.project_store.heads.c.scope_key == scope_key,
                        )
                    )
                    .with_for_update()
                ).first()
                head = self._dict(head_row) if head_row else None
                revision = int(head["revision"]) + 1 if head else 1
                memory_id = _id("memory")
                supersedes_id = str(head["memory_id"]) if head else None
                importance = {
                    "constraint": 0.90,
                    "decision": 0.85,
                    "fact": 0.80,
                }.get(kind, 0.70)
                connection.execute(
                    insert(self.project_store.items).values(
                        id=memory_id,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        memory_key=key,
                        scope_key=scope_key,
                        revision=revision,
                        kind=kind,
                        content=memory_content,
                        value_json=json.dumps(
                            {
                                "proposal_id": proposal_id,
                                "message_id": proposal["message_id"],
                                "reviewed_by": actor_id,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        state="active",
                        confidence=1.0,
                        importance=importance,
                        pinned=0,
                        build_ref=build_ref,
                        branch_ref=branch_ref,
                        commit_ref=commit_ref,
                        environment_ref=environment_ref,
                        valid_from=None,
                        valid_to=None,
                        expires_at=None,
                        source_type="user_confirmed",
                        source_id=f"proposal:{proposal_id}"[:128],
                        source_excerpt=str(proposal["content"])[:4000],
                        supersedes_id=supersedes_id,
                        created_by=actor_id,
                        created_at=now,
                    )
                )
                if head:
                    result = connection.execute(
                        update(self.project_store.heads)
                        .where(
                            and_(
                                self.project_store.heads.c.project_id == project_id,
                                self.project_store.heads.c.memory_key == key,
                                self.project_store.heads.c.scope_key == scope_key,
                                self.project_store.heads.c.revision
                                == int(head["revision"]),
                                self.project_store.heads.c.memory_id
                                == head["memory_id"],
                            )
                        )
                        .values(
                            workspace_id=workspace_id,
                            memory_id=memory_id,
                            revision=revision,
                            state="active",
                            updated_at=now,
                        )
                    )
                    if result.rowcount != 1:
                        raise MemoryConflict(f"{key}@{scope_key}")
                else:
                    connection.execute(
                        insert(self.project_store.heads).values(
                            workspace_id=workspace_id,
                            project_id=project_id,
                            memory_key=key,
                            scope_key=scope_key,
                            memory_id=memory_id,
                            revision=revision,
                            state="active",
                            updated_at=now,
                        )
                    )

                proposal_update = connection.execute(
                    update(self.proposals)
                    .where(
                        and_(
                            self.proposals.c.id == proposal_id,
                            self.proposals.c.status == "pending",
                        )
                    )
                    .values(
                        status="approved",
                        reviewed_by=actor_id,
                        reviewed_at=now,
                        approved_memory_id=memory_id,
                        review_note=str(note or "")[:4000],
                    )
                )
                if proposal_update.rowcount != 1:
                    raise MemoryConflict(f"proposal:{proposal_id}")
                memory_row = connection.execute(
                    select(self.project_store.items).where(
                        self.project_store.items.c.id == memory_id
                    )
                ).first()
                reviewed = connection.execute(
                    select(self.proposals).where(
                        self.proposals.c.id == proposal_id
                    )
                ).first()
            return {
                "proposal": self._dict(reviewed),
                "memory": self.project_store._decode_item(memory_row),
            }
        except IntegrityError as exc:
            approved = self._approved_result(
                workspace_id=workspace_id,
                actor_id=actor_id,
                project_id=project_id,
                proposal_id=proposal_id,
            )
            if approved is not None:
                return approved
            raise MemoryConflict(f"proposal:{proposal_id}") from exc

    def reject_proposal(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        project_id: str,
        proposal_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        with self.engine.begin() as connection:
            self.project_store._require_member(connection, workspace_id, actor_id)
            self.project_store._require_project(connection, workspace_id, project_id)
            row = connection.execute(
                select(self.proposals)
                .where(
                    and_(
                        self.proposals.c.id == proposal_id,
                        self.proposals.c.workspace_id == workspace_id,
                        self.proposals.c.project_id == project_id,
                    )
                )
                .with_for_update()
            ).first()
            if row is None:
                raise KeyError(proposal_id)
            proposal = self._dict(row)
            if proposal["status"] == "rejected":
                return proposal
            if proposal["status"] != "pending":
                raise ValueError("已批准的 proposal 不能改为 rejected")
            result = connection.execute(
                update(self.proposals)
                .where(
                    and_(
                        self.proposals.c.id == proposal_id,
                        self.proposals.c.status == "pending",
                    )
                )
                .values(
                    status="rejected",
                    reviewed_by=actor_id,
                    reviewed_at=now,
                    review_note=str(note or "")[:4000],
                )
            )
            if result.rowcount != 1:
                raise MemoryConflict(f"proposal:{proposal_id}")
            reviewed = connection.execute(
                select(self.proposals).where(self.proposals.c.id == proposal_id)
            ).first()
        return self._dict(reviewed)
