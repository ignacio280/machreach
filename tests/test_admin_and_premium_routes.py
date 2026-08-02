"""Coverage for privileged dashboards and the real premium analytics view."""

import base64
import json
import re
import pytest
from datetime import datetime, timezone

from machreach_core import admin_security
from machreach_core.db import _exec, get_db
from student import db as sdb


def _login(client, client_id: int, name: str) -> None:
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"


@pytest.fixture()
def admin_user(client, make_user):
    client_id = make_user("Coverage Administrator")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET is_admin = 1, email_verified = 1, "
            "academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    admin_security.enroll(client_id, "JBSWY3DPEHPK3PXP")
    _login(client, client_id, "Coverage Administrator")
    with client.session_transaction() as session:
        now = datetime.now(timezone.utc).timestamp()
        session["admin_mfa_verified_at"] = now
        session["admin_reauthenticated_at"] = now
    return client_id


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/broadcast",
        "/admin/analytics",
        "/admin/jobs",
        "/admin/leaderboard-winners-test",
    ],
)
def test_admin_get_pages_render(client, admin_user, path):
    response = client.get(path, follow_redirects=True)

    assert response.status_code == 200, path
    assert response.get_data(as_text=True).strip(), path


def test_leaderboard_email_cannot_be_sent_by_get(client, admin_user, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "worker.send_monthly_leaderboard_email",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.get("/admin/leaderboard-winners-test?month=2026-06&send=1")

    assert response.status_code == 200
    assert calls == []


def test_leaderboard_email_post_requires_recent_reauthentication(
    client,
    admin_user,
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    calls = []
    monkeypatch.setattr(
        "worker.send_monthly_leaderboard_email",
        lambda **kwargs: calls.append(kwargs),
    )
    with client.session_transaction() as session:
        session.pop("admin_reauthenticated_at", None)

    response = client.post(
        "/admin/leaderboard-winners-test",
        data={"month": "2026-06"},
    )

    assert response.status_code == 302
    assert "/admin/reauth" in response.headers["Location"]
    assert calls == []


def test_premium_analytics_renders_complete_dashboard(
    client,
    make_user,
    monkeypatch,
):
    client_id = make_user("Premium Analytics Student")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET email_verified = 1, academic_setup_complete = 1 "
            "WHERE id = %s",
            (client_id,),
        )
    course_id = sdb.create_manual_course(client_id, "Coverage Mathematics", "MAT-200")
    assert course_id
    _login(client, client_id, "Premium Analytics Student")

    from student import subscription

    monkeypatch.setattr(subscription, "has_plus_access", lambda _client_id: True)
    response = client.get("/student/analytics")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/static/machreach_app/analiticas.bundle.min.js" in body
    assert "/static/machreach_app/app/analytics.css" in body
    encoded = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', body).group(1)
    payload = json.loads(base64.b64decode(encoded))
    assert payload["plus"] is True


def test_rich_flashcard_quiz_and_exam_views_render(client, make_user):
    client_id = make_user("Rich Study Views Student")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET email_verified = 1, academic_setup_complete = 1 "
            "WHERE id = %s",
            (client_id,),
        )
    course_id = sdb.create_manual_course(client_id, "Biology", "BIO-220")
    deck_id = sdb.create_flashcard_deck(client_id, "Cells", course_id=course_id)
    sdb.add_flashcards(
        deck_id,
        [
            {"front": "What is ATP?", "back": "Cellular energy currency"},
            {"front": "What is DNA?", "back": "Genetic material"},
        ],
    )
    quiz_id = sdb.create_quiz(
        client_id,
        "Cell Biology",
        difficulty="hard",
        course_id=course_id,
    )
    sdb.add_quiz_questions(
        quiz_id,
        [
            {
                "question": "Where is DNA stored?",
                "option_a": "Nucleus",
                "option_b": "Membrane",
                "option_c": "Golgi",
                "option_d": "Vacuole",
                "correct": "a",
                "explanation": "Eukaryotic DNA is primarily nuclear.",
                "topic": "Cells",
            }
        ],
    )
    _login(client, client_id, "Rich Study Views Student")

    paths = [
        f"/student/flashcards/{deck_id}",
        f"/student/quizzes/{quiz_id}",
        f"/student/exam-sim/{quiz_id}",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.get_data(as_text=True).strip(), path


def test_admin_broadcast_validation_and_delivery(
    client,
    admin_user,
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    import app as appmod

    sent = []
    monkeypatch.setattr(appmod, "_admin_secret_ok", lambda: True)
    monkeypatch.setattr(
        appmod,
        "_send_system_email",
        lambda email, subject, body: sent.append((email, subject, body)) or True,
    )

    invalid = client.post(
        "/admin/broadcast",
        data={"action": "broadcast", "subject": "", "body": "Message"},
    )
    delivered = client.post(
        "/admin/broadcast",
        data={
            "action": "broadcast",
            "subject": "Production update",
            "body": "Everything is ready.",
            "confirm_phrase": "SEND TO ALL USERS",
        },
    )

    assert invalid.status_code == 200
    assert "Subject and body are required" in invalid.get_data(as_text=True)
    assert delivered.status_code == 302
    assert sent


def test_admin_can_delete_another_user(
    client,
    admin_user,
    make_user,
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    import app as appmod

    target_email = "admin-delete-target@example.test"
    target_id = make_user("Admin Delete Target", target_email)
    monkeypatch.setattr(appmod, "_admin_secret_ok", lambda: True)

    response = client.post(
        "/admin",
        data={
            "action": "delete_user",
            "client_id": str(target_id),
            "confirm_email": target_email,
            "confirm_phrase": "DELETE USER",
        },
    )

    assert response.status_code == 302
    assert appmod.get_client(target_id) is None
