from __future__ import annotations

from pathlib import Path
import re
import subprocess

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: str) -> str:
    return _SAFE_RE.sub("-", str(value or "asset"))[:96] or "asset"


def materialize_audio_segment(
    source_path: str,
    *,
    asset_key: str,
    start: float,
    end: float,
) -> str | None:
    """Extract a cached mono 16 kHz window from audio or a video's audio track."""
    source = Path(source_path)
    if not source.is_file() or end <= start:
        return None
    cache = source.parent / ".lingjing-context" / _safe(asset_key) / "lco-audio-segments"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    start_ms = int(round(max(0.0, start) * 1000))
    end_ms = int(round(max(start, end) * 1000))
    dest = cache / f"audio-{start_ms:010d}-{end_ms:010d}.wav"
    if dest.exists() and dest.stat().st_size:
        return str(dest)

    duration = max(0.05, end - start)
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
                "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(dest),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not dest.exists() or not dest.stat().st_size:
        return None
    return str(dest)
