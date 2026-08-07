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
    assert not admin_security.verify_code(secret, "not-a-code", timestamp=timestamp)


def test_mfa_secret_uri_and_corrupt_preferences_are_safe(monkeypatch):
    secret = admin_security.generate_secret()
    assert len(secret) == 32
    uri = admin_security.provisioning_uri(
        secret, "admin+security@example.com", issuer="Mach Reach"
    )
    assert uri.startswith("otpauth://totp/Mach%20Reach%3Aadmin%2Bsecurity")
    assert f"secret={secret}" in uri

    monkeypatch.setattr(admin_security, "get_mail_preferences", lambda _cid: "{bad")
    assert admin_security._preferences(1) == {}
    monkeypatch.setattr(admin_security, "get_mail_preferences", lambda _cid: "[]")
    assert admin_security._preferences(1) == {}


def test_mfa_storage_and_verification_fail_closed(monkeypatch):
    monkeypatch.setattr(
        admin_security, "get_mail_preferences", lambda _cid: "{}"
    )
    assert admin_security.secret_for(1) == ""
    assert admin_security.verify_client_code(1, "123456") is False

    monkeypatch.setattr(
        admin_security,
        "get_mail_preferences",
        lambda _cid: '{"admin_mfa":{"secret":"encrypted"}}',
    )
    monkeypatch.setattr(
        admin_security,
        "decrypt_password",
        lambda _value: (_ for _ in ()).throw(ValueError("corrupt")),
    )
    assert admin_security.is_enabled(1) is False
    assert admin_security.verify_client_code(1, "123456") is False


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


def test_admin_redirect_target_can_never_leave_the_admin_area():
    """`next` is attacker-supplied, so it decides only *which* admin page.

    "//evil.example" is the interesting one: it starts with a slash, so a
    naive prefix check would pass it, and browsers read it as a protocol-
    relative URL to another host.
    """
    import app as appmod

    assert appmod._safe_admin_next("/admin/growth") == "/admin/growth"
    assert appmod._safe_admin_next("//evil.example/admin") == "/admin"
    assert appmod._safe_admin_next("https://evil.example/admin") == "/admin"
    assert appmod._safe_admin_next("/dashboard") == "/admin"
    assert appmod._safe_admin_next(None) == "/admin"


def test_mfa_and_reauth_pages_are_closed_to_non_admins(client, make_user):
    """They render admin chrome and accept admin credentials, so a plain
    student must not reach them even to look."""
    _login(client, make_user("Curious Student"))

    verify = client.get("/admin/mfa/verify")
    reauth = client.get("/admin/reauth")

    assert verify.status_code == reauth.status_code == 302
    assert verify.headers["Location"].endswith("/dashboard")
    assert reauth.headers["Location"].endswith("/dashboard")


def _enrolled_admin(make_user, password="admin-password-1"):
    import app as appmod

    client_id = make_user("Security Admin")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET is_admin = 1, password = %s WHERE id = %s",
            (appmod._hash_pw(password), client_id),
        )
    admin_security.enroll(client_id, "JBSWY3DPEHPK3PXP")
    return client_id


def _verified_admin_session(client, client_id):
    """Reauthentication is a step-up, not the front door.

    /admin/reauth itself sits behind the ordinary MFA gate, so an admin who
    has not verified this session is bounced to /admin/mfa/verify before the
    route body ever runs.
    """
    _login(client, client_id)
    with client.session_transaction() as session:
        session["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()


def test_reauthentication_needs_the_password_and_the_code_together(
    client, flask_app, make_user, monkeypatch,
):
    """Either factor alone must fail.

    This is what stands between a stolen admin session and a destructive
    action, so a regression that accepted `password_ok or mfa_ok` would look
    completely normal in the UI.
    """
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    from app import limiter as app_limiter
    app_limiter.reset()

    _verified_admin_session(client, _enrolled_admin(make_user))
    good_code = admin_security.current_code("JBSWY3DPEHPK3PXP")

    password_only = client.post("/admin/reauth", data={
        "password": "admin-password-1", "code": "000000",
    })
    code_only = client.post("/admin/reauth", data={
        "password": "wrong-password", "code": good_code,
    })

    for refused in (password_only, code_only):
        assert refused.status_code == 200
        assert "incorrect" in refused.get_data(as_text=True)
    with client.session_transaction() as session:
        assert "admin_reauthenticated_at" not in session


def test_successful_reauthentication_stamps_the_session_and_ignores_a_hostile_next(
    client, flask_app, make_user, monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    from app import limiter as app_limiter
    app_limiter.reset()

    _verified_admin_session(client, _enrolled_admin(make_user, password="admin-password-2"))

    response = client.post("/admin/reauth", data={
        "password": "admin-password-2",
        "code": admin_security.current_code("JBSWY3DPEHPK3PXP"),
        "next": "https://evil.example/admin",
    })

    assert response.status_code == 302
    assert response.headers["Location"] == "/admin"
    with client.session_transaction() as session:
        assert session["admin_reauthenticated_at"] > 0


def test_mfa_verification_rejects_a_bad_code_and_accepts_the_current_one(
    client, flask_app, make_user, monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    from app import limiter as app_limiter
    app_limiter.reset()

    client_id = _enrolled_admin(make_user, password="admin-password-3")
    _login(client, client_id)

    refused = client.post("/admin/mfa/verify", data={"code": "000000"})
    with client.session_transaction() as session:
        assert "admin_mfa_verified_at" not in session

    accepted = client.post("/admin/mfa/verify", data={
        "code": admin_security.current_code("JBSWY3DPEHPK3PXP"),
        "next": "/admin/growth",
    })

    assert refused.status_code == 200
    assert "invalid" in refused.get_data(as_text=True)
    assert accepted.status_code == 302
    assert accepted.headers["Location"] == "/admin/growth"
    with client.session_transaction() as session:
        assert session["admin_mfa_verified_at"] > 0
