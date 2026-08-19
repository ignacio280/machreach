"""Response compression contracts.

Pages ship their CSS/JS inline, so bodies are large and highly repetitive.
Compression must never change what the client ends up parsing, and must not
run before the CSP pass that rewrites the HTML body.
"""
import gzip
import re


def _both(client, path):
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    gz = client.get(path, headers={"Accept-Encoding": "gzip, deflate, br"})
    return plain, gz


def test_html_is_compressed_and_shrinks(client):
    plain, gz = _both(client, "/login")
    assert gz.headers["Content-Encoding"] == "gzip"
    assert len(gz.get_data()) < len(plain.get_data()) / 2


def test_declared_length_matches_compressed_body(client):
    gz = client.get("/login", headers={"Accept-Encoding": "gzip"})
    assert gz.headers["Content-Length"] == str(len(gz.get_data()))


def test_compression_preserves_the_document(client):
    """Decompressed bytes must equal the identity response, modulo the values
    that are minted per request: the CSP nonce and the CSRF token."""
    plain, gz = _both(client, "/login")
    decoded = gzip.decompress(gz.get_data()).decode("utf-8")

    def normalize(body, response):
        nonce = response.headers["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
        assert nonce in body, "CSP nonce missing from body"
        body = body.replace(nonce, "NONCE")
        # The CSRF token is signed with the time it was minted, so two requests
        # that straddle a second legitimately carry different tokens. Comparing
        # them made this test fail on the clock rather than on compression.
        token = re.search(r'name="csrf_token" value="([^"]+)"', body)
        assert token, "CSRF token missing from body"
        body = body.replace(token.group(1), "CSRF")
        # The same token is embedded again, base64-encoded, in the bootstrap
        # payload, where a substring replace cannot reach it.
        return re.sub(r'atob\("[^"]*"\)', 'atob("PAYLOAD")', body)

    assert normalize(decoded, gz) == normalize(plain.get_data(as_text=True), plain)


def test_csp_hardening_runs_before_compression(client):
    """The nonce lands in the body only if harden_html ran first. If ordering
    regressed, compression would gzip pre-rewrite markup and the nonce would
    be absent (or the body would be an unparseable gzip blob)."""
    gz = client.get("/login", headers={"Accept-Encoding": "gzip"})
    body = gzip.decompress(gz.get_data()).decode("utf-8")
    nonce = gz.headers["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in body
    assert body.lstrip().lower().startswith("<!doctype html>")


def test_no_compression_without_client_support(client):
    r = client.get("/login", headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in r.headers
    # Caches still have to key on encoding even when we did not compress.
    assert "Accept-Encoding" in r.headers.get("Vary", "")


def test_vary_accept_encoding_is_always_set(client):
    for headers in ({"Accept-Encoding": "gzip"}, {"Accept-Encoding": "identity"}):
        r = client.get("/login", headers=headers)
        assert "Accept-Encoding" in r.headers.get("Vary", "")


def test_tiny_responses_are_left_alone(client):
    r = client.get("/robots.txt", headers={"Accept-Encoding": "gzip"})
    assert len(r.get_data()) < 1024
    assert "Content-Encoding" not in r.headers


def test_redirects_are_not_compressed(client):
    r = client.get("/student", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert r.status_code == 302
    assert "Content-Encoding" not in r.headers


def test_static_assets_are_compressed(client):
    plain, gz = _both(client, "/static/machreach_layout/layout-base.css")
    assert gz.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(gz.get_data()) == plain.get_data()


def test_gzip_response_carries_a_distinct_etag(client):
    """A shared cache must not serve this gzip body to an identity request."""
    plain, gz = _both(client, "/static/machreach_layout/layout-base.css")
    assert plain.headers["ETag"] != gz.headers["ETag"]
    assert gz.headers["ETag"].endswith('-gzip"')


def test_static_revalidation_returns_304_without_a_body(client):
    first = client.get("/static/machreach_layout/layout-base.css",
                       headers={"Accept-Encoding": "identity"})
    second = client.get("/static/machreach_layout/layout-base.css",
                        headers={"If-None-Match": first.headers["ETag"],
                                 "Accept-Encoding": "identity"})
    assert second.status_code == 304
    assert second.get_data() == b""
