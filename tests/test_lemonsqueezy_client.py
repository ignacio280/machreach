"""Direct tests for the external Lemon Squeezy HTTP client."""

import hashlib
import hmac
import json

from machreach_core import lemonsqueezy


class _Response:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def test_create_checkout_sends_customer_product_and_test_mode(monkeypatch):
    request = {}
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_TEST_API_KEY", "ls-test-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_TEST_STORE_ID", "test-store-1")

    def fake_post(url, **kwargs):
        request.update(url=url, **kwargs)
        return _Response(body={"data": {"attributes": {"url": "https://checkout.test/1"}}})

    monkeypatch.setattr(lemonsqueezy.requests, "post", fake_post)
    url = lemonsqueezy.create_checkout(
        "variant-2",
        custom_data={"client_id": 7, "purpose": "student_plus"},
        email="buyer@example.test",
        name="Buyer",
        redirect_url="https://machreach.com/student/shop",
        receipt_link_url="https://machreach.com/billing",
        test_mode=True,
    )
    payload = json.loads(request["data"])

    assert url == "https://checkout.test/1"
    assert payload["data"]["attributes"]["test_mode"] is True
    assert request["headers"]["Authorization"] == "Bearer ls-test-key"
    assert payload["data"]["relationships"]["store"]["data"]["id"] == "test-store-1"
    assert payload["data"]["relationships"]["variant"]["data"]["id"] == "variant-2"
    assert payload["data"]["attributes"]["checkout_data"]["custom"]["client_id"] == 7


def test_test_mode_requires_separate_sandbox_credentials(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-live-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "live-store")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_TEST_API_KEY", "")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_TEST_STORE_ID", "")

    try:
        lemonsqueezy.create_checkout("test-variant", custom_data={}, test_mode=True)
        raise AssertionError("missing sandbox credentials were not rejected")
    except RuntimeError as exc:
        assert "not configured" in str(exc)


def test_create_checkout_rejects_provider_error_and_missing_url(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")
    monkeypatch.setattr(
        lemonsqueezy.requests,
        "post",
        lambda *_args, **_kwargs: _Response(status_code=422, text="invalid"),
    )

    try:
        lemonsqueezy.create_checkout("variant", custom_data={})
        raise AssertionError("provider error was not raised")
    except RuntimeError as exc:
        assert "422" in str(exc)

    monkeypatch.setattr(
        lemonsqueezy.requests,
        "post",
        lambda *_args, **_kwargs: _Response(body={"data": {"attributes": {}}}),
    )
    try:
        lemonsqueezy.create_checkout("variant", custom_data={})
        raise AssertionError("missing URL was not raised")
    except RuntimeError as exc:
        assert "no URL" in str(exc)


def test_list_subscriptions_scopes_lookup_to_store_and_email(monkeypatch):
    request = {}
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")

    def fake_get(url, **kwargs):
        request.update(url=url, **kwargs)
        return _Response(body={"data": [{"id": "sub-1", "attributes": {}}]})

    monkeypatch.setattr(lemonsqueezy.requests, "get", fake_get)

    rows = lemonsqueezy.list_subscriptions_by_email(" Buyer@Example.Test ")

    assert [row["id"] for row in rows] == ["sub-1"]
    assert request["params"]["filter[store_id]"] == "store-1"
    assert request["params"]["filter[user_email]"] == "buyer@example.test"


def test_verify_webhook_and_cancel_subscription(monkeypatch):
    secret = "webhook-secret"
    raw = b'{"meta":{"event_name":"order_created"}}'
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")

    assert lemonsqueezy.verify_webhook(raw, signature) is True
    assert lemonsqueezy.verify_webhook(raw, "bad-signature") is False

    monkeypatch.setattr(
        lemonsqueezy.requests,
        "delete",
        lambda *_args, **_kwargs: _Response(status_code=204),
    )
    assert lemonsqueezy.cancel_subscription("sub-1") is True

    monkeypatch.setattr(
        lemonsqueezy.requests,
        "delete",
        lambda *_args, **_kwargs: _Response(status_code=500, text="failed"),
    )
    assert lemonsqueezy.cancel_subscription("sub-2") is False


def test_verify_webhook_identifies_separate_sandbox_secret(monkeypatch):
    raw = b'{"meta":{"test_mode":true}}'
    secret = "sandbox-webhook-secret"
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_WEBHOOK_SECRET", "live-secret")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_TEST_WEBHOOK_SECRET", secret)

    assert lemonsqueezy.webhook_signature_mode(raw, signature) == "test"
    assert lemonsqueezy.verify_webhook(raw, signature) is True


