from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import mmap
from pathlib import Path
import re
from typing import Any

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+#@-]{2,}")
_TIME_COLON_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_TIME_SECOND_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)")
_TIME_MINUTE_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分)")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _tokens(text: str) -> set[str]:
    value = _normalize(text)
    out = {token.lower() for token in _TOKEN_RE.findall(value)}
    for run in _CJK_RE.findall(value):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[index:index + 2] for index in range(len(run) - 1))
            if len(run) <= 8:
                out.add(run)
    return out


def _query_terms(text: str) -> list[str]:
    tokens = sorted(_tokens(text), key=lambda item: (-len(item), item))
    # Keep the search set intentionally small. mmap.find is fast, but a long natural
    # language query should not multiply disk scans needlessly.
    return [token for token in tokens if len(token) >= 2][:12]


def _time_hints(text: str) -> list[float]:
    hints: list[float] = []
    occupied: set[str] = set()
    for match in _TIME_COLON_RE.finditer(text):
        hints.append(float(int(match.group(1)) * 60 + int(match.group(2))))
        occupied.add(match.group(0))
    for match in _TIME_SECOND_RE.finditer(text):
        if match.group(0) not in occupied:
            hints.append(float(match.group(1)))
    # Minute-only expressions are useful when there is no mm:ss expression.
    for match in _TIME_MINUTE_RE.finditer(text):
        if match.group(0) not in occupied:
            hints.append(float(match.group(1)) * 60.0)
    return sorted({round(value, 3) for value in hints if value >= 0})[:6]


def _asset_kind(asset: dict[str, Any]) -> str:
    meta = asset.get("meta", {}) or {}
    kind = str(meta.get("kind", "") or "")
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
        "application/json", "application/xml", "text/csv", "text/plain"
    }:
        return "text"
    return kind or "file"


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remain = seconds - minutes * 60
    if minutes:
        return f"{minutes}:{remain:04.1f}"
    return f"{remain:.1f}s"


@dataclass(frozen=True)
class MultimodalPacket:
    assets: list[dict[str, Any]]
    manifest: str
    total_assets: int
    selected_assets: int
    selected_by_kind: dict[str, int]
    text_full_content_hits: int
    model_asset_estimate: int
    mode: str = "multimodal-context-compiler-v1"

    def stats(self) -> dict[str, Any]:
        return {
            "multimodal_mode": self.mode,
            "multimodal_assets": self.total_assets,
            "multimodal_selected_assets": self.selected_assets,
            "multimodal_selected_by_kind": dict(self.selected_by_kind),
            "multimodal_text_full_content_hits": self.text_full_content_hits,
            "multimodal_model_asset_estimate": self.model_asset_estimate,
        }


