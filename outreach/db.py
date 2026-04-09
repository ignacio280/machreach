"""
SQLite database — campaigns, contacts, emails, tracking.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from outreach.config import DATABASE_PATH, ENCRYPTION_KEY

# ---------------------------------------------------------------------------
# Fernet encryption for email account passwords at rest
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """Return a Fernet cipher using the configured ENCRYPTION_KEY."""
    import base64, hashlib
    # Derive a valid 32-byte Fernet key from the env variable via SHA256
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password for storage."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    """Decrypt a stored password. Returns plaintext on failure (legacy unencrypted)."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        # Legacy: password was stored in plaintext before encryption was added
        return ciphertext


def _ensure_dir():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
    _ensure_dir()
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            email       TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            business    TEXT DEFAULT '',
            mail_preferences TEXT DEFAULT '',
            is_admin    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL REFERENCES clients(id),
            token       TEXT NOT NULL UNIQUE,
            expires_at  TEXT NOT NULL,
            used        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL REFERENCES clients(id),
            name        TEXT NOT NULL,
            business_type TEXT DEFAULT '',
            target_audience TEXT DEFAULT '',
            tone        TEXT DEFAULT 'professional',
            status      TEXT DEFAULT 'draft',  -- draft, active, paused, completed
            scheduled_start TEXT,  -- optional: don't send before this datetime
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            name        TEXT DEFAULT '',
            email       TEXT NOT NULL,
            company     TEXT DEFAULT '',
            role        TEXT DEFAULT '',
            language    TEXT DEFAULT 'en',
            custom_data TEXT DEFAULT '{}',
            status      TEXT DEFAULT 'pending',  -- pending, sent, replied, bounced, unsubscribed
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS email_sequences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            step        INTEGER NOT NULL DEFAULT 1,  -- 1=initial, 2=followup1, 3=followup2...
            subject_a   TEXT NOT NULL,
            subject_b   TEXT DEFAULT '',  -- A/B test variant
            body_a      TEXT NOT NULL,
            body_b      TEXT DEFAULT '',
            delay_days  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sent_emails (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id),
            sequence_id INTEGER NOT NULL REFERENCES email_sequences(id),
            variant     TEXT DEFAULT 'a',  -- a or b
            subject     TEXT NOT NULL,
            body        TEXT NOT NULL,
            status      TEXT DEFAULT 'sent',  -- sent, opened, clicked, replied, bounced
            sent_at     TEXT DEFAULT (datetime('now', 'localtime')),
            opened_at   TEXT,
            replied_at  TEXT,
            reply_body  TEXT DEFAULT '',
            reply_sentiment TEXT DEFAULT ''  -- positive, negative, neutral
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_sent_contact ON sent_emails(contact_id);
        CREATE INDEX IF NOT EXISTS idx_sent_sequence ON sent_emails(sequence_id);

        -- Mail Hub: triaged inbox emails for day-to-day workers
        CREATE TABLE IF NOT EXISTS mail_inbox (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id    INTEGER NOT NULL REFERENCES clients(id),
            message_id   TEXT NOT NULL,          -- IMAP Message-ID header (dedup key)
            from_name    TEXT DEFAULT '',
            from_email   TEXT NOT NULL,
            to_email     TEXT DEFAULT '',
            subject      TEXT DEFAULT '',
            body_preview TEXT DEFAULT '',         -- first ~10000 chars
            received_at  TEXT DEFAULT '',
            priority     TEXT DEFAULT 'normal',   -- urgent, important, normal, low
            category     TEXT DEFAULT 'uncategorized', -- action_required, meeting, fyi, newsletter, personal, spam, uncategorized
            is_read      INTEGER DEFAULT 0,
            is_starred   INTEGER DEFAULT 0,
            is_archived  INTEGER DEFAULT 0,
            snooze_until TEXT,
            ai_summary   TEXT DEFAULT '',         -- one-line AI summary
            account_id   INTEGER,                 -- FK to email_accounts (which mailbox)
            fetched_at   TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(client_id, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_mail_inbox_client ON mail_inbox(client_id);
        CREATE INDEX IF NOT EXISTS idx_mail_inbox_priority ON mail_inbox(client_id, priority);
        CREATE INDEX IF NOT EXISTS idx_mail_inbox_category ON mail_inbox(client_id, category);

        -- Email Accounts: multiple mailboxes per client
        CREATE TABLE IF NOT EXISTS email_accounts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id    INTEGER NOT NULL REFERENCES clients(id),
            label        TEXT NOT NULL DEFAULT '',        -- e.g. "Work Gmail", "Personal"
            email        TEXT NOT NULL,                   -- email address
            imap_host    TEXT NOT NULL DEFAULT 'imap.gmail.com',
            imap_port    INTEGER NOT NULL DEFAULT 993,
            smtp_host    TEXT NOT NULL DEFAULT 'smtp.gmail.com',
            smtp_port    INTEGER NOT NULL DEFAULT 465,
            password     TEXT NOT NULL DEFAULT '',        -- app password
            is_default   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(client_id, email)
        );

        CREATE INDEX IF NOT EXISTS idx_email_accounts_client ON email_accounts(client_id);

        -- Contacts Book: personal CRM for relationship tracking
        CREATE TABLE IF NOT EXISTS contacts_book (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            email           TEXT NOT NULL,
            name            TEXT DEFAULT '',
            company         TEXT DEFAULT '',
            role            TEXT DEFAULT '',
            relationship    TEXT DEFAULT '',       -- client, colleague, vendor, friend, lead, other
            notes           TEXT DEFAULT '',       -- free-text notes about this person
            personality     TEXT DEFAULT '',       -- communication style / personality notes for AI
            tags            TEXT DEFAULT '',       -- comma-separated tags
            last_contacted  TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(client_id, email)
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_book_client ON contacts_book(client_id);
        CREATE INDEX IF NOT EXISTS idx_contacts_book_email ON contacts_book(client_id, email);

        -- Scheduled emails: compose now, send later
        CREATE TABLE IF NOT EXISTS scheduled_emails (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            to_email        TEXT NOT NULL,
            to_name         TEXT DEFAULT '',
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            scheduled_at    TEXT NOT NULL,
            status          TEXT DEFAULT 'pending',  -- pending, sent, failed
            sent_at         TEXT,
            reply_to_mail_id INTEGER,
            account_id      INTEGER,                -- FK to email_accounts (which mailbox to send from)
            created_at      TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_scheduled_client ON scheduled_emails(client_id);
        CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_emails(status, scheduled_at);

        -- Subscriptions (Stripe billing)
        CREATE TABLE IF NOT EXISTS subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL UNIQUE REFERENCES clients(id),
            plan            TEXT NOT NULL DEFAULT 'free',   -- free, growth, pro, unlimited
            stripe_customer_id   TEXT DEFAULT '',
            stripe_subscription_id TEXT DEFAULT '',
            status          TEXT DEFAULT 'active',          -- active, past_due, canceled
            current_period_start TEXT DEFAULT '',
            current_period_end   TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- Monthly usage tracking
        CREATE TABLE IF NOT EXISTS usage_tracking (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            month           TEXT NOT NULL,                  -- YYYY-MM
            emails_sent     INTEGER DEFAULT 0,
            mail_hub_syncs  INTEGER DEFAULT 0,
            ai_classifications INTEGER DEFAULT 0,
            UNIQUE(client_id, month)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_client_month ON usage_tracking(client_id, month);
        """)
        # Migrations for existing databases
        try:
            db.execute("ALTER TABLE sent_emails ADD COLUMN reply_body TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE sent_emails ADD COLUMN reply_sentiment TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE mail_inbox ADD COLUMN snooze_note TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE clients ADD COLUMN mail_preferences TEXT DEFAULT ''")
        except Exception:
            pass
        # Ensure subscriptions table exists (migration)
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL UNIQUE REFERENCES clients(id),
                plan TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id TEXT DEFAULT '',
                stripe_subscription_id TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                current_period_start TEXT DEFAULT '',
                current_period_end TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )""")
        except Exception:
            pass
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS usage_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(id),
                month TEXT NOT NULL,
                emails_sent INTEGER DEFAULT 0,
                mail_hub_syncs INTEGER DEFAULT 0,
                ai_classifications INTEGER DEFAULT 0,
                UNIQUE(client_id, month)
            )""")
        except Exception:
            pass
        # Migration: email_accounts table
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS email_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(id),
                label TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL,
                imap_host TEXT NOT NULL DEFAULT 'imap.gmail.com',
                imap_port INTEGER NOT NULL DEFAULT 993,
                smtp_host TEXT NOT NULL DEFAULT 'smtp.gmail.com',
                smtp_port INTEGER NOT NULL DEFAULT 465,
                password TEXT NOT NULL DEFAULT '',
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(client_id, email)
            )""")
        except Exception:
            pass
        # Migration: add account_id to mail_inbox
        try:
            db.execute("ALTER TABLE mail_inbox ADD COLUMN account_id INTEGER")
        except Exception:
            pass
        # Migration: add account_id to scheduled_emails
        try:
            db.execute("ALTER TABLE scheduled_emails ADD COLUMN account_id INTEGER")
        except Exception:
            pass
        # Migration: add is_admin column to clients
        try:
            db.execute("ALTER TABLE clients ADD COLUMN is_admin INTEGER DEFAULT 0")
        except Exception:
            pass
        # Migration: add scheduled_start column to campaigns
        try:
            db.execute("ALTER TABLE campaigns ADD COLUMN scheduled_start TEXT")
        except Exception:
            pass
        # Migration: team_members table
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES clients(id),
                member_email TEXT NOT NULL,
                member_client_id INTEGER REFERENCES clients(id),
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'pending',
                invite_token TEXT,
                invited_at TEXT DEFAULT (datetime('now', 'localtime')),
                accepted_at TEXT,
                UNIQUE(owner_id, member_email)
            )""")
        except Exception:
            pass
        # Migration: add mail_exclusions column to clients
        try:
            db.execute("ALTER TABLE clients ADD COLUMN mail_exclusions TEXT DEFAULT ''")
        except Exception:
            pass
        # Migration: add language column to contacts_book
        try:
            db.execute("ALTER TABLE contacts_book ADD COLUMN language TEXT DEFAULT ''")
        except Exception:
            pass
    print("Database initialized.")

def create_client(name: str, email: str, password_hash: str, business: str = "") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO clients (name, email, password, business) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, business),
        )
        return cur.lastrowid


def get_client_by_email(email: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM clients WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Email Accounts (multi-mailbox)
# ---------------------------------------------------------------------------

def get_email_accounts(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM email_accounts WHERE client_id = ? ORDER BY is_default DESC, created_at ASC",
            (client_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["password"] = decrypt_password(d["password"])
            result.append(d)
        return result


def get_email_account(account_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM email_accounts WHERE id = ? AND client_id = ?",
            (account_id, client_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["password"] = decrypt_password(d["password"])
        return d


def get_default_email_account(client_id: int) -> dict | None:
    """Get the default email account, or the first one if no default set."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM email_accounts WHERE client_id = ? ORDER BY is_default DESC, id ASC LIMIT 1",
            (client_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["password"] = decrypt_password(d["password"])
        return d


def create_email_account(client_id: int, label: str, email: str, password: str,
                         imap_host: str = "imap.gmail.com", imap_port: int = 993,
                         smtp_host: str = "smtp.gmail.com", smtp_port: int = 465,
                         is_default: int = 0) -> int:
    with get_db() as db:
        # If setting as default, unset other defaults
        if is_default:
            db.execute("UPDATE email_accounts SET is_default = 0 WHERE client_id = ?", (client_id,))
        # If this is the first account, make it default
        existing = db.execute("SELECT COUNT(*) FROM email_accounts WHERE client_id = ?", (client_id,)).fetchone()[0]
        if existing == 0:
            is_default = 1
        cur = db.execute("""
            INSERT INTO email_accounts (client_id, label, email, imap_host, imap_port,
                                        smtp_host, smtp_port, password, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (client_id, label, email, imap_host, imap_port, smtp_host, smtp_port, encrypt_password(password), is_default))
        return cur.lastrowid


def update_email_account(account_id: int, client_id: int, **kwargs) -> bool:
    allowed = {"label", "email", "imap_host", "imap_port", "smtp_host", "smtp_port", "password", "is_default"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    # Encrypt password if being updated
    if "password" in updates:
        updates["password"] = encrypt_password(updates["password"])
    with get_db() as db:
        if updates.get("is_default"):
            db.execute("UPDATE email_accounts SET is_default = 0 WHERE client_id = ?", (client_id,))
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [account_id, client_id]
        db.execute(f"UPDATE email_accounts SET {sets} WHERE id = ? AND client_id = ?", vals)
        return True


def delete_email_account(account_id: int, client_id: int) -> bool:
    with get_db() as db:
        db.execute("DELETE FROM email_accounts WHERE id = ? AND client_id = ?",
                   (account_id, client_id))
        # If we deleted the default, promote the next one
        remaining = db.execute(
            "SELECT id FROM email_accounts WHERE client_id = ? LIMIT 1",
            (client_id,)).fetchone()
        if remaining:
            db.execute("UPDATE email_accounts SET is_default = 1 WHERE id = ?", (remaining[0],))
        return True


def create_campaign(client_id: int, name: str, business_type: str,
                    target_audience: str, tone: str, scheduled_start: str = "") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO campaigns (client_id, name, business_type, target_audience, tone, scheduled_start) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, name, business_type, target_audience, tone, scheduled_start or None),
        )
        return cur.lastrowid


def update_campaign_schedule(campaign_id: int, scheduled_start: str):
    with get_db() as db:
        db.execute("UPDATE campaigns SET scheduled_start = ? WHERE id = ?",
                   (scheduled_start or None, campaign_id))


def get_campaigns(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM campaigns WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_campaign(campaign_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(row) if row else None


def add_contacts(campaign_id: int, contacts: list[dict]) -> int:
    with get_db() as db:
        # Ensure language column exists (migration for existing DBs)
        try:
            db.execute("ALTER TABLE contacts ADD COLUMN language TEXT DEFAULT 'en'")
        except Exception:
            pass  # column already exists
        count = 0
        for c in contacts:
            try:
                db.execute(
                    "INSERT INTO contacts (campaign_id, name, email, company, role, language) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (campaign_id, c.get("name", ""), c["email"],
                     c.get("company", ""), c.get("role", ""),
                     c.get("language", "en")),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass  # duplicate
        return count


def get_campaign_contacts(campaign_id: int, status: str | None = None) -> list[dict]:
    with get_db() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM contacts WHERE campaign_id = ? AND status = ?",
                (campaign_id, status),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM contacts WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def save_sequence(campaign_id: int, step: int, subject_a: str, subject_b: str,
                  body_a: str, body_b: str, delay_days: int = 0) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO email_sequences (campaign_id, step, subject_a, subject_b, "
            "body_a, body_b, delay_days) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, step, subject_a, subject_b, body_a, body_b, delay_days),
        )
        return cur.lastrowid


def get_sequences(campaign_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM email_sequences WHERE campaign_id = ? ORDER BY step",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def record_sent(contact_id: int, sequence_id: int, variant: str,
                subject: str, body: str) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO sent_emails (contact_id, sequence_id, variant, subject, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (contact_id, sequence_id, variant, subject, body),
        )
        db.execute("UPDATE contacts SET status = 'sent' WHERE id = ?", (contact_id,))
        return cur.lastrowid


def delete_sent_email(sent_id: int, contact_id: int):
    """Remove a sent_email record (e.g. if SMTP failed) and reset contact to pending."""
    with get_db() as db:
        db.execute("DELETE FROM sent_emails WHERE id = ?", (sent_id,))
        # Reset contact only if no other sent_emails exist for this contact
        other = db.execute(
            "SELECT COUNT(*) FROM sent_emails WHERE contact_id = ?", (contact_id,)
        ).fetchone()[0]
        if other == 0:
            db.execute("UPDATE contacts SET status = 'pending' WHERE id = ?", (contact_id,))


def record_open(sent_email_id: int):
    with get_db() as db:
        db.execute(
            "UPDATE sent_emails SET status = 'opened', opened_at = datetime('now', 'localtime') WHERE id = ? AND status = 'sent'",
            (sent_email_id,),
        )
        # Also update contact status to 'opened' if it's still 'sent'
        db.execute("""
            UPDATE contacts SET status = 'opened'
            WHERE id = (SELECT contact_id FROM sent_emails WHERE id = ?)
              AND status = 'sent'
        """, (sent_email_id,))


def get_campaign_stats(campaign_id: int) -> dict:
    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM contacts WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()[0]
        sent = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = ?", (campaign_id,)
        ).fetchone()[0]
        opened = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = ? AND se.status IN ('opened', 'clicked', 'replied')",
            (campaign_id,),
        ).fetchone()[0]
        replied = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = ? AND se.status = 'replied'",
            (campaign_id,),
        ).fetchone()[0]
        bounced = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = ? AND se.status = 'bounced'",
            (campaign_id,),
        ).fetchone()[0]
        return {
            "total_contacts": total,
            "emails_sent": sent,
            "opens": opened,
            "open_rate": opened / sent if sent else 0,
            "replies": replied,
            "reply_rate": replied / sent if sent else 0,
            "bounced": bounced,
            "bounce_rate": bounced / sent if sent else 0,
        }


def get_ab_stats(campaign_id: int) -> list[dict]:
    """Return A/B variant comparison stats per sequence step."""
    with get_db() as db:
        rows = db.execute("""
            SELECT es.step, se.variant,
                   COUNT(*) AS sent,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) AS opened,
                   SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) AS replied,
                   SUM(CASE WHEN se.status = 'bounced' THEN 1 ELSE 0 END) AS bounced
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE c.campaign_id = ?
            GROUP BY es.step, se.variant
            ORDER BY es.step, se.variant
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rows]


