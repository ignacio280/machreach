"""
Mail Hub — IMAP inbox fetcher + AI triage for day-to-day email management.

Fetches recent emails from the user's inbox, classifies them by priority
and category using GPT, and stores them for the dashboard.
"""
from __future__ import annotations

import email
import imaplib
import json
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from outreach.config import IMAP_HOST, IMAP_PASSWORD, IMAP_PORT, IMAP_USER


def _decode_header_value(value: str) -> str:
    """Decode an email header that might be encoded."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_text(msg) -> str:
    """Extract plain-text body from a message."""


def peek_unseen(imap_host: str | None = None, imap_port: int | None = None,
                imap_user: str | None = None, imap_password: str | None = None,
                since_date: str | None = None) -> int:
    """Quick IMAP check: return number of emails received after since_date.

    since_date should be in IMAP format like '08-Apr-2026'.
    If not provided, defaults to today (= only emails from today).
    Costs nothing (no OpenAI calls, no body downloads).
    Returns -1 on error.
    """
    host = imap_host or IMAP_HOST
    port = imap_port or IMAP_PORT
    user = imap_user or IMAP_USER
    pw = imap_password or IMAP_PASSWORD

    if not user or not pw:
        return -1

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pw)
        mail.select("INBOX", readonly=True)

        if not since_date:
            from datetime import date
            since_date = date.today().strftime("%d-%b-%Y")

        _, data = mail.search(None, f"(SINCE {since_date})")
        count = len(data[0].split()) if data[0] else 0
        mail.logout()
        return count
    except Exception as e:
        print(f"[MAIL HUB] IMAP peek error: {e}")
        return -1


def _extract_text(msg) -> str:
    """Extract plain-text body from a message."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def fetch_inbox(days: int = 3, limit: int = 50, existing_ids: set | None = None,
                imap_host: str | None = None, imap_port: int | None = None,
                imap_user: str | None = None, imap_password: str | None = None) -> list[dict]:
    """Fetch recent emails from an IMAP inbox.

    If credentials are not provided, falls back to .env defaults.
    If existing_ids is provided, skip emails already in the database.
    Returns list of dicts with: message_id, from_name, from_email,
    to_email, subject, body_preview, received_at
    """
    host = imap_host or IMAP_HOST
    port = imap_port or IMAP_PORT
    user = imap_user or IMAP_USER
    pw = imap_password or IMAP_PASSWORD

    if not user or not pw:
        return []

    if existing_ids is None:
        existing_ids = set()

    results = []
    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pw)
        mail.select("INBOX", readonly=True)

        from datetime import date, timedelta
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, msg_ids = mail.search(None, f"(SINCE {since})")

        if not msg_ids[0]:
            mail.logout()
            return []

        ids = msg_ids[0].split()
        # Take the most recent ones
        ids = ids[-limit:]

        # --- Batch dedup: single IMAP call for all Message-ID headers ---
        new_ids = list(ids)
        if existing_ids and ids:
            id_range = b",".join(ids)
            try:
                _, hdr_data = mail.fetch(id_range, "(BODY.PEEK[HEADER.FIELDS (Message-ID)])")
                # hdr_data comes as pairs: (b'seq (FLAGS...)', header_bytes), b')', ...
                idx = 0
                skip_set = set()
                for item in hdr_data:
                    if isinstance(item, tuple) and len(item) == 2:
                        header_bytes = item[1]
                        if isinstance(header_bytes, bytes):
                            decoded = header_bytes.decode("utf-8", errors="replace")
                            for line in decoded.splitlines():
                                if line.lower().startswith("message-id:"):
                                    mid_value = line.split(":", 1)[1].strip()
                                    if mid_value in existing_ids:
                                        skip_set.add(idx)
                                    break
                        idx += 1
                new_ids = [mid for i, mid in enumerate(ids) if i not in skip_set]
            except Exception:
                new_ids = list(ids)  # fallback to fetch all

        if not new_ids:
            mail.logout()
            return []

        # --- Batch fetch: FLAGS + BODY to detect seen status ---
        body_range = b",".join(new_ids)
        try:
            _, body_data = mail.fetch(body_range, "(FLAGS BODY.PEEK[])")
        except Exception:
            mail.logout()
            return []

        import re as _re
        for item in body_data:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            try:
                raw = item[1]
                msg = email.message_from_bytes(raw)
                # Detect \Seen flag from IMAP response
                flags_str = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
                is_seen = bool(_re.search(r'\\Seen', flags_str))

                # Parse headers
                subject = _decode_header_value(msg.get("Subject", ""))
                from_header = msg.get("From", "")
                from_name, from_email_addr = parseaddr(from_header)
                from_name = _decode_header_value(from_name)
                to_header = msg.get("To", "")
                _, to_email_addr = parseaddr(to_header)
                message_id = msg.get("Message-ID", "")

                # Skip if already exists (double-check)
                if message_id in existing_ids:
                    continue

                # Parse date
                date_header = msg.get("Date", "")
                try:
                    dt = parsedate_to_datetime(date_header)
                    received_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    received_at = ""

                # Extract body preview
                body = _extract_text(msg)
                body_preview = body[:10000].strip() if body else ""

                results.append({
                    "message_id": message_id,
                    "from_name": from_name,
                    "from_email": from_email_addr.lower().strip(),
                    "to_email": to_email_addr.lower().strip() if to_email_addr else "",
                    "subject": subject,
                    "body_preview": body_preview,
                    "received_at": received_at,
                    "is_seen": is_seen,
                })
            except Exception:
                continue

        mail.logout()
    except Exception as e:
        print(f"[MAIL HUB] IMAP error: {e}")

    return results


