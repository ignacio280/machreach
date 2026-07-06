"""
PostgreSQL database — campaigns, contacts, emails, tracking.
Migrated from SQLite.  Uses DATABASE_URL env var (Render Postgres format).
Falls back to SQLite via DATABASE_PATH when DATABASE_URL is empty (local dev).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from outreach.config import DATABASE_URL, ENCRYPTION_KEY, SECRET_KEY

# ---------------------------------------------------------------------------
# Detect engine: postgres vs sqlite fallback
# ---------------------------------------------------------------------------
_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import os
    import threading
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    import psycopg2.pool

    # Connection pool — one per process (gunicorn worker / the cron worker).
    # Without this, every get_db() opened a fresh TCP+TLS+auth connection to
    # the remote Postgres, which is slow per request and churns connections
    # (a leading cause of connection-limit exhaustion / "down with no users").
    # Sized small so web (threads) + worker stay well under the plan's limit.
    _POOL = None
    _POOL_LOCK = threading.Lock()
    _POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
    # Headroom for gunicorn's request threads + background daemon threads
    # (Canvas sync, quiz/notes generation) that also hit the DB. Still bounded,
    # so a runaway can't exhaust the shared Postgres connection limit.
    _POOL_MAX = int(os.getenv("DB_POOL_MAX", "8"))

    def _get_pool():
        global _POOL
        if _POOL is None:
            with _POOL_LOCK:
                if _POOL is None:
                    _POOL = psycopg2.pool.ThreadedConnectionPool(
                        _POOL_MIN, _POOL_MAX, DATABASE_URL,
                        cursor_factory=psycopg2.extras.RealDictCursor,
                        # Keepalives so the OS/Postgres drop dead sockets
                        # instead of handing us a silently-closed connection.
                        keepalives=1, keepalives_idle=30,
                        keepalives_interval=10, keepalives_count=3,
                    )
        return _POOL
else:
    import sqlite3
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
        pool = _get_pool()
        conn = pool.getconn()
        # Discard a connection the server already closed under us.
        if getattr(conn, "closed", 0):
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        broken = False
        try:
            yield conn
            conn.commit()
        except Exception as e:
            # Connection-level failures: discard the connection rather than
            # returning a poisoned one to the pool. App-level errors just roll back.
            broken = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) \
                or bool(getattr(conn, "closed", 0))
            if not broken:
                try:
                    conn.rollback()
                except Exception:
                    broken = True
            raise
        finally:
            try:
                pool.putconn(conn, close=broken)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
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
    account_type TEXT DEFAULT 'business',
    is_admin    INTEGER DEFAULT 0,
    email_verified INTEGER DEFAULT 0,
    session_version INTEGER DEFAULT 0,
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
    account_type TEXT DEFAULT 'business',
    is_admin    INTEGER DEFAULT 0,
    email_verified INTEGER DEFAULT 0,
    session_version INTEGER DEFAULT 0,
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
    init_async_jobs_table()
    print("Database initialized.")


def _run_migrations():
    """Add columns that may be missing from older schemas."""
    migrations = [
        ("clients", "physical_address", "TEXT DEFAULT ''"),
        ("clients", "email_verified", "INTEGER DEFAULT 0"),
        ("clients", "account_type", "TEXT DEFAULT 'business'"),
        ("clients", "session_version", "INTEGER DEFAULT 0"),
        ("team_members", "campaign_id", "INTEGER REFERENCES campaigns(id)"),
    ]
    # Each migration runs in its own connection so a failed ALTER TABLE
    # (column already exists) doesn't poison the PG transaction for the rest.
    for table, col, col_type in migrations:
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # column already exists
    try:
        with get_db() as db:
            _exec(db, "CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_email_lower ON clients (LOWER(email))")
    except Exception:
        pass  # Older data may contain case-only duplicates; leave startup healthy.


def init_async_jobs_table():
    """Create the shared background-job status table."""
    created_at = _now_expr()
    if _USE_PG:
        sql = f"""
            CREATE TABLE IF NOT EXISTS async_jobs (
                job_type     TEXT NOT NULL,
                job_key      TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'idle',
                progress     TEXT DEFAULT '',
                input_json   TEXT DEFAULT '{{}}',
                payload_json TEXT DEFAULT '{{}}',
                error        TEXT DEFAULT '',
                created_at   TIMESTAMP DEFAULT {created_at},
                updated_at   TIMESTAMP DEFAULT {created_at},
                PRIMARY KEY (job_type, job_key)
            )
        """
    else:
        sql = f"""
            CREATE TABLE IF NOT EXISTS async_jobs (
                job_type     TEXT NOT NULL,
                job_key      TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'idle',
                progress     TEXT DEFAULT '',
                input_json   TEXT DEFAULT '{{}}',
                payload_json TEXT DEFAULT '{{}}',
                error        TEXT DEFAULT '',
                created_at   TEXT DEFAULT ({created_at}),
                updated_at   TEXT DEFAULT ({created_at}),
                PRIMARY KEY (job_type, job_key)
            )
        """
    with get_db() as db:
        _exec(db, sql)
        _exec(db, "CREATE INDEX IF NOT EXISTS idx_async_jobs_updated ON async_jobs(updated_at)")
    try:
        with get_db() as db:
            _exec(db, "ALTER TABLE async_jobs ADD COLUMN input_json TEXT DEFAULT '{}'")
    except Exception:
        pass


def set_async_job_status(job_type: str, job_key: str, status: str, progress: str = "", payload=None, error: str = ""):
    """Persist a background job's latest visible status."""
    payload_json = json.dumps(payload or {}, separators=(",", ":"))
    now = _now_expr()
    with get_db() as db:
        if _USE_PG:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, payload_json, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, {now}, {now})
                ON CONFLICT (job_type, job_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    payload_json = EXCLUDED.payload_json,
                    error = EXCLUDED.error,
                    updated_at = {now}
            """, (job_type, str(job_key), status, progress, payload_json, error or ""))
        else:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, payload_json, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, {now}, {now})
                ON CONFLICT(job_type, job_key) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    payload_json = excluded.payload_json,
                    error = excluded.error,
                    updated_at = {now}
            """, (job_type, str(job_key), status, progress, payload_json, error or ""))