def get_emails_to_send(limit: int = 50) -> list[dict]:
    """Get pending contacts from active campaigns that haven't been emailed yet (step 1),
    or contacts due for a follow-up."""
    with get_db() as db:
        results = []

        # Step 1: contacts never emailed in active campaigns
        rows = db.execute("""
            SELECT c.id as contact_id, c.name, c.email, c.company, c.role,
                   c.language,
                   c.campaign_id, camp.tone, camp.business_type, camp.target_audience,
                   es.id as sequence_id, es.subject_a, es.subject_b, es.body_a, es.body_b, es.step
            FROM contacts c
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON es.campaign_id = camp.id AND es.step = 1
            WHERE camp.status = 'active'
              AND c.status = 'pending'
              AND c.id NOT IN (SELECT contact_id FROM sent_emails)
              AND (camp.scheduled_start IS NULL OR camp.scheduled_start <= datetime('now', 'localtime'))
            LIMIT ?
        """, (limit,)).fetchall()
        results.extend(dict(r) for r in rows)

        remaining = limit - len(results)
        if remaining <= 0:
            return results
        followup_rows = db.execute("""
            SELECT c.id as contact_id, c.name, c.email, c.company, c.role,
                   c.language,
                   c.campaign_id, camp.tone, camp.business_type, camp.target_audience,
                   next_seq.id as sequence_id, next_seq.subject_a, next_seq.subject_b,
                   next_seq.body_a, next_seq.body_b, next_seq.step
            FROM contacts c
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN sent_emails se ON se.contact_id = c.id
            JOIN email_sequences last_seq ON se.sequence_id = last_seq.id
            JOIN email_sequences next_seq ON next_seq.campaign_id = camp.id
                                          AND next_seq.step = last_seq.step + 1
            WHERE camp.status = 'active'
              AND c.status NOT IN ('replied', 'bounced', 'unsubscribed')
              AND se.status NOT IN ('replied', 'bounced')
              AND (camp.scheduled_start IS NULL OR camp.scheduled_start <= datetime('now', 'localtime'))
              -- Enough days have passed since the last email
              AND julianday('now', 'localtime') - julianday(se.sent_at) >= next_seq.delay_days
              -- Haven't already sent this next step
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se2
                  WHERE se2.contact_id = c.id AND se2.sequence_id = next_seq.id
              )
              -- This sent_email is the latest step for this contact
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se3
                  JOIN email_sequences es3 ON se3.sequence_id = es3.id
                  WHERE se3.contact_id = c.id AND es3.step > last_seq.step
              )
            LIMIT ?
        """, (remaining,)).fetchall()
        results.extend(dict(r) for r in followup_rows)

        return results


