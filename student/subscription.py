"""Subscription tier helpers for the Student app.

Tiers:
  - free      : 1 quiz/day (max 30 q), 1 flashcard set/day (max 30 c).
  - plus      : 100 combined quiz/flashcard generations per billing month,
                analytics, streak protection and cosmetics.

Provider state is stored in `student_subscription_state`; promotional access
is stored separately in `student_promotional_entitlements`. Legacy
`mail_preferences` values are read only as a migration fallback.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from machreach_core.db import _USE_PG, get_db
from student.periods import (
    day_window_utc,
    month_key,
    month_window_utc,
    user_date,
    utc_now,
)

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return utc_now()

# Legacy flag kept for back-compat with any references elsewhere; tier limits
# now always enforce regardless of value.
BETA_ACTIVE = False

# ── Plan definitions ───────────────────────────────────────────────────

FREE_DAILY_QUIZZES        = 1
FREE_QUIZ_MAX_QUESTIONS   = 30
FREE_DAILY_FLASHCARD_SETS = 1
FREE_FLASHCARD_MAX_CARDS  = 30
PLUS_MONTHLY_STREAK_FREEZES = 1
PLUS_MONTHLY_GENERATIONS = 100
PAYMENT_GRACE_DAYS = 7

PLANS = {
    "free": {
        "key": "free",
        "name": "Gratis",
        "price_clp_month": 0,
        "price_clp_year": 0,
        "blurb": "Empieza con Focus, Canvas, cursos y herramientas IA limitadas.",
        "features": [
            "Canvas, cursos, Focus, XP, monedas y rachas",
            "Planilla de notas, ranking, amigos y tienda",
            f"{FREE_DAILY_QUIZZES} quiz IA / día (hasta {FREE_QUIZ_MAX_QUESTIONS} preguntas)",
            f"{FREE_DAILY_FLASHCARD_SETS} mazo de tarjetas IA / día (hasta {FREE_FLASHCARD_MAX_CARDS} tarjetas)",
            "Hasta 3 congeladores de racha guardados",
        ],
    },
    "plus": {
        "key": "plus",
        "name": "Plus",
        "price_clp_month": 4990,
        "price_clp_year": 59880,
        "blurb": "Para estudiantes que usan MachReach todas las semanas.",
        "features": [
            f"{PLUS_MONTHLY_GENERATIONS} generaciones IA combinadas por mes",
            "Uso compartido entre quizzes y flashcards",
            "Más preguntas/tarjetas por generación",
            "Plan de estudio inteligente semanal",
            "Explicaciones y análisis de debilidades en quizzes",
            "Analítica avanzada por curso y semana",
            "Benchmarks de ramos: nota y horas promedio",
            "Streak Insurance+: 1 reparación de racha al mes",
            "Más capacidad de congeladores: hasta 5 guardados",
            "Banners, flags e insignias exclusivas PLUS",
        ],
    },
}
PLAN_ORDER = ["free", "plus"]


# ── Read / write tier ───────────────────────────────────────────────────

def _load_prefs(db, client_id: int) -> dict:
    from machreach_core.db import _fetchone
    row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (client_id,))
    raw = (row or {}).get("mail_preferences") or ""
    try:
        loaded = json.loads(raw) if raw else {}
        prefs = loaded if isinstance(loaded, dict) else {}
    except Exception:
        prefs = {}
    try:
        normalized = _fetchone(
            db,
            "SELECT * FROM student_subscription_state WHERE client_id = %s",
            (client_id,),
        )
        if normalized:
            prefs["subscription"] = _subscription_from_row(normalized)
        entitlement = _fetchone(
            db,
            "SELECT plus_until FROM student_promotional_entitlements "
            "WHERE client_id = %s",
            (client_id,),
        )
        if entitlement and entitlement.get("plus_until"):
            value = entitlement["plus_until"]
            prefs["plus_until"] = (
                value.isoformat() if isinstance(value, datetime) else str(value)
            )
    except Exception:
        # Falling through leaves prefs without subscription/plus_until, which
        # reads downstream as "not a Plus member". A paying student losing
        # access must not be an invisible event.
        log.warning(
            "failed to load subscription state for client %s; "
            "entitlement checks will see no Plus access", client_id, exc_info=True,
        )
    return prefs


def _save_prefs(db, client_id: int, prefs: dict) -> None:
    from machreach_core.db import _exec
    subscription = prefs.get("subscription")
    plus_until = prefs.get("plus_until") if "plus_until" in prefs else None
    legacy_prefs = {
        key: value
        for key, value in prefs.items()
        if key
        not in {
            "subscription",
            "plus_until",
            "pending_ref",
            "last_free_freeze_grant",
            "plus_streak_insurance_month",
        }
    }
    _exec(
        db,
        "UPDATE clients SET mail_preferences = %s WHERE id = %s",
        (json.dumps(legacy_prefs), client_id),
    )
    if isinstance(subscription, dict):
        _write_subscription_row(db, client_id, subscription)
    if "plus_until" in prefs:
        _write_promotional_entitlement(db, client_id, plus_until)


def _subscription_from_row(row: dict | None) -> dict:
    if not row:
        return {}
    mapping = {
        "tier": "tier",
        "status": "status",
        "ls_sub_id": "ls_sub_id",
        "since_at": "since",
        "ends_at": "ends_at",
        "renews_at": "renews_at",
        "quota_period_start": "quota_period_start",
        "quota_reset_at": "quota_reset_at",
        "past_due_since": "past_due_since",
        "update_payment_method_url": "update_payment_method_url",
        "customer_portal_url": "customer_portal_url",
        "provider_event_at": "provider_event_at",
        "updated_at": "updated_at",
    }
    result: dict = {}
    for column, key in mapping.items():
        value = row.get(column)
        if value not in (None, ""):
            result[key] = value.isoformat() if isinstance(value, datetime) else str(value)
    return result


def _load_subscription_row(db, client_id: int) -> dict:
    from machreach_core.db import _fetchone

    row = _fetchone(
        db,
        "SELECT * FROM student_subscription_state WHERE client_id = %s",
        (client_id,),
    )
    if row:
        return _subscription_from_row(row)
    legacy = _load_prefs(db, client_id).get("subscription")
    if isinstance(legacy, dict):
        _write_subscription_row(db, client_id, legacy)
        return dict(legacy)
    return {}


def _write_subscription_row(db, client_id: int, state: dict) -> None:
    from machreach_core.db import _exec

    def nullable(value):
        return value if value not in (None, "") else None

    values = (
        client_id,
        state.get("tier") or "free",
        state.get("status") or "inactive",
        nullable(state.get("ls_sub_id")),
        nullable(state.get("since")),
        nullable(state.get("ends_at")),
        nullable(state.get("renews_at")),
        nullable(state.get("quota_period_start")),
        nullable(state.get("quota_reset_at")),
        nullable(state.get("past_due_since")),
        state.get("update_payment_method_url") or "",
        state.get("customer_portal_url") or "",
        nullable(state.get("provider_event_at")),
        state.get("updated_at") or _utcnow().isoformat(),
    )
    insert = (
        "INSERT INTO student_subscription_state "
        "(client_id, tier, status, ls_sub_id, since_at, ends_at, renews_at, "
        "quota_period_start, quota_reset_at, past_due_since, "
        "update_payment_method_url, customer_portal_url, provider_event_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    )
    update = (
        "tier=excluded.tier, status=excluded.status, ls_sub_id=excluded.ls_sub_id, "
        "since_at=excluded.since_at, ends_at=excluded.ends_at, "
        "renews_at=excluded.renews_at, quota_period_start=excluded.quota_period_start, "
        "quota_reset_at=excluded.quota_reset_at, past_due_since=excluded.past_due_since, "
        "update_payment_method_url=excluded.update_payment_method_url, "
        "customer_portal_url=excluded.customer_portal_url, "
        "provider_event_at=excluded.provider_event_at, updated_at=excluded.updated_at"
    )
    conflict = "ON CONFLICT(client_id) DO UPDATE SET " + update
    _exec(db, insert + conflict, values)


def _load_promotional_entitlement(db, client_id: int) -> str | None:
    from machreach_core.db import _fetchval

    value = _fetchval(
        db,
        "SELECT plus_until FROM student_promotional_entitlements "
        "WHERE client_id = %s",
        (client_id,),
    )
    if value:
        return value.isoformat() if isinstance(value, datetime) else str(value)
    legacy = _load_prefs(db, client_id).get("plus_until")
    if legacy:
        _write_promotional_entitlement(db, client_id, legacy)
        return str(legacy)
    return None


def _write_promotional_entitlement(
    db, client_id: int, plus_until: str | datetime | None
) -> None:
    from machreach_core.db import _exec

    _exec(
        db,
        "INSERT INTO student_promotional_entitlements "
        "(client_id, plus_until, updated_at) VALUES (%s, %s, %s) "
        "ON CONFLICT(client_id) DO UPDATE SET plus_until=excluded.plus_until, "
        "updated_at=excluded.updated_at",
        (client_id, plus_until, _utcnow().isoformat()),
    )


# ── Promotional time while a paid plan is running ───────────────────────
#
# Free weeks (referral rewards) are stored as an absolute `plus_until`, which
# burns in real time. That is wrong the moment the student also pays: the
# weeks they earned would be spent underneath a month they already bought.
#
# So promotional time has two states, and at most one is live:
#   running  — `plus_until` is set, the clock is ticking
#   banked   — `banked_seconds` holds what is left, nothing is ticking
#
# Paid access starting banks the remainder; paid access stopping pays it back
# out from that instant. Nothing is ever lost, and nothing is ever spent twice.


def _paid_only_tier(subscription: dict) -> str:
    """The tier the paid subscription grants on its own, promo ignored."""
    return _effective_tier({"subscription": subscription, "plus_until": None})


def _load_promotional_bank(db, client_id: int) -> int:
    from machreach_core.db import _fetchval

    try:
        return max(0, int(_fetchval(
            db,
            "SELECT banked_seconds FROM student_promotional_entitlements "
            "WHERE client_id = %s",
            (client_id,),
        ) or 0))
    except Exception:
        # Pre-migration deployments have no column; treat it as an empty bank
        # rather than failing a subscription write over a promotion.
        return 0


def _write_promotional_bank(db, client_id: int, seconds: int) -> None:
    from machreach_core.db import _exec

    _exec(
        db,
        "UPDATE student_promotional_entitlements SET banked_seconds = %s, "
        "updated_at = %s WHERE client_id = %s",
        (max(0, int(seconds)), _utcnow().isoformat(), client_id),
    )


def _reconcile_promotional_bank(db, client_id: int, subscription: dict) -> str | None:
    """Move promotional time between running and banked, and report what is
    live now. Safe to call on every read: it only writes on a real transition.
    """
    plus_until_raw = _load_promotional_entitlement(db, client_id)
    try:
        bank = _load_promotional_bank(db, client_id)
    except Exception:
        return plus_until_raw
    now = _utcnow()
    paid_active = _paid_only_tier(subscription) == "plus"
    plus_until = _parse_iso(plus_until_raw)

    if paid_active:
        # Paid covers the student today: park whatever promotional time is
        # still ahead of them so it is there when the plan stops.
        if plus_until and plus_until > now:
            remaining = int((plus_until - now).total_seconds())
            if remaining > 0:
                try:
                    _write_promotional_entitlement(db, client_id, None)
                    _write_promotional_bank(db, client_id, bank + remaining)
                except Exception:
                    log.warning("could not bank promotional time for %s", client_id)
                    return plus_until_raw
            return None
        return None if bank else plus_until_raw

    # No paid access. Anything banked starts running from this moment.
    if bank > 0:
        resumed = (now + timedelta(seconds=bank)).isoformat()
        try:
            _write_promotional_entitlement(db, client_id, resumed)
            _write_promotional_bank(db, client_id, 0)
        except Exception:
            log.warning("could not resume promotional time for %s", client_id)
            return plus_until_raw
        return resumed
    return plus_until_raw


def promotional_summary(client_id: int) -> dict:
    """What the student still holds in free weeks, running or banked."""
    try:
        with get_db() as db:
            subscription = _load_subscription_row(db, client_id)
            plus_until = _reconcile_promotional_bank(db, client_id, subscription)
            bank = _load_promotional_bank(db, client_id)
    except Exception:
        return {"days_left": 0, "banked": False, "plus_until": None}
    now = _utcnow()
    if bank > 0:
        return {
            "days_left": max(0, round(bank / 86400)),
            "banked": True,
            "plus_until": None,
        }
    parsed = _parse_iso(plus_until)
    if parsed and parsed > now:
        return {
            "days_left": max(0, round((parsed - now).total_seconds() / 86400)),
            "banked": False,
            "plus_until": plus_until,
        }
    return {"days_left": 0, "banked": False, "plus_until": None}


def _extend_promotional_entitlement(db, client_id: int, days: int) -> str:
    """Extend a promotion while holding its row lock in this transaction.

    A reward earned while a paid plan is running goes straight to the bank —
    otherwise those days would tick away under a month the student paid for.
    """
    from machreach_core.db import _exec, _fetchone

    now = _utcnow()
    _exec(
        db,
        "INSERT INTO student_promotional_entitlements "
        "(client_id, plus_until, updated_at) VALUES (%s, NULL, %s) "
        "ON CONFLICT(client_id) DO NOTHING",
        (client_id, now.isoformat()),
    )
    row = _fetchone(
        db,
        "SELECT plus_until FROM student_promotional_entitlements "
        "WHERE client_id = %s" + (" FOR UPDATE" if _USE_PG else ""),
        (client_id,),
    ) or {}
    if _paid_only_tier(_load_subscription_row(db, client_id)) == "plus":
        bank = _load_promotional_bank(db, client_id) + int(days) * 86400
        current = _parse_iso(row.get("plus_until"))
        if current and current > now:
            bank += int((current - now).total_seconds())
            _write_promotional_entitlement(db, client_id, None)
        _write_promotional_bank(db, client_id, bank)
        return (now + timedelta(seconds=bank)).isoformat()
    current = _parse_iso(row.get("plus_until"))
    base = current if current and current > now else now
    plus_until = (base + timedelta(days=int(days))).isoformat()
    _exec(
        db,
        "UPDATE student_promotional_entitlements "
        "SET plus_until = %s, updated_at = %s WHERE client_id = %s",
        (plus_until, now.isoformat(), client_id),
    )
    return plus_until


def _current_bonus_month(client_id: int, db=None) -> str:
    return month_key(user_date(client_id, db=db))


@contextmanager
def _optional_db_work(db, savepoint_name: str, client_id: int):
    """Isolate non-critical benefits without poisoning a Postgres transaction."""
    from machreach_core.db import _exec

    safe_name = "machreach_" + "".join(
        char for char in savepoint_name.lower() if char.isalnum() or char == "_"
    )
    _exec(db, f"SAVEPOINT {safe_name}")
    try:
        yield
    except Exception as e:
        try:
            _exec(db, f"ROLLBACK TO SAVEPOINT {safe_name}")
            _exec(db, f"RELEASE SAVEPOINT {safe_name}")
        except Exception:
            log.exception(
                "Could not recover optional paid benefit transaction for %s",
                client_id,
            )
            raise
        log.warning(
            "Optional paid benefit %s failed for %s: %s",
            savepoint_name,
            client_id,
            e,
        )
    else:
        _exec(db, f"RELEASE SAVEPOINT {safe_name}")


def normalize_legacy_subscription_tiers() -> int:
    """Retire historical paid-tier records and queue provider cancellation."""
    from machreach_core.db import _exec, _fetchall

    changed = 0
    with get_db() as db:
        rows = _fetchall(db, "SELECT id, mail_preferences FROM clients")
        for row in rows:
            raw = row.get("mail_preferences") or ""
            try:
                prefs = json.loads(raw) if raw else {}
            except Exception:
                continue
            if not isinstance(prefs, dict):
                continue
            subscription = prefs.get("subscription")
            if not isinstance(subscription, dict) or subscription.get("tier") != "ultimate":
                continue
            subscription_id = str(subscription.get("ls_sub_id") or "").strip()
            subscription["tier"] = "free"
            subscription["status"] = "retired"
            prefs["subscription"] = subscription
            updated = _exec(
                db,
                "UPDATE clients SET mail_preferences = %s "
                "WHERE id = %s AND COALESCE(mail_preferences, '') = %s",
                (json.dumps(prefs), int(row["id"]), raw),
            )
            if not getattr(updated, "rowcount", 0):
                continue
            _write_subscription_row(db, int(row["id"]), subscription)
            if subscription_id:
                _exec(
                    db,
                    "INSERT INTO retired_billing_cancellations "
                    "(subscription_id, client_id, status, attempts, last_error) "
                    "VALUES (%s, %s, 'pending', 0, '') "
                    "ON CONFLICT(subscription_id) DO NOTHING",
                    (subscription_id, int(row["id"])),
                )
            changed += 1
    return changed


def _grant_paid_benefits(
    db, client_id: int, tier: str, subscription: dict | None = None
) -> bool:
    """Grant recurring paid-plan benefits once per user-local month.

    Kept here instead of a scheduler so rewards are applied reliably the next
    time an active subscriber uses the product, including webhook/manual tier
    changes.
    """
    if tier != "plus":
        return False
    subscription = subscription or {}
    if str(subscription.get("status") or "").lower() in {"past_due", "unpaid"}:
        return False
    changed = False
    current_month = _current_bonus_month(client_id, db=db)
    from machreach_core.db import _exec, _fetchone, _fetchval

    with _optional_db_work(db, "streak_insurance", client_id):
        claimed = _exec(
            db,
            "INSERT INTO student_reward_state "
            "(client_id, plus_insurance_period_key, updated_at) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT(client_id) DO UPDATE SET "
            "plus_insurance_period_key=excluded.plus_insurance_period_key, "
            "updated_at=excluded.updated_at "
            "WHERE COALESCE(student_reward_state.plus_insurance_period_key, '') "
            "<> excluded.plus_insurance_period_key",
            (client_id, current_month, _utcnow().isoformat()),
        )
        if claimed.rowcount:
            from machreach_core.db import _fetchval
            from student import db as sdb
            sdb._ensure_wallet(db, client_id)
            cur = int(_fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,)) or 0)
            if cur < sdb.PAID_STREAK_FREEZE_CAP:
                _exec(
                    db,
                    "UPDATE student_wallet SET streak_freezes = streak_freezes + %s WHERE client_id = %s",
                    (PLUS_MONTHLY_STREAK_FREEZES, client_id),
                )
            changed = True

    with _optional_db_work(db, "member_badge", client_id):
        has_badge = _fetchone(
            db,
            "SELECT 1 FROM student_badges WHERE client_id = %s AND badge_key = %s",
            (client_id, "plus_member"),
        )
        if not has_badge:
            _exec(db, "INSERT INTO student_badges (client_id, badge_key) VALUES (%s, %s)", (client_id, "plus_member"))
    return changed


def _parse_iso(s):
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _effective_tier(prefs: dict) -> str:
    """Effective tier from a prefs blob: a paid subscription wins; otherwise a
    promotional `plus_until` grant (e.g. referral reward) counts as Plus until
    it expires; otherwise free."""
    sub = (prefs.get("subscription") or {})
    paid = sub.get("tier") or "free"
    if paid == "ultimate":
        paid = "free"
    paid = paid if paid in PLANS else "free"
    status = str(sub.get("status") or "active").lower()
    if paid == "plus":
        if status in {"active", "on_trial", "trialing"}:
            return paid
        if status in {"cancelled", "canceled"}:
            ends_at = _parse_iso(sub.get("ends_at"))
            if ends_at and ends_at > _utcnow():
                return paid
        if status in {"past_due", "unpaid"}:
            failed_at = _parse_iso(sub.get("past_due_since") or sub.get("updated_at"))
            if failed_at and failed_at + timedelta(days=PAYMENT_GRACE_DAYS) > _utcnow():
                return paid
    pu = _parse_iso(prefs.get("plus_until"))
    if pu and pu > _utcnow():
        return "plus"
    return "free"


def get_tier(client_id: int) -> str:
    """Return the effective Free or Plus tier. Defaults to Free."""
    try:
        with get_db() as db:
            subscription = _load_subscription_row(db, client_id)
            prefs = {
                "subscription": subscription,
                # Also the moment a lapsed plan hands the student back their
                # banked free weeks — no webhook has to land for that.
                "plus_until": _reconcile_promotional_bank(db, client_id, subscription),
            }
            tier = _effective_tier(prefs)
            _grant_paid_benefits(db, client_id, tier, subscription)
        return tier
    except Exception:
        return "free"


def get_subscription_state(client_id: int) -> dict:
    """Return a defensive copy of the stored provider subscription state."""
    try:
        with get_db() as db:
            result = _load_subscription_row(db, client_id)
        if result.get("tier") == "ultimate":
            result["tier"] = "free"
            result["status"] = "retired"
        return result
    except Exception:
        return {}


def grant_plus_days(client_id: int, days: int) -> str | None:
    """Grant `days` of promotional Plus (stacks). Extends from the later of now
    or any existing grant, so multiple referral rewards add up. Returns the new
    expiry ISO string, or None on failure. Does not touch paid subscription state.
    """
    try:
        with get_db() as db:
            if not _USE_PG:
                db.execute("BEGIN IMMEDIATE")
            plus_until = _extend_promotional_entitlement(db, client_id, days)
            _grant_paid_benefits(db, client_id, "plus")
            return plus_until
    except Exception as e:
        log.warning("grant_plus_days failed for %s: %s", client_id, e)
        return None


def redeem_pending_referral_reward(
    referred_id: int, days: int = 7
) -> tuple[int, str] | None:
    """Atomically redeem a verified signup and extend the inviter's promotion.

    The pending marker is deleted only after both the idempotent redemption
    ledger and promotional entitlement have been persisted.
    """
    from machreach_core.db import _exec, _fetchone, _fetchval

    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        pending = _fetchone(
            db,
            "SELECT p.code, rc.client_id AS referrer_id "
            "FROM student_pending_referrals p "
            "LEFT JOIN student_referral_codes rc ON rc.code = p.code "
            "WHERE p.referred_id = %s"
            + (" FOR UPDATE OF p" if _USE_PG else ""),
            (referred_id,),
        )
        if not pending:
            return None
        referrer_id = int(pending.get("referrer_id") or 0)
        code = str(pending.get("code") or "")
        if not referrer_id or referrer_id == referred_id:
            _exec(
                db,
                "DELETE FROM student_pending_referrals WHERE referred_id = %s",
                (referred_id,),
            )
            return None
        _fetchone(
            db,
            "SELECT id FROM clients WHERE id=%s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (referrer_id,),
        )
        rolling_limit = max(1, int(os.getenv("REFERRAL_REWARD_30D_MAX", "20")))
        cutoff = (_utcnow() - timedelta(days=30)).isoformat()
        recent_rewards = int(
            _fetchval(
                db,
                "SELECT COUNT(*) FROM student_referral_reward_audit "
                "WHERE referrer_id=%s AND status='granted' AND created_at >= %s",
                (referrer_id, cutoff),
            )
            or 0
        )
        recorded = _exec(
            db,
            "INSERT INTO student_referrals (code, referrer_id, referred_id) "
            "VALUES (%s, %s, %s) ON CONFLICT(referred_id) DO NOTHING",
            (code, referrer_id, referred_id),
        )
        if not recorded.rowcount:
            _exec(
                db,
                "DELETE FROM student_pending_referrals WHERE referred_id = %s",
                (referred_id,),
            )
            return None
        # The referral is genuine and fresh: connect the two accounts as friends
        # regardless of whether the Plus reward itself survives the rolling cap.
        try:
            from student.db import link_referral_friendship
            link_referral_friendship(db, referrer_id, referred_id)
        except Exception as e:  # never let a friendship failure void the reward
            log.warning("referral friendship failed for %s -> %s: %s",
                        referrer_id, referred_id, e)
        if recent_rewards >= rolling_limit:
            _exec(
                db,
                "INSERT INTO student_referral_reward_audit "
                "(referred_id,referrer_id,days,status) VALUES (%s,%s,%s,'withheld') "
                "ON CONFLICT(referred_id) DO NOTHING",
                (referred_id, referrer_id, days),
            )
            _exec(
                db,
                "INSERT INTO operational_events(event_type,source) "
                "VALUES (%s,%s)",
                ("referral_reward_withheld", "rolling_cap"),
            )
            _exec(
                db,
                "DELETE FROM student_pending_referrals WHERE referred_id = %s",
                (referred_id,),
            )
            return None
        plus_until = _extend_promotional_entitlement(db, referrer_id, days)
        _grant_paid_benefits(db, referrer_id, "plus")
        _exec(
            db,
            "INSERT INTO student_referral_reward_audit "
            "(referred_id,referrer_id,days,status) VALUES (%s,%s,%s,'granted') "
            "ON CONFLICT(referred_id) DO NOTHING",
            (referred_id, referrer_id, days),
        )
        _exec(
            db,
            "DELETE FROM student_pending_referrals WHERE referred_id = %s",
            (referred_id,),
        )
        return referrer_id, plus_until


def revoke_referral_reward(referred_id: int) -> bool:
    """Administrative backend primitive for reversing one abusive reward."""
    from machreach_core.db import _exec, _fetchone

    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        row = _fetchone(
            db,
            "SELECT * FROM student_referral_reward_audit WHERE referred_id=%s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (referred_id,),
        )
        if not row or row.get("status") != "granted":
            return False
        referrer_id = int(row["referrer_id"])
        current = _parse_iso(_load_promotional_entitlement(db, referrer_id))
        now = _utcnow()
        if current and current > now:
            reduced = max(now, current - timedelta(days=int(row["days"])))
            _write_promotional_entitlement(db, referrer_id, reduced.isoformat())
        result = _exec(
            db,
            "UPDATE student_referral_reward_audit SET status='revoked', "
            "revoked_at=%s WHERE referred_id=%s AND status='granted'",
            (now.isoformat(), referred_id),
        )
        return bool(result.rowcount)


def plus_grant_until(client_id: int) -> str | None:
    """Return the promotional plus_until ISO string if still active, else None."""
    try:
        with get_db() as db:
            stored = _load_promotional_entitlement(db, client_id)
        pu = _parse_iso(stored)
        if not (pu and pu > _utcnow()):
            return None
        return pu.replace(tzinfo=None).isoformat()
    except Exception:
        return None


def set_tier(client_id: int, tier: str) -> dict:
    if tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        from machreach_core.db import _fetchone
        if not _fetchone(db, "SELECT id FROM clients WHERE id=%s", (client_id,)):
            return {"ok": False, "error": "Unknown client.", "unknown_client": True}
        subscription = _load_subscription_row(db, client_id)
        subscription.update({
            "tier": tier,
            "status": "active" if tier == "plus" else "inactive",
            "since": _utcnow().isoformat(),
        })
        if tier == "plus":
            subscription.setdefault("quota_period_start", _utcnow().isoformat())
            subscription.setdefault("quota_reset_at", (_utcnow() + timedelta(days=31)).isoformat())
        subscription["updated_at"] = _utcnow().isoformat()
        _write_subscription_row(db, client_id, subscription)
        _grant_paid_benefits(db, client_id, tier, subscription)
    return {"ok": True, "tier": tier}


def set_subscription_state(
    client_id: int,
    *,
    tier: str | None = None,
    status: str | None = None,
    ls_sub_id: str | None = None,
    ends_at: str | None = None,
    renews_at: str | None = None,
    update_payment_method_url: str | None = None,
    customer_portal_url: str | None = None,
    payment_succeeded: bool = False,
    provider_event_at: str | None = None,
) -> dict:
    """Persist provider lifecycle state without discarding reconciliation IDs."""
    if tier is not None and tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        from machreach_core.db import _fetchone
        from student.db import _USE_PG
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        # The lock row is also the existence check: every table written below
        # is keyed on clients.id, so a deleted account must fail here with an
        # answer the caller can act on, not with a foreign-key violation from
        # somewhere deep in the write.
        if not _fetchone(
            db,
            "SELECT id FROM clients WHERE id=%s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (client_id,),
        ):
            return {"ok": False, "error": "Unknown client.", "unknown_client": True}
        current_row = _fetchone(
            db,
            "SELECT provider_event_at FROM student_subscription_state "
            "WHERE client_id=%s" + (" FOR UPDATE" if _USE_PG else ""),
            (client_id,),
        ) or {}
        incoming_event = _parse_iso(provider_event_at)
        current_event = _parse_iso(current_row.get("provider_event_at"))
        if incoming_event and current_event and incoming_event < current_event:
            current_state = _load_subscription_row(db, client_id)
            return {
                "ok": True,
                "ignored_stale": True,
                "tier": _effective_tier(
                    {
                        "subscription": current_state,
                        "plus_until": _load_promotional_entitlement(db, client_id),
                    }
                ),
                "status": current_state.get("status"),
            }
        subscription = _load_subscription_row(db, client_id)
        if tier is not None:
            subscription["tier"] = tier
            if tier == "plus" and not subscription.get("since"):
                subscription["since"] = _utcnow().isoformat()
        if status is not None:
            subscription["status"] = str(status).lower()
            if str(status).lower() in {"past_due", "unpaid"}:
                subscription.setdefault("past_due_since", _utcnow().isoformat())
            else:
                subscription.pop("past_due_since", None)
        if ls_sub_id is not None:
            subscription["ls_sub_id"] = str(ls_sub_id).strip() or None
        if ends_at is not None:
            subscription["ends_at"] = str(ends_at)
        if renews_at is not None:
            subscription["renews_at"] = str(renews_at)
            subscription["quota_reset_at"] = str(renews_at)
        if update_payment_method_url is not None:
            subscription["update_payment_method_url"] = str(update_payment_method_url)
        if customer_portal_url is not None:
            subscription["customer_portal_url"] = str(customer_portal_url)
        if incoming_event:
            subscription["provider_event_at"] = incoming_event.isoformat()
        if payment_succeeded or (
            tier == "plus" and not subscription.get("quota_period_start")
        ):
            subscription["quota_period_start"] = _utcnow().isoformat()
            if not renews_at:
                subscription["quota_reset_at"] = (_utcnow() + timedelta(days=31)).isoformat()
        subscription["updated_at"] = _utcnow().isoformat()
        prefs = {
            "subscription": subscription,
            # Against the state being written, not the one on disk: a plan
            # activating banks the free weeks in the same transaction, and a
            # plan expiring releases them.
            "plus_until": _reconcile_promotional_bank(db, client_id, subscription),
        }
        effective = _effective_tier(prefs)
        _write_subscription_row(db, client_id, subscription)
        _grant_paid_benefits(db, client_id, effective, subscription)
    return {"ok": True, "tier": effective, "status": subscription.get("status")}


# ── Capability checks (the API surface that quotes the tier) ────────────

def has_plus_access(client_id: int) -> bool:
    """Return whether the account currently has Plus capabilities."""
    return get_tier(client_id) == "plus"




def cap_questions(client_id: int, requested: int) -> int:
    """Clamp a quiz/flashcard `count` to the tier's allowed maximum."""
    if has_plus_access(client_id):
        return max(1, int(requested))
    return max(1, min(int(requested), FREE_QUIZ_MAX_QUESTIONS))