def test_verify_webhook_rejects_missing_configuration_header_and_compare_error(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_WEBHOOK_SECRET", "")
    assert lemonsqueezy.verify_webhook(b"{}", "signature") is False

    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_WEBHOOK_SECRET", "secret")
    assert lemonsqueezy.verify_webhook(b"{}", "") is False

    monkeypatch.setattr(
        lemonsqueezy.hmac,
        "compare_digest",
        lambda *_args: (_ for _ in ()).throw(TypeError("invalid digest")),
    )
    assert lemonsqueezy.verify_webhook(b"{}", "signature") is False


def test_update_subscription_variant_resumes_without_creating_checkout(monkeypatch):
    request = {}
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")

    def fake_patch(url, **kwargs):
        request.update(url=url, **kwargs)
        return _Response(
            status_code=200,
            body={"data": {"attributes": {"variant_id": 1563574}}},
        )

    monkeypatch.setattr(lemonsqueezy.requests, "patch", fake_patch)

    assert lemonsqueezy.update_subscription_variant("sub-1", "1563574") is True
    payload = request["json"]
    assert request["url"].endswith("/subscriptions/sub-1")
    assert payload["data"]["attributes"] == {
        "variant_id": 1563574,
        "cancelled": False,
    }


def test_update_subscription_variant_rejects_provider_failure(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")
    monkeypatch.setattr(
        lemonsqueezy.requests,
        "patch",
        lambda *_args, **_kwargs: _Response(status_code=422, text="invalid"),
    )

    assert lemonsqueezy.update_subscription_variant("sub-1", "variant") is False


def test_update_subscription_variant_rejects_unchanged_paypal_response(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")
    monkeypatch.setattr(
        lemonsqueezy.requests,
        "patch",
        lambda *_args, **_kwargs: _Response(
            status_code=200,
            body={
                "data": {
                    "attributes": {
                        "variant_id": 1563572,
                        "urls": {"customer_portal_update_subscription": "https://portal.test"},
                    }
                }
            },
        ),
    )

    assert lemonsqueezy.update_subscription_variant("sub-1", "1563574") is False


def test_update_subscription_variant_rejects_missing_input_and_network_failure(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store-1")

    assert lemonsqueezy.update_subscription_variant("", "1563574") is False
    assert lemonsqueezy.update_subscription_variant("sub-1", "") is False

    monkeypatch.setattr(
        lemonsqueezy.requests,
        "patch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    assert lemonsqueezy.update_subscription_variant("sub-1", "1563574") is False


def test_client_rejects_missing_configuration_and_handles_network_failure(monkeypatch):
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "")
    assert lemonsqueezy.is_configured() is False
    assert lemonsqueezy.cancel_subscription("sub") is False
    try:
        lemonsqueezy.create_checkout("variant", custom_data={})
        raise AssertionError("missing config was not rejected")
    except RuntimeError as exc:
        assert "not configured" in str(exc)

    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_API_KEY", "ls-key")
    monkeypatch.setattr(lemonsqueezy, "LEMON_SQUEEZY_STORE_ID", "store")
    try:
        lemonsqueezy.create_checkout("", custom_data={})
        raise AssertionError("missing variant was not rejected")
    except RuntimeError as exc:
        assert "missing variant" in str(exc)

    monkeypatch.setattr(
        lemonsqueezy.requests,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network")),
    )
    assert lemonsqueezy.cancel_subscription("sub-network") is False
