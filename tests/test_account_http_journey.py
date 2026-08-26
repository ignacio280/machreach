"""Full account lifecycle through public HTTP endpoints."""

import re

import app as appmod
import worker
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
    # The request only queues — SMTP inside a web request is what starved the
    # thread pool during the mail outage. The worker delivers.
    assert delivered == []
    worker.process_async_jobs()
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
    assert "/verify-email-pending" in first.headers["Location"]
    assert pending is not None
    assert not pending.get("email_verified")
    assert get_async_job_status("verification_email", str(pending["id"]))["status"] == "queued"
    # A failing SMTP server costs the worker a retry, never a request thread.
    worker.process_async_jobs()
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


def test_forgot_password_queues_and_the_worker_delivers_a_working_link(
    client, flask_app, make_user, monkeypatch
):
    """No request thread may wait on SMTP: during the mail outage, sends with a
    30-second timeout inside /register, /resend-verification and
    /forgot-password could hold every gunicorn thread at once, and the whole
    site read as down — worst for whoever was stuck retrying on one account."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    email = "reset-queued@example.test"
    client_id = make_user("Reset Journey", email)
    delivered = []
    monkeypatch.setattr(
        appmod,
        "_send_system_email",
        lambda to, subject, body: delivered.append((to, body)) or True,
    )

    requested = client.post("/forgot-password", data={"email": email})

    assert requested.status_code == 302
    assert delivered == []  # nothing sent inside the request
    assert get_async_job_status("password_reset_email", str(client_id))["status"] == "queued"

    worker.process_async_jobs()

    assert get_async_job_status("password_reset_email", str(client_id))["status"] == "done"
    # The worker may also drain jobs older tests queued, so match on content.
    reset_to, reset_body = next(
        (to, body) for to, body in delivered if "/reset-password/" in body
    )
    assert reset_to == email
    reset_path = re.search(r"(/reset-password/[A-Za-z0-9_-]+)", reset_body).group(1)
    assert client.get(reset_path).status_code == 200

    # Unknown addresses answer identically and queue nothing.
    unknown = client.post("/forgot-password", data={"email": "ghost@example.test"})
    assert unknown.status_code == 302
    assert sum("/reset-password/" in body for _, body in delivered) == 1


def test_resend_verification_queues_for_the_worker(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    email = "resend-queued@example.test"
    client_id = make_user("Resend Journey", email)
    delivered = []
    monkeypatch.setattr(
        appmod,
        "_send_system_email",
        lambda to, subject, body: delivered.append(to) or True,
    )

    resent = client.post("/resend-verification", data={"email": email})

    assert resent.status_code == 302
    assert "/verify-email-pending" in resent.headers["Location"]
    assert delivered == []
    assert get_async_job_status("verification_email", str(client_id))["status"] == "queued"

    worker.process_async_jobs()

    assert delivered == [email]
    assert get_async_job_status("verification_email", str(client_id))["status"] == "done"
