# MachReach production operations

## Availability and environments

The checked-in Render Blueprint defines the paid production `starter` web
service and the `machreach-worker` cron job, which runs `python worker.py
--once` every minute and bills per second of runtime. The production
PostgreSQL database is hosted on Neon and is not declared in the Blueprint;
`DATABASE_URL` is set by hand on both services to Neon's direct connection
string. The move and its rollback are documented in
[NEON_MIGRATION.md](NEON_MIGRATION.md). Production deploys only after GitHub
checks pass. A staging stack is not currently declared in `render.yaml`;
before a release requires staging, provision it separately with its own
PostgreSQL database (a Neon branch or project), manual deploys, isolated
secrets, callback URLs, and Lemon Squeezy test mode.

`/health` is intentionally public and exposes only `healthy` or `degraded`. `/health/operations` requires `X-Operations-Secret` and reports aggregate worker, queue, webhook, wallet, SMTP, database, and dependency signals without user or job details. The worker heartbeat is written once per cron run and every 30 seconds while a run is busy; it alerts after `WORKER_HEARTBEAT_STALE_SECONDS` (180 in production).

Queued student work (quiz and flashcard generation, verification and password-reset emails) is picked up by the next cron run: up to a minute plus start-up, where the always-on worker polled every five seconds. Time-of-day jobs keep their America/Santiago schedule; each cron run fires whichever has come due since the last run it recorded, and a missed day collapses into one catch-up run.

## Backups and recovery

MachReach uses a PostgreSQL database hosted on Neon. There is no app-managed
external S3 backup workflow. Database recovery uses Neon's point-in-time
restore window, whose length depends on the Neon plan (hours on Free, days on
paid plans), plus `pg_dump` files taken outside Render as described in
[NEON_MIGRATION.md](NEON_MIGRATION.md#backups-on-neon).

Target recovery objectives for the current product are **RPO 24 hours** and
**RTO 4 hours**. While the Neon plan's restore window is shorter than a day,
a daily `pg_dump` is what meets the RPO; confirm the window on the Neon
project page before paid launch. Exercise a dump against a new, empty,
non-production database (a second Neon database or a branch) with
`scripts/restore_drill.ps1`; record the backup timestamp, start/end time,
migration result, row-count verification, and achieved RPO/RTO. The script
refuses targets whose database name does not contain `restore` or `drill`.

## Release gate

Before production deploys:

1. CI must pass for SQLite, PostgreSQL critical paths, Python and Node audits, the landing rebuild, extension validation, and the complete Playwright browser matrix.
2. Deploy and exercise staging first. Confirm it uses only staging Postgres, provider test credentials, and staging callback URLs.
3. Apply the Blueprint with the same `ENCRYPTION_KEY` shared by each environment's web service and worker cron job. Never share database or provider secrets across environments.
4. Confirm public `/health` is healthy, protected `/health/operations` has no failed signals, and the worker heartbeat is newer than three minutes.
5. In Lemon Squeezy, copy the production store ID plus the product and variant IDs for Plus and every enabled coin pack into the matching `LEMON_SQUEEZY_STORE_ID`, `LS_PRODUCT_*`, and `LS_VARIANT_*` Render variables. A signed event must match all three identifiers; checkout remains disabled when any required identifier is missing.
6. Exercise staging login, queued AI work, test checkout/webhook, account export, and provider-first deletion before approving production.

Database migrations must remain backward-compatible for at least one release.

## Incident response

1. Assign an incident lead and record the start time, affected feature, and first known bad deploy or event.
2. Stop unsafe side effects first: suspend the worker cron job for duplicate email or reward risk, disable checkout for billing drift, or block the affected route.
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
- Worker heartbeat older than three minutes (`WORKER_HEARTBEAT_STALE_SECONDS`).
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
