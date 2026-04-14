"""
Background email worker — sends queued emails on schedule.
Run separately from the web app: python worker.py
"""
from __future__ import annotations

import os
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from outreach.ai import personalize_email, personalize_subject, translate_email
from outreach.config import DELAY_BETWEEN_EMAILS_SEC, PLAN_LIMITS, SENDER_NAME

# ── Sentry error tracking (production only) ──
from outreach.config import SENTRY_DSN
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=SENTRY_DSN, environment="worker")

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
        from outreach.db import get_db, _fetchone
        with get_db() as db:
            row = _fetchone(db, "SELECT plan FROM subscriptions WHERE client_id = %s", (client_id,))
            plan = row["plan"] if row else "free"
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
      try:
        # Check per-client monthly email limit
        try:
            from outreach.db import check_limit, increment_usage, get_db, get_default_email_account, _fetchone
            with get_db() as db:
                camp = _fetchone(db, "SELECT client_id FROM campaigns WHERE id = %s",
                                  (item["campaign_id"],))
            if camp:
                client_id = camp["client_id"]
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

        # Resolve per-account SMTP credentials
        acct_smtp = {}
        if camp:
            try:
                acct = get_default_email_account(camp["client_id"])
                if acct:
                    acct_smtp = {
                        "smtp_host": acct["smtp_host"],
                        "smtp_port": acct["smtp_port"],
                        "smtp_user": acct["email"],
                        "smtp_password": acct["password"],
                        "from_name": acct.get("label", "") or "",
                    }
            except Exception:
                pass

        # Record first to get tracking ID, then send with pixel embedded
        sent_id = record_sent(
            contact_id=item["contact_id"],
            sequence_id=item["sequence_id"],
            variant=variant,
            subject=subject,
            body=body,
        )

        # Look up client physical address for CAN-SPAM footer
        _physical_address = ""
        if camp:
            try:
                from outreach.db import get_client
                _client = get_client(camp["client_id"])
                if _client:
                    _physical_address = _client.get("physical_address", "")
            except Exception:
                pass

        success = send_email(
            to_email=item["email"],
            subject=subject,
            body_text=body,
            contact_id=item["contact_id"],
            tracking_id=sent_id,
            physical_address=_physical_address,
            **acct_smtp,
        )

        if success:
            cid = camp["client_id"] if camp else None
            if cid:
                sent_today[cid] = sent_today.get(cid, 0) + 1
            # Track usage for billing
            try:
                if camp:
                    increment_usage(camp["client_id"], "emails_sent")
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

      except Exception as exc:
        print(f"  ERROR processing {item.get('email', '?')}: {exc}")
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
        continue

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
        from outreach.db import get_due_scheduled_emails, mark_scheduled_sent, mark_scheduled_failed, get_mail_item, get_email_account, get_default_email_account, get_client, is_suppressed
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, BASE_URL, SECRET_KEY
        import hashlib

        # Debug: check pending emails and DB engine
        from outreach.db import get_db, _fetchall, _USE_PG, _db_fingerprint
        from outreach.config import DATABASE_URL
        from datetime import datetime
        now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[SCHEDULED] DB engine: {'PG' if _USE_PG else 'SQLite'} | db_fingerprint={_db_fingerprint()} | DATABASE_URL len={len(DATABASE_URL or '')}", flush=True)
        try:
            with get_db() as db:
                if _USE_PG:
                    from outreach.db import _fetchval
                    pg_now = _fetchval(db, "SELECT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")
                    db_name = _fetchval(db, "SELECT current_database()")
                    total_all = _fetchval(db, "SELECT COUNT(*) FROM scheduled_emails")
                    total_pending = _fetchval(db, "SELECT COUNT(*) FROM scheduled_emails WHERE status = 'pending'")
                    print(f"[SCHEDULED] PG formatted NOW={pg_now} | db={db_name} | total_rows={total_all} | pending={total_pending}", flush=True)
                    # Show last 5 rows regardless of status
                    all_rows = _fetchall(db, "SELECT id, to_email, scheduled_at, status, client_id FROM scheduled_emails ORDER BY id DESC LIMIT 5")
                    if all_rows:
                        for r in all_rows:
                            print(f"  ROW id={r['id']} to={r['to_email']} at={r['scheduled_at']} status={r['status']} client={r['client_id']}", flush=True)
                    else:
                        print(f"  TABLE IS EMPTY — no rows at all in scheduled_emails!", flush=True)
                    # Test the actual due query logic
                    due_test = _fetchall(db, """
                        SELECT id, to_email, scheduled_at, status,
                               scheduled_at <= TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS is_due
                        FROM scheduled_emails WHERE status = 'pending'
                        ORDER BY scheduled_at ASC LIMIT 5
                    """)
                    for d in due_test:
                        print(f"  PENDING id={d['id']} at={d['scheduled_at']} is_due={d['is_due']}", flush=True)
        except Exception as dbg_err:
            print(f"[SCHEDULED] Debug query failed: {dbg_err}", flush=True)

        due = get_due_scheduled_emails()
        print(f"[SCHEDULED] {len(due)} due email(s) to process", flush=True)
        if not due:
            return

        print(f"[SCHEDULED] Found {len(due)} due email(s) to send", flush=True)
        for email in due:
            try:
                # Check suppression list before sending
                if is_suppressed(email["client_id"], email["to_email"]):
                    print(f"  SKIPPED scheduled email to {email['to_email']} (suppressed/unsubscribed)")
                    mark_scheduled_failed(email["id"])
                    continue

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

                # Build CAN-SPAM footer
                client = get_client(email["client_id"])
                physical_addr = client.get("physical_address", "") if client else ""
                app_secret = SECRET_KEY
                token = hashlib.sha256(f"{email['client_id']}:{email['to_email']}:{app_secret}".encode()).hexdigest()[:16]
                unsub_url = f"{BASE_URL}/unsubscribe/g/{token}?e={email['to_email']}&c={email['client_id']}"
                addr_html = f'<div style="color:#A0AEC0;font-size:10px;margin-top:4px;">{physical_addr}</div>' if physical_addr else ''
                unsub_footer = (f'<div style="text-align:center;padding:16px 0 8px;margin-top:20px;border-top:1px solid #E2E8F0;font-size:11px;color:#A0AEC0;">'
                               f'<a href="{unsub_url}" style="color:#A0AEC0;font-size:11px;text-decoration:underline;">Unsubscribe</a>'
                               f'{addr_html}</div>')

                # Build multipart email with HTML footer
                is_reply = bool(email.get("reply_to_mail_id"))
                msg = MIMEMultipart("alternative")
                msg["From"] = smtp_user
                msg["To"] = email["to_email"]
                msg["Subject"] = email["subject"]

                if not is_reply:
                    # Add unsubscribe headers for non-reply emails
                    msg["List-Unsubscribe"] = f"<{unsub_url}>"
                    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

                # Plain text + HTML
                plain_unsub = f"\n\n---\nUnsubscribe: {unsub_url}" + (f"\n{physical_addr}" if physical_addr else "")
                msg.attach(MIMEText(email["body"] + (plain_unsub if not is_reply else ""), "plain", "utf-8"))
                body_html = email["body"].replace("\\n", "<br>").replace("\n", "<br>")
                footer = unsub_footer if not is_reply else ""
                msg.attach(MIMEText(f'<div style="font-family:sans-serif;font-size:14px;line-height:1.6;">{body_html}{footer}</div>', "html", "utf-8"))

                # If replying, add threading headers
                if is_reply:
                    original = get_mail_item(email["reply_to_mail_id"], email["client_id"])
                    if original and original.get("message_id"):
                        msg["In-Reply-To"] = original["message_id"]
                        msg["References"] = original["message_id"]

                if smtp_port == 587:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                        srv.starttls()
                        srv.login(smtp_user, smtp_pw)
                        srv.send_message(msg)
                else:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
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
        from outreach.db import get_db, check_limit, increment_usage, get_email_accounts, get_subscription, _exec, _fetchval, _fetchall
        from outreach.mail_hub import sync_inbox, peek_unseen
        with get_db() as db:
            clients = _fetchall(db, "SELECT id FROM clients")
        for row in clients:
            try:
                client_id = row["id"]
                # Only auto-sync for paid tiers
                sub = get_subscription(client_id)
                plan = sub.get("plan", "free") if sub else "free"
                if plan == "free":
                    continue

                allowed, used, limit = check_limit(client_id, "mail_hub_syncs")
                if not allowed:
                    continue

                # Peek first — only sync if there are unseen emails since last sync
                # Get last synced email date for this client
                with get_db() as db:
                    last_synced = _fetchval(db,
                        "SELECT MAX(received_at) FROM mail_inbox WHERE client_id = %s",
                        (client_id,))

                from datetime import datetime, timedelta
                if last_synced:
                    try:
                        dt = datetime.strptime(last_synced[:10], "%Y-%m-%d")
                    except ValueError:
                        dt = datetime.now()
                    imap_since = dt.strftime("%d-%b-%Y")
                else:
                    imap_since = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")

                accounts = get_email_accounts(client_id)
                if not accounts:
                    continue  # No connected accounts — skip (never use global .env creds)

                has_new = False
                for acct in accounts:
                    n = peek_unseen(
                        imap_host=acct["imap_host"], imap_port=acct["imap_port"],
                        imap_user=acct["email"], imap_password=acct["password"],
                        since_date=imap_since)
                    if n > 0:
                        has_new = True
                        break

                if not has_new:
                    continue

                total_new = 0
                for acct in accounts:
                    n = sync_inbox(client_id, days=3, account_id=acct["id"])
                    total_new += n
                if total_new:
                    increment_usage(client_id, "mail_hub_syncs")
                    print(f"[MAIL HUB] Auto-synced {total_new} new email(s) for client {client_id}")
            except Exception as e:
                print(f"[MAIL HUB] Sync error for client {row.get('id', '?')}: {e}")
    except Exception as e:
        print(f"[MAIL HUB] Background sync error (non-fatal): {e}")


