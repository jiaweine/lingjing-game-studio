from __future__ import annotations

from sqlalchemy import Index, MetaData, event
from sqlalchemy.engine import Engine


ACTIVE_JOB_INDEX = "uq_analysis_jobs_active_conversation"
ACTIVE_JOB_CONFLICT = "当前任务已有执行正在进行"
PENDING_APPROVAL_INDEX = "uq_approval_requests_pending_action"
PENDING_APPROVAL_CONFLICT = "当前任务已有待处理审批"
_registered = False


def _attach_product_indexes(metadata: MetaData, _connection, **_kwargs) -> None:
    """Attach app-only indexes immediately before metadata.create_all().

    Alembic emits table/index DDL directly rather than calling this application's
    MetaData.create_all(), so migrations remain the sole owner of upgrade DDL.
    """
    jobs = metadata.tables.get("analysis_jobs")
    if jobs is not None:
        required = {"workspace_id", "conversation_id", "status"}
        if required.issubset(jobs.c.keys()) and not any(
            index.name == ACTIVE_JOB_INDEX for index in jobs.indexes
        ):
            active = jobs.c.status.in_(("queued", "running"))
            Index(
                ACTIVE_JOB_INDEX,
                jobs.c.workspace_id,
                jobs.c.conversation_id,
                unique=True,
                sqlite_where=active,
                postgresql_where=active,
            )

    approvals = metadata.tables.get("approval_requests")
    if approvals is not None:
        required = {"workspace_id", "conversation_id", "action", "status"}
        if required.issubset(approvals.c.keys()) and not any(
            index.name == PENDING_APPROVAL_INDEX for index in approvals.indexes
        ):
            pending = approvals.c.status == "pending"
            Index(
                PENDING_APPROVAL_INDEX,
                approvals.c.workspace_id,
                approvals.c.conversation_id,
                approvals.c.action,
                unique=True,
                sqlite_where=pending,
                postgresql_where=pending,
            )


def _translate_product_invariant_conflict(exception_context):
    statement = str(exception_context.statement or "")
    if "INSERT" not in statement.upper():
        return None
    original = str(exception_context.original_exception or "")

    if "analysis_jobs" in statement:
        named_constraint = ACTIVE_JOB_INDEX in original
        sqlite_columns = (
            "analysis_jobs.workspace_id" in original
            and "analysis_jobs.conversation_id" in original
        )
        if named_constraint or sqlite_columns:
            return ValueError(ACTIVE_JOB_CONFLICT)

    if "approval_requests" in statement:
        named_constraint = PENDING_APPROVAL_INDEX in original
        sqlite_columns = (
            "approval_requests.workspace_id" in original
            and "approval_requests.conversation_id" in original
            and "approval_requests.action" in original
        )
        if named_constraint or sqlite_columns:
            return ValueError(PENDING_APPROVAL_CONFLICT)

    return None


def install_product_schema_invariants() -> None:
    """Install DB-level product invariants before ConversationStore instances are created."""
    global _registered
    if _registered:
        return
    event.listen(MetaData, "before_create", _attach_product_indexes)
    event.listen(
        Engine,
        "handle_error",
        _translate_product_invariant_conflict,
        retval=True,
    )
    _registered = True
