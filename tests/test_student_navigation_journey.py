"""Authenticated student journeys exercised through the public HTTP surface."""

import pytest

from machreach_core.db import _exec, _fetchone, get_db
from student import db as sdb


@pytest.fixture()
def active_student(client, make_user):
    client_id = make_user("Journey Student")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1, email_verified = 1 "
            "WHERE id = %s",
            (client_id,),
        )
    course_id = sdb.create_manual_course(client_id, "Journey Mathematics", "MATH-101")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Journey Student"
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"
    return {"client_id": client_id, "course_id": course_id}


@pytest.mark.parametrize(
    "path",
    [
        "/student",
        "/student/achievements",
        "/student/analytics",
        "/student/canvas",
        "/student/canvas-settings",
        "/student/courses",
        "/student/exams",
        "/student/flashcards",
        "/student/focus",
        "/student/friends",
        "/student/gpa",
        "/student/leaderboard",
        "/student/planner",
        "/student/profile",
        "/student/profile/edit",
        "/student/quizzes",
        "/student/reviews",
        "/student/settings",
        "/student/shop",
    ],
)
def test_authenticated_student_pages_render(client, active_student, path):
    response = client.get(path, follow_redirects=True)

    assert response.status_code == 200, path
    assert response.get_data(as_text=True).strip(), path


def test_course_detail_renders_for_its_owner(client, active_student):
    response = client.get(
        f"/student/courses/{active_student['course_id']}",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Journey Mathematics" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "path",
    [
        "/api/student/assignments/incomplete",
        "/api/student/assignments/progress",
        "/api/student/courses",
        "/api/student/courses/by-semester",
        "/api/student/courses/catalog?q=math",
        "/api/student/dashboard",
        "/api/student/date-overrides",
        "/api/student/email-prefs",
        "/api/student/exams",
        "/api/student/flashcards/decks",
        "/api/student/flashcards/generate/status",
        "/api/student/focus/rival",
        "/api/student/focus/stats",
        "/api/student/friends/list",
        "/api/student/gamification",
        "/api/student/manual-plan",
        "/api/student/period/results",
        "/api/student/quests/today",
        "/api/student/quizzes",
        "/api/student/quizzes/generate/status",
        "/api/student/reviews",
        "/api/student/semester/current",
        "/api/student/stats",
        "/api/student/stats/per_course",
        "/api/student/streak/status",
    ],
)
def test_authenticated_student_read_apis_respond(client, active_student, path):
    response = client.get(path)

    assert response.status_code == 200, path
    assert response.is_json, path


def test_manual_course_creation_is_visible_through_course_api(client, active_student, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    created = client.post(
        "/api/student/courses/manual",
        json={"name": "Applied Physics", "code": "PHY-201"},
    )
    listed = client.get("/api/student/courses")

    assert created.status_code == 200
    assert created.get_json()["ok"] is True
    assert any(course["name"] == "Applied Physics" for course in listed.get_json()["courses"])


def test_duplicate_canonical_course_requires_existing_join_flow(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    first_id = make_user("First Course Member")
    second_id = make_user("Second Course Member")
    with get_db() as db:
        university = _fetchone(db, "SELECT id FROM universities ORDER BY id LIMIT 1")
        university_id = int(university["id"])
        _exec(
            db,
            "UPDATE clients SET university_id = %s, email_verified = 1, "
            "academic_setup_complete = 1 WHERE id IN (%s, %s)",
            (university_id, first_id, second_id),
        )

    def login(client_id, name):
        with client.session_transaction() as session:
            session.update({
                "client_id": client_id,
                "client_name": name,
                "account_type": "student",
                "session_version": 0,
            })

    login(first_id, "First Course Member")
    created = client.post(
        "/api/student/courses/manual",
        json={"name": "Álgebra Lineal", "code": " MAT 101 "},
    )
    assert created.status_code == 200

    login(second_id, "Second Course Member")
    duplicate = client.post(
        "/api/student/courses/manual",
        json={"name": "Álgebra Lineal", "code": "mat   101"},
    )
    payload = duplicate.get_json()

    assert duplicate.status_code == 409
    assert payload["course_exists"] is True
    joined = client.post(f"/api/student/courses/catalog/{payload['catalog_id']}/join")
    assert joined.status_code == 200
    assert joined.get_json()["joined_existing"] is True


def test_admin_course_purge_removes_course_plan_data_but_preserves_global_xp(make_user):
    admin_id = make_user("Course Administrator")
    student_id = make_user("Course Member")
    with get_db() as db:
        university = _fetchone(db, "SELECT id FROM universities ORDER BY id LIMIT 1")
        university_id = int(university["id"])
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (admin_id,))
        _exec(
            db,
            "UPDATE clients SET university_id = %s WHERE id = %s",
            (university_id, student_id),
        )

    sdb.record_course_to_catalog(university_id, "DEL-101", "Course To Delete")
    catalog = sdb.find_catalog_course(university_id, "DEL-101", "Course To Delete")
    membership = sdb.join_catalog_course(student_id, int(catalog["id"]))
    course_id = int(membership["id"])
    exam_id = sdb.upsert_exam(
        student_id,
        course_id,
        None,
        "Deleted Course Exam",
        "2099-01-01",
        30,
        ["Topic"],
    )
    sdb.save_study_plan(
        student_id,
        {
            "daily_plan": [
                {"course_id": course_id, "exam_id": exam_id, "title": "Remove me"},
                {"course_id": 999999, "title": "Keep me"},
            ]
        },
    )
    sdb.award_xp(student_id, "course_test", 25)

    deletion = sdb.soft_delete_catalog_course(
        admin_id,
        int(catalog["id"]),
        "Course To Delete",
        "Obsolete catalog entry",
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE admin_course_deletions SET recover_until = %s WHERE id = %s",
            ("2000-01-01 00:00:00", deletion["deletion_id"]),
        )

    assert sdb.purge_expired_course_deletions() == 1
    assert sdb.get_course(course_id) is None
    assert sdb.get_total_xp(student_id) == 25
    remaining = sdb.get_latest_plan(student_id)["plan_json"]["daily_plan"]
    assert remaining == [{"course_id": 999999, "title": "Keep me"}]
    assert sdb.find_catalog_course(university_id, "DEL-101", "Course To Delete") is None

    sdb.record_course_to_catalog(university_id, "DEL-101", "Course To Delete")
    assert sdb.find_catalog_course(university_id, "DEL-101", "Course To Delete") is not None
