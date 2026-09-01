"""
Configuration — loads environment, defines constants.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _is_compatible_local_database(path: Path) -> bool:
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            tables = {
                str(row[0])
                for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
    except (OSError, sqlite3.DatabaseError):
        return False
    return "clients" in tables and bool(
        tables & {"schema_metadata", "student_courses", "subscriptions"}
    )


def _migrate_compatible_local_database(target: Path) -> None:
    """Back up one unambiguous compatible local database to the current filename."""
    if target.exists() or not target.parent.exists():
        return
    candidates = [
        path
        for path in target.parent.glob("*.db")
        if path != target and _is_compatible_local_database(path)
    ]
    if len(candidates) != 1:
        return
    source = candidates[0]
    temporary_target = target.with_name(f".{target.name}.migrating")
    try:
        temporary_target.unlink(missing_ok=True)
        source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
            with closing(sqlite3.connect(temporary_target)) as target_db:
                source_db.backup(target_db)
                integrity = target_db.execute("PRAGMA integrity_check").fetchone()
                if integrity != ("ok",):
                    raise sqlite3.DatabaseError("local database backup failed integrity check")
        temporary_target.replace(target)
    except (OSError, sqlite3.DatabaseError):
        temporary_target.unlink(missing_ok=True)
        return
    for source_file in (source, Path(f"{source}-wal"), Path(f"{source}-shm")):
        try:
            source_file.unlink(missing_ok=True)
        except OSError:
            pass


# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# DKIM signing (optional — only needed if sending from your own mail server)
DKIM_PRIVATE_KEY = os.getenv("DKIM_PRIVATE_KEY", "")  # PEM-encoded private key (newlines as \n)
DKIM_SELECTOR = os.getenv("DKIM_SELECTOR", "default")  # e.g. "google", "default", "mail"
DKIM_DOMAIN = os.getenv("DKIM_DOMAIN", "")  # e.g. "yourcompany.com"

# IMAP (for reply detection — usually same credentials as SMTP)
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "") or SMTP_USER
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "") or SMTP_PASSWORD

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")
_configured_database_path = (os.getenv("DATABASE_PATH") or "").strip()
if _configured_database_path:
    DATABASE_PATH = Path(_configured_database_path)
else:
    DATABASE_PATH = BASE_DIR / "data" / "machreach.db"
    _migrate_compatible_local_database(DATABASE_PATH)

# App
_IS_PRODUCTION = bool(os.getenv("RENDER", "")) or any(
    (os.getenv(name, "") or "").strip().lower() in {"prod", "production"}
    for name in ("FLASK_ENV", "APP_ENV", "ENVIRONMENT")
)
_secret = os.getenv("SECRET_KEY", "")
if _IS_PRODUCTION and _secret in {"", "dev-secret-change-me", "change-me-to-a-random-string"}:
    raise RuntimeError("SECRET_KEY must be set in production")
SECRET_KEY = _secret or "dev-secret-change-me"
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
ADMIN_ACTION_SECRET = os.getenv("ADMIN_ACTION_SECRET", "")
OPERATIONS_SECRET = os.getenv("OPERATIONS_SECRET", "") or ADMIN_ACTION_SECRET
if _IS_PRODUCTION and not OPERATIONS_SECRET:
    raise RuntimeError("OPERATIONS_SECRET must be set in production")

# Canvas OAuth. These values come from a Canvas Developer Key. The redirect
# URI configured in Canvas must be: {BASE_URL}/canvas/oauth/callback
CANVAS_OAUTH_CLIENT_ID = os.getenv("CANVAS_OAUTH_CLIENT_ID", "")
CANVAS_OAUTH_CLIENT_SECRET = os.getenv("CANVAS_OAUTH_CLIENT_SECRET", "")

# System/transactional email sender (verification, password reset, etc.)
# This should be support@machreach.com or noreply@machreach.com
SYSTEM_FROM_EMAIL = os.getenv("SYSTEM_FROM_EMAIL", "") or os.getenv("SMTP_USER", "")
SYSTEM_FROM_NAME = os.getenv("SYSTEM_FROM_NAME", "MachReach")
# System SMTP credentials — authenticate as the support account, not personal Gmail
# Falls back to global SMTP_USER/SMTP_PASSWORD if not set
SYSTEM_SMTP_USER = os.getenv("SYSTEM_SMTP_USER", "") or SYSTEM_FROM_EMAIL
SYSTEM_SMTP_PASSWORD = os.getenv("SYSTEM_SMTP_PASSWORD", "") or os.getenv("SMTP_PASSWORD", "")

# Encryption key for email account passwords at rest (Fernet, 32-byte base64)
_enc_key = os.getenv("ENCRYPTION_KEY", "")
if _IS_PRODUCTION and _enc_key in {
    "",
    "change-me-to-a-different-stable-random-string",
    "RGV2LWVuY3J5cHRpb24ta2V5LW5vdC1mb3ItcHJvZA==",
}:
    raise RuntimeError("ENCRYPTION_KEY must be set in production for stable encrypted credentials")
ENCRYPTION_KEY = _enc_key or "RGV2LWVuY3J5cHRpb24ta2V5LW5vdC1mb3ItcHJvZA=="

# Sending limits
FOLLOWUP_DELAY_DAYS = [3, 7, 14]  # Days after initial email for follow-ups
ACADEMIC_UNIVERSITY_CHANGE_COOLDOWN_DAYS = max(
    0, int(os.getenv("ACADEMIC_UNIVERSITY_CHANGE_COOLDOWN_DAYS", "30"))
)

# Apollo.io (prospect finder — free tier: 10k credits/month)
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")

# ── Lemon Squeezy (billing) ──────────────────────────────────────────
# Hosted-checkout subscriptions + one-time orders. We send custom_data on
# every checkout so the webhook can route the event back to the right
# user / purpose / plan. Configure the variant IDs in the LS dashboard,
# then paste them as env vars.
LEMON_SQUEEZY_API_KEY        = os.getenv("LEMON_SQUEEZY_API_KEY", "")
LEMON_SQUEEZY_STORE_ID       = os.getenv("LEMON_SQUEEZY_STORE_ID", "")
LEMON_SQUEEZY_TEST_API_KEY   = os.getenv("LEMON_SQUEEZY_TEST_API_KEY", "")
LEMON_SQUEEZY_TEST_STORE_ID  = os.getenv("LEMON_SQUEEZY_TEST_STORE_ID", "")
LEMON_SQUEEZY_WEBHOOK_SECRET = os.getenv("LEMON_SQUEEZY_WEBHOOK_SECRET", "")
LEMON_SQUEEZY_TEST_WEBHOOK_SECRET = os.getenv(
    "LEMON_SQUEEZY_TEST_WEBHOOK_SECRET", ""
)
LEMON_SQUEEZY_SANDBOX_CLIENT_ID = os.getenv(
    "LEMON_SQUEEZY_SANDBOX_CLIENT_ID", ""
)
LEMON_SQUEEZY_TEST_MODE      = os.getenv("LEMON_SQUEEZY_TEST_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}
# Student Plus subscription variant ID.
LS_PRODUCT_STUDENT_PLUS = os.getenv("LS_PRODUCT_STUDENT_PLUS", "")
LS_VARIANT_STUDENT_PLUS = os.getenv("LS_VARIANT_STUDENT_PLUS", "")
LS_TEST_PRODUCT_STUDENT_PLUS = os.getenv("LS_TEST_PRODUCT_STUDENT_PLUS", "")
LS_TEST_VARIANT_STUDENT_PLUS = os.getenv("LS_TEST_VARIANT_STUDENT_PLUS", "")
# Coin-pack one-time product and variant IDs (key matches student.db.COIN_PACKS).
LS_PRODUCT_COIN_SMALL  = os.getenv("LS_PRODUCT_COIN_SMALL", "")
LS_PRODUCT_COIN_MEDIUM = os.getenv("LS_PRODUCT_COIN_MEDIUM", "")
LS_PRODUCT_COIN_LARGE  = os.getenv("LS_PRODUCT_COIN_LARGE", "")
LS_PRODUCT_COIN_MEGA   = os.getenv("LS_PRODUCT_COIN_MEGA", "")
LS_PRODUCT_COIN_ULTRA  = os.getenv("LS_PRODUCT_COIN_ULTRA", "")
LS_VARIANT_COIN_SMALL  = os.getenv("LS_VARIANT_COIN_SMALL", "")
LS_VARIANT_COIN_MEDIUM = os.getenv("LS_VARIANT_COIN_MEDIUM", "")
LS_VARIANT_COIN_LARGE  = os.getenv("LS_VARIANT_COIN_LARGE", "")
LS_VARIANT_COIN_MEGA   = os.getenv("LS_VARIANT_COIN_MEGA", "")
LS_VARIANT_COIN_ULTRA  = os.getenv("LS_VARIANT_COIN_ULTRA", "")
LS_TEST_PRODUCT_COIN_SMALL  = os.getenv("LS_TEST_PRODUCT_COIN_SMALL", "")
LS_TEST_PRODUCT_COIN_MEDIUM = os.getenv("LS_TEST_PRODUCT_COIN_MEDIUM", "")
LS_TEST_PRODUCT_COIN_LARGE  = os.getenv("LS_TEST_PRODUCT_COIN_LARGE", "")
LS_TEST_PRODUCT_COIN_MEGA   = os.getenv("LS_TEST_PRODUCT_COIN_MEGA", "")
LS_TEST_PRODUCT_COIN_ULTRA  = os.getenv("LS_TEST_PRODUCT_COIN_ULTRA", "")
LS_TEST_VARIANT_COIN_SMALL  = os.getenv("LS_TEST_VARIANT_COIN_SMALL", "")
LS_TEST_VARIANT_COIN_MEDIUM = os.getenv("LS_TEST_VARIANT_COIN_MEDIUM", "")
LS_TEST_VARIANT_COIN_LARGE  = os.getenv("LS_TEST_VARIANT_COIN_LARGE", "")
LS_TEST_VARIANT_COIN_MEGA   = os.getenv("LS_TEST_VARIANT_COIN_MEGA", "")
LS_TEST_VARIANT_COIN_ULTRA  = os.getenv("LS_TEST_VARIANT_COIN_ULTRA", "")

# Sentry (error tracking — set SENTRY_DSN in production)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
LEADERBOARD_WINNERS_RECIPIENT = os.getenv("LEADERBOARD_WINNERS_RECIPIENT", "")

# ── Deploy identity ─────────────────────────────────────────────────────────
# One string that changes exactly once per deploy, used to key every client-side
# cache. It lives here rather than in app.py so the app-design shell can use the
# same value without importing the Flask app.
def resolve_deploy_version(commit: str | None, *, now=None) -> str:
    """Return the cache key for this deploy.

    The commit is authoritative when present. Without one there is no deploy to
    speak of, so fall back to a start-time stamp: it changes on every restart,
    which keeps local caches from masking edits.
    """
    from datetime import datetime, timezone

    short = (commit or "").strip()[:12]
    if short:
        return short
    moment = now or datetime.now(timezone.utc)
    return "dev-" + moment.strftime("%Y%m%d%H%M%S")


DEPLOY_VERSION = resolve_deploy_version(os.getenv("RENDER_GIT_COMMIT"))

# How old the worker heartbeat may be before /health/operations alerts. The
# long-running worker beat every minute, so two minutes was one missed beat.
# The cron-job worker beats once per scheduled run plus every 30s while busy,
# and a scheduled run can start late, so production sets this to 180.
WORKER_HEARTBEAT_STALE_SECONDS = max(
    60, int(os.getenv("WORKER_HEARTBEAT_STALE_SECONDS", "120") or 120)
)
