"""Smoke tests: key routes respond without server errors, and the recent
CSRF / canonical-host fixes behave as intended."""
import re

import pytest

from outreach.db import _exec, _fetchone, get_db
from student import db as sdb


@pytest.mark.parametrize("path", [
    "/", "/login", "/register", "/about", "/blog", "/press",
    "/privacy", "/terms", "/roadmap", "/health",
    "/robots.txt", "/sitemap.xml", "/favicon.ico",
])
def test_public_routes_no_server_error(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} returned {r.status_code}"


def test_health_reports_db_connected(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json().get("db") == "connected"


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
    assert 'id="fr-referral-card"' in body
    assert f"/register?ref={code}" in body
    assert "Invita amigos, gana Plus" in body
    assert "/student/invite" in body


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
