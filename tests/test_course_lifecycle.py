"""A final grade closes a course; deleting it by hand withdraws what it left behind."""

import base64
import json
import re
from datetime import UTC, date, datetime, timedelta

from machreach_core.db import _exec, _fetchone, get_db
from student import db as sdb


def _student(client, make_user, name="Lifecycle Student"):
    client_id = make_user(name, f"{name.lower().replace(' ', '-')}@example.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"
    return client_id


def _app_payload(markup):
    match = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', markup)
    assert match, "the live app payload is missing"
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def _course_with_exam(client_id, name="Cálculo"):
    course_id = sdb.create_manual_course(client_id, name, "MAT100")
    sdb.upsert_exam(client_id, course_id, None, name="Prueba 1",
                    exam_date=(date.today() + timedelta(days=5)).isoformat(),
                    weight_pct=30, topics=[])
    return course_id, int(sdb.get_course_exams(course_id)[0]["id"])


def test_final_grade_freezes_the_course(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Freeze Student")
    course_id, exam_id = _course_with_exam(client_id)

    saved = client.post(f"/api/student/courses/{course_id}/outcome",
                        json={"final_grade": 5.4, "passing_grade": 3.95})
    assert saved.status_code == 200

    # Nothing about the course may change afterwards.
    again = client.post(f"/api/student/courses/{course_id}/outcome", json={"final_grade": 7.0})
    added = client.post(f"/api/student/courses/{course_id}/exams", json={"name": "Extra"})
    edited = client.put(f"/api/student/exams/{exam_id}",
                        json={"course_id": course_id, "name": "Otro", "weight_pct": 10})

    assert again.status_code == 409
    assert added.status_code == 409
    assert edited.status_code == 409
    assert sdb.course_final_outcome(client_id, course_id)["final_grade"] == 5.4


def test_courses_page_marks_a_finalised_course_as_locked(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Locked Payload")
    course_id, _ = _course_with_exam(client_id)
    client.post(f"/api/student/courses/{course_id}/outcome", json={"final_grade": 4.5})

    payload = _app_payload(client.get("/student/courses").get_data(as_text=True))

    course = next(c for c in payload["courses"]["items"] if c["id"] == course_id)
    assert course["locked"] is True
    assert course["final_grade"] == "4,5"


def test_a_finalised_course_retires_after_a_week_and_keeps_its_grade_in_notas(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Retiring Student")
    course_id, _ = _course_with_exam(client_id, name="Termo")
    client.post(f"/api/student/courses/{course_id}/outcome", json={"final_grade": 6.0})

    # Still listed during the grace week.
    assert any(int(c["id"]) == course_id for c in sdb.get_courses(client_id))

    with get_db() as db:
        _exec(
            db,
            "UPDATE student_course_outcomes SET reported_at = %s WHERE client_id = %s AND course_id = %s",
            (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=sdb.FINALISED_COURSE_GRACE_DAYS + 1), client_id, course_id),
        )

    assert all(int(c["id"]) != course_id for c in sdb.get_courses(client_id))
    sheet = _app_payload(client.get("/student/gpa").get_data(as_text=True))["grades"]["sheet"]
    rows = [row for sem in sheet["sems"] for row in sem["courses"] if row.get("course_id") == course_id]
    assert rows and rows[0]["locked"] is True
    assert rows[0]["evals"][0]["grade"] == "6.0"


def test_notas_edits_to_a_finalised_course_do_not_survive_the_page(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Reverting Student")
    course_id, _ = _course_with_exam(client_id, name="Química")
    client.post(f"/api/student/courses/{course_id}/outcome", json={"final_grade": 5.0})
    sheet = _app_payload(client.get("/student/gpa").get_data(as_text=True))["grades"]["sheet"]
    for sem in sheet["sems"]:
        for row in sem["courses"]:
            if row.get("course_id") == course_id:
                row["evals"] = [{"id": "x", "name": "Nota final", "pct": 100, "grade": "7.0"}]
    assert client.post("/api/student/grades/sheet", json={"sheet": sheet}).status_code == 200

    reloaded = _app_payload(client.get("/student/gpa").get_data(as_text=True))["grades"]["sheet"]

    row = next(r for sem in reloaded["sems"] for r in sem["courses"] if r.get("course_id") == course_id)
    assert row["evals"][0]["grade"] == "5.0"


def test_deleting_a_course_by_hand_withdraws_its_grade_and_review(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Withdrawing Student")
    course_id, _ = _course_with_exam(client_id, name="Física")
    client.post(f"/api/student/courses/{course_id}/outcome", json={"final_grade": 5.5})
    posted = client.post("/api/student/reviews", json={
        "course_id": course_id, "difficulty_rating": 4, "quality_rating": 3,
        "review_text": "Las pruebas pesan mucho más que las ayudantías.",
    })
    assert posted.status_code in (200, 201)

    deleted = client.delete(f"/api/student/courses/{course_id}")

    assert deleted.status_code == 200
    assert sdb.course_final_outcome(client_id, course_id) is None
    with get_db() as db:
        left = _fetchone(
            db,
            "SELECT COUNT(*) AS n FROM student_course_reviews WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        )
        graded = _fetchone(
            db,
            "SELECT COUNT(*) AS n FROM student_course_outcomes WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        )
    assert int(left["n"]) == 0
    assert int(graded["n"]) == 0
    # With no outcome left the course can no longer be reviewed.
    payload = _app_payload(client.get("/student/reviews").get_data(as_text=True))
    assert all(c["id"] != course_id for c in payload["reviews"]["courses"])
