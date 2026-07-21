"""Subscription tier helpers for the Student app.

Tiers:
  - free      : 1 quiz/day (max 30 q), 1 flashcard set/day (max 30 c).
  - plus      : 100 combined quiz/flashcard generations per billing month,
                analytics, streak protection and cosmetics.

Storage: re-uses the existing `clients` table via `mail_preferences` JSON-blob
column (already exists in the schema). Keys used:
    mail_preferences = {
       ...other prefs...,
       "subscription": {
           "tier": "free" | "plus",
           "since": "2026-04-21T...",
       }
    }
This avoids a schema migration and works on both Postgres and SQLite.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from machreach_core.db import get_db

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return naive UTC for compatibility with existing stored timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

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
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_prefs(db, client_id: int, prefs: dict) -> None:
    from machreach_core.db import _exec
    _exec(
        db,
        "UPDATE clients SET mail_preferences = %s WHERE id = %s",
        (json.dumps(prefs), client_id),
    )


def _current_bonus_month() -> str:
    return _utcnow().strftime("%Y-%m")


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


def _grant_paid_benefits(db, client_id: int, prefs: dict, tier: str) -> bool:
    """Grant recurring paid-plan benefits once per UTC month.

    Kept here instead of a scheduler so rewards are applied reliably the next
    time an active subscriber uses the product, including webhook/manual tier
    changes.
    """
    if tier != "plus":
        return False
    subscription = prefs.get("subscription") or {}
    if str(subscription.get("status") or "").lower() in {"past_due", "unpaid"}:
        return False
    changed = False
    month_key = _current_bonus_month()
    if prefs.get("plus_streak_insurance_month") != month_key:
        with _optional_db_work(db, "streak_insurance", client_id):
            from machreach_core.db import _exec, _fetchval
            from student import db as sdb
            sdb._ensure_wallet(db, client_id)
            cur = int(_fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,)) or 0)
            if cur < sdb.PAID_STREAK_FREEZE_CAP:
                _exec(
                    db,
                    "UPDATE student_wallet SET streak_freezes = streak_freezes + %s WHERE client_id = %s",
                    (PLUS_MONTHLY_STREAK_FREEZES, client_id),
                )
            prefs["plus_streak_insurance_month"] = month_key
            changed = True

    with _optional_db_work(db, "member_badge", client_id):
        from machreach_core.db import _exec, _fetchone
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
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
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
            prefs = _load_prefs(db, client_id)
            tier = _effective_tier(prefs)
            if _grant_paid_benefits(db, client_id, prefs, tier):
                _save_prefs(db, client_id, prefs)
        return tier
    except Exception:
        return "free"


def get_subscription_state(client_id: int) -> dict:
    """Return a defensive copy of the stored provider subscription state."""
    try:
        with get_db() as db:
            prefs = _load_prefs(db, client_id)
        subscription = prefs.get("subscription") or {}
        result = dict(subscription) if isinstance(subscription, dict) else {}
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
            prefs = _load_prefs(db, client_id)
            now = _utcnow()
            cur = _parse_iso(prefs.get("plus_until"))
            base = cur if (cur and cur > now) else now
            prefs["plus_until"] = (base + timedelta(days=int(days))).isoformat()
            _grant_paid_benefits(db, client_id, prefs, "plus")
            _save_prefs(db, client_id, prefs)
            return prefs["plus_until"]
    except Exception as e:
        log.warning("grant_plus_days failed for %s: %s", client_id, e)
        return None


def plus_grant_until(client_id: int) -> str | None:
    """Return the promotional plus_until ISO string if still active, else None."""
    try:
        with get_db() as db:
            prefs = _load_prefs(db, client_id)
        pu = _parse_iso(prefs.get("plus_until"))
        return prefs.get("plus_until") if (pu and pu > _utcnow()) else None
    except Exception:
        return None


def set_tier(client_id: int, tier: str) -> dict:
    if tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        prefs = _load_prefs(db, client_id)
        subscription = prefs.get("subscription") or {}
        subscription.update({
            "tier": tier,
            "status": "active" if tier == "plus" else "inactive",
            "since": _utcnow().isoformat(),
        })
        if tier == "plus":
            subscription.setdefault("quota_period_start", _utcnow().isoformat())
            subscription.setdefault("quota_reset_at", (_utcnow() + timedelta(days=31)).isoformat())
        prefs["subscription"] = subscription
        _grant_paid_benefits(db, client_id, prefs, tier)
        _save_prefs(db, client_id, prefs)
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
) -> dict:
    """Persist provider lifecycle state without discarding reconciliation IDs."""
    if tier is not None and tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        prefs = _load_prefs(db, client_id)
        subscription = prefs.get("subscription") or {}
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
            subscription["ls_sub_id"] = str(ls_sub_id)
        if ends_at is not None:
            subscription["ends_at"] = str(ends_at)
        if renews_at is not None:
            subscription["renews_at"] = str(renews_at)
            subscription["quota_reset_at"] = str(renews_at)
        if update_payment_method_url is not None:
            subscription["update_payment_method_url"] = str(update_payment_method_url)
        if customer_portal_url is not None:
            subscription["customer_portal_url"] = str(customer_portal_url)
        if payment_succeeded or (
            tier == "plus" and not subscription.get("quota_period_start")
        ):
            subscription["quota_period_start"] = _utcnow().isoformat()
            if not renews_at:
                subscription["quota_reset_at"] = (_utcnow() + timedelta(days=31)).isoformat()
        subscription["updated_at"] = _utcnow().isoformat()
        prefs["subscription"] = subscription
        effective = _effective_tier(prefs)
        _grant_paid_benefits(db, client_id, prefs, effective)
        _save_prefs(db, client_id, prefs)
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


def _today_str() -> str:
    return date.today().isoformat()


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
                "INSERT INTO student_xp (client_id, action, xp, detail) "
                "VALUES (%s, %s, 0, %s)",
                (client_id, kind, _today_str()),
            )
    except Exception as e:
        log.warning("record_generation failed: %s", e)


def _count_today(client_id: int, kind: str) -> int:
    try:
        from machreach_core.db import _fetchval, _USE_PG
        with get_db() as db:
            if _USE_PG:
                return int(_fetchval(
                    db,
                    "SELECT COUNT(*) FROM student_xp "
                    "WHERE client_id = %s AND action = %s "
                    "AND created_at::date = CURRENT_DATE",
                    (client_id, kind),
                ) or 0)
            return int(_fetchval(
                db,
                "SELECT COUNT(*) FROM student_xp "
                "WHERE client_id = %s AND action = %s "
                "AND date(created_at) = date('now','localtime')",
                (client_id, kind),
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
        period_start = datetime(now.year, now.month, 1)
        if now.month == 12:
            reset_at = datetime(now.year + 1, 1, 1)
        else:
            reset_at = datetime(now.year, now.month + 1, 1)
    used = 0
    try:
        from machreach_core.db import _USE_PG, _fetchval
        query_start = period_start
        query_end = reset_at
        if not _USE_PG and boundaries_are_utc:
            santiago = ZoneInfo("America/Santiago")
            query_start = period_start.replace(tzinfo=timezone.utc).astimezone(santiago).replace(tzinfo=None)
            query_end = reset_at.replace(tzinfo=timezone.utc).astimezone(santiago).replace(tzinfo=None)
        with get_db() as db:
            used = int(_fetchval(
                db,
                "SELECT COUNT(*) FROM student_xp WHERE client_id = %s "
                "AND action IN (%s, %s) AND created_at >= %s AND created_at < %s",
                (
                    client_id,
                    "quiz_generated",
                    "flashcards_generated",
                    query_start.strftime("%Y-%m-%d %H:%M:%S"),
                    query_end.strftime("%Y-%m-%d %H:%M:%S"),
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
        rows = _fetchall(db, "SELECT id, mail_preferences FROM clients")
        for row in rows:
            try:
                prefs = json.loads(row.get("mail_preferences") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(prefs, dict):
                continue
            subscription = prefs.get("subscription") or {}
            if not isinstance(subscription, dict):
                continue
            if str(subscription.get("status") or "").lower() not in {"past_due", "unpaid"}:
                continue
            failed_at = _parse_iso(
                subscription.get("past_due_since") or subscription.get("updated_at")
            )
            if not failed_at or failed_at + timedelta(days=PAYMENT_GRACE_DAYS) > now:
                continue
            subscription["tier"] = "free"
            subscription["status"] = "grace_expired"
            subscription["updated_at"] = now.isoformat()
            prefs["subscription"] = subscription
            _exec(
                db,
                "UPDATE clients SET mail_preferences = %s WHERE id = %s",
                (json.dumps(prefs), int(row["id"])),
            )
            changed += 1
    return changed
