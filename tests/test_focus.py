"""Focus timer: batch-claim must save every session, not just the first.

Regression for the bug where claiming several pomodoros only saved one and the
rest errored ("Reintentar", no XP): the batch claim inserts multiple
student_study_progress rows in the same second, which collided on
UNIQUE(client_id, plan_date) when plan_date had only second precision.
"""
from student import db as sdb


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
