"""Reliable delivery for the auth emails, and stale-account cleanup.

Nothing here sends mail inside a web request. SMTP talks to another company's
server with a 30-second socket timeout, and the web service is one gunicorn
worker with a handful of threads: a slow mail server held a thread per attempt,
a few students pressing "resend" together held them all, and with every thread
waiting on SMTP not even /health could answer — which Render reads as a dead
instance and restarts. That was the site "not loading" during the Workspace
outage, worst for whoever was stuck unverified and retrying. Requests enqueue;
the worker, which claims these jobs every five seconds, does the sending.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Callable

from machreach_core.config import BASE_URL
from machreach_core.db import (
    _USE_PG,
    _exec,
    _fetchall,
    create_reset_token,
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
RESET_JOB_TYPE = "password_reset_email"
MAX_ATTEMPTS = 3


def _deliver_once(client_id: int, sender: Sender) -> bool:
    client = get_client(client_id)
    if not client or client.get("email_verified"):
        return True
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    create_verification_token(client_id, token, expires)
    verify_link = f"{BASE_URL}/verify-email/{token}"
    saludo = f"Hola {client.get('name')}" if client.get("name") else "Hola"
    body = (
        f"{saludo},\n\n"
        "Te damos la bienvenida a MachReach. Confirma tu correo aquí:\n\n"
        f"{verify_link}\n\nEste enlace vence en 24 horas.\n\n— MachReach"
    )
    try:
        return bool(sender(str(client.get("email") or ""), "MachReach — Confirma tu correo", body))
    except Exception:
        return False


def queue_verification_email(client_id: int) -> dict:
    """Queue delivery for the worker; never talks to SMTP in the request."""
    return enqueue_async_job(
        JOB_TYPE,
        str(client_id),
        input_payload={"client_id": int(client_id)},
        progress="Verification email queued.",
        max_attempts=MAX_ATTEMPTS,
    )


def _deliver_reset_once(client_id: int, sender: Sender) -> bool:
    """Mint a reset token and send its link. The token is generated here, at
    send time, so a retry mails a link that is valid for its full hour rather
    than one minted before the first failed attempt."""
    client = get_client(client_id)
    if not client:
        return True
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    create_reset_token(client_id, token, expires)
    reset_link = f"{BASE_URL}/reset-password/{token}"
    body = "\n\n".join([
        "Entra aquí para restablecer tu contraseña de MachReach:",
        reset_link,
        "Este enlace vence en 1 hora.",
        "Si no lo pediste, ignora este correo.",
    ])
    try:
        return bool(sender(str(client.get("email") or ""), "MachReach — Restablecer contraseña", body))
    except Exception:
        return False


def queue_password_reset(client_id: int) -> dict:
    """Queue a password-reset email for the worker."""
    return enqueue_async_job(
        RESET_JOB_TYPE,
        str(client_id),
        input_payload={"client_id": int(client_id)},
        progress="Password reset email queued.",
        max_attempts=MAX_ATTEMPTS,
    )


def process_reset_job(job: dict, sender: Sender) -> dict:
    client_id = int(job.get("job_key") or 0)
    if _deliver_reset_once(client_id, sender):
        set_async_job_status(RESET_JOB_TYPE, str(client_id), "done", "Password reset email sent.")
        return {"status": "done"}
    result = fail_async_job(
        RESET_JOB_TYPE,
        str(client_id),
        "SMTP password reset delivery failed",
        progress="Could not deliver the password reset email.",
        retry=True,
    )
    if result.get("status") == "error":
        record_operational_event("smtp_failure", "password_reset")
    return result


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
            # The delivery job goes with the account: a run that exhausted its
            # retries would otherwise alert on /health/operations forever for
            # an address that no longer exists.
            _exec(
                db,
                "DELETE FROM async_jobs WHERE job_type = %s AND job_key = %s",
                (JOB_TYPE, str(client_id)),
            )
            _exec(db, "DELETE FROM clients WHERE id = %s", (client_id,))
    return len(ids)