def record_reply(contact_email: str, reply_body: str = "", reply_sentiment: str = "") -> bool:
    """Mark a contact as replied based on their email address.
    Returns True if a matching contact was found and updated."""
    with get_db() as db:
        row = db.execute(
            "SELECT c.id as contact_id, se.id as sent_id "
            "FROM contacts c "
            "JOIN sent_emails se ON se.contact_id = c.id "
            "WHERE LOWER(c.email) = LOWER(?) AND c.status != 'replied' "
            "ORDER BY se.sent_at DESC LIMIT 1",
            (contact_email,),
        ).fetchone()
        if not row:
            return False
        db.execute(
            "UPDATE contacts SET status = 'replied' WHERE id = ?",
            (row["contact_id"],),
        )
        db.execute(
            "UPDATE sent_emails SET status = 'replied', replied_at = datetime('now', 'localtime'), "
            "reply_body = ?, reply_sentiment = ? WHERE id = ?",
            (reply_body, reply_sentiment, row["sent_id"]),
        )
        return True


def get_all_sent_recipient_emails() -> set[str]:
    """Return the set of all email addresses we've sent to (for IMAP reply matching)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT DISTINCT LOWER(c.email) as email FROM contacts c "
            "JOIN sent_emails se ON se.contact_id = c.id "
            "WHERE c.status != 'replied'"
        ).fetchall()
        return {r["email"] for r in rows}


def delete_campaign(campaign_id: int):
    """Delete a campaign and all related data (cascade)."""
    with get_db() as db:
        # Delete sent_emails first (no ON DELETE CASCADE on its FKs)
        db.execute("""DELETE FROM sent_emails WHERE contact_id IN
                      (SELECT id FROM contacts WHERE campaign_id = ?)""", (campaign_id,))
        db.execute("""DELETE FROM sent_emails WHERE sequence_id IN
                      (SELECT id FROM email_sequences WHERE campaign_id = ?)""", (campaign_id,))
        db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


def update_campaign_status(campaign_id: int, status: str):
    with get_db() as db:
        db.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))


def delete_contact(contact_id: int):
    with get_db() as db:
        db.execute("DELETE FROM sent_emails WHERE contact_id = ?", (contact_id,))
        db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


def get_sent_emails(campaign_id: int) -> list[dict]:
    """Get all sent emails for a campaign with contact info."""
    with get_db() as db:
        rows = db.execute("""
            SELECT se.*, c.name as contact_name, c.email as contact_email,
                   c.company as contact_company, es.step
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE c.campaign_id = ?
            ORDER BY se.sent_at DESC
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rows]


