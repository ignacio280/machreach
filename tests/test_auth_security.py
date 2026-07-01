from datetime import datetime, timedelta

from outreach.db import (
    _fetchone,
    create_client,
    create_reset_token,
    create_verification_token,
    get_db,
    get_valid_reset_token,
    get_valid_verification_token,
    update_client_password,
)


def _future_ts():
    return (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


def test_verification_tokens_are_hashed_at_rest(make_user):
    cid = make_user("Token Hash User")
    raw = "verify-raw-token"

    create_verification_token(cid, raw, _future_ts())

    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT token FROM email_verification_tokens WHERE client_id = %s ORDER BY id DESC LIMIT 1",
            (cid,),
        )

    assert row["token"] != raw
    assert row["token"].startswith("hmac_sha256:")
    assert get_valid_verification_token(raw)["client_id"] == cid
    assert get_valid_verification_token(row["token"]) is None


def test_reset_tokens_are_hashed_at_rest(make_user):
    cid = make_user("Reset Hash User")
    raw = "reset-raw-token"

    create_reset_token(cid, raw, _future_ts())

    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT token FROM password_reset_tokens WHERE client_id = %s ORDER BY id DESC LIMIT 1",
            (cid,),
        )

    assert row["token"] != raw
    assert row["token"].startswith("hmac_sha256:")
    assert get_valid_reset_token(raw)["client_id"] == cid
    assert get_valid_reset_token(row["token"]) is None


def test_stale_session_is_cleared_after_password_change(client, make_user):
    cid = make_user("Stale Session User")
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Stale Session User"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    update_client_password(cid, "new-hash")
    client.get("/dashboard")

    with client.session_transaction() as sess:
        assert "client_id" not in sess


def test_debug_routes_are_disabled_by_default(client, flask_app, monkeypatch):
    monkeypatch.delenv("ENABLE_DEBUG_ENDPOINTS", raising=False)
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    smtp = client.get("/api/debug/smtp-test")
    db = client.post("/api/admin/check-db", headers={"X-Admin-Key": "test-secret-key"})

    assert smtp.status_code == 404
    assert db.status_code == 404
    assert "clients" not in (db.get_json() or {})


def test_email_lookup_is_case_insensitive():
    cid = create_client("Case User", "Case.User@Example.COM", "hash", "", "student")

    with get_db() as db:
        row = _fetchone(db, "SELECT email FROM clients WHERE id = %s", (cid,))

    assert row["email"] == "case.user@example.com"
