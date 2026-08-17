from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def extract_video_frames(path, out_dir, count=3):
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

        # Long clips need more than three frames to avoid blind temporal gaps.
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
                    "ffmpeg",
                    "-y",
                    "-ss", str(timestamp),
                    "-i", str(path),
                    "-frames:v", "1",
                    "-vf", "scale='min(960,iw)':-2",
                    str(dest),
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
