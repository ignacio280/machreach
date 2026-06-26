"""Transactional (system) email sending.

Extracted from app.py so non-web modules (e.g. the JR invite flow in
outreach/jr_api.py) can send mail without importing the Flask app — which would
be a circular import. app.py's `_send_system_email` now delegates here, so
there is a single implementation and one set of SMTP config.
"""
from __future__ import annotations


def send_system_email(to: str, subject: str, body: str) -> bool:
    """Send a transactional email (verification, reset, invite) from the system
    account. Returns True on success."""
    from outreach.config import SMTP_HOST, SMTP_PORT
    from outreach.config import SYSTEM_FROM_EMAIL, SYSTEM_FROM_NAME, SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    print(f"[SYSTEM EMAIL] Attempting to send to {to} via {SMTP_HOST}:{SMTP_PORT} as {SYSTEM_SMTP_USER}", flush=True)
    if not SYSTEM_SMTP_USER or not SYSTEM_SMTP_PASSWORD:
        print(f"[SYSTEM EMAIL] SMTP credentials not set — SYSTEM_SMTP_USER={'set' if SYSTEM_SMTP_USER else 'EMPTY'}, SYSTEM_SMTP_PASSWORD={'set' if SYSTEM_SMTP_PASSWORD else 'EMPTY'}", flush=True)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SYSTEM_FROM_NAME} <{SYSTEM_FROM_EMAIL}>" if SYSTEM_FROM_EMAIL else SYSTEM_SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))
    try:
        if SMTP_PORT == 587:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                srv.starttls()
                srv.login(SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                srv.login(SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD)
                srv.send_message(msg)
        print(f"[SYSTEM EMAIL] Successfully sent to {to}", flush=True)
        return True
    except Exception as e:
        import traceback
        print(f"[SYSTEM EMAIL] Send FAILED ({to}): {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return False
