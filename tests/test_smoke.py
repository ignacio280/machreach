"""Smoke tests: key routes respond without server errors, and the recent
CSRF / canonical-host fixes behave as intended."""
from contextlib import contextmanager
import base64
import json
from pathlib import Path
import re

import pytest

from machreach_core.db import SCHEMA_VERSION, _exec, _fetchone, get_db
from student import db as sdb


@pytest.mark.parametrize("path", [
    "/", "/login", "/register", "/about",
    "/privacy", "/terms", "/cookies", "/health",
    "/robots.txt", "/sitemap.xml", "/favicon.ico",
])
def test_public_routes_no_server_error(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} returned {r.status_code}"


def test_health_reports_db_connected(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "healthy"}


def test_health_hides_database_exception_details(client, monkeypatch):
    import machreach_core.db as odb

    @contextmanager
    def broken_db():
        raise RuntimeError("postgres://secret-user:secret-pass@private-host/db")
        yield

    monkeypatch.setattr(odb, "get_db", broken_db)
    r = client.get("/health")

    assert r.status_code == 503
    assert r.get_json() == {"status": "degraded"}
    assert "secret-pass" not in r.get_data(as_text=True)


def test_an_outdated_schema_is_reported_without_killing_the_instance(client, monkeypatch):
    """Liveness and correctness are different questions.

    Render restarts whatever fails /health, so answering "degraded" there over a
    schema mismatch turns a warning into an outage across every instance. The
    mismatch still has to be caught: migrate.py refuses to finish a deploy on
    it, and the operations probe reports it to whoever is monitoring.
    """
    import app as appmod
    from machreach_core.db import check_schema_readiness

    monkeypatch.setattr(appmod, "OPERATIONS_SECRET", "monitor-secret")
    with get_db() as db:
        _exec(
            db,
            "UPDATE schema_metadata SET version = %s WHERE component = %s",
            (SCHEMA_VERSION - 1, "core"),
        )
    try:
        assert client.get("/health").status_code == 200

        probe = client.get(
            "/health/operations",
            headers={"X-Operations-Secret": "monitor-secret"},
        )
        assert probe.get_json()["checks"]["schema"]["status"] == "alert"

        with pytest.raises(RuntimeError):
            check_schema_readiness()
    finally:
        with get_db() as db:
            _exec(
                db,
                "UPDATE schema_metadata SET version = %s WHERE component = %s",
                (SCHEMA_VERSION, "core"),
            )


