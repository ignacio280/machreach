# Moving production Postgres to Neon and the worker to a cron job

This is the runbook for the two infrastructure changes that took the Render
bill from three paid resources (web service, always-on worker, Postgres) to
one paid web service plus a per-second-billed cron job, with the database on
Neon. The code on this branch already expects the new topology; this document
is the operator's part.

Nothing here is reversible in one click. Read it once end to end before the
cutover window, and keep the Render database until the rollback window below
has passed.

## What changes for the product

- **Database**: Neon Postgres, reached through its *direct* connection string.
  The app keeps its own connection pool and `migrate.py` takes an advisory
  lock, and neither works through Neon's `-pooler` endpoint (PgBouncer in
  transaction mode). Neon suspends an idle compute after five minutes and
  drops every connection when it does; `machreach_core.db` now pings a pooled
  connection that sat idle for more than `DB_IDLE_PING_SECONDS` (30) before
  handing it to a request, so a student never sees the dropped socket.
- **Worker**: the same `worker.py`, run as `python worker.py --once` by a
  Render cron job every minute instead of an always-on instance. Each run
  executes every interval job once, fires any time-of-day job whose scheduled
  time (America/Santiago) has passed since the run recorded in the database,
  then drains queued student jobs for up to `WORKER_RUN_MAX_SECONDS` (50).
- **Latency a student can notice**: a queued quiz, flashcard deck, or
  verification email used to be picked up within five seconds. It now waits
  for the next run: up to a minute, plus the cron job's start-up. This is the
  one product-visible trade-off of the cheaper worker. If it matters more
  than the saving, the old always-on `type: worker` block is in git history.
- **Alerting**: `/health/operations` alerts when the heartbeat is older than
  `WORKER_HEARTBEAT_STALE_SECONDS`, set to 180 in `render.yaml` (a scheduled
  run can start late; the old two minutes assumed a beat every minute). A run
  beats once at start and every 30 seconds while it is busy.

## What could not be verified from this branch

The Render and Neon documentation were not reachable when this runbook was
written, so confirm these in the dashboards before the cutover:

1. **Neon Free plan compute allowance.** The web service's health checks and
   the per-minute cron keep the Neon compute awake around the clock, which at
   the smallest compute size (0.25 CU) is roughly 180 CU-hours a month. If the
   Free plan's monthly allowance is lower than that, production needs the paid
   Launch plan (a few dollars a month at the time of writing) rather than
   Free. Check the allowance on the Neon billing page and pick the plan
   accordingly; the total is still well under the old bill either way.
2. **Neon Free plan restore window.** Point-in-time restore on Free covers
   hours, not the days the old Render plan kept. See *Backups* below.
3. **Render cron schedule.** `render.yaml` uses `* * * * *` (every minute,
   UTC). If Render rejects it, use the smallest interval it accepts and raise
   `WORKER_HEARTBEAT_STALE_SECONDS` to three times that interval.
4. **Blueprint sync behaviour for the removed database and the changed worker
   type.** Read the sync preview before confirming it. Expect the database to
   be flagged as no longer managed (delete it by hand later), and the worker
   to be recreated as a new cron job, which will ask for its `sync: false`
   values.

## Prerequisites

- A Neon account and project in the same cloud region as the Render services
  (Render's default is Oregon, which is AWS `us-west-2` on Neon; check the
  region in the Render dashboard). Same region keeps every query's round trip
  in the low milliseconds.
- Postgres major version on Neon at least as new as the Render database's
  (visible in the Render database page).
- `pg_dump` and `pg_restore` on the laptop running the copy, of a major
  version at least as new as the Render database. The dump must never sit on
  a production instance's disk.
- The Render database's **external** connection string, and Neon's **direct**
  connection string (`...neon.tech/machreach?sslmode=require`, hostname
  without `-pooler`).
- `OPERATIONS_SECRET` at hand, to read `/health/operations`.

## Rehearsal (any time, no downtime)

1. Create the Neon database (`machreach`) and role, copy the direct URL.
2. Copy production into it and verify the row counts:

   ```bash
   python scripts/migrate_to_neon.py --source "$RENDER_EXTERNAL_URL" --target "$NEON_DIRECT_URL"
   ```

   The script refuses identical URLs, a pooled Neon endpoint, and a target
   that already has tables. It dumps, restores, runs `migrate.py` against the
   target, and prints a per-table count comparison. A non-zero exit means do
   not cut over.
3. Point a local checkout at Neon and exercise it:

   ```bash
   DATABASE_URL="$NEON_DIRECT_URL" python -c "import app; print('boot OK')"
   DATABASE_URL="$NEON_DIRECT_URL" RENDER=1 SECRET_KEY=... ENCRYPTION_KEY=... OPERATIONS_SECRET=... python worker.py --once
   ```

   The worker run prints a `[WORKER RUN]` summary line and exits 0. Its first
   run only seeds the daily schedule; nothing daily fires until its next
   scheduled time, exactly as the always-on worker behaved after a restart.