def refresh_student_plans():
    """Midnight job: for each student with a plan, check for incomplete
    assignments and regenerate the study plan with those rolled over."""
    try:
        from student import db as sdb
        from student.analyzer import generate_study_plan
        import json
        from datetime import datetime

        client_ids = sdb.get_all_student_client_ids()
        today = datetime.now().strftime("%Y-%m-%d")

        for client_id in client_ids:
            try:
                # Check if there are incomplete assignments
                incomplete = sdb.get_incomplete_assignments(client_id, today)

                # Get course data
                courses = sdb.get_courses(client_id)
                courses_data = []
                course_difficulties = {}
                for c in courses:
                    analysis = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]
                    if analysis and analysis.get("course_name"):
                        courses_data.append(analysis)
                        diff = c.get("difficulty", 3) or 3
                        course_difficulties[analysis["course_name"]] = diff

                if not courses_data:
                    continue

                # Get schedule settings
                schedule_settings = sdb.get_schedule_settings(client_id)
                schedule_list = []
                if schedule_settings:
                    for s in schedule_settings:
                        schedule_list.append({
                            "day": s["day_of_week"],
                            "hours": s["available_hours"],
                            "free": bool(s["is_free_day"]),
                        })

                # Get latest preferences
                plan_row = sdb.get_latest_plan(client_id)
                preferences = plan_row.get("preferences_json", {}) if plan_row else {}

                # Regenerate
                plan = generate_study_plan(
                    courses_data, preferences,
                    schedule_settings=schedule_list or None,
                    course_difficulties=course_difficulties or None,
                    incomplete_assignments=incomplete or None,
                )
                if plan.get("daily_plan"):
                    sdb.save_study_plan(client_id, plan, preferences)
                    print(f"[STUDENT] Refreshed study plan for client {client_id}"
                          f" ({len(incomplete)} incomplete assignments rolled over)")
            except Exception as e:
                print(f"[STUDENT] Plan refresh failed for client {client_id}: {e}")
    except Exception as e:
        print(f"[STUDENT] Midnight plan refresh error (non-fatal): {e}")


if __name__ == "__main__":
    init_db()

    # Init student tables too
    try:
        from student.db import init_student_db
        init_student_db()
    except Exception as e:
        print(f"Student DB init skipped: {e}")

    print("Email worker started. Checking every 5 minutes...")
    print("Daily limits are per-plan (free=50, growth=200, pro=500, unlimited=∞)")

    scheduler = BlockingScheduler()
    scheduler.add_job(send_batch, "interval", minutes=1, id="send_batch")
    scheduler.add_job(process_snoozes, "interval", minutes=1, id="process_snoozes")
    scheduler.add_job(send_scheduled, "interval", minutes=1, id="send_scheduled")
    scheduler.add_job(sync_mail_hub, "interval", minutes=3, id="sync_mail_hub")
    scheduler.add_job(refresh_student_plans, "cron", hour=0, minute=0, id="refresh_student_plans")

    # Run once immediately
    send_batch()
    process_snoozes()
    send_scheduled()
    sync_mail_hub()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Worker stopped.")
