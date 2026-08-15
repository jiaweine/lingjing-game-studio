# Security Policy

## Supported branch

The v2 SaaS control-plane code is the supported security baseline in this repository. Older single-tenant demo artifacts under `outputs/` are examples only and should not be deployed as services.

## Security boundaries

- Workspace identity is resolved on the server from the authenticated Principal.
- Product resources are queried with `workspace_id`; clients do not get to choose a tenant boundary.
- Passwords are stored with Argon2 hashes.
- Browser sessions use HttpOnly cookies; API clients can use Bearer JWTs.
- Production requires a stable high-entropy `WORLDFORGE_JWT_SECRET`.
- Model-provider credentials are server-side environment secrets and are never returned by `/api/providers`.
- S3 object keys include Workspace prefixes, but application authorization remains mandatory; bucket layout is not the authorization mechanism.
- Audit records are intended to support incident investigation and should be shipped to durable centralized logging in real deployments.

## Production requirements

Before exposing the service to untrusted users, use HTTPS, a secret manager, PostgreSQL backups/PITR, encrypted/versioned object storage, WAF/DDoS controls, centralized monitoring, shared/global rate limiting, and malware scanning for uploads. Public-account deployments should also add email verification and/or enterprise SSO/MFA.

## Reporting a vulnerability

Do not publish secrets, customer data, access tokens, or a working exploit in a public issue. Report the issue privately to the repository owner or security contact for the deployment, including affected version, reproduction steps, impact, and any relevant request IDs/audit evidence.
