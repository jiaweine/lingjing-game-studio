from __future__ import annotations

from collections import Counter
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from .multimodal import (
    MultimodalContextCompiler as _BaseMultimodalContextCompiler,
    MultimodalPacket,
    _asset_kind,
)
from .project_packet import ProjectScopeSnapshot, resolve_project_scope

_SCOPE_FIELDS = ("build_ref", "branch_ref", "commit_ref", "environment_ref")
_scope_context: ContextVar[ProjectScopeSnapshot | None] = ContextVar(
    "lingjing_multimodal_scope", default=None
)


@dataclass(frozen=True)
class ScopedMultimodalPacket(MultimodalPacket):
    scope_filter_active: bool = False
    scope_mismatched_assets: int = 0
    scope_unresolved_conflict: bool = False
    scope: dict[str, Any] | None = None

    def stats(self) -> dict[str, Any]:
        return {
            **super().stats(),
            "multimodal_scope_filter_active": self.scope_filter_active,
            "multimodal_scope_mismatched_assets": self.scope_mismatched_assets,
            "multimodal_scope_unresolved_conflict": self.scope_unresolved_conflict,
            "multimodal_scope": dict(self.scope or {}),
        }


class MultimodalContextCompiler(_BaseMultimodalContextCompiler):
    """Scope-aware model-facing asset compiler while preserving all raw assets.

    Project/build identity is a control-plane constraint, not a relevance feature. When a
    concrete scope is known, assets that explicitly contradict it stay visible in the raw
    manifest but cannot consume retrieval/provider evidence budget. Unscoped assets remain
    eligible because they may contain project-wide documentation. If scope itself is
    unresolved/conflicting, this layer deliberately abstains from filtering instead of
    guessing which build/branch is authoritative.
    """

    def bind_scope(
        self, scope: ProjectScopeSnapshot | dict[str, Any] | None
    ) -> Token[ProjectScopeSnapshot | None]:
        snapshot = (
            scope
            if isinstance(scope, ProjectScopeSnapshot)
            else ProjectScopeSnapshot.from_dict(scope)
            if scope
            else None
        )
        return _scope_context.set(snapshot)

    def reset_scope(self, token: Token[ProjectScopeSnapshot | None]) -> None:
        _scope_context.reset(token)

    @staticmethod
    def _mismatches(
        asset: dict[str, Any], scope: ProjectScopeSnapshot
    ) -> tuple[str, ...]:
        observed = resolve_project_scope([asset])
        mismatches: list[str] = []
        for field in _SCOPE_FIELDS:
            desired = getattr(scope, field)
            if not desired:
                continue
            conflicts = set((observed.conflicts or {}).get(field, ()))
            if conflicts and any(value != desired for value in conflicts):
                mismatches.append(field)
                continue
            value = getattr(observed, field)
            if value and value != desired:
                mismatches.append(field)
        return tuple(mismatches)

    def _wrap(
        self,
        packet: MultimodalPacket,
        *,
        scope: ProjectScopeSnapshot,
        active: bool,
        mismatched: int,
    ) -> ScopedMultimodalPacket:
        return ScopedMultimodalPacket(
            assets=packet.assets,
            manifest=packet.manifest,
            total_assets=packet.total_assets,
            selected_assets=packet.selected_assets,
            selected_by_kind=packet.selected_by_kind,
            text_full_content_hits=packet.text_full_content_hits,
            model_asset_estimate=packet.model_asset_estimate,
            mode="multimodal-scoped-context-compiler-v2",
            scope_filter_active=active,
            scope_mismatched_assets=mismatched,
            scope_unresolved_conflict=scope.unresolved_conflict,
            scope=scope.to_dict(),
        )

    def compile(
        self,
        query: str,
        assets: list[dict[str, Any]] | None,
        *,
        scope: ProjectScopeSnapshot | dict[str, Any] | None = None,
    ) -> MultimodalPacket:
        snapshot = (
            scope
            if isinstance(scope, ProjectScopeSnapshot)
            else ProjectScopeSnapshot.from_dict(scope)
            if scope
            else _scope_context.get()
        )
        if snapshot is None:
            return super().compile(query, assets)

        desired = any(getattr(snapshot, field) for field in _SCOPE_FIELDS)
        if snapshot.unresolved_conflict or not desired:
            return self._wrap(
                super().compile(query, assets),
                scope=snapshot,
                active=False,
                mismatched=0,
            )

        source_rows = list(assets or [])
        eligible_assets: list[dict[str, Any]] = []
        eligible_indices: list[int] = []
        excluded: dict[int, tuple[str, ...]] = {}
        for index, asset in enumerate(source_rows):
            mismatches = self._mismatches(asset, snapshot)
            if mismatches:
                excluded[index] = mismatches
            else:
                eligible_indices.append(index)
                eligible_assets.append(asset)

        eligible_packet = super().compile(query, eligible_assets)
        compiled_by_index = {
            original_index: compiled
            for original_index, compiled in zip(
                eligible_indices, eligible_packet.assets, strict=True
            )
        }
        merged: list[dict[str, Any]] = []
        for index, source in enumerate(source_rows):
            compiled = compiled_by_index.get(index)
            if compiled is not None:
                row = compiled
                meta = dict(row.get("meta", {}) or {})
                context = dict(meta.get("_context", {}) or {})
                context["scope_eligible"] = True
                context["scope_mismatch_fields"] = []
                meta["_context"] = context
                row["meta"] = meta
                merged.append(row)
                continue

            row = dict(source)
            meta = dict(row.get("meta", {}) or {})
            meta["_context"] = {
                "kind": _asset_kind(row),
                "score": 0.0,
                "selected": False,
                "rank": None,
                "reasons": ["scope-mismatch"],
                "excerpt": None,
                "full_content_hits": 0,
                "time_hints": [],
                "scope_eligible": False,
                "scope_mismatch_fields": list(excluded[index]),
            }
            row["meta"] = meta
            merged.append(row)

        selected = [
            asset
            for asset in merged
            if bool((asset.get("meta", {}) or {}).get("_context", {}).get("selected"))
        ]
        counts = Counter(
            str((asset.get("meta", {}) or {}).get("_context", {}).get("kind") or _asset_kind(asset))
            for asset in selected
        )
        packet = ScopedMultimodalPacket(
            assets=merged,
            manifest=self.render_manifest(merged),
            total_assets=len(merged),
            selected_assets=len(selected),
            selected_by_kind=dict(counts),
            text_full_content_hits=eligible_packet.text_full_content_hits,
            model_asset_estimate=len(self.model_assets(merged)),
            mode="multimodal-scoped-context-compiler-v2",
            scope_filter_active=True,
            scope_mismatched_assets=len(excluded),
            scope_unresolved_conflict=False,
            scope=snapshot.to_dict(),
        )
        return packet

    def render_manifest(self, assets: list[dict[str, Any]]) -> str:
        base = super().render_manifest(assets)
        notes: list[str] = []
        for index, asset in enumerate(assets, start=1):
            context = ((asset.get("meta", {}) or {}).get("_context", {}) or {})
            fields = list(context.get("scope_mismatch_fields") or [])
            if fields:
                notes.append(
                    f"A{index} scope mismatch: {', '.join(fields)}（原件保留，本轮不进入深度证据）"
                )
        return base if not notes else base + "\n" + "\n".join(notes)
