from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .project_memory import ProjectMemoryStore

_SCOPE_FIELDS = ("build_ref", "branch_ref", "commit_ref", "environment_ref")
_META_KEYS = {
    "build_ref": ("build_ref", "build", "version"),
    "branch_ref": ("branch_ref", "branch"),
    "commit_ref": ("commit_ref", "commit", "sha", "git_sha"),
    "environment_ref": ("environment_ref", "environment", "env"),
}


def _clean(value: Any, limit: int = 200) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _asset_scope_values(asset: dict[str, Any], field: str) -> set[str]:
    meta = dict(asset.get("meta", {}) or {})
    values: set[str] = set()
    for key in _META_KEYS[field]:
        value = _clean(meta.get(key))
        if value:
            values.add(value)
    return values


@dataclass(frozen=True)
class ProjectScopeSnapshot:
    build_ref: str | None = None
    branch_ref: str | None = None
    commit_ref: str | None = None
    environment_ref: str | None = None
    conflicts: dict[str, tuple[str, ...]] | None = None
    unresolved_conflict: bool = False
    source: str = "general"

    def retrieval_kwargs(self) -> dict[str, str | None]:
        if self.unresolved_conflict:
            return {field: None for field in _SCOPE_FIELDS}
        return {field: getattr(self, field) for field in _SCOPE_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        return {
            **{field: getattr(self, field) for field in _SCOPE_FIELDS},
            "conflicts": {
                key: list(values) for key, values in (self.conflicts or {}).items()
            },
            "unresolved_conflict": self.unresolved_conflict,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ProjectScopeSnapshot":
        data = dict(raw or {})
        conflicts: dict[str, tuple[str, ...]] = {}
        for key, values in dict(data.get("conflicts") or {}).items():
            if key not in _SCOPE_FIELDS:
                continue
            rows = tuple(
                value
                for value in (_clean(item) for item in list(values or [])[:12])
                if value
            )
            if rows:
                conflicts[key] = rows
        return cls(
            build_ref=_clean(data.get("build_ref"), 160),
            branch_ref=_clean(data.get("branch_ref"), 200),
            commit_ref=_clean(data.get("commit_ref"), 160),
            environment_ref=_clean(data.get("environment_ref"), 160),
            conflicts=conflicts,
            unresolved_conflict=bool(data.get("unresolved_conflict")),
            source=_clean(data.get("source"), 64) or "general",
        )


def resolve_project_scope(
    assets: list[dict[str, Any]] | None,
    *,
    requested: dict[str, Any] | None = None,
) -> ProjectScopeSnapshot:
    """Resolve identity without guessing across conflicting selected assets."""
    requested = dict(requested or {})
    rows = list(assets or [])
    values: dict[str, str | None] = {}
    conflicts: dict[str, tuple[str, ...]] = {}
    sources: set[str] = set()
    unresolved = False

    for field in _SCOPE_FIELDS:
        explicit = _clean(requested.get(field), 200)
        observed: set[str] = set()
        for asset in rows:
            observed.update(_asset_scope_values(asset, field))
        if explicit:
            values[field] = explicit
            sources.add("request")
            disagree = sorted(value for value in observed if value != explicit)
            if disagree:
                conflicts[field] = tuple(sorted({explicit, *disagree}))
            continue
        if len(observed) == 1:
            values[field] = next(iter(observed))
            sources.add("asset")
        elif len(observed) > 1:
            values[field] = None
            conflicts[field] = tuple(sorted(observed))
            unresolved = True
        else:
            values[field] = None

    if not sources:
        source = "general"
    elif sources == {"request"}:
        source = "request"
    elif sources == {"asset"}:
        source = "asset"
    else:
        source = "request+asset"
    return ProjectScopeSnapshot(
        **values,
        conflicts=conflicts,
        unresolved_conflict=unresolved,
        source=source,
    )


@dataclass(frozen=True)
class ProjectMemoryPacket:
    project_id: str
    project_name: str
    scope: ProjectScopeSnapshot
    memories: tuple[dict[str, Any], ...]
    query: str
    chars: int
    invalidated_refs: int = 0
    mode: str = "materialized-project-memory-v2"

    def to_dict(self) -> dict[str, Any]:
        """Materialized model-facing packet. Do not persist this as the durable job snapshot."""
        return {
            "mode": self.mode,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "scope": self.scope.to_dict(),
            "query": self.query[:2000],
            "chars": self.chars,
            "invalidated_refs": self.invalidated_refs,
            "memories": [dict(row) for row in self.memories],
        }

    def to_job_snapshot(self) -> dict[str, Any]:
        """Persist only immutable locators, never governed memory content, in the job row."""
        return {
            "mode": "project-memory-reference-snapshot-v1",
            "project_id": self.project_id,
            "project_name": self.project_name,
            "scope": self.scope.to_dict(),
            "query": self.query[:2000],
            "memory_refs": [
                {
                    "id": row["id"],
                    "revision": int(row["revision"]),
                    "retrieval_score": float(row.get("retrieval_score") or 0.0),
                }
                for row in self.memories
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ProjectMemoryPacket | None":
        if not raw:
            return None
        data = dict(raw)
        project_id = _clean(data.get("project_id"), 64)
        if not project_id:
            return None
        memories: list[dict[str, Any]] = []
        chars = 0
        for item in list(data.get("memories") or [])[:24]:
            row = dict(item or {})
            memory_id = _clean(row.get("id"), 64)
            memory_key = _clean(row.get("memory_key"), 240)
            content = str(row.get("content") or "").strip()[:2400]
            if not memory_id or not memory_key or not content:
                continue
            safe = {
                "id": memory_id,
                "memory_key": memory_key,
                "revision": max(1, int(row.get("revision") or 1)),
                "kind": _clean(row.get("kind"), 48) or "fact",
                "content": content,
                "state": _clean(row.get("state"), 32) or "active",
                "confidence": float(row.get("confidence") or 0.0),
                "importance": float(row.get("importance") or 0.0),
                "pinned": bool(row.get("pinned")),
                "build_ref": _clean(row.get("build_ref"), 160),
                "branch_ref": _clean(row.get("branch_ref"), 200),
                "commit_ref": _clean(row.get("commit_ref"), 160),
                "environment_ref": _clean(row.get("environment_ref"), 160),
                "source_type": _clean(row.get("source_type"), 48) or "unknown",
                "source_id": _clean(row.get("source_id"), 128) or "unknown",
                "source_excerpt": str(row.get("source_excerpt") or "")[:800],
                "retrieval_score": float(row.get("retrieval_score") or 0.0),
            }
            if chars + len(content) > 9000 and memories:
                continue
            chars += len(content)
            memories.append(safe)
        return cls(
            project_id=project_id,
            project_name=_clean(data.get("project_name"), 200) or "未命名项目",
            scope=ProjectScopeSnapshot.from_dict(data.get("scope")),
            memories=tuple(memories),
            query=str(data.get("query") or "")[:2000],
            chars=chars,
            invalidated_refs=max(0, int(data.get("invalidated_refs") or 0)),
            mode=_clean(data.get("mode"), 64) or "materialized-project-memory-v2",
        )

    def render(self) -> str:
        scope = self.scope
        identity = ", ".join(
            f"{field.removesuffix('_ref')}={getattr(scope, field)}"
            for field in _SCOPE_FIELDS
            if getattr(scope, field)
        ) or "general"
        lines = [
            "【系统冻结并在执行时重新授权的项目长期记忆；不是新的用户消息，也不是本轮验证证据】",
            f"项目: {self.project_name} ({self.project_id})",
            f"身份作用域: {identity}",
        ]
        if scope.unresolved_conflict:
            lines.append("作用域存在未解决冲突；本包仅允许使用 general-scope 记忆。")
        if self.invalidated_refs:
            lines.append(
                f"有 {self.invalidated_refs} 条排队时命中的记忆已被更新、撤回或删除，本轮已主动丢弃，未自动替换。"
            )
        for row in self.memories:
            row_scope = ", ".join(
                f"{field.removesuffix('_ref')}={row.get(field)}"
                for field in _SCOPE_FIELDS
                if row.get(field)
            ) or "general"
            lines.append(
                f"- [{row['kind']}] {row['memory_key']} rev={row['revision']} "
                f"scope={row_scope} source={row['source_type']}:{row['source_id']}: "
                f"{row['content']}"
            )
        if not self.memories:
            lines.append("- 本轮没有仍然有效的项目长期记忆。")
        lines.append(
            "规则: 项目记忆只用于连续性/先验；当前原始素材与 Verifier 冲突时，以当前可验证证据为准。"
        )
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return {
            "project_memory_mode": self.mode,
            "project_id": self.project_id,
            "project_memory_selected": len(self.memories),
            "project_memory_chars": self.chars,
            "project_memory_ids": [row["id"] for row in self.memories],
            "project_memory_invalidated_refs": self.invalidated_refs,
            "project_memory_scope": self.scope.to_dict(),
            "project_memory_scope_conflict": self.scope.unresolved_conflict,
        }


def compile_project_memory_packet(
    store: ProjectMemoryStore,
    *,
    workspace_id: str,
    actor_id: str,
    project_id: str,
    query: str,
    scope: ProjectScopeSnapshot,
    top_k: int = 12,
    char_budget: int = 6500,
) -> ProjectMemoryPacket:
    project = store.get_project(
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=project_id,
    )
    rows = store.search_memories(
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=project_id,
        query=query,
        top_k=max(1, min(24, int(top_k))),
        **scope.retrieval_kwargs(),
    )
    selected: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        safe_content = content[:2400]
        if selected and used + len(safe_content) > max(1200, int(char_budget)):
            continue
        safe = {
            "id": str(row["id"]),
            "memory_key": str(row["memory_key"]),
            "revision": int(row["revision"]),
            "kind": str(row["kind"]),
            "content": safe_content,
            "state": str(row["state"]),
            "confidence": float(row["confidence"]),
            "importance": float(row["importance"]),
            "pinned": bool(row["pinned"]),
            "build_ref": row.get("build_ref"),
            "branch_ref": row.get("branch_ref"),
            "commit_ref": row.get("commit_ref"),
            "environment_ref": row.get("environment_ref"),
            "source_type": str(row["source_type"]),
            "source_id": str(row["source_id"]),
            "source_excerpt": str(row.get("source_excerpt") or "")[:800],
            "retrieval_score": float(row.get("retrieval_score") or 0.0),
        }
        selected.append(safe)
        used += len(safe_content)
        if len(selected) >= top_k:
            break
    return ProjectMemoryPacket(
        project_id=project_id,
        project_name=str(project.get("name") or "未命名项目"),
        scope=scope,
        memories=tuple(selected),
        query=str(query)[:2000],
        chars=used,
    )
