"""Closing a semester: answers, averages, benchmarks, rewards, rankings."""
from datetime import date, timedelta

from machreach_core.db import _exec, _fetchval, get_db
from student import db as sdb


def _student(client, make_user, name, email):
    client_id = make_user(name, email)
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"
    return client_id


def _course(client_id, name, code, semester="VI"):
    # Mirror the app: the student is in a semester, and courses created there
    # inherit it. Several behaviours key off clients.current_semester.
    sdb.set_current_semester(client_id, semester)
    course_id = sdb.create_manual_course(client_id, name, code)
    sdb.set_course_semester(client_id, course_id, semester)
    return course_id


def _exam(client_id, course_id, when):
    return sdb.upsert_exam(client_id, course_id, None, "Examen", when.isoformat(), 40, [])


def _total_xp(client_id):
    with get_db() as db:
        return int(_fetchval(db, "SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE client_id = %s",
                             (client_id,)) or 0)


# ── Answers ────────────────────────────────────────────────────────────

def test_only_graded_courses_reach_the_average(make_user):
    client_id = make_user("Averages", "avg@sem.test")
    good = _course(client_id, "Cálculo", "MAT100")
    pending = _course(client_id, "Física", "FIS100")
    gone = _course(client_id, "Química", "QIM100")

    sdb.record_course_closure(client_id, good, "graded", final_grade=6.0)
    sdb.record_course_closure(client_id, pending, "pending")
    sdb.record_course_closure(client_id, gone, "withdrawn")

    summary = sdb.semester_summary(client_id, "VI")

    assert summary["total"] == 3
    assert summary["graded"] == 1
    assert summary["pending"] == 1
    assert summary["withdrawn"] == 1
    assert summary["average"] == 6.0        # the other two contribute nothing


def test_a_pending_course_stays_editable_and_keeps_nagging(make_user):
    """"Grade not published yet" is an answer for advancing, not a result: the
    course must stay open so the student can come back with the number."""
    client_id = make_user("Pending", "pending@sem.test")
    course_id = _course(client_id, "Álgebra", "MAT200")

    sdb.record_course_closure(client_id, course_id, "pending")

    assert sdb.course_final_outcome(client_id, course_id) is None
    assert course_id not in sdb.finalised_course_ids(client_id)
    assert [c["id"] for c in sdb.get_pending_course_outcomes(client_id, "VI")] == [course_id]


def test_a_withdrawn_course_is_answered_and_does_not_nag(make_user):
    client_id = make_user("Withdrawn", "withdrawn@sem.test")
    course_id = _course(client_id, "Historia", "HIS100")

    sdb.record_course_closure(client_id, course_id, "withdrawn")

    assert sdb.get_pending_course_outcomes(client_id, "VI") == []
    assert sdb.semester_summary(client_id, "VI")["average"] is None


def test_an_invalid_status_is_refused(make_user):
    client_id = make_user("Bad status", "badstatus@sem.test")
    course_id = _course(client_id, "Ramo", "RAM100")

    assert sdb.record_course_closure(client_id, course_id, "aprobado")["ok"] is False


# ── Closing ────────────────────────────────────────────────────────────

def test_closing_pays_coins_and_a_badge_but_never_xp(make_user):
    """Grades must not move anybody on a leaderboard, and the leaderboards run
    on XP — so closing a semester has to award exactly none of it."""
    client_id = make_user("Closer", "closer@sem.test")
    first = _course(client_id, "Cálculo", "MAT300")
    second = _course(client_id, "Física", "FIS300")
    sdb.get_wallet(client_id)
    coins_before = sdb.get_wallet(client_id)["coins"]
    xp_before = _total_xp(client_id)

    result = sdb.close_semester(client_id, "VI", [
        {"course_id": first, "status": "graded", "final_grade": 6.5},
        {"course_id": second, "status": "graded", "final_grade": 3.0},
    ], next_label="VII")

    assert result["ok"] is True
    assert result["average"] == 4.75
    assert result["passed"] == 1 and result["failed"] == 1
    assert sdb.get_wallet(client_id)["coins"] > coins_before
    assert _total_xp(client_id) == xp_before
    assert "semester_1" in [b["badge_key"] for b in sdb.get_badges(client_id)]
    assert sdb.get_current_semester(client_id) == "VII"


