"""
Core MachReach Uni persistence and cross-engine database helpers.
Uses DATABASE_URL env var (Render Postgres format).
Falls back to SQLite via DATABASE_PATH when DATABASE_URL is empty (local dev).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from machreach_core.config import DATABASE_URL, ENCRYPTION_KEY, SECRET_KEY

SCHEMA_VERSION = 4

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
    from machreach_core.config import DATABASE_PATH

# ---------------------------------------------------------------------------
# Fernet encryption for email account passwords at rest
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    import base64
    import hashlib
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


class CredentialDecryptionError(RuntimeError):
    """Encrypted credentials cannot be read with this deployment's key."""


def decrypt_password(ciphertext: str) -> str:
    # Historical local installs may still contain plaintext credentials. Fernet
    # tokens have a stable prefix; only those should ever be silently decrypted.
    if not str(ciphertext or "").startswith("gAAAA"):
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Stored credential cannot be decrypted; verify the shared ENCRYPTION_KEY"
        ) from exc


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
        conn.execute("PRAGMA busy_timeout=5000")
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
        params = tuple(
            value.isoformat(sep=" ") if isinstance(value, datetime) else value
            for value in params
        )
    cur = db.cursor()
    cur.execute(sql, params)
    return cur


def _is_duplicate_column_error(exc: Exception) -> bool:
    """Return True only for the expected idempotent ALTER TABLE failure."""
    return (
        exc.__class__.__name__ == "DuplicateColumn"
        or "duplicate column name" in str(exc).lower()
    )


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






















CREATE TABLE IF NOT EXISTS schema_metadata (
    component       TEXT PRIMARY KEY,
    version         INTEGER NOT NULL,
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_events (
    provider        TEXT NOT NULL,
    event_key       TEXT NOT NULL,
    event_name      TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'processing',
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_error      TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY(provider, event_key)
);

CREATE TABLE IF NOT EXISTS operational_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    source          TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_operational_events_type_created
    ON operational_events(event_type, created_at);
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






















CREATE TABLE IF NOT EXISTS schema_metadata (
    component       TEXT PRIMARY KEY,
    version         INTEGER NOT NULL,
    updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS webhook_events (
    provider        TEXT NOT NULL,
    event_key       TEXT NOT NULL,
    event_name      TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'processing',
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_error      TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY(provider, event_key)
);

CREATE TABLE IF NOT EXISTS operational_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    source          TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_operational_events_type_created
    ON operational_events(event_type, created_at);
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
    _archive_and_drop_retired_product_tables()
    with get_db() as db:
        legacy_calendar_table = "google_" + "calendar_tokens"
        _exec(db, f"DROP TABLE IF EXISTS {legacy_calendar_table}" + (" CASCADE" if _USE_PG else ""))
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET account_type = 'student' "
            "WHERE account_type IS NULL OR account_type <> 'student'",
        )
    _record_schema_version("core", SCHEMA_VERSION)
    print("Database initialized.")


_RETIRED_PRODUCT_TABLES = (
    "sent_emails",
    "email_sequences",
    "contacts",
    "campaigns",
    "scheduled_emails",
    "mail_inbox",
    "contacts_book",
    "email_accounts",
    "team_members",
    "email_suppressions",
    "email_daily_usage",
    "usage_tracking",
    "subscriptions",
)


def _archive_and_drop_retired_product_tables() -> None:
    """Retain only billing-cancellation IDs, then remove the retired product.

    Provider subscription IDs must survive long enough for the Uni worker to
    cancel them. No campaign, contact, mailbox, quota, or plan data remains
    after this migration.
    """
    with get_db() as db:
        _exec(
            db,
            "CREATE TABLE IF NOT EXISTS retired_billing_cancellations ("
            "subscription_id TEXT PRIMARY KEY, client_id INTEGER, "
            "status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, "
            "last_error TEXT DEFAULT '', updated_at TEXT)",
        )
        if _USE_PG:
            legacy_subscriptions = _fetchone(
                db,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'subscriptions'",
            )
        else:
            legacy_subscriptions = _fetchone(
                db,
                "SELECT name AS table_name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'subscriptions'",
            )
        if legacy_subscriptions:
            _exec(
                db,
                "INSERT INTO retired_billing_cancellations "
                "(subscription_id, client_id, status, attempts, last_error) "
                "SELECT stripe_subscription_id, client_id, 'pending', 0, '' "
                "FROM subscriptions "
                "WHERE COALESCE(stripe_subscription_id, '') <> '' "
                "ON CONFLICT(subscription_id) DO NOTHING",
            )
        for table in _RETIRED_PRODUCT_TABLES:
            _exec(db, f"DROP TABLE IF EXISTS {table}" + (" CASCADE" if _USE_PG else ""))


def list_retired_billing_cancellations(limit: int = 25) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT subscription_id, client_id, status, attempts "
            "FROM retired_billing_cancellations "
            "WHERE status IN ('pending', 'failed') AND attempts < 10 "
            "ORDER BY attempts, subscription_id LIMIT %s",
            (max(1, min(int(limit or 25), 100)),),
        )


