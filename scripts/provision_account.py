"""Create one account and mark its email verified, without sending mail.

Sign-up normally ends with a verification email, and the login route refuses an
account until that link is clicked. When the mail path is down — a Workspace
outage, a bounced domain, a student whose address simply never receives it —
that leaves a person who cannot get in and no button anywhere that fixes it.
This script is the way in: it writes the account and flips the verified flag
directly, which is a database change made by whoever holds the database.

Run it from the Render shell of the web service, which already has DATABASE_URL
in its environment, or locally against the SQLite file:

    python scripts/provision_account.py someone@example.com --name "Their Name"

The password is asked for interactively so it stays out of shell history, the
process list, and this file. To script it instead, put the password in an
environment variable and name it:

    MR_NEW_PASSWORD='...' python scripts/provision_account.py someone@example.com --name "Their Name" --password-env MR_NEW_PASSWORD

An address that already has an account is left alone: silently resetting a
password would be an account takeover if the address was ever typed wrong.
Pass --reset-password to change an existing account's password on purpose.

For an account that exists but is stuck unverified — they registered, the
verification email never arrived, and login refuses them — pass --verify to
flip only the verified flag, keeping the password they chose:

    python scripts/provision_account.py someone@example.com --verify

Marking verified also settles any stuck verification-email job for that
account, so /health/operations clears with it.

Whoever receives the account should change the password once they are in, since
anyone who handled it on the way here has seen it.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so the app packages would not import without a PYTHONPATH dance.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402

from machreach_core.db import (  # noqa: E402
    create_client,
    get_client_by_email,
    mark_email_verified,
    update_client_password,
)

# Read from the environment rather than importing app.py, which would pull the
# whole Flask application in to learn one number. Same default as the app.
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))


def _hash_pw(password: str) -> str:
    """Match the application's hashing exactly, cost included."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def _collect_password(password_env: str | None) -> str | None:
    if password_env:
        password = os.getenv(password_env)
        if not password:
            print(f"Environment variable {password_env} is empty or unset.")
            return None
        return password

    password = getpass.getpass("Password for the new account: ")
    if not password:
        print("No password given.")
        return None
    if password != getpass.getpass("Repeat it: "):
        print("The two passwords do not match.")
        return None
    return password


def verify_existing(email: str) -> int:
    """Mark an existing account's email verified, touching nothing else."""
    existing = get_client_by_email(email)
    if not existing:
        print(f"No account exists for {email}. To create one, run without --verify.")
        return 1
    if existing.get("email_verified"):
        print(f"#{existing['id']} {existing['email']} is already verified. Nothing changed.")
        return 0
    mark_email_verified(int(existing["id"]))
    print(f"Marked #{existing['id']} {existing['email']} verified — their password is untouched.")
    print("The login page will let this account through now.")
    return 0


def provision(email: str, name: str, password: str, *, reset_password: bool) -> int:
    existing = get_client_by_email(email)
    if existing and not reset_password:
        print(
            f"#{existing['id']} {existing['email']} already exists "
            f"({'verified' if existing.get('email_verified') else 'not verified'})."
        )
        print("Nothing changed. Use --reset-password to set a new password on it.")
        return 1

    if len(password) < PASSWORD_MIN_LENGTH:
        # Not fatal: whoever runs this has chosen the password deliberately. But
        # the sign-up form would have refused it, and so will the change and
        # reset screens, so this account cannot set the same password again.
        print(
            f"WARNING: this password is {len(password)} characters and the app's "
            f"minimum is {PASSWORD_MIN_LENGTH}. It will work for logging in, but "
            f"the change-password and reset-password screens will reject it."
        )

    if existing:
        client_id = int(existing["id"])
        # Bumping the session version signs out anything already holding a
        # session on this account, which is what a password change should do.
        update_client_password(client_id, _hash_pw(password), bump_session_version=True)
        print(f"Reset the password on #{client_id} {existing['email']}.")
    else:
        client_id = create_client(name, email, _hash_pw(password))
        print(f"Created #{client_id} {email} for {name!r}.")

    mark_email_verified(client_id)
    print("Email marked verified, so the login page will let this account through.")
    print("Next: log in at https://machreach.com/login and change the password.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a verified MachReach account without sending email."
    )
    parser.add_argument("email", help="account email address")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="the address already has an account: mark it verified and change nothing else",
    )
    parser.add_argument("--name", help="the person's display name (required unless --verify)")
    parser.add_argument(
        "--password-env",
        metavar="VAR",
        help="read the password from this environment variable instead of asking",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="the address already has an account: set a new password on it",
    )
    args = parser.parse_args(argv)

    email = args.email.strip()
    if "@" not in email:
        parser.error("that does not look like an email address")

    if args.verify:
        if args.reset_password or args.password_env:
            parser.error("--verify changes only the verified flag; drop the password options")
        return verify_existing(email)

    if not args.name:
        parser.error("--name is required when creating an account")

    password = _collect_password(args.password_env)
    if password is None:
        return 1

    return provision(
        email,
        args.name.strip(),
        password,
        reset_password=args.reset_password,
    )


if __name__ == "__main__":
    sys.exit(main())
