from __future__ import annotations

from worldforge.context.project_packet import ProjectMemoryPacket

from .contextual_analyzer_v3 import ProductAnalyzer as _NativeTokenProductAnalyzer


class ProductAnalyzer(_NativeTokenProductAnalyzer):
    """ContextOS analyzer with task-local multimodal project/build scope isolation."""

    async def run(
        self,
        *,
        text,
        assets,
        provider_key,
        sink,
        history=None,
        human_feedback_gate=False,
        project_memory=None,
    ):
        packet = (
            project_memory
            if isinstance(project_memory, ProjectMemoryPacket)
            else ProjectMemoryPacket.from_dict(project_memory)
        )
        bind = getattr(self.multimodal_compiler, "bind_scope", None)
        reset = getattr(self.multimodal_compiler, "reset_scope", None)
        token = bind(packet.scope if packet is not None else None) if callable(bind) else None
        try:
            return await super().run(
                text=text,
                assets=assets,
                provider_key=provider_key,
                sink=sink,
                history=history,
                human_feedback_gate=human_feedback_gate,
                project_memory=packet,
            )
        finally:
            if token is not None and callable(reset):
                reset(token)
