from __future__ import annotations

from typing import Any

from worldforge.context import ContextCompiler, MultimodalContextCompiler
from worldforge.context.media_derivatives import augment_model_assets, query_needs_audio

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
        audio_query = query_needs_audio(str(text))
        for asset in multimodal_packet.assets:
            meta = asset.get("meta", {}) or {}
            context = meta.get("_context", {}) or {}
            context["query_text"] = str(text)
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
        # Preserve the public meaning of history_messages/task_assets: durable totals,
        # while exposing the much smaller model-facing selections separately.
        context["history_messages"] = len(raw_history)
        context["task_assets"] = len(raw_assets)
        context["compiled_history_messages"] = len(compiled_history)
        context["task_state"] = packet.task_state
        context["multimodal_selected_asset_ids"] = [
            str(asset.get("id"))
            for asset in multimodal_packet.assets
            if (asset.get("meta", {}) or {}).get("_context", {}).get("selected")
        ]
        result["context"] = context
        return result

    def _model_assets(self, assets):
        """Provider-facing multimodal evidence under strict raw/derived budgets."""
        rows = list(assets or [])
        base = self.multimodal_compiler.model_assets(rows)
        return augment_model_assets(
            rows,
            base,
            raw_media_max_bytes=16 * 1024 * 1024,
            raw_video_budget=2,
            exact_frame_budget=4,
            audio_budget=3,
        )

    def _asset_context(self, assets):
        """Prompt-facing all-asset manifest plus deep excerpts for selected evidence."""
        return self.multimodal_compiler.render_manifest(list(assets or []))
