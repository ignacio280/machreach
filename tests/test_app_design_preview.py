"""App-design preview contracts.

/design/* serves static design mocks with sample data. The point of these tests
is the boundary: the preview must never be mistaken for the live student app,
must never be indexed, and must not require or grant auth.
"""
import re
from pathlib import Path

from student.app_design import available_pages


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "static" / "machreach_app"


def test_every_registered_page_is_built():
    """A slug in the registry with no artifact would 302 to the index instead of
    rendering, which is easy to miss."""
    for slug in available_pages():
        assert (APP_DIR / f"{slug}.prod.html").is_file(), f"{slug}.prod.html not built"
        assert (APP_DIR / f"{slug}.bundle.min.js").is_file(), f"{slug}.bundle.min.js not built"


def test_every_built_page_has_a_bootstrap_source():
    """Guards against a stale artifact lingering after its source is deleted."""
    for slug in available_pages():
        assert (APP_DIR / "pages" / f"{slug}.jsx").is_file(), f"no source for {slug}"


def test_preview_index_lists_pages_and_warns_they_are_mocks(client):
    body = client.get("/design/").get_data(as_text=True)
    assert "static mocks" in body
    for slug in available_pages():
        assert f"/design/{slug}" in body


def test_preview_pages_render_the_built_artifact(client):
    for slug in available_pages():
        r = client.get(f"/design/{slug}")
        assert r.status_code == 200, f"/design/{slug} returned {r.status_code}"
        body = r.get_data(as_text=True)
        assert '<div id="root"></div>' not in body, f"{slug}: prerender empty"
        assert "unpkg.com" not in body, f"{slug}: still points at the CDN"
        assert "text/babel" not in body, f"{slug}: still needs in-browser Babel"


def test_preview_is_not_indexable(client):
    """These are mocks with fabricated numbers; they must never reach search."""
    for slug in list(available_pages()) + [""]:
        r = client.get(f"/design/{slug}")
        assert r.headers.get("X-Robots-Tag") == "noindex", f"/design/{slug} indexable"
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /design/" in robots


def test_unknown_preview_page_redirects_to_the_index(client):
    r = client.get("/design/definitely-not-a-page", follow_redirects=False)
    assert r.status_code == 302
    assert "/design/" in r.headers["Location"]


def test_preview_never_touches_the_live_student_routes(client):
    """The mocks are unauthenticated. /student must still require a session, so
    the preview cannot become a way around auth."""
    r = client.get("/student", follow_redirects=False)
    assert r.status_code in (301, 302), "/student should redirect anonymous users"
    assert "/design/" not in r.headers.get("Location", "")


def test_preview_pages_do_not_receive_live_payloads():
    """The same components can power live routes, but preview artifacts must
    remain prerendered mocks with no injected account payload."""
    for slug in available_pages():
        html = (APP_DIR / f"{slug}.prod.html").read_text(encoding="utf-8")
        assert "window.__MACHREACH_APP__={" not in html
        assert "window.__MACHREACH_DASHBOARD__={" not in html


def test_design_system_is_shared_with_the_landing_not_forked():
    """theme.css is edited in place; the app pages must link the landing copy so
    the two surfaces cannot drift."""
    build = (ROOT / "landing_build" / "build-app.mjs").read_text(encoding="utf-8")
    assert 'SHARED_CSS = ["v6/theme.css"]' in build
    assert not (APP_DIR / "v6" / "theme.css").exists(), "theme.css forked into the app dir"
    for slug in available_pages():
        html = (APP_DIR / f"{slug}.prod.html").read_text(encoding="utf-8")
        assert "/static/machreach_landing/v6/theme.css" in html


def test_app_bundle_version_is_stamped(client):
    build = (ROOT / "landing_build" / "build-app.mjs").read_text(encoding="utf-8")
    version = re.search(r'BUNDLE_VERSION\s*=\s*"([^"]+)"', build).group(1)
    for slug in available_pages():
        body = client.get(f"/design/{slug}").get_data(as_text=True)
        assert f"{slug}.bundle.min.js?v={version}" in body
