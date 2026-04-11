"""
PostgreSQL database — campaigns, contacts, emails, tracking.
Migrated from SQLite.  Uses DATABASE_URL env var (Render Postgres format).
Falls back to SQLite via DATABASE_PATH when DATABASE_URL is empty (local dev).
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken
from outreach.config import DATABASE_URL, ENCRYPTION_KEY

# ---------------------------------------------------------------------------
# Detect engine: postgres vs sqlite fallback
# ---------------------------------------------------------------------------
_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
else:
    import sqlite3
    from pathlib import Path
    from outreach.config import DATABASE_PATH

# ---------------------------------------------------------------------------
# Fernet encryption for email account passwords at rest
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    import base64, hashlib
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ciphertext


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _db_fingerprint() -> str:
    """Short hash of DATABASE_URL for comparing web vs worker connections."""
    import hashlib
    if _USE_PG and DATABASE_URL:
        return hashlib.sha256(DATABASE_URL.encode()).hexdigest()[:12]
    return "sqlite"


@contextmanager
def get_db():
    if _USE_PG:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _exec(db, sql, params=()):
    """Execute helper — converts %s back to ? for SQLite if needed."""
    if not _USE_PG:
        sql = sql.replace("%s", "?")
    cur = db.cursor()
    cur.execute(sql, params)
    return cur


def _fetchone(db, sql, params=()):
    cur = _exec(db, sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _fetchall(db, sql, params=()):
    cur = _exec(db, sql, params)
    return [dict(r) for r in cur.fetchall()]


def _fetchval(db, sql, params=()):
    """Fetch a single scalar value."""
    cur = _exec(db, sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    if _USE_PG:
        # RealDictRow — get first value
        return list(row.values())[0]
    else:
        return row[0]


def _insert_returning_id(db, sql_pg, params, sql_sqlite=None):
    """Insert and return the new row id.
    sql_pg must end with RETURNING id.
    sql_sqlite is the INSERT without RETURNING (uses lastrowid)."""
    if _USE_PG:
        cur = db.cursor()
        cur.execute(sql_pg, params)
        return cur.fetchone()["id"]
    else:
        sql = (sql_sqlite or sql_pg.rsplit("RETURNING", 1)[0]).replace("%s", "?")
        cur = db.cursor()
        cur.execute(sql, params)
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Schema / Migrations
# ---------------------------------------------------------------------------

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    business    TEXT DEFAULT '',
    physical_address TEXT DEFAULT '',
    mail_preferences TEXT DEFAULT '',
    mail_exclusions TEXT DEFAULT '',
    is_admin    INTEGER DEFAULT 0,
    email_verified INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    token       TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMP NOT NULL,
    used        INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    token       TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMP NOT NULL,
    used        INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    name        TEXT NOT NULL,
    business_type TEXT DEFAULT '',
    target_audience TEXT DEFAULT '',
    tone        TEXT DEFAULT 'professional',
    status      TEXT DEFAULT 'draft',
    scheduled_start TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_client ON campaigns(client_id);

CREATE TABLE IF NOT EXISTS contacts (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name        TEXT DEFAULT '',
    email       TEXT NOT NULL,
    company     TEXT DEFAULT '',
    role        TEXT DEFAULT '',
    language    TEXT DEFAULT 'en',
    custom_data TEXT DEFAULT '{}',
    status      TEXT DEFAULT 'pending',
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_sequences (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    step        INTEGER NOT NULL DEFAULT 1,
    subject_a   TEXT NOT NULL,
    subject_b   TEXT DEFAULT '',
    body_a      TEXT NOT NULL,
    body_b      TEXT DEFAULT '',
    delay_days  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sent_emails (
    id          SERIAL PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id),
    sequence_id INTEGER NOT NULL REFERENCES email_sequences(id),
    variant     TEXT DEFAULT 'a',
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT DEFAULT 'sent',
    sent_at     TIMESTAMP DEFAULT NOW(),
    opened_at   TIMESTAMP,
    replied_at  TIMESTAMP,
    reply_body  TEXT DEFAULT '',
    reply_sentiment TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_sent_contact ON sent_emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_sent_sequence ON sent_emails(sequence_id);

CREATE TABLE IF NOT EXISTS mail_inbox (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    message_id   TEXT NOT NULL,
    from_name    TEXT DEFAULT '',
    from_email   TEXT NOT NULL,
    to_email     TEXT DEFAULT '',
    subject      TEXT DEFAULT '',
    body_preview TEXT DEFAULT '',
    received_at  TEXT DEFAULT '',
    priority     TEXT DEFAULT 'normal',
    category     TEXT DEFAULT 'uncategorized',
    is_read      INTEGER DEFAULT 0,
    is_starred   INTEGER DEFAULT 0,
    is_archived  INTEGER DEFAULT 0,
    snooze_until TEXT,
    snooze_note  TEXT DEFAULT '',
    ai_summary   TEXT DEFAULT '',
    account_id   INTEGER,
    fetched_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_inbox_client ON mail_inbox(client_id);
CREATE INDEX IF NOT EXISTS idx_mail_inbox_priority ON mail_inbox(client_id, priority);
CREATE INDEX IF NOT EXISTS idx_mail_inbox_category ON mail_inbox(client_id, category);

CREATE TABLE IF NOT EXISTS email_accounts (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    label        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL,
    imap_host    TEXT NOT NULL DEFAULT 'imap.gmail.com',
    imap_port    INTEGER NOT NULL DEFAULT 993,
    smtp_host    TEXT NOT NULL DEFAULT 'smtp.gmail.com',
    smtp_port    INTEGER NOT NULL DEFAULT 465,
    password     TEXT NOT NULL DEFAULT '',
    is_default   INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_email_accounts_client ON email_accounts(client_id);

CREATE TABLE IF NOT EXISTS contacts_book (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    email           TEXT NOT NULL,
    name            TEXT DEFAULT '',
    company         TEXT DEFAULT '',
    role            TEXT DEFAULT '',
    relationship    TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    personality     TEXT DEFAULT '',
    tags            TEXT DEFAULT '',
    language        TEXT DEFAULT '',
    last_contacted  TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_contacts_book_client ON contacts_book(client_id);
CREATE INDEX IF NOT EXISTS idx_contacts_book_email ON contacts_book(client_id, email);

CREATE TABLE IF NOT EXISTS scheduled_emails (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    to_email        TEXT NOT NULL,
    to_name         TEXT DEFAULT '',
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    scheduled_at    TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    sent_at         TIMESTAMP,
    reply_to_mail_id INTEGER,
    account_id      INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_client ON scheduled_emails(client_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_emails(status, scheduled_at);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL UNIQUE REFERENCES clients(id),
    plan            TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id   TEXT DEFAULT '',
    stripe_subscription_id TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',
    current_period_start TEXT DEFAULT '',
    current_period_end   TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usage_tracking (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    month           TEXT NOT NULL,
    emails_sent     INTEGER DEFAULT 0,
    mail_hub_syncs  INTEGER DEFAULT 0,
    ai_classifications INTEGER DEFAULT 0,
    UNIQUE(client_id, month)
);

CREATE INDEX IF NOT EXISTS idx_usage_client_month ON usage_tracking(client_id, month);

CREATE TABLE IF NOT EXISTS team_members (
    id              SERIAL PRIMARY KEY,
    owner_id        INTEGER NOT NULL REFERENCES clients(id),
    member_email    TEXT NOT NULL,
    member_client_id INTEGER REFERENCES clients(id),
    role            TEXT NOT NULL DEFAULT 'member',
    status          TEXT NOT NULL DEFAULT 'pending',
    invite_token    TEXT,
    campaign_id     INTEGER REFERENCES campaigns(id),
    invited_at      TIMESTAMP DEFAULT NOW(),
    accepted_at     TIMESTAMP,
    UNIQUE(owner_id, member_email)
);

CREATE TABLE IF NOT EXISTS email_suppressions (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    email           TEXT NOT NULL,
    reason          TEXT DEFAULT 'unsubscribed',
    source          TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_suppressions_client ON email_suppressions(client_id);
CREATE INDEX IF NOT EXISTS idx_suppressions_email ON email_suppressions(client_id, email);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    business    TEXT DEFAULT '',
    physical_address TEXT DEFAULT '',
    mail_preferences TEXT DEFAULT '',
    mail_exclusions TEXT DEFAULT '',
    is_admin    INTEGER DEFAULT 0,
    email_verified INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    token       TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    used        INTEGER DEFAULT 0,
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
    status      TEXT DEFAULT 'draft',
    scheduled_start TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_campaigns_client ON campaigns(client_id);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name        TEXT DEFAULT '',
    email       TEXT NOT NULL,
    company     TEXT DEFAULT '',
    role        TEXT DEFAULT '',
    language    TEXT DEFAULT 'en',
    custom_data TEXT DEFAULT '{}',
    status      TEXT DEFAULT 'pending',
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS email_sequences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    step        INTEGER NOT NULL DEFAULT 1,
    subject_a   TEXT NOT NULL,
    subject_b   TEXT DEFAULT '',
    body_a      TEXT NOT NULL,
    body_b      TEXT DEFAULT '',
    delay_days  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sent_emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id),
    sequence_id INTEGER NOT NULL REFERENCES email_sequences(id),
    variant     TEXT DEFAULT 'a',
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT DEFAULT 'sent',
    sent_at     TEXT DEFAULT (datetime('now', 'localtime')),
    opened_at   TEXT,
    replied_at  TEXT,
    reply_body  TEXT DEFAULT '',
    reply_sentiment TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_sent_contact ON sent_emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_sent_sequence ON sent_emails(sequence_id);

CREATE TABLE IF NOT EXISTS mail_inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    message_id   TEXT NOT NULL,
    from_name    TEXT DEFAULT '',
    from_email   TEXT NOT NULL,
    to_email     TEXT DEFAULT '',
    subject      TEXT DEFAULT '',
    body_preview TEXT DEFAULT '',
    received_at  TEXT DEFAULT '',
    priority     TEXT DEFAULT 'normal',
    category     TEXT DEFAULT 'uncategorized',
    is_read      INTEGER DEFAULT 0,
    is_starred   INTEGER DEFAULT 0,
    is_archived  INTEGER DEFAULT 0,
    snooze_until TEXT,
    snooze_note  TEXT DEFAULT '',
    ai_summary   TEXT DEFAULT '',
    account_id   INTEGER,
    fetched_at   TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_inbox_client ON mail_inbox(client_id);
CREATE INDEX IF NOT EXISTS idx_mail_inbox_priority ON mail_inbox(client_id, priority);
CREATE INDEX IF NOT EXISTS idx_mail_inbox_category ON mail_inbox(client_id, category);

CREATE TABLE IF NOT EXISTS email_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    label        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL,
    imap_host    TEXT NOT NULL DEFAULT 'imap.gmail.com',
    imap_port    INTEGER NOT NULL DEFAULT 993,
    smtp_host    TEXT NOT NULL DEFAULT 'smtp.gmail.com',
    smtp_port    INTEGER NOT NULL DEFAULT 465,
    password     TEXT NOT NULL DEFAULT '',
    is_default   INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_email_accounts_client ON email_accounts(client_id);

CREATE TABLE IF NOT EXISTS contacts_book (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    email           TEXT NOT NULL,
    name            TEXT DEFAULT '',
    company         TEXT DEFAULT '',
    role            TEXT DEFAULT '',
    relationship    TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    personality     TEXT DEFAULT '',
    tags            TEXT DEFAULT '',
    language        TEXT DEFAULT '',
    last_contacted  TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_contacts_book_client ON contacts_book(client_id);
CREATE INDEX IF NOT EXISTS idx_contacts_book_email ON contacts_book(client_id, email);

CREATE TABLE IF NOT EXISTS scheduled_emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    to_email        TEXT NOT NULL,
    to_name         TEXT DEFAULT '',
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    scheduled_at    TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    sent_at         TEXT,
    reply_to_mail_id INTEGER,
    account_id      INTEGER,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_scheduled_client ON scheduled_emails(client_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_emails(status, scheduled_at);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL UNIQUE REFERENCES clients(id),
    plan            TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id   TEXT DEFAULT '',
    stripe_subscription_id TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',
    current_period_start TEXT DEFAULT '',
    current_period_end   TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS usage_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    month           TEXT NOT NULL,
    emails_sent     INTEGER DEFAULT 0,
    mail_hub_syncs  INTEGER DEFAULT 0,
    ai_classifications INTEGER DEFAULT 0,
    UNIQUE(client_id, month)
);

CREATE INDEX IF NOT EXISTS idx_usage_client_month ON usage_tracking(client_id, month);

CREATE TABLE IF NOT EXISTS team_members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES clients(id),
    member_email    TEXT NOT NULL,
    member_client_id INTEGER REFERENCES clients(id),
    role            TEXT NOT NULL DEFAULT 'member',
    status          TEXT NOT NULL DEFAULT 'pending',
    invite_token    TEXT,
    campaign_id     INTEGER REFERENCES campaigns(id),
    invited_at      TEXT DEFAULT (datetime('now', 'localtime')),
    accepted_at     TEXT,
    UNIQUE(owner_id, member_email)
);

CREATE TABLE IF NOT EXISTS email_suppressions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    email           TEXT NOT NULL,
    reason          TEXT DEFAULT 'unsubscribed',
    source          TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_suppressions_client ON email_suppressions(client_id);
CREATE INDEX IF NOT EXISTS idx_suppressions_email ON email_suppressions(client_id, email);
"""


