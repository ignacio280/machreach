"""Focus timer persistence and the rules that decide whether XP is owed.

The first tests here are a regression for the bug where claiming several
pomodoros only saved one and the rest errored ("Reintentar", no XP): the batch
claim inserts multiple student_study_progress rows in the same second, which
collided on UNIQUE(client_id, plan_date) when plan_date had only second
precision.

The rest cover validate_focus_phase_claim, which is the only thing standing
between a hand-edited localStorage entry and free XP. Focus XP feeds the
leaderboard and the leaderboard pays out prizes, so each rejection it can
return is a separate promise worth pinning.
"""
from student import db as sdb
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

from machreach_core.db import get_db, _exec, _fetchall
from student.periods import FrozenClock, use_clock


def test_rapid_focus_sessions_do_not_collide(make_user):
    cid = make_user()
    ids = [sdb.save_focus_session(cid, mode="pomodoro", minutes=5, pages=0,
                                  course_name="Test Course")
           for _ in range(5)]
    saved = [i for i in ids if i]
    # All five must persist as distinct rows (pre-fix, the 2nd+ in the same
    # second threw a UNIQUE violation and was lost).
    assert len(saved) == 5, f"expected 5 saved sessions, got {saved}"
    assert len(set(saved)) == 5


def test_focus_sessions_count_in_stats(make_user):
    cid = make_user()
    for _ in range(3):
        sdb.save_focus_session(cid, mode="pomodoro", minutes=10, pages=0,
                               course_name="Test Course")
    stats = sdb.get_focus_stats(cid)
    assert stats.get("sessions", 0) >= 3
    assert stats.get("total_minutes", 0) >= 30


def test_parallel_claims_credit_one_focus_phase_exactly_once(make_user):
    cid = make_user("Concurrent Focus User")
    course_id = sdb.create_manual_course(cid, "Concurrency", code="CON101")
    phase_id = "parallel-phase-1"
    sdb.start_focus_phase(
        cid,
        phase_id,
        expected_minutes=20,
        course_id=course_id,
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_focus_phases SET started_at = %s, started_at_utc = %s "
            "WHERE client_id = %s AND phase_id = %s",
            (
                (datetime.now(UTC) - timedelta(minutes=21)).replace(tzinfo=None),
                datetime.now(UTC) - timedelta(minutes=21),
                cid,
                phase_id,
            ),
        )

    def claim():
        return sdb.claim_focus_phase_rewards(
            cid,
            phase_id,
            minutes=20,
            mode="pomodoro",
            course_name="Concurrency",
            course_id=course_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    assert sum(bool(result.get("saved")) for result in results) == 1
    assert sdb.get_focus_stats(cid)["total_minutes"] == 20
    assert sdb.get_wallet(cid)["coins"] == 2
    with get_db() as db:
        quests = _fetchall(
            db,
            "SELECT quest_key, target, progress, xp_reward FROM student_daily_quests "
            "WHERE client_id = %s ORDER BY quest_key",
            (cid,),
        )
    assert len(quests) == 3
    for quest in quests:
        expected = 1 if quest["quest_key"] == "session_3" else min(20, quest["target"])
        assert quest["progress"] == expected
    quest_xp = sum(
        quest["xp_reward"] for quest in quests if quest["progress"] >= quest["target"]
    )
    assert sdb.get_total_xp(cid) == 10 + quest_xp


# --- validate_focus_phase_claim: the anti-forgery rules --------------------
#
# A phase must be registered server-side before the browser timer starts, and
# the claim that follows has to agree with that registration on identity,
# length, subject and elapsed wall-clock time. The clock is frozen rather than
# slept through, so "not enough time has passed" is tested exactly instead of
# approximately.

_PHASE_START = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)


