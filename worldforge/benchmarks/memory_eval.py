from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from worldforge.context.memory_consolidator import MemoryConsolidator
from worldforge.context.project_job import (
    build_job_project_context,
    materialize_job_project_memory,
)
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.context.project_packet import resolve_project_scope
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


@dataclass(frozen=True)
class MemoryBenchmarkResult:
    cross_conversation_recall: float
    update_tracking: float
    scoped_version_isolation: float
    conflict_abstention: float
    selective_forgetting: float
    pending_memory_isolation: float
    provenance_integrity: float
    queued_snapshot_revocation: float
    restart_persistence: float
    score: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _conversation(product: ConversationStore, title: str) -> dict[str, Any]:
    return product.create_conversation(
        title,
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )


def _bind(
    memory: ProjectMemoryStore,
    project_id: str,
    conversation_id: str,
) -> None:
    memory.bind_conversation(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project_id,
        conversation_id=conversation_id,
    )


def _put(
    memory: ProjectMemoryStore,
    project_id: str,
    *,
    key: str,
    content: str,
    source_id: str,
    build_ref: str | None = None,
    branch_ref: str | None = None,
    kind: str = "fact",
) -> dict[str, Any]:
    return memory.put_memory(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project_id,
        memory_key=key,
        kind=kind,
        content=content,
        build_ref=build_ref,
        branch_ref=branch_ref,
        source_type="benchmark",
        source_id=source_id,
        confidence=1.0,
        importance=0.9,
    )


def _ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("id")) for row in rows}


def _contents(rows: list[dict[str, Any]]) -> str:
    return "\n".join(str(row.get("content", "")) for row in rows)


