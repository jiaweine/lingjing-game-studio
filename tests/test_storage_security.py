from __future__ import annotations

import pytest

from worldforge.storage import S3ObjectStorage, _attachment_disposition


def test_attachment_disposition_percent_encodes_header_sensitive_filename_bytes():
    value = _attachment_disposition('报告 "boss"\r\nX-Evil: yes.txt')
    assert value.startswith("attachment; filename*=UTF-8''")
    assert "\r" not in value
    assert "\n" not in value
    assert '"' not in value
    assert "%0D%0A" in value
    assert "%22boss%22" in value
    assert "%E6%8A%A5%E5%91%8A" in value


def test_s3_signed_url_passes_only_encoded_content_disposition():
    captured = {}

    class Client:
        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            captured.update(
                operation=operation,
                params=dict(Params),
                expires=ExpiresIn,
            )
            return "https://objects.example.test/signed"

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()

    url = storage.signed_url(
        "ws/assets/source.bin",
        filename='evil\r\nX-Test: 1".bin',
        expires=123,
    )
    assert url == "https://objects.example.test/signed"
    assert captured["operation"] == "get_object"
    assert captured["expires"] == 123
    disposition = captured["params"]["ResponseContentDisposition"]
    assert "\r" not in disposition and "\n" not in disposition and '"' not in disposition
    assert "%0D%0A" in disposition


def test_s3_asset_bundle_failure_deletes_prior_objects(tmp_path):
    asset_id = "b" * 32
    prefix = f"workspace/assets/{asset_id}/"
    source_key = f"{prefix}source.mp4"
    frame_key = f"{prefix}frames/00.jpg"
    source = tmp_path / "source.mp4"
    frame = tmp_path / "frame.jpg"
    source.write_bytes(b"video")
    frame.write_bytes(b"frame")
    deleted = []

    class Paginator:
        def paginate(self, *, Bucket, Prefix):
            assert Bucket == "bucket"
            assert Prefix == prefix
            return [{"Contents": [{"Key": source_key}]}]

    class Client:
        def __init__(self):
            self.upload_count = 0

        def upload_file(self, _source, bucket, key, ExtraArgs):
            assert bucket == "bucket"
            assert ExtraArgs["ContentType"] in {"video/mp4", "image/jpeg"}
            self.upload_count += 1
            if self.upload_count == 2:
                raise OSError("injected s3 derivative failure")
            assert key == source_key

        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return Paginator()

        def delete_objects(self, *, Bucket, Delete):
            deleted.append((Bucket, Delete))

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()

    storage.put_file(source_key, source, "video/mp4")
    with pytest.raises(OSError, match="injected s3 derivative failure"):
        storage.put_file(frame_key, frame, "image/jpeg")

    assert deleted == [
        (
            "bucket",
            {"Objects": [{"Key": source_key}], "Quiet": True},
        )
    ]
