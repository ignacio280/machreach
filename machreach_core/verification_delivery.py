"""Reliable email-verification delivery and stale-account cleanup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Callable

from machreach_core.config import BASE_URL
from machreach_core.db import (
    _USE_PG,
    _exec,
    _fetchall,
    create_verification_token,
    enqueue_async_job,
    fail_async_job,
    get_client,
    get_db,
    record_operational_event,
    set_async_job_status,
)


Sender = Callable[[str, str, str], bool]
JOB_TYPE = "verification_email"
MAX_ATTEMPTS = 3


def _deliver_once(client_id: int, sender: Sender) -> bool:
    client = get_client(client_id)
    if not client or client.get("email_verified"):
        return True
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    create_verification_token(client_id, token, expires)
    verify_link = f"{BASE_URL}/verify-email/{token}"
    body = (
        f"Hi {client.get('name') or 'there'},\n\n"
        "Welcome to MachReach! Please verify your email address:\n\n"
        f"{verify_link}\n\nThis link expires in 24 hours.\n\n— MachReach"
    )
    try:
        return bool(sender(str(client.get("email") or ""), "MachReach — Verify Your Email", body))
    except Exception:
        return False


def send_or_queue(client_id: int, sender: Sender) -> dict:
    """Attempt delivery immediately and queue at most three retries on failure."""
    if _deliver_once(client_id, sender):
        set_async_job_status(JOB_TYPE, str(client_id), "done", "Verification email sent.")
        return {"sent": True, "queued": False}
    enqueue_async_job(
        JOB_TYPE,
        str(client_id),
        input_payload={"client_id": int(client_id)},
        progress="Verification email delayed; retry queued.",
        max_attempts=MAX_ATTEMPTS,
    )
    return {"sent": False, "queued": True}


def process_job(job: dict, sender: Sender) -> dict:
    client_id = int(job.get("job_key") or 0)
    client = get_client(client_id)
    if not client or client.get("email_verified"):
        set_async_job_status(JOB_TYPE, str(client_id), "done", "Verification no longer required.")
        return {"status": "done"}
    if _deliver_once(client_id, sender):
        set_async_job_status(JOB_TYPE, str(client_id), "done", "Verification email sent.")
        return {"status": "done"}
    result = fail_async_job(
        JOB_TYPE,
        str(client_id),
        "SMTP verification delivery failed",
        progress="Could not deliver the verification email.",
        retry=True,
    )
    if result.get("status") == "error":
        record_operational_event("smtp_failure", "verification")
    return result


def delete_stale_unverified(days: int = 7) -> int:
    """Delete unused, unverified accounts older than the retention window."""
    days = max(1, int(days or 7))
    if _USE_PG:
        age_clause = f"created_at < NOW() - INTERVAL '{days} days'"
    else:
        age_clause = f"created_at < datetime('now', '-{days} days')"
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT id FROM clients WHERE COALESCE(email_verified, 0) = 0 "
            "AND COALESCE(is_admin, 0) = 0 AND " + age_clause,
        )
        ids = [int(row["id"]) for row in rows]
        for client_id in ids:
            _exec(db, "DELETE FROM email_verification_tokens WHERE client_id = %s", (client_id,))
            _exec(db, "DELETE FROM password_reset_tokens WHERE client_id = %s", (client_id,))
            _exec(db, "DELETE FROM clients WHERE id = %s", (client_id,))
    return len(ids)
