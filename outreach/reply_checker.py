"""
IMAP reply checker — scans inbox for replies from outreach contacts.

Connects to the sending account's inbox, looks for recent emails from
addresses we've contacted, and marks them as replied in the database.
Also fetches the reply body and classifies sentiment (positive/negative/neutral).
"""
from __future__ import annotations

import email
import imaplib
from email.header import decode_header
from email.utils import parseaddr

from outreach.config import IMAP_HOST, IMAP_PASSWORD, IMAP_PORT, IMAP_USER
from outreach.db import get_all_sent_recipient_emails, record_reply


def _extract_body(msg) -> str:
    """Extract plain-text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except Exception:
                        return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except Exception:
                return payload.decode("utf-8", errors="replace")
    return ""


def _classify_sentiment(body: str) -> str:
    """Classify reply sentiment using AI. Returns 'positive', 'negative', or 'neutral'."""
    if not body or len(body.strip()) < 5:
        return "neutral"
    try:
        from outreach.ai import client as openai_client
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You classify cold email replies. Respond with EXACTLY one word: "
                    "positive, negative, or neutral.\n"
                    "positive = interested, wants to learn more, agrees to a meeting/call, asks questions about the offer\n"
                    "negative = not interested, asks to stop emailing, unsubscribe, rude rejection, wrong person\n"
                    "neutral = out of office, ambiguous, asks for more info without clear interest, automated reply"
                )},
                {"role": "user", "content": f"Classify this reply:\n\n{body[:1000]}"},
            ],
            temperature=0,
            max_tokens=10,
        )
        result = response.choices[0].message.content.strip().lower()
        if result in ("positive", "negative", "neutral"):
            return result
        # Fuzzy match
        if "positive" in result:
            return "positive"
        if "negative" in result:
            return "negative"
        return "neutral"
    except Exception as e:
        print(f"[SENTIMENT] Classification failed: {e}")
        return "neutral"


def _fetch_body(mail, msg_id) -> str:
    """Fetch the full body of a message by ID."""
    try:
        _, data = mail.fetch(msg_id, "(BODY.PEEK[])")
        if data and data[0]:
            full_msg = email.message_from_bytes(data[0][1])
            return _extract_body(full_msg)
    except Exception:
        pass
    return ""


def check_replies() -> int:
    """Check inbox for replies from outreach contacts.
    Returns the number of new replies detected."""
    if not IMAP_USER or not IMAP_PASSWORD:
        print("[REPLY CHECK] IMAP not configured — skipping.")
        return 0

    known_emails = get_all_sent_recipient_emails()
    if not known_emails:
        return 0

    replies_found = 0
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)

        # Check INBOX for replies from contacts (standard case)
        replies_found += _scan_folder(mail, "INBOX", known_emails, mark_seen=True)

        # Also check Sent Mail — catches replies when testing with same Gmail account
        try:
            status, _ = mail.select('"[Gmail]/Sent Mail"', readonly=True)
            if status == "OK":
                replies_found += _scan_folder(mail, None, known_emails, mark_seen=False, days=2)
        except Exception:
            pass  # Not all providers have this folder

        mail.logout()
    except Exception as e:
        print(f"[REPLY CHECK] IMAP error: {e}")

    return replies_found


def _scan_folder(mail, folder: str | None, known_emails: set[str],
                 mark_seen: bool = False, days: int = 7) -> int:
    """Scan a mailbox folder for replies from known contacts."""
    if folder:
        mail.select(folder)

    # Search recent emails (both seen and unseen to catch self-replies during testing)
    _, msg_ids = mail.search(None, "(SINCE " + _imap_date(days) + ")")
    if not msg_ids[0]:
        return 0

    all_ids = msg_ids[0].split()
    if not all_ids:
        return 0

    replies = 0

    # --- Batch fetch headers for all messages in one IMAP call ---
    id_range = b",".join(all_ids)
    try:
        _, hdr_data = mail.fetch(id_range, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT IN-REPLY-TO)])")
    except Exception:
        return 0

    # Parse headers and identify which messages need full body fetch
    need_body = []  # list of (msg_id_bytes, from_addr, is_self_reply, target_addr)
    hdr_idx = 0
    for item in hdr_data:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        if hdr_idx >= len(all_ids):
            break
        msg_id_bytes = all_ids[hdr_idx]
        hdr_idx += 1

        try:
            raw_header = item[1]
            msg = email.message_from_bytes(raw_header)
            from_header = msg.get("From", "")
            _, from_addr = parseaddr(from_header)
            from_addr = from_addr.lower().strip()

            # Direct reply from a known contact
            if from_addr in known_emails:
                need_body.append((msg_id_bytes, from_addr, False, from_addr))
                continue

            # Check if this is a reply TO a known contact (self-reply during testing)
            subject = msg.get("Subject", "")
            in_reply_to = msg.get("In-Reply-To", "")
            if (in_reply_to or subject.lower().startswith("re:")) and from_addr == IMAP_USER.lower():
                need_body.append((msg_id_bytes, from_addr, True, None))
        except Exception:
            continue

    if not need_body:
        return 0

    # --- Batch fetch bodies only for messages that matched ---
    body_ids = [item[0] for item in need_body]
    body_range = b",".join(body_ids)
    try:
        _, body_data = mail.fetch(body_range, "(BODY.PEEK[])")
    except Exception:
        return 0

    # Also batch fetch TO/CC headers for self-reply messages that need them
    self_reply_indices = [i for i, item in enumerate(need_body) if item[2]]
    to_headers_map = {}
    if self_reply_indices:
        self_ids = [need_body[i][0] for i in self_reply_indices]
        self_range = b",".join(self_ids)
        try:
            _, to_data = mail.fetch(self_range, "(BODY.PEEK[HEADER.FIELDS (TO CC)])")
            to_idx = 0
            for t_item in to_data:
                if isinstance(t_item, tuple) and len(t_item) == 2 and to_idx < len(self_ids):
                    to_headers_map[self_ids[to_idx]] = t_item[1]
                    to_idx += 1
        except Exception:
            pass

    # Process bodies
    body_map = {}
    for b_item in body_data:
        if isinstance(b_item, tuple) and len(b_item) == 2:
            try:
                full_msg = email.message_from_bytes(b_item[1])
                body_text = _extract_body(full_msg)
                # Use sequence number from the fetch response to map back
                body_map[len(body_map)] = body_text
            except Exception:
                body_map[len(body_map)] = ""

    for i, (msg_id_bytes, from_addr, is_self_reply, target_addr) in enumerate(need_body):
        reply_body = body_map.get(i, "")
        if is_self_reply:
            # Check TO/CC to find which known contact this was sent to
            to_raw = to_headers_map.get(msg_id_bytes)
            if to_raw:
                to_msg = email.message_from_bytes(to_raw)
                for hdr in ("To", "Cc"):
                    to_val = to_msg.get(hdr, "")
                    for addr_pair in to_val.split(","):
                        _, to_addr = parseaddr(addr_pair.strip())
                        to_addr = to_addr.lower().strip()
                        if to_addr in known_emails:
                            sentiment = _classify_sentiment(reply_body)
                            if record_reply(to_addr, reply_body=reply_body, reply_sentiment=sentiment):
                                replies += 1
                                print(f"  Reply detected (sent by us to {to_addr}) [sentiment: {sentiment}]")
                            break
        else:
            sentiment = _classify_sentiment(reply_body)
            if record_reply(target_addr, reply_body=reply_body, reply_sentiment=sentiment):
                replies += 1
                print(f"  Reply detected from {target_addr} [sentiment: {sentiment}]")
                if mark_seen:
                    try:
                        mail.store(msg_id_bytes, "+FLAGS", "\\Seen")
                    except Exception:
                        pass

    return replies


def _imap_date(days_ago: int) -> str:
    """Format a date N days ago for IMAP SINCE queries."""
    from datetime import date, timedelta
    d = date.today() - timedelta(days=days_ago)
    return d.strftime("%d-%b-%Y")


# ---------------------------------------------------------------------------
# Bounce detection — scans for delivery failure notifications
# ---------------------------------------------------------------------------

_BOUNCE_FROM = {"mailer-daemon", "postmaster", "mail-daemon"}
_BOUNCE_SUBJECTS = [
    "delivery status notification", "undeliverable", "returned mail",
    "mail delivery failed", "failure notice", "delivery failure",
    "not delivered", "delivery problem", "undelivered mail",
    "message not delivered", "could not be delivered",
]


def check_bounces(imap_host: str | None = None, imap_port: int | None = None,
                  imap_user: str | None = None, imap_password: str | None = None) -> int:
    """Check inbox for bounce notifications and mark contacts as bounced.
    Can use default IMAP or per-account credentials.
    Returns the number of bounces detected."""
    host = imap_host or IMAP_HOST
    port = imap_port or IMAP_PORT
    user = imap_user or IMAP_USER
    pw = imap_password or IMAP_PASSWORD
    if not user or not pw:
        return 0

    known_emails = get_all_sent_recipient_emails()
    if not known_emails:
        return 0

    bounces_found = 0
    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pw)
        mail.select("INBOX")

        # Search recent emails from mailer-daemon/postmaster
        _, msg_ids = mail.search(None, "(SINCE " + _imap_date(3) + ")")
        if not msg_ids[0]:
            mail.logout()
            return 0

        all_ids = msg_ids[0].split()
        if not all_ids:
            mail.logout()
            return 0

        # Batch fetch headers
        id_range = b",".join(all_ids[-200:])  # Check last 200 messages max
        try:
            _, hdr_data = mail.fetch(id_range, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        except Exception:
            mail.logout()
            return 0

        bounce_msg_ids = []
        for item in hdr_data:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            try:
                msg = email.message_from_bytes(item[1])
                from_header = msg.get("From", "")
                _, from_addr = parseaddr(from_header)
                from_local = from_addr.lower().split("@")[0] if "@" in from_addr else from_addr.lower()
                subject = (msg.get("Subject", "") or "").lower()

                is_bounce = from_local in _BOUNCE_FROM or any(
                    kw in subject for kw in _BOUNCE_SUBJECTS
                )
                if is_bounce:
                    bounce_msg_ids.append(all_ids[len(bounce_msg_ids)])
            except Exception:
                continue

        if not bounce_msg_ids:
            mail.logout()
            return 0

        # Fetch bodies of bounce messages to extract the bounced email address
        body_range = b",".join(bounce_msg_ids[:50])  # Max 50 bounces at a time
        try:
            _, body_data = mail.fetch(body_range, "(BODY.PEEK[])")
        except Exception:
            mail.logout()
            return 0

        for b_item in body_data:
            if not isinstance(b_item, tuple) or len(b_item) != 2:
                continue
            try:
                full_msg = email.message_from_bytes(b_item[1])
                body = _extract_body(full_msg)
                # Try to find the original recipient email in the bounce body
                import re
                # Common patterns in bounce messages
                found_emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', body.lower())
                for bounced_addr in found_emails:
                    if bounced_addr in known_emails:
                        if _mark_bounced(bounced_addr):
                            bounces_found += 1
                            print(f"  Bounce detected for {bounced_addr}")
                        break
            except Exception:
                continue

        mail.logout()
    except Exception as e:
        print(f"[BOUNCE CHECK] IMAP error: {e}")

    return bounces_found


def _mark_bounced(contact_email: str) -> bool:
    """Mark a contact as bounced in both campaign contacts and sent_emails."""
    from outreach.db import get_db
    with get_db() as db:
        row = db.execute(
            "SELECT c.id as contact_id, se.id as sent_id "
            "FROM contacts c "
            "JOIN sent_emails se ON se.contact_id = c.id "
            "WHERE LOWER(c.email) = LOWER(?) AND c.status NOT IN ('bounced', 'replied') "
            "ORDER BY se.sent_at DESC LIMIT 1",
            (contact_email,),
        ).fetchone()
        if not row:
            return False
        db.execute("UPDATE contacts SET status = 'bounced' WHERE id = ?", (row["contact_id"],))
        db.execute("UPDATE sent_emails SET status = 'bounced' WHERE id = ?", (row["sent_id"],))
        return True
