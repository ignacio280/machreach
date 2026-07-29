from datetime import UTC, datetime
import json

from machreach_core.db import _exec, _fetchone, get_db
from student.periods import (
    FrozenClock,
    day_window_utc,
    month_window_utc,
    use_clock,
    user_date,
    utc_now,
)
from student import db as sdb
from student import subscription as ssub


def test_chilean_midnight_uses_saved_iana_timezone():
    before_midnight = datetime(2026, 7, 28, 3, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 28, 4, 0, 0, tzinfo=UTC)

    assert user_date(
        0, at=before_midnight, timezone_name="America/Santiago"
    ).isoformat() == "2026-07-27"
    assert user_date(
        0, at=after_midnight, timezone_name="America/Santiago"
    ).isoformat() == "2026-07-28"


def test_dst_day_window_has_real_elapsed_length():
    start, end = day_window_utc(
        0, day=datetime(2026, 9, 6).date(), timezone_name="America/Santiago"
    )

    assert (end - start).total_seconds() == 23 * 60 * 60


def test_month_window_converts_local_boundaries_to_utc():
    start, end = month_window_utc(
        0, day=datetime(2026, 7, 15).date(), timezone_name="America/Santiago"
    )

    assert start == datetime(2026, 7, 1, 4, tzinfo=UTC)
    assert end == datetime(2026, 8, 1, 4, tzinfo=UTC)


def test_clock_is_injectable_and_always_utc_aware():
    instant = datetime(2026, 7, 28, 3, 59, tzinfo=UTC)
    with use_clock(FrozenClock(instant)):
        assert utc_now() == instant
        assert utc_now().tzinfo is UTC


def test_free_ai_quota_resets_at_user_midnight_not_utc(make_user):
    client_id = make_user()
    sdb.set_user_timezone(client_id, "America/Santiago")
    before_midnight = datetime(2026, 7, 28, 3, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 28, 4, 0, 0, tzinfo=UTC)

    with use_clock(FrozenClock(before_midnight)):
        ssub.record_generation(client_id, "quiz_generated")
        assert ssub.can_generate_quiz_today(client_id)[0] is False
    with use_clock(FrozenClock(after_midnight)):
        assert ssub.can_generate_quiz_today(client_id)[0] is True


def test_focus_daily_cap_resets_at_saved_user_midnight(make_user):
    client_id = make_user()
    sdb.set_user_timezone(client_id, "America/Santiago")
    first_session = datetime(2026, 7, 28, 3, 59, 40, tzinfo=UTC)
    before_midnight = datetime(2026, 7, 28, 3, 59, 50, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 28, 4, 0, 10, tzinfo=UTC)

    with use_clock(FrozenClock(first_session)):
        assert sdb.save_focus_session(client_id, "focus", 480, 0) > 0
    with use_clock(FrozenClock(before_midnight)):
        assert sdb.save_focus_session(client_id, "focus", 480, 0) > 0
        assert sdb.save_focus_session(client_id, "focus", 1, 0) == 0
    with use_clock(FrozenClock(after_midnight)):
        assert sdb.save_focus_session(client_id, "focus", 480, 0) > 0
        assert sdb.get_focus_stats_today(client_id)["total_minutes"] == 480


def test_legacy_local_wall_time_is_backfilled_using_saved_timezone(make_user):
    client_id = make_user("Legacy Timezone")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET mail_preferences = %s WHERE id = %s",
            (json.dumps({"timezone": "America/Santiago"}), client_id),
        )
        _exec(
            db,
            "INSERT INTO student_xp "
            "(client_id, action, xp, created_at, occurred_at_utc) "
            "VALUES (%s, %s, %s, %s, NULL)",
            (client_id, "legacy", 1, "2026-07-27 23:30:00"),
        )

    sdb._backfill_normalized_student_state()

    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT occurred_at_utc FROM student_xp "
            "WHERE client_id = %s AND action = %s",
            (client_id, "legacy"),
        )
    assert datetime.fromisoformat(row["occurred_at_utc"]) == datetime(
        2026, 7, 28, 3, 30, tzinfo=UTC
    )


def test_streak_freeze_uses_user_yesterday_near_utc_midnight(make_user):
    client_id = make_user("Timezone Freeze")
    sdb.set_user_timezone(client_id, "America/Santiago")
    instant = datetime(2026, 7, 28, 3, 30, tzinfo=UTC)

    with use_clock(FrozenClock(instant)):
        assert sdb.get_wallet(client_id)["streak_freezes"] >= 1
        assert sdb.use_streak_freeze(client_id)["ok"] is True

    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT plan_date, recorded_at_utc FROM student_study_progress "
            "WHERE client_id = %s AND notes = %s",
            (client_id, "[streak freeze]"),
        )
    assert row["plan_date"].startswith("2026-07-26")
    assert datetime.fromisoformat(row["recorded_at_utc"]) == datetime(
        2026, 7, 26, 16, 0, tzinfo=UTC
    )
