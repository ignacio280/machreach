"""Client-cache invalidation contracts.

The service worker keys its static cache on VERSION, and layout assets are
cached by URL. Both must change on deploy without anyone remembering to edit a
constant, or returning users run stale JS/CSS against new HTML.
"""
import re

import app as appmod


VERSION_LINE = re.compile(r"""const\s+VERSION\s*=\s*['"]([^'"]+)['"]""")


def test_service_worker_is_stamped_with_the_deploy_version(client):
    body = client.get("/sw.js").get_data(as_text=True)
    served = VERSION_LINE.search(body)
    assert served, "service worker no longer declares a VERSION constant"
    assert served.group(1) == appmod.DEPLOY_VERSION


def test_served_version_does_not_depend_on_the_authored_constant(client):
    """The value on disk is a placeholder; the response must not echo it."""
    from pathlib import Path
    source = (Path(appmod.app.static_folder) / "sw.js").read_text(encoding="utf-8")
    authored = VERSION_LINE.search(source).group(1)
    served = VERSION_LINE.search(client.get("/sw.js").get_data(as_text=True)).group(1)
    # Only meaningful when they actually differ, which is the normal case.
    if authored != appmod.DEPLOY_VERSION:
        assert served != authored


def test_service_worker_keeps_its_scope_and_revalidates(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["Service-Worker-Allowed"] == "/"
    assert "javascript" in r.headers["Content-Type"]
    # A cached service worker script would defeat the versioning above.
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_deploy_version_prefers_the_render_commit():
    """The commit is the only value that changes exactly once per deploy."""
    assert appmod.resolve_deploy_version("0123456789abcdef") == "0123456789ab"
    assert appmod.resolve_deploy_version("  0123456789abcdef  ") == "0123456789ab"


def test_deploy_version_falls_back_to_a_restart_stamp():
    """Without a commit there is no deploy, so the value must still change
    between restarts rather than pinning to a constant."""
    from datetime import datetime, timezone
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    late = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    first = appmod.resolve_deploy_version(None, now=early)
    second = appmod.resolve_deploy_version("", now=late)
    assert first.startswith("dev-") and second.startswith("dev-")
    assert first != second


def test_layout_assets_carry_a_cache_busting_query(client):
    """Every layout asset must be version-stamped, or a long-lived cache would
    serve yesterday's CSS against today's markup."""
    html = client.get("/login").get_data(as_text=True)
    referenced = re.findall(r"/static/machreach_layout/([\w.-]+\.(?:css|js))(\?[^\"']*)?", html)
    assert referenced, "no layout assets referenced on /login"
    unstamped = [name for name, query in referenced if "v=" not in (query or "")]
    assert not unstamped, f"unversioned layout assets: {sorted(set(unstamped))}"