4. Empty the rehearsal target before the real cutover so the script's
   empty-target check passes again: on Neon, `DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;` in the SQL editor, or delete and recreate the
   database.

## Cutover (a quiet window; 03:00–04:00 America/Santiago is quietest)

Writes continue until the web service stops, so the copy is taken with the
service suspended. Budget fifteen minutes of downtime; the copy of a database
this size takes a minute or two.

1. In Render, **suspend** the `machreach` web service and the existing
   `machreach-worker` worker service. The public site returns an error page
   for the duration; `/health` monitoring will alert, which is expected.
2. Run the copy against the emptied Neon database and read the count table:

   ```bash
   python scripts/migrate_to_neon.py --source "$RENDER_EXTERNAL_URL" --target "$NEON_DIRECT_URL" --dump-file ./cutover.dump
   ```

   Keep `cutover.dump`: it is the last full copy of the Render database and
   the rollback's starting point.
3. In Render, set `DATABASE_URL` on the web service to the Neon direct URL and
   add `WORKER_HEARTBEAT_STALE_SECONDS=180`. **Resume** the web service. The
   deploy runs `migrate.py` against Neon (idempotent, already applied by the
   script) and then serves.
4. Verify before touching the worker:
   - `https://machreach.com/health` returns 200 `healthy`.
   - `POST /api/admin/check-db` (admin, debug-gated) reports `using_pg: true`
     and a `db_url_prefix` on `neon.tech`.
   - Log in as a test student; open the dashboard and one course.
5. Bring up the cron worker. Until the Blueprint sync below has created it,
   the fastest path is the existing worker service with its `DATABASE_URL`
   switched to Neon and resumed: it runs the same code and keeps the queue
   moving. Once the cron job exists, delete the old worker service.
6. Within three minutes, `GET /health/operations` (with the secret) must show
   `worker_heartbeat: ok` and no failed signals. `/admin/jobs` shows the last
   heartbeat and the four `worker_schedule` rows seeded by the first run.

## Blueprint sync (after the cutover verified)

Merge this branch. Render's Blueprint sync will show the removed
`machreach-db` database and the `machreach-worker` service changing from a
worker to a cron job. Confirm the preview matches that and nothing else.
When it asks for the cron job's `sync: false` values, paste the same
`DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, `ADMIN_ACTION_SECRET`,
`OPERATIONS_SECRET`, SMTP, OpenAI, Lemon Squeezy, Sentry, and
`LEADERBOARD_WINNERS_RECIPIENT` values the worker service had (the
`ENCRYPTION_KEY` must be identical to the web service's).

Watch the first few cron runs in the job's log: each ends with a
`[WORKER RUN] {...}` line. An idle run finishes in a few seconds.

## Rollback

For seven days after the cutover the Render database still exists and still
holds everything up to the moment the web service was suspended.

- **Within minutes of resuming, before real writes**: set `DATABASE_URL` back
  to the Render internal URL on both services and redeploy. Nothing is lost.
- **After students have written to Neon**: rolling back loses those writes
  unless they are copied back. Run the same script in the other direction
  (`--source "$NEON_DIRECT_URL" --target "$RENDER_EXTERNAL_URL" --force`
  after emptying the Render database), verify, then switch `DATABASE_URL`.

After the seven days, delete the Render database in the dashboard; it bills
until it is deleted, and the October target assumes it is gone by then.

## Backups on Neon

Neon keeps a point-in-time restore window per plan (hours on Free, days on
paid plans). The documented recovery objective is RPO 24 hours, so until a
plan with at least a day of history is chosen, take a `pg_dump` at least
daily from a laptop or a scheduled job outside Render and keep it encrypted:

```bash
pg_dump --format custom --no-owner --no-privileges --file "machreach-$(date +%F).dump" "$NEON_DIRECT_URL"
```

The restore drill (`scripts/restore_drill.ps1`) works unchanged against a
second Neon database whose name contains `drill`; a Neon branch of production
is also an easy drill target. Update `docs/operations.md`'s recorded RPO/RTO
after the first drill on Neon.

## Day-to-day differences

- **Migrations** run only in the web service's `preDeployCommand`. A cron run
  that starts on a stale schema fails `check_schema_readiness()` and retries
  a minute later; that is one red run in the log during a deploy, not an
  incident.
- **Shell scripts** (`scripts/grant_admin.py`, `diagnose_account.py`,
  `unstick_db.py`, ...) still run from the web service's shell; it has the
  Neon `DATABASE_URL`.
- **Neon usage**: watch storage against the plan's limit and compute hours
  on the Neon billing page for the first month; the compute stays awake
  around the clock because of health checks and the per-minute cron.