def _decode_json_dict(value) -> dict:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def enqueue_async_job(job_type: str, job_key: str, input_payload=None, progress: str = "Queued", visible_payload=None) -> dict:
    """Queue work for the background worker without exposing input_payload in status responses."""
    current = get_async_job_status(job_type, job_key)
    if current.get("status") in ("queued", "running", "sending"):
        return current

    input_json = json.dumps(input_payload or {}, separators=(",", ":"))
    payload_json = json.dumps(visible_payload or {}, separators=(",", ":"))
    now = _now_expr()
    with get_db() as db:
        if _USE_PG:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, input_json, payload_json, error, created_at, updated_at
                )
                VALUES (%s, %s, 'queued', %s, %s, %s, '', {now}, {now})
                ON CONFLICT (job_type, job_key) DO UPDATE SET
                    status = 'queued',
                    progress = EXCLUDED.progress,
                    input_json = EXCLUDED.input_json,
                    payload_json = EXCLUDED.payload_json,
                    error = '',
                    updated_at = {now}
            """, (job_type, str(job_key), progress, input_json, payload_json))
        else:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, input_json, payload_json, error, created_at, updated_at
                )
                VALUES (%s, %s, 'queued', %s, %s, %s, '', {now}, {now})
                ON CONFLICT(job_type, job_key) DO UPDATE SET
                    status = 'queued',
                    progress = excluded.progress,
                    input_json = excluded.input_json,
                    payload_json = excluded.payload_json,
                    error = '',
                    updated_at = {now}
            """, (job_type, str(job_key), progress, input_json, payload_json))
    return get_async_job_status(job_type, job_key)


