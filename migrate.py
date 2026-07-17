"""Run database migrations as a deployment phase, never in request workers."""

from machreach_core.db import _USE_PG, _exec, check_schema_readiness, get_db, init_db
from student.db import init_student_db
from student.subscription import normalize_legacy_subscription_tiers


_MIGRATION_LOCK_ID = 4_624_686_952_405_553_101


def migrate() -> None:
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
            check_schema_readiness()
        finally:
            if _USE_PG:
                _exec(lock_db, "SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))


if __name__ == "__main__":
    migrate()
