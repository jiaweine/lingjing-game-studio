from __future__ import annotations

from pathlib import Path

import pytest

from worldforge.storage import LocalObjectStorage


def test_local_storage_rejects_parent_traversal_on_write(tmp_path):
    root = tmp_path / "objects"
    storage = LocalObjectStorage(root)
    outside = tmp_path / "outside.bin"

    with pytest.raises(ValueError, match="invalid object key"):
        storage.put_bytes("../outside.bin", b"escape", "application/octet-stream")

    assert not outside.exists()


def test_local_storage_rejects_absolute_key_on_put_file(tmp_path):
    root = tmp_path / "objects"
    storage = LocalObjectStorage(root)
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    outside = tmp_path / "absolute-target.bin"

    with pytest.raises(ValueError, match="invalid object key"):
        storage.put_file(str(outside), source, "application/octet-stream")

    assert not outside.exists()


def test_local_storage_rejects_symlink_parent_escape(tmp_path):
    root = tmp_path / "objects"
    outside_dir = tmp_path / "outside"
    root.mkdir()
    outside_dir.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    storage = LocalObjectStorage(root)

    with pytest.raises(ValueError, match="invalid object key"):
        storage.put_bytes("link/escape.bin", b"escape", "application/octet-stream")

    assert not (outside_dir / "escape.bin").exists()


def test_local_storage_round_trip_uses_same_contained_path(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    key = "workspace/assets/item/source.bin"

    assert storage.put_bytes(key, b"payload", "application/octet-stream") == key
    path = storage.local_path(key)
    assert isinstance(path, Path)
    assert path.read_bytes() == b"payload"
    assert storage.get_bytes(key) == b"payload"

    storage.delete(key)
    assert not path.exists()
