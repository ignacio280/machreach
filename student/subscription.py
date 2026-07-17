"""Subscription tier helpers for the Student app.

Tiers:
  - free      : 1 quiz/day (max 30 q), 1 flashcard set/day (max 30 c).
  - plus      : Unlimited AI generation, analytics, streak protection and cosmetics.
  - ultimate  : Highest paid tier.

Storage: re-uses the existing `clients` table via `mail_preferences` JSON-blob
column (already exists in the schema). Keys used:
    mail_preferences = {
       ...other prefs...,
       "subscription": {
           "tier": "free" | "plus" | "ultimate",
           "since": "2026-04-21T...",
       }
    }
This avoids a schema migration and works on both Postgres and SQLite.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta, timezone

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
            "Quizzes IA ilimitados",
            "Flashcards IA ilimitadas",
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
    "ultimate": {
        "key": "ultimate",
        "name": "Ultimate",
        "price_clp_month": 8990,
        "price_clp_year": 107880,
        "blurb": "El plan completo para quienes quieren llevar su estudio al maximo.",
        "features": [
            "Todo lo de Plus",
            "Límites máximos de IA",
            "Historial completo de analítica",
            "Más monedas, reparaciones y cosméticos Ultimate",
            "Early access a herramientas nuevas",
        ],
    },
}
PLAN_ORDER = ["free", "plus", "ultimate"]


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


def _grant_paid_benefits(db, client_id: int, prefs: dict, tier: str) -> bool:
    """Grant recurring paid-plan benefits once per UTC month.

    Kept here instead of a scheduler so rewards are applied reliably the next
    time an active subscriber uses the product, including webhook/manual tier
    changes.
    """
    if tier not in ("plus", "ultimate"):
        return False
    changed = False
    month_key = _current_bonus_month()
    if prefs.get("plus_streak_insurance_month") != month_key:
        try:
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
        except Exception as e:
            log.warning("Plus monthly streak-insurance grant failed for %s: %s", client_id, e)
    try:
        from machreach_core.db import _exec, _fetchone
        has_badge = _fetchone(
            db,
            "SELECT 1 FROM student_badges WHERE client_id = %s AND badge_key = %s",
            (client_id, "plus_member"),
        )
        if not has_badge:
            _exec(db, "INSERT INTO student_badges (client_id, badge_key) VALUES (%s, %s)", (client_id, "plus_member"))
    except Exception:
        pass
    return changed


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", ""))
    except Exception:
        return None


def _effective_tier(prefs: dict) -> str:
    """Effective tier from a prefs blob: a paid subscription wins; otherwise a
    promotional `plus_until` grant (e.g. referral reward) counts as Plus until
    it expires; otherwise free."""
    sub = (prefs.get("subscription") or {})
    paid = sub.get("tier") or "free"
    paid = paid if paid in PLANS else "free"
    status = str(sub.get("status") or "active").lower()
    if paid in ("plus", "ultimate") and status in {"active", "on_trial", "trialing"}:
        return paid
    pu = _parse_iso(prefs.get("plus_until"))
    if pu and pu > _utcnow():
        return "plus"
    return "free"


def get_tier(client_id: int) -> str:
    """Return 'free', 'plus', or 'ultimate'. Defaults to 'free'."""
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
        return dict(subscription) if isinstance(subscription, dict) else {}
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
            "status": "active" if tier in ("plus", "ultimate") else "inactive",
            "since": _utcnow().isoformat(),
        })
        prefs["subscription"] = subscription
        _grant_paid_benefits(db, client_id, prefs, tier)
        _save_prefs(db, client_id, prefs)
    return {"ok": True, "tier": tier}


def set_subscription_state(client_id: int, *, tier: str | None = None,
                           status: str | None = None,
                           ls_sub_id: str | None = None) -> dict:
    """Persist provider lifecycle state without discarding reconciliation IDs."""
    if tier is not None and tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        prefs = _load_prefs(db, client_id)
        subscription = prefs.get("subscription") or {}
        if tier is not None:
            subscription["tier"] = tier
            if tier in ("plus", "ultimate") and not subscription.get("since"):
                subscription["since"] = _utcnow().isoformat()
        if status is not None:
            subscription["status"] = str(status).lower()
        if ls_sub_id is not None:
            subscription["ls_sub_id"] = str(ls_sub_id)
        subscription["updated_at"] = _utcnow().isoformat()
        prefs["subscription"] = subscription
        effective = _effective_tier(prefs)
        _grant_paid_benefits(db, client_id, prefs, effective)
        _save_prefs(db, client_id, prefs)
    return {"ok": True, "tier": effective, "status": subscription.get("status")}


# ── Capability checks (the API surface that quotes the tier) ────────────

def has_unlimited_ai(client_id: int) -> bool:
    return get_tier(client_id) in ("plus", "ultimate")




def cap_questions(client_id: int, requested: int) -> int:
    """Clamp a quiz/flashcard `count` to the tier's allowed maximum."""
    if has_unlimited_ai(client_id):
        return max(1, int(requested))
    return max(1, min(int(requested), FREE_QUIZ_MAX_QUESTIONS))


def cap_cards(client_id: int, requested: int) -> int:
    if has_unlimited_ai(client_id):
        return max(1, int(requested))
    return max(1, min(int(requested), FREE_FLASHCARD_MAX_CARDS))


def _today_str() -> str:
    return date.today().isoformat()


def can_generate_quiz_today(client_id: int) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if has_unlimited_ai(client_id):
        return True, ""
    used = _count_today(client_id, "quiz_generated")
    if used >= FREE_DAILY_QUIZZES:
        return False, (
            f"Free plan: {FREE_DAILY_QUIZZES} AI quiz per day. "
            "Upgrade to Plus for unlimited."
        )
    return True, ""


def can_generate_flashcards_today(client_id: int) -> tuple[bool, str]:
    if has_unlimited_ai(client_id):
        return True, ""
    used = _count_today(client_id, "flashcards_generated")
    if used >= FREE_DAILY_FLASHCARD_SETS:
        return False, (
            f"Free plan: {FREE_DAILY_FLASHCARD_SETS} AI flashcard set per day. "
            "Upgrade to Plus for unlimited."
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
