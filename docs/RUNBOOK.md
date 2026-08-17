# Lingjing Runbook

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make run
```

默认开发拓扑：SQLite + Local Object Storage + Dev Identity + In-process Worker。

Health:

```bash
curl http://127.0.0.1:8765/api/health/live
curl http://127.0.0.1:8765/api/health/ready
```

Tests:

```bash
make test
python scripts/product_backend_e2e.py
python scripts/product_ui_e2e.py
```

## Production topology

推荐：PostgreSQL + S3/MinIO + API + External Worker。

```bash
cp .env.production.example .env.production
# replace all CHANGE_ME values

docker compose --env-file .env.production -f docker-compose.prod.yml up --build
```

生产必须：

- `WORLDFORGE_ENV=production`
- `WORLDFORGE_AUTH_MODE=required`
- `WORLDFORGE_AUTO_CREATE_SCHEMA=0`
- stable high-entropy `WORLDFORGE_JWT_SECRET`
- PostgreSQL `DATABASE_URL`
- `WORLDFORGE_STORAGE_BACKEND=s3`
- `WORLDFORGE_QUEUE_MODE=external`
- HTTPS 下 `WORLDFORGE_SECURE_COOKIES=1`
- exact CORS origins and trusted hosts

## Database migrations

```bash
alembic upgrade head
```

Do migrations as a deployment step before API/Worker rollout. Do not use runtime `create_all` in production.

## Worker

```bash
python -m worldforge.worker
```

Worker claims durable analysis jobs, materializes task assets from object storage, runs ProductAnalyzer / Runtime / inference-resource calls, writes `task_events`, then commits terminal job state and result artifacts.

## Operations

- `/api/health/live`: process liveness.
- `/api/health/ready`: database + object storage readiness.
- Every HTTP response gets `X-Request-ID`.
- Owner/Admin workspace accounts can read `/api/audit`.
- WebSocket progress is recoverable from persisted `task_events`.
- Product metrics come from persisted `product_events` rather than browser-only counters.
- Permanent delete approvals are persisted; an approved delete that fails storage/database cleanup should remain retryable instead of being manually bypassed.
- Viewer is a server-side read-only role; do not rely on frontend button visibility as authorization.

## Incident priorities

1. **DB unavailable** — readiness returns 503; stop accepting new traffic until PostgreSQL is healthy.
2. **Object storage unavailable** — readiness returns 503; uploads/downloads and worker asset access will fail.
3. **Inference resource unavailable** — the product keeps deterministic local/demo analysis where applicable and emits a notice; inspect server-side inference configuration and upstream health.
4. **Worker backlog** — inspect durable job statuses and scale workers; do not scale API as a substitute for worker capacity.
5. **Stuck delete approval** — verify object storage and database errors first; do not consume or bypass the persisted approval manually. Retry the governed delete after the underlying dependency is healthy.
6. **Suspected cross-tenant access** — rotate credentials, preserve audit logs, inspect Workspace-scoped resource access, and treat as a security incident.

## Platform hardening outside the app

Use a reverse proxy / ingress with TLS and WebSocket support. Add WAF/DDoS controls, centralized rate limiting, metrics/tracing/error reporting, secret management, PostgreSQL backups/PITR, object-storage versioning/encryption, and malware scanning for untrusted uploads.

Enterprise SSO/MFA, email verification, legal hold, organization-level retention/export/deletion policy and other compliance controls belong to the deployment/organization layer unless explicitly implemented for that environment.
