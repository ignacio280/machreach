"""Run database migrations as a deployment phase, never in request workers."""

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


def migrate() -> None:
    # The web service's pre-deploy step runs this, and an operator may run it
    # from a shell (a restore drill, a database move) while a deploy is in
    # flight. A Postgres advisory lock serializes every caller without
    # coupling migration state to a particular service instance.
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