def get_global_stats(client_id: int) -> dict:
    """Aggregate stats across all campaigns for a client."""
    with get_db() as db:
        total_camps = db.execute(
            "SELECT COUNT(*) FROM campaigns WHERE client_id = ?", (client_id,)
        ).fetchone()[0]
        active_camps = db.execute(
            "SELECT COUNT(*) FROM campaigns WHERE client_id = ? AND status = 'active'",
            (client_id,),
        ).fetchone()[0]
        total_contacts = db.execute(
            "SELECT COUNT(*) FROM contacts c JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = ?", (client_id,)
        ).fetchone()[0]
        total_sent = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id WHERE camp.client_id = ?",
            (client_id,),
        ).fetchone()[0]
        total_opened = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = ? AND se.status IN ('opened', 'clicked', 'replied')",
            (client_id,),
        ).fetchone()[0]
        total_replied = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = ? AND se.status = 'replied'",
            (client_id,),
        ).fetchone()[0]
        total_bounced = db.execute(
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = ? AND se.status = 'bounced'",
            (client_id,),
        ).fetchone()[0]
        return {
            "total_campaigns": total_camps,
            "active_campaigns": active_camps,
            "total_contacts": total_contacts,
            "total_sent": total_sent,
            "total_opened": total_opened,
            "open_rate": total_opened / total_sent if total_sent else 0,
            "total_replied": total_replied,
            "reply_rate": total_replied / total_sent if total_sent else 0,
            "total_bounced": total_bounced,
            "bounce_rate": total_bounced / total_sent if total_sent else 0,
        }


def get_daily_analytics(client_id: int, days: int = 30) -> list[dict]:
    """Return daily sent/opened/replied/bounced counts for the last N days."""
    with get_db() as db:
        rows = db.execute(
            "SELECT DATE(se.sent_at) AS day, "
            "  COUNT(*) AS sent, "
            "  SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) AS opened, "
            "  SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) AS replied, "
            "  SUM(CASE WHEN se.status = 'bounced' THEN 1 ELSE 0 END) AS bounced "
            "FROM sent_emails se "
            "JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = ? AND se.sent_at >= DATE('now', 'localtime', ?) "
            "GROUP BY day ORDER BY day",
            (client_id, f"-{days} days"),
        ).fetchall()
        return [dict(r) for r in rows]


def duplicate_campaign(campaign_id: int, client_id: int) -> int | None:
    """Duplicate a campaign with its sequences (no contacts/sent data)."""
    with get_db() as db:
        camp = db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not camp:
            return None
        cur = db.execute(
            "INSERT INTO campaigns (client_id, name, business_type, target_audience, tone, status) "
            "VALUES (?, ?, ?, ?, ?, 'draft')",
            (client_id, camp["name"] + " (copy)", camp["business_type"],
             camp["target_audience"], camp["tone"]),
        )
        new_id = cur.lastrowid
        seqs = db.execute(
            "SELECT * FROM email_sequences WHERE campaign_id = ? ORDER BY step",
            (campaign_id,),
        ).fetchall()
        for s in seqs:
            db.execute(
                "INSERT INTO email_sequences (campaign_id, step, subject_a, subject_b, body_a, body_b, delay_days) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, s["step"], s["subject_a"], s["subject_b"],
                 s["body_a"], s["body_b"], s["delay_days"]),
            )
        return new_id


def update_client(client_id: int, name: str, business: str):
    with get_db() as db:
        db.execute("UPDATE clients SET name = ?, business = ? WHERE id = ?",
                   (name, business, client_id))