def is_retired_billing_subscription(subscription_id: str) -> bool:
    """Return whether a provider subscription was archived during product retirement."""
    normalized_id = str(subscription_id or "").strip()
    if not normalized_id:
        return False
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT 1 AS found FROM retired_billing_cancellations "
            "WHERE subscription_id = %s",
            (normalized_id,),
        )
    return row is not None


def finish_retired_billing_cancellation(subscription_id: str, *, error: str = "") -> None:
    status = "failed" if error else "cancelled"
    with get_db() as db:
        _exec(
            db,
            f"UPDATE retired_billing_cancellations SET status = %s, "
            f"attempts = attempts + 1, last_error = %s, updated_at = {_now_expr()} "
            "WHERE subscription_id = %s",
            (status, str(error or "")[:500], subscription_id),
        )


def _record_schema_version(component: str, version: int) -> None:
    now = _now_expr()
    with get_db() as db:
        if _USE_PG:
            _exec(db, f"""
                INSERT INTO schema_metadata (component, version, updated_at)
                VALUES (%s, %s, {now})
                ON CONFLICT(component) DO UPDATE SET
                    version = EXCLUDED.version,
                    updated_at = {now}
            """, (component, version))
        else:
            _exec(db, f"""
                INSERT INTO schema_metadata (component, version, updated_at)
                VALUES (%s, %s, {now})
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version,
                    updated_at = {now}
            """, (component, version))


def check_schema_readiness() -> dict:
    """Raise when the deployed schema is incomplete or behind this code."""
    from student.db import STUDENT_SCHEMA_VERSION

    with get_db() as db:
        versions = {
            "core": SCHEMA_VERSION,
            "student": STUDENT_SCHEMA_VERSION,
        }
        for component, expected in versions.items():
            version = _fetchval(
                db,
                "SELECT version FROM schema_metadata WHERE component = %s",
                (component,),
            )
            if int(version or 0) != expected:
                raise RuntimeError("database schema version is not current")
        # Verify columns used by authentication, billing events, async student
        # jobs, and student data rather than merely checking connectivity.
        _exec(db, "SELECT session_version, account_type FROM clients LIMIT 0")
        _exec(db, "SELECT status, attempts FROM async_jobs LIMIT 0")
        _exec(db, "SELECT provider, event_key, status FROM webhook_events LIMIT 0")
        _exec(db, "SELECT event_type, created_at FROM operational_events LIMIT 0")
        _exec(db, "SELECT subscription_id, status FROM retired_billing_cancellations LIMIT 0")
        _exec(db, "SELECT client_id, name FROM student_courses LIMIT 0")
        _exec(db, "SELECT client_id, coins, coin_debt FROM student_wallet LIMIT 0")
        _exec(
            db,
            "SELECT coins_credited, coins_reversed, refunded_amount, provider_total "
            "FROM student_coin_pack_orders LIMIT 0",
        )
    return {"versions": versions}