def test_closing_refuses_to_leave_a_course_unanswered(make_user):
    client_id = make_user("Partial", "partial@sem.test")
    answered = _course(client_id, "Cálculo", "MAT400")
    _course(client_id, "Olvidado", "OLV400")

    result = sdb.close_semester(client_id, "VI", [
        {"course_id": answered, "status": "graded", "final_grade": 5.0},
    ])

    assert result["ok"] is False
    assert "Olvidado" in result["error"]


def test_a_semester_can_only_be_closed_once(make_user):
    """Otherwise the coins are farmable by closing the same semester forever."""
    client_id = make_user("Twice", "twice@sem.test")
    course_id = _course(client_id, "Cálculo", "MAT500")
    answers = [{"course_id": course_id, "status": "graded", "final_grade": 5.0}]

    assert sdb.close_semester(client_id, "VI", answers)["ok"] is True
    coins_after_first = sdb.get_wallet(client_id)["coins"]
    second = sdb.close_semester(client_id, "VI", answers)

    assert second["ok"] is False
    assert sdb.get_wallet(client_id)["coins"] == coins_after_first


def test_an_invalid_grade_stops_the_whole_close(make_user):
    client_id = make_user("Bad grade", "badgrade@sem.test")
    course_id = _course(client_id, "Cálculo", "MAT600")

    result = sdb.close_semester(client_id, "VI", [
        {"course_id": course_id, "status": "graded", "final_grade": 9.9},
    ])

    assert result["ok"] is False
    assert sdb.closed_semesters(client_id) == {}


# ── Correcting vs advancing ────────────────────────────────────────────

def test_correcting_the_semester_skips_the_grade_gate_and_moves_courses(make_user):
    """Somebody who picked the wrong number at signup is not ending anything,
    so they must not be asked for grades they do not have."""
    client_id = make_user("Corrector", "corrector@sem.test")
    course_id = _course(client_id, "Cálculo", "MAT700", semester="VI")

    result = sdb.correct_current_semester(client_id, "II", move_courses=True)

    assert result["ok"] is True
    assert result["moved"] == 1
    assert sdb.get_current_semester(client_id) == "II"
    assert sdb.semester_courses(client_id, "II")[0]["id"] == course_id
    assert sdb.semester_courses(client_id, "VI") == []


def test_correcting_can_leave_the_courses_where_they_are(make_user):
    client_id = make_user("Keep", "keep@sem.test")
    _course(client_id, "Cálculo", "MAT800", semester="VI")

    sdb.correct_current_semester(client_id, "VII", move_courses=False)

    assert sdb.semester_courses(client_id, "VI") != []
    assert sdb.get_current_semester(client_id) == "VII"


def test_a_single_course_can_be_refiled(make_user):
    client_id = make_user("Refile", "refile@sem.test")
    course_id = _course(client_id, "Cálculo", "MAT900", semester="VI")

    assert sdb.set_course_semester(client_id, course_id, "V") is True
    assert sdb.semester_courses(client_id, "V")[0]["id"] == course_id


def test_a_course_belonging_to_someone_else_cannot_be_refiled(make_user):
    mine = make_user("Mine", "mine@sem.test")
    theirs = make_user("Theirs", "theirs@sem.test")
    course_id = _course(theirs, "Ajeno", "AJE100")

    assert sdb.set_course_semester(mine, course_id, "I") is False


# ── Benchmarks ─────────────────────────────────────────────────────────

