from __future__ import annotations

from typing import Callable

from fastapi import APIRouter

from .control_core import build_control_router as build_core_control_router
from .external_issue_push_api import build_external_issue_push_router
from .github_issue_publisher import GitHubIssuePublisher


github_issue_publisher = GitHubIssuePublisher.from_environment()


def build_control_router(
    *,
    store,
    storage,
    require_principal: Callable,
    session_response: Callable,
    schedule_retry: Callable,
) -> APIRouter:
    """Compose the stable product control plane with optional external-workflow actions."""
    router = build_core_control_router(
        store=store,
        storage=storage,
        require_principal=require_principal,
        session_response=session_response,
        schedule_retry=schedule_retry,
    )
    router.include_router(
        build_external_issue_push_router(
            store=store,
            require_principal=require_principal,
            publisher=github_issue_publisher,
        )
    )
    return router
