from __future__ import annotations

from sqlalchemy import Index, event
from sqlalchemy.engine import Engine
from sqlalchemy.schema import Table


ACTIVE_JOB_INDEX = "uq_analysis_jobs_active_conversation"
ACTIVE_JOB_CONFLICT = "当前任务已有执行正在进行"
_registered = False


def _attach_single_active_job_index(table: Table, _metadata) -> None:
    if table.name != "analysis_jobs":
        return
    if any(index.name == ACTIVE_JOB_INDEX for index in table.indexes):
        return
    active = table.c.status.in_(("queued", "running"))
    Index(
        ACTIVE_JOB_INDEX,
        table.c.workspace_id,
        table.c.conversation_id,
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def _translate_single_active_job_conflict(exception_context):
    statement = str(exception_context.statement or "")
    if "analysis_jobs" not in statement or "INSERT" not in statement.upper():
        return None
    original = str(exception_context.original_exception or "")
    named_constraint = ACTIVE_JOB_INDEX in original
    sqlite_columns = (
        "analysis_jobs.workspace_id" in original
        and "analysis_jobs.conversation_id" in original
    )
    if named_constraint or sqlite_columns:
        return ValueError(ACTIVE_JOB_CONFLICT)
    return None


def install_product_schema_invariants() -> None:
    """Install DB-level product invariants before ConversationStore instances are created."""
    global _registered
    if _registered:
        return
    event.listen(Table, "after_parent_attach", _attach_single_active_job_index)
    event.listen(
        Engine,
        "handle_error",
        _translate_single_active_job_conflict,
        retval=True,
    )
    _registered = True
