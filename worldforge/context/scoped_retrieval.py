from __future__ import annotations

from typing import Any

from .retrieval_sidecar import MultimodalRetrievalClient, MultimodalRetrievalResult


def scope_eligible_assets(assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return only assets allowed to participate in the current scoped evidence pass."""
    rows: list[dict[str, Any]] = []
    for asset in list(assets or []):
        context = ((asset.get("meta", {}) or {}).get("_context", {}) or {})
        if context.get("scope_eligible") is False:
            continue
        rows.append(asset)
    return rows


class ScopedMultimodalRetrievalClient(MultimodalRetrievalClient):
    """Sidecar client that cannot send or accept scope-ineligible task assets.

    This is intentionally enforced client-side even if a remote retriever has its own scope
    filters. The sidecar is a derived ranking service, not an authority over project/build
    identity. Filtering before request construction also avoids spending GPU work on evidence
    that the current task is forbidden to consume.
    """

    async def rank(
        self, query: str, assets: list[dict[str, Any]]
    ) -> MultimodalRetrievalResult:
        eligible = scope_eligible_assets(assets)
        return await super().rank(query, eligible)
