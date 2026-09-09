from __future__ import annotations

from contextvars import ContextVar

from .registry import ProviderRegistry as _ProviderRegistry


class ProviderRegistry(_ProviderRegistry):
    """Provider registry with task-local record of the final route chosen for a request."""

    def __init__(self) -> None:
        self._request_choice: ContextVar[str | None] = ContextVar(
            f"lingjing_provider_choice_{id(self)}",
            default=None,
        )
        super().__init__()

    def choose(self, preferred: str | None, assets: list[dict]):
        provider = super().choose(preferred, assets)
        key = getattr(getattr(provider, "info", None), "key", None) if provider else None
        self._request_choice.set(str(key) if key else None)
        return provider

    def request_choice(self) -> str | None:
        return self._request_choice.get()
