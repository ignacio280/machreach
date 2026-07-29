"""Money path: deleting an account MUST cancel recurring billing at the provider.

Regression for the bug where a deleted account kept getting charged every month:
the delete route wiped the local subscription rows but never told Lemon Squeezy to
stop the subscription.
"""
import hashlib
import hmac
import json
import os

from machreach_core import config as cfg, lemonsqueezy as ls
from machreach_core.db import get_client

SECRET = os.environ["LEMON_SQUEEZY_WEBHOOK_SECRET"].encode()


def _sign(raw: bytes) -> str:
    return hmac.new(SECRET, raw, hashlib.sha256).hexdigest()


def _provision_student_sub(client, cid, ls_sub_id):
    """Drive the real webhook so the LS subscription id gets stashed exactly the
    way production stashes it (clients.mail_preferences -> subscription.ls_sub_id)."""
    payload = {
        "meta": {"event_name": "subscription_created",
                 "custom_data": {"purpose": "student_sub", "client_id": str(cid), "tier": "plus"}},
        "data": {"id": ls_sub_id, "attributes": {
            "status": "active", "variant_id": cfg.LS_VARIANT_STUDENT_PLUS,
        }},
    }
    raw = json.dumps(payload).encode()
    r = client.post("/webhooks/lemonsqueezy", data=raw, content_type="application/json",
                    headers={"X-Signature": _sign(raw)})
    assert r.status_code == 200


def _login(client, cid):
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["account_type"] = "student"
        sess["email"] = "x@example.com"
        sess["session_version"] = 0


def test_delete_account_cancels_ls_subscription(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _provision_student_sub(client, cid, "sub_LIVE_42")

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    _login(client, cid)
    r = client.post("/settings/delete-account", data={"confirm": "DELETE"})

    assert r.status_code in (302, 303)         # redirect to index on success
    assert calls == ["sub_LIVE_42"]            # provider was told to stop charging


def test_delete_free_account_does_not_call_provider(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()                          # never subscribed

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    _login(client, cid)
    r = client.post("/settings/delete-account", data={"confirm": "DELETE"})

    assert r.status_code in (302, 303)
    assert calls == []                         # nothing to cancel for a free user


def test_delete_without_confirmation_keeps_account_and_billing(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _provision_student_sub(client, cid, "sub_LIVE_99")

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    _login(client, cid)
    r = client.post("/settings/delete-account", data={"confirm": "nope"})

    assert r.status_code in (302, 303)         # bounced back with an error flash
    assert calls == []                         # no deletion -> no cancellation


def test_delete_account_keeps_local_account_when_provider_cancellation_fails(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _provision_student_sub(client, cid, "sub_RETRY_1")
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: False)

    _login(client, cid)
    response = client.post("/settings/delete-account", data={"confirm": "DELETE"})

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/settings")
    assert get_client(cid) is not None
