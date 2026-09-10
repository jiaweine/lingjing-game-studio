from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote


logger = logging.getLogger("worldforge.storage")
_ASSET_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _attachment_disposition(filename: str) -> str:
    """Return a header-safe UTF-8 Content-Disposition value for object-store responses."""
    encoded = quote(str(filename or "download.bin"), safe="")
    return f"attachment; filename*=UTF-8''{encoded}"


def _asset_bundle_prefix(key: str) -> str | None:
    """Return the creation-only asset prefix for app-generated object keys."""
    raw = str(key or "")
    if not raw or raw.startswith("/") or "\x00" in raw:
        return None
    parts = PurePosixPath(raw).parts
    for index, part in enumerate(parts[:-2]):
        if part != "assets" or index + 1 >= len(parts):
            continue
        asset_id = parts[index + 1]
        if _ASSET_ID_RE.fullmatch(asset_id):
            return "/".join(parts[: index + 2]) + "/"
    return None


class ObjectStorage:
    name = "base"

    def put_bytes(self, key, data, content_type):
        raise NotImplementedError

    def put_file(self, key, source, content_type):
        try:
            return self._put_file(key, source, content_type)
        except Exception:
            prefix = _asset_bundle_prefix(str(key))
            if prefix:
                try:
                    self.delete_prefix(prefix)
                except Exception:
                    logger.exception(
                        "failed to roll back asset object bundle",
                        extra={"object_prefix": prefix},
                    )
            raise

    def _put_file(self, key, source, content_type):
        return self.put_bytes(key, Path(source).read_bytes(), content_type)

    def local_path(self, key):
        return None

    def get_bytes(self, key):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def delete_prefix(self, prefix):
        raise NotImplementedError

    def signed_url(self, key, *, filename, expires=300):
        return None

    def healthcheck(self):
        return True


class LocalObjectStorage(ObjectStorage):
    name = "local"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key) -> Path:
        raw = str(key or "")
        if not raw or "\x00" in raw:
            raise ValueError("invalid object key")
        root = self.root.resolve()
        path = (root / raw).resolve()
        if path == root or root not in path.parents:
            raise ValueError("invalid object key")
        return path

    def put_bytes(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def _put_file(self, key, source, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        return key

    def local_path(self, key):
        return self._path(key)

    def get_bytes(self, key):
        return self._path(key).read_bytes()

    def delete(self, key):
        path = self._path(key)
        path.unlink(missing_ok=True)
        current = path.parent
        root = self.root.resolve()
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def delete_prefix(self, prefix):
        prefix = str(prefix or "")
        if _asset_bundle_prefix(f"{prefix.rstrip('/')}/__rollback__") != prefix:
            raise ValueError("invalid asset object prefix")
        marker = self._path(f"{prefix}__rollback__")
        shutil.rmtree(marker.parent, ignore_errors=True)

    def healthcheck(self):
        path = self._path(".healthcheck")
        path.write_text("ok")
        path.unlink(missing_ok=True)
        return True


class S3ObjectStorage(ObjectStorage):
    name = "s3"

    def __init__(self, *, bucket, region=None, endpoint_url=None, access_key=None, secret_key=None):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put_bytes(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def _put_file(self, key, source, content_type):
        self.client.upload_file(str(source), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix):
        prefix = str(prefix or "")
        if _asset_bundle_prefix(f"{prefix.rstrip('/')}/__rollback__") != prefix:
            raise ValueError("invalid asset object prefix")
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [
                {"Key": row["Key"]}
                for row in page.get("Contents", [])
                if row.get("Key")
            ]
            if objects:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )

    def healthcheck(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True

    def signed_url(self, key, *, filename, expires=300):
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": _attachment_disposition(filename),
            },
            ExpiresIn=expires,
        )


def build_storage(settings, asset_dir):
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required when WORLDFORGE_STORAGE_BACKEND=s3")
        return S3ObjectStorage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    return LocalObjectStorage(asset_dir)
