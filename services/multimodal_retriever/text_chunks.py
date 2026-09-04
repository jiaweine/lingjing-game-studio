from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib


@dataclass(frozen=True)
class TextChunk:
    start: int
    end: int
    text: str
    fingerprint: str


def _fingerprint(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return f"sha256:{digest}"


def iter_text_chunks(
    path: str | Path,
    *,
    chunk_chars: int = 8000,
    overlap_chars: int = 800,
    max_chunks: int = 4096,
) -> list[TextChunk]:
    """Read a complete text/log/config file into bounded overlapping semantic units.

    The reader is streaming: peak Python text memory is roughly one chunk plus overlap, not
    the entire file. ``start``/``end`` are exact character offsets in the decoded stream and
    are provenance locators only; the raw file remains authoritative.
    """
    source = Path(path)
    if not source.is_file():
        return []
    chunk_chars = max(1000, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    max_chunks = max(1, int(max_chunks))

    rows: list[TextChunk] = []
    buffer = ""
    buffer_start = 0
    eof = False

    with source.open("r", encoding="utf-8", errors="ignore") as handle:
        while len(rows) < max_chunks:
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
                # The semantic text is trimmed for embedding, but provenance points to the
                # complete raw window so a verifier can reopen the exact source range.
                rows.append(
                    TextChunk(
                        start=buffer_start,
                        end=buffer_start + cut,
                        text=stripped,
                        fingerprint=_fingerprint(stripped),
                    )
                )

            if final_chunk:
                break

            advance = max(1, cut - overlap_chars)
            buffer = buffer[advance:]
            buffer_start += advance

    return rows
