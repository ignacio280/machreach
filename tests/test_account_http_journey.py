"""Full account lifecycle through public HTTP endpoints."""

import re

import app as appmod


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