def claim_async_jobs(job_type: str, limit: int = 1, progress: str = "Running") -> list[dict]:
    """Atomically claim queued jobs for a worker process."""
    now = _now_expr()
    limit = max(1, int(limit or 1))
    claimed = []
    with get_db() as db:
        if _USE_PG:
            rows = _exec(db, f"""
                WITH next_jobs AS (
                    SELECT job_type, job_key
                    FROM async_jobs
                    WHERE job_type = %s AND status = 'queued'
                    ORDER BY updated_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE async_jobs AS j
                SET status = 'running',
                    progress = %s,
                    error = '',
                    updated_at = {now}
                FROM next_jobs
                WHERE j.job_type = next_jobs.job_type
                  AND j.job_key = next_jobs.job_key
                RETURNING j.job_type, j.job_key, j.input_json
            """, (job_type, limit, progress)).fetchall()
            claimed = [dict(row) for row in rows]
        else:
            rows = _fetchall(db, """
                SELECT job_type, job_key, input_json
                FROM async_jobs
                WHERE job_type = %s AND status = 'queued'
                ORDER BY updated_at ASC
                LIMIT %s
            """, (job_type, limit))
            for row in rows:
                cur = _exec(db, f"""
                    UPDATE async_jobs
                    SET status = 'running',
                        progress = %s,
                        error = '',
                        updated_at = {now}
                    WHERE job_type = %s AND job_key = %s AND status = 'queued'
                """, (progress, row["job_type"], row["job_key"]))
                if cur.rowcount:
                    claimed.append(dict(row))

    for job in claimed:
        job["input"] = _decode_json_dict(job.get("input_json"))
        job.pop("input_json", None)
    return claimed


def _async_job_is_stale(updated_at, stale_after_seconds: int) -> bool:
    if not updated_at or stale_after_seconds <= 0:
        return False
    try:
        if isinstance(updated_at, datetime):
            now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()
            return (now - updated_at).total_seconds() > stale_after_seconds
        ts = str(updated_at).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            parsed = datetime.strptime(ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
        return (now - parsed).total_seconds() > stale_after_seconds
    except Exception:
        return False


def get_async_job_status(job_type: str, job_key: str, default=None, stale_after_seconds: int = 3600) -> dict:
    """Return a background job's latest status, merged with its JSON payload."""
    fallback = dict(default or {"status": "idle"})
    with get_db() as db:
        row = _fetchone(db, """
            SELECT status, progress, payload_json, error, updated_at
            FROM async_jobs
            WHERE job_type = %s AND job_key = %s
        """, (job_type, str(job_key)))
    if not row:
        return fallback

    status = {"status": row.get("status") or fallback.get("status", "idle")}
    if row.get("progress"):
        status["progress"] = row["progress"]

    status.update(_decode_json_dict(row.get("payload_json")))

    if status["status"] in ("queued", "running", "sending") and _async_job_is_stale(row.get("updated_at"), stale_after_seconds):
        status["status"] = "error"
        status["progress"] = "Background job was interrupted. Please try again."
        status["error"] = "Background job interrupted before it finished."
        return status

    if row.get("error"):
        status["error"] = row["error"]
    return status


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












# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

_TOKEN_HASH_PREFIX = "hmac_sha256:"


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _clean_auth_token(token: str) -> str:
    return (token or "").strip()


def _hash_auth_token(token: str) -> str:
    digest = hmac.new(
        SECRET_KEY.encode(),
        _clean_auth_token(token).encode(),
        hashlib.sha256,
    ).hexdigest()
    return _TOKEN_HASH_PREFIX + digest


def create_client(name: str, email: str, password_hash: str, business: str = "", account_type: str = "business") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO clients (name, email, password, business, account_type) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, _normalize_email(email), password_hash, business, account_type),
        )


