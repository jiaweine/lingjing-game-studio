from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return (
        default
        if value is None
        else value.strip().lower() in {"1", "true", "yes", "on"}
    )


def _list(name: str, default: str = "") -> list[str]:
    return [
        value.strip()
        for value in os.getenv(name, default).split(",")
        if value.strip()
    ]


@dataclass(frozen=True)
class Settings:
    env: str
    data_dir: Path
    database_url: str
    auth_mode: str
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str
    access_token_minutes: int
    secure_cookies: bool
    cors_origins: list[str]
    trusted_hosts: list[str]
    rate_limit_per_minute: int
    max_upload_mb: int
    max_context_assets: int
    max_context_mb: int
    queue_mode: str
    storage_backend: str
    s3_bucket: str | None
    s3_region: str | None
    s3_endpoint_url: str | None
    s3_access_key: str | None
    s3_secret_key: str | None
    request_log: bool
    auto_create_schema: bool

    @property
    def production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    env = os.getenv("WORLDFORGE_ENV", "development").strip().lower()
    data_dir = Path(
        os.getenv("WORLDFORGE_DATA", ROOT / "outputs" / "runtime")
    )
    database_url = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(data_dir / 'product.db').as_posix()}",
    )
    auth_mode = os.getenv(
        "WORLDFORGE_AUTH_MODE",
        "required" if env == "production" else "dev",
    ).strip().lower()
    if auth_mode not in {"dev", "required"}:
        raise RuntimeError("WORLDFORGE_AUTH_MODE must be 'dev' or 'required'")

    secret = os.getenv("WORLDFORGE_JWT_SECRET", "").strip()
    if not secret:
        if env == "production":
            raise RuntimeError("WORLDFORGE_JWT_SECRET is required in production")
        secret = secrets.token_urlsafe(48)
    if env == "production" and len(secret) < 32:
        raise RuntimeError(
            "WORLDFORGE_JWT_SECRET must be at least 32 characters in production"
        )

    queue_mode = os.getenv("WORLDFORGE_QUEUE_MODE", "inprocess").strip().lower()
    if queue_mode not in {"inprocess", "external"}:
        raise RuntimeError(
            "WORLDFORGE_QUEUE_MODE must be 'inprocess' or 'external'"
        )

    storage_backend = os.getenv(
        "WORLDFORGE_STORAGE_BACKEND", "local"
    ).strip().lower()
    if storage_backend not in {"local", "s3"}:
        raise RuntimeError(
            "WORLDFORGE_STORAGE_BACKEND must be 'local' or 's3'"
        )

    return Settings(
        env=env,
        data_dir=data_dir,
        database_url=database_url,
        auth_mode=auth_mode,
        jwt_secret=secret,
        jwt_issuer=os.getenv("WORLDFORGE_JWT_ISSUER", "lingjing-game-studio"),
        jwt_audience=os.getenv("WORLDFORGE_JWT_AUDIENCE", "lingjing-web"),
        access_token_minutes=max(
            1, int(os.getenv("WORLDFORGE_ACCESS_TOKEN_MINUTES", "720"))
        ),
        secure_cookies=_bool("WORLDFORGE_SECURE_COOKIES", env == "production"),
        cors_origins=_list(
            "WORLDFORGE_CORS_ORIGINS",
            "http://localhost:8765,http://127.0.0.1:8765",
        ),
        trusted_hosts=_list(
            "WORLDFORGE_TRUSTED_HOSTS",
            "localhost,127.0.0.1,testserver" if env != "production" else "",
        ),
        rate_limit_per_minute=max(
            10, int(os.getenv("WORLDFORGE_RATE_LIMIT_PER_MINUTE", "120"))
        ),
        max_upload_mb=max(
            1, int(os.getenv("WORLDFORGE_MAX_UPLOAD_MB", "120"))
        ),
        max_context_assets=max(
            1, min(100, int(os.getenv("WORLDFORGE_MAX_CONTEXT_ASSETS", "24")))
        ),
        max_context_mb=max(
            1, int(os.getenv("WORLDFORGE_MAX_CONTEXT_MB", "256"))
        ),
        queue_mode=queue_mode,
        storage_backend=storage_backend,
        s3_bucket=os.getenv("S3_BUCKET") or None,
        s3_region=os.getenv("S3_REGION") or None,
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        s3_access_key=os.getenv("S3_ACCESS_KEY") or None,
        s3_secret_key=os.getenv("S3_SECRET_KEY") or None,
        request_log=_bool("WORLDFORGE_REQUEST_LOG", True),
        auto_create_schema=_bool("WORLDFORGE_AUTO_CREATE_SCHEMA", env != "production"),
    )


settings = load_settings()
