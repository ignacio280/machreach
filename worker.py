"""
Background email worker — sends queued emails on schedule.
Run separately from the web app: python worker.py
"""
from __future__ import annotations

import time

from apscheduler.schedulers.blocking import BlockingScheduler

from outreach.ai import personalize_email, personalize_subject, translate_email
from outreach.config import DELAY_BETWEEN_EMAILS_SEC, PLAN_LIMITS, SENDER_NAME
from outreach.db import get_emails_to_send, init_db, record_sent
from outreach.reply_checker import check_replies, check_bounces
from outreach.sender import pick_variant, send_email

sent_today = {}  # {client_id: count}
last_reset_day = None


def _reset_daily_counter():
    global sent_today, last_reset_day
    from datetime import date
    today = date.today()
    if last_reset_day != today:
        sent_today = {}
        last_reset_day = today


def _get_daily_limit(client_id):
    """Return daily email limit for a client based on their plan."""
    try:
        from outreach.db import get_db
        with get_db() as db:
            row = db.execute("SELECT plan FROM subscriptions WHERE client_id = ?", (client_id,)).fetchone()
            plan = row[0] if row else "free"
    except Exception:
        plan = "free"
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["emails_per_day"]


def send_batch():
    """Check for replies, then send a batch of pending emails."""
    global sent_today
    _reset_daily_counter()

    # Check for replies first — stops follow-ups to people who responded
    try:
        n = check_replies()
        if n:
            print(f"Detected {n} new reply(s).")
    except Exception as e:
        print(f"Reply check error (non-fatal): {e}")

    # Check for bounces — stops follow-ups to invalid addresses
    try:
        b = check_bounces()
        if b:
            print(f"Detected {b} bounce(s).")
    except Exception as e:
        print(f"Bounce check error (non-fatal): {e}")

    batch = get_emails_to_send(limit=30)
    if not batch:
        print("No emails to send.")
        return

    print(f"Sending {len(batch)} emails...")
    for item in batch:
        # Check per-client monthly email limit
        try:
            from outreach.db import check_limit, increment_usage, get_db
            with get_db() as db:
                camp = db.execute("SELECT client_id FROM campaigns WHERE id = ?",
                                  (item["campaign_id"],)).fetchone()
            if camp:
                client_id = camp[0]
                allowed, used, limit = check_limit(client_id, "emails_sent")
                if not allowed:
                    print(f"  Skipping {item['email']} — client {client_id} hit monthly limit ({used}/{limit})")
                    continue
                # Check daily limit per plan
                daily_limit = _get_daily_limit(client_id)
                client_sent = sent_today.get(client_id, 0)
                if daily_limit != -1 and client_sent >= daily_limit:
                    print(f"  Skipping {item['email']} — client {client_id} hit daily limit ({client_sent}/{daily_limit})")
                    continue
        except Exception:
            pass

        variant = pick_variant()
        if variant == "b" and item.get("subject_b"):
            subject = item["subject_b"]
            body = item.get("body_b") or item["body_a"]
        else:
            variant = "a"
            subject = item["subject_a"]
            body = item["body_a"]

        contact = {
            "name": item["name"],
            "company": item["company"],
            "role": item["role"],
        }
        subject = personalize_subject(subject, contact, SENDER_NAME)
        body = personalize_email(body, contact, SENDER_NAME)

        # Translate if contact language is not English
        lang = item.get("language", "en")
        if lang and lang.lower() not in ("en", "english"):
            try:
                subject, body = translate_email(subject, body, lang)
                print(f"    Translated email for {item['email']} to {lang}")
            except Exception as e:
                print(f"    Translation failed for {item['email']} ({lang}): {e}")

        # Record first to get tracking ID, then send with pixel embedded
        sent_id = record_sent(
            contact_id=item["contact_id"],
            sequence_id=item["sequence_id"],
            variant=variant,
            subject=subject,
            body=body,
        )

        success = send_email(
            to_email=item["email"],
            subject=subject,
            body_text=body,
            contact_id=item["contact_id"],
            tracking_id=sent_id,
        )

        if success:
            cid = camp[0] if camp else None
            if cid:
                sent_today[cid] = sent_today.get(cid, 0) + 1
            # Track usage for billing
            try:
                if camp:
                    increment_usage(camp[0], "emails_sent")
            except Exception:
                pass
            step = item.get("step", 1)
            step_label = "initial" if step == 1 else f"follow-up {step - 1}"
            print(f"  Sent to {item['email']} ({step_label}, variant {variant}, id={sent_id})")
        else:
            # Send failed — remove the orphaned record so it can be retried
            from outreach.db import delete_sent_email
            delete_sent_email(sent_id, item["contact_id"])
            print(f"  FAILED to send to {item['email']} — will retry next cycle")

        time.sleep(DELAY_BETWEEN_EMAILS_SEC)

    total_sent = sum(sent_today.values())
    print(f"Batch complete. {total_sent} sent today across all clients.")


def process_snoozes():
    """Bump resurfaced snoozed emails to 'important' priority."""
    try:
        from outreach.db import process_snoozed_emails
        n = process_snoozed_emails()
        if n:
            print(f"Resurfaced {n} snoozed email(s) as important.")
    except Exception as e:
        print(f"Snooze processing error (non-fatal): {e}")