def update_mail_preferences(client_id: int, preferences: str):
    with get_db() as db:
        db.execute("UPDATE clients SET mail_preferences = ? WHERE id = ?",
                   (preferences, client_id))


def get_mail_preferences(client_id: int) -> str:
    with get_db() as db:
        row = db.execute("SELECT mail_preferences FROM clients WHERE id = ?",
                         (client_id,)).fetchone()
        return (row[0] or "") if row else ""


def update_mail_exclusions(client_id: int, exclusions: str):
    with get_db() as db:
        db.execute("UPDATE clients SET mail_exclusions = ? WHERE id = ?",
                   (exclusions, client_id))


def get_mail_exclusions(client_id: int) -> str:
    with get_db() as db:
        row = db.execute("SELECT mail_exclusions FROM clients WHERE id = ?",
                         (client_id,)).fetchone()
        return (row[0] or "") if row else ""


def update_client_password(client_id: int, password_hash: str):
    with get_db() as db:
        db.execute("UPDATE clients SET password = ? WHERE id = ?",
                   (password_hash, client_id))


def create_reset_token(client_id: int, token: str, expires_at: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO password_reset_tokens (client_id, token, expires_at) VALUES (?, ?, ?)",
            (client_id, token, expires_at),
        )


def get_valid_reset_token(token: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ? AND used = 0 AND expires_at > datetime('now', 'localtime')",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def mark_reset_token_used(token: str):
    with get_db() as db:
        db.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))


def get_client(client_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return dict(row) if row else None


def get_all_client_emails() -> list[dict]:
    """Return id, name, email for all registered clients (admin broadcast)."""
    with get_db() as db:
        rows = db.execute("SELECT id, name, email FROM clients ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def update_sequence(seq_id: int, subject_a: str, subject_b: str,
                    body_a: str, delay_days: int):
    with get_db() as db:
        db.execute(
            "UPDATE email_sequences SET subject_a=?, subject_b=?, body_a=?, delay_days=? "
            "WHERE id=?",
            (subject_a, subject_b, body_a, delay_days, seq_id),
        )


def delete_sequence(seq_id: int):
    with get_db() as db:
        db.execute("DELETE FROM email_sequences WHERE id = ?", (seq_id,))

def get_replies(client_id: int) -> list[dict]:
    """Get all replied contacts with their email thread for the inbox."""
    with get_db() as db:
        rows = db.execute("""
            SELECT se.id as sent_id, se.subject, se.body, se.variant,
                   se.sent_at, se.replied_at, se.status as email_status,
                   c.id as contact_id, c.name as contact_name, c.email as contact_email,
                   c.company, c.role, c.status as contact_status,
                   camp.id as campaign_id, camp.name as campaign_name,
                   es.step
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE camp.client_id = ?
              AND se.status = 'replied'
            ORDER BY se.replied_at DESC
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]


def get_inbox_all(client_id: int) -> list[dict]:
    """Get all sent emails grouped by contact for the inbox view."""
    with get_db() as db:
        rows = db.execute("""
            SELECT se.id as sent_id, se.subject, se.body, se.variant,
                   se.sent_at, se.opened_at, se.replied_at,
                   se.status as email_status,
                   se.reply_body, se.reply_sentiment,
                   c.id as contact_id, c.name as contact_name,
                   c.email as contact_email, c.company, c.role,
                   c.status as contact_status,
                   camp.id as campaign_id, camp.name as campaign_name,
                   es.step
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE camp.client_id = ?
            ORDER BY se.sent_at DESC
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]

def get_ab_stats(client_id: int) -> list[dict]:
    """Get A/B test performance for all sequences across all campaigns."""
    with get_db() as db:
        rows = db.execute("""
            SELECT es.id as seq_id, es.step, es.subject_a, es.subject_b,
                   camp.id as campaign_id, camp.name as campaign_name,
                   se.variant,
                   COUNT(se.id) as sent_count,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) as opens,
                   SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) as replies
            FROM email_sequences es
            JOIN campaigns camp ON es.campaign_id = camp.id
            LEFT JOIN sent_emails se ON se.sequence_id = es.id
            WHERE camp.client_id = ?
              AND es.subject_b IS NOT NULL AND es.subject_b != ''
            GROUP BY es.id, se.variant
            ORDER BY camp.name, es.step, se.variant
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]


def get_send_time_stats(client_id: int) -> list[dict]:
    """Get open rates by hour-of-day and day-of-week."""
    with get_db() as db:
        rows = db.execute("""
            SELECT strftime('%w', se.sent_at) as dow,
                   strftime('%H', se.sent_at) as hour,
                   COUNT(*) as total,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) as opens
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE camp.client_id = ?
            GROUP BY dow, hour
            ORDER BY dow, hour
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]
    
def get_calendar_events(client_id: int) -> list[dict]:
    """Get all sent and pending emails for the calendar view."""
    with get_db() as db:
        # Already sent
        sent = db.execute("""
            SELECT se.sent_at as date, c.name as contact_name, c.email as contact_email,
                   se.subject, se.status as email_status, se.variant,
                   camp.name as campaign_name, camp.id as campaign_id,
                   es.step, 'sent' as event_type
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE camp.client_id = ?
            ORDER BY se.sent_at DESC
        """, (client_id,)).fetchall()

        # Pending (contacts waiting for follow-ups)
        pending = db.execute("""
            SELECT date(se.sent_at, '+' || next_seq.delay_days || ' days') as date,
                   c.name as contact_name, c.email as contact_email,
                   next_seq.subject_a as subject,
                   'pending' as email_status, '' as variant,
                   camp.name as campaign_name, camp.id as campaign_id,
                   next_seq.step, 'scheduled' as event_type
            FROM contacts c
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN sent_emails se ON se.contact_id = c.id
            JOIN email_sequences last_seq ON se.sequence_id = last_seq.id
            JOIN email_sequences next_seq ON next_seq.campaign_id = camp.id
                                          AND next_seq.step = last_seq.step + 1
            WHERE camp.client_id = ?
              AND camp.status = 'active'
              AND c.status NOT IN ('replied', 'bounced', 'unsubscribed')
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se2
                  WHERE se2.contact_id = c.id AND se2.sequence_id = next_seq.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se3
                  JOIN email_sequences es3 ON se3.sequence_id = es3.id
                  WHERE se3.contact_id = c.id AND es3.step > last_seq.step
              )
        """, (client_id,)).fetchall()

        events = [dict(r) for r in sent] + [dict(r) for r in pending]
        return events


