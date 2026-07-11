"""Course-review API behavior."""

from outreach.db import get_db, _exec
from student import db as sdb


def _login(client, client_id):
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = "Review User"
        sess["account_type"] = "student"
        sess["session_version"] = 0


def test_reviews_api_returns_an_empty_collection(client, make_user):
    client_id = make_user("Review Reader")
    _login(client, client_id)

    response = client.get("/api/student/reviews")

    assert response.status_code == 200
    assert response.get_json()["reviews"] == []


def test_student_can_publish_and_search_a_completed_course_review(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Review Writer")
    course_id = sdb.create_manual_course(
        client_id, "Cálculo Diferencial", code="MAT101", term="2026-1"
    )
    assert sdb.record_course_outcome(client_id, course_id, 6.2)["ok"] is True
    _login(client, client_id)

    created = client.post(
        "/api/student/reviews",
        json={
            "course_id": course_id,
            "difficulty_rating": 5,
            "quality_rating": 4,
            "review_text": "Muy exigente pero excelente.",
        },
    )
    found = client.get("/api/student/reviews?q=MAT101")

    assert created.status_code == 200
    assert created.get_json()["id"] > 0
    assert found.status_code == 200
    reviews = found.get_json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["course_name"] == "Cálculo Diferencial"
    assert reviews[0]["difficulty_rating"] == 5
    assert reviews[0]["quality_rating"] == 4
