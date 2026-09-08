from __future__ import annotations

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
