"""Give a student back freezes or coins the streak walk spent on them.

Until the freezes_since fix, get_streak_days walked back over a student's whole
history and spent a freeze on every gap it found, so opening the dashboard
could empty a freshly bought wallet into months-old gaps. This puts back what
that took.

There is no ledger to reconstruct it from, and this script does not pretend
otherwise. Nothing records a freeze purchase (buy_streak_freeze only moves two
numbers), nothing records a consumption, and student_streak_freezes is UNIQUE
per ISO week with the insert swallowing the violation — so the extra freezes
the walk spent inside a week left no row at all. The amount owed comes from
the student, not from a query.

So: run it without an amount to see what the account looks like now, agree the
number with the student, then apply it.

    python scripts/refund_streak_freezes.py someone@example.com
    python scripts/refund_streak_freezes.py someone@example.com --freezes 3 --apply
    python scripts/refund_streak_freezes.py someone@example.com --coins 25 --apply

Freezes are capped at what the account may hold (3, or 5 on Plus); a request
over the cap is clamped and says so rather than silently vanishing. Restored
freezes are stamped as owned from today, so they protect from here forward and
cannot be spent on the days that were already lost. Every applied refund
records an operational event, so the audit trail exists even though the
original loss did not.
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
    _fetchone,
    get_client_by_email,
    get_db,
    record_operational_event,
)
from student import db as sdb  # noqa: E402


def _describe(client: dict) -> dict:
    """Print what the account holds now, so a human can judge the amount."""
    client_id = int(client["id"])
    wallet = sdb.get_wallet(client_id)
    with get_db() as db:
        since = _fetchval_since(db, client_id)
        weeks = _fetchall(
            db,
            "SELECT iso_year, iso_week, freeze_date FROM student_streak_freezes "
            "WHERE client_id = %s ORDER BY freeze_date DESC LIMIT 10",
            (client_id,),
        ) or []
    cap = sdb._streak_freeze_cap(client_id)

    print(f"#{client_id} {client.get('email')} — {client.get('name') or ''}")
    print(f"  coins:            {wallet['coins']}")
    print(f"  coin debt:        {wallet['coin_debt']}")
    print(f"  freezes:          {wallet['streak_freezes']}  (cap {cap})")
    print(f"  freezes since:    {since or '(unstamped — pre-fix stock)'}")
    print(f"  streak:           {sdb.get_streak_days(client_id)}")
    if weeks:
        print("  freeze weeks on record (most recent 10):")
        for row in weeks:
            print(f"    {str(row['freeze_date'])[:10]}  ISO {row['iso_year']}-W{row['iso_week']:02d}")
    else:
        print("  freeze weeks on record: none")
    print(
        "\n  Only one freeze per ISO week is ever recorded, so any extra ones the\n"
        "  walk spent inside a week are not in that list. Ask the student what\n"
        "  they lost; this cannot be recovered from the database."
    )
    return wallet


def _fetchval_since(db, client_id: int) -> str:
    row = _fetchone(
        db, "SELECT freezes_since FROM student_wallet WHERE client_id = %s", (client_id,)
    ) or {}
    return str(row.get("freezes_since") or "")


def refund(email: str, freezes: int, coins: int, apply: bool) -> int:
    client = get_client_by_email(email)
    if not client:
        print(f"No account for {email}.")
        return 1
    client_id = int(client["id"])
    wallet = _describe(client)

    if not freezes and not coins:
        print("\nNo amount given, so nothing to apply. Re-run with --freezes and/or --coins.")
        return 0

    cap = sdb._streak_freeze_cap(client_id)
    room = max(0, cap - int(wallet["streak_freezes"]))
    granted = min(freezes, room)
    if freezes and granted < freezes:
        print(
            f"\nNOTE: asked for {freezes} freezes but the account may hold {cap} "
            f"and already has {wallet['streak_freezes']}, so {granted} will be added."
            + (" Refund the rest as coins instead." if granted < freezes else "")
        )

    print(f"\nWould add: {granted} freeze(s), {coins} coin(s).")
    if not apply:
        print("Dry run — nothing written. Re-run with --apply to make it real.")
        return 0

    with get_db() as db:
        sdb._ensure_wallet(db, client_id)
        if granted:
            _exec(
                db,
                "UPDATE student_wallet SET streak_freezes = streak_freezes + %s "
                "WHERE client_id = %s",
                (granted, client_id),
            )
            # Owned from today: a restored freeze protects from here forward,
            # it does not reach back into the days that were already lost.
            sdb._mark_freeze_stock(db, client_id, sdb.user_date(client_id, db=db))
        if coins:
            sdb._credit_wallet_with_debt(db, client_id, int(coins))

    record_operational_event("streak_freeze_refund", f"client={client_id} f={granted} c={coins}")
    after = sdb.get_wallet(client_id)
    print(f"Done. coins {wallet['coins']} -> {after['coins']}, "
          f"freezes {wallet['streak_freezes']} -> {after['streak_freezes']}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Return streak freezes or coins to one student, with an audit trail."
    )
    parser.add_argument("email", help="the student's email address")
    parser.add_argument("--freezes", type=int, default=0, help="freezes to put back")
    parser.add_argument("--coins", type=int, default=0, help="coins to credit")
    parser.add_argument("--apply", action="store_true", help="write it (otherwise report only)")
    args = parser.parse_args(argv)

    if args.freezes < 0 or args.coins < 0:
        parser.error("amounts cannot be negative — this script only gives back")
    email = args.email.strip()
    if "@" not in email:
        parser.error("that does not look like an email address")

    return refund(email, args.freezes, args.coins, args.apply)


if __name__ == "__main__":
    sys.exit(main())
