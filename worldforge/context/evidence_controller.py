from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .retrieval_sidecar import MultimodalRetrievalResult

_AUDIO_MARKERS = (
    "音频", "声音", "语音", "台词", "说话", "谁说", "说了", "音效", "声效", "音乐", "听到",
    "audio", "sound", "voice", "speech", "music", "said", "speaker",
)
_VISUAL_MARKERS = (
    "视频", "录像", "录屏", "截图", "图片", "画面", "帧", "动画", "图标",
    "video", "image", "frame", "visual", "screen",
)
_TEXT_MARKERS = (
    "日志", "配置", "报错", "堆栈", "json", "csv", "log", "config", "trace", "stack",
)
_CAUSAL_MARKERS = (
    "为什么", "原因", "根因", "导致", "触发", "偶发", "竞态", "race", "root cause",
    "why", "cause", "caused", "trigger", "intermittent",
)
_COMPARE_MARKERS = (
    "对比", "比较", "差异", "前后", "版本间", "回归", "vs", "versus", "compare", "diff",
    "regression", "before", "after",
)
_TIME_RE = re.compile(
    r"(?ix)(?:"
    r"(?<!\d)\d{1,4}:\d{2}(?:\.\d+)?(?!\d)"
    r"|(?<![\w.])\d+(?:\.\d+)?\s*(?:毫秒|ms|秒|分钟|分|"
    r"s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?)(?![A-Za-z])"
    r")"
)
_IDENTIFIER_RE = re.compile(
    r"(?i)(?:\b[a-f0-9]{7,40}\b|\b\d+\.\d+(?:\.\d+){0,3}\b|"
    r"\b[A-Za-z][\w.-]*[_./:-][\w./:-]+\b)"
)


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    value = str(text or "").lower()
    return any(marker in value for marker in markers)


def _asset_kind(asset: dict[str, Any]) -> str:
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
        "application/json", "text/csv", "application/xml"
    }:
        return "text"
    return kind or "file"


@dataclass(frozen=True)
class EvidencePlan:
    needs_visual: bool
    needs_audio: bool
    needs_text: bool
    needs_temporal: bool
    causal: bool
    comparison: bool
    exact_identifier: bool
    semantic_retrieval: bool
    temporal_frame_budget: int
    max_semantic_promotions: int
    deterministic_score: float
    reason: str

    def stats(self) -> dict[str, Any]:
        return {
            "evidence_needs_visual": self.needs_visual,
            "evidence_needs_audio": self.needs_audio,
            "evidence_needs_text": self.needs_text,
            "evidence_needs_temporal": self.needs_temporal,
            "evidence_causal": self.causal,
            "evidence_comparison": self.comparison,
            "evidence_exact_identifier": self.exact_identifier,
            "evidence_semantic_requested": self.semantic_retrieval,
            "evidence_temporal_frame_budget": self.temporal_frame_budget,
            "evidence_max_semantic_promotions": self.max_semantic_promotions,
            "evidence_deterministic_score": round(self.deterministic_score, 4),
            "evidence_plan_reason": self.reason,
        }


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficiency: float
    stop: bool
    stop_reason: str
    strongest_score: float
    localized_hits: int
    semantic_modalities: tuple[str, ...]
    verifier_posture: str

    def stats(self) -> dict[str, Any]:
        return {
            "evidence_sufficiency": round(self.sufficiency, 4),
            "evidence_stop": self.stop,
            "evidence_stop_reason": self.stop_reason,
            "evidence_strongest_semantic_score": round(self.strongest_score, 4),
            "evidence_localized_hits": self.localized_hits,
            "evidence_semantic_modalities": list(self.semantic_modalities),
            "evidence_verifier_posture": self.verifier_posture,
        }

    def render(self, plan: EvidencePlan) -> str:
        requirements: list[str] = []
        if plan.needs_visual:
            requirements.append("视觉/视频原始证据")
        if plan.needs_audio:
            requirements.append("声音/语音原始证据")
        if plan.needs_text:
            requirements.append("日志/配置原文证据")
        if plan.needs_temporal:
            requirements.append("带时间范围的局部证据")
        if plan.causal:
            requirements.append("至少一个可反驳的因果链")
        if plan.comparison:
            requirements.append("同维度对照证据")
        need_text = "、".join(requirements) if requirements else "当前任务直接证据"
        return (
            "【证据控制器；系统派生，不是用户陈述】\n"
            f"需要: {need_text}\n"
            f"充分性={self.sufficiency:.2f}; stop={str(self.stop).lower()}; reason={self.stop_reason}\n"
            f"验证姿态: {self.verifier_posture}\n"
            "规则: 检索向量/摘要只负责定位；最终事实必须回到原始素材或 Frozen Verifier。"
        )