def send_scheduled():
    """Send any due scheduled emails — uses per-account SMTP if available."""
    try:
        from outreach.db import get_due_scheduled_emails, mark_scheduled_sent, mark_scheduled_failed, get_mail_item, get_email_account, get_default_email_account
        import smtplib
        from email.mime.text import MIMEText
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

        due = get_due_scheduled_emails()
        if not due:
            return

        print(f"Sending {len(due)} scheduled email(s)...")
        for email in due:
            try:
                # Determine SMTP credentials
                smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
                if email.get("account_id"):
                    acct = get_email_account(email["account_id"], email["client_id"])
                    if acct:
                        smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                        smtp_user, smtp_pw = acct["email"], acct["password"]
                elif not smtp_user:
                    acct = get_default_email_account(email["client_id"])
                    if acct:
                        smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                        smtp_user, smtp_pw = acct["email"], acct["password"]

                msg = MIMEText(email["body"], "plain", "utf-8")
                msg["From"] = smtp_user
                msg["To"] = email["to_email"]
                msg["Subject"] = email["subject"]

                # If replying, add threading headers
                if email.get("reply_to_mail_id"):
                    original = get_mail_item(email["reply_to_mail_id"], email["client_id"])
                    if original and original.get("message_id"):
                        msg["In-Reply-To"] = original["message_id"]
                        msg["References"] = original["message_id"]

                with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
                    srv.login(smtp_user, smtp_pw)
                    srv.send_message(msg)

                mark_scheduled_sent(email["id"])
                print(f"  Sent scheduled email to {email['to_email']} (id={email['id']})")
            except Exception as e:
                mark_scheduled_failed(email["id"])
                print(f"  FAILED scheduled email to {email['to_email']}: {e}")
    except Exception as e:
        print(f"Scheduled email processing error (non-fatal): {e}")


def sync_mail_hub():
    """Background sync of Mail Hub inbox for all active clients — syncs all connected accounts.
    
    For paid users: peeks first, only syncs if new mail is found (saves OpenAI cost).
    For free users: skip background sync (they sync manually within their limit).
    """
    try:
        from outreach.db import get_db, check_limit, increment_usage, get_email_accounts, get_subscription
        from outreach.mail_hub import sync_inbox, peek_unseen
        with get_db() as db:
            clients = db.execute("SELECT id FROM clients").fetchall()
        for row in clients:
            try:
                # Only auto-sync for paid tiers
                sub = get_subscription(row[0])
                plan = sub.get("plan", "free") if sub else "free"
                if plan == "free":
                    continue

                allowed, used, limit = check_limit(row[0], "mail_hub_syncs")
                if not allowed:
                    continue

                # Peek first — only sync if there are unseen emails since last sync
                # Get last synced email date for this client
                with get_db() as db:
                    last_row = db.execute(
                        "SELECT MAX(received_at) FROM mail_inbox WHERE client_id = ?",
                        (row[0],)).fetchone()
                    last_synced = last_row[0] if last_row and last_row[0] else None

                from datetime import datetime, timedelta
                if last_synced:
                    try:
                        dt = datetime.strptime(last_synced[:10], "%Y-%m-%d")
                    except ValueError:
                        dt = datetime.now()
                    imap_since = dt.strftime("%d-%b-%Y")
                else:
                    imap_since = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")

                accounts = get_email_accounts(row[0])
                has_new = False
                if accounts:
                    for acct in accounts:
                        n = peek_unseen(
                            imap_host=acct["imap_host"], imap_port=acct["imap_port"],
                            imap_user=acct["email"], imap_password=acct["password"],
                            since_date=imap_since)
                        if n > 0:
                            has_new = True
                            break
                else:
                    n = peek_unseen(since_date=imap_since)
                    has_new = n > 0

                if not has_new:
                    continue

                total_new = 0
                if accounts:
                    for acct in accounts:
                        n = sync_inbox(row[0], days=3, account_id=acct["id"])
                        total_new += n
                else:
                    # Fallback to .env credentials
                    total_new = sync_inbox(row[0], days=3)
                if total_new:
                    increment_usage(row[0], "mail_hub_syncs")
                    print(f"[MAIL HUB] Auto-synced {total_new} new email(s) for client {row[0]}")
            except Exception as e:
                print(f"[MAIL HUB] Sync error for client {row[0]}: {e}")
    except Exception as e:
        print(f"[MAIL HUB] Background sync error (non-fatal): {e}")


if __name__ == "__main__":
    init_db()
    print("Email worker started. Checking every 5 minutes...")
    print("Daily limits are per-plan (free=50, growth=200, pro=500, unlimited=∞)")

    scheduler = BlockingScheduler()
    scheduler.add_job(send_batch, "interval", minutes=1, id="send_batch")
    scheduler.add_job(process_snoozes, "interval", minutes=1, id="process_snoozes")
    scheduler.add_job(send_scheduled, "interval", minutes=1, id="send_scheduled")
    scheduler.add_job(sync_mail_hub, "interval", minutes=3, id="sync_mail_hub")

    # Run once immediately
    send_batch()
    process_snoozes()
    send_scheduled()
    sync_mail_hub()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Worker stopped.")
