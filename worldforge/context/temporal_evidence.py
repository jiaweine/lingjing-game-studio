from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

_RANGE_COLON_RE = re.compile(
    r"(?<!\d)(\d{1,3}):(\d{2})\s*(?:-|~|～|—|–|到|至|to)\s*(\d{1,3}):(\d{2})(?!\d)",
    re.IGNORECASE,
)
_RANGE_SECONDS_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?\s*"
    r"(?:-|~|～|—|–|到|至|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)",
    re.IGNORECASE,
)
_RANGE_MINUTES_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分)\s*"
    r"(?:-|~|～|—|–|到|至|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分)",
    re.IGNORECASE,
)
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def extract_time_ranges(text: str, *, max_ranges: int = 4) -> list[tuple[float, float]]:
    """Parse explicit video/audio intervals while rejecting degenerate ranges.

    Supported examples: ``1:10-1:20``, ``35-45 秒``, ``2min 到 2.5min``.
    Point timestamps are handled by the existing exact-frame path; this helper is only
    for ranges that justify denser evidence sampling.
    """
    value = str(text or "")
    rows: list[tuple[float, float]] = []

    for match in _RANGE_COLON_RE.finditer(value):
        start = float(int(match.group(1)) * 60 + int(match.group(2)))
        end = float(int(match.group(3)) * 60 + int(match.group(4)))
        rows.append((start, end))
    for match in _RANGE_MINUTES_RE.finditer(value):
        rows.append((float(match.group(1)) * 60.0, float(match.group(2)) * 60.0))
    for match in _RANGE_SECONDS_RE.finditer(value):
        rows.append((float(match.group(1)), float(match.group(2))))

    normalized: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for left, right in rows:
        start, end = sorted((max(0.0, left), max(0.0, right)))
        if end - start < 0.25:
            continue
        item = (round(start, 3), round(end, 3))
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
        if len(normalized) >= max_ranges:
            break
    return normalized


def _safe(value: Any) -> str:
    return _SAFE_RE.sub("-", str(value or "asset"))[:80] or "asset"


def _cache_dir(asset: dict[str, Any]) -> Path | None:
    source = Path(str(asset.get("path", "") or ""))
    if not source.is_file():
        return None
    target = source.parent / ".lingjing-context" / _safe(asset.get("id")) / "intervals"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return target


def _frame_at(asset: dict[str, Any], timestamp: float) -> dict[str, Any] | None:
    source = Path(str(asset.get("path", "") or ""))
    cache = _cache_dir(asset)
    if cache is None:
        return None
    duration = float((asset.get("meta", {}) or {}).get("duration", 0) or 0)
    if duration > 0:
        timestamp = min(max(0.0, timestamp), max(0.0, duration - 0.02))
    stamp_ms = int(round(timestamp * 1000))
    dest = cache / f"dense-{stamp_ms:010d}.jpg"
    if not dest.exists() or not dest.stat().st_size:
        try:
            completed = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(source),
                    "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "3",
                    str(dest),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not dest.exists() or not dest.stat().st_size:
            return None
    return {
        "id": f"{asset.get('id', 'video')}:dense-frame:{stamp_ms}",
        "name": f"{asset.get('name', '录像')} · 区间密集帧 @{timestamp:.2f}s",
        "mime": "image/jpeg",
        "path": str(dest),
        "meta": {
            "kind": "image",
            "source_kind": "video",
            "source_asset_id": asset.get("id"),
            "source_name": asset.get("name"),
            "timestamp": round(timestamp, 3),
            "derived": "dense_interval_frame",
        },
    }


def dense_interval_frames(
    assets: list[dict[str, Any]],
    *,
    max_total_frames: int = 6,
    max_frames_per_range: int = 6,
) -> list[dict[str, Any]]:
    """Materialize dense visual evidence only for explicit queried intervals.

    Cost is bounded by ``max_total_frames``. Long ranges are uniformly represented rather
    than decoded exhaustively, while a repeated query reuses the on-disk derivative cache.
    """
    out: list[dict[str, Any]] = []
    for asset in assets:
        meta = asset.get("meta", {}) or {}
        context = meta.get("_context", {}) or {}
        if not context.get("selected"):
            continue
        if str(context.get("kind") or meta.get("kind") or "") != "video":
            continue
        query = str(context.get("query_text", "") or "")
        ranges = extract_time_ranges(query)
        if not ranges:
            continue
        duration = float(meta.get("duration", 0) or 0)
        for start, end in ranges:
            if duration > 0:
                start = min(start, duration)
                end = min(end, duration)
            if end <= start:
                continue
            remaining = max_total_frames - len(out)
            if remaining <= 0:
                return out
            count = min(max_frames_per_range, remaining)
            # About one sample every two seconds for short windows, capped globally.
            count = min(count, max(2, int((end - start) / 2.0) + 1))
            if count == 1:
                timestamps = [(start + end) / 2.0]
            else:
                timestamps = [
                    start + (end - start) * index / (count - 1)
                    for index in range(count)
                ]
            for timestamp in timestamps:
                frame = _frame_at(asset, timestamp)
                if frame:
                    out.append(frame)
                if len(out) >= max_total_frames:
                    return out
    return out


def merge_temporal_evidence(
    assets: list[dict[str, Any]],
    base_assets: list[dict[str, Any]],
    *,
    max_total_frames: int = 6,
    max_images: int = 10,
) -> list[dict[str, Any]]:
    """Insert interval evidence after exact timestamp frames and keep payload bounded."""
    dense = dense_interval_frames(assets, max_total_frames=max_total_frames)
    exact: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for item in base_assets:
        derived = str((item.get("meta", {}) or {}).get("derived", "") or "")
        if derived == "exact_temporal_frame":
            exact.append(item)
        else:
            rest.append(item)

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    image_count = 0
    for item in [*exact, *dense, *rest]:
        key = (str(item.get("mime", "")), str(item.get("path", "")))
        if not key[1] or key in seen:
            continue
        if key[0].startswith("image/"):
            if image_count >= max_images:
                continue
            image_count += 1
        seen.add(key)
        merged.append(item)
    return merged
