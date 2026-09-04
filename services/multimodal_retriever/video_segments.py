from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Iterable

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def segment_windows(
    start: float,
    end: float,
    span: float,
    *,
    max_windows: int | None = None,
) -> list[tuple[float, float]]:
    start = max(0.0, float(start))
    end = max(start, float(end))
    span = max(1.0, float(span))
    if end <= start:
        return []
    if max_windows is not None and max_windows > 0:
        span = max(span, (end - start) / max_windows)
    rows: list[tuple[float, float]] = []
    cursor = start
    while cursor < end - 1e-6:
        right = min(end, cursor + span)
        rows.append((round(cursor, 3), round(right, 3)))
        cursor = right
    return rows


def merge_windows(windows: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for start, end in windows:
        item = (round(float(start), 3), round(float(end), 3))
        if item[1] <= item[0] or item in seen:
            continue
        seen.add(item)
        out.append(item)
    out.sort()
    return out


def _safe(value: str) -> str:
    return _SAFE_RE.sub("-", str(value or "asset"))[:96] or "asset"


def materialize_video_segment(
    source_path: str,
    *,
    asset_key: str,
    start: float,
    end: float,
) -> str | None:
    """Create a disposable video-only segment, preferring stream copy for low CPU cost."""
    source = Path(source_path)
    if not source.is_file() or end <= start:
        return None
    cache = source.parent / ".lingjing-context" / _safe(asset_key) / "wemm-segments"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    start_ms = int(round(max(0.0, start) * 1000))
    end_ms = int(round(max(start, end) * 1000))
    dest = cache / f"segment-{start_ms:010d}-{end_ms:010d}.mp4"
    if dest.exists() and dest.stat().st_size:
        return str(dest)

    duration = max(0.05, end - start)
    copy_command = [
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-map", "0:v:0", "-an", "-c:v", "copy",
        "-movflags", "+faststart", str(dest),
    ]
    try:
        completed = subprocess.run(
            copy_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=35,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0 and dest.exists() and dest.stat().st_size:
        return str(dest)

    # Keyframe alignment/codec quirks can make stream-copy segments unusable. The fallback
    # re-encodes only the requested segment at bounded resolution; it is slower but cached.
    try:
        if dest.exists():
            dest.unlink()
        completed = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
                "-t", f"{duration:.3f}", "-map", "0:v:0", "-an",
                "-vf", "scale='min(640,iw)':-2", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "28", "-movflags", "+faststart",
                str(dest),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not dest.exists() or not dest.stat().st_size:
        return None
    return str(dest)
