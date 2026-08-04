"""Administrator MFA enrolment and rotation."""
from datetime import datetime, timezone

import pytest

from machreach_core import admin_security
from machreach_core.db import _exec, get_db


@pytest.fixture
def admin(client, make_user, request):
    # The suite shares one database, so each test needs its own address.
    client_id = make_user("Admin", f"admin-mfa-{request.node.name}@rotate.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["account_type"] = "student"
        session["session_version"] = 0
    return client_id


def _sign_in_with_mfa(client, client_id):
    admin_security.enroll(client_id, "JBSWY3DPEHPK3PXP")
    with client.session_transaction() as session:
        session["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()


def test_the_enrolment_page_offers_a_scannable_qr(client, admin):
    body = client.get("/admin/mfa").get_data(as_text=True)

    assert "<svg" in body                      # scan instead of typing the key
    assert "ADMIN_ACTION_SECRET" in body       # says where the other field comes from
    assert "otpauth://totp/" in body


def test_rotation_needs_the_deployment_secret_and_a_live_code(client, flask_app, admin, monkeypatch):
    _sign_in_with_mfa(client, admin)
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    import app as appmod
    monkeypatch.setattr(appmod, "ADMIN_ACTION_SECRET", "deployment-secret")

    wrong_secret = client.post("/admin/mfa/rotate", data={
        "admin_action_secret": "nope",
        "code": admin_security.current_code("JBSWY3DPEHPK3PXP"),
    })
    assert wrong_secret.status_code == 200
    assert admin_security.is_enabled(admin) is True

    wrong_code = client.post("/admin/mfa/rotate", data={
        "admin_action_secret": "deployment-secret", "code": "000000",
    })
    assert wrong_code.status_code == 200
    assert admin_security.is_enabled(admin) is True


def test_a_valid_rotation_clears_the_enrolment(client, flask_app, admin, monkeypatch):
    _sign_in_with_mfa(client, admin)
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    import app as appmod
    monkeypatch.setattr(appmod, "ADMIN_ACTION_SECRET", "deployment-secret")

    response = client.post("/admin/mfa/rotate", data={
        "admin_action_secret": "deployment-secret",
        "code": admin_security.current_code("JBSWY3DPEHPK3PXP"),
    })

    assert response.status_code == 302
    assert "/admin/mfa" in response.headers["Location"]
    assert admin_security.is_enabled(admin) is False
    # And the session no longer counts as verified.
    with client.session_transaction() as session:
        assert "admin_mfa_verified_at" not in session


def test_rotation_is_not_reachable_before_enrolling(client, admin):
    response = client.get("/admin/mfa/rotate")

    assert response.status_code == 302
    assert "/admin/mfa" in response.headers["Location"]


def test_rotation_is_admin_only(client, make_user):
    client_id = make_user("Not Admin", "notadmin@rotate.test")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["account_type"] = "student"
        session["session_version"] = 0

    response = client.get("/admin/mfa/rotate")

    assert response.status_code == 302
    assert "/admin" not in response.headers["Location"]


def test_disable_reports_whether_there_was_anything_to_remove(make_user):
    client_id = make_user("Disable Probe", "disable@rotate.test")

    assert admin_security.disable(client_id) is False
    admin_security.enroll(client_id, "JBSWY3DPEHPK3PXP")
    assert admin_security.disable(client_id) is True
    assert admin_security.is_enabled(client_id) is False
