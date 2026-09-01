# Production on one Render service: Postgres on Neon, worker embedded

This is the chosen setup and the runbook for getting there. The Render bill
had three paid resources: the web service, an always-on worker, and a
Postgres database. This keeps only the web service. The database moves to
Neon's free tier, and the background worker runs inside the web service as a
scheduler thread pool (`EMBEDDED_WORKER=1`, started by `gunicorn.conf.py`
after the fork). `render.yaml` on this branch already describes that shape;
merging it is the last step of the cutover, not the first.

Nothing changes for a student: same code, same five-second job pickup, same
dashboard for you. Two alternatives stay documented in case this one ever
stops fitting: the worker as a per-minute Render cron job (*Appendix* at the
end) and a single server of your own ([VPS_DEPLOY.md](VPS_DEPLOY.md)).

## What changes for the product

- **Database**: Neon Postgres, reached through its *direct* connection string.
  The app keeps its own connection pool and `migrate.py` takes an advisory
  lock, and neither works through Neon's `-pooler` endpoint (PgBouncer in
  transaction mode). Neon suspends an idle compute after five minutes and
  drops every connection when it does; `machreach_core.db` pings a pooled
  connection that sat idle for more than `DB_IDLE_PING_SECONDS` (30) before
  handing it to a request, so a student never sees the dropped socket.
- **Worker**: the same `worker.py` jobs on an APScheduler background
  scheduler with four threads inside the gunicorn worker process. It starts
  in gunicorn's `post_fork` hook (never at import, so `--preload` stays
  safe) and stops in `worker_exit`, giving a job in flight up to a minute
  so a `--max-requests` recycle does not strand a quiz as "running". The
  time-of-day jobs (plan refresh, streak reminders, cleanups) use the
  database-backed schedule in every runtime, so a restart at 00:03 still
  runs the midnight job and two overlapping processes cannot both send a
  reminder.
- **Capacity**: the jobs mostly wait on the database, the AI provider, and
  SMTP, so they take little of the web instance's half CPU. `DB_POOL_MAX`
  goes to 16 for the 8 request threads plus the 4 scheduler threads.
- **Never two workers**: `EMBEDDED_WORKER=1` and a separate `python
  worker.py` process against the same database would each claim jobs
  (safely) but double the load. The blueprint has no worker service; do
  not add one back while the flag is set.

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
3. **Blueprint sync behaviour for the removed database and worker.** Read
   the sync preview before confirming it. Expect both to be flagged as no
   longer managed by the Blueprint rather than deleted; delete them by hand
   after the rollback window.

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
   python scripts/copy_database.py --source "$RENDER_EXTERNAL_URL" --target "$NEON_DIRECT_URL"
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
   python scripts/copy_database.py --source "$RENDER_EXTERNAL_URL" --target "$NEON_DIRECT_URL" --dump-file ./cutover.dump
   ```

   Keep `cutover.dump`: it is the last full copy of the Render database and
   the rollback's starting point.
3. Merge this branch into `master`. Render's Blueprint sync shows the
   removed `machreach-db` database and `machreach-worker` service and asks
   for the web service's new `DATABASE_URL`: paste the Neon direct URL.
   Confirm. The web service redeploys with `EMBEDDED_WORKER=1`: `migrate.py`
   runs against Neon in the pre-deploy step (idempotent, already applied by
   the script), then gunicorn starts and each worker process starts the job
   scheduler. **Resume** the web service if the sync did not.
4. Verify:
   - `https://machreach.com/health` returns 200 `healthy`.
   - `GET /health/operations` (with the secret) shows `worker_heartbeat: ok`
     within two minutes and no failed signals.
   - The service log shows `[EMBEDDED WORKER] started in pid ...`.
   - `POST /api/admin/check-db` (admin, debug-gated) reports `using_pg: true`
     and a `db_url_prefix` on `neon.tech`.
   - Log in as a test student, open a course, ask for a quiz: it starts
     within seconds.
5. The old worker service stays suspended. Delete it and the Render database
   after the rollback window below.

## Rollback

For seven days after the cutover the Render database still exists and still
holds everything up to the moment the web service was suspended.

- **Within minutes of resuming, before real writes**: revert the merge,
  resync the Blueprint so `DATABASE_URL` comes from the Render database
  again, and resume the worker service. Nothing is lost.
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

- **Migrations** run only in the web service's `preDeployCommand`, before
  the new code and its embedded worker start.
- **Shell scripts** (`scripts/grant_admin.py`, `diagnose_account.py`,
  `unstick_db.py`, ...) still run from the web service's shell; it has the
  Neon `DATABASE_URL`.
- **Neon usage**: watch storage against the plan's limit and compute hours
  on the Neon billing page for the first month; the compute stays awake
  around the clock because of health checks and the per-minute cron.

## Appendix: the worker as a Render cron job instead

If the embedded worker ever has to leave the web process (for example the
instance runs out of memory during a large plan refresh), the same jobs run
as a per-minute Render cron job billed per second: `python worker.py
--once` executes every interval job once, fires any due time-of-day job, and
drains the queue for up to `WORKER_RUN_MAX_SECONDS` (50). It costs about a
dollar a month more and a queued quiz waits up to a minute for the next run
instead of five seconds. Remove `EMBEDDED_WORKER` from the web service, set
`WORKER_HEARTBEAT_STALE_SECONDS=180` there (a scheduled run can start
late), and add:

```yaml
  - type: cron
    name: machreach-worker
    runtime: python
    plan: starter
    schedule: "* * * * *"          # UTC; jobs keep Santiago times internally
    autoDeployTrigger: checksPass
    buildCommand: pip install --require-hashes -r requirements.lock
    startCommand: python worker.py --once
    envVars:
      - key: PYTHON_VERSION
        value: "3.13.12"
      - key: PYTHONUNBUFFERED
        value: "1"
      - key: DATABASE_URL
        sync: false
      - key: WORKER_RUN_MAX_SECONDS
        value: "50"
      - key: DB_POOL_MAX
        value: "4"
      # ... plus the same secret keys the web service has, all sync: false
```

Cron jobs have no `preDeployCommand`; migrations run in the web service's
pre-deploy step, and a run that starts on a stale schema fails
`check_schema_readiness()` and retries a minute later.
