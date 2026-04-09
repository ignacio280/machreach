"""
Email sender — SMTP delivery with open-tracking pixel injection.
"""
from __future__ import annotations

import random
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from outreach.config import BASE_URL, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER


def _wrap_html(body_text: str, contact_id: int | None = None,
               tracking_id: int | None = None) -> str:
    """Wrap plain-text email body in a polished HTML template."""
    body_html = body_text.replace("\n", "<br>")

    tracking_pixel = ""
    if tracking_id:
        tracking_pixel = (
            f'<img src="{BASE_URL}/track/open/{tracking_id}" '
            f'width="1" height="1" style="display:none;" alt="" />'
        )

    unsub_link = ""
    if contact_id:
        unsub_link = f'{BASE_URL}/unsubscribe/{contact_id}'

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
</head>
<body style="margin:0;padding:0;background:#F5F5F5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F5F5F5;">
    <tr><td style="padding:32px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#FFFFFF;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
        <tr><td style="padding:32px 36px;color:#1E293B;font-size:15px;line-height:1.7;">
          {body_html}
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;">
        <tr><td style="padding:20px 36px;text-align:center;">
          {f'<a href="{unsub_link}" style="color:#A0AEC0;font-size:11px;text-decoration:underline;">Unsubscribe</a>' if unsub_link else ''}
        </td></tr>
      </table>
      {tracking_pixel}
    </td></tr>
  </table>
</body>
</html>"""


def send_email(
    to_email: str,
    subject: str,
    body_text: str,
    contact_id: int | None = None,
    tracking_id: int | None = None,
) -> bool:
    """Send a single email via SMTP. Returns True on success."""
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[DRY RUN] Would send to {to_email}: {subject}")
        return True

    html = _wrap_html(body_text, contact_id=contact_id, tracking_id=tracking_id)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(html, "html"))

    # CAN-SPAM / RFC 8058 unsubscribe headers (required by Gmail/Yahoo since 2024)
    if contact_id and BASE_URL:
        unsub_url = f"{BASE_URL}/unsubscribe/{contact_id}"
        msg["List-Unsubscribe"] = f"<{unsub_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    try:
        if SMTP_PORT == 587:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email send failed ({to_email}): {e}")
        return False


def pick_variant() -> str:
    """Randomly pick A or B variant for A/B testing (50/50)."""
    return random.choice(["a", "b"])
