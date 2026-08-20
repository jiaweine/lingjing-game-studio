from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_keeps_secure_cookie_default_and_requires_hosts():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "WORLDFORGE_SECURE_COOKIES: ${WORLDFORGE_SECURE_COOKIES:-1}" in compose
    assert "WORLDFORGE_SECURE_COOKIES:-0" not in compose
    assert "WORLDFORGE_CORS_ORIGINS: ${WORLDFORGE_CORS_ORIGINS:-}" in compose
    assert "WORLDFORGE_TRUSTED_HOSTS: ${WORLDFORGE_TRUSTED_HOSTS:?set WORLDFORGE_TRUSTED_HOSTS}" in compose
    assert "FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-127.0.0.1}" in compose


def test_production_env_example_does_not_recommend_insecure_cookie_or_proxy_trust():
    example = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "WORLDFORGE_SECURE_COOKIES=1" in example
    assert "WORLDFORGE_SECURE_COOKIES=0" not in example
    assert "WORLDFORGE_CORS_ORIGINS=\n" in example
    assert "WORLDFORGE_TRUSTED_HOSTS=game-studio.example.com" in example
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in example
    assert "FORWARDED_ALLOW_IPS=*" not in example


def test_dockerfile_does_not_trust_forwarded_ips_from_every_peer():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '--forwarded-allow-ips", "*"' not in dockerfile
    assert '"--proxy-headers"' in dockerfile
