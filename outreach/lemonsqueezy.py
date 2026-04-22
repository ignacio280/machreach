"""Lemon Squeezy integration: hosted checkout + webhook verification.

Why a single module?
- All three paid surfaces (outreach SaaS subs, student PLUS sub, one-time
  coin packs) use the same checkout-creation API and the same webhook.
- Routing of webhook events back to the correct user / purpose is done
  via the `custom_data` we attach to every checkout.

Env vars required (see outreach/config.py):
- LEMON_SQUEEZY_API_KEY
- LEMON_SQUEEZY_STORE_ID
- LEMON_SQUEEZY_WEBHOOK_SECRET
- LS_VARIANT_*  (one per priced product)

Public API:
- create_checkout(variant_id, *, custom_data, email=None, redirect_url=None,
                   receipt_link_url=None) -> str  # the hosted checkout URL
- verify_webhook(raw_body: bytes, signature_header: str) -> bool
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
    LEMON_SQUEEZY_WEBHOOK_SECRET,
)

log = logging.getLogger(__name__)

LS_API = "https://api.lemonsqueezy.com/v1"
_HEADERS_JSON = {
    "Accept":       "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
}


def _auth_headers() -> dict:
    return {**_HEADERS_JSON, "Authorization": f"Bearer {LEMON_SQUEEZY_API_KEY}"}


def is_configured() -> bool:
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
    if not is_configured():
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
                "store":   {"data": {"type": "stores",   "id": str(LEMON_SQUEEZY_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }

    resp = requests.post(
        f"{LS_API}/checkouts",
        headers=_auth_headers(),
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


def verify_webhook(raw_body: bytes, signature_header: str) -> bool:
    """Verify the X-Signature header against our webhook secret (HMAC-SHA256)."""
    if not LEMON_SQUEEZY_WEBHOOK_SECRET:
        log.warning("[LS] webhook received but LEMON_SQUEEZY_WEBHOOK_SECRET is not set")
        return False
    if not signature_header:
        return False
    expected = hmac.new(
        LEMON_SQUEEZY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    # `signature_header` is the raw hex digest.
    try:
        return hmac.compare_digest(expected, signature_header)
    except Exception:
        return False


def cancel_subscription(subscription_id: str) -> bool:
    """Cancel a subscription via the Lemon Squeezy REST API.
    Returns True on success, False otherwise (errors are logged, not raised)."""
    if not subscription_id or not is_configured():
        return False
    try:
        resp = requests.delete(
            f"{LS_API}/subscriptions/{subscription_id}",
            headers=_auth_headers(),
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
