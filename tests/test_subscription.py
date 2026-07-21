"""Money path: subscription tiers, promotional Plus grants, and free limits."""
from datetime import datetime, timedelta, timezone

from student import subscription as ssub
from machreach_core.db import get_db


def test_default_tier_is_free(make_user):
    assert ssub.get_tier(make_user()) == "free"
    assert ssub.has_plus_access(make_user()) is False


def test_paid_tier_set_and_read(make_user):
    cid = make_user()
    ssub.set_tier(cid, "plus")
    assert ssub.get_tier(cid) == "plus"
    assert ssub.has_plus_access(cid) is True


def test_unknown_tier_rejected(make_user):
    cid = make_user()
    res = ssub.set_tier(cid, "bogus")
    assert res["ok"] is False
    assert ssub.get_tier(cid) == "free"


def test_grant_plus_days_makes_user_plus(make_user):
    cid = make_user()
    ssub.grant_plus_days(cid, 7)
    assert ssub.get_tier(cid) == "plus"
    assert ssub.has_plus_access(cid) is True
    assert ssub.plus_grant_until(cid) is not None


def test_grant_plus_days_stacks(make_user):
    cid = make_user()
    ssub.grant_plus_days(cid, 7)
    first = datetime.fromisoformat(ssub.plus_grant_until(cid))
    ssub.grant_plus_days(cid, 7)
    second = datetime.fromisoformat(ssub.plus_grant_until(cid))
    # Two weeks granted -> roughly 14 days out, and strictly later than one week.
    assert second > first
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert (second - now).days >= 13


def test_expired_grant_reverts_to_free(make_user):
    cid = make_user()
    ssub.grant_plus_days(cid, 7)
    assert ssub.get_tier(cid) == "plus"
    # Force the grant into the past.
    with get_db() as db:
        prefs = ssub._load_prefs(db, cid)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        prefs["plus_until"] = (now - timedelta(days=1)).isoformat()
        ssub._save_prefs(db, cid, prefs)
    assert ssub.get_tier(cid) == "free"
    assert ssub.plus_grant_until(cid) is None


def test_paid_subscription_beats_expired_promo(make_user):
    cid = make_user()
    ssub.set_tier(cid, "plus")           # real paid subscription
    with get_db() as db:                  # expired promo grant present too
        prefs = ssub._load_prefs(db, cid)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        prefs["plus_until"] = (now - timedelta(days=30)).isoformat()
        ssub._save_prefs(db, cid, prefs)
    assert ssub.get_tier(cid) == "plus"   # paid wins, expired promo irrelevant


def test_question_and_card_caps(make_user):
    free = make_user()
    assert ssub.cap_questions(free, 500) == ssub.FREE_QUIZ_MAX_QUESTIONS
    assert ssub.cap_cards(free, 500) == ssub.FREE_FLASHCARD_MAX_CARDS
    plus = make_user()
    ssub.grant_plus_days(plus, 7)
    assert ssub.cap_questions(plus, 500) == 500
    assert ssub.cap_cards(plus, 500) == 500


def test_free_daily_quiz_limit(make_user):
    cid = make_user()
    allowed, _ = ssub.can_generate_quiz_today(cid)
    assert allowed is True
    ssub.record_generation(cid, "quiz_generated")
    allowed_after, reason = ssub.can_generate_quiz_today(cid)
    assert allowed_after is False
    assert reason  # a user-facing upgrade message


def test_plus_user_has_no_quiz_limit(make_user):
    cid = make_user()
    ssub.grant_plus_days(cid, 7)
    ssub.record_generation(cid, "quiz_generated")
    allowed, _ = ssub.can_generate_quiz_today(cid)
    assert allowed is True


def test_plus_combined_generation_limit_is_100(make_user):
    cid = make_user()
    ssub.set_tier(cid, "plus")
    for index in range(ssub.PLUS_MONTHLY_GENERATIONS):
        kind = "quiz_generated" if index % 2 else "flashcards_generated"
        ssub.record_generation(cid, kind)

    usage = ssub.generation_usage(cid)
    quiz_allowed, quiz_reason = ssub.can_generate_quiz_today(cid)
    cards_allowed, cards_reason = ssub.can_generate_flashcards_today(cid)

    assert usage["used"] == 100
    assert usage["remaining"] == 0
    assert quiz_allowed is False
    assert cards_allowed is False
    assert "reinicia" in quiz_reason
    assert "reinicia" in cards_reason


def test_expired_provider_window_does_not_create_calendar_month_quota(make_user):
    cid = make_user()
    ssub.set_subscription_state(cid, tier="plus", status="active")
    with get_db() as db:
        prefs = ssub._load_prefs(db, cid)
        prefs["subscription"]["quota_period_start"] = "2026-01-01T00:00:00"
        prefs["subscription"]["quota_reset_at"] = "2026-02-01T00:00:00"
        ssub._save_prefs(db, cid, prefs)

    usage = ssub.generation_usage(cid)

    assert usage["limit"] == 100
    assert usage["remaining"] == 0


def test_past_due_grace_expires_after_seven_days(make_user):
    cid = make_user()
    ssub.set_subscription_state(cid, tier="plus", status="past_due")
    with get_db() as db:
        prefs = ssub._load_prefs(db, cid)
        prefs["subscription"]["past_due_since"] = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
        ).isoformat()
        ssub._save_prefs(db, cid, prefs)

    assert ssub.get_tier(cid) == "free"
    assert ssub.expire_payment_grace_periods() == 1
    assert ssub.get_subscription_state(cid)["status"] == "grace_expired"
