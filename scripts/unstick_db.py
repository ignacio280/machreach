"""Show every open transaction and terminate the ones that are stuck.

A connection sitting "idle in transaction" holds every row lock it took,
forever. In this codebase that state is always a leak — a thread parked
mid-transaction on something that will never finish — and its locks brick
whatever they cover: one student's login, or a deploy whose migration queues
behind them. New connections defend themselves with
idle_in_transaction_session_timeout; this clears holders opened before that
setting existed.

Run it from the Render shell of the web service:

    python scripts/unstick_db.py            # list, then kill stuck > 5 min
    python scripts/unstick_db.py --dry-run  # only list, kill nothing

Only backends idle in a transaction older than five minutes are terminated.
Active queries, fresh transactions, and idle-without-transaction sessions are
never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so the app packages would not import without a PYTHONPATH dance.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machreach_core.db import _USE_PG, _fetchall, get_db  # noqa: E402


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    dry_run = "--dry-run" in argv
    min_age = "30 seconds" if "--min-age-test" in argv else "5 minutes"

    if not _USE_PG:
        print("SQLite has no server-side sessions to unstick.")
        return 0

    with get_db() as db:
        rows = _fetchall(db, """
            SELECT pid, application_name,
                   date_trunc('second', now() - xact_start) AS transaction_age,
                   state, wait_event_type, left(query, 70) AS last_query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND xact_start IS NOT NULL
              AND pid <> pg_backend_pid()
            ORDER BY xact_start
        """)
        if not rows:
            print("No open transactions besides this one. Nothing is stuck.")
            return 0
        print(f"{len(rows)} open transaction(s):")
        for row in rows:
            print("  " + "  ".join(f"{key}={row[key]}" for key in row))

        if dry_run:
            print("\n--dry-run: nothing terminated.")
            return 0

        killed = _fetchall(db, f"""
            SELECT pid, pg_terminate_backend(pid) AS terminated
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND state = 'idle in transaction'
              AND now() - xact_start > interval '{min_age}'
              AND pid <> pg_backend_pid()
        """)
    if killed:
        for row in killed:
            print(f"terminated stuck backend pid={row['pid']}: {row['terminated']}")
        print("Locks released. Retry the deploy / the login now.")
    else:
        print(f"\nNo backend was idle in transaction for over {min_age} — nothing terminated.")
        print("If a deploy still fails, its log names the real phase and error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
