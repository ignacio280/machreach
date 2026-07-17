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
    assert payload["data"]["relationships"]["variant"]["data"]["id"] == "variant-2"
    assert payload["data"]["attributes"]["checkout_data"]["custom"]["client_id"] == 7


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