def test_claiming_a_phase_that_was_never_registered_is_refused(make_user):
    """The forged-localStorage case, and the reason registration exists.

    Nothing was ever started server-side, so there is no evidence any studying
    happened. Accepting this would make focus XP purely client-asserted.
    """
    cid = make_user("Forged Phase User")
    ok, reason, row = sdb.validate_focus_phase_claim(cid, "never-registered", minutes=25)
    assert ok is False
    assert reason == "unverified-phase"
    assert row is None


def test_blank_phase_id_is_refused_before_any_lookup(make_user):
    cid = make_user("Blank Phase User")
    assert sdb.validate_focus_phase_claim(cid, "   ", minutes=25) == (
        False, "missing-phase-id", None,
    )


def test_a_phase_can_only_be_claimed_once(make_user):
    """Replay protection: the same finished pomodoro must not pay twice."""
    cid = make_user("Replay User")
    phase_id = "replay-phase"
    with use_clock(FrozenClock(_PHASE_START)):
        sdb.start_focus_phase(cid, phase_id, expected_minutes=25)

    with use_clock(FrozenClock(_PHASE_START + timedelta(minutes=25))):
        first_ok, first_reason, _ = sdb.validate_focus_phase_claim(cid, phase_id, minutes=25)
        assert (first_ok, first_reason) == (True, "ok")
        sdb.mark_focus_phase_claimed(cid, phase_id)
        second_ok, second_reason, _ = sdb.validate_focus_phase_claim(cid, phase_id, minutes=25)

    assert second_ok is False
    assert second_reason == "duplicate-phase"


def test_a_claim_cannot_report_more_minutes_than_were_registered(make_user):
    """Registering a 25-minute pomodoro and claiming an 8-hour one.

    The cap is expected + 1, so a minute of rounding slack is allowed and an
    inflated claim is not.
    """
    cid = make_user("Inflated Claim User")
    phase_id = "inflated-phase"
    with use_clock(FrozenClock(_PHASE_START)):
        sdb.start_focus_phase(cid, phase_id, expected_minutes=25)

    with use_clock(FrozenClock(_PHASE_START + timedelta(hours=9))):
        inflated_ok, inflated_reason, _ = sdb.validate_focus_phase_claim(
            cid, phase_id, minutes=480,
        )
        rounded_ok, rounded_reason, _ = sdb.validate_focus_phase_claim(
            cid, phase_id, minutes=26,
        )

    assert inflated_ok is False
    assert inflated_reason == "minutes-exceed-phase"
    assert (rounded_ok, rounded_reason) == (True, "ok")


def test_a_phase_cannot_be_claimed_before_its_time_has_actually_passed(make_user):
    """Starting a 25-minute phase and claiming it five minutes later.

    Twenty seconds of grace are allowed for the round trip, so the boundary is
    24:40, not 25:00 — checked from both sides.
    """
    cid = make_user("Impatient User")
    phase_id = "impatient-phase"
    with use_clock(FrozenClock(_PHASE_START)):
        sdb.start_focus_phase(cid, phase_id, expected_minutes=25)

    with use_clock(FrozenClock(_PHASE_START + timedelta(minutes=5))):
        early_ok, early_reason, _ = sdb.validate_focus_phase_claim(cid, phase_id, minutes=25)
    just_short = timedelta(minutes=25) - timedelta(seconds=sdb.FOCUS_PHASE_CLAIM_GRACE_SECONDS + 1)
    with use_clock(FrozenClock(_PHASE_START + just_short)):
        short_ok, short_reason, _ = sdb.validate_focus_phase_claim(cid, phase_id, minutes=25)
    just_enough = timedelta(minutes=25) - timedelta(seconds=sdb.FOCUS_PHASE_CLAIM_GRACE_SECONDS)
    with use_clock(FrozenClock(_PHASE_START + just_enough)):
        allowed_ok, allowed_reason, _ = sdb.validate_focus_phase_claim(cid, phase_id, minutes=25)

    assert (early_ok, early_reason) == (False, "phase-too-early")
    assert (short_ok, short_reason) == (False, "phase-too-early")
    assert (allowed_ok, allowed_reason) == (True, "ok")