def get_reply_context(sent_email_id: int) -> dict | None:
    """Get the full thread context for a replied email (for AI reply drafts)."""
    with get_db() as db:
        row = db.execute("""
            SELECT se.subject, se.body, se.reply_body, se.reply_sentiment,
                   c.name as contact_name, c.email as contact_email,
                   c.company, c.role,
                   camp.business_type, camp.target_audience, camp.tone
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE se.id = ? AND se.status = 'replied'
        """, (sent_email_id,)).fetchone()
        return dict(row) if row else None


def get_export_data(client_id: int, campaign_id: int | None = None) -> list[dict]:
    """Get all email data for CSV export."""
    with get_db() as db:
        query = """
            SELECT camp.name as campaign_name,
                   c.name as contact_name, c.email as contact_email,
                   c.company, c.role, c.status as contact_status,
                   se.subject, se.variant, se.status as email_status,
                   se.sent_at, se.opened_at, se.replied_at,
                   se.reply_body, se.reply_sentiment,
                   es.step
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE camp.client_id = ?
        """
        params = [client_id]
        if campaign_id:
            query += " AND camp.id = ?"
            params.append(campaign_id)
        query += " ORDER BY camp.name, se.sent_at DESC"
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    
def upsert_mail(client_id: int, message_id: str, from_name: str, from_email: str,
                to_email: str, subject: str, body_preview: str, received_at: str,
                priority: str = "normal", category: str = "uncategorized",
                ai_summary: str = "", account_id: int | None = None,
                is_read: int = 0) -> bool:
    """Insert or ignore a mail message. Returns True if new."""
    with get_db() as db:
        try:
            db.execute("""
                INSERT INTO mail_inbox
                    (client_id, message_id, from_name, from_email, to_email,
                     subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (client_id, message_id, from_name, from_email, to_email,
                  subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read))
            return True
        except Exception:
            return False


def get_mail_inbox(client_id: int, filter_by: str = "all",
                   category: str | None = None, account_id: int | None = None,
                   sender: str | None = None,
                   limit: int = 100) -> list[dict]:
    """Get mail inbox items with optional filtering."""
    with get_db() as db:
        conditions = ["m.client_id = ?", "m.is_archived = 0"]
        params: list = [client_id]

        # Handle snoozed items
        conditions.append("(m.snooze_until IS NULL OR m.snooze_until <= datetime('now', 'localtime'))")

        if filter_by == "all":
            conditions.append("m.is_read = 0")
        elif filter_by == "unread":
            conditions.append("m.is_read = 0")
        elif filter_by == "read":
            conditions.append("m.is_read = 1")
        elif filter_by == "starred":
            conditions.append("m.is_starred = 1")
        elif filter_by == "urgent":
            conditions.append("m.priority IN ('urgent', 'important')")
        elif filter_by == "snoozed":
            # Override — show snoozed items regardless
            conditions = ["m.client_id = ?", "m.snooze_until IS NOT NULL AND m.snooze_until > datetime('now', 'localtime')"]
            params = [client_id]

        if category and category != "all":
            conditions.append("m.category = ?")
            params.append(category)

        if account_id is not None:
            conditions.append("m.account_id = ?")
            params.append(account_id)

        if sender:
            conditions.append("LOWER(m.from_email) = ?")
            params.append(sender.lower())

        where = " AND ".join(conditions)
        rows = db.execute(f"""
            SELECT * FROM mail_inbox m
            WHERE {where}
            ORDER BY
                CASE m.priority
                    WHEN 'urgent' THEN 1
                    WHEN 'important' THEN 2
                    ELSE 3
                END,
                CASE WHEN m.category = 'personal' THEN 0 ELSE 1 END,
                CASE m.priority
                    WHEN 'normal' THEN 1
                    WHEN 'low' THEN 2
                    ELSE 0
                END,
                m.received_at DESC
            LIMIT ?
        """, params + [limit]).fetchall()
        return [dict(r) for r in rows]


def get_mail_stats(client_id: int) -> dict:
    """Get counts for the mail hub sidebar."""
    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND is_archived = 0",
            (client_id,)).fetchone()[0]
        unread = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND is_archived = 0 AND is_read = 0",
            (client_id,)).fetchone()[0]
        starred = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND is_starred = 1 AND is_archived = 0",
            (client_id,)).fetchone()[0]
        urgent = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND is_archived = 0 AND priority IN ('urgent','important')",
            (client_id,)).fetchone()[0]
        read = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND is_archived = 0 AND is_read = 1",
            (client_id,)).fetchone()[0]
        snoozed = db.execute(
            "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND snooze_until IS NOT NULL AND snooze_until > datetime('now', 'localtime')",
            (client_id,)).fetchone()[0]

        # Category counts
        cat_rows = db.execute("""
            SELECT category, COUNT(*) as cnt FROM mail_inbox
            WHERE client_id = ? AND is_archived = 0
            GROUP BY category
        """, (client_id,)).fetchall()
        categories = {r["category"]: r["cnt"] for r in cat_rows}

        # Priority counts
        pri_rows = db.execute("""
            SELECT priority, COUNT(*) as cnt FROM mail_inbox
            WHERE client_id = ? AND is_archived = 0
            GROUP BY priority
        """, (client_id,)).fetchall()
        priorities = {r["priority"]: r["cnt"] for r in pri_rows}

        return {
            "total": total, "unread": unread, "read": read, "starred": starred,
            "urgent": urgent, "snoozed": snoozed,
            "categories": categories, "priorities": priorities,
        }


def get_top_senders(client_id: int, limit: int = 10) -> list[dict]:
    """Get saved contacts that appear in the Mail Hub inbox for the sidebar filter."""
    with get_db() as db:
        rows = db.execute("""
            SELECT cb.email, cb.name, COUNT(m.id) as cnt
            FROM contacts_book cb
            JOIN mail_inbox m ON LOWER(m.from_email) = LOWER(cb.email) AND m.client_id = cb.client_id
            WHERE cb.client_id = ? AND m.is_archived = 0
            GROUP BY LOWER(cb.email)
            ORDER BY cnt DESC
            LIMIT ?
        """, (client_id, limit)).fetchall()
        return [{"email": r["email"], "name": r["name"] or r["email"].split("@")[0], "count": r["cnt"]} for r in rows]


def update_mail_field(mail_id: int, client_id: int, field: str, value) -> bool:
    """Update a single field on a mail item (safe fields only)."""
    allowed = {"is_read", "is_starred", "is_archived", "snooze_until", "snooze_note", "priority", "category"}
    if field not in allowed:
        return False
    with get_db() as db:
        db.execute(
            f"UPDATE mail_inbox SET {field} = ? WHERE id = ? AND client_id = ?",
            (value, mail_id, client_id),
        )
        return True


def get_mail_item(mail_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM mail_inbox WHERE id = ? AND client_id = ?",
            (mail_id, client_id)).fetchone()
        return dict(row) if row else None

def upsert_contact(client_id: int, email: str, name: str = "", company: str = "",
                   role: str = "", relationship: str = "", notes: str = "",
                   personality: str = "", tags: str = "", language: str = "") -> int:
    with get_db() as db:
        cur = db.execute("""
            INSERT INTO contacts_book (client_id, email, name, company, role, relationship, notes, personality, tags, language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, email) DO UPDATE SET
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE contacts_book.name END,
                company = CASE WHEN excluded.company != '' THEN excluded.company ELSE contacts_book.company END,
                role = CASE WHEN excluded.role != '' THEN excluded.role ELSE contacts_book.role END,
                relationship = CASE WHEN excluded.relationship != '' THEN excluded.relationship ELSE contacts_book.relationship END,
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE contacts_book.notes END,
                personality = CASE WHEN excluded.personality != '' THEN excluded.personality ELSE contacts_book.personality END,
                tags = CASE WHEN excluded.tags != '' THEN excluded.tags ELSE contacts_book.tags END,
                language = CASE WHEN excluded.language != '' THEN excluded.language ELSE contacts_book.language END
        """, (client_id, email, name, company, role, relationship, notes, personality, tags, language))
        return cur.lastrowid


def get_contacts(client_id: int, search: str = "", tag: str = "",
                 relationship: str = "") -> list[dict]:
    with get_db() as db:
        sql = "SELECT * FROM contacts_book WHERE client_id = ?"
        params: list = [client_id]
        if search:
            sql += " AND (name LIKE ? OR email LIKE ? OR company LIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s])
        if tag:
            sql += " AND (',' || tags || ',') LIKE ?"
            params.append(f"%,{tag},%")
        if relationship:
            sql += " AND relationship = ?"
            params.append(relationship)
        sql += " ORDER BY last_contacted DESC, name ASC"
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_contact(contact_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM contacts_book WHERE id = ? AND client_id = ?",
            (contact_id, client_id)).fetchone()
        return dict(row) if row else None


def get_contact_by_email(client_id: int, email: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM contacts_book WHERE client_id = ? AND email = ?",
            (client_id, email)).fetchone()
        return dict(row) if row else None


def update_contact(contact_id: int, client_id: int, **fields) -> bool:
    allowed = {"name", "company", "role", "relationship", "notes", "personality",
               "tags", "last_contacted", "language"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [contact_id, client_id]
    with get_db() as db:
        db.execute(f"UPDATE contacts_book SET {set_clause} WHERE id = ? AND client_id = ?", vals)
        return True


def delete_contact_book(contact_id: int, client_id: int) -> bool:
    with get_db() as db:
        db.execute("DELETE FROM contacts_book WHERE id = ? AND client_id = ?",
                   (contact_id, client_id))
        return True


def get_contact_email_history(client_id: int, email: str, limit: int = 20) -> list[dict]:
    """Get recent emails from mail_inbox for a given contact email."""
    with get_db() as db:
        return [dict(r) for r in db.execute("""
            SELECT id, subject, body_preview, received_at, priority, category, ai_summary
            FROM mail_inbox WHERE client_id = ? AND from_email = ?
            ORDER BY received_at DESC LIMIT ?
        """, (client_id, email, limit)).fetchall()]


def mark_contact_emails_priority(client_id: int, email: str, priority: str) -> int:
    """Set priority on all mail_inbox emails from a given sender."""
    allowed = {"urgent", "important", "normal", "low"}
    if priority not in allowed:
        return 0
    with get_db() as db:
        cur = db.execute(
            "UPDATE mail_inbox SET priority = ? WHERE client_id = ? AND from_email = ?",
            (priority, client_id, email),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_mail_inbox(client_id: int, query: str, limit: int = 50) -> list[dict]:
    """Full-text search across subject, body, sender."""
    with get_db() as db:
        like = f"%{query}%"
        rows = db.execute("""
            SELECT * FROM mail_inbox
            WHERE client_id = ? AND is_archived = 0
              AND (subject LIKE ? OR body_preview LIKE ? OR from_name LIKE ? OR from_email LIKE ?)
            ORDER BY received_at DESC
            LIMIT ?
        """, (client_id, like, like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

def bulk_update_mail(mail_ids: list[int], client_id: int, field: str, value) -> int:
    """Bulk update a field on multiple mail items."""
    allowed = {"is_read", "is_starred", "is_archived", "priority", "category"}
    if field not in allowed or not mail_ids:
        return 0
    placeholders = ",".join("?" * len(mail_ids))
    with get_db() as db:
        cur = db.execute(
            f"UPDATE mail_inbox SET {field} = ? WHERE id IN ({placeholders}) AND client_id = ?",
            [value] + mail_ids + [client_id],
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------

def invite_team_member(owner_id: int, member_email: str, role: str = "member") -> dict:
    """Invite a team member. Returns the invite record or error."""
    import secrets
    token = secrets.token_urlsafe(32)
    with get_db() as db:
        # Check if already invited
        existing = db.execute(
            "SELECT id, status FROM team_members WHERE owner_id = ? AND member_email = ?",
            (owner_id, member_email),
        ).fetchone()
        if existing:
            return {"error": "Already invited", "status": dict(existing)["status"]}
        db.execute(
            "INSERT INTO team_members (owner_id, member_email, role, invite_token) VALUES (?, ?, ?, ?)",
            (owner_id, member_email, role, token),
        )
        return {"token": token, "email": member_email, "role": role}


def get_team_members(owner_id: int) -> list[dict]:
    """Get all team members for a workspace owner."""
    with get_db() as db:
        rows = db.execute(
            """SELECT tm.*, c.name as member_name
               FROM team_members tm
               LEFT JOIN clients c ON c.id = tm.member_client_id
               WHERE tm.owner_id = ?
               ORDER BY tm.invited_at DESC""",
            (owner_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def accept_team_invite(token: str, client_id: int) -> dict | None:
    """Accept a team invite using the token. Links the member_client_id."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM team_members WHERE invite_token = ? AND status = 'pending'",
            (token,),
        ).fetchone()
        if not row:
            return None
        db.execute(
            """UPDATE team_members
               SET status = 'active', member_client_id = ?, accepted_at = datetime('now', 'localtime'), invite_token = NULL
               WHERE id = ?""",
            (client_id, row["id"]),
        )
        return dict(row)


