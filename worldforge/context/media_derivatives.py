from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

_AUDIO_MARKERS = (
    "音频", "声音", "语音", "说话", "台词", "声效", "音效", "音乐", "听到",
    "audio", "sound", "voice", "speech", "music",
)
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def query_needs_audio(text: str) -> bool:
    value = str(text or "").lower()
    return any(marker in value for marker in _AUDIO_MARKERS)


def _safe_id(value: Any) -> str:
    return _SAFE_RE.sub("-", str(value or "asset"))[:80] or "asset"


def _file_size(path: Any) -> int:
    try:
        source = Path(str(path or ""))
        return source.stat().st_size if source.is_file() else 0
    except OSError:
        return 0


def _cache_dir(asset: dict[str, Any]) -> Path | None:
    path = Path(str(asset.get("path", "") or ""))
    if not path.is_file():
        return None
    target = path.parent / ".lingjing-context" / _safe_id(asset.get("id"))
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return target


def _run(command: list[str], timeout: int = 30) -> bool:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _video_frame(asset: dict[str, Any], timestamp: float) -> dict[str, Any] | None:
    source = Path(str(asset.get("path", "") or ""))
    cache = _cache_dir(asset)
    if cache is None:
        return None
    duration = float((asset.get("meta", {}) or {}).get("duration", 0) or 0)
    if duration > 0:
        timestamp = min(max(0.0, timestamp), max(0.0, duration - 0.02))
    stamp_ms = int(round(max(0.0, timestamp) * 1000))
    dest = cache / f"frame-{stamp_ms:010d}.jpg"
    if not dest.exists() or not dest.stat().st_size:
        ok = _run(
            [
                "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(source),
                "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "3",
                str(dest),
            ]
        )
        if not ok or not dest.exists() or not dest.stat().st_size:
            return None
    return {
        "id": f"{asset.get('id', 'video')}:exact-frame:{stamp_ms}",
        "name": f"{asset.get('name', '录像')} · 精确时间帧 @{timestamp:.2f}s",
        "mime": "image/jpeg",
        "path": str(dest),
        "meta": {
            "kind": "image",
            "source_kind": "video",
            "source_asset_id": asset.get("id"),
            "source_name": asset.get("name"),
            "timestamp": round(timestamp, 3),
            "derived": "exact_temporal_frame",
        },
    }


def _audio_clip(
    asset: dict[str, Any],
    center: float,
    *,
    seconds: float = 12.0,
) -> dict[str, Any] | None:
    source = Path(str(asset.get("path", "") or ""))
    cache = _cache_dir(asset)
    if cache is None:
        return None
    duration = float((asset.get("meta", {}) or {}).get("duration", 0) or 0)
    half = max(2.0, seconds / 2.0)
    start = max(0.0, center - half)
    if duration > 0:
        start = min(start, max(0.0, duration - seconds))
        clip_seconds = min(seconds, max(0.25, duration - start))
    else:
        clip_seconds = seconds
    stamp_ms = int(round(start * 1000))
    dest = cache / f"audio-{stamp_ms:010d}-{int(clip_seconds * 1000):06d}.wav"
    if not dest.exists() or not dest.stat().st_size:
        ok = _run(
            [
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{clip_seconds:.3f}",
                "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(dest),
            ],
            timeout=40,
        )
        if not ok or not dest.exists() or not dest.stat().st_size:
            return None
    return {
        "id": f"{asset.get('id', 'media')}:audio:{stamp_ms}",
        "name": (
            f"{asset.get('name', '素材')} · 音频片段 "
            f"{start:.1f}s-{start + clip_seconds:.1f}s"
        ),
        "mime": "audio/wav",
        "path": str(dest),
        "meta": {
            "kind": "audio",
            "source_asset_id": asset.get("id"),
            "source_name": asset.get("name"),
            "source_kind": (asset.get("meta", {}) or {}).get("kind"),
            "start": round(start, 3),
            "end": round(start + clip_seconds, 3),
            "derived": "temporal_audio_window",
        },
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("mime", "")), str(row.get("path", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def augment_model_assets(
    assets: list[dict[str, Any]],
    base_assets: list[dict[str, Any]],
    *,
    raw_media_max_bytes: int = 16 * 1024 * 1024,
    raw_video_budget: int = 2,
    exact_frame_budget: int = 4,
    audio_budget: int = 3,
) -> list[dict[str, Any]]:
    """Add raw/temporal evidence without letting large media dominate inference.

    `base_assets` contains the context compiler's selected images/keyframes/audio. We add
    raw short videos for providers that can consume them, exact frames for timestamped
    questions, and short 16 kHz audio windows for oversized audio or audio questions over
    video. The raw originals are never modified; all derivatives are disposable caches.
    """
    selected = [
        asset for asset in assets
        if (asset.get("meta", {}) or {}).get("_context", {}).get("selected")
    ]
    selected.sort(
        key=lambda asset: (asset.get("meta", {}) or {}).get("_context", {}).get("rank") or 999
    )
    preferred: list[dict[str, Any]] = []
    raw_videos = 0
    exact_frames = 0
    audio_count = 0

    for asset in selected:
        meta = asset.get("meta", {}) or {}
        context = meta.get("_context", {}) or {}
        kind = str(context.get("kind") or meta.get("kind") or "")
        time_hints = [float(value) for value in context.get("time_hints", [])[:4]]
        needs_audio = bool(context.get("needs_audio"))
        duration = float(meta.get("duration", 0) or 0)

        if kind == "video":
            # Exact temporal anchors have the highest information density for questions
            # such as "37 秒发生了什么" and avoid trusting approximate upload keyframes.
            for hint in time_hints:
                if exact_frames >= exact_frame_budget:
                    break
                frame = _video_frame(asset, hint)
                if frame:
                    preferred.append(frame)
                    exact_frames += 1

            size = _file_size(asset.get("path"))
            if 0 < size <= raw_media_max_bytes and raw_videos < raw_video_budget:
                preferred.append(asset)
                raw_videos += 1

            if needs_audio and meta.get("has_audio") and audio_count < audio_budget:
                centers = time_hints or ([duration / 2.0] if duration > 0 else [0.0])
                for center in centers:
                    if audio_count >= audio_budget:
                        break
                    clip = _audio_clip(asset, center)
                    if clip:
                        preferred.append(clip)
                        audio_count += 1

        elif kind == "audio":
            size = _file_size(asset.get("path"))
            if 0 < size <= raw_media_max_bytes and audio_count < audio_budget:
                preferred.append(asset)
                audio_count += 1
            else:
                centers = time_hints or ([duration / 2.0] if duration > 0 else [0.0])
                for center in centers:
                    if audio_count >= audio_budget:
                        break
                    clip = _audio_clip(asset, center)
                    if clip:
                        preferred.append(clip)
                        audio_count += 1

    # Base visual evidence fills in broad coverage after high-density exact/raw evidence.
    # Remove oversized raw audio from the base pack because compatible providers may route
    # to it but later refuse/skip the payload.
    safe_base: list[dict[str, Any]] = []
    for asset in base_assets:
        mime = str(asset.get("mime", ""))
        if mime.startswith("audio/") and _file_size(asset.get("path")) > raw_media_max_bytes:
            continue
        safe_base.append(asset)
    return _dedupe([*preferred, *safe_base])
