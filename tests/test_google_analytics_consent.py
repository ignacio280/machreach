"""Google Analytics may not run until the student has said yes.

The container is loaded through Google Tag Manager, which sets Google's own
cookies and reports behaviour off-site. It is gated on the same
`analytics_consent` cookie as PostHog, and the gate is server-side: without
consent the snippet is never written into the page, so there is nothing to
opt out of after the fact.
"""
import app as appmod


GTM_HOST = "www.googletagmanager.com"


def _page(client) -> str:
    return client.get("/login").get_data(as_text=True)


def test_no_tag_manager_before_consent(client):
    page = _page(client)
    assert GTM_HOST not in page
    assert appmod._GTM_CONTAINER_ID not in page


def test_no_tag_manager_when_consent_is_refused(client):
    client.set_cookie("analytics_consent", "0")
    page = _page(client)
    assert GTM_HOST not in page


def test_tag_manager_loads_once_consent_is_given(client):
    client.set_cookie("analytics_consent", "1")
    page = _page(client)

    # The head snippet and the noscript fallback, both pointing at our container.
    assert f"https://{GTM_HOST}/ns.html?id={appmod._GTM_CONTAINER_ID}" in page
    assert "gtm.start" in page
    assert appmod._GTM_CONTAINER_ID in page


def test_the_policy_only_admits_google_while_a_container_is_configured(client):
    client.set_cookie("analytics_consent", "1")
    response = client.get("/login")
    csp = response.headers["Content-Security-Policy"]

    # Loading the container, and letting Analytics report back.
    assert f"https://{GTM_HOST}" in csp
    assert "https://www.google-analytics.com" in csp
    # The noscript iframe is framed from the same host.
    frame_src = next(part for part in csp.split(";") if part.strip().startswith("frame-src"))
    assert GTM_HOST in frame_src
    # Widening for Google must not have cost us the nonce discipline.
    assert "'unsafe-inline'" not in csp


def test_an_unset_container_leaves_the_policy_untouched(client, monkeypatch):
    """A deployment that does not want Google Analytics keeps the tighter CSP."""
    monkeypatch.setattr(appmod, "_GTM_CONTAINER_ID", "")
    client.set_cookie("analytics_consent", "1")
    response = client.get("/login")

    assert GTM_HOST not in response.headers["Content-Security-Policy"]
    assert GTM_HOST not in response.get_data(as_text=True)
