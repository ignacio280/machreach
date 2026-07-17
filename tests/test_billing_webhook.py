"""Money path: the Lemon Squeezy webhook that provisions/revokes paid tiers."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os

from student import subscription as ssub
from student import db as sdb
from machreach_core import lemonsqueezy as ls
from machreach_core import config as cfg
from machreach_core.db import claim_webhook_event, get_db, _exec, _fetchone

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


def test_sandbox_webhook_is_limited_to_configured_qa_client(
    client, make_user, monkeypatch
):
    qa_id = make_user("Sandbox QA")
    other_id = make_user("Not Sandbox QA")
    sandbox_secret = "sandbox-signing-secret"
    monkeypatch.setattr(ls, "LEMON_SQUEEZY_TEST_WEBHOOK_SECRET", sandbox_secret)
    monkeypatch.setattr(cfg, "LEMON_SQUEEZY_SANDBOX_CLIENT_ID", str(qa_id))

    def post_sandbox(cid):
        payload = _student_event("subscription_created", cid, "plus")
        payload["meta"]["test_mode"] = True
        payload["data"]["id"] = f"sandbox-sub-{cid}"
        raw = json.dumps(payload).encode()
        signature = hmac.new(
            sandbox_secret.encode(), raw, hashlib.sha256
        ).hexdigest()
        return client.post(
            URL,
            data=raw,
            content_type="application/json",
            headers={"X-Signature": signature},
        )

    assert post_sandbox(qa_id).status_code == 200
    assert ssub.get_tier(qa_id) == "plus"
    assert post_sandbox(other_id).status_code == 403
    assert ssub.get_tier(other_id) == "free"


def test_subscription_cancelled_reverts_to_free(client, make_user):
    cid = make_user()
    ssub.set_tier(cid, "plus")
    r = _post(client, _student_event("subscription_cancelled", cid))
    assert r.status_code == 200
    assert ssub.get_tier(cid) == "free"


def test_legacy_ultimate_provider_event_is_cancelled_and_blocked(
    client, make_user, monkeypatch
):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))
    changed = _student_event("subscription_plan_changed", cid, "plus")
    changed["data"]["attributes"]["product_name"] = "Test Student Ultimate"
    changed["data"]["attributes"]["variant_id"] = 999999
    cancelled = []
    monkeypatch.setattr(
        ls, "cancel_subscription", lambda sid: cancelled.append(sid) or True
    )

    assert _post(client, changed).status_code == 200
    assert ssub.get_tier(cid) == "free"
    assert cancelled == ["sub_test_1"]


def test_legacy_stored_tier_migration_rewrites_provider_state(make_user):
    cid = make_user()
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET mail_preferences = %s WHERE id = %s",
            (
                json.dumps(
                    {
                        "subscription": {
                            "tier": "ultimate",
                            "status": "active",
                            "ls_sub_id": "legacy-ultimate-sub",
                        }
                    }
                ),
                cid,
            ),
        )

    assert ssub.normalize_legacy_subscription_tiers() == 1
    state = ssub.get_subscription_state(cid)
    assert state["tier"] == "free"
    assert state["status"] == "retired"
    with get_db() as db:
        row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (cid,))
        cancellation = _fetchone(
            db,
            "SELECT status FROM retired_billing_cancellations WHERE subscription_id = %s",
            ("legacy-ultimate-sub",),
        )
    assert json.loads(row["mail_preferences"])["subscription"]["tier"] == "free"
    assert cancellation == {"status": "pending"}


def test_payment_failure_revokes_access_and_success_restores_it(client, make_user):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))
    assert ssub.get_tier(cid) == "plus"

    failed = _student_event("subscription_payment_failed", cid, "plus")
    failed["data"]["attributes"]["status"] = "past_due"
    assert _post(client, failed).status_code == 200
    assert ssub.get_tier(cid) == "free"

    recovered = _student_event("subscription_payment_success", cid, "plus")
    assert _post(client, recovered).status_code == 200
    assert ssub.get_tier(cid) == "plus"


def test_payment_recovered_restores_access(client, make_user):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))

    failed = _student_event("subscription_payment_failed", cid, "plus")
    failed["data"]["attributes"]["status"] = "past_due"
    assert _post(client, failed).status_code == 200
    assert ssub.get_tier(cid) == "free"

    recovered = _student_event("subscription_payment_recovered", cid, "plus")
    assert _post(client, recovered).status_code == 200
    assert ssub.get_tier(cid) == "plus"


def test_full_subscription_refund_cancels_renewal_and_revokes_paid_access(
    client, make_user, monkeypatch
):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))
    cancelled = []
    monkeypatch.setattr(
        ls, "cancel_subscription", lambda sid: cancelled.append(sid) or True
    )
    refunded = _student_event("subscription_payment_refunded", cid, "plus")
    refunded["data"] = {
        "type": "subscription-invoices",
        "id": "invoice-refunded",
        "attributes": {
            "subscription_id": "sub_test_1",
            "total": 4990,
            "refunded_amount": 4990,
            "refunded": True,
        },
    }

    assert _post(client, refunded).status_code == 200
    assert ssub.get_tier(cid) == "free"
    state = ssub.get_subscription_state(cid)
    assert state["status"] == "refunded"
    assert state["ls_sub_id"] == "sub_test_1"
    assert cancelled == ["sub_test_1"]


def test_full_subscription_refund_retries_if_renewal_cannot_be_cancelled(
    client, make_user, monkeypatch
):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))
    monkeypatch.setattr(ls, "cancel_subscription", lambda _sid: False)
    refunded = _student_event("subscription_payment_refunded", cid, "plus")
    refunded["data"]["attributes"].update(
        subscription_id="sub_test_1",
        total=4990,
        refunded_amount=4990,
        refunded=True,
    )

    assert _post(client, refunded).status_code == 503
    assert ssub.get_tier(cid) == "plus"


def test_partial_subscription_refund_keeps_paid_access(client, make_user):
    cid = make_user()
    _post(client, _student_event("subscription_created", cid, "plus"))
    refunded = _student_event("subscription_payment_refunded", cid, "plus")
    refunded["data"]["attributes"].update(
        subscription_id="sub_test_1",
        total=4990,
        refunded_amount=1000,
        refunded=False,
    )

    assert _post(client, refunded).status_code == 200
    assert ssub.get_tier(cid) == "plus"


def test_payment_invoice_event_preserves_provider_subscription_id(
    client, make_user, flask_app, monkeypatch
):
    """Invoice webhooks must not replace the cancellable subscription id."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)

    created = _student_event("subscription_created", cid, "plus")
    created["data"]["id"] = "2350235"
    assert _post(client, created).status_code == 200

    paid_invoice = _student_event("subscription_payment_success", cid, "plus")
    paid_invoice["data"] = {
        "type": "subscription-invoices",
        "id": "7926265",
        "attributes": {
            "status": "paid",
            "subscription_id": 2350235,
        },
    }
    assert _post(client, paid_invoice).status_code == 200

    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "free"})

    assert response.status_code == 200
    assert calls == ["2350235"]