def classify_emails_batch(emails: list[dict]) -> list[dict]:
    """Classify a batch of emails by priority and category using GPT.

    Takes list of {subject, from_email, body_preview} dicts.
    Returns list of {priority, category, summary} dicts in same order.
    """
    if not emails:
        return []

    try:
        from outreach.ai import client as openai_client
    except Exception:
        # Fallback if AI not configured
        return [{"priority": "normal", "category": "uncategorized", "summary": ""}
                for _ in emails]

    # Build a compact prompt for batch classification
    email_texts = []
    for i, e in enumerate(emails[:20]):  # Max 20 at a time to stay within limits
        email_texts.append(
            f"[{i}] From: {e.get('from_email','')} | "
            f"Subject: {e.get('subject','')} | "
            f"Preview: {e.get('body_preview','')[:200]}"
        )

    prompt = f"""Classify each email below. Return a JSON array with one object per email, in order.

Each object must have:
- "priority": one of "urgent", "important", "normal", "low"
- "category": one of "action_required", "meeting", "fyi", "newsletter", "personal", "spam"
- "summary": a single-sentence summary (max 15 words)

Priority rules:
- urgent = needs response within hours (deadlines, escalations, client emergencies)
- important = needs response today (direct requests, project updates needing action)
- normal = general business email, can respond within a day or two
- low = newsletters, marketing, automated notifications, no action needed

Category rules:
- action_required = explicitly asks you to do something or respond
- meeting = calendar invites, scheduling, meeting notes
- fyi = informational, CC'd, status updates, no action needed
- newsletter = marketing emails, subscriptions, promotions
- personal = personal/social messages
- spam = obvious spam or irrelevant

Emails:
{chr(10).join(email_texts)}

Return ONLY a JSON array, no markdown, no explanation."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        classifications = json.loads(text)

        # Ensure same length
        while len(classifications) < len(emails):
            classifications.append({"priority": "normal", "category": "uncategorized", "summary": ""})

        # Validate values
        valid_priorities = {"urgent", "important", "normal", "low"}
        valid_categories = {"action_required", "meeting", "fyi", "newsletter", "personal", "spam"}
        for c in classifications:
            if c.get("priority") not in valid_priorities:
                c["priority"] = "normal"
            if c.get("category") not in valid_categories:
                c["category"] = "uncategorized"
            if not isinstance(c.get("summary"), str):
                c["summary"] = ""

        return classifications
    except Exception as e:
        print(f"[MAIL HUB] AI classification error: {e}")
        return [{"priority": "normal", "category": "uncategorized", "summary": ""}
                for _ in emails]


def sync_inbox(client_id: int, days: int = 3, account_id: int | None = None) -> int:
    """Fetch emails from IMAP, classify with AI, and store in DB.

    If account_id is provided, uses that account's credentials.
    Otherwise falls back to .env IMAP defaults.
    Returns number of new emails added. Skips already-synced messages.
    """
    from outreach.db import upsert_mail, get_db, get_email_account

    # Get account credentials if specified
    imap_kwargs = {}
    if account_id:
        acct = get_email_account(account_id, client_id)
        if acct:
            imap_kwargs = {
                "imap_host": acct["imap_host"],
                "imap_port": acct["imap_port"],
                "imap_user": acct["email"],
                "imap_password": acct["password"],
            }

    # Get existing message IDs to skip them during fetch
    existing_ids = set()
    with get_db() as db:
        rows = db.execute(
            "SELECT message_id FROM mail_inbox WHERE client_id = ?",
            (client_id,)).fetchall()
        existing_ids = {r[0] for r in rows}

    raw_emails = fetch_inbox(days=days, existing_ids=existing_ids, **imap_kwargs)
    if not raw_emails:
        return 0

    # Build set of campaign contact emails and sent subjects for auto-important
    campaign_emails = set()
    campaign_subjects = set()
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT LOWER(c.email) FROM contacts c
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE camp.client_id = ?
        """, (client_id,)).fetchall()
        campaign_emails = {r[0] for r in rows}
        rows = db.execute("""
            SELECT DISTINCT se.subject FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE camp.client_id = ?
        """, (client_id,)).fetchall()
        campaign_subjects = {r[0].lower() for r in rows if r[0]}

    def _is_campaign_related(email_data: dict) -> bool:
        sender = email_data.get("from_email", "").lower().strip()
        if sender in campaign_emails:
            return True
        subj = (email_data.get("subject") or "").strip()
        if subj.lower().startswith("re:"):
            clean = subj[3:].strip().lower()
            if clean in campaign_subjects:
                return True
        return False

    # Stage 1: Insert all emails immediately with default classification
    new_count = 0
    unclassified_ids = []  # (db_mail_id, email_data) for background classification
    for email_data in raw_emails:
        priority = "normal"
        if _is_campaign_related(email_data):
            priority = "important"

        added = upsert_mail(
            client_id=client_id,
            message_id=email_data["message_id"],
            from_name=email_data["from_name"],
            from_email=email_data["from_email"],
            to_email=email_data["to_email"],
            subject=email_data["subject"],
            body_preview=email_data["body_preview"],
            received_at=email_data["received_at"],
            priority=priority,
            category="uncategorized",
            ai_summary="",
            account_id=account_id,
            is_read=1 if email_data.get("is_seen") else 0,
        )
        if added:
            new_count += 1
            unclassified_ids.append(email_data)

    # Stage 2: Classify in background thread (non-blocking)
    if unclassified_ids:
        import threading
        def _bg_classify():
            try:
                for i in range(0, len(unclassified_ids), 20):
                    batch = unclassified_ids[i:i + 20]
                    classifications = classify_emails_batch(batch)
                    for email_data, cls in zip(batch, classifications):
                        p = cls.get("priority", "normal")
                        if _is_campaign_related(email_data) and p not in ("urgent",):
                            p = "important"
                        try:
                            with get_db() as db:
                                db.execute("""
                                    UPDATE mail_inbox
                                    SET priority = ?, category = ?, ai_summary = ?
                                    WHERE client_id = ? AND message_id = ?
                                """, (p, cls.get("category", "uncategorized"),
                                      cls.get("summary", ""), client_id,
                                      email_data["message_id"]))
                                db.commit()
                        except Exception:
                            pass
            except Exception as e:
                print(f"[MAIL HUB] Background classification error: {e}")
        threading.Thread(target=_bg_classify, daemon=True).start()

    return new_count