def test_a_phase_registered_for_one_course_cannot_be_claimed_for_another(make_user):
    """Studying is credited to the course the timer was actually started on.

    Otherwise a student could farm a course they never opened, which matters
    because per-course minutes drive the planner and the public profile.
    """
    cid = make_user("Course Swap User")
    studied = sdb.create_manual_course(cid, "Álgebra", code="ALG101")
    other = sdb.create_manual_course(cid, "Historia", code="HIS101")
    phase_id = "course-swap-phase"
    with use_clock(FrozenClock(_PHASE_START)):
        sdb.start_focus_phase(cid, phase_id, expected_minutes=25, course_id=studied)

    with use_clock(FrozenClock(_PHASE_START + timedelta(minutes=25))):
        swapped_ok, swapped_reason, _ = sdb.validate_focus_phase_claim(
            cid, phase_id, minutes=25, course_id=other,
        )
        honest_ok, honest_reason, _ = sdb.validate_focus_phase_claim(
            cid, phase_id, minutes=25, course_id=studied,
        )

    assert swapped_ok is False
    assert swapped_reason == "course-mismatch"
    assert (honest_ok, honest_reason) == (True, "ok")


# --- _parse_focus_dt -------------------------------------------------------
#
# Focus timestamps arrive in several shapes: real datetimes from Postgres,
# ISO strings with a Z or an offset, and SQLite's naive
# "YYYY-MM-DD HH:MM:SS.ffffff". Everything downstream — the elapsed-time check
# that blocks early claims, and the day bucketing behind streaks — subtracts
# these, so the one rule that has to hold is that a naive value means UTC.
# Reading it as local time instead would shift every comparison by the account
# offset, which in Chile is three to four hours: enough to make a 25-minute
# phase claimable the moment it starts.

def test_naive_focus_timestamps_are_read_as_utc_not_local_time():
    noon_utc = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

    assert sdb._parse_focus_dt("2026-03-11 12:00:00") == noon_utc
    assert sdb._parse_focus_dt("2026-03-11 12:00:00.000000") == noon_utc
    assert sdb._parse_focus_dt("2026-03-11T12:00:00") == noon_utc
    assert sdb._parse_focus_dt(datetime(2026, 3, 11, 12, 0)) == noon_utc


def test_explicit_offsets_are_honoured_and_normalised_to_utc():
    noon_utc = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

    assert sdb._parse_focus_dt("2026-03-11T12:00:00Z") == noon_utc
    assert sdb._parse_focus_dt("2026-03-11T12:00:00+00:00") == noon_utc
    # 09:00 in Santiago (UTC-3 in March) is the same instant as 12:00 UTC.
    assert sdb._parse_focus_dt("2026-03-11T09:00:00-03:00") == noon_utc
    assert sdb._parse_focus_dt(
        datetime(2026, 3, 11, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    ) == noon_utc


def test_unparseable_focus_timestamps_are_none_rather_than_a_guess():
    """_focus_started_at returning None is what makes a claim invalid, so a
    bad value must not silently become "now" or the epoch."""
    for bad in (None, "", "   ", "not-a-date", "2026-13-45 99:99:99", 0):
        assert sdb._parse_focus_dt(bad) is None


def test_focus_start_prefers_the_canonical_utc_column(make_user):
    """Legacy rows only have the naive local column; new rows have both."""
    modern = sdb._focus_started_at({
        "started_at_utc": "2026-03-11T12:00:00+00:00",
        "started_at": "2026-03-11 06:00:00",
    })
    legacy = sdb._focus_started_at({"started_at": "2026-03-11 06:00:00"})
    empty = sdb._focus_started_at({"started_at_utc": None, "started_at": None})

    assert modern == datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
    assert legacy == datetime(2026, 3, 11, 6, 0, tzinfo=UTC)
    assert empty is None