def test_subscription_checkout_forwards_test_mode(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(cfg, "LS_VARIANT_STUDENT_PLUS", "variant_plus")
    monkeypatch.setattr(cfg, "LEMON_SQUEEZY_TEST_MODE", True)
    captured = {}

    def fake_checkout(variant, **kwargs):
        captured.update(variant=variant, **kwargs)
        return "https://example.test/checkout"

    monkeypatch.setattr(ls, "create_checkout", fake_checkout)
    cid = make_user()
    _complete_setup(cid)
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 200
    assert response.get_json()["checkout_url"] == "https://example.test/checkout"
    assert captured["variant"] == "variant_plus"
    assert captured["test_mode"] is True


def test_same_paid_plan_does_not_create_a_duplicate_checkout(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    ssub.set_subscription_state(
        cid,
        tier="plus",
        status="active",
        ls_sub_id="existing-plus",
    )
    monkeypatch.setattr(
        ls,
        "create_checkout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate checkout")),
    )
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "tier": "plus",
        "unchanged": True,
    }


def test_subscription_change_requires_login(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 401
    assert response.get_json()["ok"] is False


def test_subscription_state_read_falls_back_safely_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        ssub,
        "get_db",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    assert ssub.get_subscription_state(999) == {}


def test_paid_subscription_state_survives_optional_benefit_database_error(
    make_user, monkeypatch
):
    cid = make_user()

    def fail_wallet_setup(db, _client_id):
        _exec(db, "SELECT missing_paid_benefit_column FROM clients LIMIT 1")

    monkeypatch.setattr(sdb, "_ensure_wallet", fail_wallet_setup)

    result = ssub.set_subscription_state(
        cid,
        tier="plus",
        status="active",
        ls_sub_id="provider-sub",
    )

    assert result["ok"] is True
    assert ssub.get_subscription_state(cid)["ls_sub_id"] == "provider-sub"
    assert ssub.get_tier(cid) == "plus"


def test_subscription_change_rejects_unknown_plan(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "enterprise"})

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_subscription_change_rejects_removed_ultimate_plan(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    monkeypatch.setattr(
        ls,
        "create_checkout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("removed plan must not create a checkout")
        ),
    )
    _login_student(client, cid)

    response = client.post(
        "/api/student/subscription/change", json={"tier": "ultimate"}
    )

    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "error": "Plan no disponible"}


