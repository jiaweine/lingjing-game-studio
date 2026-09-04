from __future__ import annotations

from typing import Any

from worldforge.context import ContextCompiler, MultimodalContextCompiler
from worldforge.context.media_derivatives import augment_model_assets, query_needs_audio
from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalClient,
    apply_retrieval_hits,
)
from worldforge.context.temporal_evidence import merge_temporal_evidence

from .analyzer import ProductAnalyzer as BaseProductAnalyzer


class ProductAnalyzer(BaseProductAnalyzer):
    """Product analyzer with bounded long-horizon and multimodal context compilation.

    The original analyzer remains untouched. This adapter compiles the complete durable
    conversation and every task asset into small, provenance-preserving packets before
    delegating to the existing analysis pipeline. Raw messages/assets remain authoritative;
    the compiled state is disposable and fully rebuildable.
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

        packet = self.context_compiler.compile(str(text), raw_history)
        multimodal_packet = self.multimodal_compiler.compile(str(text), raw_assets)

        semantic_result = await self.semantic_retriever.rank(
            str(text), multimodal_packet.assets
        )
        apply_retrieval_hits(multimodal_packet.assets, semantic_result)

        semantic_ranges: dict[str, list[tuple[float, float]]] = {}
        semantic_text_hits = 0
        for hit in semantic_result.hits:
            if hit.text_excerpt:
                semantic_text_hits += 1
            if hit.start is None or hit.end is None or hit.end <= hit.start:
                continue
            semantic_ranges.setdefault(hit.asset_id, []).append((hit.start, hit.end))

        audio_query = query_needs_audio(str(text))
        for asset in multimodal_packet.assets:
            meta = asset.get("meta", {}) or {}
            context = meta.get("_context", {}) or {}
            extra_ranges = semantic_ranges.get(str(asset.get("id", "")), [])[:3]
            range_suffix = " ".join(
                f"{start:.3f}-{end:.3f}秒"
                for start, end in extra_ranges
            )
            context["query_text"] = (
                f"{text} {range_suffix}".strip() if range_suffix else str(text)
            )
            context["semantic_time_ranges"] = [list(item) for item in extra_ranges]
            context["needs_audio"] = audio_query
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

        result = await super().run(
            text=text,
            assets=multimodal_packet.assets,
            provider_key=provider_key,
            sink=sink,
            history=compiled_history,
            human_feedback_gate=human_feedback_gate,
        )

        context = dict(result.get("context") or {})
        context.update(packet.stats())
        context.update(multimodal_packet.stats())
        context.update(semantic_result.stats())
        context["history_messages"] = len(raw_history)
        context["task_assets"] = len(raw_assets)
        context["compiled_history_messages"] = len(compiled_history)
        context["task_state"] = packet.task_state
        context["multimodal_selected_asset_ids"] = [
            str(asset.get("id"))
            for asset in multimodal_packet.assets
            if (asset.get("meta", {}) or {}).get("_context", {}).get("selected")
        ]
        context["semantic_segment_hits"] = sum(len(rows) for rows in semantic_ranges.values())
        context["semantic_text_chunk_hits"] = semantic_text_hits
        result["context"] = context
        return result

    def _model_assets(self, assets):
        """Provider-facing multimodal evidence under strict raw/derived budgets."""
        rows = list(assets or [])
        base = self.multimodal_compiler.model_assets(rows)
        enriched = augment_model_assets(
            rows,
            base,
            raw_media_max_bytes=16 * 1024 * 1024,
            raw_video_budget=2,
            exact_frame_budget=4,
            scene_frame_budget=6,
            audio_budget=3,
        )
        return merge_temporal_evidence(
            rows,
            enriched,
            max_total_frames=6,
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