def get_client_by_email(email: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM clients WHERE LOWER(email) = %s", (_normalize_email(email),))


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


def update_client_password(client_id: int, password_hash: str, bump_session_version: bool = True):
    with get_db() as db:
        if bump_session_version:
            _exec(db, "UPDATE clients SET password = %s, session_version = COALESCE(session_version, 0) + 1 WHERE id = %s",
                  (password_hash, client_id))
        else:
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






# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def create_reset_token(client_id: int, token: str, expires_at: str):
    with get_db() as db:
        _exec(db,
              "INSERT INTO password_reset_tokens (client_id, token, expires_at) VALUES (%s, %s, %s)",
              (client_id, _hash_auth_token(token), expires_at))


def get_valid_reset_token(token: str) -> dict | None:
    with get_db() as db:
        now = _now_expr()
        hashed = _hash_auth_token(token)
        rec = _fetchone(db,
            f"SELECT * FROM password_reset_tokens WHERE token = %s AND used = 0 AND expires_at > {now}",
            (hashed,))
        if rec:
            return rec
        return _fetchone(db,
            f"SELECT * FROM password_reset_tokens WHERE token = %s AND token NOT LIKE %s AND used = 0 AND expires_at > {now}",
            (_clean_auth_token(token), _TOKEN_HASH_PREFIX + "%"))


def mark_reset_token_used(token: str):
    with get_db() as db:
        _exec(db,
              "UPDATE password_reset_tokens SET used = 1 WHERE token = %s OR (token = %s AND token NOT LIKE %s)",
              (_hash_auth_token(token), _clean_auth_token(token), _TOKEN_HASH_PREFIX + "%"))


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

def create_verification_token(client_id: int, token: str, expires_at: str):
    with get_db() as db:
        _exec(db,
              "INSERT INTO email_verification_tokens (client_id, token, expires_at) VALUES (%s, %s, %s)",
              (client_id, _hash_auth_token(token), expires_at))


def get_valid_verification_token(token: str) -> dict | None:
    with get_db() as db:
        now = _now_expr()
        hashed = _hash_auth_token(token)
        rec = _fetchone(db,
            f"SELECT * FROM email_verification_tokens WHERE token = %s AND used = 0 AND expires_at > {now}",
            (hashed,))
        if rec:
            return rec
        return _fetchone(db,
            f"SELECT * FROM email_verification_tokens WHERE token = %s AND token NOT LIKE %s AND used = 0 AND expires_at > {now}",
            (_clean_auth_token(token), _TOKEN_HASH_PREFIX + "%"))


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








# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------





def get_campaigns(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM campaigns WHERE client_id = %s ORDER BY created_at DESC",
            (client_id,))










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






# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------









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









# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# A/B global stats
# ---------------------------------------------------------------------------



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








def get_mail_item(mail_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM mail_inbox WHERE id = %s AND client_id = %s",
                         (mail_id, client_id))






# ---------------------------------------------------------------------------
# Contacts Book (CRM)
# ---------------------------------------------------------------------------



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










# ---------------------------------------------------------------------------
# Email Suppressions (Global unsubscribe / CAN-SPAM)
# ---------------------------------------------------------------------------



def is_suppressed(client_id: int, email: str) -> bool:
    """Check if an email is on the global suppression list."""
    with get_db() as db:
        row = _fetchone(db,
            "SELECT 1 FROM email_suppressions WHERE client_id = %s AND email = %s",
            (client_id, email.lower().strip()))
        return row is not None












def get_contact_email_history(client_id: int, email: str, limit: int = 20) -> list[dict]:
    with get_db() as db:
        return _fetchall(db, """
            SELECT id, subject, body_preview, received_at, priority, category, ai_summary
            FROM mail_inbox WHERE client_id = %s AND from_email = %s
            ORDER BY received_at DESC LIMIT %s
        """, (client_id, email, limit))




# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------











def get_team_owner(client_id: int) -> int | None:
    with get_db() as db:
        row = _fetchone(db,
            "SELECT owner_id FROM team_members WHERE member_client_id = %s AND status = 'active' AND campaign_id IS NULL",
            (client_id,))
        return row["owner_id"] if row else None




# ---------------------------------------------------------------------------
# Scheduled emails
# ---------------------------------------------------------------------------







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