class MultimodalContextCompiler:
    """Compile every task asset into a bounded, provenance-preserving context packet.

    Raw assets remain authoritative and immutable. This compiler only annotates copies.
    Every asset is represented in the compact manifest; text/log files are searched over
    their complete local bytes (not only the 4k upload preview), while model-facing
    image/audio/video evidence is aggressively budgeted. Video keyframe timestamps are
    reconstructed from the extractor's uniform sampling schedule, making temporal queries
    such as "37 秒" resolve to the nearest available frame without changing storage schema.
    """

    def __init__(
        self,
        *,
        selected_asset_budget: int = 14,
        per_kind_budget: dict[str, int] | None = None,
        text_excerpt_chars: int = 5200,
        frames_per_video: int = 3,
        image_budget: int = 9,
        audio_budget: int = 3,
    ) -> None:
        self.selected_asset_budget = max(4, int(selected_asset_budget))
        self.per_kind_budget = dict(
            per_kind_budget
            or {"text": 7, "image": 6, "video": 4, "audio": 3, "file": 2}
        )
        self.text_excerpt_chars = max(1200, int(text_excerpt_chars))
        self.frames_per_video = max(1, int(frames_per_video))
        self.image_budget = max(1, int(image_budget))
        self.audio_budget = max(1, int(audio_budget))

    def compile(
        self,
        query: str,
        assets: list[dict[str, Any]] | None,
    ) -> MultimodalPacket:
        rows = [dict(asset) for asset in (assets or [])]
        if not rows:
            return MultimodalPacket([], "没有任务素材", 0, 0, {}, 0, 0)

        query_tokens = _tokens(query)
        query_terms = _query_terms(query)
        time_hints = _time_hints(query)
        scored: list[tuple[float, int, str, str | None, int]] = []
        text_hits = 0

        for index, asset in enumerate(rows):
            meta = dict(asset.get("meta", {}) or {})
            asset["meta"] = meta
            kind = _asset_kind(asset)
            searchable = " ".join(
                [
                    str(asset.get("name", "")),
                    str(asset.get("mime", "")),
                    str(meta.get("preview", ""))[:4000],
                    str(meta.get("warning", "")),
                ]
            )
            doc_tokens = _tokens(searchable)
            overlap = len(query_tokens & doc_tokens) / max(1, len(query_tokens))
            score = overlap * 5.0
            reasons: list[str] = []
            if overlap:
                reasons.append("metadata-match")

            name_lower = str(asset.get("name", "")).lower()
            exact_ids = [
                token for token in query_tokens
                if any(ch.isdigit() for ch in token) or "_" in token or "." in token
            ]
            identifier_hits = sum(1 for token in exact_ids if token in name_lower)
            if identifier_hits:
                score += 3.5 * identifier_hits
                reasons.append("identifier-match")

            excerpt = None
            full_hits = 0
            if kind == "text":
                excerpt, full_hits = self._text_excerpt(asset, query_terms)
                if full_hits:
                    score += min(8.0, 2.6 + full_hits * 1.1)
                    reasons.append("full-content-match")
                    text_hits += 1
                elif excerpt:
                    score += 0.2

            query_lower = query.lower()
            kind_markers = {
                "video": ("视频", "录像", "录屏", "video", "replay"),
                "image": ("图片", "截图", "画面", "image", "frame"),
                "audio": ("音频", "声音", "语音", "audio", "sound"),
                "text": ("日志", "配置", "json", "csv", "log", "config"),
            }
            if any(marker in query_lower for marker in kind_markers.get(kind, ())):
                score += 1.5
                reasons.append("modality-match")
            if kind == "video" and time_hints:
                score += 2.0
                reasons.append("temporal-query")

            # Assets are immutable task evidence. A small recency term breaks ties without
            # overwhelming lexical/full-content relevance.
            score += 0.25 * (index + 1) / max(1, len(rows))
            context = {
                "kind": kind,
                "score": round(score, 5),
                "selected": False,
                "rank": None,
                "reasons": reasons,
                "excerpt": excerpt,
                "full_content_hits": full_hits,
                "time_hints": time_hints if kind == "video" else [],
            }
            meta["_context"] = context
            scored.append((score, index, kind, excerpt, full_hits))

        selected: list[int] = []
        kind_counts: Counter[str] = Counter()

        # First preserve modality coverage: if a modality exists, its best candidate gets
        # one slot. This prevents a large log collection from starving video/audio/image.
        by_kind: dict[str, list[tuple[float, int]]] = {}
        for score, index, kind, _excerpt, _hits in scored:
            by_kind.setdefault(kind, []).append((score, index))
        for kind, candidates in sorted(by_kind.items()):
            candidates.sort(reverse=True)
            if candidates and len(selected) < self.selected_asset_budget:
                selected.append(candidates[0][1])
                kind_counts[kind] += 1

        for score, index, kind, _excerpt, _hits in sorted(scored, reverse=True):
            if index in selected or len(selected) >= self.selected_asset_budget:
                continue
            if kind_counts[kind] >= self.per_kind_budget.get(kind, 2):
                continue
            # Even a weakly matching asset may matter in a multimodal R&D task; keep a
            # bounded tail rather than imposing a hard relevance threshold.
            selected.append(index)
            kind_counts[kind] += 1

        selected_set = set(selected)
        ranked_selected = sorted(selected, key=lambda idx: rows[idx]["meta"]["_context"]["score"], reverse=True)
        for rank, index in enumerate(ranked_selected, start=1):
            context = rows[index]["meta"]["_context"]
            context["selected"] = True
            context["rank"] = rank
            if context["kind"] == "video":
                context["keyframe_indices"] = self._video_frame_indices(
                    rows[index], time_hints
                )
                context["keyframe_times"] = self._video_frame_times(rows[index])

        manifest = self.render_manifest(rows)
        model_estimate = len(self.model_assets(rows))
        return MultimodalPacket(
            assets=rows,
            manifest=manifest,
            total_assets=len(rows),
            selected_assets=len(selected_set),
            selected_by_kind=dict(kind_counts),
            text_full_content_hits=text_hits,
            model_asset_estimate=model_estimate,
        )

    def model_assets(self, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = [
            asset for asset in assets
            if (asset.get("meta", {}) or {}).get("_context", {}).get("selected")
        ]
        selected.sort(
            key=lambda asset: (asset.get("meta", {}) or {}).get("_context", {}).get("rank") or 999
        )
        out: list[dict[str, Any]] = []
        images = 0
        audio = 0
        for asset in selected:
            mime = str(asset.get("mime", ""))
            meta = asset.get("meta", {}) or {}
            context = meta.get("_context", {}) or {}
            kind = context.get("kind") or _asset_kind(asset)
            if kind == "image" and mime.startswith("image/"):
                if images < self.image_budget:
                    out.append(asset)
                    images += 1
                continue
            if kind == "audio" and mime.startswith("audio/"):
                if audio < self.audio_budget:
                    out.append(asset)
                    audio += 1
                continue
            if kind != "video":
                continue
            frames = list(meta.get("keyframes", []) or [])
            frame_times = self._video_frame_times(asset)
            indices = list(context.get("keyframe_indices", []) or [])
            if not indices:
                indices = self._video_frame_indices(asset, [])
            for frame_index in indices:
                if images >= self.image_budget or not (0 <= frame_index < len(frames)):
                    break
                timestamp = frame_times[frame_index] if frame_index < len(frame_times) else None
                out.append(
                    {
                        "id": f"{asset.get('id', 'video')}:frame:{frame_index}",
                        "name": (
                            f"{asset.get('name', '录像')} · 关键帧"
                            + (f" @{_fmt_time(timestamp)}" if timestamp is not None else f" {frame_index + 1}")
                        ),
                        "mime": "image/jpeg",
                        "path": frames[frame_index],
                        "meta": {
                            "kind": "image",
                            "source_asset_id": asset.get("id"),
                            "source_kind": "video",
                            "source_name": asset.get("name"),
                            "frame_index": frame_index,
                            "timestamp": timestamp,
                        },
                    }
                )
                images += 1
        return out

    def render_manifest(self, assets: list[dict[str, Any]]) -> str:
        if not assets:
            return "没有任务素材"
        lines = [
            "全量素材索引（所有素材均保留可追溯原件；✓ 表示本轮进入深度上下文）："
        ]
        for index, asset in enumerate(assets, start=1):
            meta = asset.get("meta", {}) or {}
            context = meta.get("_context", {}) or {}
            kind = context.get("kind") or _asset_kind(asset)
            selected = "✓" if context.get("selected") else "·"
            details: list[str] = []
            if meta.get("duration"):
                details.append(f"duration={meta['duration']}s")
            if meta.get("width") and meta.get("height"):
                details.append(f"{meta['width']}x{meta['height']}")
            if meta.get("chars"):
                details.append(f"chars={meta['chars']}")
            if meta.get("lines"):
                details.append(f"lines={meta['lines']}")
            if meta.get("keyframes"):
                details.append(f"keyframes={len(meta.get('keyframes') or [])}")
            suffix = f" | {' | '.join(details)}" if details else ""
            lines.append(
                f"A{index} {selected} | {kind} | {asset.get('name', '未命名素材')}{suffix}"
            )
            excerpt = str(context.get("excerpt", "") or "").strip()
            if context.get("selected") and excerpt:
                lines.append(f"A{index} 命中内容片段:\n{excerpt[:self.text_excerpt_chars]}")
            if kind == "video" and context.get("selected"):
                times = self._video_frame_times(asset)
                chosen = context.get("keyframe_indices", []) or []
                if chosen:
                    anchors = [
                        _fmt_time(times[item])
                        for item in chosen
                        if 0 <= item < len(times)
                    ]
                    if anchors:
                        lines.append(f"A{index} 本轮视觉时间锚点: {', '.join(anchors)}")
        return "\n".join(lines)

    def _text_excerpt(
        self,
        asset: dict[str, Any],
        query_terms: list[str],
    ) -> tuple[str | None, int]:
        path_value = asset.get("path")
        if not path_value:
            preview = str((asset.get("meta", {}) or {}).get("preview", "") or "")
            return (preview[: self.text_excerpt_chars] or None, 0)
        path = Path(str(path_value))
        if not path.is_file():
            preview = str((asset.get("meta", {}) or {}).get("preview", "") or "")
            return (preview[: self.text_excerpt_chars] or None, 0)
        try:
            size = path.stat().st_size
            if size <= 0:
                return None, 0
            with path.open("rb") as handle:
                with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    hits: list[int] = []
                    for term in query_terms:
                        needle = term.encode("utf-8", errors="ignore")
                        if len(needle) < 2:
                            continue
                        start = 0
                        local_hits = 0
                        while local_hits < 3:
                            found = data.find(needle, start)
                            if found < 0:
                                break
                            hits.append(found)
                            local_hits += 1
                            start = found + max(1, len(needle))
                    if hits:
                        center = min(hits)
                        radius = self.text_excerpt_chars // 2
                        start = max(0, center - radius)
                        end = min(size, center + radius)
                        snippet = bytes(data[start:end]).decode("utf-8", errors="ignore")
                        return snippet.strip(), len(hits)
                    # No direct query hit: preserve both beginning and end. This is much
                    # stronger for logs than the previous head-only 4k preview.
                    half = self.text_excerpt_chars // 2
                    if size <= self.text_excerpt_chars:
                        sample = bytes(data[:]).decode("utf-8", errors="ignore")
                    else:
                        sample = (
                            bytes(data[:half]).decode("utf-8", errors="ignore")
                            + "\n… [中间内容按需检索] …\n"
                            + bytes(data[-half:]).decode("utf-8", errors="ignore")
                        )
                    return sample.strip(), 0
        except (OSError, ValueError):
            preview = str((asset.get("meta", {}) or {}).get("preview", "") or "")
            return (preview[: self.text_excerpt_chars] or None, 0)

    def _video_frame_times(self, asset: dict[str, Any]) -> list[float]:
        meta = asset.get("meta", {}) or {}
        frames = list(meta.get("keyframes", []) or [])
        duration = float(meta.get("duration", 0) or 0)
        if not frames or duration <= 0:
            return [float(index) for index in range(len(frames))]
        count = len(frames)
        return [
            round(duration * (index + 1) / (count + 1), 3)
            for index in range(count)
        ]

    def _video_frame_indices(
        self,
        asset: dict[str, Any],
        time_hints: list[float],
    ) -> list[int]:
        meta = asset.get("meta", {}) or {}
        frames = list(meta.get("keyframes", []) or [])
        if not frames:
            return []
        times = self._video_frame_times(asset)
        chosen: list[int] = []
        for hint in time_hints:
            nearest = min(range(len(times)), key=lambda index: abs(times[index] - hint))
            if nearest not in chosen:
                chosen.append(nearest)
            if len(chosen) >= self.frames_per_video:
                return chosen
        if self.frames_per_video == 1:
            middle = len(frames) // 2
            return chosen or [middle]
        slots = min(self.frames_per_video, len(frames))
        for position in range(slots):
            index = round((len(frames) - 1) * position / max(1, slots - 1))
            if index not in chosen:
                chosen.append(index)
            if len(chosen) >= self.frames_per_video:
                break
        return chosen
