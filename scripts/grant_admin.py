"""Grant or revoke administrator access for one account.

Admin is deliberately not something the app can hand out to itself: there is no
button anywhere in the product, because a bug in one would be a full account
takeover. It is a database change made by whoever holds the database, which is
what this script is.

Run it from the Render shell of the web service (it picks up DATABASE_URL from
the environment), or locally against the SQLite file:

    python scripts/grant_admin.py ignaciomachuca2005@gmail.com
    python scripts/grant_admin.py ignaciomachuca2005@gmail.com --revoke
    python scripts/grant_admin.py --list

The account must already exist — sign up normally first. On the next visit to
/admin the app forces enrolment in TOTP two-factor before anything opens.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so the app packages would not import without a PYTHONPATH dance.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machreach_core.db import _exec, _fetchall, _fetchone, get_db  # noqa: E402


def _find(db, email: str) -> dict | None:
    row = _fetchone(
        db,
        "SELECT id, name, email, is_admin, email_verified FROM clients "
        "WHERE LOWER(email) = LOWER(%s)",
        (email,),
    )
    return dict(row) if row else None


def list_admins() -> int:
    with get_db() as db:
        rows = _fetchall(db, "SELECT id, name, email FROM clients WHERE is_admin = 1 ORDER BY id", ())
    if not rows:
        print("No administrator accounts.")
        return 0
    print(f"{len(rows)} administrator account(s):")
    for row in rows:
        print(f"  #{row['id']:<5} {row['email']}  ({row['name']})")
    return 0


def set_admin(email: str, *, admin: bool) -> int:
    with get_db() as db:
        client = _find(db, email)
        if not client:
            print(f"No account with the email {email!r}. Sign up first, then run this again.")
            return 1
        already = bool(client.get("is_admin"))
        if already == admin:
            print(f"#{client['id']} {client['email']} is already "
                  f"{'an administrator' if admin else 'a normal account'}. Nothing to do.")
            return 0
        _exec(db, "UPDATE clients SET is_admin = %s WHERE id = %s",
              (1 if admin else 0, client["id"]))

    if not admin:
        print(f"Removed administrator access from #{client['id']} {client['email']}.")
        return 0
    print(f"#{client['id']} {client['email']} is now an administrator.")
    if not client.get("email_verified"):
        print("Note: this account has not verified its email yet, so it cannot log in.")
    print("Next: log in, open /admin, and complete the two-factor enrolment it asks for.")
    print("Then the owner dashboard is at /admin/growth.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke MachReach administrator access.")
    parser.add_argument("email", nargs="?", help="account email address")
    parser.add_argument("--revoke", action="store_true", help="remove administrator access instead")
    parser.add_argument("--list", action="store_true", help="list current administrators and exit")
    args = parser.parse_args(argv)

    if args.list:
        return list_admins()
    if not args.email:
        parser.error("give an email address, or --list")
    return set_admin(args.email, admin=not args.revoke)


if __name__ == "__main__":
    sys.exit(main())
