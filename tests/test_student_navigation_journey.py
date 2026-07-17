"""Authenticated student journeys exercised through the public HTTP surface."""

import pytest

from machreach_core.db import _exec, get_db
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
        "/student/invite",
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
