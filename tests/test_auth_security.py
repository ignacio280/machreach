from datetime import datetime, timedelta

from machreach_core.db import (
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


def test_language_redirect_never_trusts_an_external_referrer(client):
    external = client.get(
        "/set-language/en",
        headers={"Referer": "https://attacker.example/phish?from=machreach"},
    )
    same_origin = client.get(
        "/set-language/es",
        headers={"Referer": "http://localhost/student/dashboard?tab=today"},
    )

    assert external.status_code == 302
    assert external.headers["Location"] == "/"
    assert same_origin.headers["Location"] == "/student/dashboard?tab=today"


# --- Password change and reset over HTTP -----------------------------------
#
# update_client_password bumping session_version is covered above at the
# database level. What follows covers the routes on top of it, where the
# interesting part is which sessions survive: changing your password from
# settings must keep you signed in on the device you are using and sign you
# out everywhere else, while resetting a forgotten password must sign out
# every session there is — that is the lever a user pulls when they think
# someone else is in their account.

def _sign_in(test_client, client_id, version=0):
    with test_client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = "Password User"
        sess["account_type"] = "student"
        sess["session_version"] = version


def _stored_password(client_id):
    with get_db() as db:
        return _fetchone(db, "SELECT password FROM clients WHERE id = %s", (client_id,))["password"]


def _make_user_with_password(password):
    import app as appmod

    email = f"pw_{datetime.now().timestamp():.6f}@example.com".replace(".", "_", 1)
    return create_client("Password User", email, appmod._hash_pw(password), "", "student"), email


def test_forgot_password_cannot_be_used_to_discover_registered_emails(
    client, flask_app, monkeypatch,
):
    """A known and an unknown address must be indistinguishable.

    The route sends mail in one case and not the other, so the only thing
    keeping it from being an account-enumeration oracle is that both paths end
    at the same redirect.
    """
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr("app._send_system_email", lambda *a, **k: None)
    from app import limiter as app_limiter
    app_limiter.reset()

    _, registered = _make_user_with_password("correct-horse-battery")
    known = client.post("/forgot-password", data={"email": registered})
    unknown = client.post("/forgot-password", data={"email": "nobody-here@example.com"})

    assert known.status_code == unknown.status_code == 302
    # Same destination and same query shape; only the echoed address differs.
    assert known.headers["Location"].split("&email=")[0] == \
        unknown.headers["Location"].split("&email=")[0]
    assert "sent=1" in known.headers["Location"]
    assert "sent=1" in unknown.headers["Location"]


def test_changing_a_password_requires_the_current_one(client, flask_app, monkeypatch):
    """Otherwise a stolen session cookie is enough to take the account."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid, _ = _make_user_with_password("original-password")
    before = _stored_password(cid)
    _sign_in(client, cid)

    client.post("/settings/change-password", data={
        "current_password": "not-the-password",
        "new_password": "brand-new-password",
        "confirm_password": "brand-new-password",
    })

    assert _stored_password(cid) == before


def test_a_password_change_is_refused_when_the_confirmation_does_not_match(
    client, flask_app, monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid, _ = _make_user_with_password("original-password")
    before = _stored_password(cid)
    _sign_in(client, cid)

    client.post("/settings/change-password", data={
        "current_password": "original-password",
        "new_password": "brand-new-password",
        "confirm_password": "brand-new-passwrod",
    })

    assert _stored_password(cid) == before


def test_a_password_change_is_refused_when_the_new_password_is_too_short(
    client, flask_app, monkeypatch,
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    import app as appmod

    cid, _ = _make_user_with_password("original-password")
    before = _stored_password(cid)
    _sign_in(client, cid)
    short = "a" * (appmod.PASSWORD_MIN_LENGTH - 1)

    client.post("/settings/change-password", data={
        "current_password": "original-password",
        "new_password": short,
        "confirm_password": short,
    })

    assert _stored_password(cid) == before


def test_changing_a_password_keeps_this_session_and_signs_out_the_others(
    flask_app, monkeypatch,
):
    """The point of bumping session_version, from both sides at once.

    The device doing the change re-syncs to the new version; a device that was
    already signed in does not, and is cleared on its next request. Testing
    only one side would let the wrong half regress unnoticed.
    """
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid, _ = _make_user_with_password("original-password")
    here = flask_app.test_client()
    elsewhere = flask_app.test_client()
    _sign_in(here, cid)
    _sign_in(elsewhere, cid)

    here.post("/settings/change-password", data={
        "current_password": "original-password",
        "new_password": "brand-new-password",
        "confirm_password": "brand-new-password",
    })
    elsewhere.get("/dashboard")

    with here.session_transaction() as sess:
        assert sess.get("client_id") == cid
    with elsewhere.session_transaction() as sess:
        assert "client_id" not in sess


def test_a_reset_token_cannot_be_replayed(client, flask_app, monkeypatch):
    """One token, one password. A second use must not set a third password."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    from app import limiter as app_limiter
    app_limiter.reset()

    cid, _ = _make_user_with_password("original-password")
    token = "reset-replay-token"
    create_reset_token(cid, token, _future_ts())

    first = client.post(f"/reset-password/{token}", data={
        "password": "first-new-password", "password2": "first-new-password",
    })
    after_first = _stored_password(cid)
    second = client.post(f"/reset-password/{token}", data={
        "password": "second-new-password", "password2": "second-new-password",
    })

    assert first.headers["Location"].endswith("ok=1")
    assert "expired=1" in second.headers["Location"]
    assert _stored_password(cid) == after_first


def test_resetting_a_forgotten_password_signs_out_every_existing_session(
    flask_app, monkeypatch,
):
    """The recovery path assumes someone else may already be signed in."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    from app import limiter as app_limiter
    app_limiter.reset()

    cid, _ = _make_user_with_password("original-password")
    intruder = flask_app.test_client()
    _sign_in(intruder, cid)
    token = "reset-signout-token"
    create_reset_token(cid, token, _future_ts())

    flask_app.test_client().post(f"/reset-password/{token}", data={
        "password": "recovered-password", "password2": "recovered-password",
    })
    intruder.get("/dashboard")

    with intruder.session_transaction() as sess:
        assert "client_id" not in sess


def test_a_login_with_no_session_is_handed_back_the_form_not_a_raw_400(client):
    """The exact dead end students hit: a login page left open past the 24h
    cookie posts with a token but no session to check it against, and the reply
    was "Bad Request: The CSRF session token is missing." with no way out but
    retyping the URL. /register already recovered; /login did not."""
    # A token from the stale page, and no session cookie to check it against.
    response = client.post(
        "/login",
        data={"csrf_token": "token-from-a-page-older-than-the-cookie",
              "email": "x@example.test", "password": "y"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert "CSRF session token is missing" not in response.get_data(as_text=True)

    # The redirect carries a fresh session, so the retry has a token that matches.
    retried = client.get("/login")
    assert retried.status_code == 200


def test_the_password_reset_request_recovers_the_same_way(client):
    response = client.post(
        "/forgot-password",
        data={"csrf_token": "token-from-a-page-older-than-the-cookie",
              "email": "x@example.test"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/forgot-password")


def test_a_state_changing_route_still_refuses_a_request_with_no_csrf_token(client):
    """Only the auth forms are handed back. Everything else must keep failing:
    silently retrying a state-changing POST is what CSRF protection prevents."""
    response = client.post(
        "/logout", data={"csrf_token": "token-from-a-page-older-than-the-cookie"}
    )

    assert response.status_code == 400
    assert "Bad Request" in response.get_data(as_text=True)