class EvidenceController:
    """Route expensive evidence work by expected information gain.

    The policy is deterministic on purpose: GPU retrieval may improve recall, but it cannot
    become an opaque authority for whether evidence exists. Long video/audio content is a
    special case: even with one source there is a *within-source* ranking problem, so a
    single asset must not be mistaken for a trivial no-retrieval task.
    """

    def plan(
        self,
        query: str,
        assets: list[dict[str, Any]],
        *,
        retriever_enabled: bool,
    ) -> EvidencePlan:
        query_text = str(query or "")
        explicit_audio = _contains_any(query_text, _AUDIO_MARKERS)
        explicit_visual = _contains_any(query_text, _VISUAL_MARKERS)
        needs_text = _contains_any(query_text, _TEXT_MARKERS)
        causal = _contains_any(query_text, _CAUSAL_MARKERS)
        comparison = _contains_any(query_text, _COMPARE_MARKERS)
        needs_temporal = bool(_TIME_RE.search(query_text))
        exact_identifier = bool(_IDENTIFIER_RE.search(query_text))

        kinds = [_asset_kind(asset) for asset in assets]
        visual_count = sum(kind in {"image", "video"} for kind in kinds)
        text_count = sum(kind == "text" for kind in kinds)
        video_assets = [
            asset for asset in assets if _asset_kind(asset) == "video"
        ]
        audio_assets = [
            asset for asset in assets if _asset_kind(asset) == "audio"
        ]
        videos_with_audio = [
            asset
            for asset in video_assets
            if bool((asset.get("meta", {}) or {}).get("has_audio"))
        ]
        audio_count = len(audio_assets) + len(videos_with_audio)
        long_video_present = any(
            float((asset.get("meta", {}) or {}).get("duration") or 0.0) >= 30.0
            for asset in video_assets
        )
        long_audio_present = any(
            float((asset.get("meta", {}) or {}).get("duration") or 0.0) >= 20.0
            for asset in audio_assets
        )

        # A media asset itself is a strong modality prior. Users often ask "为什么没死？"
        # after attaching one replay and never say the word "video". Do not require magic
        # keywords to recognize a within-video/within-audio localization problem.
        needs_audio = explicit_audio or bool(audio_assets)
        needs_visual = explicit_visual or bool(video_assets)

        full_text_hits = 0
        identifier_hits = 0
        metadata_hits = 0
        temporal_anchors = 0
        selected = 0
        for asset in assets:
            context = (asset.get("meta", {}) or {}).get("_context", {}) or {}
            if context.get("selected"):
                selected += 1
            if int(context.get("full_content_hits") or 0) > 0:
                full_text_hits += 1
            reasons = set(context.get("reasons", []) or [])
            identifier_hits += int("identifier-match" in reasons)
            metadata_hits += int("metadata-match" in reasons)
            temporal_anchors += len(context.get("time_hints", []) or [])

        deterministic = 0.12
        deterministic += min(0.28, identifier_hits * 0.14)
        deterministic += min(0.32, full_text_hits * 0.20)
        deterministic += min(0.12, metadata_hits * 0.04)
        deterministic += 0.14 if needs_temporal and temporal_anchors else 0.0
        if len(assets) <= 1 and selected:
            deterministic += 0.10
        deterministic = min(1.0, deterministic)

        timestamp_direct = (
            needs_temporal
            and temporal_anchors > 0
            and not causal
            and not comparison
        )
        visual_ambiguity = needs_visual and (
            visual_count > 1 or bool(video_assets)
        )
        audio_ambiguity = explicit_audio and audio_count > 0
        text_semantic_gap = (
            (needs_text or text_count > 0)
            and text_count > 0
            and full_text_hits == 0
        )
        cross_source_reasoning = (causal or comparison) and len(assets) > 1
        vague_multimodal = (
            not exact_identifier and len(set(kinds) - {"file"}) >= 2
        )
        # Exact build/hash questions are usually metadata/log lookup. Otherwise a long media
        # source contains thousands of candidate moments and benefits from localization even
        # when it is the only attached asset.
        implicit_long_media = (
            not exact_identifier
            and (
                long_video_present
                or long_audio_present
                or (causal and bool(video_assets))
            )
        )

        semantic = bool(
            retriever_enabled
            and not timestamp_direct
            and (
                visual_ambiguity
                or audio_ambiguity
                or text_semantic_gap
                or cross_source_reasoning
                or vague_multimodal
                or implicit_long_media
            )
        )

        if not retriever_enabled:
            reason = "retriever-disabled"
        elif timestamp_direct:
            reason = "direct-temporal-source-evidence"
        elif semantic:
            reason = (
                "within-media-localization"
                if implicit_long_media and len(assets) <= 1
                else "semantic-information-gain"
            )
        elif deterministic >= 0.62:
            reason = "deterministic-evidence-sufficient"
        elif len(assets) <= 1:
            reason = "single-source-no-ranking-value"
        else:
            reason = "low-expected-semantic-gain"

        if causal or comparison:
            frame_budget = 6
        elif needs_temporal:
            frame_budget = 4
        elif needs_visual:
            frame_budget = 3
        else:
            frame_budget = 2
        promotions = 8 if (causal or comparison) else 5 if semantic else 0

        return EvidencePlan(
            needs_visual=needs_visual,
            needs_audio=needs_audio,
            needs_text=needs_text,
            needs_temporal=needs_temporal,
            causal=causal,
            comparison=comparison,
            exact_identifier=exact_identifier,
            semantic_retrieval=semantic,
            temporal_frame_budget=frame_budget,
            max_semantic_promotions=promotions,
            deterministic_score=deterministic,
            reason=reason,
        )

    def assess(
        self,
        plan: EvidencePlan,
        result: MultimodalRetrievalResult,
        assets: list[dict[str, Any]],
    ) -> EvidenceAssessment:
        hits = list(result.hits)
        strongest = max((float(hit.score) for hit in hits), default=0.0)
        localized = sum(
            1
            for hit in hits
            if (
                (
                    hit.start is not None
                    and hit.end is not None
                    and hit.end > hit.start
                )
                or (
                    hit.char_start is not None
                    and hit.char_end is not None
                    and hit.char_end > hit.char_start
                )
                or bool(hit.text_excerpt)
            )
        )
        modalities = tuple(
            sorted({str(hit.modality) for hit in hits if hit.modality})
        )

        semantic_component = 0.0
        if hits:
            semantic_component += min(
                0.38, max(0.0, strongest) * 0.30
            )
            semantic_component += min(0.18, localized * 0.09)
            semantic_component += min(0.10, len(modalities) * 0.05)
        sufficiency = min(
            1.0, plan.deterministic_score + semantic_component
        )

        if not plan.semantic_retrieval:
            stop = (
                plan.deterministic_score >= 0.52
                or len(assets) <= 1
                or plan.needs_temporal
            )
            stop_reason = (
                plan.reason
                if stop
                else "semantic-unavailable-or-not-worth-cost"
            )
        elif hits and localized and sufficiency >= 0.60:
            stop = True
            stop_reason = "localized-semantic-evidence-sufficient"
        elif hits and sufficiency >= 0.68:
            stop = True
            stop_reason = "semantic-evidence-sufficient"
        else:
            stop = True
            stop_reason = "evidence-budget-exhausted"

        if plan.causal:
            posture = (
                "因果结论需至少两类独立证据或执行/Verifier 复现；否则明确保留为假设。"
            )
        elif plan.comparison:
            posture = (
                "比较必须保持 build/branch/config 对齐，并指出缺失的对照维度。"
            )
        elif plan.needs_temporal:
            posture = (
                "时间定位必须引用原始媒体时间段；embedding 区间只作导航。"
            )
        else:
            posture = (
                "优先引用可追溯原始证据；证据不足时不要把相关性写成事实。"
            )

        return EvidenceAssessment(
            sufficiency=sufficiency,
            stop=stop,
            stop_reason=stop_reason,
            strongest_score=strongest,
            localized_hits=localized,
            semantic_modalities=modalities,
            verifier_posture=posture,
        )
