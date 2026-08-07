"""Streak counting and the two ways a missed day can be forgiven.

A streak is the number the student sees every day and the thing they lose by
skipping, so what counts as "unbroken" is worth stating exactly. Two mechanisms
overlap here and only one of them is free:

  * one automatic freeze per ISO week, applied silently the first time a gap in
    that week is observed and then recorded so it is not granted twice;
  * beyond that, a freeze the student bought with coins, consumed one per gap
    day, which is why an off-by-one would quietly spend real currency.

The clock is frozen to a Wednesday so "this ISO week" has days on both sides of
today, which is what makes the once-per-week rule observable.
"""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from machreach_core.db import _exec, _fetchval, get_db
from student import db as sdb
from student.periods import FrozenClock, use_clock

# 2026-03-11 12:00 in Santiago. ISO week 11 runs Mon 03-09 to Sun 03-15, so
# today, yesterday and the day before all sit inside the same week while
# 03-08 falls in the previous one.
_NOW = datetime(2026, 3, 11, 15, 0, tzinfo=UTC)
_TODAY = date(2026, 3, 11)
_SANTIAGO = ZoneInfo("America/Santiago")


def _award_xp_on(client_id, *days):
    """Record activity on each given local date, at local noon."""
    with get_db() as db:
        for day in days:
            instant = datetime.combine(day, time(12, 0), tzinfo=_SANTIAGO).astimezone(UTC)
            _exec(
                db,
                "INSERT INTO student_xp (client_id, action, xp, detail, occurred_at_utc) "
                "VALUES (%s, %s, %s, %s, %s)",
                (client_id, "streak-seed", 5, "seeded activity", instant.isoformat()),
            )


def _grant_streak_freezes(client_id, count):
    with get_db() as db:
        sdb._ensure_wallet(db, client_id)
        _exec(
            db,
            "UPDATE student_wallet SET streak_freezes = %s WHERE client_id = %s",
            (count, client_id),
        )


def _owned_freezes(client_id):
    with get_db() as db:
        return int(_fetchval(
            db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,),
        ) or 0)


def test_consecutive_days_count_without_spending_any_freeze(make_user):
    cid = make_user("Unbroken Streak User")
    _grant_streak_freezes(cid, 3)
    _award_xp_on(cid, _TODAY, _TODAY - timedelta(days=1), _TODAY - timedelta(days=2))

    with use_clock(FrozenClock(_NOW)):
        streak = sdb.get_streak_days(cid)

    assert streak == 3
    # Nothing was missed, so nothing should have been paid for.
    assert _owned_freezes(cid) == 3


def test_a_single_missed_day_is_forgiven_free_once_per_week(make_user):
    """The gap on Tuesday is covered by the week's automatic freeze."""
    cid = make_user("One Gap User")
    _grant_streak_freezes(cid, 2)
    _award_xp_on(cid, _TODAY, _TODAY - timedelta(days=2))

    with use_clock(FrozenClock(_NOW)):
        streak = sdb.get_streak_days(cid)

    assert streak == 3
    # The free weekly freeze covered it, so the purchased ones are untouched.
    assert _owned_freezes(cid) == 2


def test_a_second_gap_in_the_same_week_breaks_a_streak_with_nothing_to_spend(make_user):
    """Two gaps, one free freeze, no purchased ones: the streak stops."""
    cid = make_user("Two Gap Broke User")
    _grant_streak_freezes(cid, 0)
    _award_xp_on(cid, _TODAY, _TODAY - timedelta(days=3))

    with use_clock(FrozenClock(_NOW)):
        streak = sdb.get_streak_days(cid)

    # Today counts, the first gap is frozen free, the second has no cover.
    assert streak == 2


def test_a_purchased_freeze_covers_the_second_gap_and_is_spent(make_user):
    """Same two gaps as above, but the student owns a freeze.

    The streak survives and exactly one freeze is consumed — not zero, which
    would make the purchase pointless, and not two, which would charge for the
    day the weekly freeze already covered.
    """
    cid = make_user("Two Gap Paid User")
    _grant_streak_freezes(cid, 2)
    _award_xp_on(cid, _TODAY, _TODAY - timedelta(days=3))

    with use_clock(FrozenClock(_NOW)):
        streak = sdb.get_streak_days(cid)

    assert streak == 4
    assert _owned_freezes(cid) == 1


def test_freezes_are_never_applied_to_days_before_the_account_had_activity(make_user):
    """Otherwise a new account could freeze its way back through empty history."""
    cid = make_user("Brand New User")
    _grant_streak_freezes(cid, 50)
    _award_xp_on(cid, _TODAY)

    with use_clock(FrozenClock(_NOW)):
        streak = sdb.get_streak_days(cid)

    assert streak == 1
    assert _owned_freezes(cid) == 50


def test_a_user_with_no_activity_at_all_has_no_streak(make_user):
    cid = make_user("Idle User")

    with use_clock(FrozenClock(_NOW)):
        assert sdb.get_streak_days(cid) == 0
