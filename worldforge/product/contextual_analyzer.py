from __future__ import annotations

from typing import Any

from worldforge.context import ContextCompiler, MultimodalContextCompiler
from worldforge.context.evidence_controller import EvidenceController
from worldforge.context.media_derivatives import augment_model_assets, query_needs_audio
from worldforge.context.preindex import PreindexScheduler
from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalClient,
    MultimodalRetrievalResult,
    apply_retrieval_hits,
)
from worldforge.context.temporal_evidence import merge_temporal_evidence
from worldforge.context.verification_contract import build_verification_contract

from .analyzer import ProductAnalyzer as BaseProductAnalyzer


class ProductAnalyzer(BaseProductAnalyzer):
    """Product analyzer with bounded long-horizon and multimodal context compilation.

    Raw messages/assets remain authoritative. The original analyzer is not modified; this
    wrapper constrains its model-facing context, evidence budget and confidence semantics.
    Derived retrieval/index caches are disposable and synthetic runtime runs are explicitly
    prevented from masquerading as real project verification.
    """

    def __init__(self, engine, providers):
        super().__init__(engine, providers)
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

    async def run(
        self,
        *,
        text,
        assets,
        provider_key,
        sink,
        history=None,
        human_feedback_gate=False,
    ):
        raw_history: list[dict[str, Any]] = list(history or [])
        raw_assets: list[dict[str, Any]] = list(assets or [])
        query = str(text)

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
            # The current product analyzer still uses built-in synthetic scenarios. A future
            # real GameAdapter/EnvironmentAdapter can explicitly flip this to True only after
            # actual project execution is connected and verified.
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

        audio_query = query_needs_audio(query)
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
            context["needs_audio"] = audio_query
            context["evidence_temporal_frame_budget"] = evidence_plan.temporal_frame_budget
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
                }
            )
        if raw_assets:
            compiled_history.append(
                {
                    "id": "context:evidence-controller",
                    "role": "user",
                    "content": evidence_assessment.render(evidence_plan),
                }
            )
            compiled_history.append(
                {
                    "id": "context:verification-contract",
                    "role": "user",
                    "content": verification_contract.render(),
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
        context.update(self.preindex_scheduler.stats())
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
        result["context"] = context
        return result

    def _model_assets(self, assets):
        """Provider-facing multimodal evidence under a query-adaptive raw-media budget."""
        rows = list(assets or [])
        frame_budget = max(
            [
                int(
                    ((asset.get("meta", {}) or {}).get("_context", {}) or {}).get(
                        "evidence_temporal_frame_budget", 2
                    )
                    or 2
                )
                for asset in rows
            ]
            or [2]
        )
        frame_budget = max(2, min(6, frame_budget))

        base = self.multimodal_compiler.model_assets(rows)
        enriched = augment_model_assets(
            rows,
            base,
            raw_media_max_bytes=16 * 1024 * 1024,
            raw_video_budget=2,
            exact_frame_budget=min(4, frame_budget),
            scene_frame_budget=frame_budget,
            audio_budget=3,
        )
        return merge_temporal_evidence(
            rows,
            enriched,
            max_total_frames=frame_budget,
            max_images=10,
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