def claim_webhook_event(provider: str, event_key: str, event_name: str = "") -> dict:
    """Claim one provider event; failed events remain retryable."""
    now = _now_expr()
    with get_db() as db:
        if _USE_PG:
            inserted = _fetchone(
                db,
                f"INSERT INTO webhook_events "
                f"(provider, event_key, event_name, status, created_at, updated_at) "
                f"VALUES (%s, %s, %s, 'processing', {now}, {now}) "
                "ON CONFLICT(provider, event_key) DO NOTHING RETURNING status",
                (provider, event_key, event_name),
            )
            if inserted:
                return {"claimed": True, "status": "processing"}
        else:
            db.execute("BEGIN IMMEDIATE")
        existing = _fetchone(
            db,
            "SELECT status, attempts FROM webhook_events WHERE provider = %s AND event_key = %s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (provider, event_key),
        )
        if existing and existing["status"] != "failed":
            return {"claimed": False, "status": existing["status"]}
        if existing:
            _exec(
                db,
                f"UPDATE webhook_events SET status = 'processing', attempts = attempts + 1, "
                f"event_name = %s, last_error = '', updated_at = {now} "
                "WHERE provider = %s AND event_key = %s",
                (event_name, provider, event_key),
            )
        elif not _USE_PG:
            _exec(
                db,
                f"INSERT INTO webhook_events "
                f"(provider, event_key, event_name, status, created_at, updated_at) "
                f"VALUES (%s, %s, %s, 'processing', {now}, {now})",
                (provider, event_key, event_name),
            )
        return {"claimed": True, "status": "processing"}


def finish_webhook_event(provider: str, event_key: str, *, error: str = "") -> None:
    now = _now_expr()
    status = "failed" if error else "succeeded"
    with get_db() as db:
        _exec(
            db,
            f"UPDATE webhook_events SET status = %s, last_error = %s, updated_at = {now} "
            "WHERE provider = %s AND event_key = %s",
            (status, str(error or "")[:500], provider, event_key),
        )


def settle_unmatchable_order_events() -> int:
    """Close out order events the subscription handler could never have accepted.

    Until the handler learned to read an order payload's product and variant
    from `first_order_item`, every `order_created` that belonged to a Plus
    checkout was rejected as `unapproved-subscription-variant` — not because the
    variant was wrong, but because the field it looked in does not exist on an
    order. No order event grants anything (the entitlement rides on
    `subscription_created`), and the provider stops retrying after three
    attempts, so those rows sit at `failed` forever and hold
    /health/operations degraded.

    Scoped deliberately: only order events, only that error. A subscription
    payload rejected for a genuinely unapproved variant is a real alert and is
    left exactly where it is. Idempotent — once the rows are `abandoned` there
    is nothing left to match.
    """
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT event_key FROM webhook_events "
            "WHERE provider = 'lemonsqueezy' AND status = 'failed' "
            "AND event_name IN ('order_created', 'order_refunded') "
            "AND last_error LIKE %s",
            ("%unapproved-subscription-variant%",),
        )
        if not rows:
            return 0
        keys = [row["event_key"] for row in rows]
        _exec(
            db,
            "UPDATE webhook_events SET status = 'abandoned' "
            "WHERE provider = 'lemonsqueezy' AND event_key IN ("
            + ",".join(["%s"] * len(keys))
            + ")",
            tuple(keys),
        )
    record_operational_event("billing_webhook_abandoned", "unmatchable_order_event")
    return len(keys)


def record_operational_event(event_type: str, source: str = "") -> None:
    """Record a non-sensitive operational signal for health checks and alerts."""
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO operational_events (event_type, source) VALUES (%s, %s)",
            (str(event_type or "unknown")[:80], str(source or "")[:80]),
        )


def _run_migrations():
    """Add columns that may be missing from older schemas."""
    migrations = [
        ("clients", "physical_address", "TEXT DEFAULT ''"),
        ("clients", "email_verified", "INTEGER DEFAULT 0"),
        ("clients", "account_type", "TEXT DEFAULT 'student'"),
        ("clients", "session_version", "INTEGER DEFAULT 0"),
    ]
    # Each migration runs in its own connection so a failed ALTER TABLE
    # (column already exists) doesn't poison the PG transaction for the rest.
    for table, col, col_type in migrations:
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
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
                attempts     INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
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
                attempts     INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
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
    except Exception as exc:
        if not _is_duplicate_column_error(exc):
            raise
    for column, spec in [
        ("attempts", "INTEGER DEFAULT 0"),
        ("max_attempts", "INTEGER DEFAULT 3"),
    ]:
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE async_jobs ADD COLUMN {column} {spec}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise


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


