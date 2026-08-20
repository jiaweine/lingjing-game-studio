from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


class ObjectStorage:
    name = "base"

    def put_bytes(self, key, data, content_type):
        raise NotImplementedError

    def put_file(self, key, source, content_type):
        return self.put_bytes(key, Path(source).read_bytes(), content_type)

    def put_files_atomic(self, rows):
        """Upload a small related object bundle with best-effort compensation.

        Object stores do not provide a multi-object transaction. Track every object that
        completed and delete it if a later upload fails so a partial video/keyframe
        bundle does not leak orphaned objects.
        """
        uploaded = []
        try:
            for key, source, content_type in rows:
                self.put_file(key, source, content_type)
                uploaded.append(key)
        except Exception:
            for key in reversed(uploaded):
                try:
                    self.delete(key)
                except Exception:
                    pass
            raise
        return uploaded

    def local_path(self, key):
        return None

    def get_bytes(self, key):
        raise NotImplementedError

    def materialize_to(self, key, target):
        """Write an object to a local path used by inference tooling."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.get_bytes(key))
        return target

    def delete(self, key):
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

    def put_bytes(self, key, data, content_type):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def put_file(self, key, source, content_type):
        import shutil

        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        return key

    def local_path(self, key):
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise ValueError("invalid object key")
        return path

    def get_bytes(self, key):
        return self.local_path(key).read_bytes()

    def materialize_to(self, key, target):
        import shutil

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.local_path(key), target)
        return target

    def delete(self, key):
        path = self.local_path(key)
        path.unlink(missing_ok=True)
        current = path.parent
        root = self.root.resolve()
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def healthcheck(self):
        path = self.root / ".healthcheck"
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

    def put_file(self, key, source, content_type):
        self.client.upload_file(str(source), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def materialize_to(self, key, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # boto3's managed download streams multipart/chunked data directly to disk,
        # avoiding a whole-object bytes allocation for large video/audio evidence.
        self.client.download_file(self.bucket, key, str(target))
        return target

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def healthcheck(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True

    def signed_url(self, key, *, filename, expires=300):
        safe_filename = quote(str(filename), safe="")
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f"attachment; filename*=UTF-8''{safe_filename}"
                ),
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
