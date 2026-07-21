"""Full account lifecycle through public HTTP endpoints."""

import re

import app as appmod
from machreach_core.db import get_async_job_status, get_client_by_email


def test_register_verify_login_and_delete_account(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    delivered = []

    def deliver(to, subject, body):
        delivered.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(appmod, "_send_system_email", deliver)

    registered = client.post(
        "/register",
        data={
            "name": "Lifecycle Student",
            "email": "lifecycle@example.test",
            "password": "correct-horse-battery",
            "password2": "correct-horse-battery",
        },
    )

    assert registered.status_code == 302
    assert "/verify-email-pending" in registered.headers["Location"]
    assert len(delivered) == 1
    assert delivered[0]["to"] == "lifecycle@example.test"
    verification_path = re.search(
        r"https?://[^/]+(/verify-email/[A-Za-z0-9_-]+)",
        delivered[0]["body"],
    ).group(1)

    verified = client.get(verification_path)
    assert verified.status_code == 302
    assert verified.headers["Location"].endswith("/login")

    logged_in = client.post(
        "/login",
        data={
            "email": "lifecycle@example.test",
            "password": "correct-horse-battery",
        },
    )
    assert logged_in.status_code == 302
    assert logged_in.headers["Location"].endswith("/student")

    deleted = client.post(
        "/settings/delete-account",
        data={"confirm": "DELETE"},
        follow_redirects=True,
    )
    assert deleted.status_code == 200
    assert "MachReach" in deleted.get_data(as_text=True)

    rejected = client.post(
        "/login",
        data={
            "email": "lifecycle@example.test",
            "password": "correct-horse-battery",
        },
    )
    assert rejected.status_code == 302
    assert rejected.headers["Location"].endswith("/login")


def test_registration_survives_email_failure_and_reuses_pending_account(
    client,
    flask_app,
    monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(appmod, "_send_system_email", lambda *_args: False)
    email = "pending-reuse@example.test"

    first = client.post(
        "/register",
        data={
            "name": "Pending Student",
            "email": email,
            "password": "correct-horse-battery",
            "password2": "correct-horse-battery",
        },
    )
    pending = get_client_by_email(email)

    assert first.status_code == 302
    assert "delayed=1" in first.headers["Location"]
    assert pending is not None
    assert not pending.get("email_verified")
    assert get_async_job_status("verification_email", str(pending["id"]))["status"] == "queued"

    second = client.post(
        "/register",
        data={
            "name": "Updated Pending Student",
            "email": email,
            "password": "new-correct-horse-battery",
            "password2": "new-correct-horse-battery",
        },
    )
    reused = get_client_by_email(email)

    assert second.status_code == 302
    assert reused["id"] == pending["id"]
    # Re-registration can resend verification, but cannot take over a pending
    # identity by replacing credentials before email ownership is proven.
    assert reused["name"] == "Pending Student"
    assert reused["password"] == pending["password"]


def test_unverified_account_cannot_log_in(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    password = "correct-horse-battery"
    email = "still-unverified@example.test"
    client_id = make_user("Still Unverified", email)
    from machreach_core.db import update_client_password
    update_client_password(client_id, appmod._hash_pw(password), bump_session_version=False)

    response = client.post("/login", data={"email": email, "password": password})

    assert response.status_code == 302
    assert "/verify-email-pending" in response.headers["Location"]


def test_billing_routes_send_logged_in_students_to_shop(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Checkout Redirect Student")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Checkout Redirect Student"
        session["account_type"] = "student"
        session["session_version"] = 0

    for path, method in [
        ("/billing", "get"),
        ("/billing/checkout", "post"),
        ("/billing/downgrade", "post"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/student/shop")