def enqueue_async_job(job_type: str, job_key: str, input_payload=None, progress: str = "Queued", visible_payload=None, max_attempts: int = 3) -> dict:
    """Queue work for the background worker without exposing input_payload in status responses."""
    current = get_async_job_status(job_type, job_key)
    if current.get("status") in ("queued", "running", "sending"):
        return current

    input_json = json.dumps(input_payload or {}, separators=(",", ":"))
    payload_json = json.dumps(visible_payload or {}, separators=(",", ":"))
    max_attempts = max(1, int(max_attempts or 1))
    now = _now_expr()
    with get_db() as db:
        if _USE_PG:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, input_json, payload_json, error,
                    attempts, max_attempts, created_at, updated_at
                )
                VALUES (%s, %s, 'queued', %s, %s, %s, '', 0, %s, {now}, {now})
                ON CONFLICT (job_type, job_key) DO UPDATE SET
                    status = 'queued',
                    progress = EXCLUDED.progress,
                    input_json = EXCLUDED.input_json,
                    payload_json = EXCLUDED.payload_json,
                    error = '',
                    attempts = 0,
                    max_attempts = EXCLUDED.max_attempts,
                    updated_at = {now}
            """, (job_type, str(job_key), progress, input_json, payload_json, max_attempts))
        else:
            _exec(db, f"""
                INSERT INTO async_jobs (
                    job_type, job_key, status, progress, input_json, payload_json, error,
                    attempts, max_attempts, created_at, updated_at
                )
                VALUES (%s, %s, 'queued', %s, %s, %s, '', 0, %s, {now}, {now})
                ON CONFLICT(job_type, job_key) DO UPDATE SET
                    status = 'queued',
                    progress = excluded.progress,
                    input_json = excluded.input_json,
                    payload_json = excluded.payload_json,
                    error = '',
                    attempts = 0,
                    max_attempts = excluded.max_attempts,
                    updated_at = {now}
            """, (job_type, str(job_key), progress, input_json, payload_json, max_attempts))
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
                    attempts = COALESCE(j.attempts, 0) + 1,
                    updated_at = {now}
                FROM next_jobs
                WHERE j.job_type = next_jobs.job_type
                  AND j.job_key = next_jobs.job_key
                RETURNING j.job_type, j.job_key, j.input_json, j.attempts, j.max_attempts
            """, (job_type, limit, progress)).fetchall()
            claimed = [dict(row) for row in rows]
        else:
            rows = _fetchall(db, """
                SELECT job_type, job_key, input_json, COALESCE(attempts, 0) AS attempts, COALESCE(max_attempts, 3) AS max_attempts
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
                        attempts = COALESCE(attempts, 0) + 1,
                        updated_at = {now}
                    WHERE job_type = %s AND job_key = %s AND status = 'queued'
                """, (progress, row["job_type"], row["job_key"]))
                if cur.rowcount:
                    claimed_job = dict(row)
                    claimed_job["attempts"] = int(claimed_job.get("attempts") or 0) + 1
                    claimed.append(claimed_job)

    for job in claimed:
        job["input"] = _decode_json_dict(job.get("input_json"))
        job.pop("input_json", None)
    return claimed


def recover_stale_async_jobs(stale_after_seconds: int = 3600) -> int:
    """Requeue interrupted jobs until their retry budget is exhausted."""
    stale_after_seconds = max(60, int(stale_after_seconds or 3600))
    now = _now_expr()
    if _USE_PG:
        stale_sql = f"updated_at < NOW() - INTERVAL '{stale_after_seconds} seconds'"
    else:
        stale_sql = f"updated_at < datetime('now', '-{stale_after_seconds} seconds')"
    with get_db() as db:
        exhausted = _exec(db, f"""
            UPDATE async_jobs
            SET status = 'error',
                progress = 'Background job was interrupted and exhausted its retry budget.',
                error = 'Interrupted worker job',
                updated_at = {now}
            WHERE status IN ('running', 'sending')
              AND COALESCE(attempts, 0) >= COALESCE(max_attempts, 3)
              AND {stale_sql}
        """).rowcount
        requeued = _exec(db, f"""
            UPDATE async_jobs
            SET status = 'queued',
                progress = 'Recovered after a worker interruption.',
                error = '',
                updated_at = {now}
            WHERE status IN ('running', 'sending')
              AND COALESCE(attempts, 0) < COALESCE(max_attempts, 3)
              AND {stale_sql}
              AND job_type <> 'worker_heartbeat'
        """).rowcount
    return int(exhausted or 0) + int(requeued or 0)


