from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


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
    production = env == "production"
    data_dir = Path(os.getenv("WORLDFORGE_DATA", ROOT / "outputs" / "runtime"))
    database_url = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(data_dir / 'product.db').as_posix()}",
    )

    auth_mode = os.getenv(
        "WORLDFORGE_AUTH_MODE",
        "required" if production else "dev",
    ).strip().lower()
    if auth_mode not in {"dev", "required"}:
        raise RuntimeError("WORLDFORGE_AUTH_MODE must be 'dev' or 'required'")
    if production and auth_mode != "required":
        raise RuntimeError("WORLDFORGE_AUTH_MODE=required is mandatory in production")

    secret = os.getenv("WORLDFORGE_JWT_SECRET", "").strip()
    if not secret:
        if production:
            raise RuntimeError("WORLDFORGE_JWT_SECRET is required in production")
        secret = secrets.token_urlsafe(48)
    if production and len(secret.encode("utf-8")) < 32:
        raise RuntimeError("WORLDFORGE_JWT_SECRET must be at least 32 bytes in production")

    secure_cookies = _bool("WORLDFORGE_SECURE_COOKIES", production)
    if production and not secure_cookies:
        raise RuntimeError("WORLDFORGE_SECURE_COOKIES cannot be disabled in production")

    trusted_hosts = _list(
        "WORLDFORGE_TRUSTED_HOSTS",
        "localhost,127.0.0.1,testserver" if not production else "",
    )
    if production and not trusted_hosts:
        raise RuntimeError("WORLDFORGE_TRUSTED_HOSTS is required in production")
    if production and "*" in trusted_hosts:
        raise RuntimeError("WORLDFORGE_TRUSTED_HOSTS cannot contain '*' in production")

    cors_origins = _list(
        "WORLDFORGE_CORS_ORIGINS",
        "http://localhost:8765,http://127.0.0.1:8765" if not production else "",
    )
    if production and "*" in cors_origins:
        raise RuntimeError(
            "WORLDFORGE_CORS_ORIGINS cannot contain '*' with credentialed production CORS"
        )

    queue_mode = os.getenv("WORLDFORGE_QUEUE_MODE", "inprocess").strip().lower()
    if queue_mode not in {"inprocess", "external"}:
        raise RuntimeError("WORLDFORGE_QUEUE_MODE must be 'inprocess' or 'external'")

    storage_backend = os.getenv("WORLDFORGE_STORAGE_BACKEND", "local").strip().lower()
    if storage_backend not in {"local", "s3"}:
        raise RuntimeError("WORLDFORGE_STORAGE_BACKEND must be 'local' or 's3'")

    return Settings(
        env=env,
        data_dir=data_dir,
        database_url=database_url,
        auth_mode=auth_mode,
        jwt_secret=secret,
        jwt_issuer=os.getenv("WORLDFORGE_JWT_ISSUER", "lingjing-game-studio"),
        jwt_audience=os.getenv("WORLDFORGE_JWT_AUDIENCE", "lingjing-web"),
        access_token_minutes=max(1, int(os.getenv("WORLDFORGE_ACCESS_TOKEN_MINUTES", "720"))),
        secure_cookies=secure_cookies,
        cors_origins=cors_origins,
        trusted_hosts=trusted_hosts,
        rate_limit_per_minute=max(
            10,
            int(os.getenv("WORLDFORGE_RATE_LIMIT_PER_MINUTE", "120")),
        ),
        max_upload_mb=max(1, int(os.getenv("WORLDFORGE_MAX_UPLOAD_MB", "120"))),
        queue_mode=queue_mode,
        storage_backend=storage_backend,
        s3_bucket=os.getenv("S3_BUCKET") or None,
        s3_region=os.getenv("S3_REGION") or None,
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        s3_access_key=os.getenv("S3_ACCESS_KEY") or None,
        s3_secret_key=os.getenv("S3_SECRET_KEY") or None,
        request_log=_bool("WORLDFORGE_REQUEST_LOG", True),
        auto_create_schema=_bool("WORLDFORGE_AUTO_CREATE_SCHEMA", not production),
    )


settings = load_settings()
