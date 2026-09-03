# MachReach production operations

## Availability and environments

The checked-in Render Blueprint defines one paid `starter` web service. The
background schedule runs inside it, in a daemon thread started by
gunicorn.conf.py's `post_fork` hook and guarded by a Postgres advisory lock so
only one process ever schedules. There is no separate worker service: Render
has no free tier for background workers, and at this product's size a second
paid service was a fixed cost for a process that was idle almost all the time.
`python worker.py` still runs the identical schedule standalone, so splitting
them again is adding the service back in the dashboard. Production deploys only after GitHub checks pass. A staging
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
3. Apply the Blueprint. Never share database or provider secrets across environments.
4. Confirm public `/health` is healthy, protected `/health/operations` has no
   failed signals, and the worker heartbeat is fresh — newer than two minutes
   normally, or than ninety with `DB_SLEEP_FRIENDLY=1`, which drops the
   heartbeat to hourly so the database can suspend. That heartbeat is written
   from inside the web service, so a stale one means the in-process scheduler
   did not start — check the deploy log for the `[gunicorn] background
   schedule` line.
5. In Lemon Squeezy, copy the production store ID plus the product and variant IDs for Plus and every enabled coin pack into the matching `LEMON_SQUEEZY_STORE_ID`, `LS_PRODUCT_*`, and `LS_VARIANT_*` Render variables. A signed event must match all three identifiers; checkout remains disabled when any required identifier is missing.
6. Exercise staging login, queued AI work, test checkout/webhook, account export, and provider-first deletion before approving production.

Database migrations must remain backward-compatible for at least one release.

## Incident response

1. Assign an incident lead and record the start time, affected feature, and first known bad deploy or event.
2. Stop unsafe side effects first. There is no worker service to suspend: set
   `MACHREACH_DISABLE_INPROCESS_SCHEDULER=1` on the web service and redeploy,
   which leaves the queue intact and every job to catch up later. For billing
   drift, disable checkout; for one bad route, block it.
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
- Worker heartbeat older than two minutes, or than ninety when
  `DB_SLEEP_FRIENDLY=1`; `/health/operations` already applies whichever
  threshold is in force, so alert on what it reports rather than on a clock of
  your own. This is the alert that the in-process scheduler failed to start,
  which is silent otherwise: the site serves every page normally while no
  background job runs at all.
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
