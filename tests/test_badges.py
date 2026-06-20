"""Badge wiring: pruned badges are gone, and the evaluators award the right
threshold / event badges."""
from student import db as sdb


def _has(cid, key):
    return any(b["badge_key"] == key for b in sdb.get_badges(cid))


def test_pruned_badges_are_removed():
    for key in ("plus_supporter", "plus_founder", "plus_vip", "plus_gold",
                "no_phone_30", "quiz_speed", "quiz_no_skip", "exam_master"):
        assert key not in sdb.BADGE_DEFS, f"{key} should have been pruned"


def test_no_unreachable_is_a_known_count():
    # Sanity: the catalog is the pruned size.
    assert len(sdb.BADGE_DEFS) == 78


def test_evaluate_badges_threshold_awards(make_user):
    cid = make_user()
    sdb.award_xp(cid, "test", 600, "seed")               # -> xp_100, xp_500
    sdb.save_focus_session(cid, mode="pomodoro", minutes=70, pages=0,
                           course_name="Math")            # -> focus_1h
    sdb.upsert_course(cid, 90001, "Math", "MAT101", "2026")  # -> first_course

    newly = sdb.evaluate_badges(cid)
    for key in ("xp_100", "xp_500", "focus_1h", "first_course"):
        assert _has(cid, key), f"expected {key}; newly={newly}"
    # A tier the user hasn't reached must NOT be awarded.
    assert not _has(cid, "xp_10000")


def test_evaluate_event_badge_four_hour_session(make_user):
    cid = make_user()
    earned = sdb.evaluate_event_badges(cid, session_minutes=240)
    assert _has(cid, "focus_session_4h"), f"earned={earned}"
