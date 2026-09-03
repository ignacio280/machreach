# Moving the database to Neon

The app keeps running on Render; only Postgres moves.

## Read this first if the target is Neon's free plan

The free plan gives 100 CU-hours a month, which is about **400 hours** of a
0.25 CU compute. A month is 730. So it only works if the compute is allowed to
suspend, and MachReach as shipped never lets it: the queue is polled every five
seconds, three jobs run every minute, and Render's own liveness check calls
`/health`, which opens a connection. With any of those in place the compute
never idles, the quota runs out around day 17, and Neon suspends the project —
existing connections drop and new ones cannot open, for the rest of the month.

Set `DB_SLEEP_FRIENDLY=1` and none of that happens: the queue drains on an
in-process signal instead of a poll, every periodic job drops to hourly, and
`/health` stops opening a connection. A database nobody is studying against is
asked nothing at all, and five active students land somewhere near 20 CU-hours
a month.

What it costs, so the choice is informed: work orphaned by something other than
a restart waits for the hourly sweep rather than a minute, the worker-heartbeat
alert widens from two minutes to ninety, `/health` reports that the process is
answering rather than that the database is reachable (`/health/operations`
still checks that), and the first request after a quiet spell pays the wake —
half a second to two.

Also check the size. The free plan stops accepting writes above 0.5 GB, and
`--check` below prints the current database size before anything is copied.

Leave `DB_SLEEP_FRIENDLY` unset on a database that bills by the month. There is
nothing to gain and a poll is the simpler thing. Nothing here is
automatic — the copy is one command, but the switch is a decision, so it is a
separate deliberate step and the old database is left intact behind it.

## Before you start

Two things about Neon differ from Render's Postgres and both fail quietly.
`migrate.py` refuses to run against the first one and `machreach_core.db`
absorbs the second, but they decide which connection string you should use, so
they come first.

**Use the direct endpoint, not the pooled one.** Neon's dashboard offers the
pooled string first. Its host contains `-pooler`:

    ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech   ← not this one
    ep-cool-darkness-123456.us-east-2.aws.neon.tech          ← this one

Behind the pooled host is PgBouncer in transaction mode, which gives each
statement whichever backend is free. Two things here need a *session*: the
advisory lock that stops the web service and the worker migrating at the same
time, and the `idle_in_transaction_session_timeout` the connection pool sets as
a startup option. Through a pooler the lock protects nothing and the timeout
may be rejected. `migrate.py` exits rather than start, so a wrong paste costs a
failed deploy instead of a mangled schema.

If you set the pooled string as `NEON_DATABASE_URL` anyway, the migration
script corrects it to the direct endpoint and tells you — there is nothing to
undo. `DATABASE_URL` at step 5 is the one that has to be right by hand.

The app opens at most `DB_POOL_MAX` (12) connections, all from the one web
service now that the background schedule runs inside it, which is comfortably
inside the direct endpoint's limit.

**Scale-to-zero.** Neon suspends an idle compute and wakes it on the next
connection, so the first connect after a quiet spell can be refused while the
wake is in progress. `get_db()` now retries that three times with a short
backoff (`DB_CONNECT_ATTEMPTS`, `DB_CONNECT_BACKOFF`) and bounds the connect at
`DB_CONNECT_TIMEOUT` seconds, which keeps a cold start inside the five seconds
Render allows `/health` before it kills the instance. On the free plan the
suspending is the point, so leave scale-to-zero on: turning it off removes the
wake latency and the quota along with it.

## 1. Create the Neon database

In the Neon console: a project in the region closest to Render's (Oregon for
`oregon-postgres.render.com`), the default Postgres version, one database. Copy
the **direct** connection string.

## 2. Give Render the string

Add `NEON_DATABASE_URL` to the **machreach** web service's environment in the
Render dashboard, set to that string. It is a form field, so nothing has to be
pasted into a terminal. Do not touch `DATABASE_URL` yet — that is the switch,
and it comes last.

Render will redeploy. That is fine: nothing reads `NEON_DATABASE_URL` except
the script below.

## 3. Copy the data

Open the Render shell on the **machreach** web service. `DATABASE_URL` and
`NEON_DATABASE_URL` are both already in the environment there, so the commands
carry no secrets and are short enough to type.

First look at what you are about to move. This writes nothing:

    python scripts/migrate_to_neon.py --check

Then copy it. **Use `pg_dump`, not `--run`:**

    pg_dump --no-owner --no-privileges -Fc "$DATABASE_URL" -f /tmp/machreach.dump       && pg_restore --no-owner --no-privileges --clean --if-exists            -d "$NEON_DATABASE_URL" /tmp/machreach.dump       && echo "COPIA COMPLETA"

`--run` will refuse this database, and it is right to. It builds the target
schema with the app's own `migrate.py` and then checks that every source table
exists on the target. Production carries about 38 tables `migrate.py` never
creates: `jr_*` and `pro_*` from sibling products, `training_*`, leftovers from
student features that were removed, and `product_analytics_events`, which the
app creates lazily at runtime and which is the largest table in the database.
The script promises a target identical to the source, so rather than copy part
of it, it stops. `pg_dump` has no such gap — it copies whatever is actually
there, schema and all.

Roughly how long: 23 MB across 98 tables copied in seconds.

## 4. Stop writes, then re-copy

Any row written between the copy and the switch exists only on the old
database. To lose nothing:

1. Put the web service in maintenance, or accept the few minutes of writes you
   are about to discard — for five students at night that is usually nothing.
   There is no worker service to suspend any more: the schedule runs inside the
   web service.
2. Run the same `pg_dump` / `pg_restore` line again.

`--clean --if-exists` is what makes this safe to repeat: it drops and rebuilds
every object, so nothing from the first copy survives into the second and the
second run is the authoritative one.

## 5. Switch

In the Render dashboard, on the **machreach** web service — there is only the
one now — set `DATABASE_URL` to the Neon direct string. In `render.yaml`
`DATABASE_URL` is declared as `fromDatabase`, so removing that block is what
lets a dashboard value stand:

```yaml
      - key: DATABASE_URL
        sync: false
```

Set `DB_SLEEP_FRIENDLY=1` in the same edit if the target is the free plan; the
first section explains why. Then deploy. `preDeployCommand` runs `migrate.py`,
which will refuse if the string is the pooled one — that refusal is the guard
working, not a problem with Neon.

Then check, in this order:

    /health
    /health/operations

and log in as a real account. With `DB_SLEEP_FRIENDLY=1`, `/health` answers
`{"status": "healthy", "database": "not_probed"}` — that is the endpoint doing
its narrowed job, not a failure. `/health/operations` is what proves the
database is reachable.

## 6. Afterwards

Leave the Render database running for a few days. It is the rollback: setting
`DATABASE_URL` back and redeploying returns the app to it, minus whatever was
written to Neon in the meantime. Delete it — and `NEON_DATABASE_URL`, and the
`databases:` block in `render.yaml` — only once you are sure.

## If something goes wrong

`scripts/migrate_to_neon.py --verify` compares row counts between the two
databases at any time and needs no downtime. A mismatch after the switch is
expected and grows: it is the new writes that only exist on Neon.

Nothing in this procedure writes to the source. `pg_dump` only reads, and the
one destructive step — `pg_restore --clean` — runs against the *target*, so a
failed copy costs the Neon side and never production.
