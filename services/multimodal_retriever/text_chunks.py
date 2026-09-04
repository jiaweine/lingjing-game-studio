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
    the entire file. Offsets are character offsets in the UTF-8-decoded stream and are used
    only as provenance locators; the original file remains authoritative.
    """
    source = Path(path)
    if not source.is_file():
        return []
    chunk_chars = max(1000, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    max_chunks = max(1, int(max_chunks))

    rows: list[TextChunk] = []
    carry = ""
    consumed = 0
    with source.open("r", encoding="utf-8", errors="ignore") as handle:
        while len(rows) < max_chunks:
            need = max(1, chunk_chars - len(carry))
            piece = handle.read(need)
            if not piece and not carry:
                break
            block = carry + piece
            if not block:
                break

            # Prefer a natural line boundary near the end of a full block. This improves
            # log/config semantic coherence without allowing pathological long lines to
            # create unbounded chunks.
            cut = len(block)
            if piece and len(block) >= chunk_chars:
                floor = max(chunk_chars // 2, chunk_chars - 1200)
                newline = block.rfind("\n", floor)
                if newline > 0:
                    cut = newline + 1
            text = block[:cut]
            start = max(0, consumed - len(carry))
            end = start + len(text)
            stripped = text.strip()
            if stripped:
                rows.append(
                    TextChunk(
                        start=start,
                        end=end,
                        text=stripped,
                        fingerprint=_fingerprint(stripped),
                    )
                )

            if not piece and cut >= len(block):
                break
            tail_source = block[:cut]
            carry = tail_source[-overlap_chars:] if overlap_chars else ""
            # Any bytes after a natural-boundary cut have already been read from the file;
            # preserve them after the overlap so they are not skipped.
            remainder = block[cut:]
            carry += remainder
            consumed = end

    return rows