def remove_team_member(member_id: int, owner_id: int) -> bool:
    """Remove a team member (only the owner can)."""
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM team_members WHERE id = ? AND owner_id = ?",
            (member_id, owner_id),
        )
        return cur.rowcount > 0


def get_team_owner(client_id: int) -> int | None:
    """If client_id is a team member, return the owner's client_id. Otherwise None."""
    with get_db() as db:
        row = db.execute(
            "SELECT owner_id FROM team_members WHERE member_client_id = ? AND status = 'active'",
            (client_id,),
        ).fetchone()
        return row["owner_id"] if row else None


# ---------------------------------------------------------------------------
# Scheduled emails
# ---------------------------------------------------------------------------

def create_scheduled_email(client_id: int, to_email: str, subject: str, body: str,
                           scheduled_at: str, to_name: str = "",
                           reply_to_mail_id: int | None = None,
                           account_id: int | None = None) -> int:
    with get_db() as db:
        cur = db.execute("""
            INSERT INTO scheduled_emails (client_id, to_email, to_name, subject, body,
                                          scheduled_at, reply_to_mail_id, account_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (client_id, to_email, to_name, subject, body, scheduled_at, reply_to_mail_id, account_id))
        return cur.lastrowid


def get_scheduled_emails(client_id: int, status: str | None = None) -> list[dict]:
    with get_db() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM scheduled_emails WHERE client_id = ? AND status = ? ORDER BY scheduled_at ASC",
                (client_id, status),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM scheduled_emails WHERE client_id = ? ORDER BY scheduled_at DESC",
                (client_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_scheduled_email(email_id: int, client_id: int) -> bool:
    with get_db() as db:
        db.execute(
            "DELETE FROM scheduled_emails WHERE id = ? AND client_id = ? AND status = 'pending'",
            (email_id, client_id),
        )
        return True


def get_due_scheduled_emails() -> list[dict]:
    """Get all pending scheduled emails that are due (scheduled_at <= now)."""
    with get_db() as db:
        rows = db.execute("""
            SELECT * FROM scheduled_emails
            WHERE status = 'pending' AND scheduled_at <= datetime('now', 'localtime')
            ORDER BY scheduled_at ASC
        """).fetchall()
        return [dict(r) for r in rows]


def mark_scheduled_sent(email_id: int) -> bool:
    with get_db() as db:
        db.execute(
            "UPDATE scheduled_emails SET status = 'sent', sent_at = datetime('now', 'localtime') WHERE id = ?",
            (email_id,),
        )
        return True


def mark_scheduled_failed(email_id: int) -> bool:
    with get_db() as db:
        db.execute(
            "UPDATE scheduled_emails SET status = 'failed' WHERE id = ?",
            (email_id,),
        )
        return True


# ---------------------------------------------------------------------------
# Snooze processing
# ---------------------------------------------------------------------------

def process_snoozed_emails() -> int:
    """Bump resurfaced snoozed emails to 'important' priority. Returns count updated."""
    with get_db() as db:
        cur = db.execute("""
            UPDATE mail_inbox
            SET priority = 'important'
            WHERE snooze_until IS NOT NULL
              AND snooze_until <= datetime('now', 'localtime')
              AND priority NOT IN ('urgent', 'important')
        """)
        return cur.rowcount


# ---------------------------------------------------------------------------
# Billing & Usage
# ---------------------------------------------------------------------------

def get_subscription(client_id: int) -> dict:
    """Get or create subscription for a client. Defaults to free plan."""
    with get_db() as db:
        row = db.execute("SELECT * FROM subscriptions WHERE client_id = ?",
                         (client_id,)).fetchone()
        if row:
            return dict(row)
        db.execute("INSERT INTO subscriptions (client_id, plan) VALUES (?, 'free')",
                   (client_id,))
        db.commit()
        row = db.execute("SELECT * FROM subscriptions WHERE client_id = ?",
                         (client_id,)).fetchone()
        return dict(row)


def update_subscription(client_id: int, **fields) -> bool:
    allowed = {"plan", "stripe_customer_id", "stripe_subscription_id", "status",
               "current_period_start", "current_period_end"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = "datetime('now', 'localtime')"
    set_parts = []
    vals = []
    for k, v in updates.items():
        if v == "datetime('now', 'localtime')":
            set_parts.append(f"{k} = datetime('now', 'localtime')")
        else:
            set_parts.append(f"{k} = ?")
            vals.append(v)
    vals.append(client_id)
    with get_db() as db:
        db.execute(f"UPDATE subscriptions SET {', '.join(set_parts)} WHERE client_id = ?", vals)
        return True


def get_subscription_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM subscriptions WHERE stripe_customer_id = ?",
                         (stripe_customer_id,)).fetchone()
        return dict(row) if row else None


def get_subscription_by_stripe_sub(stripe_sub_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?",
                         (stripe_sub_id,)).fetchone()
        return dict(row) if row else None


def _current_month() -> str:
    from datetime import date
    return date.today().strftime("%Y-%m")


def get_usage(client_id: int) -> dict:
    """Get current month usage. Creates record if missing."""
    month = _current_month()
    with get_db() as db:
        row = db.execute("SELECT * FROM usage_tracking WHERE client_id = ? AND month = ?",
                         (client_id, month)).fetchone()
        if row:
            return dict(row)
        db.execute("INSERT INTO usage_tracking (client_id, month) VALUES (?, ?)",
                   (client_id, month))
        db.commit()
        row = db.execute("SELECT * FROM usage_tracking WHERE client_id = ? AND month = ?",
                         (client_id, month)).fetchone()
        return dict(row)


def increment_usage(client_id: int, field: str, amount: int = 1) -> int:
    """Increment a usage counter. Returns new value."""
    allowed = {"emails_sent", "mail_hub_syncs", "ai_classifications"}
    if field not in allowed:
        return 0
    month = _current_month()
    with get_db() as db:
        db.execute(f"""
            INSERT INTO usage_tracking (client_id, month, {field})
            VALUES (?, ?, ?)
            ON CONFLICT(client_id, month) DO UPDATE SET {field} = {field} + ?
        """, (client_id, month, amount, amount))
        db.commit()
        row = db.execute(f"SELECT {field} FROM usage_tracking WHERE client_id = ? AND month = ?",
                         (client_id, month)).fetchone()
        return row[0] if row else 0


def check_limit(client_id: int, field: str) -> tuple[bool, int, int]:
    """Check if a client has remaining quota for a field.
    Returns (allowed, used, limit). limit=-1 means unlimited."""
    from outreach.config import PLAN_LIMITS
    sub = get_subscription(client_id)
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    usage = get_usage(client_id)

    limit_map = {"emails_sent": "emails_per_month", "mail_hub_syncs": "mail_hub_syncs"}
    limit_key = limit_map.get(field, field)
    max_val = limits.get(limit_key, 0)
    used = usage.get(field, 0)

    if max_val == -1:
        return True, used, -1
    return used < max_val, used, max_val


if __name__ == "__main__":
    init_db()