def test_new_paid_checkout_requires_configured_variant(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(cfg, "LS_VARIANT_STUDENT_PLUS", "")
    cid = make_user()
    _complete_setup(cid)
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_paid_plan_without_provider_id_blocks_another_checkout(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    ssub.set_tier(cid, "plus")
    monkeypatch.setattr(
        ls,
        "create_checkout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate checkout")),
    )
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_delinquent_subscription_blocks_plan_change_checkout(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    ssub.set_subscription_state(cid, tier="plus", status="past_due", ls_sub_id="past-due-sub")
    monkeypatch.setattr(
        ls,
        "create_checkout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate checkout")),
    )
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_expired_subscription_can_start_a_fresh_checkout(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(cfg, "LS_VARIANT_STUDENT_PLUS", "plus-variant")
    cid = make_user()
    _complete_setup(cid)
    ssub.set_subscription_state(cid, tier="plus", status="expired", ls_sub_id="expired-sub")
    monkeypatch.setattr(ls, "create_checkout", lambda *_args, **_kwargs: "https://checkout.test/new")
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 200
    assert response.get_json()["checkout_url"] == "https://checkout.test/new"


def test_cancelled_same_plan_resumes_existing_subscription(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(cfg, "LS_VARIANT_STUDENT_PLUS", "plus-variant")
    cid = make_user()
    _complete_setup(cid)
    ssub.set_subscription_state(cid, tier="plus", status="cancelled", ls_sub_id="cancelled-sub")
    calls = []
    monkeypatch.setattr(
        ls,
        "update_subscription_variant",
        lambda sid, variant: calls.append((sid, variant)) or True,
    )
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 200
    assert response.get_json()["updated"] is True
    assert calls == [("cancelled-sub", "plus-variant")]


def test_unknown_paid_subscription_state_blocks_duplicate_checkout(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    cid = make_user()
    _complete_setup(cid)
    ssub.set_subscription_state(
        cid,
        tier="plus",
        status="provider_state_not_recognized",
        ls_sub_id="existing-plus",
    )
    monkeypatch.setattr(
        ls,
        "create_checkout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate checkout")),
    )
    _login_student(client, cid)

    response = client.post("/api/student/subscription/change", json={"tier": "plus"})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_coin_checkout_forwards_test_mode(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(cfg, "LS_VARIANT_COIN_SMALL", "variant_small")
    monkeypatch.setattr(cfg, "LEMON_SQUEEZY_TEST_MODE", True)
    captured = {}

    def fake_checkout(variant, **kwargs):
        captured.update(variant=variant, **kwargs)
        return "https://example.test/coin-checkout"

    monkeypatch.setattr(ls, "create_checkout", fake_checkout)
    cid = make_user()
    _complete_setup(cid)
    _login_student(client, cid)

    response = client.post("/api/student/wallet/buy-coin-pack", json={"pack_key": "small"})

    assert response.status_code == 200
    assert response.get_json()["checkout_url"] == "https://example.test/coin-checkout"
    assert captured["variant"] == "variant_small"
    assert captured["test_mode"] is True


def test_bad_signature_rejected(client, make_user):
    cid = make_user()
    r = _post(client, _student_event("subscription_created", cid, "plus"), sign=False)
    assert r.status_code == 401
    assert ssub.get_tier(cid) == "free"   # nothing provisioned on a forged call


def test_missing_client_id_is_rejected_for_provider_review(client):
    payload = {"meta": {"event_name": "subscription_created",
                        "custom_data": {"purpose": "student_sub", "tier": "plus"}},
               "data": {"id": "x", "attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 400


def test_removed_product_webhook_is_rejected_like_every_unknown_product(client, make_user):
    payload = {
        "meta": {
            "event_name": "subscription_created",
            "custom_data": {
                "purpose": "removed_product",
                "client_id": str(make_user()),
                "plan": "growth",
            },
        },
        "data": {"id": "removed-product-subscription", "attributes": {}},
    }

    response = _post(client, payload)

    assert response.status_code == 400
    assert response.get_data(as_text=True) == "Unsupported product"


def test_archived_subscription_retry_is_acknowledged_without_named_product_compatibility(
    client, make_user
):
    subscription_id = "archived-provider-subscription"
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO retired_billing_cancellations "
            "(subscription_id, status, attempts) VALUES (%s, 'cancelled', 1)",
            (subscription_id,),
        )
    payload = {
        "meta": {
            "event_name": "subscription_cancelled",
            "custom_data": {
                "purpose": "removed_product",
                "client_id": str(make_user()),
            },
        },
        "data": {"id": subscription_id, "attributes": {"status": "cancelled"}},
    }

    response = _post(client, payload)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "retired"


def test_archived_subscription_payment_retry_uses_invoice_subscription_id(client):
    subscription_id = "archived-payment-subscription"
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO retired_billing_cancellations "
            "(subscription_id, status, attempts) VALUES (%s, 'cancelled', 1)",
            (subscription_id,),
        )
    payload = {
        "meta": {
            "event_name": "subscription_payment_failed",
            "custom_data": {"purpose": "removed_product"},
        },
        "data": {
            "id": "invoice-id-is-not-the-subscription-id",
            "attributes": {"subscription_id": subscription_id},
        },
    }

    response = _post(client, payload)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "retired"


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
    raw = json.dumps(payload).encode()
    event_key = hashlib.sha256(raw).hexdigest()
    with get_db() as db:
        event = _fetchone(
            db,
            "SELECT status, attempts FROM webhook_events WHERE provider = %s AND event_key = %s",
            ("lemonsqueezy", event_key),
        )
    assert event == {"status": "succeeded", "attempts": 1}


def test_full_coin_pack_refund_reverses_credit_exactly_once(client, make_user):
    cid = make_user()
    purchased = {
        "meta": {
            "event_name": "order_created",
            "custom_data": {
                "purpose": "coin_pack",
                "client_id": str(cid),
                "pack_key": "small",
            },
        },
        "data": {"id": "order_refund_1", "attributes": {"total": 1990}},
    }
    assert _post(client, purchased).status_code == 200
    refunded = json.loads(json.dumps(purchased))
    refunded["meta"]["event_name"] = "order_refunded"
    refunded["data"]["attributes"].update(
        refunded=True, refunded_amount=1990
    )

    assert _post(client, refunded).status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 0
    assert sdb.get_wallet(cid)["coin_debt"] == 0
    assert _post(client, refunded).status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 0


def test_partial_coin_pack_refund_reverses_proportional_credit(client, make_user):
    cid = make_user()
    purchased = {
        "meta": {
            "event_name": "order_created",
            "custom_data": {
                "purpose": "coin_pack",
                "client_id": str(cid),
                "pack_key": "small",
            },
        },
        "data": {"id": "order_partial_refund", "attributes": {"total": 2000}},
    }
    assert _post(client, purchased).status_code == 200
    refunded = json.loads(json.dumps(purchased))
    refunded["meta"]["event_name"] = "order_refunded"
    refunded["data"]["attributes"].update(
        refunded=False, refunded_amount=1000
    )

    assert _post(client, refunded).status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 125
    assert sdb.get_wallet(cid)["coin_debt"] == 0
    assert _post(client, refunded).status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 125


def test_out_of_order_partial_refunds_never_reverse_twice(client, make_user):
    cid = make_user()
    purchased = {
        "meta": {
            "event_name": "order_created",
            "custom_data": {
                "purpose": "coin_pack",
                "client_id": str(cid),
                "pack_key": "small",
            },
        },
        "data": {"id": "order_out_of_order", "attributes": {"total": 2000}},
    }
    assert _post(client, purchased).status_code == 200

    def refund(amount, event_revision):
        payload = json.loads(json.dumps(purchased))
        payload["meta"]["event_name"] = "order_refunded"
        payload["data"]["attributes"].update(
            refunded=False,
            refunded_amount=amount,
            event_revision=event_revision,
        )
        return _post(client, payload)

    assert refund(1500, "newer").status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 63
    assert refund(1000, "older").status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 63
    assert refund(1500, "newest-replay").status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 63
    assert sdb.get_wallet(cid)["coin_debt"] == 0


def test_spent_coin_pack_refund_records_debt_repaid_by_future_earnings(
    client, make_user
):
    cid = make_user()
    purchased = {
        "meta": {
            "event_name": "order_created",
            "custom_data": {
                "purpose": "coin_pack",
                "client_id": str(cid),
                "pack_key": "small",
            },
        },
        "data": {"id": "order_refund_spent", "attributes": {"total": 1990}},
    }
    assert _post(client, purchased).status_code == 200
    sdb.add_coins(cid, -200, "spent before refund")
    refunded = json.loads(json.dumps(purchased))
    refunded["meta"]["event_name"] = "order_refunded"
    refunded["data"]["attributes"].update(
        refunded=True, refunded_amount=1990
    )

    assert _post(client, refunded).status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 0
    assert sdb.get_wallet(cid)["coin_debt"] == 200

    assert sdb.add_coins(cid, 150, "future reward") == 0
    assert sdb.get_wallet(cid)["coin_debt"] == 50
    assert sdb.add_coins(cid, 75, "future reward") == 25
    assert sdb.get_wallet(cid)["coin_debt"] == 0


def test_parallel_webhook_workers_claim_an_event_once():
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda _: claim_webhook_event(
                    "lemonsqueezy", "parallel-event", "order_created"
                ),
                range(2),
            )
        )

    assert sorted(claim["claimed"] for claim in claims) == [False, True]
    assert {claim["status"] for claim in claims} == {"processing"}


def test_coin_pack_without_order_id_is_acked_without_credit(client, make_user):
    cid = make_user()
    payload = {"meta": {"event_name": "order_created",
                        "custom_data": {"purpose": "coin_pack", "client_id": str(cid), "pack_key": "small"}},
               "data": {"attributes": {}}}
    r = _post(client, payload)
    assert r.status_code == 200
    assert sdb.get_wallet(cid)["coins"] == 0


def test_coin_pack_processing_failure_requests_provider_retry(
    client, make_user, monkeypatch
):
    cid = make_user()
    payload = {"meta": {"event_name": "order_created",
                        "custom_data": {"purpose": "coin_pack", "client_id": str(cid), "pack_key": "small"}},
               "data": {"id": "order_retry_me", "attributes": {}}}
    monkeypatch.setattr(sdb, "credit_coin_pack", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")))

    response = _post(client, payload)

    assert response.status_code == 503


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
