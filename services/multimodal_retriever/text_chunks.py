from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
from typing import Iterator


@dataclass(frozen=True)
class TextChunk:
    start: int
    end: int
    text: str
    fingerprint: str


def _fingerprint(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return f"sha256:{digest}"


def stream_text_chunks(
    path: str | Path,
    *,
    chunk_chars: int = 8000,
    overlap_chars: int = 800,
    max_chunks: int = 20000,
) -> Iterator[TextChunk]:
    """Stream a complete text/log/config file as bounded overlapping semantic units.

    Peak Python text memory is roughly one chunk plus overlap. ``start``/``end`` are exact
    character offsets in the decoded stream and are provenance locators only; the raw file
    remains authoritative.
    """
    source = Path(path)
    if not source.is_file():
        return
    chunk_chars = max(1000, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    max_chunks = max(1, int(max_chunks))

    buffer = ""
    buffer_start = 0
    eof = False
    emitted = 0

    with source.open("r", encoding="utf-8", errors="ignore") as handle:
        while emitted < max_chunks:
            while len(buffer) < chunk_chars and not eof:
                piece = handle.read(chunk_chars - len(buffer))
                if piece:
                    buffer += piece
                else:
                    eof = True

            if not buffer:
                break

            if eof and len(buffer) <= chunk_chars:
                cut = len(buffer)
                final_chunk = True
            else:
                cut = min(chunk_chars, len(buffer))
                final_chunk = False
                # Prefer a line boundary near the end without dropping the already-read
                # remainder after that newline.
                floor = max(chunk_chars // 2, chunk_chars - 1200)
                newline = buffer.rfind("\n", floor, cut)
                if newline > 0:
                    cut = newline + 1

            raw_text = buffer[:cut]
            stripped = raw_text.strip()
            if stripped:
                emitted += 1
                yield TextChunk(
                    start=buffer_start,
                    end=buffer_start + cut,
                    text=stripped,
                    fingerprint=_fingerprint(stripped),
                )

            if final_chunk:
                break

            advance = max(1, cut - overlap_chars)
            buffer = buffer[advance:]
            buffer_start += advance


def iter_text_chunks(
    path: str | Path,
    *,
    chunk_chars: int = 8000,
    overlap_chars: int = 800,
    max_chunks: int = 20000,
) -> list[TextChunk]:
    """Convenience materialization for tests/small files; workers use stream_text_chunks."""
    return list(
        stream_text_chunks(
            path,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            max_chunks=max_chunks,
        )
    )