def test_ungraded_answers_stay_out_of_the_benchmarks(make_user):
    """Five "pending" rows must not unlock a benchmark that has no grades."""
    from machreach_core.db import _fetchone

    grader = make_user("Bench", "bench@sem.test")
    course_id = _course(grader, "Benchmarked", "BEN100")
    sdb.record_course_closure(grader, course_id, "pending")

    with get_db() as db:
        row = _fetchone(db, "SELECT university_id, canonical_course_id, cohort_version "
                            "FROM student_course_outcomes WHERE course_id = %s", (course_id,))
    bench = sdb.get_course_success_benchmark(course_id)

    assert row is not None                      # the answer is recorded
    assert bench.get("has_data") is not True    # but it is not a result


# ── Detection ──────────────────────────────────────────────────────────

def test_the_close_prompt_appears_once_every_evaluation_has_passed(make_user):
    client_id = make_user("Hint", "hint@sem.test")
    course_id = _course(client_id, "Cálculo", "MATA10")
    _exam(client_id, course_id, date.today() - timedelta(days=3))

    hint = sdb.semester_close_hint(client_id)

    assert hint["offer"] is True
    assert hint["reason"] == "evaluations_done"


def test_the_close_prompt_stays_away_while_an_evaluation_is_ahead(make_user):
    client_id = make_user("Future", "future@sem.test")
    course_id = _course(client_id, "Cálculo", "MATA20")
    _exam(client_id, course_id, date.today() + timedelta(days=10))

    assert sdb.semester_close_hint(client_id)["offer"] is False


def test_dismissing_the_prompt_silences_it_for_that_semester(make_user):
    client_id = make_user("Dismiss", "dismiss@sem.test")
    course_id = _course(client_id, "Cálculo", "MATA30")
    _exam(client_id, course_id, date.today() - timedelta(days=2))
    assert sdb.semester_close_hint(client_id)["offer"] is True

    sdb.dismiss_semester_prompt(client_id, "VI")

    assert sdb.semester_close_hint(client_id)["offer"] is False


# ── HTTP ───────────────────────────────────────────────────────────────

def test_the_endpoints_close_a_semester_end_to_end(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "HTTP", "http@sem.test")
    course_id = _course(client_id, "Cálculo", "MATB10")

    summary = client.get("/api/student/semester/summary?label=VI").get_json()
    assert [c["id"] for c in summary["courses"]] == [course_id]

    closed = client.post("/api/student/semester/close", json={
        "label": "VI", "next_label": "VII",
        "answers": [{"course_id": course_id, "status": "graded", "final_grade": 5.5}],
    })

    assert closed.status_code == 200
    body = closed.get_json()
    assert body["ok"] is True and body["average"] == 5.5
    assert body["current"] == "VII"
    assert body["wallet"]["coins"] >= body["coins_awarded"]


def test_the_correction_endpoint_does_not_demand_grades(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "HTTP fix", "httpfix@sem.test")
    _course(client_id, "Sin nota", "MATB20")

    # Advancing is still gated...
    advance = client.post("/api/student/semester/current", json={"label": "VII"})
    assert advance.status_code == 409

    # ...but correcting is not.
    fix = client.post("/api/student/semester/correct", json={"label": "III", "move_courses": True})

    assert fix.status_code == 200
    assert sdb.get_current_semester(client_id) == "III"


def test_the_courses_page_ships_the_semester_history_and_hint(client, make_user):
    import base64
    import json
    import re

    client_id = _student(client, make_user, "Payload", "payload@sem.test")
    _course(client_id, "Cálculo", "MATB30")

    body = client.get("/student/courses").get_data(as_text=True)
    payload = json.loads(base64.b64decode(re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', body).group(1)))

    courses = payload["courses"]
    assert courses["semester"] == "VI" or courses["semester"] == ""
    assert "VI" in courses["semesters"]
    assert "close_hint" in courses
    assert courses["closed_semesters"] == []
