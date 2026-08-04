"""Administrator MFA storage and RFC 6238 TOTP verification.

The public interface deliberately stays small: callers can enroll a secret,
check whether MFA exists, and verify a submitted code. Secrets are encrypted
with the deployment encryption key before they are stored in the existing
client preferences document.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import struct
from urllib.parse import quote

from machreach_core.db import (
    decrypt_password,
    encrypt_password,
    get_mail_preferences,
    update_mail_preferences,
)


MFA_PERIOD_SECONDS = 30
MFA_DIGITS = 6


def generate_secret() -> str:
    """Return a 160-bit base32 TOTP secret without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, email: str, issuer: str = "MachReach") -> str:
    label = quote(f"{issuer}:{email}")
    return (
        f"otpauth://totp/{label}?secret={quote(secret)}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={MFA_DIGITS}"
        f"&period={MFA_PERIOD_SECONDS}"
    )


def _totp(secret: str, timestamp: int) -> str:
    normalized = str(secret or "").strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding, casefold=True)
    counter = int(timestamp) // MFA_PERIOD_SECONDS
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** MFA_DIGITS)).zfill(MFA_DIGITS)


def current_code(secret: str, *, timestamp: int | None = None) -> str:
    """Return the current code. Exposed for deterministic verification tests."""
    now = int(datetime.now(timezone.utc).timestamp()) if timestamp is None else int(timestamp)
    return _totp(secret, now)


def verify_code(secret: str, code: str, *, timestamp: int | None = None) -> bool:
    submitted = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(submitted) != MFA_DIGITS:
        return False
    now = int(datetime.now(timezone.utc).timestamp()) if timestamp is None else int(timestamp)
    return any(
        hmac.compare_digest(submitted, _totp(secret, now + offset * MFA_PERIOD_SECONDS))
        for offset in (-1, 0, 1)
    )


def _preferences(client_id: int) -> dict:
    raw = get_mail_preferences(client_id) or ""
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def enroll(client_id: int, secret: str) -> None:
    prefs = _preferences(client_id)
    prefs["admin_mfa"] = {
        "secret": encrypt_password(secret),
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }
    update_mail_preferences(client_id, json.dumps(prefs, separators=(",", ":")))


def disable(client_id: int) -> bool:
    """Forget this account's authenticator so it can enrol again.

    Used when a setup key has to be rotated — a leaked key is only worth
    rotating if there is a supported way to replace it. Returns whether there
    was an enrolment to remove.
    """
    if not _preferences(client_id).get("admin_mfa"):
        return False
    # update_mail_preferences merges rather than replaces, so dropping the key
    # locally and writing the rest back would not remove anything. Writing an
    # explicit null does: SQLite's json_patch deletes it outright, and on
    # Postgres the key survives as null, which reads the same everywhere here.
    update_mail_preferences(client_id, json.dumps({"admin_mfa": None}, separators=(",", ":")))
    return True


def secret_for(client_id: int) -> str:
    record = _preferences(client_id).get("admin_mfa") or {}
    if not isinstance(record, dict) or not record.get("secret"):
        return ""
    return decrypt_password(str(record["secret"]))


def is_enabled(client_id: int) -> bool:
    try:
        return bool(secret_for(client_id))
    except Exception:
        return False


def verify_client_code(client_id: int, code: str) -> bool:
    try:
        secret = secret_for(client_id)
    except Exception:
        return False
    return bool(secret and verify_code(secret, code))