def run_memory_benchmark() -> MemoryBenchmarkResult:
    """Deterministic correctness benchmark for governed long-term project memory.

    This benchmark intentionally scores storage/retrieval/governance semantics, not model QA
    quality. A memory system that retrieves stale, cross-version or unapproved facts should
    fail before any LLM benchmark is attempted.
    """
    with TemporaryDirectory(prefix="lingjing-memory-bench-") as tmpdir:
        root = Path(tmpdir)
        db_path = root / "product.db"
        product = ConversationStore(
            db_path,
            root / "assets",
            seed_dev_identity=True,
        )
        memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
        consolidator = MemoryConsolidator(
            product.engine,
            memory,
            auto_create_schema=True,
        )
        project = memory.create_project(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            name="MemoryBench Atlas",
            default_branch="main",
        )
        project_id = str(project["id"])
        source_conversation = _conversation(product, "Source")
        followup_conversation = _conversation(product, "Follow-up")
        _bind(memory, project_id, source_conversation["id"])
        _bind(memory, project_id, followup_conversation["id"])

        # 1) Pending text is not project truth; explicit approval enables cross-conversation
        # retrieval while preserving user-confirmed provenance.
        source_message = product.add_message(
            source_conversation["id"],
            "user",
            "发布前必须运行 regression-suite-gamma。",
            {},
            workspace_id=DEMO_WORKSPACE_ID,
        )
        proposals = consolidator.propose_user_message(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            conversation_id=source_conversation["id"],
            message_id=source_message["id"],
            content=source_message["content"],
            scope=resolve_project_scope([]),
        )
        pending_search = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="regression-suite-gamma 发布前要求",
        )
        pending_memory_isolation = float(bool(proposals) and not pending_search)
        approved = consolidator.approve_proposal(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            proposal_id=proposals[0]["id"],
            memory_key="release.regression.required",
        )["memory"]
        followup_snapshot = build_job_project_context(
            memory,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            conversation_id=followup_conversation["id"],
            query="发布前 regression-suite-gamma 有什么要求？",
        )
        snapshot_refs = {
            str(row.get("id"))
            for row in (
                (followup_snapshot or {})
                .get("memory_snapshot", {})
                .get("memory_refs", [])
            )
        }
        cross_conversation_recall = float(str(approved["id"]) in snapshot_refs)
        provenance_integrity = float(
            approved.get("source_type") == "user_confirmed"
            and approved.get("source_id") == f"proposal:{proposals[0]['id']}"
            and approved.get("content") == proposals[0]["content"]
        )

        # 2) Knowledge update: only the newest head may be returned for a semantic key.
        old = _put(
            memory,
            project_id,
            key="combat.shield.cooldown",
            content="护盾冷却是 6 秒",
            source_id="update-v1",
        )
        new = _put(
            memory,
            project_id,
            key="combat.shield.cooldown",
            content="护盾冷却是 5 秒",
            source_id="update-v2",
        )
        updated_rows = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="护盾冷却",
        )
        update_tracking = float(
            new["id"] in _ids(updated_rows)
            and old["id"] not in _ids(updated_rows)
            and "5 秒" in _contents(updated_rows)
            and "6 秒" not in _contents(updated_rows)
            and int(new["revision"]) == 2
        )

        # 3) Build-scoped heads must not contaminate one another or the no-identity query.
        build_a = _put(
            memory,
            project_id,
            key="combat.damage.multiplier",
            content="build 1.4.7 伤害倍率是 1.20",
            source_id="build-a",
            build_ref="1.4.7",
        )
        build_b = _put(
            memory,
            project_id,
            key="combat.damage.multiplier",
            content="build 2.0.0 伤害倍率是 0.95",
            source_id="build-b",
            build_ref="2.0.0",
        )
        rows_a = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="伤害倍率",
            build_ref="1.4.7",
        )
        rows_b = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="伤害倍率",
            build_ref="2.0.0",
        )
        rows_general = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="伤害倍率",
        )
        scoped_version_isolation = float(
            build_a["id"] in _ids(rows_a)
            and build_b["id"] not in _ids(rows_a)
            and build_b["id"] in _ids(rows_b)
            and build_a["id"] not in _ids(rows_b)
            and build_a["id"] not in _ids(rows_general)
            and build_b["id"] not in _ids(rows_general)
        )

        # 4) Conflicting selected-asset identities must abstain from version-specific truth.
        general_rule = _put(
            memory,
            project_id,
            key="release.rollback.rule",
            content="发生 critical regression 时必须 rollback",
            source_id="general-rule",
            kind="constraint",
        )
        scoped_rule = _put(
            memory,
            project_id,
            key="release.hotfix.owner",
            content="build 1.4.7 hotfix owner 是 Team Red",
            source_id="scoped-rule",
            build_ref="1.4.7",
        )
        conflict_snapshot = build_job_project_context(
            memory,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            conversation_id=followup_conversation["id"],
            query="release hotfix rollback 规则",
            selected_assets=[
                {"meta": {"build": "1.4.7"}},
                {"meta": {"build": "2.0.0"}},
            ],
        )
        conflict_refs = {
            str(row.get("id"))
            for row in (
                (conflict_snapshot or {})
                .get("memory_snapshot", {})
                .get("memory_refs", [])
            )
        }
        conflict_scope = (conflict_snapshot or {}).get("scope", {})
        conflict_abstention = float(
            bool(conflict_scope.get("unresolved_conflict"))
            and scoped_rule["id"] not in conflict_refs
            and general_rule["id"] in conflict_refs
        )

        # 5) Selective forgetting: retract one head without losing unrelated active memory.
        forget_me = _put(
            memory,
            project_id,
            key="legacy.exploit.signature",
            content="legacy exploit marker omega-17",
            source_id="forget-me",
        )
        keep_me = _put(
            memory,
            project_id,
            key="release.owner",
            content="release owner 是 Team Blue",
            source_id="keep-me",
        )
        memory.set_memory_state(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            memory_key="legacy.exploit.signature",
            state="retracted",
            source_type="benchmark",
            source_id="forget-retract",
        )
        forgotten_rows = memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="omega-17 release owner Team Blue",
        )
        selective_forgetting = float(
            forget_me["id"] not in _ids(forgotten_rows)
            and keep_me["id"] in _ids(forgotten_rows)
            and "omega-17" not in _contents(forgotten_rows)
        )

        # 6) A queued old revision must be invalidated after retraction, never replaced.
        queued = _put(
            memory,
            project_id,
            key="deploy.freeze.window",
            content="deploy freeze window 是 22:00",
            source_id="queue-before-retract",
        )
        queued_context = build_job_project_context(
            memory,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            conversation_id=followup_conversation["id"],
            query="deploy freeze window",
        )
        queued_refs = {
            str(row.get("id"))
            for row in (
                (queued_context or {})
                .get("memory_snapshot", {})
                .get("memory_refs", [])
            )
        }
        memory.set_memory_state(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            memory_key="deploy.freeze.window",
            state="retracted",
            source_type="benchmark",
            source_id="queue-retract",
        )
        materialized = materialize_job_project_memory(
            memory,
            workspace_id=DEMO_WORKSPACE_ID,
            job_context=queued_context,
        )
        queued_snapshot_revocation = float(
            queued["id"] in queued_refs
            and materialized is not None
            and queued["id"] not in _ids(list(materialized.memories))
            and materialized.invalidated_refs >= 1
        )

        # 7) Durable store must survive fresh store objects/process-like restart boundaries.
        persisted_key = "workflow.capture.required"
        persisted = _put(
            memory,
            project_id,
            key=persisted_key,
            content="复现完成后必须保存 evidence pack",
            source_id="restart-persist",
            kind="constraint",
        )
        restarted_product = ConversationStore(
            db_path,
            root / "assets-restarted",
            seed_dev_identity=True,
        )
        restarted_memory = ProjectMemoryStore(
            restarted_product.engine,
            auto_create_schema=False,
        )
        restarted_rows = restarted_memory.search_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project_id,
            query="evidence pack 复现完成",
        )
        restart_persistence = float(persisted["id"] in _ids(restarted_rows))

        metrics = [
            cross_conversation_recall,
            update_tracking,
            scoped_version_isolation,
            conflict_abstention,
            selective_forgetting,
            pending_memory_isolation,
            provenance_integrity,
            queued_snapshot_revocation,
            restart_persistence,
        ]
        score = sum(metrics) / len(metrics)
        passed = all(value == 1.0 for value in metrics)
        return MemoryBenchmarkResult(
            cross_conversation_recall=cross_conversation_recall,
            update_tracking=update_tracking,
            scoped_version_isolation=scoped_version_isolation,
            conflict_abstention=conflict_abstention,
            selective_forgetting=selective_forgetting,
            pending_memory_isolation=pending_memory_isolation,
            provenance_integrity=provenance_integrity,
            queued_snapshot_revocation=queued_snapshot_revocation,
            restart_persistence=restart_persistence,
            score=round(score, 6),
            passed=passed,
        )
