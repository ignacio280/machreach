"""Money path: the Lemon Squeezy webhook that provisions/revokes paid tiers."""
import hashlib
import hmac
import json
import os

from student import subscription as ssub

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


def test_coin_pack_order_is_acked(client, make_user):
    cid = make_user()
    payload = {"meta": {"event_name": "order_created",
                        "custom_data": {"purpose": "coin_pack", "client_id": str(cid), "pack_key": "small"}},
               "data": {"id": "order_1", "attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 200