def init_db():
    """Create all tables if they don't exist, then run migrations."""
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(_PG_SCHEMA)
        else:
            db.executescript(_SQLITE_SCHEMA)

    # Run migrations for columns that may not exist yet
    _run_migrations()
    print("Database initialized.")


def _run_migrations():
    """Add columns that may be missing from older schemas."""
    migrations = [
        ("clients", "physical_address", "TEXT DEFAULT ''"),
        ("clients", "email_verified", "INTEGER DEFAULT 0"),
    ]
    with get_db() as db:
        for table, col, col_type in migrations:
            try:
                _exec(db, f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except Exception:
                pass  # column already exists


# ---------------------------------------------------------------------------
# Helpers for datetime — cross-engine
# ---------------------------------------------------------------------------

def _now_expr():
    """SQL expression for current timestamp."""
    return "NOW()" if _USE_PG else "datetime('now', 'localtime')"


def _ts_cast(col: str) -> str:
    """Cast a TEXT column to timestamp for comparison (PG needs explicit cast)."""
    return f"{col}::timestamp" if _USE_PG else col


def _date_diff_days(col):
    """SQL expression: fractional days since `col` until now."""
    if _USE_PG:
        return f"EXTRACT(EPOCH FROM NOW() - {col}::timestamp) / 86400.0"
    return f"julianday('now', 'localtime') - julianday({col})"


def _n_days_ago(n):
    """SQL expression for the date N days ago."""
    if _USE_PG:
        return f"(CURRENT_DATE - INTERVAL '{n} days')::text"
    return f"DATE('now', 'localtime', '-{n} days')"


def _date_plus_days(col, days_col):
    """SQL expression: col + days_col days (both column names)."""
    if _USE_PG:
        return f"({col}::date + ({days_col} || ' days')::interval)"
    return f"date({col}, '+' || {days_col} || ' days')"


def _dow_expr(col):
    """Day of week (0=Sun for SQLite, 0=Sun for PG via DOW)."""
    if _USE_PG:
        return f"EXTRACT(DOW FROM {col}::timestamp)::int"
    return f"CAST(strftime('%w', {col}) AS INTEGER)"


def _hour_expr(col):
    """Hour of day."""
    if _USE_PG:
        return f"EXTRACT(HOUR FROM {col}::timestamp)::int"
    return f"CAST(strftime('%H', {col}) AS INTEGER)"


def _date_expr(col):
    """Extract date from timestamp."""
    if _USE_PG:
        return f"({col})::date::text"
    return f"DATE({col})"


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def create_client(name: str, email: str, password_hash: str, business: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO clients (name, email, password, business) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, email, password_hash, business),
        )


