from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ProviderInfo:
    key: str
    name: str
    vendor: str
    model: str | None
    configured: bool
    multimodal: bool
    supports_video: bool = False
    supports_audio: bool = False
    note: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    """Provider interface with task-local request telemetry.

    Provider instances are shared across concurrent jobs. Request telemetry therefore lives in
    a ContextVar rather than mutable ``last_request`` state, so token preflight/counting metrics
    cannot leak between asyncio tasks.
    """

    info: ProviderInfo

    def _telemetry_var(self) -> ContextVar[dict[str, Any]]:
        var = getattr(self, "_request_telemetry_var", None)
        if var is None:
            var = ContextVar(
                f"lingjing_provider_telemetry_{id(self)}",
                default={},
            )
            self._request_telemetry_var = var
        return var

    def reset_request_telemetry(self) -> None:
        self._telemetry_var().set({})

    def update_request_telemetry(self, **items: Any) -> None:
        telemetry = dict(self._telemetry_var().get())
        telemetry.update(items)
        self._telemetry_var().set(telemetry)

    def request_telemetry(self) -> dict[str, Any]:
        return dict(self._telemetry_var().get())

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        assets: list[dict[str, Any]] | None = None,
        temperature: float = .2,
        max_tokens: int = 1400,
    ) -> str:
        raise NotImplementedError
