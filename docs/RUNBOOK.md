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
python scripts/product_fullstack_ui_e2e.py
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
- `WORLDFORGE_JOB_LEASE_SECONDS=120`
- `WORLDFORGE_JOB_HEARTBEAT_SECONDS=30`（必须短于 lease）
- HTTPS 下 `WORLDFORGE_SECURE_COOKIES=1`
- exact CORS origins and trusted hosts

Compose 将 `/app/outputs/runtime` 作为 `runtime_data` 同时挂载给 API 与 Worker，保证单机
Compose 中 Runtime 事件、Harness generation 与报告使用同一事实源。多主机部署不能依赖
本地/单机 volume，必须换成所有实例可访问的持久化 Runtime Store。

## Database migrations

```bash
alembic upgrade head
```

Do migrations as a deployment step before API/Worker rollout. Do not use runtime `create_all` in production.
Development `auto_create_schema` contains only a narrow compatibility upgrade for lease columns on legacy
create-all databases; it is not a replacement for Alembic and is disabled in production.

## Worker

```bash
python -m worldforge.worker
```

Worker 使用带 fencing token 的可续约 lease 领取 durable analysis job，处理期间按 heartbeat
续约；进程崩溃或失联后，过期任务会自动重新入队。旧 Worker 恢复后无法提交或失败一个已被
新 Worker 领取的 attempt。随后 Worker 从对象存储物化素材，执行 ProductAnalyzer / Runtime /
推理资源调用，写入 `task_events`，并以事务提交终态和结果。

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
3. **Inference resource unavailable** — the product keeps deterministic local/demo analysis where applicable, marks it as `analysis_mode=demo` / `claim_status=hypothesis_only`, and emits a notice; inspect server-side inference configuration and upstream health.
4. **Worker backlog** — inspect durable job status, `attempts`, `heartbeat_at`, `lease_expires_at` and `last_error`. Expired leases are reclaimed on the next claim; scale workers only after distinguishing backlog from dependency failures.
5. **Stuck delete approval** — verify object storage and database errors first; do not consume or bypass the persisted approval manually. Retry the governed delete after the underlying dependency is healthy.
6. **Suspected cross-tenant access** — rotate credentials, preserve audit logs, inspect Workspace-scoped resource access, and treat as a security incident.

## Platform hardening outside the app

Use a reverse proxy / ingress with TLS and WebSocket support. Add WAF/DDoS controls, centralized rate limiting, metrics/tracing/error reporting, secret management, PostgreSQL backups/PITR, object-storage versioning/encryption, and malware scanning for untrusted uploads.

Enterprise SSO/MFA, email verification, legal hold, organization-level retention/export/deletion policy and other compliance controls belong to the deployment/organization layer unless explicitly implemented for that environment.
