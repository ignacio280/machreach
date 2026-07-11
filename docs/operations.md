# MachReach operations runbook

## Release gate

Before production deploys:

1. CI must pass for SQLite, PostgreSQL critical paths, Python/Node audits, the landing rebuild, and extension validation.
2. Apply the Blueprint with the same `ENCRYPTION_KEY` exposed to web and worker.
3. Confirm `/health` reports the expected core and student schema versions.
4. Confirm the worker heartbeat in `/admin/jobs` is newer than two minutes.
5. Exercise a staging login, Canvas import, queued AI job, sandbox checkout/webhook, data export, and provider-first deletion.

## Backup and restore drill

- Keep provider-managed PostgreSQL backups enabled with retention appropriate to the current plan.
- Before migrations or destructive maintenance, create an on-demand database snapshot.
- Quarterly, restore the latest backup into an isolated database and point a staging web/worker pair at it.
- Verify row counts for `clients`, `student_courses`, `student_study_progress`, `student_wallet`, `subscriptions`, `webhook_events`, and `sent_emails`; then run `/health` and the staging smoke journeys.
- Record the snapshot timestamp, restore duration, verification results, and operator. Never test a restore over production.

## Incident response

1. Assign an incident lead and record the start time, affected feature, and first known bad deploy/event.
2. Stop the unsafe side effect first: suspend the worker for duplicate email/reward risk, disable checkout for billing drift, or block the affected route.
3. Preserve Sentry events, worker logs, webhook ledger rows, async-job state, and relevant database snapshots. Do not copy secrets or recipient content into the incident document.
4. Roll back the application when the previous code is schema-compatible. Otherwise deploy a forward fix.
5. Reconcile provider state for subscriptions and coin orders; quarantine `delivery_unknown` email rows for manual review.
6. Communicate impact and resolution to affected users, then document cause, detection gap, corrective action, and owner.

## Rollback

- Use the hosting provider's previous successful deploy when no irreversible schema change was made.
- Database changes must remain backward-compatible for at least one release. Add columns/tables first; remove them only after old code is no longer deployed and a verified backup exists.
- After rollback, verify `/health`, worker heartbeat, queued-job age, webhook failures, and one read-only student journey.

## Data retention and deletion

- Account deletion is provider-first: recurring subscriptions must be cancelled successfully before local records are removed.
- Data exports are generated on demand and are not retained by MachReach after the response finishes.
- Failed webhook records retain only event metadata/error category needed for reconciliation; do not store secrets in error fields.
- `delivery_unknown` email rows require manual disposition because automatically retrying an uncertain SMTP result can duplicate mail.
- Define and approve concrete production retention periods with legal counsel before general availability, then automate them and disclose them in the privacy policy.

## Required alerts

- `/health` non-200 or schema version mismatch.
- Worker heartbeat older than two minutes.
- Oldest queued job over ten minutes or any job exhausting retries.
- Any webhook failure, billing reconciliation drift, or coin order without a completed ledger entry.
- Repeated student-email failures, stale AI jobs, database saturation, or backup failure.
