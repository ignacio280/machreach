"""Run database migrations as a deployment phase, never in request workers."""

from urllib.parse import urlsplit

from machreach_core.config import DATABASE_URL
from machreach_core.db import (
    _USE_PG,
    _exec,
    check_schema_readiness,
    get_db,
    init_db,
    settle_unmatchable_order_events,
)
from student.db import drop_retired_boosts_table, init_student_db
from student.subscription import normalize_legacy_subscription_tiers


_MIGRATION_LOCK_ID = 4_624_686_952_405_553_101


def _refuse_transaction_pooler() -> None:
    """Migrations must not run through a transaction pooler.

    Neon shows the *pooled* connection string first, so it is the one that
    naturally gets pasted into DATABASE_URL. Behind it is PgBouncer in
    transaction mode, which hands each statement whichever backend is free.
    The lock below is a **session** lock: taken on one backend and released
    from another, it protects nothing, and the two services deploying together
    would run init_db() concurrently — the exact race the lock exists to stop.
    It fails silently, as a mangled schema rather than an error, so this
    refuses to start instead of finding out afterwards.

    The app should use the direct endpoint too: the pool sets
    idle_in_transaction_session_timeout as a startup option, which a pooler is
    free to reject. Drop the "-pooler" from the host and everything works.
    """
    host = (urlsplit(DATABASE_URL).hostname or "") if DATABASE_URL else ""
    if "-pooler." in host:
        raise SystemExit(
            f"DATABASE_URL points at a transaction pooler ({host}).\n"
            "Migrations take a session-level advisory lock, which a pooler "
            "silently breaks. Use the direct endpoint — the same host without "
            "'-pooler' — for both services."
        )


def migrate() -> None:
    _refuse_transaction_pooler()
    # Both Render services may deploy together. A Postgres advisory lock makes
    # their pre-deploy commands serialize without coupling migration state to a
    # particular service instance.
    with get_db() as lock_db:
        if _USE_PG:
            _exec(lock_db, "SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
        try:
            init_db()
            init_student_db()
            normalize_legacy_subscription_tiers()
            # After init_student_db, which no longer creates this table.
            drop_retired_boosts_table()
            settled = settle_unmatchable_order_events()
            if settled:
                print(f"[migrate] settled {settled} unmatchable order event(s)", flush=True)
            check_schema_readiness()
        finally:
            if _USE_PG:
                _exec(lock_db, "SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))


if __name__ == "__main__":
    migrate()