def fail_async_job(job_type: str, job_key: str, error: str, progress: str = "", payload=None, retry: bool = True) -> dict:
    """Record a job failure, requeueing it if retryable attempts remain."""
    payload_json = json.dumps(payload or {}, separators=(",", ":"))
    now = _now_expr()
    with get_db() as db:
        row = _fetchone(db, """
            SELECT COALESCE(attempts, 0) AS attempts, COALESCE(max_attempts, 3) AS max_attempts
            FROM async_jobs
            WHERE job_type = %s AND job_key = %s
        """, (job_type, str(job_key))) or {"attempts": 0, "max_attempts": 3}
        attempts = int(row.get("attempts") or 0)
        max_attempts = max(1, int(row.get("max_attempts") or 3))
        should_retry = bool(retry and attempts < max_attempts)
        status = "queued" if should_retry else "error"
        message = progress or str(error or "Job failed.")
        if should_retry:
            message = f"{message} Retrying attempt {attempts + 1} of {max_attempts}."
        _exec(db, f"""
            UPDATE async_jobs
            SET status = %s,
                progress = %s,
                payload_json = %s,
                error = %s,
                updated_at = {now}
            WHERE job_type = %s AND job_key = %s
        """, (status, message, payload_json, str(error or ""), job_type, str(job_key)))
    return get_async_job_status(job_type, job_key)


def retry_async_job(job_type: str, job_key: str) -> bool:
    """Manually requeue a failed job from the admin console."""
    now = _now_expr()
    with get_db() as db:
        cur = _exec(db, f"""
            UPDATE async_jobs
            SET status = 'queued',
                progress = 'Manually queued for retry.',
                error = '',
                attempts = 0,
                updated_at = {now}
            WHERE job_type = %s
              AND job_key = %s
              AND status = 'error'
        """, (job_type, str(job_key)))
        return bool(cur.rowcount)


def list_async_jobs(limit: int = 80, job_type: str | None = None) -> list[dict]:
    """List recent async jobs for admin/debug views."""
    limit = max(1, min(int(limit or 80), 200))
    where = "WHERE job_type = %s" if job_type else "WHERE job_type <> 'worker_heartbeat'"
    params = (job_type, limit) if job_type else (limit,)
    with get_db() as db:
        rows = _fetchall(db, f"""
            SELECT job_type, job_key, status, progress, error,
                   COALESCE(attempts, 0) AS attempts,
                   COALESCE(max_attempts, 3) AS max_attempts,
                   payload_json,
                   created_at,
                   updated_at
            FROM async_jobs
            {where}
            ORDER BY updated_at DESC
            LIMIT %s
        """, params)
    for row in rows:
        row["payload"] = _decode_json_dict(row.get("payload_json"))
        row.pop("payload_json", None)
    return rows


def record_worker_heartbeat(worker_name: str = "machreach-worker") -> None:
    set_async_job_status(
        "worker_heartbeat",
        worker_name,
        "running",
        progress="Worker heartbeat",
        payload={"worker": worker_name},
    )


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


def create_client(name: str, email: str, password_hash: str, business: str = "", account_type: str = "student") -> int:
    """Create a MachReach Uni student account.

    ``account_type`` remains in the signature for migration compatibility with
    older callers, but legacy product account types are no longer accepted.
    """
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO clients (name, email, password, business, account_type) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, _normalize_email(email), password_hash, "", "student"),
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
    """Atomically merge top-level legacy preferences.

    Subscription, referral, timezone, and reward state live in normalized
    tables. This merge keeps remaining cosmetic/security writers from erasing
    one another while legacy callers are retired.
    """
    try:
        parsed = json.loads(preferences or "{}")
        if not isinstance(parsed, dict):
            parsed = {}
        preferences = json.dumps(parsed, separators=(",", ":"))
    except (TypeError, ValueError):
        preferences = "{}"
    with get_db() as db:
        if _USE_PG:
            _exec(
                db,
                "UPDATE clients SET mail_preferences = "
                "(COALESCE(NULLIF(mail_preferences, ''), '{}')::jsonb "
                "|| %s::jsonb)::text WHERE id = %s",
                (preferences, client_id),
            )
        else:
            _exec(
                db,
                "UPDATE clients SET mail_preferences = json_patch("
                "CASE WHEN json_valid(mail_preferences) THEN mail_preferences "
                "ELSE '{}' END, %s) WHERE id = %s",
                (preferences, client_id),
            )


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
