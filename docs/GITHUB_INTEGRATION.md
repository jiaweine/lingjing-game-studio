# GitHub integration

Lingjing's GitHub integration is intentionally split into explicit product boundaries:

1. **Issue linkage** — a Lingjing task is explicitly linked to a GitHub Issue.
2. **PR / Commit context** — the linked task is bound to a concrete code version.
3. **Explicit result push** — a user chooses when to publish a reproduction or verified-result comment.
4. **CI revalidation trigger** — an approved successful GitHub Actions workflow can enqueue a new Lingjing verification run for the exact bound commit.

A successful GitHub Actions run is only a trigger. It is never treated as proof that a bug is fixed. The resulting Lingjing execution still goes through the existing evidence and Verifier path.

## Server configuration

The current integration is deployment-level. Provider credentials are never accepted from task payloads or stored in conversation rows.

```bash
# Server-side GitHub credential used for PR/commit reads and explicit Issue-comment pushes.
export WORLDFORGE_GITHUB_TOKEN='...'

# Shared secret configured on the GitHub repository webhook and on the Lingjing server.
export WORLDFORGE_GITHUB_WEBHOOK_SECRET='...'

# Exact GitHub Actions workflow display names that are allowed to trigger revalidation.
# Keep this list narrow; do not add docs/lint workflows unless they genuinely produce a game build
# that should be revalidated.
export WORLDFORGE_GITHUB_REVALIDATION_WORKFLOWS='Game CI,Regression Build'
```

The GitHub credential needs access to the repositories that users explicitly link. It must be able to read the repository's PR/commit context and, when explicit result push is used, create or update Issue comments.

## GitHub webhook

Configure the repository webhook to deliver to:

```text
https://<your-lingjing-host>/integrations/github/webhook
```

Use the same secret as `WORLDFORGE_GITHUB_WEBHOOK_SECRET` and enable the **Workflow runs** event. The endpoint accepts GitHub's JSON payload and validates `X-Hub-Signature-256` before recording or acting on a delivery.

Only this event shape can enqueue a revalidation:

- event: `workflow_run`
- action: `completed`
- conclusion: `success`
- workflow name: exact member of `WORLDFORGE_GITHUB_REVALIDATION_WORKFLOWS`
- repository + `head_sha`: exact match to a Lingjing task's current structured GitHub code context

All other events/results are recorded or ignored without starting a Lingjing execution.

## Task workflow

In the Lingjing task:

1. Link the GitHub Issue using an Issue URL or `owner/repo#123`.
2. Bind a same-repository PR or commit using a PR URL, `PR #12`, commit URL, or SHA.
3. Run the reproduction and keep the evidence in Lingjing.
4. Optionally use **推送复现摘要** to publish the evidence-bounded reproduction summary.
5. When an approved GitHub workflow succeeds for the exact bound commit, Lingjing can enqueue a new revalidation run.
6. The new run receives the CI commit as its verification scope. Old commit-specific frozen project-memory refs are not carried into the new run.
7. Only after the resulting structured outcome is `verified=true` can **推送验证结论** publish the verifier-authoritative result.

Lingjing does **not** automatically close the GitHub Issue, change labels, change assignees, or change Issue state.

## Replay and failure behavior

GitHub deliveries are recorded in `github_webhook_deliveries`.

- Re-delivery of an already completed delivery id does not enqueue a second job.
- A currently processing delivery has a 60-second lease, preventing concurrent duplicate processing.
- If the process dies while a delivery is still `received`, the delivery can be reclaimed after the lease expires.
- If a job was already created before the process died, the same delivery reuses and re-schedules that job instead of creating another one.
- The indexed code binding is rechecked against the authoritative task linkage metadata before execution, so a stale derived index cannot trigger a task.
- If another different Lingjing job is already active for the task, the CI delivery is recorded as `deferred`. **This slice does not automatically drain deferred deliveries later.**

## Information boundary for GitHub comments

Explicit Issue comments are built from product-visible fields only:

- task identity;
- Build / Branch / Commit scope;
- structured issue outcome and reason;
- user-visible final result;
- evidence display names.

Raw evidence locators, local file paths, provider telemetry, ContextOS internals, and Agent trace are not included. Repeated pushes update the Lingjing-authored comment when its identity can be recovered, rather than intentionally creating comment spam.

## Current limitations

The current implementation deliberately does not claim:

- per-workspace GitHub App installation or OAuth self-service;
- automatic Issue closing or status mutation;
- automatic tracking of a PR when its head moves to a new commit without refreshing the bound code context;
- durable automatic draining of CI triggers that arrived while another task execution was active;
- Jira / Linear / other tracker parity.

Those are separate productization steps. The current boundary is designed so they can be added without weakening task isolation, evidence authority, or provider-secret handling.
