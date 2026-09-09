from __future__ import annotations

import pytest

from worldforge.settings import load_settings


_PRODUCTION_KEYS = (
    "WORLDFORGE_ENV",
    "WORLDFORGE_AUTH_MODE",
    "WORLDFORGE_JWT_SECRET",
    "WORLDFORGE_SECURE_COOKIES",
    "WORLDFORGE_TRUSTED_HOSTS",
    "WORLDFORGE_CORS_ORIGINS",
    "WORLDFORGE_QUEUE_MODE",
    "WORLDFORGE_STORAGE_BACKEND",
)


def _clear(monkeypatch):
    for key in _PRODUCTION_KEYS:
        monkeypatch.delenv(key, raising=False)


def _valid_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_ENV", "production")
    monkeypatch.setenv("WORLDFORGE_AUTH_MODE", "required")
    monkeypatch.setenv("WORLDFORGE_JWT_SECRET", "x" * 48)
    monkeypatch.setenv("WORLDFORGE_SECURE_COOKIES", "1")
    monkeypatch.setenv("WORLDFORGE_TRUSTED_HOSTS", "studio.example.com,api.example.com")
    monkeypatch.setenv("WORLDFORGE_CORS_ORIGINS", "https://studio.example.com")


def test_production_cannot_enable_dev_auth(monkeypatch):
    _valid_production(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_AUTH_MODE", "dev")
    with pytest.raises(RuntimeError, match="mandatory in production"):
        load_settings()


def test_production_requires_strong_stable_jwt_secret(monkeypatch):
    _valid_production(monkeypatch)
    monkeypatch.delenv("WORLDFORGE_JWT_SECRET")
    with pytest.raises(RuntimeError, match="required in production"):
        load_settings()

    monkeypatch.setenv("WORLDFORGE_JWT_SECRET", "short-secret")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        load_settings()


def test_production_cannot_disable_secure_cookies(monkeypatch):
    _valid_production(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_SECURE_COOKIES", "0")
    with pytest.raises(RuntimeError, match="cannot be disabled"):
        load_settings()


def test_production_requires_explicit_non_wildcard_trusted_hosts(monkeypatch):
    _valid_production(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_TRUSTED_HOSTS", "")
    with pytest.raises(RuntimeError, match="TRUSTED_HOSTS is required"):
        load_settings()

    monkeypatch.setenv("WORLDFORGE_TRUSTED_HOSTS", "*")
    with pytest.raises(RuntimeError, match="cannot contain '\*'"):
        load_settings()


def test_production_credentialed_cors_rejects_wildcard(monkeypatch):
    _valid_production(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="credentialed production CORS"):
        load_settings()


def test_valid_production_settings_keep_explicit_boundaries(monkeypatch):
    _valid_production(monkeypatch)
    settings = load_settings()
    assert settings.production is True
    assert settings.auth_mode == "required"
    assert settings.secure_cookies is True
    assert settings.trusted_hosts == ["studio.example.com", "api.example.com"]
    assert settings.cors_origins == ["https://studio.example.com"]
    assert settings.auto_create_schema is False


def test_unknown_queue_or_storage_backend_is_rejected(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WORLDFORGE_QUEUE_MODE", "magic")
    with pytest.raises(RuntimeError, match="QUEUE_MODE"):
        load_settings()

    monkeypatch.setenv("WORLDFORGE_QUEUE_MODE", "inprocess")
    monkeypatch.setenv("WORLDFORGE_STORAGE_BACKEND", "mystery")
    with pytest.raises(RuntimeError, match="STORAGE_BACKEND"):
        load_settings()
