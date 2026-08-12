"""Close out Lemon Squeezy webhook events that can never succeed on a retry.

`/health/operations` alerts on any `webhook_events` row left at `failed`, which
is right: a failed billing event usually means money moved and the product did
not follow. But some failures are terminal rather than pending. An event whose
account was deleted between the provider cancelling the subscription and the
provider sending the follow-up lifecycle event has nothing left to apply, and
the provider stops retrying long before anyone reads the alert. Those rows then
hold the health check degraded forever.

The webhook handler now settles such events itself, so only rows that failed
before that fix need this. Nothing is deleted: the row is moved to `abandoned`,
keeping the event key, the attempt count and the original error.

Run it from the Render shell of the web service (it reads DATABASE_URL from the
environment), or locally against the SQLite file:

    python scripts/resolve_failed_webhooks.py                  # show, change nothing
    python scripts/resolve_failed_webhooks.py --error ForeignKeyViolation --apply
    python scripts/resolve_failed_webhooks.py --event-key id:abc123 --apply

`--error` matches the stored `last_error` as a substring, so the crash class
name recorded by the request teardown is enough to select a family of rows.
Without `--error` or `--event-key` the script only lists, and refuses to apply:
closing every failed billing event at once is never the right move.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so the app packages would not import without a PYTHONPATH dance.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machreach_core.db import (  # noqa: E402
    _exec,
    _fetchall,
    get_db,
    record_operational_event,
)

PROVIDER = "lemonsqueezy"


def _failed_rows(db, *, error: str, event_key: str) -> list[dict]:
    sql = (
        "SELECT event_key, event_name, attempts, last_error, updated_at "
        "FROM webhook_events WHERE provider = %s AND status = 'failed'"
    )
    params: list[object] = [PROVIDER]
    if event_key:
        sql += " AND event_key = %s"
        params.append(event_key)
    if error:
        sql += " AND last_error LIKE %s"
        params.append(f"%{error}%")
    return [dict(row) for row in _fetchall(db, sql + " ORDER BY event_key", tuple(params))]


def _describe(rows: list[dict]) -> None:
    if not rows:
        print("No failed Lemon Squeezy webhook events match.")
        return
    print(f"{len(rows)} failed Lemon Squeezy webhook event(s):")
    for row in rows:
        print(
            f"  {row['event_key']:<50} {row['event_name'] or '(unnamed)':<32} "
            f"attempts={row['attempts']}  last_error={row['last_error'] or '(none)'!r}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move terminally failed Lemon Squeezy webhook events to 'abandoned'."
    )
    parser.add_argument(
        "--error", default="", help="only rows whose last_error contains this text"
    )
    parser.add_argument(
        "--event-key", default="", help="only the row with this exact event key"
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the change (otherwise only list)"
    )
    args = parser.parse_args(argv)

    with get_db() as db:
        rows = _failed_rows(db, error=args.error, event_key=args.event_key)
        _describe(rows)
        if not args.apply or not rows:
            if args.apply and not rows:
                return 0
            print("\nNothing written. Re-run with --apply to close these out.")
            return 0
        if not (args.error or args.event_key):
            print(
                "\nRefusing to abandon every failed billing event. Narrow it with "
                "--error or --event-key first."
            )
            return 1
        _exec(
            db,
            "UPDATE webhook_events SET status = 'abandoned' "
            "WHERE provider = %s AND event_key IN ("
            + ",".join(["%s"] * len(rows))
            + ")",
            (PROVIDER, *(row["event_key"] for row in rows)),
        )

    record_operational_event(
        "billing_webhook_abandoned", (args.event_key or args.error)[:80]
    )
    print(f"\nMoved {len(rows)} event(s) to 'abandoned'. /health/operations should clear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
