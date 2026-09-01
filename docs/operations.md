# MachReach production operations

## Availability and environments

Production runs today on Render (`render.yaml`): the `starter` web service,
the always-on `machreach-worker`, and the `machreach-db` PostgreSQL database.
Production deploys only after GitHub checks pass. It is moving to a single
small server running the stack in `deploy/` (Postgres, web, worker, and Caddy
for HTTPS), which keeps every behaviour below and cuts the bill to one
instance; the setup, cutover, and rollback are in
[VPS_DEPLOY.md](VPS_DEPLOY.md). The Render-plus-Neon-plus-cron alternative is
documented in [NEON_MIGRATION.md](NEON_MIGRATION.md). A staging stack is not
currently declared; before a release requires staging, provision it
separately with its own PostgreSQL database, manual deploys, isolated
secrets, callback URLs, and Lemon Squeezy test mode.

`/health` is intentionally public and exposes only `healthy` or `degraded`. `/health/operations` requires `X-Operations-Secret` and reports aggregate worker, queue, webhook, wallet, SMTP, database, and dependency signals without user or job details. The worker heartbeat alerts after `WORKER_HEARTBEAT_STALE_SECONDS` (default 120; raise it only for the cron-job worker variant).

Queued student work (quiz and flashcard generation, verification and password-reset emails) is picked up within five seconds by the always-on worker. `worker.py --once` runs the same jobs as one bounded pass for a cron-style host; its time-of-day jobs keep their America/Santiago schedule in the database, and a missed day collapses into one catch-up run.

## Backups and recovery

On Render, database recovery uses the recovery capabilities of the Render
database plan. On the VPS, `deploy/backup.sh` writes a nightly `pg_dump` to
`/var/backups/machreach` and keeps fourteen days; copy the newest file off the
server regularly, since a backup on the database's own disk does not survive
losing the server. There is no app-managed external S3 backup workflow.

Target recovery objectives for the current product are **RPO 24 hours** and
**RTO 4 hours**. Exercise a backup against a new, empty, non-production
database with `scripts/restore_drill.ps1`; record the backup timestamp,
start/end time, migration result, row-count verification, and achieved
RPO/RTO. The script refuses targets whose database name does not contain
`restore` or `drill`.

## Release gate

Before production deploys:

1. CI must pass for SQLite, PostgreSQL critical paths, Python and Node audits, the landing rebuild, extension validation, and the complete Playwright browser matrix.
2. Deploy and exercise staging first. Confirm it uses only staging Postgres, provider test credentials, and staging callback URLs.
3. Deploy with the same `ENCRYPTION_KEY` shared by each environment's web and worker processes. Never share database or provider secrets across environments.
4. Confirm public `/health` is healthy, protected `/health/operations` has no failed signals, and the worker heartbeat is newer than two minutes.
5. In Lemon Squeezy, copy the production store ID plus the product and variant IDs for Plus and every enabled coin pack into the matching `LEMON_SQUEEZY_STORE_ID`, `LS_PRODUCT_*`, and `LS_VARIANT_*` Render variables. A signed event must match all three identifiers; checkout remains disabled when any required identifier is missing.
6. Exercise staging login, queued AI work, test checkout/webhook, account export, and provider-first deletion before approving production.

Database migrations must remain backward-compatible for at least one release.

## Incident response

1. Assign an incident lead and record the start time, affected feature, and first known bad deploy or event.
2. Stop unsafe side effects first: stop the worker for duplicate email or reward risk, disable checkout for billing drift, or block the affected route.
3. Preserve Sentry events, worker logs, webhook ledger rows, async-job state, and relevant snapshots. Never copy secrets or recipient content into the incident record.
4. Restore the previous successful deploy when its code is schema-compatible; otherwise deploy a forward fix.
5. Reconcile provider state for subscriptions and coin orders, and quarantine uncertain email delivery for manual review.
6. Communicate impact and resolution to affected users, then record cause, detection gap, corrective action, owner, RPO, and achieved RTO.

After rollback or recovery, verify both health endpoints, worker heartbeat, queued-job age, webhook failures, billing reconciliation, and one read-only student journey. Never perform a restore drill over production.

## Data retention and deletion

- Account deletion is provider-first: recurring subscriptions must be cancelled successfully before local records are removed.
- Data exports are generated on demand and are not retained after the response completes.
- Failed webhook records retain only reconciliation metadata and safe error categories.
- Unverified accounts are removed after seven days; an attempted registration before cleanup reuses the pending account.
- Soft-deleted courses have a 30-day administrator recovery window before course-specific data is purged.
- Define any additional production retention periods with Chilean privacy counsel, automate them, and disclose them in the privacy policy.

## Required alerts

- Public `/health` non-200 and protected `/health/operations` degraded.
- Worker heartbeat older than `WORKER_HEARTBEAT_STALE_SECONDS` (two minutes).
- Oldest queued job over ten minutes or any job exhausting retries. An
  exhausted job stops alerting on its own only when nothing is left to act on:
  the worker settles verification deliveries for accounts verified another way
  and drops failed jobs whose account was deleted. Anything else stays visible
  until it is requeued from `/admin/jobs` (generation and verification jobs)
  or resolved in an incident.
- Failed webhook, billing reconciliation drift, or coin order without a completed ledger entry.
- Outstanding partial-refund review or failed provider cancellation after a locally enforced full refund. After reconciling it in Lemon Squeezy, remove the corresponding `operational_events` row and record the action in the incident log.
- Any AI reservation older than fifteen minutes or withheld referral reward reported by `/health/operations`.
- Repeated verification-email failures, SMTP failure, database saturation, and Sentry exception-rate spikes.