def cap_cards(client_id: int, requested: int) -> int:
    if has_plus_access(client_id):
        return max(1, int(requested))
    return max(1, min(int(requested), FREE_FLASHCARD_MAX_CARDS))


def _today_str(client_id: int) -> str:
    return user_date(client_id).isoformat()


def can_generate_quiz_today(client_id: int) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if has_plus_access(client_id):
        usage = generation_usage(client_id)
        if usage["remaining"] > 0:
            return True, ""
        return False, (
            f"Plus: ya usaste tus {PLUS_MONTHLY_GENERATIONS} generaciones IA de este ciclo. "
            f"Tu cupo se reinicia el {usage['reset_at'][:10]}."
        )
    used = _count_today(client_id, "quiz_generated")
    if used >= FREE_DAILY_QUIZZES:
        return False, (
            f"Free plan: {FREE_DAILY_QUIZZES} AI quiz per day. "
            "Upgrade to Plus for 100 monthly generations."
        )
    return True, ""


def can_generate_flashcards_today(client_id: int) -> tuple[bool, str]:
    if has_plus_access(client_id):
        usage = generation_usage(client_id)
        if usage["remaining"] > 0:
            return True, ""
        return False, (
            f"Plus: ya usaste tus {PLUS_MONTHLY_GENERATIONS} generaciones IA de este ciclo. "
            f"Tu cupo se reinicia el {usage['reset_at'][:10]}."
        )
    used = _count_today(client_id, "flashcards_generated")
    if used >= FREE_DAILY_FLASHCARD_SETS:
        return False, (
            f"Free plan: {FREE_DAILY_FLASHCARD_SETS} AI flashcard set per day. "
            "Upgrade to Plus for 100 monthly generations."
        )
    return True, ""


