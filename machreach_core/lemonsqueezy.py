"""Lemon Squeezy integration: hosted checkout + webhook verification.

Why a single module?
- Student PLUS subscriptions and one-time coin packs use the same
  checkout-creation API and webhook.
- Routing of webhook events back to the correct user / purpose is done
  via the `custom_data` we attach to every checkout.

Env vars required (see machreach_core/config.py):
- LEMON_SQUEEZY_API_KEY
- LEMON_SQUEEZY_STORE_ID
- LEMON_SQUEEZY_WEBHOOK_SECRET
- LEMON_SQUEEZY_TEST_WEBHOOK_SECRET (optional, for a restricted sandbox account)
- LS_VARIANT_*  (one per priced product)

Public API:
- create_checkout(variant_id, *, custom_data, email=None, redirect_url=None,
                   receipt_link_url=None) -> str  # the hosted checkout URL
- verify_webhook(raw_body: bytes, signature_header: str) -> bool
- update_subscription_variant(subscription_id: str, variant_id: str) -> bool
- cancel_subscription(subscription_id: str) -> bool
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

import requests

from .config import (
    LEMON_SQUEEZY_API_KEY,
    LEMON_SQUEEZY_STORE_ID,
    LEMON_SQUEEZY_TEST_API_KEY,
    LEMON_SQUEEZY_TEST_STORE_ID,
    LEMON_SQUEEZY_TEST_WEBHOOK_SECRET,
    LEMON_SQUEEZY_WEBHOOK_SECRET,
)

log = logging.getLogger(__name__)

LS_API = "https://api.lemonsqueezy.com/v1"
_HEADERS_JSON = {
    "Accept":       "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
}


def _auth_headers(*, test_mode: bool = False) -> dict:
    api_key = LEMON_SQUEEZY_TEST_API_KEY if test_mode else LEMON_SQUEEZY_API_KEY
    return {**_HEADERS_JSON, "Authorization": f"Bearer {api_key}"}


def is_configured(*, test_mode: bool = False) -> bool:
    if test_mode:
        return bool(LEMON_SQUEEZY_TEST_API_KEY and LEMON_SQUEEZY_TEST_STORE_ID)
    return bool(LEMON_SQUEEZY_API_KEY and LEMON_SQUEEZY_STORE_ID)


def create_checkout(
    variant_id: str,
    *,
    custom_data: dict,
    email: Optional[str] = None,
    name: Optional[str] = None,
    redirect_url: Optional[str] = None,
    receipt_link_url: Optional[str] = None,
    test_mode: bool = False,
) -> str:
    """Create a hosted checkout and return its URL.

    `custom_data` is echoed back on every webhook event for this checkout —
    we always include at least {client_id, purpose, ...}.
    """
    if not is_configured(test_mode=test_mode):
        raise RuntimeError("Lemon Squeezy is not configured (missing API key or store id).")
    if not variant_id:
        raise RuntimeError("Lemon Squeezy: missing variant id for this product.")

    checkout_data: dict = {
        "custom": custom_data or {},
    }
    if email or name:
        checkout_data["email"] = email or ""
        if name:
            checkout_data["name"] = name

    product_options: dict = {}
    if redirect_url:
        product_options["redirect_url"] = redirect_url
    if receipt_link_url:
        product_options["receipt_link_url"] = receipt_link_url

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "test_mode": bool(test_mode),
                "checkout_data": checkout_data,
                **({"product_options": product_options} if product_options else {}),
            },
            "relationships": {
                "store":   {"data": {"type": "stores",   "id": str(LEMON_SQUEEZY_TEST_STORE_ID if test_mode else LEMON_SQUEEZY_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }

    resp = requests.post(
        f"{LS_API}/checkouts",
        headers=_auth_headers(test_mode=test_mode),
        data=json.dumps(payload),
        timeout=15,
    )
    if resp.status_code >= 300:
        log.error("[LS] create_checkout failed %s: %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Lemon Squeezy checkout failed: {resp.status_code}")
    body = resp.json()
    url = (((body.get("data") or {}).get("attributes") or {}).get("url"))
    if not url:
        raise RuntimeError("Lemon Squeezy checkout: no URL in response.")
    return url


def webhook_signature_mode(raw_body: bytes, signature_header: str) -> str | None:
    """Return ``live`` or ``test`` for an authenticated provider signature."""
    secrets = (
        ("live", LEMON_SQUEEZY_WEBHOOK_SECRET),
        ("test", LEMON_SQUEEZY_TEST_WEBHOOK_SECRET),
    )
    if not any(secret for _mode, secret in secrets):
        log.warning("[LS] webhook received but LEMON_SQUEEZY_WEBHOOK_SECRET is not set")
        return None
    if not signature_header:
        return None
    for mode, secret in secrets:
        if not secret:
            continue
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        try:
            if hmac.compare_digest(expected, signature_header):
                return mode
        except Exception:
            return None
    return None


def verify_webhook(raw_body: bytes, signature_header: str) -> bool:
    """Verify the X-Signature header against an allowed webhook secret."""
    return webhook_signature_mode(raw_body, signature_header) is not None


def cancel_subscription(subscription_id: str, *, test_mode: bool = False) -> bool:
    """Cancel a subscription via the Lemon Squeezy REST API.
    Returns True on success, False otherwise (errors are logged, not raised)."""
    if not subscription_id or not is_configured(test_mode=test_mode):
        return False
    try:
        resp = requests.delete(
            f"{LS_API}/subscriptions/{subscription_id}",
            headers=_auth_headers(test_mode=test_mode),
            timeout=15,
        )
        if resp.status_code >= 300:
            log.error("[LS] cancel_subscription %s failed: %s %s",
                      subscription_id, resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        log.exception("[LS] cancel_subscription exception: %s", e)
        return False


def update_subscription_variant(
    subscription_id: str, variant_id: str, *, test_mode: bool = False
) -> bool:
    """Move an existing subscription to another configured variant.

    ``cancelled=False`` also resumes a subscription that was scheduled to end,
    avoiding a second checkout and duplicate subscription.
    """
    if not subscription_id or not variant_id or not is_configured(test_mode=test_mode):
        return False
    normalized_variant_id: int | str = (
        int(str(variant_id)) if str(variant_id).isdigit() else str(variant_id)
    )
    payload = {
        "data": {
            "type": "subscriptions",
            "id": str(subscription_id),
            "attributes": {
                "variant_id": normalized_variant_id,
                "cancelled": False,
            },
        }
    }
    try:
        resp = requests.patch(
            f"{LS_API}/subscriptions/{subscription_id}",
            headers=_auth_headers(test_mode=test_mode),
            json=payload,
            timeout=20,
        )
        if resp.status_code >= 300:
            log.error(
                "[LS] update_subscription_variant %s failed: %s %s",
                subscription_id,
                resp.status_code,
                resp.text[:200],
            )
            return False
        attributes = (((resp.json() or {}).get("data") or {}).get("attributes") or {})
        returned_variant_id = attributes.get("variant_id")
        if str(returned_variant_id) != str(normalized_variant_id):
            log.warning(
                "[LS] update_subscription_variant %s returned variant %s instead of %s",
                subscription_id,
                returned_variant_id,
                normalized_variant_id,
            )
            return False
        return True
    except Exception as e:
        log.exception("[LS] update_subscription_variant exception: %s", e)
        return False
