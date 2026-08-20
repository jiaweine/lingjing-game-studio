from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["app"]

if TYPE_CHECKING:
    from fastapi import FastAPI

    app: FastAPI


def __getattr__(name: str) -> Any:
    if name != "app":
        raise AttributeError(name)
    from .app import app as fastapi_app

    globals()["app"] = fastapi_app
    return fastapi_app
