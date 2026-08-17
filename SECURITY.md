# Security Policy

## Supported baseline

The supported security baseline is the current `main` product architecture. Historical demo artifacts or old generated outputs should not be deployed as services or treated as supported security boundaries.

## Security boundaries

- Workspace identity is resolved on the server from the authenticated principal and current membership.
- Product resources are queried and authorized with `workspace_id`; clients do not choose their own tenant boundary.
- Owner / Admin / Member / Viewer permissions are enforced server-side; Viewer write requests are rejected even if a custom client bypasses the UI.
- Passwords are stored with Argon2 hashes.
- Browser sessions use HttpOnly cookies; API clients can use Bearer JWTs.
- Production requires a stable high-entropy `WORLDFORGE_JWT_SECRET`.
- Inference-service credentials are server-side secrets and are not exposed through the customer workspace.
- S3-compatible object keys include Workspace prefixes, but application authorization remains mandatory; bucket layout is not the authorization mechanism.
- Upload media is validated before being treated as trusted image/audio/video input.
- Durable audit records are intended to support incident investigation and should be shipped to centralized durable logging in real deployments.

## Governed destructive actions

Permanent task deletion uses a persisted approval request rather than a browser-only confirmation.

- the task is locked while deletion approval is pending;
- only authorized workspace managers can resolve the approval;
- rejection restores the previous task status;
- approved deletion validates the approval again before the database delete;
- database deletion and `conversation.delete` audit are committed atomically;
- object-storage deletion failure leaves the task and approval available for retry.

## Production requirements

Before exposing the service to untrusted users, use HTTPS, a secret manager, PostgreSQL backups/PITR, encrypted/versioned object storage, WAF/DDoS controls, centralized monitoring, shared/global rate limiting, and malware scanning for uploads. Public-account deployments should also add email verification and/or enterprise SSO/MFA.

Application-level approval is not a substitute for organization-level retention, export, legal hold, or compliance deletion policy.

## Reporting a vulnerability

Do not publish secrets, customer data, access tokens, or a working exploit in a public issue. Report the issue privately to the repository owner or security contact for the deployment, including affected commit/version, reproduction steps, impact, and any relevant request IDs or audit evidence.
