from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - production/runtime images are Linux.
    fcntl = None


@contextmanager
def exclusive_path_lock(path: str | Path):
    """Serialize cross-process mutations associated with one persistent JSON path."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is None:
            raise RuntimeError(
                "Cross-process Harness persistence requires POSIX file locking"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_write_text(path: str | Path, text: str) -> None:
    """Durably replace a text file without exposing a partially written generation."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
