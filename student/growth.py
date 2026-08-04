"""Owner-only growth reporting: who signed up, who they brought, who paid.

Everything here is read-only. The heavy lifting is a handful of aggregate
queries joined in Python rather than one clever SQL statement, because the app
runs on Postgres in production and SQLite locally and the two disagree about
almost every date function.

"Paid" deliberately means a real provider subscription. Referral weeks and
other promotional grants make an account behave like Plus, but they are not
revenue, so they are reported in their own column.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from machreach_core.db import _fetchall, get_db

# Statuses the provider can leave behind that still mean "this person is a
# paying customer right now" — mirrors subscription._effective_tier.
_LIVE_STATUSES = {"active", "on_trial", "trialing"}
_ENDING_STATUSES = {"cancelled", "canceled"}
_DUNNING_STATUSES = {"past_due", "unpaid"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_paying(row: dict, *, grace_days: int) -> bool:
    """Whether this subscription row is worth money today."""
    if str(row.get("tier") or "free") != "plus":
        return False
    status = str(row.get("status") or "active").lower()
    if status in _LIVE_STATUSES:
        return True
    if status in _ENDING_STATUSES:
        # Cancelled but already paid for the rest of the term.
        ends_at = _parse(row.get("ends_at"))
        return bool(ends_at and ends_at > _utcnow())
    if status in _DUNNING_STATUSES:
        failed_at = _parse(row.get("past_due_since") or row.get("updated_at"))
        return bool(failed_at and failed_at + timedelta(days=grace_days) > _utcnow())
    return False


def paying_client_ids() -> set[int]:
    """Client ids with a live paid subscription (promotional grants excluded)."""
    from student.subscription import PAYMENT_GRACE_DAYS

    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT client_id, tier, status, ends_at, past_due_since, updated_at "
            "FROM student_subscription_state",
            (),
        )
    return {
        int(row["client_id"]) for row in rows
        if _is_paying(dict(row), grace_days=PAYMENT_GRACE_DAYS)
    }


def promo_plus_client_ids() -> set[int]:
    """Client ids running on a promotional grant — referral weeks, mostly."""
    with get_db() as db:
        rows = _fetchall(db, "SELECT client_id, plus_until FROM student_promotional_entitlements", ())
    now = _utcnow()
    out = set()
    for row in rows:
        until = _parse(row.get("plus_until"))
        if until and until > now:
            out.add(int(row["client_id"]))
    return out


def user_rows() -> list[dict]:
    """One row per student account, richest inviters first.

    Each row carries the account's own state plus what its invite link has
    produced: how many people joined with it, how many of those verified their
    email, and how many of those are paying.
    """
    with get_db() as db:
        clients = _fetchall(
            db,
            "SELECT id, name, email, created_at, email_verified "
            "FROM clients WHERE COALESCE(account_type,'student') = 'student' "
            "ORDER BY id",
            (),
        )
        codes = _fetchall(db, "SELECT client_id, code FROM student_referral_codes", ())
        referrals = _fetchall(db, "SELECT referrer_id, referred_id FROM student_referrals", ())
        rewards = _fetchall(
            db,
            "SELECT referrer_id, status, days FROM student_referral_reward_audit",
            (),
        )
        xp_rows = _fetchall(
            db, "SELECT client_id, COALESCE(SUM(xp),0) AS xp FROM student_xp GROUP BY client_id", ()
        )

    paying = paying_client_ids()
    promo = promo_plus_client_ids()
    verified = {int(c["id"]) for c in clients if c.get("email_verified")}
    code_by_client = {int(r["client_id"]): str(r["code"]) for r in codes}
    xp_by_client = {int(r["client_id"]): int(r["xp"] or 0) for r in xp_rows}

    invited: dict[int, list[int]] = {}
    for row in referrals:
        invited.setdefault(int(row["referrer_id"]), []).append(int(row["referred_id"]))

    weeks_earned: dict[int, int] = {}
    weeks_withheld: dict[int, int] = {}
    for row in rewards:
        referrer = int(row["referrer_id"])
        bucket = weeks_earned if str(row.get("status")) == "granted" else weeks_withheld
        bucket[referrer] = bucket.get(referrer, 0) + 1

    out: list[dict] = []
    for client in clients:
        client_id = int(client["id"])
        joined = invited.get(client_id, [])
        out.append({
            "id": client_id,
            "name": str(client.get("name") or ""),
            "email": str(client.get("email") or ""),
            "created_at": str(client.get("created_at") or "")[:10],
            "verified": client_id in verified,
            "paying": client_id in paying,
            "promo_plus": client_id in promo,
            "xp": xp_by_client.get(client_id, 0),
            "referral_code": code_by_client.get(client_id, ""),
            "referred": len(joined),
            "referred_verified": sum(1 for cid in joined if cid in verified),
            "referred_paying": sum(1 for cid in joined if cid in paying),
            "weeks_earned": weeks_earned.get(client_id, 0),
            "weeks_withheld": weeks_withheld.get(client_id, 0),
        })
    # Best inviters first; then the people actually paying; then newest.
    out.sort(key=lambda r: (r["referred_paying"], r["referred"], r["paying"], r["id"]), reverse=True)
    return out


def summary(rows: list[dict] | None = None) -> dict:
    """Top-line numbers, derived from the same rows the table shows so the two
    can never disagree."""
    rows = user_rows() if rows is None else rows
    total = len(rows)
    verified = sum(1 for r in rows if r["verified"])
    paying = sum(1 for r in rows if r["paying"])
    promo = sum(1 for r in rows if r["promo_plus"] and not r["paying"])
    referred_total = sum(r["referred"] for r in rows)
    referred_paying = sum(r["referred_paying"] for r in rows)
    inviters = sum(1 for r in rows if r["referred"])

    def pct(part: int, whole: int) -> float:
        return round(part * 100 / whole, 1) if whole else 0.0

    return {
        "users": total,
        "verified": verified,
        "verified_pct": pct(verified, total),
        "paying": paying,
        "paying_pct": pct(paying, total),
        "promo_plus": promo,
        "inviters": inviters,
        "signups_from_referrals": referred_total,
        "referral_share_pct": pct(referred_total, total),
        "referred_paying": referred_paying,
        # Does the referral channel actually convert better than the rest?
        "referred_conversion_pct": pct(referred_paying, referred_total),
        "overall_conversion_pct": pct(paying, total),
    }


def signups_by_day(days: int = 30) -> list[dict]:
    """Signups per day for the last `days` days, oldest first, with no gaps."""
    with get_db() as db:
        rows = _fetchall(db, "SELECT created_at FROM clients "
                             "WHERE COALESCE(account_type,'student') = 'student'", ())
    counts: dict[str, int] = {}
    for row in rows:
        day = str(row.get("created_at") or "")[:10]
        if day:
            counts[day] = counts.get(day, 0) + 1
    today = _utcnow().date()
    out = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        out.append({"day": day, "signups": counts.get(day, 0)})
    return out
