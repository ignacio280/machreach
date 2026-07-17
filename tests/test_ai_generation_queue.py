"""Slow AI generation must leave web request threads immediately."""

from machreach_core.db import _exec, get_async_job_status, get_db


def _login_student(client, client_id):
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Queued Student"
        session["account_type"] = "student"
        session["session_version"] = 0


def test_flashcard_generation_is_queued(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Queued Student", "queued-flashcards@example.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    _login_student(client, client_id)

    response = client.post(
        "/api/student/flashcards/generate",
        json={"source_text": "A sufficiently detailed source text for cards.", "count": 5},
    )

    assert response.status_code == 202
    assert response.get_json()["queued"] is True
    assert get_async_job_status("student_flashcard_generation", str(client_id))["status"] == "queued"


def test_legacy_synchronous_quiz_endpoint_is_retired(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Queued Quiz", "queued-quiz@example.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    _login_student(client, client_id)

    response = client.post("/api/student/quizzes/generate", json={"source_text": "material"})

    assert response.status_code == 410
    assert response.get_json()["replacement"].endswith("generate-async")