def record_generation(client_id: int, kind: str) -> None:
    """Log a generation in `student_xp` (re-using existing table) so daily
    quotas can be counted cheaply without a new table.

    `kind` is one of 'quiz_generated', 'flashcards_generated'.
    Recorded with xp=0 so it doesn't affect XP totals.
    """
    try:
        from machreach_core.db import _exec
        with get_db() as db:
            _exec(
                db,
                "INSERT INTO student_xp "
                "(client_id, action, xp, detail, occurred_at_utc) "
                "VALUES (%s, %s, 0, %s, %s)",
                (client_id, kind, _today_str(client_id), _utcnow().isoformat()),
            )
    except Exception as e:
        log.warning("record_generation failed: %s", e)


def _count_today(client_id: int, kind: str) -> int:
    try:
        from machreach_core.db import _fetchval
        start, end = day_window_utc(client_id)
        with get_db() as db:
            return int(_fetchval(
                db,
                "SELECT COUNT(*) FROM student_xp "
                "WHERE client_id = %s AND action = %s "
                "AND occurred_at_utc >= %s AND occurred_at_utc < %s",
                (client_id, kind, start.isoformat(), end.isoformat()),
            ) or 0)
    except Exception:
        return 0


def generation_usage(client_id: int) -> dict:
    """Return the combined Plus quota for the current provider billing window."""
    now = _utcnow()
    state = get_subscription_state(client_id)
    period_start = _parse_iso(state.get("quota_period_start"))
    reset_at = _parse_iso(state.get("quota_reset_at") or state.get("renews_at"))
    boundaries_are_utc = bool(period_start and reset_at)
    if not period_start or not reset_at:
        period_start, reset_at = month_window_utc(client_id, at=now)
    used = 0
    try:
        from machreach_core.db import _fetchval
        with get_db() as db:
            used = int(_fetchval(
                db,
                "SELECT COUNT(*) FROM student_xp WHERE client_id = %s "
                "AND action IN (%s, %s, %s) "
                "AND occurred_at_utc >= %s AND occurred_at_utc < %s",
                (
                    client_id,
                    "quiz_generated",
                    "flashcards_generated",
                    "ai_tool_generated",
                    period_start.isoformat(),
                    reset_at.isoformat(),
                ),
            ) or 0)
    except Exception:
        log.exception("Could not calculate AI generation usage for %s", client_id)
    limit = PLUS_MONTHLY_GENERATIONS if get_tier(client_id) == "plus" else 0
    window_expired = bool(boundaries_are_utc and reset_at <= now)
    return {
        "limit": limit,
        "used": used,
        # Never manufacture a new calendar-month allowance while waiting for
        # Lemon Squeezy to confirm the next paid billing period.
        "remaining": 0 if window_expired else max(0, limit - used),
        "reset_at": reset_at.isoformat(),
    }


def expire_payment_grace_periods() -> int:
    """Persist Free after a payment has remained past due for seven days."""
    from machreach_core.db import _exec, _fetchall

    changed = 0
    now = _utcnow()
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT client_id, tier, status, past_due_since, updated_at "
            "FROM student_subscription_state "
            "WHERE status IN ('past_due', 'unpaid')",
        )
        for row in rows:
            failed_at = _parse_iso(
                row.get("past_due_since") or row.get("updated_at")
            )
            if not failed_at or failed_at + timedelta(days=PAYMENT_GRACE_DAYS) > now:
                continue
            _exec(
                db,
                "UPDATE student_subscription_state SET tier = 'free', "
                "status = 'grace_expired', updated_at = %s WHERE client_id = %s",
                (now.isoformat(), int(row["client_id"])),
            )
            changed += 1
    return changed
