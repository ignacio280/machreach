# Moving the database to Neon

The app keeps running on Render; only Postgres moves. Nothing here is
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

The app opens at most `DB_POOL_MAX` (12) connections per service, 24 across
both, which is comfortably inside the direct endpoint's limit.

**Scale-to-zero.** Neon suspends an idle compute and wakes it on the next
connection, so the first connect after a quiet spell can be refused while the
wake is in progress. `get_db()` now retries that three times with a short
backoff (`DB_CONNECT_ATTEMPTS`, `DB_CONNECT_BACKOFF`) and bounds the connect at
`DB_CONNECT_TIMEOUT` seconds, which keeps a cold start inside the five seconds
Render allows `/health` before it kills the instance. The background worker
touches the database often enough that it should rarely suspend at all, but if
you see wake latency in the logs, turning scale-to-zero off on the Neon compute
removes the question entirely.

## 1. Create the Neon database

In the Neon console: a project in the region closest to Render's (Oregon for
`oregon-postgres.render.com`), Postgres 16 or 17, one database. Copy the
**direct** connection string.

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
carry no secrets and are short enough to type:

    python scripts/migrate_to_neon.py --check
    python scripts/migrate_to_neon.py --run

`--check` writes nothing and prints both databases, their sizes and every
non-empty table. Read it before running `--run`.

`--run` builds the schema on Neon with the app's own `migrate.py`, copies every
table in foreign-key order, moves the id sequences past the largest copied id,
and then counts every table on both sides. It prints `Every table matches.` or
it tells you which table did not. It is safe to run again: `--force` empties
the target and refills it.

Roughly how long: the copy is a `COPY` per table over one connection, so a
database of a few hundred MB is minutes, not hours.

## 4. Stop writes, then re-copy

Any row written between the copy and the switch exists only on the old
database. To lose nothing:

1. Suspend the **machreach-worker** service in Render (it writes on a schedule).
2. Put the web service in maintenance, or accept the few minutes of writes you
   are about to discard — for a student app at night this is usually nothing.
3. Re-run `python scripts/migrate_to_neon.py --run --force`.

`--force` is the whole point of this step: it re-copies from scratch, so the
second run is the authoritative one.

## 5. Switch

In the Render dashboard, on **both** the web service and the worker, set
`DATABASE_URL` to the Neon direct string. In `render.yaml` `DATABASE_URL` is
declared as `fromDatabase`, so removing that block is what lets a dashboard
value stand:

```yaml
      - key: DATABASE_URL
        sync: false
```

Deploy both. `preDeployCommand` runs `migrate.py`, which will refuse if the
string is the pooled one — that refusal is the guard working, not a problem
with Neon.

Then check, in this order:

    /health
    /health/operations

and log in as a real account.

## 6. Afterwards

Leave the Render database running for a few days. It is the rollback: setting
`DATABASE_URL` back and redeploying returns the app to it, minus whatever was
written to Neon in the meantime. Delete it — and `NEON_DATABASE_URL`, and the
`databases:` block in `render.yaml` — only once you are sure.

## If something goes wrong

`scripts/migrate_to_neon.py --verify` compares row counts between the two
databases at any time and needs no downtime. A mismatch after the switch is
expected and grows: it is the new writes that only exist on Neon.

The script never writes to the source. The one destructive statement in it is a
`TRUNCATE` of the *target*, so a failed run costs the Neon copy, never
production.
