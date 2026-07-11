"""Focus timer: batch-claim must save every session, not just the first.

Regression for the bug where claiming several pomodoros only saved one and the
rest errored ("Reintentar", no XP): the batch claim inserts multiple
student_study_progress rows in the same second, which collided on
UNIQUE(client_id, plan_date) when plan_date had only second precision.
"""
from student import db as sdb
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from outreach.db import get_db, _exec, _fetchall


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
            "UPDATE student_focus_phases SET started_at = %s "
            "WHERE client_id = %s AND phase_id = %s",
            (
                (datetime.now() - timedelta(minutes=21)).strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                ),
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
