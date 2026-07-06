"""Money path: the Lemon Squeezy webhook that provisions/revokes paid tiers."""
import hashlib
import hmac
import json
import os

from student import subscription as ssub
from student import db as sdb
from outreach import lemonsqueezy as ls
from outreach.db import get_db, _exec

SECRET = os.environ["LEMON_SQUEEZY_WEBHOOK_SECRET"].encode()
URL = "/webhooks/lemonsqueezy"


def _sign(raw: bytes) -> str:
    return hmac.new(SECRET, raw, hashlib.sha256).hexdigest()


def _post(client, payload: dict, sign=True):
    raw = json.dumps(payload).encode()
    sig = _sign(raw) if sign else "deadbeef"
    return client.post(URL, data=raw, content_type="application/json",
                       headers={"X-Signature": sig})


def _student_event(event, cid, tier="plus"):
    return {
        "meta": {"event_name": event,
                 "custom_data": {"purpose": "student_sub", "client_id": str(cid), "tier": tier}},
        "data": {"id": "sub_test_1", "attributes": {"status": "active"}},
    }


def _login_student(client, cid):
    with client.session_transaction() as sess:
        sess["client_id"] = cid
        sess["client_name"] = "Student Billing"
        sess["account_type"] = "student"
        sess["email"] = "student@example.com"
        sess["session_version"] = 0


def _complete_setup(cid):
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (cid,))


def test_subscription_created_grants_plus(client, make_user):
    cid = make_user()
    assert ssub.get_tier(cid) == "free"
    r = _post(client, _student_event("subscription_created", cid, "plus"))
    assert r.status_code == 200
    assert ssub.get_tier(cid) == "plus"


def test_subscription_cancelled_reverts_to_free(client, make_user):
    cid = make_user()
    ssub.set_tier(cid, "plus")
    r = _post(client, _student_event("subscription_cancelled", cid))
    assert r.status_code == 200
    assert ssub.get_tier(cid) == "free"


def test_bad_signature_rejected(client, make_user):
    cid = make_user()
    r = _post(client, _student_event("subscription_created", cid, "plus"), sign=False)
    assert r.status_code == 401
    assert ssub.get_tier(cid) == "free"   # nothing provisioned on a forged call


def test_missing_client_id_is_acked_without_change(client):
    payload = {"meta": {"event_name": "subscription_created",
                        "custom_data": {"purpose": "student_sub", "tier": "plus"}},
               "data": {"id": "x", "attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 200   # ack so LS stops retrying


def test_coin_pack_order_credits_once(client, make_user):
    cid = make_user()
    payload = {"meta": {"event_name": "order_created",
                        "custom_data": {"purpose": "coin_pack", "client_id": str(cid), "pack_key": "small"}},
               "data": {"id": "order_1", "attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 250

    retry = _post(client, payload)
    assert retry.status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 250


def test_coin_pack_without_order_id_is_acked_without_credit(client, make_user):
    cid = make_user()
    payload = {"meta": {"event_name": "order_created",
                        "custom_data": {"purpose": "coin_pack", "client_id": str(cid), "pack_key": "small"}},
               "data": {"attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 0


def test_student_downgrade_cancels_provider_before_marking_free(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    _post(client, _student_event("subscription_created", cid, "plus"))
    assert ssub.get_tier(cid) == "plus"

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    _login_student(client, cid)
    r = client.post("/api/student/subscription/change", json={"tier": "free"})

    assert r.status_code == 200
    assert r.get_json()["tier"] == "free"
    assert calls == ["sub_test_1"]
    assert ssub.get_tier(cid) == "free"


def test_student_downgrade_failure_keeps_paid_tier(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    _post(client, _student_event("subscription_created", cid, "plus"))
    assert ssub.get_tier(cid) == "plus"

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or False)

    _login_student(client, cid)
    r = client.post("/api/student/subscription/change", json={"tier": "free"})

    assert r.status_code == 502
    assert r.get_json()["ok"] is False
    assert calls == ["sub_test_1"]
    assert ssub.get_tier(cid) == "plus"


def test_student_downgrade_without_provider_id_keeps_paid_tier(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    ssub.set_tier(cid, "plus")
    assert ssub.get_tier(cid) == "plus"

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    _login_student(client, cid)
    r = client.post("/api/student/subscription/change", json={"tier": "free"})

    assert r.status_code == 409
    assert r.get_json()["ok"] is False
    assert calls == []
    assert ssub.get_tier(cid) == "plus"
