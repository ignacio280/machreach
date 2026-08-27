"""Explain why one specific account misbehaves when every other one works.

Run it from the Render shell of the web service against the account's email:

    python scripts/diagnose_account.py someone@example.com

It prints the shape of the account (flags, hash format, academic state, job
states, row counts — never the password hash itself or any content), then runs
the same per-account steps a login and a dashboard render would run, printing
each step BEFORE executing it and its duration after. If the process stalls,
the last "-> running:" line on screen names the exact step that hangs, and
after 30 stuck seconds faulthandler prints the Python stack so the guilty
frame is in the output too.

Read-mostly: the streak step can persist auto-applied freezes, exactly as the
real dashboard render would.
"""
from __future__ import annotations

import faulthandler
import sys
import time
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so the app packages would not import without a PYTHONPATH dance.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machreach_core.db import _fetchall, _fetchval, get_client_by_email, get_db  # noqa: E402


def _step(name, fn):
    print(f"-> running: {name}", flush=True)
    start = time.monotonic()
    try:
        value = fn()
    except Exception as exc:
        print(f"   FAILED after {time.monotonic() - start:.2f}s: {type(exc).__name__}: {exc}", flush=True)
        return None
    print(f"   ok in {time.monotonic() - start:.2f}s: {value}", flush=True)
    return value


def _hash_shape(stored) -> str:
    if stored is None:
        return "MISSING (None) — _verify_pw would crash on this"
    stored = str(stored)
    if stored.startswith(("$2b$", "$2a$")):
        return f"bcrypt cost {stored[4:6]} (len {len(stored)})"
    if len(stored) == 64:
        return "legacy sha256 (would upgrade on next successful login)"
    return f"UNRECOGNISED format (len {len(stored)}) — login would treat it as legacy sha256"


def _count(sql: str, params) -> int:
    with get_db() as db:
        return int(_fetchval(db, sql, params) or 0)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1 or "@" not in argv[0]:
        print("usage: python scripts/diagnose_account.py someone@example.com")
        return 2
    email = argv[0].strip()
    faulthandler.dump_traceback_later(30, repeat=True)

    client = get_client_by_email(email)
    if not client:
        print(f"No account found for {email} — login would say invalid credentials.")
        return 1
    cid = int(client["id"])

    print(f"account #{cid} {client.get('email')}")
    print(f"  name:                    {client.get('name')!r}")
    print(f"  account_type:            {client.get('account_type')!r}")
    print(f"  email_verified:          {client.get('email_verified')!r}"
          + ("   <- login bounces to /verify-email-pending" if not client.get("email_verified") else ""))
    print(f"  is_admin:                {client.get('is_admin')!r}"
          + ("   <- login goes to /admin MFA, not /student" if client.get("is_admin") else ""))
    print(f"  retired:                 {client.get('retired')!r}")
    print(f"  session_version:         {client.get('session_version')!r}")
    print(f"  academic_setup_complete: {client.get('academic_setup_complete')!r}")
    print(f"  university/major ids:    {client.get('university_id')!r} / {client.get('major_id')!r}")
    print(f"  created_at:              {client.get('created_at')!r}")
    print(f"  password hash:           {_hash_shape(client.get('password'))}")

    with get_db() as db:
        jobs = _fetchall(
            db,
            "SELECT job_type, status, attempts, max_attempts, error FROM async_jobs WHERE job_key = %s",
            (str(cid),),
        ) or []
    for job in jobs:
        print(f"  job {job['job_type']}: {job['status']}"
              f" (attempts {job.get('attempts')}/{job.get('max_attempts')}, error {str(job.get('error') or '')[:80]!r})")
    if not jobs:
        print("  jobs: none")

    for label, sql in [
        ("xp rows", "SELECT COUNT(*) FROM student_xp WHERE client_id = %s"),
        ("courses", "SELECT COUNT(*) FROM student_courses WHERE client_id = %s"),
        ("friends", "SELECT COUNT(*) FROM student_friends WHERE client_id = %s"),
        ("study progress rows", "SELECT COUNT(*) FROM student_study_progress WHERE client_id = %s"),
        ("streak freezes", "SELECT COUNT(*) FROM student_streak_freezes WHERE client_id = %s"),
        ("duplicate accounts on this email",
         "SELECT COUNT(*) FROM clients WHERE LOWER(email) = LOWER(%s)"),
    ]:
        params = (email,) if "email" in sql else (cid,)
        try:
            print(f"  {label}: {_count(sql, params)}")
        except Exception as exc:
            print(f"  {label}: query failed ({type(exc).__name__})")

    print("\nNow the per-account steps a login + dashboard render runs, in order.")
    print("If this output stops, the last '-> running:' line is the hang.\n")

    from student import db as sdb
    from student import academic as ac
    from student.periods import get_user_timezone, user_date

    _step("timezone", lambda: get_user_timezone(cid))
    _step("user_date (calendar day in their timezone)", lambda: user_date(cid))
    total_xp = _step("total XP", lambda: sdb.get_total_xp(cid)) or 0
    _step("level from XP", lambda: sdb.get_level(int(total_xp)))
    _step("streak (walks days backward, can persist freezes)", lambda: sdb.get_streak_days(cid))
    _step("wallet", lambda: (sdb.get_wallet(cid) or {}).get("coins"))
    rank = _step("weekly global rank", lambda: (ac.my_rank("global", cid, period="week") or {}).get("rank"))
    fetch_limit = max(5, int(rank or 0) + 3)
    _step(
        f"weekly leaderboard fetch at their rank (limit {fetch_limit} — dashboard does exactly this)",
        lambda: len(ac.leaderboard("global", cid, limit=fetch_limit, period="week") or []),
    )
    _step("friends list", lambda: {k: len(v) for k, v in (sdb.list_friends(cid) or {}).items()})
    _step("courses", lambda: len(sdb.get_courses(cid) or []))
    _step("friend leaderboard", lambda: len(sdb.get_friend_leaderboard(cid) or []))

    faulthandler.cancel_dump_traceback_later()
    print("\nEvery step returned. If login still fails for this account, the problem")
    print("is not in the dashboard data — capture the URL and status the browser is")
    print("stuck on and check Sentry for this client id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
