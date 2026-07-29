# MachReach production operations

## Availability and environments

The checked-in Render Blueprint defines the paid production `starter` web and
worker services. Production deploys only after GitHub checks pass. A staging
stack is not currently declared in `render.yaml`; before a release requires
staging, provision it separately with its own paid PostgreSQL database, manual
deploys, isolated secrets, callback URLs, and Lemon Squeezy test mode.

`/health` is intentionally public and exposes only `healthy` or `degraded`. `/health/operations` requires `X-Operations-Secret` and reports aggregate worker, queue, webhook, wallet, SMTP, database, and dependency signals without user or job details.

## Backups and recovery

MachReach uses the `machreach-db` PostgreSQL database hosted on Render. There is no app-managed external S3 backup workflow. Database recovery must use the recovery capabilities enabled for the Render database plan.

Target recovery objectives for the current product are **RPO 24 hours** and
**RTO 4 hours**, subject to the recovery capabilities actually enabled on the
Render database plan. Confirm the provider retention window in the Render
dashboard before paid launch. Exercise a downloaded provider backup against a
new, empty, non-production database with `scripts/restore_drill.ps1`; record the
backup timestamp, start/end time, migration result, row-count verification, and
achieved RPO/RTO. The script refuses targets whose database name does not
contain `restore` or `drill`.

## Release gate

Before production deploys:

1. CI must pass for SQLite, PostgreSQL critical paths, Python and Node audits, the landing rebuild, extension validation, and the complete Playwright browser matrix.
2. Deploy and exercise staging first. Confirm it uses only staging Postgres, provider test credentials, and staging callback URLs.
3. Apply the Blueprint with the same `ENCRYPTION_KEY` shared by each environment's web and worker services. Never share database or provider secrets across environments.
4. Confirm public `/health` is healthy, protected `/health/operations` has no failed signals, and the worker heartbeat is newer than two minutes.
5. Exercise staging login, queued AI work, test checkout/webhook, account export, and provider-first deletion before approving production.

Database migrations must remain backward-compatible for at least one release.

## Incident response

1. Assign an incident lead and record the start time, affected feature, and first known bad deploy or event.
2. Stop unsafe side effects first: suspend the worker for duplicate email or reward risk, disable checkout for billing drift, or block the affected route.
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
- Worker heartbeat older than two minutes.
- Oldest queued job over ten minutes or any job exhausting retries.
- Failed webhook, billing reconciliation drift, or coin order without a completed ledger entry.
- Outstanding partial-refund review or failed provider cancellation after a locally enforced full refund. After reconciling it in Lemon Squeezy, remove the corresponding `operational_events` row and record the action in the incident log.
- Repeated verification-email failures, SMTP failure, database saturation, and Sentry exception-rate spikes.
