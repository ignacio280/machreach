"""Subscription tier helpers for the Student app.

Tiers:
  - free      : 1 quiz/day (max 30 q), 1 flashcard set/day (max 30 c).
                Limits ENFORCED only after BETA ends.
  - plus      : Unlimited AI generation, ad-free, smarter tutor, no MailHub.
  - ultimate  : Everything Plus has + MailHub access.

During beta everything is unlimited regardless of tier — see `BETA_ACTIVE`.

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
from datetime import datetime, date

from outreach.db import get_db

log = logging.getLogger(__name__)

# Toggle this off when you launch out of beta.
# When True, every tier is treated as Ultimate (no limits, all features).
BETA_ACTIVE = True

# ── Plan definitions ───────────────────────────────────────────────────

FREE_DAILY_QUIZZES        = 1
FREE_QUIZ_MAX_QUESTIONS   = 30
FREE_DAILY_FLASHCARD_SETS = 1
# Free users can send up to N quiz-duel challenges per day. Sending even one
# also blocks AI quiz generation for the rest of the day (only the SENDER
# is penalized; the receiver is unaffected).
FREE_DAILY_QUIZ_DUELS_SENT = 3
FREE_FLASHCARD_MAX_CARDS  = 30

PLANS = {
    "free": {
        "key": "free",
        "name": "Free",
        "price_usd_month": 0.00,
        "price_usd_year": 0.00,
        "blurb": "Get started with daily AI study tools.",
        "features": [
            f"{FREE_DAILY_QUIZZES} AI quiz / day  (up to {FREE_QUIZ_MAX_QUESTIONS} questions)",
            f"{FREE_DAILY_FLASHCARD_SETS} AI flashcard set / day  (up to {FREE_FLASHCARD_MAX_CARDS} cards)",
            "Focus timer, streaks, leaderboards, duels, shop",
            "Community Exchange (browse + buy notes)",
        ],
    },
    "plus": {
        "key": "plus",
        "name": "Plus",
        "price_usd_month": 4.99,
        "price_usd_year": 39.99,
        "blurb": "Unlimited AI study tools.",
        "features": [
            "Unlimited AI quizzes & flashcards",
            "Unlimited cards / questions per generation",
            "300 bonus coins per month",
            "PLUS profile badge & exclusive cosmetics",
            "Detailed analytics & exportable reports",
        ],
    },
    "ultimate": {
        "key": "ultimate",
        "name": "Ultimate",
        "price_usd_month": 9.99,
        "price_usd_year": 79.99,
        "blurb": "Everything in Plus, plus mail tools.",
        "features": [
            "Everything in Plus",
            "Mail organization",
            "Email AI reply",
            "Priority support",
        ],
    },
}

PLAN_ORDER = ["free", "plus", "ultimate"]


# ── Read / write tier ───────────────────────────────────────────────────

def _load_prefs(db, client_id: int) -> dict:
    from outreach.db import _fetchone
    row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (client_id,))
    raw = (row or {}).get("mail_preferences") or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_prefs(db, client_id: int, prefs: dict) -> None:
    from outreach.db import _exec
    _exec(
        db,
        "UPDATE clients SET mail_preferences = %s WHERE id = %s",
        (json.dumps(prefs), client_id),
    )


def get_tier(client_id: int) -> str:
    """Return 'free', 'plus', or 'ultimate'. Defaults to 'free'."""
    try:
        with get_db() as db:
            prefs = _load_prefs(db, client_id)
        sub = (prefs.get("subscription") or {})
        tier = sub.get("tier") or "free"
        return tier if tier in PLANS else "free"
    except Exception:
        return "free"


def set_tier(client_id: int, tier: str) -> dict:
    if tier not in PLANS:
        return {"ok": False, "error": "Unknown plan."}
    with get_db() as db:
        prefs = _load_prefs(db, client_id)
        prefs["subscription"] = {
            "tier": tier,
            "since": datetime.utcnow().isoformat(),
        }
        _save_prefs(db, client_id, prefs)
    return {"ok": True, "tier": tier}


# ── Capability checks (the API surface that quotes the tier) ────────────

def has_unlimited_ai(client_id: int) -> bool:
    if BETA_ACTIVE:
        return True
    return get_tier(client_id) in ("plus", "ultimate")


def has_mailhub(client_id: int) -> bool:
    if BETA_ACTIVE:
        return True
    return get_tier(client_id) == "ultimate"


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
    # Sending a quiz duel uses up the day's free AI quiz budget too.
    if _count_today(client_id, "quiz_duel_sent") > 0:
        return False, (
            "You've already sent a quiz duel today. Free plan: 1 AI activity "
            "per day (a duel counts). Upgrade to Plus for unlimited."
        )
    used = _count_today(client_id, "quiz_generated")
    if used >= FREE_DAILY_QUIZZES:
        return False, (
            f"Free plan: {FREE_DAILY_QUIZZES} AI quiz per day. "
            "Upgrade to Plus for unlimited."
        )
    return True, ""


def can_send_quiz_duel_today(client_id: int) -> tuple[bool, str]:
    """Return (allowed, reason). Plus/Ultimate: unlimited. Free: 3 sent / day,
    AND if a regular AI quiz was already generated today the duel slot is
    consumed too (mirror of can_generate_quiz_today)."""
    if has_unlimited_ai(client_id):
        return True, ""
    if _count_today(client_id, "quiz_generated") > 0:
        return False, (
            "You've already used your daily AI quiz. Upgrade to Plus to send "
            "unlimited quiz duels."
        )
    sent = _count_today(client_id, "quiz_duel_sent")
    if sent >= FREE_DAILY_QUIZ_DUELS_SENT:
        return False, (
            f"Free plan: {FREE_DAILY_QUIZ_DUELS_SENT} quiz duels per day. "
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

    `kind` is one of 'quiz_generated', 'flashcards_generated', 'quiz_duel_sent'.
    Recorded with xp=0 so it doesn't affect XP totals.
    """
    try:
        from outreach.db import _exec
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
        from outreach.db import _fetchval, _USE_PG
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