def test_removed_reset_all_accounts_endpoint_is_not_registered(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    r = client.post("/api/admin/reset-all-accounts")

    assert r.status_code == 404


def test_admin_jobs_page_renders_for_admin(client, make_user):
    from datetime import datetime, timezone
    from machreach_core import admin_security

    cid = make_user("Admin Jobs Owner", "admin-jobs@example.com")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (cid,))
    admin_security.enroll(cid, "JBSWY3DPEHPK3PXP")
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Admin Jobs Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0
        sess["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()

    r = client.get("/admin/jobs")

    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Jobs & worker" in body
    assert "Worker heartbeat" in body


def test_register_shows_referral_banner_with_ref(client):
    r = client.get("/register?ref=ABC1234")
    body = r.get_data(as_text=True)
    assert 'name="ref" value="ABC1234"' in body


def test_register_has_no_ref_field_without_code(client):
    body = client.get("/register").get_data(as_text=True)
    assert 'name="ref"' not in body


def test_friends_tab_keeps_referral_invite_card(client, make_user):
    cid = make_user("Referral Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Referral Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0
        sess["lang"] = "es"

    r = client.get("/student/friends")
    body = r.get_data(as_text=True)
    code = sdb.get_or_create_referral_code(cid)

    assert r.status_code == 200
    encoded = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', body).group(1)
    payload = json.loads(base64.b64decode(encoded))
    source = (Path(__file__).parents[1] / "static/machreach_app/app/friends.jsx").read_text(encoding="utf-8")
    assert "/static/machreach_app/amigos.bundle.min.js" in body
    assert payload["friends"]["referral_link"].endswith(f"/register?ref={code}")
    assert "Invita amigos, gana Plus" in source
    assert "/student/invite" not in body


def test_legacy_invite_page_redirects_to_friends(client, make_user):
    cid = make_user("Legacy Invite Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Legacy Invite Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    response = client.get("/student/invite")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/student/friends")


def test_www_redirects_to_apex(client):
    r = client.get("/login?x=1", base_url="https://www.machreach.com")
    assert r.status_code == 301
    assert r.headers["Location"] == "https://machreach.com/login?x=1"


def test_csrf_blocks_post_without_token(client):
    # A CSRF-protected POST with no token must be rejected.
    r = client.post("/login", data={"email": "a@b.com", "password": "x"})
    assert r.status_code == 400


def test_register_form_has_server_rendered_csrf_field(client):
    body = client.get("/register").get_data(as_text=True)
    assert '<input type="hidden" name="csrf_token"' in body


def test_auth_labels_are_associated_with_inputs(client):
    register = client.get("/register").get_data(as_text=True)
    login = client.get("/login").get_data(as_text=True)

    assert '<label for="register-name">' in register
    assert 'id="register-name"' in register
    assert '<label for="login-email">' in login
    assert 'id="login-email"' in login
    assert '<label for="login-password">' in login


def test_logout_requires_post(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Logout User", "logout@example.test")
    with client.session_transaction() as session:
        session["client_id"] = client_id

    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    with client.session_transaction() as session:
        assert "client_id" not in session


def test_onboarding_uses_keyboard_options_and_live_errors(client, make_user):
    client_id = make_user("Setup User", "setup-accessibility@example.test")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Setup User"
        session["account_type"] = "student"
        session["session_version"] = 0

    body = client.get("/student/setup").get_data(as_text=True)

    # Onboarding is a React page now, so the accessible markup lives in the
    # bundle the page loads rather than in the server response.
    assert "/static/machreach_app/setup.bundle.min.js" in body
    source = open("static/machreach_app/app/setup.jsx", encoding="utf-8").read()
    assert 'htmlFor="su-cq"' in source and 'htmlFor="su-uq"' in source and 'htmlFor="su-mq"' in source
    assert 'id="su-univ-list" role="listbox" aria-live="polite"' in source
    assert source.count('role="option"') == 3
    assert 'id="su-err" role="alert" aria-live="assertive"' in source


def test_posthog_loads_only_after_analytics_consent(client, monkeypatch):
    monkeypatch.setenv("POSTHOG_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")

    declined = client.get("/login")
    assert "posthog.init('phc_test_key'" not in declined.get_data(as_text=True)
    assert "Solo esenciales" in declined.get_data(as_text=True)

    client.set_cookie("analytics_consent", "1")
    allowed = client.get("/login")
    assert "posthog.init('phc_test_key'" in allowed.get_data(as_text=True)
    csp = allowed.headers["Content-Security-Policy"]
    assert "https://eu.i.posthog.com" in csp
    assert "https://eu-assets.i.posthog.com" in csp
    assert "'unsafe-eval'" not in csp


def test_stale_register_csrf_redirects_instead_of_raw_bad_request(client):
    body = client.get("/register").get_data(as_text=True)
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', body).group(1)
    with client.session_transaction() as sess:
        sess.pop("csrf_token", None)

    r = client.post(
        "/register",
        data={
            "name": "CSRF User",
            "email": "csrf-user@example.com",
            "password": "secret123",
            "password2": "secret123",
            "csrf_token": token,
        },
    )

    assert r.status_code == 302
    assert r.headers["Location"].endswith("/register")


def test_student_can_delete_uploaded_course_file(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user("Course File Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))
    course_id = sdb.create_manual_course(cid, "Math", "MATH101")
    file_id = sdb.add_course_file(
        cid,
        course_id,
        "prueba.pdf",
        "pdf",
        "contenido de prueba",
    )

    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Course File Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    preview = client.get(f"/api/student/files/{file_id}")
    assert preview.status_code == 200
    assert preview.get_json()["name"] == "prueba.pdf"

    r = client.delete(f"/api/student/files/{file_id}")

    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    with get_db() as db:
        assert _fetchone(
            db,
            "SELECT id FROM student_course_files WHERE id = %s",
            (file_id,),
        ) is None


def test_courses_page_uses_clear_delete_and_stable_theme_controls(client, make_user):
    cid = make_user("Course Theme Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))
    sdb.create_manual_course(cid, "Ruta Novata UC 2024", "CUR-RUT-NOV-1_2024")

    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Course Theme Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    body = client.get("/student/courses").get_data(as_text=True)

    source = (Path(__file__).parents[1] / "static/machreach_app/app/courses.jsx").read_text(encoding="utf-8")
    theme_source = (Path(__file__).parents[1] / "static/machreach_landing/v5/tweaks-panel.jsx").read_text(encoding="utf-8")
    assert "/static/machreach_app/cursos.bundle.min.js" in body
    assert 'aria-label="Eliminar curso"' in source
    assert 'className="ccard-menu"' not in source
    assert 'localStorage.setItem("machreach-theme"' in theme_source
    encoded = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', body).group(1)
    payload = json.loads(base64.b64decode(encoded))
    assert any(course["name"] == "Ruta Novata UC 2024" for course in payload["courses"]["items"])
    assert "Privacidad del benchmark:" not in body


def test_leaderboard_page_uses_readable_prizes_and_rank_translations(client, make_user):
    cid = make_user("Leaderboard Style Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1, country_iso = %s WHERE id = %s", ("CL", cid))

    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Leaderboard Style Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    body = client.get("/student/leaderboard").get_data(as_text=True)

    rank_css = (Path(__file__).parents[1] / "static/machreach_app/app/rank.css").read_text(encoding="utf-8")
    rank_source = (Path(__file__).parents[1] / "static/machreach_app/app/rank.jsx").read_text(encoding="utf-8")
    assert "/static/machreach_app/ranking.bundle.min.js" in body
    assert "/static/machreach_app/app/rank.css" in body
    assert ".prize" in rank_css
    assert "useRankRows" in rank_source


def test_shop_cards_have_dark_mode_surfaces(client, make_user):
    cid = make_user("Dark Shop Owner")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Dark Shop Owner"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    body = client.get("/student/shop").get_data(as_text=True)

    source = (Path(__file__).parents[1] / "static/machreach_app/app/shop.jsx").read_text(encoding="utf-8")
    css = (Path(__file__).parents[1] / "static/machreach_app/app/shop.css").read_text(encoding="utf-8")
    theme = (Path(__file__).parents[1] / "static/machreach_landing/v6/theme.css").read_text(encoding="utf-8")
    assert "/static/machreach_app/tienda.bundle.min.js" in body
    assert "CosCard" in source and "pack" in source and "PlanSection" in source
    assert "background:var(--surface)" in css
    assert '[data-theme="dark"]' in theme


def test_auth_pages_serve_the_design_shell_without_the_legacy_layout(client):
    """/login and /register are the Cuenta design: its own public top bar, its
    own stylesheet, and none of the legacy layout CSS that used to repaint the
    card through `.auth-card h1`-style rules."""
    for path, tag in (("/login", "Iniciar sesión"), ("/register", "Crear cuenta")):
        body = client.get(path).get_data(as_text=True)

        assert "/static/machreach_app/cuenta.bundle.min.js" in body
        assert "/static/machreach_app/app/pubtop.css" in body
        assert "/static/machreach_layout/layout-base.css" not in body
        assert tag in body


def test_the_top_loading_bar_is_gone_everywhere(client):
    layout = client.get("/login").get_data(as_text=True)
    css = open("static/machreach_layout/layout-base.css", encoding="utf-8").read()

    assert 'id="topbar-progress"' not in layout
    assert "topbarStart" not in layout
    assert "topbar-progress" not in css


def test_legacy_auth_card_rules_cannot_reach_the_design(client):
    """The legacy pages that still use `.auth-card` all sit inside
    `.auth-wrapper`, so every such rule is scoped to it."""
    import re

    css = open("static/machreach_layout/layout-base.css", encoding="utf-8").read()
    selectors = re.findall(r"([^{}]*)\{", css)
    unscoped = [
        part.strip()
        for selector in selectors
        for part in selector.split(",")
        if ".auth-card" in part and ".auth-wrapper" not in part
    ]

    assert unscoped == []


def test_no_service_worker_update_banner(client):
    """Updates install silently — the old "A MachReach update is ready" toast
    and its Update button are gone."""
    body = client.get("/login").get_data(as_text=True)

    assert "update is ready" not in body
    assert "pwaNotice" not in body
    assert "SKIP_WAITING" in body        # the update itself still applies
