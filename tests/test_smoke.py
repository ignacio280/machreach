"""Smoke tests: key routes respond without server errors, and the recent
CSRF / canonical-host fixes behave as intended."""
import pytest


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


def test_www_redirects_to_apex(client):
    r = client.get("/login?x=1", base_url="https://www.machreach.com")
    assert r.status_code == 301
    assert r.headers["Location"] == "https://machreach.com/login?x=1"


def test_csrf_blocks_post_without_token(client):
    # A CSRF-protected POST with no token must be rejected.
    r = client.post("/login", data={"email": "a@b.com", "password": "x"})
    assert r.status_code == 400
