from __future__ import annotations

from typing import Callable

from fastapi import APIRouter

from .control_core import build_control_router as build_core_control_router
from .external_issue_push_api import build_external_issue_push_router
from .github_ci_api import build_github_ci_router
from .github_context_api import build_github_context_router
from .github_issue_publisher import GitHubIssuePublisher
from .github_repository_client import GitHubRepositoryClient


github_issue_publisher = GitHubIssuePublisher.from_environment()
github_repository_client = GitHubRepositoryClient.from_environment()


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
    router.include_router(
        build_github_context_router(
            store=store,
            require_principal=require_principal,
            client=github_repository_client,
        )
    )
    router.include_router(
        build_github_ci_router(
            store=store,
            require_principal=require_principal,
            schedule_job=schedule_retry,
        )
    )
    return router
