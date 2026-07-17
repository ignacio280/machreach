import re

from machreach_core.csp import harden_html


def test_csp_uses_nonce_and_removes_inline_exceptions(client):
    response = client.get("/login")
    body = response.get_data(as_text=True)
    csp = response.headers["Content-Security-Policy"]

    assert "'unsafe-inline'" not in csp
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert nonce
    assert "script-src-attr 'none'" in csp
    assert "style-src-attr 'none'" in csp
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", body, re.IGNORECASE)
    assert not re.search(r"<[^>]*\sstyle\s*=", body, re.IGNORECASE)
    assert all(f'nonce="{nonce}"' in tag for tag in re.findall(r"<script\b[^>]*>", body, re.IGNORECASE))
    assert all(f'nonce="{nonce}"' in tag for tag in re.findall(r"<style\b[^>]*>", body, re.IGNORECASE))


def test_csp_rewriter_preserves_inline_handler_behavior_through_delegation(client):
    response = client.get("/cookies")
    body = response.get_data(as_text=True)

    assert "data-mr-csp-click=" in body
    assert "addEventListener(eventName" in body
    assert "analytics_consent=0" in body
    assert "mr-csp-style-" in body


def test_csp_rewriter_keeps_dynamic_fragment_handlers_as_safe_calls(client, make_user):
    client_id = make_user("CSP Dynamic Student")
    from machreach_core.db import _exec, get_db

    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "CSP Dynamic Student"
        session["account_type"] = "student"
        session["session_version"] = 0
    body = client.get("/student/courses").get_data(as_text=True)

    assert "data-mr-csp-call-click" in body
    assert "data-mr-csp-call-change" in body
    assert "function(event){deleteExamFile(' +" not in body
    assert "function invoke(code,node,event)" in body


def test_dynamic_dispatcher_supports_only_required_control_values():
    document = """<html><head></head><body><script>
    var html = '<input oninput="update(' + index + ',this.value)">' +
      '<input onchange="toggle(' + index + ',this.checked)">' +
      '<input onchange="upload(this.files[0])">';
    </script></body></html>"""

    hardened = harden_html(document, "test-nonce")

    assert "token==='this.value'" in hardened
    assert "token==='this.checked'" in hardened
    assert r"/^this\.files\[(\d+)\]$/" in hardened
    assert "eval(" not in hardened