def get_client_by_email(email: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM clients WHERE email = %s", (email,))


def get_client(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM clients WHERE id = %s", (client_id,))


def get_all_client_emails() -> list[dict]:
    with get_db() as db:
        return _fetchall(db, "SELECT id, name, email FROM clients ORDER BY id")


def update_client(client_id: int, name: str, business: str, physical_address: str = ""):
    with get_db() as db:
        _exec(db, "UPDATE clients SET name = %s, business = %s, physical_address = %s WHERE id = %s",
              (name, business, physical_address, client_id))


def update_client_password(client_id: int, password_hash: str):
    with get_db() as db:
        _exec(db, "UPDATE clients SET password = %s WHERE id = %s",
              (password_hash, client_id))


def update_mail_preferences(client_id: int, preferences: str):
    with get_db() as db:
        _exec(db, "UPDATE clients SET mail_preferences = %s WHERE id = %s",
              (preferences, client_id))


def get_mail_preferences(client_id: int) -> str:
    with get_db() as db:
        val = _fetchval(db, "SELECT mail_preferences FROM clients WHERE id = %s",
                        (client_id,))
        return (val or "")


def update_mail_exclusions(client_id: int, exclusions: str):
    with get_db() as db:
        _exec(db, "UPDATE clients SET mail_exclusions = %s WHERE id = %s",
              (exclusions, client_id))


def get_mail_exclusions(client_id: int) -> str:
    with get_db() as db:
        val = _fetchval(db, "SELECT mail_exclusions FROM clients WHERE id = %s",
                        (client_id,))
        return (val or "")


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def create_reset_token(client_id: int, token: str, expires_at: str):
    with get_db() as db:
        _exec(db,
              "INSERT INTO password_reset_tokens (client_id, token, expires_at) VALUES (%s, %s, %s)",
              (client_id, token, expires_at))


def get_valid_reset_token(token: str) -> dict | None:
    with get_db() as db:
        now = _now_expr()
        return _fetchone(db,
            f"SELECT * FROM password_reset_tokens WHERE token = %s AND used = 0 AND expires_at > {now}",
            (token,))


def mark_reset_token_used(token: str):
    with get_db() as db:
        _exec(db, "UPDATE password_reset_tokens SET used = 1 WHERE token = %s", (token,))


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

def create_verification_token(client_id: int, token: str, expires_at: str):
    with get_db() as db:
        _exec(db,
              "INSERT INTO email_verification_tokens (client_id, token, expires_at) VALUES (%s, %s, %s)",
              (client_id, token, expires_at))


def get_valid_verification_token(token: str) -> dict | None:
    with get_db() as db:
        now = _now_expr()
        return _fetchone(db,
            f"SELECT * FROM email_verification_tokens WHERE token = %s AND used = 0 AND expires_at > {now}",
            (token,))


def mark_email_verified(client_id: int):
    with get_db() as db:
        _exec(db, "UPDATE clients SET email_verified = 1 WHERE id = %s", (client_id,))
        _exec(db, "UPDATE email_verification_tokens SET used = 1 WHERE client_id = %s", (client_id,))


# ---------------------------------------------------------------------------
# Email Accounts (multi-mailbox)
# ---------------------------------------------------------------------------

def get_email_accounts(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT * FROM email_accounts WHERE client_id = %s ORDER BY is_default DESC, created_at ASC",
            (client_id,))
        for d in rows:
            d["password"] = decrypt_password(d["password"])
        return rows


def get_email_account(account_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        d = _fetchone(db,
            "SELECT * FROM email_accounts WHERE id = %s AND client_id = %s",
            (account_id, client_id))
        if d:
            d["password"] = decrypt_password(d["password"])
        return d


def get_default_email_account(client_id: int) -> dict | None:
    with get_db() as db:
        d = _fetchone(db,
            "SELECT * FROM email_accounts WHERE client_id = %s ORDER BY is_default DESC, id ASC LIMIT 1",
            (client_id,))
        if d:
            d["password"] = decrypt_password(d["password"])
        return d


def create_email_account(client_id: int, label: str, email: str, password: str,
                         imap_host: str = "imap.gmail.com", imap_port: int = 993,
                         smtp_host: str = "smtp.gmail.com", smtp_port: int = 465,
                         is_default: int = 0) -> int:
    with get_db() as db:
        if is_default:
            _exec(db, "UPDATE email_accounts SET is_default = 0 WHERE client_id = %s", (client_id,))
        existing = _fetchval(db, "SELECT COUNT(*) FROM email_accounts WHERE client_id = %s", (client_id,))
        if existing == 0:
            is_default = 1
        return _insert_returning_id(
            db,
            """INSERT INTO email_accounts (client_id, label, email, imap_host, imap_port,
                                           smtp_host, smtp_port, password, is_default)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (client_id, label, email, imap_host, imap_port, smtp_host, smtp_port,
             encrypt_password(password), is_default),
        )


def update_email_account(account_id: int, client_id: int, **kwargs) -> bool:
    allowed = {"label", "email", "imap_host", "imap_port", "smtp_host", "smtp_port", "password", "is_default"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    if "password" in updates:
        updates["password"] = encrypt_password(updates["password"])
    with get_db() as db:
        if updates.get("is_default"):
            _exec(db, "UPDATE email_accounts SET is_default = 0 WHERE client_id = %s", (client_id,))
        sets = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [account_id, client_id]
        _exec(db, f"UPDATE email_accounts SET {sets} WHERE id = %s AND client_id = %s", vals)
        return True


def delete_email_account(account_id: int, client_id: int) -> bool:
    with get_db() as db:
        _exec(db, "DELETE FROM email_accounts WHERE id = %s AND client_id = %s",
              (account_id, client_id))
        remaining = _fetchone(db, "SELECT id FROM email_accounts WHERE client_id = %s LIMIT 1",
                              (client_id,))
        if remaining:
            _exec(db, "UPDATE email_accounts SET is_default = 1 WHERE id = %s", (remaining["id"],))
        return True


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

def create_campaign(client_id: int, name: str, business_type: str,
                    target_audience: str, tone: str, scheduled_start: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO campaigns (client_id, name, business_type, target_audience, tone, scheduled_start) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, name, business_type, target_audience, tone, scheduled_start or None),
        )


def update_campaign_schedule(campaign_id: int, scheduled_start: str):
    with get_db() as db:
        _exec(db, "UPDATE campaigns SET scheduled_start = %s WHERE id = %s",
              (scheduled_start or None, campaign_id))


def get_campaigns(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM campaigns WHERE client_id = %s ORDER BY created_at DESC",
            (client_id,))


def get_campaign(campaign_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM campaigns WHERE id = %s", (campaign_id,))


def update_campaign_status(campaign_id: int, status: str):
    with get_db() as db:
        _exec(db, "UPDATE campaigns SET status = %s WHERE id = %s", (status, campaign_id))


def delete_campaign(campaign_id: int):
    with get_db() as db:
        _exec(db, """DELETE FROM sent_emails WHERE contact_id IN
                     (SELECT id FROM contacts WHERE campaign_id = %s)""", (campaign_id,))
        _exec(db, """DELETE FROM sent_emails WHERE sequence_id IN
                     (SELECT id FROM email_sequences WHERE campaign_id = %s)""", (campaign_id,))
        _exec(db, "DELETE FROM campaigns WHERE id = %s", (campaign_id,))


def duplicate_campaign(campaign_id: int, client_id: int) -> int | None:
    with get_db() as db:
        camp = _fetchone(db, "SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
        if not camp:
            return None
        new_id = _insert_returning_id(
            db,
            "INSERT INTO campaigns (client_id, name, business_type, target_audience, tone, status) "
            "VALUES (%s, %s, %s, %s, %s, 'draft') RETURNING id",
            (client_id, camp["name"] + " (copy)", camp["business_type"],
             camp["target_audience"], camp["tone"]),
        )
        seqs = _fetchall(db,
            "SELECT * FROM email_sequences WHERE campaign_id = %s ORDER BY step",
            (campaign_id,))
        for s in seqs:
            _exec(db,
                "INSERT INTO email_sequences (campaign_id, step, subject_a, subject_b, body_a, body_b, delay_days) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (new_id, s["step"], s["subject_a"], s["subject_b"],
                 s["body_a"], s["body_b"], s["delay_days"]))
        return new_id


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def add_contacts(campaign_id: int, contacts: list[dict]) -> int:
    with get_db() as db:
        count = 0
        for c in contacts:
            try:
                _exec(db,
                    "INSERT INTO contacts (campaign_id, name, email, company, role, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (campaign_id, c.get("name", ""), c["email"],
                     c.get("company", ""), c.get("role", ""),
                     c.get("language", "en")))
                count += 1
            except Exception:
                if _USE_PG:
                    db.rollback()  # PG requires rollback after error in tx
                pass
        return count


def get_campaign_contacts(campaign_id: int, status: str | None = None) -> list[dict]:
    with get_db() as db:
        if status:
            return _fetchall(db,
                "SELECT * FROM contacts WHERE campaign_id = %s AND status = %s",
                (campaign_id, status))
        return _fetchall(db,
            "SELECT * FROM contacts WHERE campaign_id = %s",
            (campaign_id,))


def delete_contact(contact_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM sent_emails WHERE contact_id = %s", (contact_id,))
        _exec(db, "DELETE FROM contacts WHERE id = %s", (contact_id,))


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

def save_sequence(campaign_id: int, step: int, subject_a: str, subject_b: str,
                  body_a: str, body_b: str, delay_days: int = 0) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO email_sequences (campaign_id, step, subject_a, subject_b, "
            "body_a, body_b, delay_days) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (campaign_id, step, subject_a, subject_b, body_a, body_b, delay_days),
        )


def get_sequences(campaign_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM email_sequences WHERE campaign_id = %s ORDER BY step",
            (campaign_id,))


def update_sequence(seq_id: int, subject_a: str, subject_b: str,
                    body_a: str, delay_days: int):
    with get_db() as db:
        _exec(db,
            "UPDATE email_sequences SET subject_a=%s, subject_b=%s, body_a=%s, delay_days=%s "
            "WHERE id=%s",
            (subject_a, subject_b, body_a, delay_days, seq_id))


def delete_sequence(seq_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM email_sequences WHERE id = %s", (seq_id,))


# ---------------------------------------------------------------------------
# Sent emails & tracking
# ---------------------------------------------------------------------------

def record_sent(contact_id: int, sequence_id: int, variant: str,
                subject: str, body: str) -> int:
    with get_db() as db:
        sent_id = _insert_returning_id(
            db,
            "INSERT INTO sent_emails (contact_id, sequence_id, variant, subject, body) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (contact_id, sequence_id, variant, subject, body),
        )
        _exec(db, "UPDATE contacts SET status = 'sent' WHERE id = %s", (contact_id,))
        return sent_id


def delete_sent_email(sent_id: int, contact_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM sent_emails WHERE id = %s", (sent_id,))
        other = _fetchval(db, "SELECT COUNT(*) FROM sent_emails WHERE contact_id = %s", (contact_id,))
        if other == 0:
            _exec(db, "UPDATE contacts SET status = 'pending' WHERE id = %s", (contact_id,))


def record_open(sent_email_id: int):
    with get_db() as db:
        now = _now_expr()
        _exec(db,
            f"UPDATE sent_emails SET status = 'opened', opened_at = {now} WHERE id = %s AND status = 'sent'",
            (sent_email_id,))
        _exec(db, """
            UPDATE contacts SET status = 'opened'
            WHERE id = (SELECT contact_id FROM sent_emails WHERE id = %s)
              AND status = 'sent'
        """, (sent_email_id,))


def record_reply(contact_email: str, reply_body: str = "", reply_sentiment: str = "") -> bool:
    with get_db() as db:
        row = _fetchone(db,
            "SELECT c.id as contact_id, se.id as sent_id "
            "FROM contacts c "
            "JOIN sent_emails se ON se.contact_id = c.id "
            "WHERE LOWER(c.email) = LOWER(%s) AND c.status != 'replied' "
            "ORDER BY se.sent_at DESC LIMIT 1",
            (contact_email,))
        if not row:
            return False
        now = _now_expr()
        _exec(db, "UPDATE contacts SET status = 'replied' WHERE id = %s",
              (row["contact_id"],))
        _exec(db,
            f"UPDATE sent_emails SET status = 'replied', replied_at = {now}, "
            "reply_body = %s, reply_sentiment = %s WHERE id = %s",
            (reply_body, reply_sentiment, row["sent_id"]))
        return True


def get_all_sent_recipient_emails() -> set[str]:
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT DISTINCT LOWER(c.email) as email FROM contacts c "
            "JOIN sent_emails se ON se.contact_id = c.id "
            "WHERE c.status != 'replied'")
        return {r["email"] for r in rows}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_campaign_stats(campaign_id: int) -> dict:
    with get_db() as db:
        total = _fetchval(db, "SELECT COUNT(*) FROM contacts WHERE campaign_id = %s", (campaign_id,))
        sent = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = %s", (campaign_id,))
        opened = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = %s AND se.status IN ('opened', 'clicked', 'replied')",
            (campaign_id,))
        replied = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = %s AND se.status = 'replied'", (campaign_id,))
        bounced = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "WHERE c.campaign_id = %s AND se.status = 'bounced'", (campaign_id,))
        return {
            "total_contacts": total or 0,
            "emails_sent": sent or 0,
            "opens": opened or 0,
            "open_rate": (opened or 0) / sent if sent else 0,
            "replies": replied or 0,
            "reply_rate": (replied or 0) / sent if sent else 0,
            "bounced": bounced or 0,
            "bounce_rate": (bounced or 0) / sent if sent else 0,
        }


def get_ab_stats(campaign_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT es.step, se.variant,
                   COUNT(*) AS sent,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) AS opened,
                   SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) AS replied,
                   SUM(CASE WHEN se.status = 'bounced' THEN 1 ELSE 0 END) AS bounced
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE c.campaign_id = %s
            GROUP BY es.step, se.variant
            ORDER BY es.step, se.variant
        """, (campaign_id,))


def get_global_stats(client_id: int) -> dict:
    with get_db() as db:
        total_camps = _fetchval(db, "SELECT COUNT(*) FROM campaigns WHERE client_id = %s", (client_id,))
        active_camps = _fetchval(db, "SELECT COUNT(*) FROM campaigns WHERE client_id = %s AND status = 'active'", (client_id,))
        total_contacts = _fetchval(db,
            "SELECT COUNT(*) FROM contacts c JOIN campaigns camp ON c.campaign_id = camp.id WHERE camp.client_id = %s",
            (client_id,))
        total_sent = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id WHERE camp.client_id = %s", (client_id,))
        total_opened = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = %s AND se.status IN ('opened', 'clicked', 'replied')", (client_id,))
        total_replied = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = %s AND se.status = 'replied'", (client_id,))
        total_bounced = _fetchval(db,
            "SELECT COUNT(*) FROM sent_emails se JOIN contacts c ON se.contact_id = c.id "
            "JOIN campaigns camp ON c.campaign_id = camp.id "
            "WHERE camp.client_id = %s AND se.status = 'bounced'", (client_id,))
        ts = total_sent or 0
        return {
            "total_campaigns": total_camps or 0,
            "active_campaigns": active_camps or 0,
            "total_contacts": total_contacts or 0,
            "total_sent": ts,
            "total_opened": total_opened or 0,
            "open_rate": (total_opened or 0) / ts if ts else 0,
            "total_replied": total_replied or 0,
            "reply_rate": (total_replied or 0) / ts if ts else 0,
            "total_bounced": total_bounced or 0,
            "bounce_rate": (total_bounced or 0) / ts if ts else 0,
        }


def get_daily_analytics(client_id: int, days: int = 30) -> list[dict]:
    with get_db() as db:
        date_col = _date_expr("se.sent_at")
        since = _n_days_ago(days)
        return _fetchall(db, f"""
            SELECT {date_col} AS day,
              COUNT(*) AS sent,
              SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) AS opened,
              SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) AS replied,
              SUM(CASE WHEN se.status = 'bounced' THEN 1 ELSE 0 END) AS bounced
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE camp.client_id = %s AND se.sent_at >= {since}
            GROUP BY day ORDER BY day
        """, (client_id,))


def get_send_time_stats(client_id: int) -> list[dict]:
    with get_db() as db:
        dow = _dow_expr("se.sent_at")
        hour = _hour_expr("se.sent_at")
        return _fetchall(db, f"""
            SELECT {dow} as dow, {hour} as hour,
                   COUNT(*) as total,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) as opens
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE camp.client_id = %s
            GROUP BY dow, hour
            ORDER BY dow, hour
        """, (client_id,))


# ---------------------------------------------------------------------------
# Emails to send (worker)
# ---------------------------------------------------------------------------

def get_emails_to_send(limit: int = 50) -> list[dict]:
    with get_db() as db:
        results = []
        now = _now_expr()

        rows = _fetchall(db, f"""
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
              AND (camp.scheduled_start IS NULL OR camp.scheduled_start <= {now})
            LIMIT %s
        """, (limit,))
        results.extend(rows)

        remaining = limit - len(results)
        if remaining <= 0:
            return results

        diff = _date_diff_days("se.sent_at")
        followup_rows = _fetchall(db, f"""
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
              AND (camp.scheduled_start IS NULL OR camp.scheduled_start <= {now})
              AND {diff} >= next_seq.delay_days
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se2
                  WHERE se2.contact_id = c.id AND se2.sequence_id = next_seq.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM sent_emails se3
                  JOIN email_sequences es3 ON se3.sequence_id = es3.id
                  WHERE se3.contact_id = c.id AND es3.step > last_seq.step
              )
            LIMIT %s
        """, (remaining,))
        results.extend(followup_rows)
        return results


# ---------------------------------------------------------------------------
# Inbox / threads
# ---------------------------------------------------------------------------

def get_replies(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
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
            WHERE camp.client_id = %s AND se.status = 'replied'
            ORDER BY se.replied_at DESC
        """, (client_id,))


def get_inbox_all(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
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
            WHERE camp.client_id = %s
            ORDER BY se.sent_at DESC
        """, (client_id,))


def get_sent_emails(campaign_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT se.*, c.name as contact_name, c.email as contact_email,
                   c.company as contact_company, es.step
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE c.campaign_id = %s
            ORDER BY se.sent_at DESC
        """, (campaign_id,))


def get_reply_context(sent_email_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, """
            SELECT se.subject, se.body, se.reply_body, se.reply_sentiment,
                   c.name as contact_name, c.email as contact_email,
                   c.company, c.role,
                   camp.business_type, camp.target_audience, camp.tone
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            WHERE se.id = %s AND se.status = 'replied'
        """, (sent_email_id,))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def get_calendar_events(client_id: int) -> list[dict]:
    with get_db() as db:
        sent = _fetchall(db, """
            SELECT se.sent_at as date, c.name as contact_name, c.email as contact_email,
                   se.subject, se.status as email_status, se.variant,
                   camp.name as campaign_name, camp.id as campaign_id,
                   es.step, 'sent' as event_type
            FROM sent_emails se
            JOIN contacts c ON se.contact_id = c.id
            JOIN campaigns camp ON c.campaign_id = camp.id
            JOIN email_sequences es ON se.sequence_id = es.id
            WHERE camp.client_id = %s
            ORDER BY se.sent_at DESC
        """, (client_id,))

        date_plus = _date_plus_days("se.sent_at", "next_seq.delay_days")
        pending = _fetchall(db, f"""
            SELECT {date_plus} as date,
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
            WHERE camp.client_id = %s
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
        """, (client_id,))

        return sent + pending


# ---------------------------------------------------------------------------
# A/B global stats
# ---------------------------------------------------------------------------

def get_ab_stats_global(client_id: int) -> list[dict]:
    """get_ab_stats but across all client campaigns (used by /ab-tests page)."""
    with get_db() as db:
        return _fetchall(db, """
            SELECT es.id as seq_id, es.step, es.subject_a, es.subject_b,
                   camp.id as campaign_id, camp.name as campaign_name,
                   se.variant,
                   COUNT(se.id) as sent_count,
                   SUM(CASE WHEN se.status IN ('opened','clicked','replied') THEN 1 ELSE 0 END) as opens,
                   SUM(CASE WHEN se.status = 'replied' THEN 1 ELSE 0 END) as replies
            FROM email_sequences es
            JOIN campaigns camp ON es.campaign_id = camp.id
            LEFT JOIN sent_emails se ON se.sequence_id = es.id
            WHERE camp.client_id = %s
              AND es.subject_b IS NOT NULL AND es.subject_b != ''
            GROUP BY es.id, es.step, es.subject_a, es.subject_b,
                     camp.id, camp.name, se.variant
            ORDER BY camp.name, es.step, se.variant
        """, (client_id,))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def get_export_data(client_id: int, campaign_id: int | None = None) -> list[dict]:
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
            WHERE camp.client_id = %s
        """
        params = [client_id]
        if campaign_id:
            query += " AND camp.id = %s"
            params.append(campaign_id)
        query += " ORDER BY camp.name, se.sent_at DESC"
        return _fetchall(db, query, params)


# ---------------------------------------------------------------------------
# Mail Hub
# ---------------------------------------------------------------------------

def upsert_mail(client_id: int, message_id: str, from_name: str, from_email: str,
                to_email: str, subject: str, body_preview: str, received_at: str,
                priority: str = "normal", category: str = "uncategorized",
                ai_summary: str = "", account_id: int | None = None,
                is_read: int = 0) -> bool:
    with get_db() as db:
        try:
            if _USE_PG:
                _exec(db, """
                    INSERT INTO mail_inbox
                        (client_id, message_id, from_name, from_email, to_email,
                         subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (client_id, message_id) DO NOTHING
                """, (client_id, message_id, from_name, from_email, to_email,
                      subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read))
                return db.cursor().rowcount != 0 if hasattr(db, 'cursor') else True
            else:
                _exec(db, """
                    INSERT INTO mail_inbox
                        (client_id, message_id, from_name, from_email, to_email,
                         subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (client_id, message_id, from_name, from_email, to_email,
                      subject, body_preview, received_at, priority, category, ai_summary, account_id, is_read))
                return True
        except Exception:
            if _USE_PG:
                db.rollback()
            return False


def get_mail_inbox(client_id: int, filter_by: str = "all",
                   category: str | None = None, account_id: int | None = None,
                   sender: str | None = None,
                   limit: int = 100) -> list[dict]:
    with get_db() as db:
        now = _now_expr()
        conditions = ["m.client_id = %s", "m.is_archived = 0"]
        params: list = [client_id]

        conditions.append(f"(m.snooze_until IS NULL OR {_ts_cast('m.snooze_until')} <= {now})")

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
            conditions = ["m.client_id = %s",
                          f"m.snooze_until IS NOT NULL AND {_ts_cast('m.snooze_until')} > {now}"]
            params = [client_id]

        if category and category != "all":
            conditions.append("m.category = %s")
            params.append(category)

        if account_id is not None:
            conditions.append("m.account_id = %s")
            params.append(account_id)

        if sender:
            conditions.append("LOWER(m.from_email) = %s")
            params.append(sender.lower())

        where = " AND ".join(conditions)
        return _fetchall(db, f"""
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
            LIMIT %s
        """, params + [limit])


def get_mail_stats(client_id: int) -> dict:
    with get_db() as db:
        now = _now_expr()
        total = _fetchval(db, "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND is_archived = 0", (client_id,))
        unread = _fetchval(db, "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND is_archived = 0 AND is_read = 0", (client_id,))
        starred = _fetchval(db, "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND is_starred = 1 AND is_archived = 0", (client_id,))
        urgent = _fetchval(db, "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND is_archived = 0 AND priority IN ('urgent','important')", (client_id,))
        read_count = _fetchval(db, "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND is_archived = 0 AND is_read = 1", (client_id,))
        snoozed = _fetchval(db, f"SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND snooze_until IS NOT NULL AND {_ts_cast('snooze_until')} > {now}", (client_id,))

        cat_rows = _fetchall(db, """
            SELECT category, COUNT(*) as cnt FROM mail_inbox
            WHERE client_id = %s AND is_archived = 0
            GROUP BY category
        """, (client_id,))
        categories = {r["category"]: r["cnt"] for r in cat_rows}

        pri_rows = _fetchall(db, """
            SELECT priority, COUNT(*) as cnt FROM mail_inbox
            WHERE client_id = %s AND is_archived = 0
            GROUP BY priority
        """, (client_id,))
        priorities = {r["priority"]: r["cnt"] for r in pri_rows}

        return {
            "total": total or 0, "unread": unread or 0, "read": read_count or 0,
            "starred": starred or 0, "urgent": urgent or 0, "snoozed": snoozed or 0,
            "categories": categories, "priorities": priorities,
        }


def get_top_senders(client_id: int, limit: int = 10) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(db, """
            SELECT cb.email, cb.name, COUNT(m.id) as cnt
            FROM contacts_book cb
            JOIN mail_inbox m ON LOWER(m.from_email) = LOWER(cb.email) AND m.client_id = cb.client_id
            WHERE cb.client_id = %s AND m.is_archived = 0
            GROUP BY cb.email, cb.name
            ORDER BY cnt DESC
            LIMIT %s
        """, (client_id, limit))
        return [{"email": r["email"], "name": r["name"] or r["email"].split("@")[0], "count": r["cnt"]} for r in rows]


def update_mail_field(mail_id: int, client_id: int, field: str, value) -> bool:
    allowed = {"is_read", "is_starred", "is_archived", "snooze_until", "snooze_note", "priority", "category"}
    if field not in allowed:
        return False
    with get_db() as db:
        _exec(db, f"UPDATE mail_inbox SET {field} = %s WHERE id = %s AND client_id = %s",
              (value, mail_id, client_id))
        return True


def get_mail_item(mail_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM mail_inbox WHERE id = %s AND client_id = %s",
                         (mail_id, client_id))


def search_mail_inbox(client_id: int, query: str, limit: int = 50) -> list[dict]:
    with get_db() as db:
        like = f"%{query}%"
        return _fetchall(db, """
            SELECT * FROM mail_inbox
            WHERE client_id = %s AND is_archived = 0
              AND (subject LIKE %s OR body_preview LIKE %s OR from_name LIKE %s OR from_email LIKE %s)
            ORDER BY received_at DESC
            LIMIT %s
        """, (client_id, like, like, like, like, limit))


def bulk_update_mail(mail_ids: list[int], client_id: int, field: str, value) -> int:
    allowed = {"is_read", "is_starred", "is_archived", "priority", "category"}
    if field not in allowed or not mail_ids:
        return 0
    with get_db() as db:
        if _USE_PG:
            placeholders = ",".join(["%s"] * len(mail_ids))
        else:
            placeholders = ",".join(["?"] * len(mail_ids))
        # Use raw cursor here since we mix placeholders
        cur = db.cursor()
        sql = f"UPDATE mail_inbox SET {field} = %s WHERE id IN ({placeholders}) AND client_id = %s"
        if not _USE_PG:
            sql = sql.replace("%s", "?")
        cur.execute(sql, [value] + mail_ids + [client_id])
        return cur.rowcount


# ---------------------------------------------------------------------------
# Contacts Book (CRM)
# ---------------------------------------------------------------------------

def upsert_contact(client_id: int, email: str, name: str = "", company: str = "",
                   role: str = "", relationship: str = "", notes: str = "",
                   personality: str = "", tags: str = "", language: str = "") -> int:
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO contacts_book (client_id, email, name, company, role, relationship, notes, personality, tags, language)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(client_id, email) DO UPDATE SET
                    name = CASE WHEN EXCLUDED.name != '' THEN EXCLUDED.name ELSE contacts_book.name END,
                    company = CASE WHEN EXCLUDED.company != '' THEN EXCLUDED.company ELSE contacts_book.company END,
                    role = CASE WHEN EXCLUDED.role != '' THEN EXCLUDED.role ELSE contacts_book.role END,
                    relationship = CASE WHEN EXCLUDED.relationship != '' THEN EXCLUDED.relationship ELSE contacts_book.relationship END,
                    notes = CASE WHEN EXCLUDED.notes != '' THEN EXCLUDED.notes ELSE contacts_book.notes END,
                    personality = CASE WHEN EXCLUDED.personality != '' THEN EXCLUDED.personality ELSE contacts_book.personality END,
                    tags = CASE WHEN EXCLUDED.tags != '' THEN EXCLUDED.tags ELSE contacts_book.tags END,
                    language = CASE WHEN EXCLUDED.language != '' THEN EXCLUDED.language ELSE contacts_book.language END
                RETURNING id
            """, (client_id, email, name, company, role, relationship, notes, personality, tags, language))
            return cur.fetchone()["id"]
        else:
            cur = db.cursor()
            cur.execute("""
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
        sql = "SELECT * FROM contacts_book WHERE client_id = %s"
        params: list = [client_id]
        if search:
            sql += " AND (name LIKE %s OR email LIKE %s OR company LIKE %s)"
            s = f"%{search}%"
            params.extend([s, s, s])
        if tag:
            sql += " AND (',' || tags || ',') LIKE %s"
            params.append(f"%,{tag},%")
        if relationship:
            sql += " AND relationship = %s"
            params.append(relationship)
        sql += " ORDER BY last_contacted DESC, name ASC"
        return _fetchall(db, sql, params)


def get_contact(contact_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db,
            "SELECT * FROM contacts_book WHERE id = %s AND client_id = %s",
            (contact_id, client_id))


def get_contact_by_email(client_id: int, email: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db,
            "SELECT * FROM contacts_book WHERE client_id = %s AND email = %s",
            (client_id, email))


def update_contact(contact_id: int, client_id: int, **fields) -> bool:
    allowed = {"name", "company", "role", "relationship", "notes", "personality",
               "tags", "last_contacted", "language"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    vals = list(updates.values()) + [contact_id, client_id]
    with get_db() as db:
        _exec(db, f"UPDATE contacts_book SET {set_clause} WHERE id = %s AND client_id = %s", vals)
        return True


def delete_contact_book(contact_id: int, client_id: int) -> bool:
    with get_db() as db:
        _exec(db, "DELETE FROM contacts_book WHERE id = %s AND client_id = %s",
              (contact_id, client_id))
        return True


# ---------------------------------------------------------------------------
# Email Suppressions (Global unsubscribe / CAN-SPAM)
# ---------------------------------------------------------------------------

def add_suppression(client_id: int, email: str, reason: str = "unsubscribed", source: str = "") -> None:
    """Add an email to the global suppression list for a client."""
    with get_db() as db:
        _exec(db,
            """INSERT INTO email_suppressions (client_id, email, reason, source)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (client_id, email) DO UPDATE SET reason = EXCLUDED.reason""",
            (client_id, email.lower().strip(), reason, source))


def is_suppressed(client_id: int, email: str) -> bool:
    """Check if an email is on the global suppression list."""
    with get_db() as db:
        row = _fetchone(db,
            "SELECT 1 FROM email_suppressions WHERE client_id = %s AND email = %s",
            (client_id, email.lower().strip()))
        return row is not None


def filter_suppressed(client_id: int, emails: list[str]) -> list[str]:
    """Return list of emails that are NOT suppressed."""
    if not emails:
        return []
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT email FROM email_suppressions WHERE client_id = %s",
            (client_id,))
    suppressed = {r["email"] for r in rows}
    return [e for e in emails if e.lower().strip() not in suppressed]


def get_suppressions(client_id: int) -> list[dict]:
    """Get all suppressed emails for a client."""
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM email_suppressions WHERE client_id = %s ORDER BY created_at DESC",
            (client_id,))


def remove_suppression(client_id: int, email: str) -> bool:
    """Remove an email from the suppression list."""
    with get_db() as db:
        _exec(db, "DELETE FROM email_suppressions WHERE client_id = %s AND email = %s",
              (client_id, email.lower().strip()))
        return True


def get_contact_groups(client_id: int) -> list[dict]:
    """Return all unique tags (groups) with contact count."""
    all_contacts = get_contacts(client_id)
    groups: dict[str, int] = {}
    for c in all_contacts:
        if c.get("tags"):
            for tg in c["tags"].split(","):
                tg = tg.strip()
                if tg:
                    groups[tg] = groups.get(tg, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(groups.items())]


def get_contacts_by_group(client_id: int, group: str) -> list[dict]:
    """Return contacts that have a specific tag/group."""
    return get_contacts(client_id, tag=group)


def get_contact_email_history(client_id: int, email: str, limit: int = 20) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT id, subject, body_preview, received_at, priority, category, ai_summary
            FROM mail_inbox WHERE client_id = %s AND from_email = %s
            ORDER BY received_at DESC LIMIT %s
        """, (client_id, email, limit))


def mark_contact_emails_priority(client_id: int, email: str, priority: str) -> int:
    allowed = {"urgent", "important", "normal", "low"}
    if priority not in allowed:
        return 0
    with get_db() as db:
        cur = _exec(db,
            "UPDATE mail_inbox SET priority = %s WHERE client_id = %s AND from_email = %s",
            (priority, client_id, email))
        return cur.rowcount


# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------

def invite_team_member(owner_id: int, member_email: str, role: str = "member",
                       campaign_id: int | None = None) -> dict:
    import secrets
    token = secrets.token_urlsafe(32)
    with get_db() as db:
        if campaign_id:
            existing = _fetchone(db,
                "SELECT id, status FROM team_members WHERE owner_id = %s AND member_email = %s AND campaign_id = %s",
                (owner_id, member_email, campaign_id))
        else:
            existing = _fetchone(db,
                "SELECT id, status FROM team_members WHERE owner_id = %s AND member_email = %s AND campaign_id IS NULL",
                (owner_id, member_email))
        if existing:
            return {"error": "Already invited", "status": existing["status"]}
        _exec(db,
            "INSERT INTO team_members (owner_id, member_email, role, invite_token, campaign_id) VALUES (%s, %s, %s, %s, %s)",
            (owner_id, member_email, role, token, campaign_id))
        return {"token": token, "email": member_email, "role": role}


def accept_team_invite(token: str, client_id: int) -> bool:
    """Accept a team invite using the token. Links the member_client_id."""
    with get_db() as db:
        row = _fetchone(db,
            "SELECT id, status FROM team_members WHERE invite_token = %s", (token,))
        if not row or row["status"] != "pending":
            return False
        now = _now_expr()
        _exec(db,
            f"UPDATE team_members SET status = 'active', member_client_id = %s, accepted_at = {now} WHERE id = %s",
            (client_id, row["id"]))
        return True


def remove_team_member(member_id: int, owner_id: int) -> bool:
    """Remove a team member (only owner can remove)."""
    with get_db() as db:
        cur = _exec(db,
            "DELETE FROM team_members WHERE id = %s AND owner_id = %s",
            (member_id, owner_id))
        return cur.rowcount > 0


def get_team_members(owner_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT tm.*, c.name as member_name, camp.name as campaign_name
            FROM team_members tm
            LEFT JOIN clients c ON c.id = tm.member_client_id
            LEFT JOIN campaigns camp ON camp.id = tm.campaign_id
            WHERE tm.owner_id = %s
            ORDER BY tm.invited_at DESC
        """, (owner_id,))


def get_my_team_memberships(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT tm.*, owner.name as owner_name, owner.email as owner_email,
                   camp.name as campaign_name
            FROM team_members tm
            JOIN clients owner ON owner.id = tm.owner_id
            LEFT JOIN campaigns camp ON camp.id = tm.campaign_id
            WHERE tm.member_client_id = %s AND tm.status = 'active'
            ORDER BY tm.accepted_at DESC
        """, (client_id,))


def get_team_owner(client_id: int) -> int | None:
    with get_db() as db:
        row = _fetchone(db,
            "SELECT owner_id FROM team_members WHERE member_client_id = %s AND status = 'active' AND campaign_id IS NULL",
            (client_id,))
        return row["owner_id"] if row else None


def get_team_campaign_ids(client_id: int) -> list[int]:
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT campaign_id FROM team_members WHERE member_client_id = %s AND status = 'active' AND campaign_id IS NOT NULL",
            (client_id,))
        return [r["campaign_id"] for r in rows]


# ---------------------------------------------------------------------------
# Scheduled emails
# ---------------------------------------------------------------------------

def create_scheduled_email(client_id: int, to_email: str, subject: str, body: str,
                           scheduled_at: str, to_name: str = "",
                           reply_to_mail_id: int | None = None,
                           account_id: int | None = None) -> int:
    with get_db() as db:
        new_id = _insert_returning_id(
            db,
            """INSERT INTO scheduled_emails (client_id, to_email, to_name, subject, body,
                                             scheduled_at, reply_to_mail_id, account_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (client_id, to_email, to_name, subject, body, scheduled_at, reply_to_mail_id, account_id),
        )
        # Log insert so we can confirm it persisted
        print(f"[SCHED INSERT] id={new_id} to={to_email} at={scheduled_at} client={client_id} db={'PG' if _USE_PG else 'SQLite'} db_fingerprint={_db_fingerprint()}", flush=True)
        return new_id


def get_scheduled_emails(client_id: int, status: str | None = None) -> list[dict]:
    with get_db() as db:
        if status:
            return _fetchall(db,
                "SELECT * FROM scheduled_emails WHERE client_id = %s AND status = %s ORDER BY scheduled_at ASC",
                (client_id, status))
        return _fetchall(db,
            "SELECT * FROM scheduled_emails WHERE client_id = %s ORDER BY scheduled_at DESC",
            (client_id,))


def delete_scheduled_email(email_id: int, client_id: int) -> bool:
    with get_db() as db:
        _exec(db, "DELETE FROM scheduled_emails WHERE id = %s AND client_id = %s AND status = 'pending'",
              (email_id, client_id))
        return True


def get_due_scheduled_emails() -> list[dict]:
    with get_db() as db:
        if _USE_PG:
            # scheduled_at stores UTC text like '2026-04-11 06:30:00'
            # Use pure TEXT comparison — ISO dates sort lexicographically.
            # This avoids all ::timestamp / AT TIME ZONE cast issues.
            return _fetchall(db, """
                SELECT * FROM scheduled_emails
                WHERE status = 'pending'
                  AND scheduled_at <= TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                ORDER BY scheduled_at ASC
            """)
        else:
            return _fetchall(db, """
                SELECT * FROM scheduled_emails
                WHERE status = 'pending' AND scheduled_at <= datetime('now')
                ORDER BY scheduled_at ASC
            """)


def mark_scheduled_sent(email_id: int) -> bool:
    with get_db() as db:
        now = _now_expr()
        _exec(db, f"UPDATE scheduled_emails SET status = 'sent', sent_at = {now} WHERE id = %s",
              (email_id,))
        return True


def mark_scheduled_failed(email_id: int) -> bool:
    with get_db() as db:
        _exec(db, "UPDATE scheduled_emails SET status = 'failed' WHERE id = %s", (email_id,))
        return True


# ---------------------------------------------------------------------------
# Snooze processing
# ---------------------------------------------------------------------------

def process_snoozed_emails() -> int:
    with get_db() as db:
        now = _now_expr()
        cur = _exec(db, f"""
            UPDATE mail_inbox SET priority = 'important'
            WHERE snooze_until IS NOT NULL
              AND {_ts_cast('snooze_until')} <= {now}
              AND priority NOT IN ('urgent', 'important')
        """)
        return cur.rowcount


# ---------------------------------------------------------------------------
# Billing & Usage
# ---------------------------------------------------------------------------

def get_subscription(client_id: int) -> dict:
    with get_db() as db:
        row = _fetchone(db, "SELECT * FROM subscriptions WHERE client_id = %s", (client_id,))
        if row:
            return row
        _exec(db, "INSERT INTO subscriptions (client_id, plan) VALUES (%s, 'free')", (client_id,))
        db.commit()
        row = _fetchone(db, "SELECT * FROM subscriptions WHERE client_id = %s", (client_id,))
        return row


def update_subscription(client_id: int, **fields) -> bool:
    allowed = {"plan", "stripe_customer_id", "stripe_subscription_id", "status",
               "current_period_start", "current_period_end"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    now = _now_expr()
    set_parts = []
    vals = []
    for k, v in updates.items():
        set_parts.append(f"{k} = %s")
        vals.append(v)
    set_parts.append(f"updated_at = {now}")
    vals.append(client_id)
    with get_db() as db:
        _exec(db, f"UPDATE subscriptions SET {', '.join(set_parts)} WHERE client_id = %s", vals)
        return True


def get_subscription_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM subscriptions WHERE stripe_customer_id = %s",
                         (stripe_customer_id,))


def get_subscription_by_stripe_sub(stripe_sub_id: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM subscriptions WHERE stripe_subscription_id = %s",
                         (stripe_sub_id,))


def _current_month() -> str:
    from datetime import date
    return date.today().strftime("%Y-%m")


def get_usage(client_id: int) -> dict:
    month = _current_month()
    with get_db() as db:
        row = _fetchone(db, "SELECT * FROM usage_tracking WHERE client_id = %s AND month = %s",
                        (client_id, month))
        if row:
            return row
        _exec(db, "INSERT INTO usage_tracking (client_id, month) VALUES (%s, %s)",
              (client_id, month))
        db.commit()
        row = _fetchone(db, "SELECT * FROM usage_tracking WHERE client_id = %s AND month = %s",
                        (client_id, month))
        return row


def increment_usage(client_id: int, field: str, amount: int = 1) -> int:
    allowed = {"emails_sent", "mail_hub_syncs", "ai_classifications"}
    if field not in allowed:
        return 0
    month = _current_month()
    with get_db() as db:
        if _USE_PG:
            _exec(db, f"""
                INSERT INTO usage_tracking (client_id, month, {field})
                VALUES (%s, %s, %s)
                ON CONFLICT(client_id, month) DO UPDATE SET {field} = usage_tracking.{field} + %s
            """, (client_id, month, amount, amount))
        else:
            _exec(db, f"""
                INSERT INTO usage_tracking (client_id, month, {field})
                VALUES (%s, %s, %s)
                ON CONFLICT(client_id, month) DO UPDATE SET {field} = {field} + %s
            """, (client_id, month, amount, amount))
        db.commit()
        val = _fetchval(db, f"SELECT {field} FROM usage_tracking WHERE client_id = %s AND month = %s",
                        (client_id, month))
        return val or 0


def check_limit(client_id: int, field: str) -> tuple[bool, int, int]:
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
