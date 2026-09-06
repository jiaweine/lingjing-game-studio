from __future__ import annotations

import os
from typing import Any

from worldforge.context import ContextCompiler, MultimodalContextCompiler
from worldforge.context.claim_graph import build_claim_evidence_graph
from worldforge.context.evidence_controller import EvidenceController
from worldforge.context.media_derivatives import augment_model_assets
from worldforge.context.preindex import PreindexScheduler
from worldforge.context.project_packet import ProjectMemoryPacket
from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalClient,
    MultimodalRetrievalResult,
    apply_retrieval_hits,
)
from worldforge.context.temporal_evidence import merge_temporal_evidence
from worldforge.context.verification_contract import build_verification_contract

from .analyzer import ProductAnalyzer as BaseProductAnalyzer


def _kind(asset: dict[str, Any]) -> str:
    meta = asset.get("meta", {}) or {}
    context = meta.get("_context", {}) or {}
    kind = str(context.get("kind") or meta.get("kind") or "")
    if kind and kind != "file":
        return kind
    mime = str(asset.get("mime", "") or "")
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or mime in {
        "application/json",
        "application/xml",
        "text/csv",
    }:
        return "text"
    return kind or "file"


class ProductAnalyzer(BaseProductAnalyzer):
    """Product analyzer with bounded long-horizon and multimodal context compilation.

    Raw messages/assets remain authoritative. ContextOS constrains model-facing context,
    evidence budget, execution scope and confidence semantics. Project long-term memory is
    accepted only as a pre-authorized frozen packet; this layer never queries or mutates the
    memory database. Derived retrieval/index caches are disposable, and synthetic runtime is
    opt-in so it cannot masquerade as real project verification in the default product path.
    """

    def __init__(self, engine, providers):
        super().__init__(engine, providers)
        self.synthetic_review_enabled = os.getenv(
            "LINGJING_PRODUCT_SYNTHETIC_REVIEW", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.context_compiler = ContextCompiler(
            recent_messages=4,
            retrieved_messages=3,
            message_char_budget=9000,
            per_message_chars=2400,
            state_items_per_kind=8,
        )
        self.multimodal_compiler = MultimodalContextCompiler(
            selected_asset_budget=14,
            per_kind_budget={
                "text": 7,
                "image": 6,
                "video": 4,
                "audio": 3,
                "file": 2,
            },
            text_excerpt_chars=5200,
            frames_per_video=3,
            image_budget=9,
            audio_budget=3,
        )
        self.semantic_retriever = MultimodalRetrievalClient()
        self.evidence_controller = EvidenceController()
        self.preindex_scheduler = PreindexScheduler()

    def intent(self, text, assets, history=None):
        """Keep system-derived control context out of user-intent routing."""
        clean_history = [
            message
            for message in (history or [])
            if not str(message.get("id", "") or "").startswith("context:")
            and not bool((message.get("payload", {}) or {}).get("system_derived"))
        ]
        return super().intent(text, assets, clean_history)

    async def run(
        self,
        *,
        text,
        assets,
        provider_key,
        sink,
        history=None,
        human_feedback_gate=False,
        project_memory: ProjectMemoryPacket | dict[str, Any] | None = None,
    ):
        raw_history: list[dict[str, Any]] = list(history or [])
        raw_assets: list[dict[str, Any]] = list(assets or [])
        query = str(text)
        memory_packet = (
            project_memory
            if isinstance(project_memory, ProjectMemoryPacket)
            else ProjectMemoryPacket.from_dict(project_memory)
        )

        packet = self.context_compiler.compile(query, raw_history)
        multimodal_packet = self.multimodal_compiler.compile(query, raw_assets)

        evidence_plan = self.evidence_controller.plan(
            query,
            multimodal_packet.assets,
            retriever_enabled=self.semantic_retriever.enabled,
        )
        if evidence_plan.semantic_retrieval:
            semantic_result = await self.semantic_retriever.rank(
                query, multimodal_packet.assets
            )
            apply_retrieval_hits(
                multimodal_packet.assets,
                semantic_result,
                max_semantic_promotions=evidence_plan.max_semantic_promotions,
            )
        else:
            semantic_result = MultimodalRetrievalResult(
                hits=[], backend=None, latency_ms=0.0, available=False, error=None
            )

        evidence_assessment = self.evidence_controller.assess(
            evidence_plan, semantic_result, multimodal_packet.assets
        )
        verification_contract = build_verification_contract(
            evidence_plan,
            multimodal_packet.assets,
            actual_project_execution_available=False,
        )

        semantic_ranges: dict[str, list[tuple[float, float]]] = {}
        semantic_text_hits = 0
        for hit in semantic_result.hits:
            if hit.text_excerpt:
                semantic_text_hits += 1
            if hit.start is None or hit.end is None or hit.end <= hit.start:
                continue
            semantic_ranges.setdefault(hit.asset_id, []).append((hit.start, hit.end))

        for asset in multimodal_packet.assets:
            meta = asset.get("meta", {}) or {}
            context = meta.get("_context", {}) or {}
            extra_ranges = semantic_ranges.get(str(asset.get("id", "")), [])[:3]
            range_suffix = " ".join(
                f"{start:.3f}-{end:.3f}秒" for start, end in extra_ranges
            )
            context["query_text"] = (
                f"{query} {range_suffix}".strip() if range_suffix else query
            )
            context["semantic_time_ranges"] = [list(item) for item in extra_ranges]
            context["needs_audio"] = evidence_plan.needs_audio
            context["provider_visual_enabled"] = evidence_plan.needs_visual
            context["provider_audio_enabled"] = evidence_plan.needs_audio
            context["evidence_temporal_frame_budget"] = (
                evidence_plan.temporal_frame_budget
            )
            context["evidence_stop_reason"] = evidence_assessment.stop_reason
            meta["_context"] = context
            asset["meta"] = meta

        compiled_history = [dict(message) for message in packet.messages]
        if raw_history:
            compiled_history.append(
                {
                    "id": "context:task-state",
                    "role": "user",
                    "content": (
                        "【系统编译的持久任务状态；不是新的用户消息】\n"
                        + packet.render_task_state()
                    ),
                    "payload": {"system_derived": True, "context_kind": "task_state"},
                }
            )
        if memory_packet is not None:
            compiled_history.append(
                {
                    "id": "context:project-memory",
                    "role": "user",
                    "content": memory_packet.render(),
                    "payload": {
                        "system_derived": True,
                        "context_kind": "project_memory",
                        "project_id": memory_packet.project_id,
                    },
                }
            )
        if raw_assets:
            compiled_history.append(
                {
                    "id": "context:evidence-controller",
                    "role": "user",
                    "content": evidence_assessment.render(evidence_plan),
                    "payload": {"system_derived": True, "context_kind": "evidence_control"},
                }
            )
            compiled_history.append(
                {
                    "id": "context:verification-contract",
                    "role": "user",
                    "content": verification_contract.render(),
                    "payload": {"system_derived": True, "context_kind": "verification"},
                }
            )

        result = await super().run(
            text=text,
            assets=multimodal_packet.assets,
            provider_key=provider_key,
            sink=sink,
            history=compiled_history,
            human_feedback_gate=human_feedback_gate,
        )

        claim_graph = build_claim_evidence_graph(
            answer=result.get("answer"),
            evidence=list(result.get("evidence") or []),
            plan=evidence_plan,
            semantic_result=semantic_result,
            contract=verification_contract,
            runtime_result=result.get("runtime"),
        )
        result["claim_evidence_graph"] = claim_graph.to_dict()

        preindex_scheduled = self.preindex_scheduler.schedule(
            self.semantic_retriever,
            multimodal_packet.assets,
        )

        context = dict(result.get("context") or {})
        heuristic_confidence = float(context.get("evidence_confidence") or 0.0)
        bounded_confidence = round(
            min(heuristic_confidence, verification_contract.confidence_cap), 2
        )
        context["evidence_confidence_heuristic"] = heuristic_confidence
        context["evidence_confidence"] = bounded_confidence
        context["evidence_confidence_basis"] = (
            "heuristic-bounded-by-verification-scope"
        )
        context.update(packet.stats())
        context.update(multimodal_packet.stats())
        context.update(semantic_result.stats())
        context.update(evidence_plan.stats())
        context.update(evidence_assessment.stats())
        context.update(verification_contract.stats())
        context.update(claim_graph.stats())
        context.update(self.preindex_scheduler.stats())
        if memory_packet is not None:
            context.update(memory_packet.stats())
        else:
            context.update(
                {
                    "project_memory_mode": "none",
                    "project_id": None,
                    "project_memory_selected": 0,
                    "project_memory_chars": 0,
                    "project_memory_ids": [],
                    "project_memory_scope": None,
                    "project_memory_scope_conflict": False,
                }
            )
        context["preindex_scheduled_this_run"] = preindex_scheduled
        context["history_messages"] = len(raw_history)
        context["task_assets"] = len(raw_assets)
        context["compiled_history_messages"] = len(compiled_history)
        context["task_state"] = packet.task_state
        context["multimodal_selected_asset_ids"] = [
            str(asset.get("id"))
            for asset in multimodal_packet.assets
            if (asset.get("meta", {}) or {}).get("_context", {}).get("selected")
        ]
        context["semantic_segment_hits"] = sum(
            len(rows) for rows in semantic_ranges.values()
        )
        context["semantic_text_chunk_hits"] = semantic_text_hits
        context["runtime_verification_scope"] = (
            "synthetic-builtin-scenario" if result.get("runtime") else "none"
        )
        context["synthetic_review_opt_in"] = bool(self.synthetic_review_enabled)
        result["context"] = context
        return result

    def _model_assets(self, assets):
        """Spend provider multimodal tokens only for modalities the plan actually needs."""
        rows = list(assets or [])
        contexts = [
            ((asset.get("meta", {}) or {}).get("_context", {}) or {})
            for asset in rows
        ]
        allow_visual = any(
            bool(context.get("provider_visual_enabled")) for context in contexts
        )
        allow_audio = any(
            bool(context.get("provider_audio_enabled")) for context in contexts
        )

        eligible_rows: list[dict[str, Any]] = []
        for asset in rows:
            kind = _kind(asset)
            if kind in {"image", "video"}:
                if allow_visual:
                    eligible_rows.append(asset)
                continue
            if kind == "audio":
                if allow_audio:
                    eligible_rows.append(asset)
                continue
            eligible_rows.append(asset)

        if allow_visual:
            frame_budget = max(
                [
                    int(context.get("evidence_temporal_frame_budget", 0) or 0)
                    for context in contexts
                ]
                or [0]
            )
            frame_budget = max(1, min(6, frame_budget))
        else:
            frame_budget = 0

        base = self.multimodal_compiler.model_assets(eligible_rows)
        enriched = augment_model_assets(
            eligible_rows,
            base,
            raw_media_max_bytes=16 * 1024 * 1024,
            raw_video_budget=1 if (allow_visual and allow_audio) else 0,
            exact_frame_budget=min(4, frame_budget) if allow_visual else 0,
            scene_frame_budget=frame_budget if allow_visual else 0,
            audio_budget=3 if allow_audio else 0,
        )
        return merge_temporal_evidence(
            eligible_rows,
            enriched,
            max_total_frames=frame_budget,
            max_images=10 if allow_visual else 0,
        )

    def _asset_context(self, assets):
        """Deterministic manifest plus optional semantic locators from full raw assets."""
        rows = list(assets or [])
        manifest = self.multimodal_compiler.render_manifest(rows)
        semantic_lines: list[str] = []
        for index, asset in enumerate(rows, start=1):
            context = (asset.get("meta", {}) or {}).get("_context", {}) or {}
            excerpt = str(context.get("semantic_excerpt", "") or "").strip()
            if not excerpt:
                continue
            char_start = context.get("semantic_char_start")
            char_end = context.get("semantic_char_end")
            locator = (
                f" chars={char_start}-{char_end}"
                if char_start is not None and char_end is not None
                else ""
            )
            ref = str(context.get("semantic_evidence_ref", "") or "")
            semantic_lines.append(
                f"A{index} 语义检索定位{locator}"
                + (f" ref={ref}" if ref else "")
                + f":\n{excerpt[:4000]}"
            )
        if not semantic_lines:
            return manifest
        return (
            manifest
            + "\n\n【语义检索定位证据；仅用于定位，结论仍需以原始素材/Verifier 为准】\n"
            + "\n".join(semantic_lines)
        )
