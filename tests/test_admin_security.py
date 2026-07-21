from __future__ import annotations

from datetime import datetime, timezone

from machreach_core import admin_security
from machreach_core.db import _exec, get_db


def _login(client, client_id: int) -> None:
    with client.session_transaction() as session:
        session.update({
            "client_id": client_id,
            "client_name": "Security Admin",
            "account_type": "student",
            "session_version": 0,
        })


def test_totp_roundtrip_is_time_bounded():
    secret = "JBSWY3DPEHPK3PXP"
    timestamp = 1_700_000_000
    code = admin_security.current_code(secret, timestamp=timestamp)

    assert admin_security.verify_code(secret, code, timestamp=timestamp)
    assert not admin_security.verify_code(secret, code, timestamp=timestamp + 90)


def test_email_address_never_grants_admin_access(client, make_user):
    client_id = make_user("Not An Admin", "ignaciomachuca2005@gmail.com")
    _login(client, client_id)

    response = client.get("/admin")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_database_admin_must_complete_mfa(client, make_user):
    client_id = make_user("Security Admin")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (client_id,))
    _login(client, client_id)

    response = client.get("/admin")

    assert response.status_code == 302
    assert "/admin/mfa" in response.headers["Location"]


def test_dangerous_admin_action_requires_recent_reauthentication(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Security Admin")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (client_id,))
    admin_security.enroll(client_id, "JBSWY3DPEHPK3PXP")
    _login(client, client_id)
    with client.session_transaction() as session:
        session["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()

    response = client.post(
        "/admin/broadcast",
        data={
            "action": "broadcast",
            "subject": "Security notice",
            "body": "Test",
            "confirm_phrase": "SEND TO ALL USERS",
        },
    )

    assert response.status_code == 200
    assert "/admin/reauth" in response.get_data(as_text=True)
