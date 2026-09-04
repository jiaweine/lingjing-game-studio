from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def probe_media(path, mime):
    path = Path(path)
    meta = {"kind": "file", "valid": True}

    if mime.startswith("image/"):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                meta.update({
                    "kind": "image",
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode,
                })
        except Exception:
            meta.update({"kind": "file", "valid": False, "warning": "invalid_image"})
        return meta

    if mime.startswith(("video/", "audio/")):
        try:
            completed = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            data = json.loads(completed.stdout or "{}")
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            has_video = any(stream.get("codec_type") == "video" for stream in streams)
            has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
            if not has_video and not has_audio:
                return {"kind": "file", "valid": False, "warning": "invalid_media"}

            kind = "video" if has_video else "audio"
            meta.update({
                "kind": kind,
                "duration": round(float(fmt.get("duration", 0) or 0), 2),
                "bit_rate": int(float(fmt.get("bit_rate", 0) or 0)),
                "has_audio": has_audio,
            })
            for stream in streams:
                if stream.get("codec_type") == "video":
                    meta.update({
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                        "fps": stream.get("avg_frame_rate"),
                    })
                elif stream.get("codec_type") == "audio":
                    meta.update({
                        "sample_rate": stream.get("sample_rate"),
                        "channels": stream.get("channels"),
                    })
        except Exception:
            meta.update({"kind": "file", "valid": False, "warning": "probe_failed"})
        return meta

    if mime in {"application/json", "text/plain", "text/csv", "application/xml"} or mime.startswith("text/"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            meta.update({
                "kind": "text",
                "chars": len(text),
                "lines": text.count("\n") + 1,
                "preview": text[:4000],
            })
        except Exception:
            meta.update({"kind": "text", "valid": False, "warning": "text_read_failed"})
    return meta


def _frame_signature(path: Path) -> np.ndarray:
    """Tiny grayscale signature for cheap scene-change scoring."""
    with Image.open(path) as image:
        gray = image.convert("L").resize((64, 36))
        return np.asarray(gray, dtype=np.float32) / 255.0


def _uniform_indices(total: int, count: int) -> list[int]:
    if total <= 0 or count <= 0:
        return []
    if count >= total:
        return list(range(total))
    if count == 1:
        return [total // 2]
    return sorted({
        round((total - 1) * index / (count - 1))
        for index in range(count)
    })


def extract_video_keyframes(path, out_dir, count=3):
    """Extract timestamped scene-aware keyframes without adding heavy CV deps.

    This richer extractor returns explicit timestamps and scene scores. Callers should only
    persist its frames when they also persist the returned timestamps. The current product
    upload path still uses `extract_video_frames` below so legacy assets keep exact,
    reconstructable uniform timestamps until the asset schema is upgraded together.
    """
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_dir = out_dir / ".probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    try:
        media = probe_media(path, "video/mp4")
        if media.get("kind") != "video":
            return []
        duration = float(media.get("duration", 0) or 0)
        if duration <= 0:
            return []

        target_count = min(12, max(int(count), 3 + int(duration // 18)))
        probe_count = min(36, max(12, target_count * 3))
        fps = max(0.01, probe_count / duration)
        probe_pattern = probe_dir / "probe_%03d.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-vf", f"fps={fps:.8f},scale=160:-2",
                "-frames:v", str(probe_count), "-q:v", "5", str(probe_pattern),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        probes = sorted(probe_dir.glob("probe_*.jpg"))
        if not probes:
            return []

        signatures = [_frame_signature(frame) for frame in probes]
        transition_scores = [0.0]
        for index in range(1, len(signatures)):
            transition_scores.append(
                float(np.mean(np.abs(signatures[index] - signatures[index - 1])))
            )

        selected = set(_uniform_indices(len(probes), min(3, target_count)))
        ranked_transitions = sorted(
            range(1, len(probes)),
            key=lambda index: transition_scores[index],
            reverse=True,
        )
        for index in ranked_transitions:
            if len(selected) >= target_count:
                break
            if any(abs(index - existing) <= 1 for existing in selected):
                continue
            selected.add(index)
        for index in _uniform_indices(len(probes), target_count):
            if len(selected) >= target_count:
                break
            selected.add(index)

        rows = []
        for output_index, probe_index in enumerate(sorted(selected), start=1):
            timestamp = min(
                duration,
                max(0.0, (probe_index + 0.5) / max(1, len(probes)) * duration),
            )
            dest = out_dir / f"frame_{output_index}.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale='min(960,iw)':-2",
                    "-q:v", "3", str(dest),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if dest.exists() and dest.stat().st_size:
                rows.append({
                    "path": str(dest),
                    "timestamp": round(timestamp, 3),
                    "scene_score": round(transition_scores[probe_index], 6),
                })
        return rows
    except Exception:
        return []
    finally:
        for frame in probe_dir.glob("probe_*.jpg") if probe_dir.exists() else []:
            try:
                frame.unlink()
            except OSError:
                pass
        try:
            probe_dir.rmdir()
        except OSError:
            pass


def extract_video_frames(path, out_dir, count=3):
    """Timestamp-safe uniform upload frames used by the current asset schema."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        media = probe_media(path, "video/mp4")
        if media.get("kind") != "video":
            return []
        duration = float(media.get("duration", 0) or 0)
        if duration <= 0:
            return []
        adaptive_count = min(10, max(int(count), 3 + int(duration // 18)))
        timestamps = [
            duration * (index + 1) / (adaptive_count + 1)
            for index in range(adaptive_count)
        ]
        rows = []
        for index, timestamp in enumerate(timestamps, start=1):
            dest = out_dir / f"frame_{index}.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(path),
                    "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", str(dest),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if dest.exists() and dest.stat().st_size:
                rows.append(str(dest))
        return rows
    except Exception:
        return []
