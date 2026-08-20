from __future__ import annotations

from typing import Any

from sqlalchemy import case, distinct, func, select, union


def _scalar(connection, statement, default=0):
    value = connection.execute(statement).scalar_one_or_none()
    return default if value is None else value


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / max(1.0, float(denominator)), 4)


def calculate_product_metrics(store, *, workspace_id: str) -> dict[str, Any]:
    """Aggregate workspace product metrics without loading full history into Python.

    The old implementation materialized every conversation, job, message, product event
    and feedback row on every dashboard refresh. This version keeps the aggregation in
    the database and only returns scalar results.

    `first_task_completion_rate` is retained as a backwards-compatible alias for
    `eventual_task_completion_rate`. New consumers should use the explicit
    `first_attempt_completion_rate` / `eventual_task_completion_rate` pair.
    """

    conversations = store.conversations
    jobs = store.jobs
    messages = store.messages
    events = store.product_events
    feedback = store.result_feedback

    ranked_jobs = (
        select(
            jobs.c.conversation_id.label("conversation_id"),
            jobs.c.status.label("status"),
            func.row_number()
            .over(
                partition_by=jobs.c.conversation_id,
                order_by=(jobs.c.created_at, jobs.c.id),
            )
            .label("rn"),
        )
        .where(jobs.c.workspace_id == workspace_id)
        .subquery()
    )

    per_task_jobs = (
        select(
            jobs.c.conversation_id.label("conversation_id"),
            func.min(jobs.c.created_at).label("started_at"),
            func.min(
                case(
                    (jobs.c.status == "completed", jobs.c.completed_at),
                    else_=None,
                )
            ).label("first_completed_at"),
            func.max(case((jobs.c.status == "completed", 1), else_=0)).label(
                "has_completed"
            ),
            func.max(
                case((jobs.c.status.in_(("failed", "cancelled")), 1), else_=0)
            ).label("has_failed_or_cancelled"),
        )
        .where(jobs.c.workspace_id == workspace_id)
        .group_by(jobs.c.conversation_id)
        .subquery()
    )

    user_turns = (
        select(
            messages.c.conversation_id.label("conversation_id"),
            func.count().label("turns"),
        )
        .select_from(
            messages.join(
                conversations,
                messages.c.conversation_id == conversations.c.id,
            )
        )
        .where(
            conversations.c.workspace_id == workspace_id,
            messages.c.role == "user",
        )
        .group_by(messages.c.conversation_id)
        .subquery()
    )

    failed_or_cancelled_tasks = select(jobs.c.conversation_id).where(
        jobs.c.workspace_id == workspace_id,
        jobs.c.status.in_(("failed", "cancelled")),
    )
    explicit_intervention_tasks = select(events.c.conversation_id).where(
        events.c.workspace_id == workspace_id,
        events.c.conversation_id.is_not(None),
        events.c.name.in_(("task.retry", "task.handoff")),
    )
    manual_intervention_tasks = union(
        failed_or_cancelled_tasks,
        explicit_intervention_tasks,
    ).subquery()

    with store.engine.connect() as connection:
        task_count = int(
            _scalar(
                connection,
                select(func.count()).select_from(conversations).where(
                    conversations.c.workspace_id == workspace_id
                ),
            )
        )
        active_tasks = int(
            _scalar(
                connection,
                select(func.count()).select_from(conversations).where(
                    conversations.c.workspace_id == workspace_id,
                    conversations.c.archived_at.is_(None),
                ),
            )
        )
        job_count = int(
            _scalar(
                connection,
                select(func.count()).select_from(jobs).where(
                    jobs.c.workspace_id == workspace_id
                ),
            )
        )
        task_ids_with_jobs = int(
            _scalar(
                connection,
                select(func.count(distinct(jobs.c.conversation_id))).where(
                    jobs.c.workspace_id == workspace_id
                ),
            )
        )
        first_attempt_completed = int(
            _scalar(
                connection,
                select(func.count()).select_from(ranked_jobs).where(
                    ranked_jobs.c.rn == 1,
                    ranked_jobs.c.status == "completed",
                ),
            )
        )
        eventual_completed = int(
            _scalar(
                connection,
                select(func.count()).select_from(per_task_jobs).where(
                    per_task_jobs.c.has_completed == 1
                ),
            )
        )
        failed_or_cancelled = int(
            _scalar(
                connection,
                select(func.count()).select_from(per_task_jobs).where(
                    per_task_jobs.c.has_failed_or_cancelled == 1
                ),
            )
        )
        recovered = int(
            _scalar(
                connection,
                select(func.count()).select_from(per_task_jobs).where(
                    per_task_jobs.c.has_failed_or_cancelled == 1,
                    per_task_jobs.c.has_completed == 1,
                ),
            )
        )
        avg_time_to_first_result = _scalar(
            connection,
            select(
                func.avg(
                    per_task_jobs.c.first_completed_at
                    - per_task_jobs.c.started_at
                )
            ).where(per_task_jobs.c.first_completed_at.is_not(None)),
            default=None,
        )
        cancelled_jobs = int(
            _scalar(
                connection,
                select(func.count()).select_from(jobs).where(
                    jobs.c.workspace_id == workspace_id,
                    jobs.c.status == "cancelled",
                ),
            )
        )
        failed_jobs = int(
            _scalar(
                connection,
                select(func.count()).select_from(jobs).where(
                    jobs.c.workspace_id == workspace_id,
                    jobs.c.status == "failed",
                ),
            )
        )
        conversations_with_user_turns = int(
            _scalar(
                connection,
                select(func.count()).select_from(user_turns),
            )
        )
        continued_tasks = int(
            _scalar(
                connection,
                select(func.count()).select_from(user_turns).where(
                    user_turns.c.turns >= 2
                ),
            )
        )
        manual_intervention_count = int(
            _scalar(
                connection,
                select(func.count()).select_from(manual_intervention_tasks),
            )
        )

        completed_jobs_for_events = jobs.alias("completed_jobs_for_events")
        evidence_open_count = int(
            _scalar(
                connection,
                select(func.count(distinct(events.c.conversation_id)))
                .select_from(
                    events.join(
                        completed_jobs_for_events,
                        events.c.conversation_id
                        == completed_jobs_for_events.c.conversation_id,
                    )
                )
                .where(
                    events.c.workspace_id == workspace_id,
                    events.c.name == "evidence.open",
                    completed_jobs_for_events.c.workspace_id == workspace_id,
                    completed_jobs_for_events.c.status == "completed",
                ),
            )
        )
        adoption_count = int(
            _scalar(
                connection,
                select(func.count(distinct(events.c.conversation_id)))
                .select_from(
                    events.join(
                        completed_jobs_for_events,
                        events.c.conversation_id
                        == completed_jobs_for_events.c.conversation_id,
                    )
                )
                .where(
                    events.c.workspace_id == workspace_id,
                    events.c.name.in_(("result.copy", "deliverable.copy")),
                    completed_jobs_for_events.c.workspace_id == workspace_id,
                    completed_jobs_for_events.c.status == "completed",
                ),
            )
        )
        feedback_count = int(
            _scalar(
                connection,
                select(func.count()).select_from(feedback).where(
                    feedback.c.workspace_id == workspace_id
                ),
            )
        )
        verified_feedback = int(
            _scalar(
                connection,
                select(func.count()).select_from(feedback).where(
                    feedback.c.workspace_id == workspace_id,
                    feedback.c.human_verified != 0,
                ),
            )
        )

    eventual_rate = _ratio(eventual_completed, task_ids_with_jobs)
    return {
        "task_count": task_count,
        "active_tasks": active_tasks,
        # Legacy alias kept so older frontends continue to render the same number.
        "first_task_completion_rate": eventual_rate,
        "first_attempt_completion_rate": _ratio(
            first_attempt_completed, task_ids_with_jobs
        ),
        "eventual_task_completion_rate": eventual_rate,
        "avg_time_to_first_result_seconds": (
            round(float(avg_time_to_first_result), 2)
            if avg_time_to_first_result is not None
            else None
        ),
        "interruption_rate": _ratio(cancelled_jobs, job_count),
        "failure_rate": _ratio(failed_jobs, job_count),
        "recovery_rate": _ratio(recovered, failed_or_cancelled),
        "continuation_rate": _ratio(
            continued_tasks, conversations_with_user_turns
        ),
        "manual_intervention_rate": _ratio(
            manual_intervention_count, task_ids_with_jobs
        ),
        "evidence_open_rate": _ratio(evidence_open_count, eventual_completed),
        "result_adoption_rate": _ratio(adoption_count, eventual_completed),
        "human_verified_feedback_rate": _ratio(
            verified_feedback, feedback_count
        ),
    }
