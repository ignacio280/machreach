"""
Flask web application for MachReach Uni.
"""
from __future__ import annotations

import bcrypt
import gzip
import hashlib
import hmac
import html as html_module
import logging
import os

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, flash, g, jsonify, make_response, redirect, render_template_string, request, session, url_for
from markupsafe import Markup

from machreach_core.config import ADMIN_ACTION_SECRET, OPERATIONS_SECRET, SECRET_KEY
from machreach_core.i18n import t, t_dict

# ── Sentry error tracking (production only — set SENTRY_DSN env var) ──
from machreach_core.config import SENTRY_DSN
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        # Performance tracing + profiling add CPU/memory overhead on a small
        # box; default them off (error reporting still works) and make them
        # env-tunable if you want to sample perf again.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0")),
        environment="production" if os.getenv("RENDER", "") else "development",
        release=(os.getenv("RENDER_GIT_COMMIT") or "").strip() or None,
        send_default_pii=False,
    )

from machreach_core.db import (
    create_client,
    get_client,
    get_client_by_email,
    get_valid_reset_token,
    get_valid_verification_token,
    init_db,
    mark_email_verified,
    mark_reset_token_used,
    update_client_password,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── Deploy identity ─────────────────────────────────────────────────────────
# One string that changes exactly once per deploy, used to key client-side
# caches. Render exposes the commit; locally there is no deploy, so fall back to
# the process start time, which changes on every restart and keeps dev honest.
from machreach_core.config import DEPLOY_VERSION, resolve_deploy_version  # noqa: E402,F401
# Matches:  const VERSION = 'mr-v3';   capturing the quotes so any authored
# value can be swapped without the file's own constant needing to be correct.
_SW_VERSION_RE = re.compile(r"""(const\s+VERSION\s*=\s*['"])([^'"]*)(['"])""")

# ── Security: session cookie hardening ──
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# HTTPS-only cookies in production (Render always runs behind TLS)
_IS_PRODUCTION = bool(os.getenv("RENDER", "")) or any(
    (os.getenv(name) or "").strip().lower() == "production"
    for name in ("FLASK_ENV", "APP_ENV", "ENVIRONMENT")
)
app.config["SESSION_COOKIE_SECURE"] = _IS_PRODUCTION
app.config["SESSION_COOKIE_NAME"] = "machreach_sess"
# Trust Render/Heroku-style proxy headers so secure-cookie detection works
if _IS_PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]
app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours max session
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # bounded document/file upload limit
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))

# ── Security: CSRF protection ──
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf  # noqa: E402
# Don't expire CSRF tokens faster than the session. The Flask-WTF default of
# 1 hour caused "The CSRF tokens do not match" when a page was left open and
# submitted later (very common on mobile). The token is still bound to the
# session secret, so CSRF protection is unchanged — only the early timeout is
# removed. It now lasts the session lifetime (24h cookie).
app.config["WTF_CSRF_TIME_LIMIT"] = None
csrf = CSRFProtect(app)


# Auth forms a rejected submit can be handed back to. Each is a GET page that
# mints a fresh token, so the redirect below leaves the person somewhere they
# can simply try again. Anything not listed here keeps the plain 400: a token
# mismatch on a state-changing route is not something to retry silently.
_CSRF_RETRY_PAGES = {
    "/register": "register",
    "/login": "login",
    "/forgot-password": "forgot_password",
}


@app.errorhandler(CSRFError)
def _handle_csrf_error(err):
    """Recover gracefully from stale auth forms instead of showing raw 400s.

    A missing session token means the browser sent no session at all — the
    cookie expired under a login page left open overnight, or a restored tab
    submitted a form older than the 24h cookie. The token itself is fine; there
    is simply no session to check it against, and the person has done nothing
    wrong. Showing them "Bad Request: The CSRF session token is missing." is a
    dead end whose only escape is retyping the URL, which is what /login did
    while /register recovered. The redirect issues a fresh session cookie, so
    the retry has a token that matches.
    """
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Session expired. Refresh and try again."}), 400
    reason = getattr(err, "description", "") or ""
    stale_token = (
        "session token is missing" in reason
        or "tokens do not match" in reason
        or "token has expired" in reason
    )
    retry_endpoint = _CSRF_RETRY_PAGES.get(request.path)
    if not (stale_token and retry_endpoint):
        return f"Bad Request: {reason}", 400
    try:
        _log_security("CSRF_REJECT", path=request.path, reason=reason)
    except Exception:
        pass
    msg = (
        "Your session expired. Please try again."
        if session.get("lang") == "en"
        else "Tu sesion expiro. Intentalo de nuevo."
    )
    flash(("error", msg))
    # Keep the referral the sign-up form was carrying, or the retry silently
    # drops the invite that brought them here.
    ref = (request.form.get("ref") or "").strip().upper()[:16]
    if ref:
        session["referral_ref"] = ref
    return redirect(url_for(retry_endpoint))


@app.before_request
def _canonical_host_redirect():
    """Send www.* to the bare apex (machreach.com) with a 301, preserving path
    and query. Keeps everyone on one host so the session/CSRF cookie can't split
    across www vs apex. Registered first so it runs before session/CSRF logic.
    """
    raw = request.host or ""
    if raw.lower().startswith("www."):
        code = 301 if request.method in ("GET", "HEAD", "OPTIONS") else 308
        return redirect(request.url.replace("://" + raw, "://" + raw[4:], 1), code=code)

# ── Security: Rate limiting ──
from flask_limiter import Limiter  # noqa: E402
from flask_limiter.util import get_remote_address  # noqa: E402
_ratelimit_storage_uri = (os.getenv("RATELIMIT_STORAGE_URI") or "memory://").strip() or "memory://"


def _rate(name: str, fallback: str) -> str:
    """A limit, overridable by environment variable so it can be widened
    without a deploy: RATELIMIT_LOGIN="60 per minute", and so on."""
    return (os.getenv(f"RATELIMIT_{name}") or fallback).strip() or fallback


# Limits are counted per IP address, and a university shares one: everybody on
# the campus network arrives from the same address. The budget therefore has to
# fit a whole lecture hall browsing at once, not one person. Brute force is
# fenced off per account instead, which is where it actually belongs.
RATE_DEFAULT = _rate("DEFAULT", "1200 per minute")
RATE_LOGIN = _rate("LOGIN", "40 per minute")
RATE_LOGIN_ACCOUNT = _rate("LOGIN_ACCOUNT", "8 per minute")
RATE_REGISTER = _rate("REGISTER", "40 per minute")
RATE_EMAIL = _rate("EMAIL", "12 per minute")

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[RATE_DEFAULT],
    storage_uri=_ratelimit_storage_uri,
)


def _submitted_email() -> str:
    """Rate-limit key for the account being tried, not the network it is tried
    from. Ten people signing in from one campus is normal; ten passwords for
    one address in a minute is not."""
    try:
        return (request.form.get("email") or "").strip().lower() or get_remote_address()
    except Exception:
        return get_remote_address()

# ── Startup diagnostic — log DB path so we can debug persistence ──
from machreach_core.config import DATABASE_PATH  # noqa: E402
logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("machreach")
_log.info(f"DATABASE_PATH = {DATABASE_PATH}")
_log.info(f"DATABASE_PATH exists = {DATABASE_PATH.exists()}")
_log.info(f"/data dir exists = {os.path.isdir('/data')}")
if _IS_PRODUCTION and _ratelimit_storage_uri == "memory://":
    _log.warning("RATELIMIT_STORAGE_URI is memory:// in production; use Redis/Valkey for shared rate limits.")
if os.path.isdir('/data'):
    _log.info(f"/data contents = {os.listdir('/data')}")

# Local development remains zero-setup. Production migrations run once in the
# deploy phase (migrate.py) rather than racing in every Gunicorn process.
_RUN_STARTUP_MIGRATIONS = (
    not _IS_PRODUCTION
    or (os.getenv("MACHREACH_RUN_STARTUP_MIGRATIONS") or "").strip() == "1"
)
if _RUN_STARTUP_MIGRATIONS:
    init_db()

# ── MachReach Student module ──
from student.db import init_student_db  # noqa: E402
from student.routes import register_student_routes  # noqa: E402
from student.academic_routes import register_academic_routes  # noqa: E402
if _RUN_STARTUP_MIGRATIONS:
    init_student_db()
register_student_routes(app, csrf, limiter)
register_academic_routes(app, csrf, limiter)


# ---------------------------------------------------------------------------
# System email helper — sends transactional emails from support@machreach.com
# ---------------------------------------------------------------------------

def _send_system_email(
    to: str, subject: str, body: str, *, idempotency_key: str | None = None
) -> bool:
    """Send a transactional email (verification, reset, invite) from the system account.
    Returns True on success."""
    from machreach_core.config import SMTP_HOST, SMTP_PORT
    from machreach_core.config import SYSTEM_FROM_EMAIL, SYSTEM_FROM_NAME, SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    print(f"[SYSTEM EMAIL] Attempting to send to {to} via {SMTP_HOST}:{SMTP_PORT} as {SYSTEM_SMTP_USER}", flush=True)
    if not SYSTEM_SMTP_USER or not SYSTEM_SMTP_PASSWORD:
        print(f"[SYSTEM EMAIL] SMTP credentials not set — SYSTEM_SMTP_USER={'set' if SYSTEM_SMTP_USER else 'EMPTY'}, SYSTEM_SMTP_PASSWORD={'set' if SYSTEM_SMTP_PASSWORD else 'EMPTY'}", flush=True)
        try:
            from machreach_core.db import record_operational_event
            record_operational_event("smtp_failure", "configuration")
        except Exception:
            _log.exception("[smtp] could not persist missing-configuration signal")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SYSTEM_FROM_NAME} <{SYSTEM_FROM_EMAIL}>" if SYSTEM_FROM_EMAIL else SYSTEM_SMTP_USER
    msg["To"] = to
    if idempotency_key:
        import hashlib
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        msg["Message-ID"] = f"<{digest}@machreach.com>"
    msg.attach(MIMEText(body, "plain"))
    try:
        if SMTP_PORT == 587:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                srv.starttls()
                srv.login(SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                srv.login(SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD)
                srv.send_message(msg)
        print(f"[SYSTEM EMAIL] Successfully sent to {to}", flush=True)
        return True
    except Exception as e:
        import traceback
        try:
            from machreach_core.db import record_operational_event
            record_operational_event("smtp_failure", "transactional")
        except Exception:
            _log.exception("[smtp] could not persist failure signal")
        print(f"[SYSTEM EMAIL] Send FAILED ({to}): {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Health check — Render uses this to know if the app is alive
# ---------------------------------------------------------------------------

@app.route("/health")
@limiter.exempt
def health_check():
    """Liveness probe: can this instance still reach the database?

    Render kills an instance that cannot answer this within five seconds, so it
    must stay cheap. It used to verify every schema version and probe five
    tables — around seven round trips — which turned an ordinary slow moment on
    the database into a killed instance and a "server failure" alert.

    The schema is not unchecked: migrate.py runs check_schema_readiness() as its
    last pre-deploy step, so a deploy carrying a stale schema never reaches an
    instance at all, and /health/operations re-checks it for monitoring.

    With DB_SLEEP_FRIENDLY the probe stops opening a connection. Render calls
    this endpoint often enough to keep a serverless compute awake by itself, so
    a liveness check that touches the database would cancel out every other
    saving. What it reports narrows to what a liveness probe is actually for —
    is this process able to answer — and whether the database is reachable is
    left to /health/operations, which a monitor calls once a minute.
    """
    from machreach_core.config import DB_SLEEP_FRIENDLY

    if DB_SLEEP_FRIENDLY:
        response = jsonify({"status": "healthy", "database": "not_probed"})
        response.headers["Cache-Control"] = "no-store"
        return response, 200
    try:
        from machreach_core.db import get_db, _fetchval
        with get_db() as db:
            _fetchval(db, "SELECT 1")
        response = jsonify({"status": "healthy"})
        response.headers["Cache-Control"] = "no-store"
        return response, 200
    except Exception as e:
        _log.exception("[health] database probe failed: %s", e)
        return jsonify({"status": "degraded"}), 503


@app.route("/health/operations")
@limiter.limit("12 per minute")
def operational_health_check():
    """Detailed operational probe for authenticated monitors only."""
    supplied = request.headers.get("X-Operations-Secret", "")
    if not OPERATIONS_SECRET or not hmac.compare_digest(supplied, OPERATIONS_SECRET):
        return jsonify({"error": "not_found"}), 404
    try:
        from machreach_core.operations import collect_operational_health
        payload = collect_operational_health()
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response, 200 if payload["status"] == "ok" else 503
    except Exception as exc:
        _log.exception("[health] operational probe failed: %s", exc)
        return jsonify({"status": "error"}), 503


def _debug_admin_gate():
    """Gate operational diagnostics behind explicit enablement and recent reauth."""
    enabled = os.getenv("ENABLE_DEBUG_ENDPOINTS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return jsonify({"error": "not_found"}), 404
    if not _is_admin():
        return jsonify({"error": "unauthorized"}), 403
    if not _admin_mfa_session_ok() or not _admin_secret_ok():
        return jsonify({"error": "reauthentication_required"}), 403
    if ADMIN_ACTION_SECRET:
        supplied = request.headers.get("X-Admin-Action-Secret", "")
        if not hmac.compare_digest(supplied, ADMIN_ACTION_SECRET):
            return jsonify({"error": "unauthorized"}), 403
    return None


@app.route("/api/debug/smtp-test")
@limiter.limit("3 per minute")
def debug_smtp_test():
    """Diagnose SMTP — test connection without sending."""
    gate = _debug_admin_gate()
    if gate:
        return gate
    from machreach_core.config import SMTP_HOST, SMTP_PORT, SYSTEM_FROM_EMAIL, SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD
    info = {
        "SMTP_HOST": SMTP_HOST,
        "SMTP_PORT": SMTP_PORT,
        "SYSTEM_FROM_EMAIL": SYSTEM_FROM_EMAIL,
        "SYSTEM_SMTP_USER": SYSTEM_SMTP_USER[:3] + "***" if SYSTEM_SMTP_USER else "(empty)",
        "SYSTEM_SMTP_PASSWORD": ("set, len=" + str(len(SYSTEM_SMTP_PASSWORD))) if SYSTEM_SMTP_PASSWORD else "(empty)",
    }
    # Try actual SMTP connection
    import smtplib
    try:
        if SMTP_PORT == 587:
            srv = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            srv.starttls()
        else:
            srv = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        info["connection"] = "OK"
        try:
            srv.login(SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD)
            info["login"] = "OK"
        except Exception as e:
            info["login"] = f"FAILED: {e}"
        srv.quit()
    except Exception as e:
        info["connection"] = f"FAILED: {e}"
    return jsonify(info)


@app.route("/api/debug/smtp-send-test")
@limiter.limit("3 per minute")
def debug_smtp_send_test():
    """Actually send a test email to support@machreach.com to verify delivery."""
    gate = _debug_admin_gate()
    if gate:
        return gate
    result = _send_system_email(
        "support@machreach.com",
        "MachReach SMTP Test",
        "If you received this, system emails are working correctly."
    )
    return jsonify({"sent": result})


# ---------------------------------------------------------------------------
# ONE-TIME: Diagnostic — check what DB Render is using
# ---------------------------------------------------------------------------

@app.route("/api/admin/check-db", methods=["POST"])
@limiter.limit("3 per minute")
def admin_check_db():
    gate = _debug_admin_gate()
    if gate:
        return gate

    from machreach_core.db import get_db, _fetchval, _USE_PG, _db_fingerprint
    from machreach_core.config import DATABASE_URL

    with get_db() as db:
        client_count = _fetchval(db, "SELECT COUNT(*) FROM clients")

    return jsonify({
        "using_pg": _USE_PG,
        "db_fingerprint": _db_fingerprint(),
        "db_url_prefix": (DATABASE_URL[:40] + "...") if DATABASE_URL else "NOT SET",
        "client_count": int(client_count or 0),
    })


def _hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()


def _verify_pw(pw: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash. Supports bcrypt and legacy SHA256."""
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return bcrypt.checkpw(pw.encode(), stored_hash.encode())
    # Legacy SHA256 — verify and auto-upgrade
    return hashlib.sha256(pw.encode()).hexdigest() == stored_hash


def _maybe_upgrade_hash(client_id: int, pw: str, stored_hash: str):
    """If the stored hash is legacy SHA256, upgrade it to bcrypt."""
    if not (stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$")):
        update_client_password(client_id, _hash_pw(pw), bump_session_version=False)


_sec_log = logging.getLogger("machreach.security")


def _log_security(event: str, **extra):
    """Log a security event with request context."""
    ip = request.remote_addr or "unknown"
    ua = request.headers.get("User-Agent", "")[:100]
    details = " ".join(f"{k}={v}" for k, v in extra.items())
    _sec_log.info(f"[SECURITY] {event} ip={ip} ua={ua} {details}")


def _logged_in() -> bool:
    return "client_id" in session


def _is_admin() -> bool:
    if not _logged_in():
        return False
    c = get_client(session["client_id"])
    if not c:
        return False
    return bool(c.get("is_admin"))


def _log_admin_action(action: str, target: str = "", **extra):
    """Audit high-risk admin actions in the app logs."""
    admin_id = session.get("client_id")
    admin_email = ""
    if admin_id:
        c = get_client(admin_id)
        admin_email = (c.get("email") or "") if c else ""
    _log_security(
        f"admin.{action}",
        admin_id=admin_id,
        admin_email=admin_email,
        target=target,
        **extra,
    )


def _admin_secret_ok() -> bool:
    """Return whether password + TOTP were reconfirmed in the last 10 minutes."""
    try:
        verified_at = float(session.get("admin_reauthenticated_at") or 0)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc).timestamp() - verified_at <= 10 * 60


def _admin_mfa_session_ok() -> bool:
    try:
        verified_at = float(session.get("admin_mfa_verified_at") or 0)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc).timestamp() - verified_at <= 12 * 60 * 60


def _safe_admin_next(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate.startswith("/admin") and not candidate.startswith("//") else "/admin"


# ── Admin shell ────────────────────────────────────────────────────────────
#
# Every admin page used to be an island: one breadcrumb back to /admin, and the
# only list of sections lived on /admin itself. Moving between two of them meant
# going back first. The sections are declared once here and every page renders
# the same bar, so any section is one click from any other.

_ADMIN_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("/admin",                        "Panel",     "\U0001F4E3"),
    ("/admin/growth",                 "Usuarios",  "\U0001F4C8"),
    ("/admin/analytics",              "Analytics", "\U0001F4CA"),
    ("/admin/jobs",                   "Jobs",      "⚙️"),
    ("/admin/courses",                "Cursos",    "\U0001F393"),
    ("/admin/leaderboard-winners-test", "Ganadores", "\U0001F3C6"),
)

# Security utilities. Separated because they are things you do, not places you
# browse — and because putting "rotate MFA" beside "Usuarios" invites a misclick.
_ADMIN_TOOLS: tuple[tuple[str, str], ...] = (
    ("/admin/reauth",     "Confirmar acciones"),
    ("/admin/mfa/rotate", "Rotar MFA"),
)

_ADMIN_SHELL_CSS = """
<style>
  /* Colours come from the layout's variables so the bar follows the app if it
     ever gains a dark theme; the literals are only fallbacks.

     Not sticky on purpose: the layout sets `overflow-x:hidden` on body, which
     computes overflow-y to `auto` and makes body a scroll container that never
     scrolls — position:sticky inside it would silently never engage. Fixing
     that means touching global layout CSS, which is not worth it for a bar
     that already sits at the top of every page. */
  .adm-bar{margin:0 -4px 18px;padding:0 4px 12px;border-bottom:1px solid var(--border,#E2DCCC)}
  .adm-bar-in{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .adm-brand{display:inline-flex;align-items:baseline;gap:7px;font-family:'Bricolage Grotesque',sans-serif;
    font-size:17px;font-weight:800;letter-spacing:-.02em;color:var(--text,#1A1A1F);text-decoration:none;flex:none}
  .adm-brand span{color:#FF7A3D}
  .adm-brand small{font-size:10px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;
    color:var(--text-muted,#77756F);border:1px solid var(--border,#E2DCCC);border-radius:999px;
    padding:2px 7px;background:var(--card,#fff)}
  .adm-tabs{display:flex;gap:6px;flex-wrap:wrap;flex:1;min-width:0}
  .adm-tab{display:inline-flex;align-items:center;gap:6px;padding:7px 13px;border-radius:999px;
    border:1px solid var(--border,#E2DCCC);background:var(--card,#fff);color:var(--text-secondary,#4E4852);
    font-size:13.5px;font-weight:700;text-decoration:none;white-space:nowrap;
    transition:transform .15s ease,box-shadow .15s ease,background .15s ease,color .15s ease}
  .adm-tab:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(20,18,30,.08);color:var(--text,#1A1A1F)}
  .adm-tab.on{background:var(--text,#1A1A1F);border-color:var(--text,#1A1A1F);color:var(--card,#fff)}
  .adm-tab.on:hover{transform:none;box-shadow:none;color:var(--card,#fff)}
  .adm-tab i{font-style:normal;font-size:13px;line-height:1}
  .adm-side{display:flex;gap:6px;flex-wrap:wrap;flex:none}
  .adm-side a{display:inline-flex;align-items:center;padding:6px 11px;border-radius:999px;
    border:1px dashed var(--border,#E2DCCC);background:transparent;color:var(--text-muted,#77756F);
    font-size:12px;font-weight:700;text-decoration:none;white-space:nowrap;transition:.15s ease}
  .adm-side a:hover{border-style:solid;border-color:#FF7A3D;color:#FF7A3D;background:var(--card,#fff)}
  .adm-side a.exit{color:var(--text-secondary,#4E4852)}
  .adm-head{margin:0 0 18px}
  .adm-head h1{font-family:'Bricolage Grotesque',sans-serif;font-size:clamp(25px,3.2vw,33px);
    font-weight:700;letter-spacing:-.025em;line-height:1.05;margin:0;color:var(--text,#1A1A1F)}
  .adm-head p{margin:7px 0 0;color:var(--text-muted,#77756F);font-size:14px;line-height:1.5;max-width:74ch}
  @media (max-width:760px){
    .adm-bar{margin-bottom:14px}
    .adm-bar-in{gap:10px}
    /* Brand, tabs and tools each take their own row: sharing one leaves the
       tabs a sliver to scroll inside. */
    .adm-brand{flex-basis:100%}
    .adm-tabs{flex-basis:100%;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;padding-bottom:2px}
    .adm-tabs::-webkit-scrollbar{display:none}
    .adm-side{flex-basis:100%}
  }
  @media (prefers-reduced-motion:reduce){.adm-tab{transition:none}.adm-tab:hover{transform:none}}
</style>
"""


def _admin_shell(active: str, heading: str, subtitle: str = "", body: str = "") -> str:
    """Wrap an admin page in the shared nav + header.

    `active` is the section path, so the bar highlights where you are. Pages
    that are not sections (a delete confirmation, MFA rotation) pass the
    section they belong under, or "" for none.
    """
    tabs = "".join(
        f'<a class="adm-tab{" on" if path == active else ""}" href="{path}">'
        f'<i>{icon}</i>{_esc(label)}</a>'
        for path, label, icon in _ADMIN_SECTIONS
    )
    tools = "".join(
        f'<a href="{path}">{_esc(label)}</a>' for path, label in _ADMIN_TOOLS
    )
    return f"""{_ADMIN_SHELL_CSS}
    <div class="adm-bar"><div class="adm-bar-in">
      <a class="adm-brand" href="/admin">Mach<span>Reach</span> <small>Admin</small></a>
      <nav class="adm-tabs">{tabs}</nav>
      <div class="adm-side">{tools}<a class="exit" href="/student">&larr; Volver a la app</a></div>
    </div></div>
    <div class="adm-head"><h1>{heading}</h1>
      {f'<p>{subtitle}</p>' if subtitle else ''}</div>
    {body}"""


@app.before_request
def _protect_admin_routes():
    if not request.path.startswith("/admin"):
        return None
    if request.path in {"/admin/mfa", "/admin/mfa/verify"}:
        return None
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from machreach_core import admin_security
    client_id = int(session["client_id"])
    if not admin_security.is_enabled(client_id):
        return redirect(url_for("admin_mfa_setup", next=request.full_path.rstrip("?")))
    if not _admin_mfa_session_ok():
        return redirect(url_for("admin_mfa_verify", next=request.full_path.rstrip("?")))
    return None


def _mfa_qr_svg(uri: str) -> str:
    """QR for an otpauth:// URI, as an inline data: PNG.

    A data URI rather than its own route, so the secret never becomes a URL
    that could land in a log or a proxy cache; a PNG rather than inline SVG,
    because the app's stylesheets restyle bare <path> elements and paint the
    modules into invisibility.
    """
    try:
        import base64
        import io
        import segno

        buffer = io.BytesIO()
        segno.make(uri, error="m").save(
            buffer, kind="png", scale=6, border=2, dark="#1A1A1F", light="#FFFFFF",
        )
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return (
            f'<img src="data:image/png;base64,{encoded}" width="260" height="260" '
            'alt="Codigo QR para tu app de autenticacion" '
            'style="display:block;image-rendering:pixelated;">'
        )
    except Exception as error:  # never block enrolment over a picture
        app.logger.warning("MFA QR render failed: %s", error)
        return '<p class="subtitle">QR unavailable — use the setup key below.</p>'


@app.route("/admin/mfa", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def admin_mfa_setup():
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from machreach_core import admin_security
    client_id = int(session["client_id"])
    if admin_security.is_enabled(client_id):
        return redirect(url_for("admin_mfa_verify", next=_safe_admin_next(request.values.get("next"))))
    secret = str(session.get("admin_mfa_setup_secret") or "")
    if not secret:
        secret = admin_security.generate_secret()
        session["admin_mfa_setup_secret"] = secret
    error = ""
    if request.method == "POST":
        supplied_secret = request.form.get("admin_action_secret", "")
        deployment_ok = bool(ADMIN_ACTION_SECRET) and hmac.compare_digest(
            supplied_secret,
            ADMIN_ACTION_SECRET,
        )
        if not deployment_ok:
            error = "The deployment administrator secret is incorrect."
        elif not admin_security.verify_code(secret, request.form.get("code", "")):
            error = "The authenticator code is invalid."
        else:
            admin_security.enroll(client_id, secret)
            session.pop("admin_mfa_setup_secret", None)
            session["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()
            _log_admin_action("mfa_enabled")
            return redirect(_safe_admin_next(request.form.get("next")))
    client = get_client(client_id) or {}
    uri = admin_security.provisioning_uri(secret, str(client.get("email") or "admin"))
    return _render("Administrator MFA", f"""
    <div class="card" style="max-width:660px;margin:40px auto;">
      <h1>Secure administrator access</h1>
      <style>
        .mfa-go {{ display:inline-flex; align-items:center; gap:8px; padding:11px 18px; border:0;
          border-radius:999px; background:#FF7A3D; color:#fff; font-weight:800; font-size:14px; cursor:pointer; }}
        .mfa-go:hover {{ background:#E9662B; }}
      </style>

      <p>Two things are needed here, and neither is a password you already know.</p>
      {'<div class="alert alert-red">' + _esc(error) + '</div>' if error else ''}

      <h2 style="font-size:19px;margin:22px 0 6px;">1 · Scan this with an authenticator app</h2>
      <p style="margin:0 0 12px;">Google Authenticator, Authy, 1Password &mdash; any of them. Scanning is all
         it takes; there is nothing to type into the app.</p>
      <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;">
        <div style="background:#fff;padding:10px;border:1px solid var(--border);border-radius:14px;">{_mfa_qr_svg(uri)}</div>
        <div style="flex:1;min-width:220px;">
          <p style="margin:0 0 6px;"><strong>Can&rsquo;t scan?</strong> In the app choose
             &ldquo;enter a setup key&rdquo; and paste this, with type <em>time-based</em>:</p>
          <code style="display:block;padding:12px;word-break:break-all;">{_esc(secret)}</code>
          <p class="subtitle" style="margin:10px 0 0;">Don&rsquo;t reload this page before submitting &mdash;
             a reload issues a new key and the one in your app would stop matching.</p>
        </div>
      </div>

      <h2 style="font-size:19px;margin:26px 0 6px;">2 · Fill in the form</h2>
      <p style="margin:0 0 12px;">The <strong>deployment administrator secret</strong> is the
         <code>ADMIN_ACTION_SECRET</code> environment variable of this service (Render &rarr; machreach
         &rarr; Environment). The <strong>six-digit code</strong> is the number your authenticator app is
         showing right now.</p>
      <form method="post">
        {_csrf_hidden_input()}
        <input type="hidden" name="next" value="{_esc(_safe_admin_next(request.values.get('next')))}">
        <div class="form-group"><label>Deployment administrator secret (ADMIN_ACTION_SECRET)</label><input type="password" name="admin_action_secret" required autocomplete="current-password"></div>
        <div class="form-group"><label>Six-digit authenticator code</label><input name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="one-time-code"></div>
        <button class="mfa-go" type="submit">Enable administrator MFA</button>
      </form>
      <details style="margin:16px 0 0;"><summary>Authenticator URI</summary><code style="word-break:break-all;">{_esc(uri)}</code></details>
    </div>
    """, active_page="admin")


@app.route("/admin/mfa/rotate", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def admin_mfa_rotate():
    """Throw away this account's authenticator so a new one can be enrolled.

    A setup key that leaked is only worth worrying about if it cannot be
    replaced, so replacing it is supported — but it costs the same two proofs
    that enrolling did: the deployment secret and a live code from the
    authenticator being retired.
    """
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from machreach_core import admin_security
    client_id = int(session["client_id"])
    if not admin_security.is_enabled(client_id):
        return redirect(url_for("admin_mfa_setup"))
    error = ""
    if request.method == "POST":
        supplied = request.form.get("admin_action_secret", "")
        deployment_ok = bool(ADMIN_ACTION_SECRET) and hmac.compare_digest(supplied, ADMIN_ACTION_SECRET)
        if not deployment_ok:
            error = "The deployment administrator secret is incorrect."
        elif not admin_security.verify_client_code(client_id, request.form.get("code", "")):
            error = "The authenticator code is invalid."
        else:
            admin_security.disable(client_id)
            session.pop("admin_mfa_setup_secret", None)
            session.pop("admin_mfa_verified_at", None)
            _log_admin_action("mfa_rotated")
            flash(("success", "Authenticator removed. Enrol a new one to continue."))
            return redirect(url_for("admin_mfa_setup"))
    return _render("Rotate administrator MFA", _admin_shell("", "\U0001F501 Rotar MFA", "", f"""
    <div class="card" style="max-width:620px;margin:0 auto;">
      <style>
        .mfa-go {{ display:inline-flex; align-items:center; gap:8px; padding:11px 18px; border:0;
          border-radius:999px; background:#FF7A3D; color:#fff; font-weight:800; font-size:14px; cursor:pointer; }}
        .mfa-go:hover {{ background:#E9662B; }}
      </style>

      <p>This deletes the authenticator currently registered for your account and sends you
         back to enrolment with a brand-new setup key. Do it if the old key was ever seen by
         anyone else.</p>
      {'<div class="alert alert-red">' + _esc(error) + '</div>' if error else ''}
      <form method="post">
        {_csrf_hidden_input()}
        <div class="form-group"><label>Deployment administrator secret (ADMIN_ACTION_SECRET)</label><input type="password" name="admin_action_secret" required autocomplete="current-password"></div>
        <div class="form-group"><label>Six-digit code from the authenticator you are replacing</label><input name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="one-time-code"></div>
        <button class="mfa-go" type="submit">Remove and start over</button>
        <a class="btn btn-outline" href="/admin" style="margin-left:8px;">Cancel</a>
      </form>
    </div>
    """), active_page="admin")


@app.route("/admin/mfa/verify", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def admin_mfa_verify():
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from machreach_core import admin_security
    client_id = int(session["client_id"])
    if not admin_security.is_enabled(client_id):
        return redirect(url_for("admin_mfa_setup", next=_safe_admin_next(request.values.get("next"))))
    error = ""
    if request.method == "POST":
        if admin_security.verify_client_code(client_id, request.form.get("code", "")):
            session["admin_mfa_verified_at"] = datetime.now(timezone.utc).timestamp()
            _log_admin_action("mfa_verified")
            return redirect(_safe_admin_next(request.form.get("next")))
        error = "The authenticator code is invalid."
    return _render("Verify administrator MFA", f"""
    <div class="card" style="max-width:520px;margin:40px auto;">
      <h1>Administrator verification</h1>
      <p>Enter the current code from your authenticator app.</p>
      {'<div class="alert alert-red">' + _esc(error) + '</div>' if error else ''}
      <form method="post">
        {_csrf_hidden_input()}
        <input type="hidden" name="next" value="{_esc(_safe_admin_next(request.values.get('next')))}">
        <div class="form-group"><label>Six-digit code</label><input name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="one-time-code" autofocus></div>
        <button class="btn btn-primary" type="submit">Continue</button>
      </form>
    </div>
    """, active_page="admin")


@app.route("/admin/reauth", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def admin_reauthenticate():
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from machreach_core import admin_security
    client_id = int(session["client_id"])
    error = ""
    if request.method == "POST":
        client = get_client(client_id) or {}
        password_ok = bool(client and _verify_pw(request.form.get("password", ""), str(client.get("password") or "")))
        mfa_ok = admin_security.verify_client_code(client_id, request.form.get("code", ""))
        if password_ok and mfa_ok:
            now = datetime.now(timezone.utc).timestamp()
            session["admin_mfa_verified_at"] = now
            session["admin_reauthenticated_at"] = now
            _log_admin_action("reauthenticated")
            return redirect(_safe_admin_next(request.form.get("next")))
        error = "Password or authenticator code is incorrect."
    return _render("Confirm administrator action", _admin_shell("", "\U0001F512 Confirmar acción", "Las acciones peligrosas piden tu contraseña y el código actual del autenticador.", f"""
    <div class="card" style="max-width:520px;margin:0 auto;">
      {'<div class="alert alert-red">' + _esc(error) + '</div>' if error else ''}
      <form method="post">
        {_csrf_hidden_input()}
        <input type="hidden" name="next" value="{_esc(_safe_admin_next(request.values.get('next')))}">
        <div class="form-group"><label>Password</label><input type="password" name="password" required autocomplete="current-password"></div>
        <div class="form-group"><label>Six-digit code</label><input name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="one-time-code"></div>
        <button class="btn btn-primary" type="submit">Confirm for 10 minutes</button>
      </form>
    </div>
    """), active_page="admin")


@app.route("/admin/courses", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def admin_courses():
    from student import db as sdb

    if request.method == "POST":
        if not _admin_secret_ok():
            return redirect(url_for("admin_reauthenticate", next="/admin/courses"))
        action = request.form.get("action", "")
        if action == "delete":
            result = sdb.soft_delete_catalog_course(
                int(session["client_id"]),
                int(request.form.get("catalog_id") or 0),
                request.form.get("confirmation", ""),
                request.form.get("reason", ""),
            )
            if result.get("ok"):
                _log_admin_action(
                    "course_soft_deleted",
                    target=str(request.form.get("catalog_id") or ""),
                    deletion_id=result.get("deletion_id"),
                )
                flash(("success", "Course hidden. It can be recovered for 30 days."))
            else:
                flash(("error", result.get("error") or "Could not delete the course."))
        elif action == "recover":
            result = sdb.recover_catalog_course(
                int(session["client_id"]),
                int(request.form.get("deletion_id") or 0),
            )
            if result.get("ok"):
                _log_admin_action(
                    "course_recovered",
                    target=str(result.get("catalog_id") or ""),
                )
                flash(("success", "Course restored."))
            else:
                flash(("error", result.get("error") or "Could not recover the course."))
        return redirect(url_for("admin_courses"))

    catalog = sdb.list_admin_course_catalog()
    active_rows = "".join(
        f"""
        <tr><td>{row['id']}</td><td>{_esc(row.get('code') or '')}</td>
        <td>{_esc(row.get('name') or '')}</td><td>{int(row.get('uses') or 0)}</td>
        <td><form method="post" style="display:flex;gap:6px;flex-wrap:wrap;">
          {_csrf_hidden_input()}<input type="hidden" name="action" value="delete">
          <input type="hidden" name="catalog_id" value="{row['id']}">
          <input name="confirmation" required placeholder="Type exact course name">
          <input name="reason" required maxlength="500" placeholder="Deletion reason">
          <button class="btn btn-outline btn-sm" type="submit">Delete</button>
        </form></td></tr>
        """
        for row in catalog["active"]
    ) or '<tr><td colspan="5">No active courses.</td></tr>'
    recovery_rows = "".join(
        f"""
        <tr><td>{row['id']}</td><td>{_esc(row.get('course_code') or '')}</td>
        <td>{_esc(row.get('course_name') or '')}</td>
        <td>{_esc(str(row.get('recover_until') or ''))}</td>
        <td><form method="post">{_csrf_hidden_input()}
          <input type="hidden" name="action" value="recover">
          <input type="hidden" name="deletion_id" value="{row['id']}">
          <button class="btn btn-primary btn-sm" type="submit">Recover</button>
        </form></td></tr>
        """
        for row in catalog["recoverable"]
    ) or '<tr><td colspan="5">No recoverable courses.</td></tr>'
    return _render("Admin courses", _admin_shell("/admin/courses", "\U0001F393 Cursos", "Los cursos no tienen dueño. Borrarlos queda auditado y es recuperable por 30 días.", f"""
      <div class="card"><div class="card-header"><h2>Active courses</h2></div>
      <div style="overflow:auto"><table><thead><tr><th>ID</th><th>Code</th><th>Name</th><th>Members</th><th>Delete</th></tr></thead>
      <tbody>{active_rows}</tbody></table></div></div>
      <div class="card"><div class="card-header"><h2>Recovery window</h2></div>
      <div style="overflow:auto"><table><thead><tr><th>Deletion</th><th>Code</th><th>Name</th><th>Recover until</th><th></th></tr></thead>
      <tbody>{recovery_rows}</tbody></table></div></div>
    """), active_page="admin")


_PRESENCE_LAST_TOUCH: dict[int, int] = {}  # cid -> last unix-second we wrote a heartbeat
_PRESENCE_TOUCH_THROTTLE = 25  # don't UPDATE more often than every 25s per user


@app.before_request
def _validate_session():
    if "client_id" in session:
        from machreach_core.db import get_db, _fetchone
        with get_db() as db:
            row = _fetchone(db, "SELECT session_version FROM clients WHERE id = %s",
                            (session["client_id"],))
            if row is None:
                session.clear()
                return
            try:
                expected_version = int(row.get("session_version") or 0)
                actual_version = int(session.get("session_version"))
            except (TypeError, ValueError):
                session.clear()
                return
            if actual_version != expected_version:
                session.clear()
                return
        # Throttled presence touch — keeps friends' online indicators fresh
        # without hammering the DB on every request.
        try:
            import time as _time
            cid = int(session["client_id"])
            now = int(_time.time())
            if now - _PRESENCE_LAST_TOUCH.get(cid, 0) >= _PRESENCE_TOUCH_THROTTLE:
                _PRESENCE_LAST_TOUCH[cid] = now
                from student import db as _sdb
                _sdb.touch_presence(cid)
                # Bound memory: evict stale entries so this dict can't grow
                # without limit over the life of the process.
                if len(_PRESENCE_LAST_TOUCH) > 2000:
                    cutoff = now - 300
                    for _k in [k for k, v in _PRESENCE_LAST_TOUCH.items() if v < cutoff]:
                        _PRESENCE_LAST_TOUCH.pop(_k, None)
        except Exception:
            pass


def _esc(text: str) -> str:
    """HTML-escape user content to prevent XSS."""
    return html_module.escape(str(text)) if text else ""


def _csrf_hidden_input() -> str:
    return f'<input type="hidden" name="csrf_token" value="{_esc(generate_csrf())}">'


# ── Response compression ────────────────────────────────────────────────────
# Pages are server-rendered with their CSS/JS inline, so a single student view
# is 70-270 KB of highly repetitive text. gunicorn does not compress, so without
# this every navigation shipped the full payload.
_COMPRESSIBLE_MIMETYPES = frozenset({
    "text/html",
    "text/css",
    "text/plain",
    "text/xml",
    "application/json",
    "application/javascript",
    "application/manifest+json",
    "image/svg+xml",
})
# Below ~1 KB, framing overhead cancels the saving out.
_COMPRESS_MIN_BYTES = 1024
# Static files stream by default; only materialize ones small enough to hold.
_COMPRESS_MAX_BYTES = 2 * 1024 * 1024


@app.after_request
def _compress_response(response):
    """Gzip compressible bodies.

    Registered *before* the security-header handler on purpose: Flask runs
    after_request callbacks in reverse registration order, so this runs last —
    after ``harden_html`` has finished rewriting the HTML body. Compressing any
    earlier would hand that rewrite a gzip blob instead of markup.
    """
    if not (200 <= response.status_code < 300):
        return response
    if "Content-Encoding" in response.headers:
        return response
    # Any cache in front of us must key on the request's encoding, whether or
    # not this particular response ended up compressed.
    response.headers.add("Vary", "Accept-Encoding")
    if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
        return response
    if response.mimetype not in _COMPRESSIBLE_MIMETYPES:
        return response
    if response.direct_passthrough:
        # send_static_file streams from disk. Reading it in is only safe for
        # bounded, known-length files.
        length = response.content_length or 0
        if not 0 < length <= _COMPRESS_MAX_BYTES:
            return response
        response.direct_passthrough = False
    data = response.get_data()
    if len(data) < _COMPRESS_MIN_BYTES:
        return response
    compressed = gzip.compress(data, compresslevel=6)
    if len(compressed) >= len(data):
        return response
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    # The entity changed, so the validator has to change with it, or a cache
    # can serve this gzip body against an identity-encoded request's ETag.
    etag = response.headers.get("ETag")
    if etag and etag.endswith('"'):
        response.headers["ETag"] = f'{etag[:-1]}-gzip"'
    return response


@app.after_request
def _set_security_headers(response):
    # Core hardening
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "0"  # modern browsers: CSP is authoritative, legacy header can introduce issues
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "magnetometer=(), accelerometer=(), gyroscope=(), interest-cohort=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Download-Options"] = "noopen"
    # Content Security Policy — nonce inline blocks and extract legacy inline
    # attributes so both script-src and style-src can reject unsafe-inline.
    from machreach_core.csp import harden_html, new_nonce
    csp_nonce = new_nonce()
    if response.mimetype == "text/html" and not response.direct_passthrough:
        response.set_data(harden_html(response.get_data(as_text=True), csp_nonce))
    posthog_host = ""
    posthog_assets = ""
    configured_posthog_host = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com").rstrip("/")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(configured_posthog_host)
        if parsed.scheme == "https" and parsed.netloc:
            posthog_host = f"https://{parsed.netloc}"
            asset_netloc = parsed.netloc.replace(".i.posthog.com", "-assets.i.posthog.com")
            posthog_assets = f"https://{asset_netloc}"
    except ValueError:
        pass
    analytics_script_source = f" {posthog_assets}" if posthog_assets else ""
    analytics_connect_source = f" {posthog_host}" if posthog_host else ""
    _CSP = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{csp_nonce}' https://cdn.jsdelivr.net{analytics_script_source}; "
        "script-src-attr 'none'; "
        f"style-src 'self' 'nonce-{csp_nonce}' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "style-src-attr 'none'; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' https:; "
        f"connect-src 'self' https://api.openai.com https://*.instructure.com https://cdn.jsdelivr.net{analytics_connect_source}; "
        "frame-src 'self' https://open.spotify.com https://www.youtube.com https://www.youtube-nocookie.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self' https://*.lemonsqueezy.com; "
        "object-src 'none'"
    )
    # upgrade-insecure-requests only makes sense behind TLS (production).
    # Over plain HTTP on the LAN (e.g. testing from a phone via
    # http://192.168.x.x:5000) it would upgrade every asset to https://,
    # which the dev server doesn't speak — blank page.
    if _IS_PRODUCTION:
        _CSP += "; upgrade-insecure-requests"
    response.headers["Content-Security-Policy"] = _CSP
    # HSTS with preload in production
    if _IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response


@app.before_request
def _make_session_permanent():
    session.permanent = True


# ---------------------------------------------------------------------------
# HTML Layout
# ---------------------------------------------------------------------------

@app.context_processor
def _inject_analytics_context():
    """Expose PostHog config + identity to every template (cheap, no DB).

    Dormant until POSTHOG_KEY is set, so this is a no-op until configured.
    """
    analytics_allowed = request.cookies.get("analytics_consent") == "1"
    return {
        # Cache key for versioned static URLs; changes once per deploy.
        "deploy_version": DEPLOY_VERSION,
        # Consent banner lives in the shared layout, outside the student
        # fragment translator, so it needs explicit keys.
        "consent": t_dict("consent"),
        "posthog_key": os.getenv("POSTHOG_KEY", "") if analytics_allowed else "",
        "posthog_host": os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
        "analytics_uid": session.get("client_id") or "",
        "analytics_account_type": session.get("account_type") or "",
    }


LAYOUT = """<!DOCTYPE html>
<html lang="{{lang}}" data-theme="">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="csrf-token" content="{{ csrf_token() }}">
  <title>MachReach — {{title}}</title>
  <link rel="icon" type="image/svg+xml" href="/static/machreach-logo-flat.svg?v=1">
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
  <!-- PWA / installable iPhone-app support -->
  <link rel="manifest" href="/manifest.webmanifest">
  <meta name="theme-color" content="#F8F4EA" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#0B0B10" media="(prefers-color-scheme: dark)">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <meta name="apple-mobile-web-app-title" content="MachReach">
  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function () {
        // Updates install themselves. No banner, no button, and no reload
        // under the user: the new worker takes over on the next navigation.
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function (registration) {
          function applyUpdate(worker) {
            if (worker) worker.postMessage({type:'SKIP_WAITING'});
          }
          if (registration.waiting) applyUpdate(registration.waiting);
          registration.addEventListener('updatefound', function () {
            var worker = registration.installing;
            if (!worker) return;
            worker.addEventListener('statechange', function () {
              if (worker.state === 'installed' && navigator.serviceWorker.controller) applyUpdate(worker);
            });
          });
        }).catch(function (error) {
          console.error('MachReach service worker registration failed', error);
          if (window.Sentry && window.Sentry.captureException) window.Sentry.captureException(error);
        });
      });
    }
  </script>
  <style>
    /* ====== Installed-app (standalone PWA) native feel ======
       Only applies when launched from the home screen, never in a browser. */
    .pwa-standalone, .pwa-standalone body { overscroll-behavior-y: none; }
    .pwa-standalone body { -webkit-tap-highlight-color: transparent; -webkit-user-select: none; user-select: none; }
    .pwa-standalone * { -webkit-touch-callout: none; }
    .pwa-standalone input, .pwa-standalone textarea, .pwa-standalone select,
    .pwa-standalone [contenteditable] { -webkit-user-select: text; user-select: text; }
    /* iOS zooms when a focused field is <16px — pin to 16px to stop the jump. */
    .pwa-standalone input, .pwa-standalone textarea, .pwa-standalone select { font-size: 16px; }
    /* Top bar clears the notch / status bar. */
    .pwa-standalone .mr-topbar { padding-top: calc(12px + env(safe-area-inset-top)) !important; }
    /* Page content clears the fixed bottom tab bar. */
    .pwa-standalone .mr-tb-main .content,
    .pwa-standalone .mr-tb-main .content-wide { padding-bottom: calc(82px + env(safe-area-inset-bottom)) !important; }

    /* Native-style bottom tab bar (hidden unless installed). */
    .mr-tabbar { display: none; }
    .pwa-standalone .mr-tabbar {
      display: grid; grid-template-columns: repeat(5, 1fr);
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 850;
      padding: 7px 6px calc(7px + env(safe-area-inset-bottom));
      background: color-mix(in srgb, var(--bg, #F8F4EA) 86%, transparent);
      -webkit-backdrop-filter: blur(20px) saturate(150%);
      backdrop-filter: blur(20px) saturate(150%);
      border-top: 1px solid var(--border-light, #E6DCCB);
      box-shadow: 0 -6px 24px rgba(20,18,30,.06);
    }
    .mr-tab {
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px;
      text-decoration: none; color: var(--text-muted, #94939C);
      font-size: 10px; font-weight: 800; letter-spacing: .01em;
      padding: 5px 2px; border-radius: 13px; min-width: 0;
      transition: color .15s ease, transform .12s ease;
    }
    .mr-tab span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .mr-tab svg { width: 24px; height: 24px; display: block; }
    .mr-tab.active { color: var(--primary, #FF7A3D); }
    .mr-tab.active svg { filter: drop-shadow(0 2px 8px color-mix(in srgb, var(--primary, #FF7A3D) 45%, transparent)); }
    .mr-tab:active { transform: scale(.9); }
    button.mr-tab { background: none; border: 0; font: inherit; cursor: pointer; }

    /* In the installed app the bottom tabs ARE the navigation — remove the
       browser-style top pill nav + hamburger entirely so it stops feeling like
       a web page. The top bar becomes a simple centered title bar. */
    .pwa-standalone .mr-tb-nav,
    .pwa-standalone .mr-tb-right { display: none !important; }
    .pwa-standalone .mr-topbar { justify-content: center !important; }

    /* "Más" sheet — native bottom sheet for everything not in the tab bar. */
    .mr-more { position: fixed; inset: 0; z-index: 4000; display: none; }
    .mr-more.open { display: block; }
    .mr-more-backdrop { position: absolute; inset: 0; background: rgba(10,10,16,.45); -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px); animation: mrMoreFade .2s ease; }
    .mr-more-panel { position: absolute; left: 0; right: 0; bottom: 0; background: var(--card, #fff); border-radius: 24px 24px 0 0; padding: 6px 14px calc(18px + env(safe-area-inset-bottom)); max-height: 88vh; overflow-y: auto; box-shadow: 0 -12px 44px rgba(20,18,30,.28); animation: mrMoreUp .26s cubic-bezier(.2,.85,.2,1); }
    .mr-more-grab { width: 38px; height: 5px; border-radius: 999px; background: var(--border-light, #D9D2C3); margin: 9px auto 12px; }
    .mr-more-id { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 18px; background: var(--bg, #F6F2E9); text-decoration: none; color: var(--text, #1A1A1F); margin-bottom: 8px; }
    .mr-more-av { width: 44px; height: 44px; border-radius: 14px; background: var(--primary, #FF7A3D); color: #fff; display: grid; place-items: center; font-weight: 800; font-size: 19px; font-family: "Bricolage Grotesque", sans-serif; }
    .mr-more-id-txt { display: flex; flex-direction: column; flex: 1; min-width: 0; }
    .mr-more-id-txt strong { font-size: 15.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .mr-more-id-txt small { color: var(--text-muted, #94939C); font-size: 12.5px; }
    .mr-more-list { display: grid; gap: 1px; }
    .mr-more-list a, .mr-more-act { display: flex; align-items: center; gap: 13px; padding: 13px 14px; border-radius: 14px; text-decoration: none; color: var(--text, #1A1A1F); font-weight: 700; font-size: 15px; background: none; border: 0; width: 100%; text-align: left; font-family: inherit; cursor: pointer; }
    .mr-more-list a:active, .mr-more-act:active { background: var(--bg, #F0EBDF); }
    .mr-more-ic { width: 26px; text-align: center; font-size: 19px; flex-shrink: 0; }
    .mr-more-chev { margin-left: auto; color: var(--text-muted, #B8B2A8); font-size: 20px; font-weight: 400; }
    .mr-more-actions { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-light, #EADFCE); display: grid; gap: 1px; }
    .mr-more-logout { color: #E0533F !important; }
    @keyframes mrMoreUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
    @keyframes mrMoreFade { from { opacity: 0; } to { opacity: 1; } }
  </style>
  {% if posthog_key %}<script>
  {% raw %}!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug getPageViewId".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);{% endraw %}
  posthog.init('{{ posthog_key }}', { api_host: '{{ posthog_host }}', person_profiles: 'identified_only', capture_pageview: true, capture_pageleave: true });
  {% if analytics_uid %}posthog.identify('{{ analytics_uid }}', { account_type: {{ analytics_account_type|tojson }} });{% endif %}
  </script>{% endif %}
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700;12..96,800&family=Nunito:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" onload="if(typeof renderMathInElement==='function')renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}],throwOnError:false});"></script>
  <script>
    // Apply the saved global theme before CSS paints. This prevents the app
    // from flashing back to light mode when navigating between student tabs.
    (function(){
      try {
        var mode = localStorage.getItem('machreach-theme') || '';
        var named = localStorage.getItem('mr_theme') || 'default';
        var theme = mode === 'dark' ? 'dark' : ((named && named !== 'default') ? ('mr-' + named) : '');
        if (theme) document.documentElement.setAttribute('data-theme', theme);
        else document.documentElement.removeAttribute('data-theme');
        document.documentElement.style.colorScheme = theme === 'dark' ? 'dark' : 'light';
      } catch(e) {}
    })();
    // Tag the document when running as an INSTALLED app (home-screen / standalone)
    // so we can give it a native app feel (bottom tab bar, safe areas, no
    // page-style bounce/selection) without changing the website in a browser.
    (function(){
      try {
        var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
                         window.navigator.standalone === true;
        if (standalone) document.documentElement.classList.add('pwa-standalone');
      } catch(e) {}
    })();
    // Auto-inject CSRF token into all fetch requests
    (function(){
      var _fetch = window.fetch;
      window.fetch = function(url, opts) {
        opts = opts || {};
        if (opts.method && opts.method !== 'GET') {
          opts.headers = opts.headers || {};
          if (opts.headers instanceof Headers) {
            if (!opts.headers.has('X-CSRFToken')) {
              var m = document.querySelector('meta[name="csrf-token"]');
              if (m) opts.headers.set('X-CSRFToken', m.content);
            }
          } else {
            if (!opts.headers['X-CSRFToken']) {
              var m = document.querySelector('meta[name="csrf-token"]');
              if (m) opts.headers['X-CSRFToken'] = m.content;
            }
          }
        }
        return _fetch.call(this, url, opts);
      };
    })();
    // Safe JSON helper: avoids crashes when server returns non-JSON (502, HTML error pages)
    window._safeJson = async function(r) {
      try { var t = await r.text(); return t ? JSON.parse(t) : {}; }
      catch(e) { return {error: 'Server error (status ' + r.status + '). Please try again.'}; }
    };
    window.__mrNavigating = false;
    window.mrIsAbortLike = function(e) {
      if (!e) return false;
      if (e.name === 'AbortError') return true;
      var msg = String(e.message || e || '').toLowerCase();
      return msg.indexOf('abort') >= 0 || msg.indexOf('cancel') >= 0 || msg.indexOf('interrupted') >= 0;
    };
    window.mrReload = function() {
      window.__mrNavigating = true;
      window.location.reload();
    };
    window.mrGo = function(url) {
      window.__mrNavigating = true;
      window.location.href = url;
    };
    window.mrNetworkError = function(e, msg) {
      if (window.__mrNavigating || window.mrIsAbortLike(e)) {
        console.warn('[MachReach] Ignored navigation-related request interruption.', e);
        return;
      }
      var text = msg || 'Network error. Please check your connection and try again.';
      if (typeof showToast === 'function') showToast(text, 'error');
      else window.alert(text);
    };
  </script>
  {% if dashboard_design %}
  <link rel="stylesheet" href="/static/machreach_landing/v6/theme.css?v={{ deploy_version }}"/>
  <link rel="stylesheet" href="/static/machreach_app/app/dash.css?v={{ deploy_version }}"/>
  <link rel="stylesheet" href="/static/machreach_app/app/focus.css?v={{ deploy_version }}"/>
  {% else %}
  <link rel="stylesheet" href="/static/machreach_layout/layout-base.css?v={{ deploy_version }}"/>
  {% endif %}
  <link rel="stylesheet" href="/static/machreach_consent.css?v={{ deploy_version }}"/>
</head>
<body>
  <script>
    window.__IS_LOGGED_IN__ = {% if logged_in %}true{% else %}false{% endif %};
    window.__ACCOUNT_TYPE__ = "{{ account_type|default('student') }}";
    (function(){
      // Close any open nav dropdown when clicking outside or pressing Escape.
      document.addEventListener('click', function(e){
        document.querySelectorAll('.nav-dropdown.open').forEach(function(d){
          if (!d.contains(e.target)) d.classList.remove('open');
        });
      });
      document.addEventListener('keydown', function(e){
        if (e.key === 'Escape') document.querySelectorAll('.nav-dropdown.open').forEach(function(d){ d.classList.remove('open'); });
      });
      // Topbar tools dropdown: position the menu as fixed so the nav's
      // overflow-x:auto can never clip it (in any theme).
      document.addEventListener('DOMContentLoaded', function(){
        document.querySelectorAll('.mr-tb-tools').forEach(function(wrap){
          var btn = wrap.querySelector('.mr-tb-tools-btn');
          var menu = wrap.querySelector('.mr-tb-tools-menu');
          if (!btn || !menu) return;
          function place(){
            if (window.matchMedia('(max-width: 860px)').matches) {
              // burger menu: the dropdown is an inline section, no fixed coords
              menu.style.position = '';
              menu.style.left = '';
              menu.style.top = '';
              return;
            }
            var r = btn.getBoundingClientRect();
            menu.style.position = 'fixed';
            menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
            menu.style.top = (r.bottom + 8) + 'px';
          }
          wrap.addEventListener('mouseenter', place);
          btn.addEventListener('focus', place);
          btn.addEventListener('click', function(){ place(); wrap.classList.toggle('open'); });
          window.addEventListener('resize', function(){ if (wrap.matches(':hover') || wrap.classList.contains('open')) place(); });
          document.addEventListener('click', function(e){ if (!wrap.contains(e.target)) wrap.classList.remove('open'); });
        });
      });
    })();
  </script>
  {% if logged_in and account_type|default('student') == 'student' %}
  {% if dashboard_design %}
  {{content|safe}}
  {% else %}
  <div class="mr-app-shell app top-shell">
    <header class="mr-topbar">
      <a href="/student" class="mr-tb-brand">
        <span class="mr-tb-logo"><img src="/static/machreach-logo-flat.svg?v=1" alt="MachReach" /></span>
        <span class="mr-tb-name">Mach<span>Reach</span></span>
      </a>
      <nav class="mr-tb-nav" id="mrTopNav">
        <a class="mr-tb-link {% if active_page == 'student_dashboard' %}active{% endif %}" href="/student">{{ student_ui.home }}</a>
        <a class="mr-tb-link {% if active_page == 'student_focus' %}active{% endif %}" href="/student/focus">{{ student_ui.focus }}</a>
        <a class="mr-tb-link {% if active_page == 'student_planner' %}active{% endif %}" href="/student/planner">{{ student_ui.planner }}</a>
        <a class="mr-tb-link {% if active_page == 'student_courses' %}active{% endif %}" href="/student/courses">{{ student_ui.courses }}</a>
        <div class="mr-tb-tools {% if active_page in ['student_quizzes','student_flashcards'] %}active{% endif %}">
          <button class="mr-tb-link mr-tb-tools-btn" type="button">{{ student_ui.tools }}</button>
          <div class="mr-tb-tools-menu">
            <a href="/student/quizzes">{{ student_ui.quizzes }}</a>
            <a href="/student/flashcards">{{ student_ui.flashcards }}</a>
          </div>
        </div>
        <a class="mr-tb-link {% if active_page == 'student_reviews' %}active{% endif %}" href="/student/reviews">{{ student_ui.reviews }}</a>
        <a class="mr-tb-link {% if active_page == 'student_leaderboard' %}active{% endif %}" href="/student/leaderboard">{{ student_ui.leaderboard }}</a>
        <a class="mr-tb-link {% if active_page == 'student_friends' %}active{% endif %}" href="/student/friends">{{ student_ui.friends }}</a>
        <a class="mr-tb-link {% if active_page == 'student_shop' %}active{% endif %}" href="/student/shop">{{ student_ui.shop }}</a>
        <a class="mr-tb-link {% if active_page == 'student_gpa' %}active{% endif %}" href="/student/gpa">{{ student_ui.grades }}</a>
        {% if is_admin %}<a class="mr-tb-link {% if active_page == 'admin' %}active{% endif %}" href="/admin">{{ student_ui.admin }}</a>{% endif %}
      </nav>
      <div class="mr-tb-right">
        <button id="theme-toggle" class="mr-tb-icon" type="button" onclick="toggleDarkMode()" title="{{ student_ui.toggle_theme }}" aria-label="{{ student_ui.toggle_theme }}">&#127769;</button>
        <a class="mr-tb-icon" href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <form method="post" action="/logout" style="margin:0"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button class="mr-tb-icon" type="submit" title="{{nav.logout}}" aria-label="{{nav.logout}}" style="border:0;background:transparent">&#10162;</button></form>
        <a class="mr-tb-icon mr-tb-settings-gear {% if active_page == 'student_settings' %}active{% endif %}" href="/student/settings" title="{{ student_ui.settings }}" aria-label="{{ student_ui.settings }}">&#9881;</a>
        <a class="mr-tb-user" href="/student/profile" title="{{client_name}}">
          <span class="mr-tb-av">{{ (client_name[:1] or 'M')|upper }}</span>
          <span class="mr-tb-uname">{{ (client_name.split()[0] if client_name else student_ui.student_fallback) }}</span>
        </a>
        <button class="mr-tb-burger" type="button" onclick="document.getElementById('mrTopNav').classList.toggle('open')" aria-label="Menu">&#9776;</button>
      </div>
    </header>

    <main class="mr-tb-main">
      <div class="content{% if wide %} content-wide{% endif %}">
        <div class="toast-container" id="toast-container">
        {% for cat, msg in messages %}
          <div class="toast toast-{{cat}}" onclick="dismissToast(this)">
            {% if cat == 'success' %}&#10003;{% elif cat == 'error' %}&#10007;{% else %}&#8505;{% endif %}
            <span style="flex:1;">{{msg}}</span>
            <button class="toast-close" onclick="event.stopPropagation();dismissToast(this.parentElement)">&times;</button>
            <div class="toast-progress"></div>
          </div>
        {% endfor %}
        </div>
        {{content|safe}}
        <link rel="stylesheet" href="/static/machreach_layout/layout-dark.css?v={{ deploy_version }}"/>
      </div>
    </main>
    {% if active_page != 'student_setup' %}
    <nav class="mr-tabbar" aria-label="Primary">
      <a class="mr-tab {% if active_page == 'student_dashboard' %}active{% endif %}" href="/student">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>
        <span>{% if lang == 'en' %}Home{% else %}Inicio{% endif %}</span>
      </a>
      <a class="mr-tab {% if active_page == 'student_focus' %}active{% endif %}" href="/student/focus">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 13V9.5"/><path d="M9.5 2.5h5"/></svg>
        <span>{% if lang == 'en' %}Focus{% else %}Enfoque{% endif %}</span>
      </a>
      <a class="mr-tab {% if active_page == 'student_courses' %}active{% endif %}" href="/student/courses">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2z"/><path d="M9 3v16"/></svg>
        <span>{% if lang == 'en' %}Courses{% else %}Cursos{% endif %}</span>
      </a>
      <a class="mr-tab {% if active_page == 'student_leaderboard' %}active{% endif %}" href="/student/leaderboard">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V4h12v5a6 6 0 0 1-12 0z"/><path d="M6 5H3.5v1.5A3.5 3.5 0 0 0 7 10"/><path d="M18 5h2.5v1.5A3.5 3.5 0 0 1 17 10"/><path d="M9.5 21h5"/><path d="M12 15v6"/></svg>
        <span>Ranking</span>
      </a>
      <button id="mrMoreTrigger" class="mr-tab {% if active_page in ['student_planner','student_quizzes','student_flashcards','student_reviews','student_friends','student_shop','student_gpa','student_profile','student_settings','admin'] %}active{% endif %}" type="button" onclick="mrToggleMore()" aria-haspopup="dialog" aria-controls="mrMore" aria-expanded="false">
        <svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
        <span>{% if lang == 'en' %}More{% else %}Más{% endif %}</span>
      </button>
    </nav>
    <div class="mr-more" id="mrMore" aria-hidden="true">
      <div class="mr-more-backdrop" onclick="mrToggleMore(false)"></div>
      <div class="mr-more-panel" role="dialog" aria-modal="true" tabindex="-1" aria-label="{% if lang == 'en' %}More{% else %}Más{% endif %}">
        <div class="mr-more-grab"></div>
        <a class="mr-more-id" href="/student/profile">
          <span class="mr-more-av">{{ (client_name[:1] or 'M')|upper }}</span>
          <span class="mr-more-id-txt">
            <strong>{{ (client_name or student_ui.student_fallback) }}</strong>
            <small>{% if lang == 'en' %}View profile{% else %}Ver perfil{% endif %}</small>
          </span>
          <span class="mr-more-chev">&rsaquo;</span>
        </a>
        <nav class="mr-more-list">
          <a href="/student/planner"><span class="mr-more-ic">&#128197;</span>{{ student_ui.planner }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/quizzes"><span class="mr-more-ic">&#128221;</span>{{ student_ui.quizzes }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/flashcards"><span class="mr-more-ic">&#127183;</span>{{ student_ui.flashcards }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/reviews"><span class="mr-more-ic">&#11088;</span>{{ student_ui.reviews }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/friends"><span class="mr-more-ic">&#128101;</span>{{ student_ui.friends }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/shop"><span class="mr-more-ic">&#128722;</span>{{ student_ui.shop }}<span class="mr-more-chev">&rsaquo;</span></a>
          <a href="/student/gpa"><span class="mr-more-ic">&#128202;</span>{{ student_ui.grades }}<span class="mr-more-chev">&rsaquo;</span></a>
          {% if is_admin %}<a href="/admin"><span class="mr-more-ic">&#128227;</span>{{ student_ui.admin }}<span class="mr-more-chev">&rsaquo;</span></a>{% endif %}
        </nav>
        <div class="mr-more-actions">
          <button type="button" class="mr-more-act" onclick="if(typeof toggleDarkMode==='function')toggleDarkMode()"><span class="mr-more-ic">&#127769;</span>{{ student_ui.toggle_theme }}</button>
          <a class="mr-more-act" href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}"><span class="mr-more-ic">&#127760;</span>{% if lang == 'en' %}Español{% else %}English{% endif %}</a>
          <a class="mr-more-act" href="/student/settings"><span class="mr-more-ic">&#9881;</span>{{ student_ui.settings }}</a>
          <form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="mr-more-act mr-more-logout"><span class="mr-more-ic">&#9211;</span>{{ nav.logout }}</button></form>
        </div>
      </div>
    </div>
    <script>
      var mrMoreLastFocus = null;
      function mrToggleMore(open){
        var m = document.getElementById('mrMore');
        if (!m) return;
        var willOpen = (open === undefined) ? !m.classList.contains('open') : !!open;
        var trigger = document.getElementById('mrMoreTrigger');
        var panel = m.querySelector('.mr-more-panel');
        m.classList.toggle('open', willOpen);
        m.setAttribute('aria-hidden', willOpen ? 'false' : 'true');
        if (trigger) trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        document.body.style.overflow = willOpen ? 'hidden' : '';
        document.querySelectorAll('.mr-topbar,.mr-tb-main').forEach(function(el){ el.inert = willOpen; });
        if (willOpen) {
          mrMoreLastFocus = document.activeElement;
          window.setTimeout(function(){ if (panel) panel.focus(); }, 0);
        } else if (mrMoreLastFocus && typeof mrMoreLastFocus.focus === 'function') {
          mrMoreLastFocus.focus();
          mrMoreLastFocus = null;
        }
      }
      document.addEventListener('keydown', function(e){
        var m = document.getElementById('mrMore');
        if (!m || !m.classList.contains('open')) return;
        if (e.key === 'Escape') { e.preventDefault(); mrToggleMore(false); return; }
        if (e.key !== 'Tab') return;
        var focusable = Array.prototype.slice.call(m.querySelectorAll('a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])'));
        if (!focusable.length) { e.preventDefault(); return; }
        var first = focusable[0], last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      });
    </script>
    {% endif %}
  </div>
  {% endif %}
  {% else %}
  <div class="nav">
    <a href="/" class="brand">
      <div class="brand-icon"><img src="/static/machreach-logo-flat.svg?v=1" alt="MachReach" /></div>
      MachReach
    </a>
    <button class="hamburger" onclick="document.querySelector('.nav-links').classList.toggle('open');this.innerHTML=this.innerHTML==='&#9776;'?'&#10005;':'&#9776;'" aria-label="Menu">&#9776;</button>
    <div class="nav-links">
      {% if logged_in %}
        {% if account_type|default('student') == 'student' %}
        {% if lang == 'es' %}
        <a href="/student" {% if active_page == 'student_dashboard' %}class="active"{% endif %}>&#127891; Panel</a>
        {% if is_admin %}<a href="/admin" {% if active_page == 'admin' %}class="active"{% endif %} style="color:var(--yellow);">&#128227; Admin</a>{% endif %}
        <a href="/student/courses" {% if active_page == 'student_courses' %}class="active"{% endif %}>&#128218; Cursos</a>
        <a href="/student/planner" {% if active_page == 'student_planner' %}class="active"{% endif %}>&#128197; Plan</a>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_flashcards','student_quizzes'] %}class="active"{% endif %}>&#128218; Herramientas de Estudio &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/flashcards">&#127183; Tarjetas</a>
            <a href="/student/quizzes">&#128221; Quizzes</a>
          </div>
        </div>
        <a href="/student/focus" {% if active_page == 'student_focus' %}class="active"{% endif %}>&#127919; Enfoque</a>
        <div class="nav-divider"></div>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_gpa','student_friends','student_shop'] %}class="active"{% endif %}>Más &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/friends">&#128101; Amigos</a>
            <a href="/student/shop">&#129534; Tienda</a>
            <a href="/student/gpa">&#128200; Planilla de Notas</a>
          </div>
        </div>
        <a href="/student/leaderboard" {% if active_page == 'student_leaderboard' %}class="active"{% endif %}>&#127942; Ranking</a>
        {% else %}
        <a href="/student" {% if active_page == 'student_dashboard' %}class="active"{% endif %}>&#127891; Dashboard</a>
        {% if is_admin %}<a href="/admin" {% if active_page == 'admin' %}class="active"{% endif %} style="color:var(--yellow);">&#128227; Admin</a>{% endif %}
        <a href="/student/courses" {% if active_page == 'student_courses' %}class="active"{% endif %}>&#128218; Courses</a>
        <a href="/student/planner" {% if active_page == 'student_planner' %}class="active"{% endif %}>&#128197; Plan</a>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_flashcards','student_quizzes'] %}class="active"{% endif %}>&#128218; Study Tools &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/flashcards">&#127183; Flashcards</a>
            <a href="/student/quizzes">&#128221; Quizzes</a>
          </div>
        </div>
        <a href="/student/focus" {% if active_page == 'student_focus' %}class="active"{% endif %}>&#127919; Focus</a>
        <div class="nav-divider"></div>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_gpa','student_friends','student_shop'] %}class="active"{% endif %}>More &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/friends">&#128101; Friends</a>
            <a href="/student/shop">&#129534; Shop</a>
            <a href="/student/gpa">&#128200; Grade Sheet</a>
          </div>
        </div>
        <a href="/student/leaderboard" {% if active_page == 'student_leaderboard' %}class="active"{% endif %}>&#127942; Leaderboard</a>
        {% endif %}
        <a href="/student/settings" {% if active_page == 'student_settings' %}class="active"{% endif %}>&#9881;</a>
        {% endif %}
        <button id="theme-toggle" class="nav-theme-toggle" type="button" onclick="toggleDarkMode()" title="{{ student_ui.toggle_theme }}" aria-label="{{ student_ui.toggle_theme }}">&#127769;</button>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <div class="nav-divider"></div>
        <a href="/student/profile" class="nav-user" style="text-decoration:none;cursor:pointer;color:#94A3B8;" title="My profile">{{client_name}}</a>
        <form method="post" action="/logout" style="margin:0"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="btn btn-ghost btn-sm" style="color:#EF4444;">{{nav.logout}}</button></form>
      {% else %}
        <a href="/login">{{nav.login}}</a>
        <a href="/register" class="btn btn-primary btn-sm" style="color:#fff;">{{nav.get_started}}</a>
        <button id="theme-toggle" class="nav-theme-toggle" type="button" onclick="toggleDarkMode()" title="{{nav.toggle_theme|default('Cambiar modo')}}" aria-label="{{nav.toggle_theme|default('Cambiar modo')}}">&#127769;</button>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
      {% endif %}
    </div>
  </div>
  <div class="container{% if wide %} container-wide{% endif %}">
    <div class="toast-container" id="toast-container">
    {% for cat, msg in messages %}
      <div class="toast toast-{{cat}}" onclick="dismissToast(this)">
        {% if cat == 'success' %}&#10003;{% elif cat == 'error' %}&#10007;{% else %}&#8505;{% endif %}
        <span style="flex:1;">{{msg}}</span>
        <button class="toast-close" onclick="event.stopPropagation();dismissToast(this.parentElement)">&times;</button>
        <div class="toast-progress"></div>
      </div>
    {% endfor %}
    </div>
    {{content|safe}}
  </div>
  <footer style="border-top:1px solid var(--border);margin-top:48px;padding:24px 48px;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--text-muted);flex-wrap:wrap;gap:12px;">
    {% if lang == 'es' %}
    <span>&copy; 2026 MachReach. Todos los derechos reservados.</span>
    <div style="display:flex;gap:18px;">
      <a href="/privacy" style="color:var(--text-muted);text-decoration:none;">Política de Privacidad</a>
      <a href="/terms" style="color:var(--text-muted);text-decoration:none;">Términos del Servicio</a>
      <a href="mailto:support@machreach.com" style="color:var(--text-muted);text-decoration:none;">Contacto</a>
    </div>
    {% else %}
    <span>&copy; 2026 MachReach. All rights reserved.</span>
    <div style="display:flex;gap:18px;">
      <a href="/privacy" style="color:var(--text-muted);text-decoration:none;">Privacy Policy</a>
      <a href="/terms" style="color:var(--text-muted);text-decoration:none;">Terms of Service</a>
      <a href="mailto:support@machreach.com" style="color:var(--text-muted);text-decoration:none;">Contact</a>
    </div>
    {% endif %}
  </footer>
  {% endif %}
  <script>
    // Toast notifications
    function dismissToast(el) {
      el.classList.add('toast-out');
      setTimeout(function() { el.remove(); }, 300);
    }
    function showToast(msg, cat) {
      var c = document.getElementById('toast-container');
      if (!c) return;
      var icons = {success: '\u2713', error: '\u2717', info: '\u2139'};
      var d = document.createElement('div');
      d.className = 'toast toast-' + (cat || 'success');
      d.onclick = function() { dismissToast(d); };
      d.innerHTML = (icons[cat] || icons.success) +
        ' <span style="flex:1;">' + msg + '</span>' +
        '<button class="toast-close" onclick="event.stopPropagation();dismissToast(this.parentElement)">&times;</button>' +
        '<div class="toast-progress"></div>';
      c.appendChild(d);
      setTimeout(function() { dismissToast(d); }, 4000);
    }
    // Auto-dismiss server-rendered toasts
    document.querySelectorAll('.toast').forEach(function(t) {
      setTimeout(function() { dismissToast(t); }, 4000);
    });
    // Global confetti helper — sprinkles celebratory particles
    window.confettiBurst = function(count) {
      count = count || 40;
      var colors = ['#6366F1','#8B5CF6','#EC4899','#F59E0B','#10B981','#3B82F6'];
      for (var i=0; i<count; i++) {
        (function(delay){
          setTimeout(function(){
            var p = document.createElement('div');
            p.className = 'confetti';
            p.style.left = Math.random()*100 + 'vw';
            p.style.background = colors[Math.floor(Math.random()*colors.length)];
            p.style.animationDuration = (1.8 + Math.random()*1.4) + 's';
            p.style.transform = 'rotate(' + (Math.random()*360) + 'deg)';
            document.body.appendChild(p);
            setTimeout(function(){ p.remove(); }, 3500);
          }, delay);
        })(i * 25);
      }
    };
    // Pop a stat number (call with element)
    window.popNumber = function(el, newValue) {
      if (!el) return;
      if (newValue !== undefined) el.textContent = newValue;
      el.classList.remove('num-pop');
      void el.offsetWidth;
      el.classList.add('num-pop');
    };
    // Promotion overlay — fullscreen, center-screen rank-up celebration.
    // Shown when a user ranks up after a focus session. Dismisses on click,
    // Escape key, or after ~6 seconds. Includes confetti, glow, scale-in,
    // and the new rank's full name + tier color.
    window.showPromotionToast = function(promo) {
      if (!promo || !promo.promoted || !promo.rank_after) return;
      var r = promo.rank_after;
      var title = promo.reached_elite ? 'ELITE RANK ACHIEVED'
                : (promo.tier_up ? 'TIER PROMOTION' : 'RANK UP');
      var subtitle = promo.reached_elite
        ? "You\'ve broken into the Elite tier. Few ever make it this far."
        : (promo.tier_up
            ? "A whole new tier of mastery. Keep going."
            : "Your dedication is paying off. Onward.");

      // Inject keyframes once
      if (!document.getElementById('promo-overlay-style')) {
        var st = document.createElement('style');
        st.id = 'promo-overlay-style';
        st.textContent =
          '@keyframes promoFadeIn{from{opacity:0}to{opacity:1}}'
          + '@keyframes promoFadeOut{to{opacity:0}}'
          + '@keyframes promoZoom{0%{transform:scale(.4) rotate(-8deg);opacity:0}'
          + '60%{transform:scale(1.08) rotate(2deg);opacity:1}'
          + '100%{transform:scale(1) rotate(0deg);opacity:1}}'
          + '@keyframes promoPulse{0%,100%{box-shadow:0 0 60px var(--promo-c, #6366F1),0 0 120px var(--promo-c,#6366F1)}'
          + '50%{box-shadow:0 0 90px var(--promo-c,#6366F1),0 0 180px var(--promo-c,#6366F1)}}'
          + '@keyframes promoShine{0%{transform:translateX(-100%) skewX(-20deg)}100%{transform:translateX(220%) skewX(-20deg)}}'
          + '@keyframes promoTitleSlide{from{transform:translateY(-12px);opacity:0;letter-spacing:.5em}'
          + 'to{transform:translateY(0);opacity:1;letter-spacing:.4em}}'
          + '@keyframes promoSubFade{from{opacity:0;transform:translateY(8px)}to{opacity:.85;transform:translateY(0)}}'
          + '@keyframes promoRayRotate{from{transform:translate(-50%,-50%) rotate(0deg)}to{transform:translate(-50%,-50%) rotate(360deg)}}';
        document.head.appendChild(st);
      }

      // Backdrop
      var overlay = document.createElement('div');
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-label', title);
      overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;'
        + 'background:radial-gradient(ellipse at center, rgba(0,0,0,.55) 0%, rgba(0,0,0,.85) 80%);'
        + 'backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);'
        + 'display:flex;align-items:center;justify-content:center;'
        + 'animation:promoFadeIn .35s ease-out;cursor:pointer;'
        + '--promo-c:' + r.color + ';';

      // Spinning rays behind the card
      var rays = document.createElement('div');
      rays.style.cssText = 'position:absolute;top:50%;left:50%;width:140vmax;height:140vmax;'
        + 'background:conic-gradient(from 0deg, transparent 0deg, ' + r.color + '22 12deg, transparent 28deg,'
        + ' transparent 92deg, ' + r.color + '22 102deg, transparent 118deg,'
        + ' transparent 184deg, ' + r.color + '22 192deg, transparent 208deg,'
        + ' transparent 274deg, ' + r.color + '22 282deg, transparent 298deg, transparent 360deg);'
        + 'animation:promoRayRotate 18s linear infinite;pointer-events:none;opacity:.5;';
      overlay.appendChild(rays);

      // Card
      var card = document.createElement('div');
      card.style.cssText = 'position:relative;text-align:center;color:#fff;'
        + 'padding:48px 64px;border-radius:28px;'
        + 'background:linear-gradient(150deg, ' + r.color + ' 0%, #0B1220 110%);'
        + 'border:2px solid ' + r.color + ';'
        + 'animation:promoZoom .7s cubic-bezier(.18,.89,.32,1.28) both, promoPulse 2.4s ease-in-out infinite .7s;'
        + 'max-width:min(560px, 92vw);overflow:hidden;font-family:inherit;';

      // Shine sweep
      var shine = document.createElement('div');
      shine.style.cssText = 'position:absolute;inset:0;'
        + 'background:linear-gradient(110deg, transparent 30%, rgba(255,255,255,.25) 50%, transparent 70%);'
        + 'animation:promoShine 1.6s ease-out 0.4s both;pointer-events:none;';
      card.appendChild(shine);

      var inner = document.createElement('div');
      inner.style.cssText = 'position:relative;';
      inner.innerHTML =
        '<div style="font-size:14px;font-weight:700;letter-spacing:.4em;text-transform:uppercase;'
        + 'opacity:.95;animation:promoTitleSlide .6s ease-out .25s both;color:#fff;">' + title + '</div>'
        + '<div style="font-size:64px;line-height:1;margin:18px 0 14px;'
        + 'animation:promoZoom .9s cubic-bezier(.18,.89,.32,1.28) .15s both;">'
        + (promo.reached_elite ? '👑' : (promo.tier_up ? '✨' : '🌟'))
        + '</div>'
        + '<div style="font-size:38px;font-weight:800;letter-spacing:-.02em;line-height:1.1;margin-bottom:10px;'
        + 'animation:promoZoom .9s cubic-bezier(.18,.89,.32,1.28) .35s both;'
        + 'text-shadow:0 4px 24px rgba(0,0,0,.4);">' + r.full_name + '</div>'
        + '<div style="font-size:14px;opacity:.85;max-width:380px;margin:0 auto;line-height:1.55;'
        + 'animation:promoSubFade .6s ease-out .9s both;">' + subtitle + '</div>'
        + '<div style="margin-top:22px;font-size:11px;opacity:.55;letter-spacing:.15em;text-transform:uppercase;'
        + 'animation:promoSubFade .6s ease-out 1.4s both;">tap anywhere to dismiss</div>';
      card.appendChild(inner);
      overlay.appendChild(card);
      document.body.appendChild(overlay);

      // Confetti burst (uses the existing global helper)
      try { if (typeof window.confettiBurst === 'function') window.confettiBurst(120); } catch(e) {}

      var dismiss = function(){
        if (overlay._dismissed) return;
        overlay._dismissed = true;
        overlay.style.animation = 'promoFadeOut .35s ease-in forwards';
        setTimeout(function(){ overlay.remove(); }, 380);
        document.removeEventListener('keydown', onKey);
      };
      var onKey = function(e){ if (e.key === 'Escape') dismiss(); };
      overlay.addEventListener('click', dismiss);
      document.addEventListener('keydown', onKey);
      setTimeout(dismiss, 6500);
    };
    // Theme system — applies named themes via CSS variables on <body>
    window.MR_THEMES = {
      // ── MachReach base ──
      default: { bg:'#F4F1EA', card:'#FFFFFF', border:'#E2DCCC', text:'#1A1A1F', textMuted:'#77756F', primary:'#FF7A3D' },
      dashboard_dark: { bg:'#11131A', card:'#1D202A', border:'#34313A', text:'#F7F0E4', textMuted:'#B9B0A5', primary:'#FF7A3D' },
      // ── Dark ──
      midnight:{ bg:'#050816', card:'#0c1026', border:'#1e1b4b', text:'#e2e8f0', textMuted:'#94a3b8', primary:'#8b5cf6' },
      forest:  { bg:'#0b2018', card:'#11322a', border:'#14532d', text:'#d1fae5', textMuted:'#6ee7b7', primary:'#10b981' },
      ocean:   { bg:'#082f49', card:'#0c4a6e', border:'#075985', text:'#e0f2fe', textMuted:'#7dd3fc', primary:'#06b6d4' },
      rose:    { bg:'#3f0a1a', card:'#581132', border:'#9f1239', text:'#fecdd3', textMuted:'#fda4af', primary:'#f43f5e' },
      sunset:  { bg:'#431407', card:'#7c2d12', border:'#9a3412', text:'#ffedd5', textMuted:'#fdba74', primary:'#f97316' },
      mono:    { bg:'#0a0a0a', card:'#171717', border:'#262626', text:'#fafafa', textMuted:'#a3a3a3', primary:'#fafafa' },
      // ── Light / Pastel ── (bumped saturation + colored cards so the pastel actually shows)
      light:    { bg:'#f8fafc', card:'#ffffff', border:'#e2e8f0', text:'#0f172a', textMuted:'#64748b', primary:'#6366f1' },
      lavender: { bg:'#ede9fe', card:'#f5f3ff', border:'#c4b5fd', text:'#3b0764', textMuted:'#6d28d9', primary:'#7c3aed' },
      mint:     { bg:'#bbf7d0', card:'#dcfce7', border:'#86efac', text:'#14532d', textMuted:'#15803d', primary:'#16a34a' },
      peach:    { bg:'#fed7aa', card:'#ffedd5', border:'#fdba74', text:'#7c2d12', textMuted:'#c2410c', primary:'#ea580c' },
      sky:      { bg:'#bae6fd', card:'#e0f2fe', border:'#7dd3fc', text:'#0c4a6e', textMuted:'#0369a1', primary:'#0284c7' },
      butter:   { bg:'#fef9c3', card:'#fefce8', border:'#fde047', text:'#713f12', textMuted:'#a16207', primary:'#ca8a04' },
      lilac:    { bg:'#f5d0fe', card:'#fae8ff', border:'#e879f9', text:'#581c87', textMuted:'#9333ea', primary:'#c026d3' },
      blush:    { bg:'#fecdd3', card:'#ffe4e6', border:'#fda4af', text:'#881337', textMuted:'#be123c', primary:'#e11d48' },
      sand:     { bg:'#e7d9c2', card:'#f4ead7', border:'#c8a47a', text:'#44342a', textMuted:'#78603e', primary:'#a16207' },
      cottoncandy:{ bg:'#fbcfe8', card:'#fce7f3', border:'#f9a8d4', text:'#831843', textMuted:'#be185d', primary:'#db2777' },
      seafoam:  { bg:'#a5f3fc', card:'#cffafe', border:'#67e8f9', text:'#164e63', textMuted:'#0e7490', primary:'#0891b2' },
    };
    window.applyMrTheme = function(name) {
      var legacyMode = localStorage.getItem('machreach-theme') || '';
      var t = window.MR_THEMES[name] || window.MR_THEMES['default'];
      if (name && name !== 'default') {
        legacyMode = '';
        try { localStorage.removeItem('machreach-theme'); } catch(e) {}
      }
      if (!name || name === 'default') {
        t = legacyMode === 'dark' ? window.MR_THEMES.dashboard_dark : window.MR_THEMES.default;
      }
      var r = document.documentElement;
      r.style.setProperty('--bg', t.bg);
      r.style.setProperty('--card', t.card);
      r.style.setProperty('--border', t.border);
      r.style.setProperty('--text', t.text);
      r.style.setProperty('--text-muted', t.textMuted);
      r.style.setProperty('--primary', t.primary);
      // Also set the data-theme attribute so the CSS rules
      // (:root[data-theme="mr-lavender"] body { ... } etc.) kick in
      // for nav background, body bg, input colors, etc.
      if (!name || name === 'default') {
        r.setAttribute('data-theme', legacyMode);
      } else {
        r.setAttribute('data-theme', 'mr-' + name);
      }
      r.style.colorScheme = legacyMode === 'dark' ? 'dark' : 'light';
      try { localStorage.setItem('mr_theme', name || 'default'); } catch(e) {}
      document.body && document.body.setAttribute('data-theme', name);
    };
    // Apply saved theme on load
    try { window.applyMrTheme(localStorage.getItem('mr_theme') || 'default'); } catch(e) {}

    // ── FOCUS SHIELD (DISABLED) ──
    // Previously blocked every non-focus MachReach page when a focus session
    // was active. Disabled because it blocked legitimate study navigation
    // (leaderboards, courses, flashcards). Anti-distraction is now opt-in via
    // the user's own browser focus extensions.
    (function(){})();
    // Loading button handler
    document.querySelectorAll('form[data-loading]').forEach(form => {
      form.addEventListener('submit', () => {
        const btn = form.querySelector('button[type=submit]');
        if (btn) btn.classList.add('loading');
      });
    });
    // --- Dark mode toggle ---
    function toggleDarkMode() {
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      const next = current === 'dark' ? '' : 'dark';
      localStorage.setItem('machreach-theme', next);
      localStorage.setItem('mr_theme', 'default');
      window.applyMrTheme && window.applyMrTheme('default');
      html.style.colorScheme = next === 'dark' ? 'dark' : 'light';
      const btn = document.getElementById('theme-toggle');
      if (btn) btn.innerHTML = next === 'dark' ? '&#9728;' : '&#127769;';
    }
    // Set correct icon on load
    (function(){
      var btn = document.getElementById('theme-toggle');
      if (btn && document.documentElement.getAttribute('data-theme') === 'dark') btn.innerHTML = '&#9728;';
    })();

    // --- Global keyboard shortcuts ---
    (function() {
      let selectedIdx = -1;
      function getRows() { return Array.from(document.querySelectorAll('tr[data-mail-id]')); }
      function selectRow(idx) {
        const rows = getRows();
        if (rows.length === 0) return;
        rows.forEach(r => r.style.outline = '');
        selectedIdx = Math.max(0, Math.min(idx, rows.length - 1));
        const row = rows[selectedIdx];
        row.style.outline = '2px solid var(--primary)';
        row.scrollIntoView({block: 'nearest'});
      }

      document.addEventListener('keydown', function(e) {
        // Skip if typing in input/textarea
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;

        const rows = getRows();
        if (rows.length === 0) return;

        switch(e.key) {
          case 'j': // Next email
            e.preventDefault();
            selectRow(selectedIdx + 1);
            break;
          case 'k': // Previous email
            e.preventDefault();
            selectRow(selectedIdx - 1);
            break;
          case 'o': // Open selected
          case 'Enter':
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              rows[selectedIdx].click();
            }
            break;
          case 'x': // Toggle checkbox
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const cb = rows[selectedIdx].querySelector('input[type=checkbox]');
              if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change', {bubbles:true})); }
            }
            break;
          case 'e': // Archive
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const id = rows[selectedIdx].dataset.mailId;
              if (typeof archiveEmail === 'function') archiveEmail(parseInt(id), rows[selectedIdx].querySelector('button'));
            }
            break;
          case 's': // Star
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const starEl = rows[selectedIdx].querySelector('span[onclick*="toggleStar"]');
              if (starEl) starEl.click();
            }
            break;
          case '/': // Focus search
            e.preventDefault();
            const searchInput = document.getElementById('mail-search-input');
            if (searchInput) searchInput.focus();
            break;
          case '?': // Show shortcuts help
            e.preventDefault();
            const helpModal = document.getElementById('shortcuts-modal');
            if (helpModal) helpModal.style.display = helpModal.style.display === 'flex' ? 'none' : 'flex';
            break;
        }
      });
    })();
  </script>
  <script>
    // Auto-inject CSRF hidden field into all forms
    document.addEventListener('DOMContentLoaded', function() {
      var token = document.querySelector('meta[name="csrf-token"]');
      if (!token) return;
      document.querySelectorAll('form[method="post"]').forEach(function(f) {
        if (!f.querySelector('input[name="csrf_token"]')) {
          var inp = document.createElement('input');
          inp.type = 'hidden'; inp.name = 'csrf_token'; inp.value = token.content;
          f.appendChild(inp);
        }
      });
    });
  </script>

  <!-- ─── MachReach global UX enhancements ─── -->
  <script src="/static/machreach_layout/layout-1.js?v={{ deploy_version }}"></script>

  <!-- Cookie Consent Banner (GDPR) -->
  <section id="cookie-consent" class="mr-consent" role="dialog" aria-label="{{ consent.aria_label }}" aria-live="polite" aria-hidden="true" hidden>
    <div class="mr-consent__panel">
      <div class="mr-consent__copy">
        <span class="mr-consent__icon" aria-hidden="true">✓</span>
        <div>
          <strong>{{ consent.title }}</strong>
          <p>{{ consent.body }} <a href="/privacy">{{ consent.privacy_link }}</a></p>
        </div>
      </div>
      <div class="mr-consent__actions">
        <button type="button" class="mr-consent__button mr-consent__button--secondary" data-consent-choice="essential">{{ consent.essential_only }}</button>
        <button type="button" class="mr-consent__button mr-consent__button--primary" data-consent-choice="analytics">{{ consent.allow_analytics }}</button>
      </div>
    </div>
  </section>
  <script src="/static/machreach_consent.js?v={{ deploy_version }}" defer></script>

  <!-- Floating focus timer widget (persists across pages) -->
  <aside id="focus-float" aria-label="Temporizador de enfoque" aria-live="polite">
    <button type="button" class="ff-main" onclick="activateFocusFloat()" aria-label="Abrir temporizador de enfoque">
      <span class="ff-mini-icon" aria-hidden="true">&#9201;</span>
      <span class="ff-copy">
        <span class="ff-time" id="ff-time">--:--</span>
        <span class="ff-label" id="ff-label">Focus</span>
      </span>
    </button>
    <button type="button" class="ff-close" onclick="closeFocusFloat()" aria-label="Minimizar temporizador" title="Minimizar temporizador">&minus;</button>
  </aside>

  <!-- Persistent silent <audio> element used for keepalive (prevents Chrome
       from throttling/freezing this tab while a focus session is running on
       another MachReach tab). The actual src is set programmatically. -->
  <audio id="focus-keepalive" loop preload="auto" style="display:none"></audio>
  <audio id="focus-alarm" preload="auto" style="display:none"></audio>

  <script src="/static/machreach_layout/layout-2.js?v={{ deploy_version }}"></script>

  <!-- Student i18n: Spanish translations (client-side) -->
  {% if lang == 'es' and account_type|default('student') == 'student' %}
  <script src="/static/machreach_layout/layout-3.js?v={{ deploy_version }}"></script>
  {% endif %}

  <!-- Student i18n: English fallback for newer pages that were authored in Spanish -->
  {% if lang == 'en' and account_type|default('student') == 'student' %}
  <script>
  (function(){
    var T = {
      "Principal": "Main",
      "Inicio": "Home",
      "Enfoque": "Focus",
      "Mis cursos": "My courses",
      "Mis Cursos": "My Courses",
      "Estudio": "Study",
      "Tarjetas": "Flashcards",
      "Comunidad": "Community",
      "Ranking": "Leaderboard",
      "Amigos": "Friends",
      "Tienda": "Shop",
      "Cuenta": "Account",
      "Notas": "Grades",
      "Ajustes": "Settings",
      "Cambiar modo": "Toggle theme",
      "Listo para ganar el semestre.": "Ready to win the semester.",
      "Liga activa": "Active league",
      "sigue subiendo": "keep climbing",

      "ANALYTICS SEMANALES": "WEEKLY ANALYTICS",
      "Tu semana de estudio.": "Your study week.",
      "Revisa cuanto estudiaste cada dia, cambia de semana, compara cursos y haz click en cualquier curso para ver su detalle diario.": "Review how much you studied each day, switch weeks, compare courses, and click any course to see the daily breakdown.",
      "Semana actual": "Current week",
      "Total semana": "Week total",
      "Mejor dia": "Best day",
      "Mejor día": "Best day",
      "Cursos activos": "Active courses",
      "Promedio diario": "Daily average",
      "Minutos por día": "Minutes per day",
      "Minutos por dia": "Minutes per day",
      "Linea de lunes a domingo para la semana seleccionada.": "Line from Monday to Sunday for the selected week.",
      "Línea de lunes a domingo para la semana seleccionada.": "Line from Monday to Sunday for the selected week.",
      "Horas por curso": "Hours per course",
      "Haz click en una barra para ver el detalle diario.": "Click a bar to see the daily detail.",
      "Detalle diario por curso": "Daily detail by course",
      "Selecciona un curso para ver como se repartio durante la semana.": "Select a course to see how it was distributed during the week.",
      "Selecciona un curso para ver cómo se repartió durante la semana.": "Select a course to see how it was distributed during the week.",
      "Minutos estudiados por dia en la semana seleccionada.": "Minutes studied per day in the selected week.",
      "No hay sesiones registradas esta semana.": "No sessions recorded this week.",
      "No hay datos para esta semana.": "No data for this week.",

      "ANALYTICS DE ESTUDIO": "STUDY ANALYTICS",
      "Tu rendimiento, sin humo.": "Your performance, no fluff.",
      "Minutos de enfoque, XP, cursos dominantes y consistencia real. Esto es para ver si estas estudiando de verdad o solo abriendo la app.": "Focus minutes, XP, dominant courses, and real consistency. This shows whether you are actually studying or just opening the app.",
      "Tiempo total": "Total time",
      "Sesiones": "Sessions",
      "Promedio": "Average",
      "Racha 🔥": "Streak 🔥",
      "acumulado en enfoque": "total in focus",
      "registros guardados": "saved records",
      "por sesion": "per session",
      "por sesión": "per session",
      "dias seguidos": "days in a row",
      "días seguidos": "days in a row",
      "Curso fuerte": "Strongest course",
      "Hora activa": "Active hour",
      "Consistencia": "Consistency",
      "Tendencia de enfoque": "Focus trend",
      "Minutos estudiados durante los ultimos 14 dias.": "Minutes studied during the last 14 days.",
      "Minutos estudiados durante los últimos 14 días.": "Minutes studied during the last 14 days.",
      "Tiempo por curso": "Time per course",
      "Donde se esta yendo tu energia.": "Where your energy is going.",
      "Donde se está yendo tu energía.": "Where your energy is going.",
      "Ritmo de XP": "XP rhythm",
      "Ultimas ganancias registradas.": "Latest recorded gains.",
      "Últimas ganancias registradas.": "Latest recorded gains.",
      "Mapa de constancia": "Consistency map",
      "Ultimos 35 dias. Mas verde significa mas minutos.": "Last 35 days. Greener means more minutes.",
      "Últimos 35 días. Más verde significa más minutos.": "Last 35 days. Greener means more minutes.",
      "Detalle por curso": "Course detail",
      "Resumen exacto de minutos acumulados.": "Exact summary of accumulated minutes.",

      "Quizzes de práctica": "Practice quizzes",
      "Elige de dónde vienen tus preguntas — una prueba oficial o tus propios apuntes.": "Choose where your questions come from — an official exam or your own notes.",
      "Generar quiz": "Generate quiz",
      "Reto diario": "Daily challenge",
      "5 preguntas · todos tus cursos": "5 questions · all your courses",
      "Calienta antes de estudiar y gana XP extra cuando completas quizzes.": "Warm up before studying and earn extra XP when you complete quizzes.",
      "Generar ahora": "Generate now",
      "preguntas": "questions",
      "intentos": "attempts",

      "Modo Enfoque": "Focus Mode",
      "Sesión de hoy": "Today's session",
      "Pausa": "Pause",
      "Reiniciar": "Restart",
      "Saltar": "Skip",
      "Ambiente": "Ambience",
      "Fuego": "Fire",
      "Lluvia": "Rain",
      "Bosque": "Forest",
      "Playa": "Beach",

      "Conexión a Canvas": "Canvas Connection",
      "No conectado": "Not connected",
      "Conectado": "Connected",
      "Conectar Canvas": "Connect Canvas",
      "Actualizar": "Update",
      "Desconectar": "Disconnect",

      "Planilla de Notas": "Grade Sheet",
      "Promedio del semestre": "Semester average",
      "Créditos del semestre": "Semester credits",
      "Promedio de la carrera": "Career average",
      "Créditos de la carrera": "Career credits",
      "Agregar evaluación": "Add evaluation",
      "Agregar ramo": "Add course",
      "Evaluación": "Evaluation",
      "Nota": "Grade",
      "Necesitas": "You need",

      "Logros y progreso": "Achievements and progress",
      "POSICIÓN": "POSITION",
      "Insignias Obtenidas": "Badges earned",
      "Todas las Insignias": "All badges",
      "Actividad Reciente": "Recent activity",

      "Perfil": "Profile",
      "Profile banner": "Profile banner",
      "Leaderboard flag": "Leaderboard flag",
      "Predeterminado": "Default",
      "Equipado": "Equipped",
      "EQUIPADO": "EQUIPPED",
      "Sin bandera": "No flag",

      "Suscripción": "Subscription",
      "Gratis": "Free",
      "GRATIS": "FREE",
      "ACTIVO": "ACTIVE",
      "Plan actual": "Current plan",
      "Mejorar a Plus": "Upgrade to Plus",
      "Gasta monedas en congeladores de racha 🔥 y banners de perfil. Gana monedas completando sesiones de enfoque, quizzes y tarjetas.": "Spend coins on streak 🔥 freezes and profile banners. Earn coins by completing focus sessions, quizzes, and flashcards.",

      "Comprar": "Buy",
      "Vender": "Sell",
      "Buscar": "Search",
      "Mis publicaciones": "My listings",
      "Vender archivo": "Sell a file",
      "Aún no hay apuntes compartidos.": "No shared notes yet.",

      "Suelta tu archivo": "Drop your file",
      "Sube un archivo": "Upload a file",

      "Admin": "Admin",
      "Analytics de producto": "Product analytics",
      "Tráfico diario · 14 días": "Daily traffic · 14 days",
      "Minutos de estudio · 14 días": "Study minutes · 14 days",
      "Quizzes creados · 14 días": "Quizzes created · 14 days",
      "Mazos de tarjetas · 14 días": "Flashcard decks · 14 days",
      "Features más usadas · 7 días": "Most used features · 7 days",
      "Páginas más vistas · 7 días": "Most viewed pages · 7 days",
      "Eventos de producto · 7 días": "Product events · 7 days",
      "XP por fuente · 30 días": "XP by source · 30 days",
      "Visitas hoy": "Visits today",
      "Usuarios únicos hoy": "Unique users today",
      "Registros hoy": "Signups today",
      "Focus min hoy": "Focus min today",
      "Quizzes hoy": "Quizzes today",
      "Mazos hoy": "Decks today",
      "Tarjetas hoy": "Cards today",
      "Apuntes hoy": "Notes today",
      "Usuarios totales": "Total users",
      "Activos 7 días": "Active 7 days"
    };

    Object.assign(T, {{ student_en_visible|default({})|tojson }});

    function trText(txt) {
      if (!txt) return null;
      if (T[txt]) return T[txt];
      var out = txt;
      Object.keys(T).sort(function(a,b){ return b.length - a.length; }).forEach(function(k){
        // Only substitute long multi-word phrases mid-text. Short/single-word
        // entries still apply via the exact whole-text match above —
        // substituting bare words into untranslated sentences produced
        // Spanglish like "Sincroniza tus courses de Canvas".
        if (k.length < 12 || k.indexOf(' ') === -1) return;
        if (/^[A-Za-zÁÉÍÓÚáéíóúÑñ]+$/.test(k)) {
          out = out.replace(new RegExp("(^|[^A-Za-zÁÉÍÓÚáéíóúÑñ])" + k + "(?=$|[^A-Za-zÁÉÍÓÚáéíóúÑñ])", "g"), function(m, lead){
            return lead + T[k];
          });
        } else if (out.indexOf(k) !== -1) {
          out = out.split(k).join(T[k]);
        }
      });
      return out !== txt ? out : null;
    }

    function translate(el) {
      if (el.childElementCount === 0) {
        var raw = el.textContent || "";
        var txt = raw.trim();
        var repl = trText(txt);
        if (txt && repl) el.textContent = raw.replace(txt, repl);
      }
      var ph = trText(el.placeholder || "");
      if (ph) el.placeholder = ph;
      var ttl = trText(el.title || "");
      if (ttl) el.title = ttl;
      if (el.getAttribute && el.getAttribute("aria-label")) {
        var aria = trText(el.getAttribute("aria-label"));
        if (aria) el.setAttribute("aria-label", aria);
      }
    }
    function translateTextNode(node) {
      if (!node || !node.nodeValue) return;
      var parent = node.parentElement;
      if (parent && /^(SCRIPT|STYLE|TEXTAREA|CODE|PRE)$/i.test(parent.tagName || "")) return;
      var raw = node.nodeValue;
      var txt = raw.trim();
      var repl = trText(txt);
      if (txt && repl) node.nodeValue = raw.replace(txt, repl);
    }
    function runTranslate(){
      var root = document.querySelector('.container') || document.body;
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
      while(walker.nextNode()) translate(walker.currentNode);
      var textWalker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
      while(textWalker.nextNode()) translateTextNode(textWalker.currentNode);
      document.querySelectorAll('h1,h2,h3,h4,h5,label,button,a,th,td,li,p,span,div,option,summary,figcaption,small,strong,em,b,i').forEach(translate);
      document.querySelectorAll('input[type="button"],input[type="submit"]').forEach(function(el){
        var val = trText(el.value || "");
        if (val) el.value = val;
      });
    }
    runTranslate();
    setTimeout(runTranslate, 400);
    setTimeout(runTranslate, 1200);
    setTimeout(runTranslate, 3000);
    try {
      var _mo = new MutationObserver(function(muts){
        for (var i=0; i<muts.length; i++){
          if (muts[i].addedNodes && muts[i].addedNodes.length){
            clearTimeout(window._mrEnTrTimer);
            window._mrEnTrTimer = setTimeout(runTranslate, 150);
            break;
          }
        }
      });
      _mo.observe(document.body, {childList:true, subtree:true});
    } catch(_){}
    var origAlert = window.alert;
    window.alert = function(msg) {
      var raw = String(msg || '').trim();
      origAlert(trText(raw) || raw);
    };
  })();
  </script>
  {% endif %}


</body>
</html>"""


# LAYOUT is ~4,500 lines; render_template_string would re-lex/parse/compile it
# on every request. Compile it once here and reuse the Template object.
_LAYOUT_TEMPLATE = app.jinja_env.from_string(LAYOUT)


def render_layout(**context):
    """Render the shared LAYOUT shell from the pre-compiled template.

    Equivalent to ``render_template_string(LAYOUT, **context)`` (runs Flask's
    context processors / default context) but without recompiling the 4.5k-line
    template on every request.
    """
    app.update_template_context(context)
    return _LAYOUT_TEMPLATE.render(context)


def _render(title: str, content: str, active_page: str = "", wide: bool = False, **kwargs):
    flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])
    nav = t_dict("nav")
    student_ui = t_dict("student_ui")
    is_admin = False
    acct_type = session.get("account_type", "student")
    if _logged_in():
        c = get_client(session["client_id"])
        is_admin = _is_admin()
        acct_type = (c.get("account_type") or "student") if c else acct_type
    return render_layout(
        title=title,
        content=render_template_string(content, **kwargs),
        logged_in=_logged_in(),
        messages=flashed,
        active_page=active_page,
        client_name=session.get("client_name", ""),
        wide=wide,
        nav=nav,
        student_ui=student_ui,
        tr=t,
        lang=session.get("lang", "es"),
        is_admin=is_admin,
        account_type=acct_type,
    )

@app.route("/")
def index():
    if _logged_in():
        return redirect(url_for("student_dashboard_page"))

    lang = session.get("lang", "es")
    from student.landing_design import render_landing_page
    resp = make_response(render_landing_page(lang))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


_LANDING_STATS_CACHE: dict = {"at": 0.0, "payload": None}
_LANDING_STATS_TTL_SECONDS = 300


@app.route("/api/public/landing-stats")
@limiter.limit("30 per minute")
def public_landing_stats():
    """Real aggregate numbers for the landing's stats strip.

    The strip used to show hardcoded figures; it now shows these or nothing.
    Aggregates only — no per-user data. Cached in-process so a landing-page
    burst costs one query round per five minutes, not one per visitor.
    """
    import time as _time

    now = _time.monotonic()
    if _LANDING_STATS_CACHE["payload"] is not None and now - _LANDING_STATS_CACHE["at"] < _LANDING_STATS_TTL_SECONDS:
        payload = _LANDING_STATS_CACHE["payload"]
    else:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from machreach_core.db import get_db, _fetchval

        # clients.last_seen_at is BIGINT epoch seconds, not a timestamp.
        week_ago_epoch = int(_time.time()) - 7 * 86400
        # The product's day is Chile's day — same convention as user quotas.
        today_cl = datetime.now(ZoneInfo("America/Santiago")).date().isoformat()
        try:
            with get_db() as db:
                students_week = int(_fetchval(
                    db,
                    "SELECT COUNT(*) FROM clients WHERE COALESCE(email_verified,0)=1 "
                    "AND COALESCE(last_seen_at,0) >= %s",
                    (week_ago_epoch,),
                ) or 0)
                minutes_today = int(_fetchval(
                    db,
                    "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress "
                    "WHERE plan_date LIKE %s",
                    (today_cl + "%",),
                ) or 0)
                universities = int(_fetchval(
                    db,
                    "SELECT COUNT(DISTINCT university_id) FROM clients "
                    "WHERE university_id IS NOT NULL AND COALESCE(email_verified,0)=1",
                ) or 0)
            payload = {
                "students_week": students_week,
                "hours_today": round(minutes_today / 60, 1),
                "universities": universities,
            }
            _LANDING_STATS_CACHE.update(at=now, payload=payload)
        except Exception:
            _log.exception("[landing-stats] aggregate query failed")
            payload = _LANDING_STATS_CACHE["payload"]
    if payload is None:
        return jsonify({"ok": False}), 503
    response = jsonify({"ok": True, **payload})
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.route("/design/")
def design_index():
    """Index of the app-design preview pages.

    These are static mocks with sample data. They are deliberately separate
    from /student/* so the live app is never served fabricated numbers.
    """
    from student.app_design import available_pages
    items = "".join(
        f'<li><a href="/design/{slug}">{_esc(label)}</a> '
        f'<code>/design/{slug}</code></li>'
        for slug, label in sorted(available_pages().items())
    )
    body = (
        "<h1>App design preview</h1>"
        "<p><strong>These pages are static mocks.</strong> They render sample "
        "data and read nothing from the database. The live student app is at "
        "<a href='/student'>/student</a>.</p>"
        f"<ul>{items}</ul>"
    )
    resp = make_response(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex'>"
        "<title>MachReach — app design preview</title></head>"
        f"<body style='font:16px/1.6 system-ui;max-width:46rem;margin:3rem auto;padding:0 1rem'>{body}</body></html>"
    )
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@app.route("/design/<page>")
def design_page(page: str):
    from student.app_design import render_design_page
    html = render_design_page(page)
    if html is None:
        return redirect(url_for("design_index"))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _auth_story_panel(kind: str, lang: str) -> str:
    is_en = lang == "en"
    if kind == "login":
        kicker = "Welcome back" if is_en else "Vuelve a tu ritmo"
        title = "Pick up where<br>you left off." if is_en else "Retoma donde<br>lo dejaste."
        desc = (
            "Your courses, focus blocks, notes and rankings stay ready in one place."
            if is_en else
            "Tus cursos, bloques de enfoque, notas y rankings siguen listos en un solo lugar."
        )
        status = "Session ready" if is_en else "Sesi&oacute;n lista"
        primary = "Focus streak" if is_en else "Racha de enfoque"
        secondary = "Courses organized" if is_en else "Cursos ordenados"
    else:
        kicker = "Start clean" if is_en else "Empieza limpio"
        title = "Study stops<br>feeling random." if is_en else "Estudiar deja<br>de ser una lata."
        desc = (
            "Create your account, activate the MachReach extension, and turn your semester into visible progress."
            if is_en else
            "Crea tu cuenta, activa la extensi&oacute;n de MachReach y convierte tu semestre en progreso visible."
        )
        status = "Extension flow" if is_en else "Flujo por extensi&oacute;n"
        primary = "Canvas detected" if is_en else "Canvas detectado"
        secondary = "Tools ready" if is_en else "Herramientas listas"

    return f"""
      <aside class="auth-story" aria-hidden="true">
        <div>
          <span class="auth-kicker"><span></span>{kicker}</span>
          <h2>{title}</h2>
          <p>{desc}</p>
          <div class="auth-proof">
            <span>&#10003; Focus + XP</span>
            <span>&#10003; Quizzes IA</span>
            <span>&#10003; Ranking</span>
          </div>
        </div>
        <div class="auth-preview">
          <div class="auth-preview-top">
            <div class="auth-dots"><i></i><i></i><i></i></div>
            <span>{status}</span>
          </div>
          <div class="auth-preview-body">
            <div class="auth-course-row">
              <div>
                <div class="auth-course-name">C&aacute;lculo I</div>
                <div class="auth-course-meta">{primary}</div>
              </div>
              <div class="auth-course-xp">+120 XP</div>
            </div>
            <div class="auth-course-row">
              <div>
                <div class="auth-course-name">&Aacute;lgebra Lineal</div>
                <div class="auth-course-meta">{secondary}</div>
              </div>
              <div class="auth-course-xp">3/4</div>
            </div>
            <div class="auth-build">
              <i style="--w:92%"></i><i style="--w:70%"></i><i style="--w:84%"></i><i style="--w:58%"></i>
            </div>
          </div>
        </div>
      </aside>
    """

def _render_auth_flow(slug: str, page_title: str, **payload):
    """Serve one of the supplied side-flow designs (verify email, recover
    password) with the server deciding which view opens."""
    from student.app_design import render_live_page

    csrf = generate_csrf()
    data = dict(payload)
    data["csrf"] = csrf
    data["messages"] = list(session.pop("_flashes", []) if "_flashes" in session else [])
    fragment = render_live_page(slug, data)
    if not fragment:
        return None
    # The design ships a complete stylesheet; the legacy layout CSS would only
    # fight it on specificity, so this page opts out of it.
    return render_layout(
        title=page_title,
        content=Markup(
            "<style>body>.nav,body>footer{display:none!important}"
            "body>.container{width:100%!important;max-width:none!important;margin:0!important;padding:0!important}"
            "body>.container>.toast-container{position:fixed;z-index:2147483000}</style>"
            + fragment
        ),
        logged_in=False,
        messages=[],
        active_page="login",
        client_name="",
        wide=True,
        nav=t_dict("nav"),
        student_ui=t_dict("student_ui"),
        tr=t,
        lang=session.get("lang", "es"),
        is_admin=False,
        account_type="student",
        dashboard_design=True,
    )


def _render_claude_auth(mode: str, *, ref: str = ""):
    """Serve the supplied Cuenta design while keeping Flask auth authoritative."""
    from student.app_design import render_live_page

    csrf = generate_csrf()
    messages = list(session.pop("_flashes", []) if "_flashes" in session else [])
    payload = {
        "mode": mode,
        "csrf": csrf,
        "ref": ref,
        "messages": messages,
    }
    fragment = render_live_page("cuenta", payload)
    if not fragment:
        return None
    page_title = "Crear cuenta" if mode == "signup" else "Iniciar sesión"
    fallback_fields = (
        f'<input type="hidden" name="csrf_token" value="{_esc(csrf)}">'
        + (f'<input type="hidden" name="ref" value="{_esc(ref)}">'
           '<p>¡Te invitaron! Tu amigo gana una semana gratis de Plus cuando te unes.</p>' if ref else "")
        + ('<label for="register-name">Nombre</label><input id="register-name" name="name">'
           '<label for="register-email">Correo</label><input id="register-email" name="email">'
           '<label for="register-password">Contraseña</label><input id="register-password" name="password">'
           if mode == "signup" else
           '<label for="login-email">Correo</label><input id="login-email" name="email">'
           '<label for="login-password">Contraseña</label><input id="login-password" name="password">')
    )
    content = Markup(
        "<style>body>.nav,body>footer{display:none!important}"
        "body>.container{width:100%!important;max-width:none!important;margin:0!important;padding:0!important}"
        "body>.container>.toast-container{position:fixed;z-index:2147483000}</style>"
        + fragment
        + f'<noscript><form method="post" action="/{"register" if mode == "signup" else "login"}">{fallback_fields}</form></noscript>'
    )
    return render_layout(
        title=page_title,
        content=content,
        logged_in=False,
        messages=[],
        active_page="register" if mode == "signup" else "login",
        client_name="",
        wide=True,
        nav=t_dict("nav"),
        student_ui=t_dict("student_ui"),
        tr=t,
        lang=session.get("lang", "es"),
        is_admin=False,
        account_type="student",
        # The design ships its own complete stylesheet. Loading the legacy
        # layout CSS alongside it only lets old `.auth-card`-era rules win on
        # specificity and repaint the card, so keep it off this page.
        dashboard_design=True,
    )


@app.route("/register", methods=["GET", "POST"])
@limiter.limit(RATE_REGISTER, methods=["POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        business = ""
        account_type = "student"
        if not name or not email or not password:
            flash(("error", t("auth.all_required")))
            return redirect(url_for("register"))
        if len(password) < PASSWORD_MIN_LENGTH:
            flash(("error", f"Password must be at least {PASSWORD_MIN_LENGTH} characters." if session.get("lang") == "en" else f"La contrasena debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."))
            return redirect(url_for("register"))
        if password2 and password2 != password:
            flash(("error", "Passwords do not match." if session.get("lang") == "en" else "Las contraseñas no coinciden."))
            return redirect(url_for("register"))
        existing_client = get_client_by_email(email)
        if existing_client and existing_client.get("email_verified"):
            _log_security("REGISTER_DUPLICATE", email=email)
            flash(("error", t("auth.email_exists")))
            return redirect(url_for("register"))
        created_new = existing_client is None
        if existing_client:
            client_id = int(existing_client["id"])
            _log_security("REGISTER_PENDING_REUSED", client_id=client_id, email=email)
        else:
            client_id = create_client(name, email, _hash_pw(password), business, account_type)
            _log_security("REGISTER_OK", client_id=client_id, email=email)

        # Referral: stash the inviter's code on this new account so they're
        # rewarded once it verifies its email (genuine sign-up only). The code was
        # captured on the /register GET into a hidden field + session.
        _ref = (request.form.get("ref") or session.get("referral_ref") or "").strip().upper()[:16]
        if _ref and created_new:
            try:
                from student import db as _sdb
                _sdb.set_pending_referral(client_id, _ref)
            except Exception as _re:
                print(f"[REFERRAL] Failed to stash ref for {email}: {_re}", flush=True)
            session.pop("referral_ref", None)

        # Queued, not sent here: SMTP inside a request held one of the few
        # gunicorn threads for its 30s timeout, and a handful of sign-ups
        # against a slow mail server could starve the whole service. The
        # worker claims the job within seconds.
        from machreach_core.verification_delivery import queue_verification_email
        queue_verification_email(client_id)
        return redirect(url_for("verify_email_pending", email=email, created="1"))
    # Referral capture: a shared link is /register?ref=CODE. Keep it in the
    # session so it survives the preview->submit roundtrip, and pass it into the
    # Canvas signup form as a hidden field.
    _ref_code = (request.args.get("ref") or session.get("referral_ref") or "").strip().upper()[:16]
    if _ref_code:
        session["referral_ref"] = _ref_code
    _auth_page = _render_claude_auth("signup", ref=_ref_code)
    if _auth_page is not None:
        return _auth_page
    _ref_hidden = f'<input type="hidden" name="ref" value="{_esc(_ref_code)}">' if _ref_code else ""
    _ref_banner = (
        '<div class="auth-ref">'
        + ("&#127873; You were invited! Your friend gets a free week of Plus when you join."
           if session.get("lang") == "en"
           else "&#127873; &iexcl;Te invitaron! Tu amigo gana una semana gratis de Plus cuando te unes.")
        + "</div>"
    ) if _ref_code else ""
    _is_en = session.get("lang") == "en"
    _story = _auth_story_panel("register", session.get("lang", "es"))
    _csrf_field = _csrf_hidden_input()
    _name_label = "Full name" if _is_en else "Nombre"
    _name_ph = "Your name" if _is_en else "Tu nombre"
    _password_label = "Password" if _is_en else "Contrase&ntilde;a"
    _password_ph = f"At least {PASSWORD_MIN_LENGTH} characters" if _is_en else f"M&iacute;nimo {PASSWORD_MIN_LENGTH} caracteres"
    _confirm_label = "Confirm password" if _is_en else "Confirmar contrase&ntilde;a"
    _confirm_ph = "Repeat your password" if _is_en else "Repite tu contrase&ntilde;a"
    _account_note = (
        "We'll email you a link to confirm your account. The extension can be activated after signup."
        if _is_en else
        "Te enviaremos un correo para confirmar tu cuenta. La extensi&oacute;n se activa despu&eacute;s de crearla."
    )
    _legal = (
        'By creating an account, you agree to our <a href="/terms">Terms of Service</a> and <a href="/privacy">Privacy Policy</a>.'
        if _is_en else
        'Al crear una cuenta, aceptas nuestros <a href="/terms">T&eacute;rminos del Servicio</a> y la <a href="/privacy">Pol&iacute;tica de Privacidad</a>.'
    )
    return render_layout(title="Register", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="register", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"), content=Markup(f"""
    <div class="auth-wrapper">
      <section class="auth-shell">
        {_story}
        <div class="auth-card">
          <div class="auth-card-head">
            <h1>{t("auth.create_account")}</h1>
            <p class="subtitle">{t("auth.create_subtitle")}</p>
          </div>
          {_ref_banner}
          <form class="auth-form" method="post" action="/register" autocomplete="off">
            {_csrf_field}
            {_ref_hidden}
            <div class="auth-field"><label for="register-name">{_name_label}</label><input id="register-name" name="name" type="text" required autocomplete="name" placeholder="{_name_ph}"></div>
            <div class="auth-field"><label for="register-email">{"Email" if _is_en else "Correo"}</label><input id="register-email" name="email" type="email" required autocomplete="username" placeholder="tu@correo.com"></div>
            <div class="auth-field"><label for="register-password">{_password_label}</label><input id="register-password" name="password" type="password" required minlength="{PASSWORD_MIN_LENGTH}" autocomplete="new-password" placeholder="{_password_ph}"></div>
            <div class="auth-field"><label for="register-password2">{_confirm_label}</label><input id="register-password2" name="password2" type="password" required minlength="{PASSWORD_MIN_LENGTH}" autocomplete="new-password" placeholder="{_confirm_ph}"></div>
            <button class="btn btn-primary auth-submit" type="submit">{t("auth.create_account")}</button>
            <p class="auth-note">{_account_note}</p>
          </form>
          <p class="auth-legal">{_legal}</p>
          <div class="auth-footer">{t("auth.have_account")} <a href="/login">{t("auth.log_in")}</a></div>
        </div>
      </section>
    </div>
    """))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit(RATE_LOGIN, methods=["POST"])
@limiter.limit(RATE_LOGIN_ACCOUNT, key_func=_submitted_email, methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        client = get_client_by_email(email)
        if not client or not _verify_pw(password, client["password"]):
            _log_security("LOGIN_FAIL", email=email)
            flash(("error", t("auth.invalid_creds")))
            return redirect(url_for("login"))
        if not client.get("email_verified"):
            return redirect(url_for("verify_email_pending", email=email))
        _maybe_upgrade_hash(client["id"], password, client["password"])
        _log_security("LOGIN_OK", client_id=client["id"], email=email)
        session.clear()
        session["client_id"] = client["id"]
        session["client_name"] = client["name"]
        session["account_type"] = "student"
        session["session_version"] = int(client.get("session_version") or 0)
        if client.get("is_admin"):
            from machreach_core import admin_security
            if admin_security.is_enabled(int(client["id"])):
                return redirect(url_for("admin_mfa_verify", next="/admin"))
            return redirect(url_for("admin_mfa_setup", next="/admin"))
        return redirect(url_for("student_dashboard_page"))
    _auth_page = _render_claude_auth("login")
    if _auth_page is not None:
        return _auth_page
    _is_en = session.get("lang") == "en"
    _story = _auth_story_panel("login", session.get("lang", "es"))
    _resend_summary = "Didn't get verification email?" if _is_en else "&iquest;No recibiste el correo de verificaci&oacute;n?"
    _resend_button = "Resend" if _is_en else "Reenviar"
    _csrf_field = _csrf_hidden_input()
    return render_layout(title="Login", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="login", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"), content=Markup(f"""
    <div class="auth-wrapper">
      <section class="auth-shell">
        {_story}
        <div class="auth-card">
          <div class="auth-card-head">
            <h1>{t("auth.welcome_back")}</h1>
            <p class="subtitle">{t("auth.sign_in_desc")}</p>
          </div>
          <form class="auth-form" method="post" action="/login">
            {_csrf_field}
            <div class="auth-field"><label for="login-email">{t("auth.email")}</label><input id="login-email" name="email" type="email" placeholder="you@school.edu" autocomplete="username" required></div>
            <div class="auth-field"><label for="login-password">{t("auth.password")}</label><input id="login-password" name="password" type="password" autocomplete="current-password" required></div>
            <button class="btn btn-primary auth-submit" type="submit">{t("auth.sign_in")}</button>
          </form>
          <div class="auth-link-row"><a href="/forgot-password">{t("auth.forgot_password")}</a></div>
          <details class="auth-details">
            <summary>{_resend_summary}</summary>
            <form class="auth-resend-form" method="post" action="/resend-verification">
              {_csrf_field}
              <label class="sr-only" for="resend-email">{t("auth.email")}</label>
              <input id="resend-email" name="email" type="email" placeholder="your@email.com" required>
              <button class="btn btn-outline btn-sm" type="submit">{_resend_button}</button>
            </form>
          </details>
          <div class="auth-footer">{t("auth.no_account")} <a href="/register">{t("auth.sign_up_free")}</a></div>
        </div>
      </section>
    </div>
    """))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/verify-email/<token>")
def verify_email(token):
    rec = get_valid_verification_token(token)
    if not rec:
        flash(("error", "Invalid or expired verification link. Please request a new one."))
        return redirect(url_for("login"))
    # Referral reward: now that this sign-up is genuine (email verified), grant the
    # inviter a free week of Plus. The pending marker, redemption ledger, and
    # entitlement extension commit together, so retries cannot lose or duplicate it.
    try:
        from student import subscription as _ssub
        _ssub.redeem_pending_referral_reward(rec["client_id"], 7)
    except Exception as _re:
        print(f"[REFERRAL] Reward grant failed for client {rec['client_id']}: {_re}", flush=True)
        flash(("error", "We could not finish verification yet. Please try the link again."))
        return redirect(url_for("login"))
    mark_email_verified(rec["client_id"])
    client = get_client(rec["client_id"])
    flash(("success", f"Email verified! Welcome, {_esc(client['name']) if client else ''}. You can now log in."))
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["POST"])
@limiter.limit(RATE_EMAIL)
@limiter.limit("3 per minute", key_func=_submitted_email)
def resend_verification():
    email = request.form.get("email", "").strip()
    client = get_client_by_email(email)
    if client and not client.get("email_verified"):
        from machreach_core.verification_delivery import queue_verification_email
        queue_verification_email(int(client["id"]))
    # If we know an email, take the user back to the dedicated pending page
    # so they land somewhere meaningful — the toast on /login was easy to miss.
    if email:
        return redirect(url_for("verify_email_pending", email=email, sent="1"))
    flash(("info", "If the email is registered, a new verification link has been sent."))
    return redirect(url_for("login"))


@app.route("/verify-email-pending")
def verify_email_pending():
    """Landing page for accounts whose email is still unverified — the resend
    form lives here rather than behind an easy-to-miss toast on /login."""
    email = (request.args.get("email") or "").strip()
    if request.args.get("delayed") == "1":
        state = "delayed"
    elif request.args.get("created") == "1":
        state = "created"
    elif request.args.get("sent") == "1":
        state = "sent"
    else:
        state = "blocked"
    return _render_auth_flow("verificar", "Verifica tu correo", state=state, email=email)


@app.route("/set-language/<lang>")
def set_language(lang):
    if lang in ("en", "es"):
        session["lang"] = lang
    referrer = str(request.referrer or "")
    parsed = urlsplit(referrer)
    if parsed.netloc and parsed.netloc == request.host:
        destination = parsed.path or "/"
        if parsed.query:
            destination += "?" + parsed.query
    else:
        destination = url_for("index")
    return redirect(destination)


# ---------------------------------------------------------------------------
# Routes — Forgot / Reset Password
# ---------------------------------------------------------------------------

@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(RATE_EMAIL, methods=["POST"])
@limiter.limit("3 per minute", key_func=_submitted_email, methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        client = get_client_by_email(email)
        if client:
            # The token is minted by the worker at send time, so a retried
            # delivery mails a link valid for its full hour — and no request
            # thread ever waits on SMTP.
            from machreach_core.verification_delivery import queue_password_reset
            try:
                queue_password_reset(int(client["id"]))
            except Exception:
                pass  # Don't reveal whether email was queued
        # Same answer either way, so the page can never be used to discover
        # which addresses have an account.
        return redirect(url_for("forgot_password", sent="1", email=email))
    state = "form"
    if request.args.get("ok") == "1":
        state = "ok"
    elif request.args.get("expired") == "1":
        state = "expired"
    elif request.args.get("sent") == "1":
        state = "sent"
    return _render_auth_flow(
        "recuperar", "Recupera tu contraseña",
        state=state, email=(request.args.get("email") or "").strip(),
    )


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def reset_password(token):
    reset = get_valid_reset_token(token)
    if not reset:
        # A dead link gets the flow's own "caducó" view, with a field to ask
        # for a fresh one, rather than a toast on the login page.
        return redirect(url_for("forgot_password", expired="1"))
    if request.method == "POST":
        pw1 = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if pw1 != pw2:
            flash(("error", t("auth.passwords_no_match")))
            return redirect(f"/reset-password/{token}")
        if len(pw1) < PASSWORD_MIN_LENGTH:
            flash(("error", t("auth.all_required")))
            return redirect(f"/reset-password/{token}")
        update_client_password(reset["client_id"], _hash_pw(pw1))
        mark_reset_token_used(token)
        _log_security("PASSWORD_RESET_OK", client_id=reset["client_id"])
        return redirect(url_for("forgot_password", ok="1"))
    return _render_auth_flow(
        "recuperar", "Elige una nueva contraseña", state="reset", token=token,
    )


# ---------------------------------------------------------------------------
# Routes — Change Password (from Settings)
# ---------------------------------------------------------------------------

@app.route("/settings/change-password", methods=["POST"])
def change_password():
    if not _logged_in():
        return redirect(url_for("login"))
    redir = "student_settings_page" if session.get("account_type") == "student" else "settings"
    current = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    client = get_client(session["client_id"])
    if not _verify_pw(current, client["password"]):
        _log_security("PASSWORD_CHANGE_FAIL", client_id=session["client_id"])
        flash(("error", t("settings.wrong_password")))
        return redirect(url_for(redir))
    if new_pw != confirm:
        flash(("error", t("auth.passwords_no_match")))
        return redirect(url_for(redir))
    if len(new_pw) < PASSWORD_MIN_LENGTH:
        flash(("error", t("auth.all_required")))
        return redirect(url_for(redir))
    update_client_password(session["client_id"], _hash_pw(new_pw))
    session["session_version"] = int(client.get("session_version") or 0) + 1
    _log_security("PASSWORD_CHANGE_OK", client_id=session["client_id"])
    flash(("success", t("settings.password_updated")))
    return redirect(url_for(redir))


# ---------------------------------------------------------------------------
# Routes — Delete Account
# ---------------------------------------------------------------------------

def _collect_ls_sub_ids(db, client_id: int) -> list:
    """Return the Lemon Squeezy subscription id(s) stored for this client.

    The student Plus subscription ID is read before local rows are
    deleted so recurring billing can be cancelled first.
    """
    import json as _json
    from machreach_core.db import _fetchone
    ids = []
    try:
        normalized = _fetchone(
            db,
            "SELECT ls_sub_id FROM student_subscription_state "
            "WHERE client_id = %s",
            (client_id,),
        )
        normalized_id = str((normalized or {}).get("ls_sub_id") or "").strip()
        if normalized_id:
            ids.append(normalized_id)
        crow = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (client_id,))
        prefs = _json.loads(((crow or {}).get("mail_preferences")) or "{}")
        sid2 = ((prefs.get("subscription") or {}).get("ls_sub_id") or "").strip()
        if sid2 and sid2 not in ids:
            ids.append(sid2)
    except Exception:
        # Callers use this list to cancel provider subscriptions on account
        # deletion. Returning a short list silently means a deleted account can
        # keep being billed, so this must never disappear without a trace.
        _log.warning(
            "failed to collect Lemon Squeezy subscription ids for client %s; "
            "found %d before the error", client_id, len(ids), exc_info=True,
        )
    return ids


def _cancel_ls_subs(ids, client_id: int) -> bool:
    """Cancel recurring billing at Lemon Squeezy before local data is removed.

    Called after the DB connection is released, since cancel_subscription makes a
    network call (up to 15s) and we don't want to hold the connection open for it.
    """
    if not ids:
        return True
    try:
        from machreach_core import config as _cfg
        from machreach_core import lemonsqueezy as ls
        test_mode = (
            _cfg.LEMON_SQUEEZY_TEST_MODE
            and str(client_id) == str(_cfg.LEMON_SQUEEZY_SANDBOX_CLIENT_ID)
        )
        for sid in set(ids):
            cancelled = (
                ls.cancel_subscription(sid, test_mode=True)
                if test_mode
                else ls.cancel_subscription(sid)
            )
            if not cancelled:
                return False
        return True
    except Exception:
        _log.exception("[delete-account] LS subscription cancel failed for client %s", client_id)
        return False


@app.route("/settings/delete-account", methods=["POST"])
def delete_account():
    if not _logged_in():
        return redirect(url_for("login"))
    confirm_text = request.form.get("confirm", "").strip()
    redir = "student_settings_page" if session.get("account_type") == "student" else "settings"
    if confirm_text not in ("DELETE", "ELIMINAR"):
        flash(("error", "Please type DELETE to confirm."))
        return redirect(url_for(redir))
    client_id = session["client_id"]
    from machreach_core.db import get_db, _exec, _fetchall, _USE_PG
    with get_db() as db:
        _ls_ids = _collect_ls_sub_ids(db, client_id)
    if not _cancel_ls_subs(_ls_ids, client_id):
        _log_security("ACCOUNT_DELETE_BILLING_CANCEL_FAILED", client_id=client_id)
        flash(("error", "We could not cancel your active subscription. Your account was kept intact; please try again or contact support."))
        return redirect(url_for(redir))

    with get_db() as db:
        if _USE_PG:
            table_rows = _fetchall(
                db,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'",
            )
        else:
            table_rows = _fetchall(
                db,
                "SELECT name AS table_name FROM sqlite_master WHERE type = 'table'",
            )
        existing_tables = {row["table_name"] for row in table_rows}
        # Student data (flashcards & quiz_questions cascade-delete via their parent tables)
        for tbl in ["student_quizzes",
                     "student_flashcard_decks", "student_notes",
                     "student_course_files", "student_exams", "student_study_progress",
                     "student_study_plans", "student_assignment_progress",
                     "student_schedule_settings",
                     "student_xp", "student_badges", "student_email_prefs",
                     "student_canvas_tokens", "student_courses"]:
            if tbl in existing_tables:
                _exec(db, f"DELETE FROM {tbl} WHERE client_id = %s", (client_id,))
        for tbl2 in ["password_reset_tokens", "email_verification_tokens"]:
            if tbl2 in existing_tables:
                _exec(db, f"DELETE FROM {tbl2} WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM clients WHERE id = %s", (client_id,))
    session.clear()
    flash(("success", t("settings.account_deleted")))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes — Team Seats
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("student_dashboard_page"))

def _admin_delete_client_account(client_id: int) -> dict:
    """Best-effort full account removal for the admin panel."""
    from machreach_core.db import get_db, _exec, _fetchall, _USE_PG

    target = get_client(client_id)
    if not target:
        return {"ok": False, "error": "User not found."}
    if target.get("is_admin"):
        return {"ok": False, "error": "Administrator accounts cannot be deleted from the panel."}

    deleted_steps = []
    with get_db() as db:
        _ls_ids = _collect_ls_sub_ids(db, client_id)
    if not _cancel_ls_subs(_ls_ids, client_id):
        return {"ok": False, "error": "Billing cancellation failed; the account was kept intact."}

    with get_db() as db:
        for table, column in [
            ("email_verification_tokens", "client_id"),
            ("password_reset_tokens", "client_id"),
        ]:
            try:
                _exec(db, f"DELETE FROM {table} WHERE {column} = %s", (client_id,))
            except Exception:
                pass
        deleted_steps.append("account-linked data")

        try:
            if _USE_PG:
                rows = _fetchall(db, """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND column_name IN ('client_id','owner_id','member_client_id','friend_client_id','challenger_id','opponent_id','seller_id','buyer_id')
                    ORDER BY table_name
                """)
            else:
                tables = _fetchall(db, "SELECT name AS table_name FROM sqlite_master WHERE type='table'")
                rows = []
                for tbl in tables:
                    name = tbl["table_name"]
                    if name.startswith("sqlite_"):
                        continue
                    for col in _fetchall(db, f"PRAGMA table_info({name})"):
                        if col.get("name") in ("client_id", "owner_id", "member_client_id", "friend_client_id", "challenger_id", "opponent_id", "seller_id", "buyer_id"):
                            rows.append({"table_name": name, "column_name": col["name"]})
            by_table: dict[str, list[str]] = {}
            for row in rows:
                table = row["table_name"]
                if table in {"clients", "retired_billing_cancellations"}:
                    continue
                by_table.setdefault(table, []).append(row["column_name"])
            for _ in range(3):
                for table, cols in by_table.items():
                    where = " OR ".join(f"{col} = %s" for col in cols)
                    try:
                        _exec(db, f"DELETE FROM {table} WHERE {where}", tuple([client_id] * len(cols)))
                    except Exception:
                        pass
            deleted_steps.append("student data")
        except Exception as e:
            print(f"[ADMIN] dynamic user cleanup skipped for {client_id}: {e}", flush=True)

        _exec(db, "DELETE FROM clients WHERE id = %s", (client_id,))

    return {"ok": True, "email": target.get("email"), "steps": deleted_steps}


@app.route("/admin", methods=["GET", "POST"])
@app.route("/admin/broadcast", methods=["GET", "POST"])
def admin_dashboard():
    """Owner-only admin dashboard for broadcasts and user management."""
    if not _is_admin():
        return redirect(url_for("dashboard"))

    from machreach_core.db import get_all_client_emails

    users = get_all_client_emails()
    error_msg = ""

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "broadcast":
            subject = request.form.get("subject", "").strip()
            body = request.form.get("body", "").strip()
            confirm_phrase = request.form.get("confirm_phrase", "").strip()
            if not subject or not body:
                error_msg = "Subject and body are required."
            elif confirm_phrase != "SEND TO ALL USERS":
                error_msg = "Type SEND TO ALL USERS to confirm the broadcast."
            elif not _admin_secret_ok():
                error_msg = "Confirm your password and MFA at /admin/reauth before this action."
            else:
                sent_count = 0
                failed = []
                for u in users:
                    email = (u.get("email") or "").strip()
                    if not email:
                        continue
                    ok = _send_system_email(email, subject, body)
                    if ok:
                        sent_count += 1
                    else:
                        failed.append(email)
                if failed:
                    flash(("warning", f"Broadcast sent to {sent_count} users. Failed: {len(failed)}."))
                else:
                    flash(("success", f"Broadcast sent to {sent_count} users."))
                _log_admin_action("broadcast", target="all_users", sent=sent_count, failed=len(failed), subject=subject[:120])
                return redirect(url_for("admin_dashboard"))
        elif action == "delete_user":
            target_id = int(request.form.get("client_id") or 0)
            typed_email = (request.form.get("confirm_email") or "").strip().lower()
            confirm_phrase = request.form.get("confirm_phrase", "").strip()
            target = get_client(target_id)
            if not target:
                error_msg = "User not found."
            elif typed_email != (target.get("email") or "").strip().lower():
                error_msg = "Type the user's exact email to confirm deletion."
            elif confirm_phrase != "DELETE USER":
                error_msg = "Type DELETE USER to confirm account deletion."
            elif not _admin_secret_ok():
                error_msg = "Confirm your password and MFA at /admin/reauth before this action."
            elif target_id == session.get("client_id"):
                error_msg = "You cannot delete your own logged-in account."
            else:
                result = _admin_delete_client_account(target_id)
                if result.get("ok"):
                    _log_admin_action("delete_user", target=str(target_id), email=result.get("email", ""))
                    flash(("success", f"Deleted account: {result.get('email')}"))
                    return redirect(url_for("admin_dashboard"))
                error_msg = result.get("error") or "Could not delete that account."

    users = get_all_client_emails()
    user_rows = "".join(
        f"""
        <tr>
          <td>{_esc(u.get("name") or "")}</td>
          <td style="font-family:'Nunito',sans-serif;font-size:13px;">{_esc(u.get("email") or "")}</td>
          <td>{_esc(str(u.get("id") or ""))}</td>
          <td style="width:320px;">
            <form method="POST" style="display:grid;grid-template-columns:1fr 120px auto;gap:8px;align-items:center;">
              <input type="hidden" name="action" value="delete_user">
              <input type="hidden" name="client_id" value="{_esc(str(u.get("id") or ""))}">
              <input name="confirm_email" placeholder="Type email to delete" autocomplete="off" style="font-size:12px;padding:8px;">
              <input name="confirm_phrase" placeholder="DELETE USER" autocomplete="off" style="font-size:12px;padding:8px;">
              <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);" onclick="return confirm('Delete this user and their data?')">Delete</button>
            </form>
          </td>
        </tr>
        """
        for u in users
    )

    return _render("Admin", _admin_shell("/admin", "Panel", "Envía un correo a todos los usuarios y administra cuentas. El acceso viene solo de la base de datos y queda auditado.", f"""
    {'<div class="alert alert-red" style="margin-bottom:16px;">' + _esc(error_msg) + '</div>' if error_msg else ''}
    <div class="card" style="max-width:820px;">
      <div class="card-header"><h2>Send Email to All Users</h2></div>
      <form method="POST">
        <input type="hidden" name="action" value="broadcast">
        <div class="form-group">
          <label>Subject</label>
          <input name="subject" placeholder="Important: MachReach Platform Update" required style="font-size:15px;">
        </div>
        <div class="form-group">
          <label>Message Body</label>
          <textarea name="body" rows="10" placeholder="Hi there,&#10;&#10;We have an important update..." required style="font-size:14px;line-height:1.7;"></textarea>
          <p class="form-hint">Plain text. Will be wrapped in the standard MachReach email template.</p>
        </div>
        <div class="form-group">
          <label>Confirmation</label>
          <input name="confirm_phrase" placeholder="Type SEND TO ALL USERS" autocomplete="off" required style="font-size:15px;">
        </div>
        <div style="display:flex;gap:12px;align-items:center;">
          <button type="submit" class="btn btn-primary" style="font-size:15px;padding:10px 28px;" onclick="return confirm('Send this email to ALL {len(users)} registered users?')">&#128640; Send to {len(users)} Users</button>
        </div>
      </form>
    </div>

    <div class="card" style="margin-top:20px;">
      <div class="card-header"><h2>User Accounts ({len(users)})</h2></div>
      <table>
        <thead><tr><th>Name</th><th>Email</th><th>ID</th><th>Delete Account</th></tr></thead>
        <tbody>
          {user_rows}
        </tbody>
      </table>
    </div>
    """), active_page="admin", wide=True)


_ANALYTICS_TABLE_READY = False


def _ensure_product_analytics_table():
    """Create lightweight product analytics storage lazily."""
    global _ANALYTICS_TABLE_READY
    if _ANALYTICS_TABLE_READY:
        return
    try:
        from machreach_core.db import get_db, _USE_PG, _exec
        with get_db() as db:
            if _USE_PG:
                _exec(db, """
                    CREATE TABLE IF NOT EXISTS product_analytics_events (
                      id SERIAL PRIMARY KEY,
                      client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                      event_type TEXT NOT NULL,
                      path TEXT DEFAULT '',
                      method TEXT DEFAULT '',
                      metadata TEXT DEFAULT '',
                      created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
            else:
                _exec(db, """
                    CREATE TABLE IF NOT EXISTS product_analytics_events (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                      event_type TEXT NOT NULL,
                      path TEXT DEFAULT '',
                      method TEXT DEFAULT '',
                      metadata TEXT DEFAULT '',
                      created_at TEXT DEFAULT (datetime('now','localtime'))
                    )
                """)
            _exec(db, "CREATE INDEX IF NOT EXISTS idx_product_events_created ON product_analytics_events(created_at)")
            _exec(db, "CREATE INDEX IF NOT EXISTS idx_product_events_type ON product_analytics_events(event_type)")
        _ANALYTICS_TABLE_READY = True
    except Exception as e:
        print(f"[analytics] table init skipped: {e}", flush=True)


def _record_product_event(event_type: str, metadata: dict | None = None):
    try:
        _ensure_product_analytics_table()
        from machreach_core.db import get_db, _exec
        cid = session.get("client_id")
        with get_db() as db:
            _exec(
                db,
                "INSERT INTO product_analytics_events (client_id, event_type, path, method, metadata) VALUES (%s, %s, %s, %s, %s)",
                (cid, event_type, request.path[:300], request.method, json.dumps(metadata or {}, ensure_ascii=False)[:1200]),
            )
    except Exception:
        pass


def _analytics_admin_filter_sql() -> str:
    return "(client_id IS NULL OR client_id NOT IN (SELECT id FROM clients WHERE COALESCE(is_admin, 0) = 1))"


def _analytics_should_skip_request() -> bool:
    path = request.path or ""
    if path.startswith(("/static/", "/favicon", "/health", "/admin/analytics")):
        return True
    try:
        if _is_admin():
            return True
    except Exception:
        pass
    remote = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip().lower()
    return remote in {"127.0.0.1", "::1", "localhost"}


def _analytics_feature_event_for_path(path: str) -> str | None:
    feature_pages = [
        ("/student/focus", "view_focus"),
        ("/student/analytics", "view_analytics"),
        ("/student/courses", "view_courses"),
        ("/student/quizzes", "view_quizzes"),
        ("/student/quiz", "view_quizzes"),
        ("/student/flashcards", "view_flashcards"),
        ("/student/leaderboard", "view_leaderboard"),
        ("/student/shop", "view_shop"),
        ("/student/canvas", "view_canvas"),
        ("/student/grades", "view_grades"),
        ("/student/profile", "view_profile"),
        ("/student/dashboard", "view_dashboard"),
    ]
    for prefix, event in feature_pages:
        if path == prefix or path.startswith(prefix + "/"):
            return event
    if path in {"/dashboard", "/student"}:
        return "view_dashboard"
    return None


@app.before_request
def _machreach_product_analytics_hook():
    """Track product usage for the owner analytics dashboard."""
    path = request.path or ""
    if _analytics_should_skip_request():
        return
    if request.method == "GET":
        wants_html = "text/html" in (request.headers.get("Accept") or "")
        if wants_html and not path.startswith("/api/"):
            _record_product_event("page_view")
            feature_event = _analytics_feature_event_for_path(path)
            if feature_event:
                _record_product_event(feature_event)
        return
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    feature_map = [
        ("/api/student/focus/save", "focus_session_saved"),
        ("/api/student/quiz", "quiz_action"),
        ("/api/student/quizzes", "quiz_action"),
        ("/api/student/flashcard", "flashcard_action"),
        ("/api/student/flashcards", "flashcard_action"),
        ("/api/student/canvas", "canvas_action"),
    ]
    for prefix, event in feature_map:
        if path.startswith(prefix):
            _record_product_event(event)
            return


def _admin_metric(sql_pg: str, sql_lite: str | None = None, params=()) -> int:
    try:
        from machreach_core.db import get_db, _USE_PG, _fetchval
        with get_db() as db:
            return int(_fetchval(db, sql_pg if _USE_PG else (sql_lite or sql_pg), params) or 0)
    except Exception:
        return 0


def _admin_rows(sql_pg: str, sql_lite: str | None = None, params=()) -> list[dict]:
    try:
        from machreach_core.db import get_db, _USE_PG, _fetchall
        with get_db() as db:
            return _fetchall(db, sql_pg if _USE_PG else (sql_lite or sql_pg), params) or []
    except Exception:
        return []


@app.route("/admin/jobs", methods=["GET", "POST"])
def admin_jobs():
    if not _is_admin():
        return redirect(url_for("dashboard"))

    from machreach_core.db import list_async_jobs, retry_async_job

    error_msg = ""
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "retry_job":
            job_type = request.form.get("job_type", "").strip()
            job_key = request.form.get("job_key", "").strip()
            if job_type not in {"student_quiz_generation", "student_flashcard_generation", "verification_email"} or not job_key:
                error_msg = "Invalid job retry request."
            elif not _admin_secret_ok():
                error_msg = "Confirm your password and MFA at /admin/reauth before this action."
            elif retry_async_job(job_type, job_key):
                _log_admin_action("retry_job", target=f"{job_type}:{job_key}")
                flash(("success", f"Queued retry for {job_type}:{job_key}"))
                return redirect(url_for("admin_jobs"))
            else:
                error_msg = "Could not retry that job. It may no longer be failed."

    jobs = list_async_jobs(limit=80)
    heartbeats = list_async_jobs(limit=5, job_type="worker_heartbeat")
    worker = heartbeats[0] if heartbeats else None
    worker_status = "unknown"
    worker_updated = ""
    if worker:
        worker_status = str(worker.get("status") or "unknown")
        worker_updated = str(worker.get("updated_at") or "")

    counts = {k: 0 for k in ("queued", "running", "error", "done")}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    recent_users = _admin_rows(
        """
        SELECT id, name, email, COALESCE(account_type, 'student') AS account_type, created_at::text AS created_at
        FROM clients
        ORDER BY created_at DESC
        LIMIT 12
        """,
        """
        SELECT id, name, email, COALESCE(account_type, 'student') AS account_type, created_at
        FROM clients
        ORDER BY created_at DESC
        LIMIT 12
        """,
    )
    coin_orders = _admin_rows(
        """
        SELECT o.order_id, o.client_id, c.email, o.pack_key, o.coins_credited, o.created_at::text AS created_at
        FROM student_coin_pack_orders o
        LEFT JOIN clients c ON c.id = o.client_id
        ORDER BY o.created_at DESC
        LIMIT 12
        """,
        """
        SELECT o.order_id, o.client_id, c.email, o.pack_key, o.coins_credited, o.created_at
        FROM student_coin_pack_orders o
        LEFT JOIN clients c ON c.id = o.client_id
        ORDER BY o.created_at DESC
        LIMIT 12
        """,
    )

    job_labels = {
        "student_quiz_generation": "Quiz generation",
        "student_flashcard_generation": "Flashcard generation",
    }
    status_colors = {
        "queued": "#7C3AED",
        "running": "#2563EB",
        "sending": "#2563EB",
        "done": "#059669",
        "error": "#DC2626",
    }

    def _pill(status: str) -> str:
        color = status_colors.get(status, "#6B7280")
        return f"<span class='job-pill' style='background:{color};'>{_esc(status)}</span>"

    def _retry_form(job: dict) -> str:
        if job.get("status") != "error" or job.get("job_type") not in {"student_quiz_generation", "student_flashcard_generation"}:
            return ""
        return f"""
        <form method="POST" style="margin:0;display:flex;gap:6px;align-items:center;justify-content:flex-end;flex-wrap:wrap;">
          <input type="hidden" name="action" value="retry_job">
          <input type="hidden" name="job_type" value="{_esc(str(job.get('job_type') or ''))}">
          <input type="hidden" name="job_key" value="{_esc(str(job.get('job_key') or ''))}">
          <button class="btn btn-outline btn-sm" type="submit">Retry</button>
        </form>
        """

    def _jobs_table(rows: list[dict], empty: str) -> str:
        if not rows:
            return f"<div class='admin-empty'>{_esc(empty)}</div>"
        body = []
        for job in rows:
            payload = job.get("payload") or {}
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            body.append(f"""
            <tr>
              <td>{_esc(job_labels.get(str(job.get('job_type') or ''), str(job.get('job_type') or '')))}</td>
              <td><code>{_esc(str(job.get('job_key') or ''))}</code></td>
              <td>{_pill(str(job.get('status') or 'unknown'))}</td>
              <td>{_esc(str(job.get('attempts') or 0))}/{_esc(str(job.get('max_attempts') or 3))}</td>
              <td>{_esc(str(job.get('progress') or ''))}<br><small>{_esc(str(job.get('error') or ''))}</small></td>
              <td><code>{_esc(payload_text[:220])}</code></td>
              <td>{_esc(str(job.get('updated_at') or ''))}</td>
              <td>{_retry_form(job)}</td>
            </tr>
            """)
        return """
        <table class="jobs-table">
          <thead><tr><th>Type</th><th>Key</th><th>Status</th><th>Attempts</th><th>Message</th><th>Payload</th><th>Updated</th><th>Action</th></tr></thead>
          <tbody>
        """ + "".join(body) + "</tbody></table>"

    def _simple_table(headers: list[str], rows: list[dict], keys: list[str], empty: str) -> str:
        if not rows:
            return f"<div class='admin-empty'>{_esc(empty)}</div>"
        head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{_esc(str(row.get(key, '')))}</td>" for key in keys) + "</tr>"
            for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    failed_quiz = [j for j in jobs if j.get("job_type") == "student_quiz_generation" and j.get("status") == "error"]
    failed_flashcards = [j for j in jobs if j.get("job_type") == "student_flashcard_generation" and j.get("status") == "error"]
    active_jobs = [j for j in jobs if j.get("status") in ("queued", "running", "sending")]

    cards = "".join(
        f"<div class='admin-metric'><div class='k'>{label}</div><div class='v'>{value}</div></div>"
        for label, value in [
            ("Worker", worker_status),
            ("Queued", counts.get("queued", 0)),
            ("Running", counts.get("running", 0) + counts.get("sending", 0)),
            ("Failed", counts.get("error", 0)),
            ("Done", counts.get("done", 0)),
        ]
    )

    body = f"""
    <style>
      .admin-jobs {{ display:grid; gap:18px; }}
      .admin-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }}
      .admin-metric {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:16px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-metric .k {{ color:#77756F; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
      .admin-metric .v {{ font-family:'Bricolage Grotesque',sans-serif; font-size:26px; font-weight:650; margin-top:8px; color:#1A1A1F; }}
      .admin-panel {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); overflow:auto; }}
      .admin-panel h2 {{ margin:0 0 12px; font-family:'Bricolage Grotesque',sans-serif; font-size:24px; }}
      .admin-note {{ color:#77756F; background:#FBF8F0; border:1px solid #E2DCCC; border-radius:14px; padding:12px 14px; margin:0; line-height:1.45; }}
      .admin-empty {{ color:#94939C; background:#FBF8F0; border:1px dashed #D8D0BE; border-radius:14px; padding:16px; }}
      .job-pill {{ display:inline-flex; color:#fff; border-radius:999px; padding:4px 8px; font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
      .jobs-table small {{ color:#77756F; }}
      .jobs-table code {{ font-size:11px; white-space:normal; }}
    </style>
    <div class="admin-jobs">
      {'<div class="alert alert-red">' + _esc(error_msg) + '</div>' if error_msg else ''}
      <div class="admin-grid">{cards}</div>
      <div class="admin-panel">
        <h2>Worker heartbeat</h2>
        <div class="admin-note">Last heartbeat: <b>{_esc(worker_updated or 'never')}</b>. If this stops updating after deploy, check the Render <code>machreach-worker</code> service logs/env vars.</div>
      </div>
      <div class="admin-panel"><h2>Active jobs</h2>{_jobs_table(active_jobs, "No active queued or running jobs.")}</div>
      <div class="admin-panel"><h2>Failed quiz generations</h2>{_jobs_table(failed_quiz, "No failed quiz generations.")}</div>
      <div class="admin-panel"><h2>Failed flashcard generations</h2>{_jobs_table(failed_flashcards, "No failed flashcard generations.")}</div>
      <div class="admin-panel"><h2>Recent job activity</h2>{_jobs_table(jobs[:30], "No background jobs recorded yet.")}</div>
      <div class="admin-panel"><h2>Recent signups</h2>{_simple_table(["ID","Name","Email","Type","Created"], recent_users, ["id","name","email","account_type","created_at"], "No signups yet.")}</div>
      <div class="admin-panel"><h2>Recent coin-pack orders</h2>{_simple_table(["Order","Client","Email","Pack","Coins","Created"], coin_orders, ["order_id","client_id","email","pack_key","coins_credited","created_at"], "No coin-pack orders recorded yet.")}</div>
    </div>
    """
    return _render("Admin jobs", _admin_shell(
        "/admin/jobs", "⚙️ Jobs & worker",
        "Generación de quizzes y flashcards en segundo plano, reintentos, latido del worker, registros y cobros.",
        body,
    ), active_page="admin", wide=True)


def _current_process_rss_mb() -> float | None:
    """Best-effort current worker memory in MB.

    Render runs Linux, where /proc/self/status gives the actual RSS. Local
    Windows/dev installs may fall back to psutil when it is available.
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return round(int(parts[1]) / 1024, 1)
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        pass
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            # ``ctypes.windll`` only exists on Windows.  Using ``getattr``
            # keeps this guarded Windows path type-checkable on Linux CI.
            windll = getattr(ctypes, "windll")
            kernel32 = windll.kernel32
            psapi = windll.psapi
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            if ok:
                return round(float(counters.WorkingSetSize) / (1024 * 1024), 1)
    except Exception:
        pass
    try:
        import resource  # type: ignore
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)  # type: ignore[attr-defined]
        return round(rss / 1024, 1)
    except Exception:
        return None


def _estimate_user_ram_pressure(row: dict) -> tuple[str, str]:
    """Estimate transient RAM pressure from recent activity.

    Flask users share the same Python worker, so this is not an exact per-user
    memory allocation. The estimate is intentionally conservative and meant to
    highlight users likely to create temporary memory spikes.
    """
    events = int(row.get("events_15m") or 0)
    actions = int(row.get("actions_15m") or 0)
    heavy = int(row.get("heavy_15m") or 0)
    est = min(96.0, round((events * 0.08) + (actions * 0.7) + (heavy * 3.5), 1))
    if heavy >= 6 or est >= 24:
        level = "Alto"
    elif heavy >= 2 or est >= 8:
        level = "Medio"
    else:
        level = "Bajo"
    return f"~{est:.1f} MB", level


def _admin_user_ram_rows(external_events: str) -> list[dict]:
    rows = _admin_rows(
        f"""
        SELECT
          c.id,
          c.name,
          c.email,
          COUNT(e.id) AS events_15m,
          SUM(CASE WHEN e.event_type = 'page_view' THEN 1 ELSE 0 END) AS page_views_15m,
          SUM(CASE WHEN e.event_type <> 'page_view' THEN 1 ELSE 0 END) AS actions_15m,
          SUM(CASE WHEN e.event_type IN ('quiz_action','flashcard_action','canvas_action','focus_session_saved') THEN 1 ELSE 0 END) AS heavy_15m,
          MAX(e.created_at)::text AS last_seen
        FROM product_analytics_events e
        JOIN clients c ON c.id = e.client_id
        WHERE e.client_id IS NOT NULL
          AND e.created_at >= NOW() - INTERVAL '15 minutes'
          AND {external_events}
        GROUP BY c.id, c.name, c.email
        ORDER BY heavy_15m DESC, events_15m DESC, last_seen DESC
        LIMIT 30
        """,
        f"""
        SELECT
          c.id,
          c.name,
          c.email,
          COUNT(e.id) AS events_15m,
          SUM(CASE WHEN e.event_type = 'page_view' THEN 1 ELSE 0 END) AS page_views_15m,
          SUM(CASE WHEN e.event_type <> 'page_view' THEN 1 ELSE 0 END) AS actions_15m,
          SUM(CASE WHEN e.event_type IN ('quiz_action','flashcard_action','canvas_action','focus_session_saved') THEN 1 ELSE 0 END) AS heavy_15m,
          MAX(e.created_at) AS last_seen
        FROM product_analytics_events e
        JOIN clients c ON c.id = e.client_id
        WHERE e.client_id IS NOT NULL
          AND datetime(e.created_at) >= datetime('now','localtime','-15 minutes')
          AND {external_events}
        GROUP BY c.id, c.name, c.email
        ORDER BY heavy_15m DESC, events_15m DESC, last_seen DESC
        LIMIT 30
        """,
    )
    for row in rows:
        estimate, level = _estimate_user_ram_pressure(row)
        row["user"] = f"{row.get('name') or 'Sin nombre'} · {row.get('email') or 'sin correo'}"
        row["ram_pressure"] = estimate
        row["load_level"] = level
    return rows


@app.route("/admin/analytics")
def admin_product_analytics():
    if not _is_admin():
        return redirect(url_for("dashboard"))
    _ensure_product_analytics_table()

    today_pg = "created_at >= CURRENT_DATE"
    today_lite = "date(created_at) = date('now','localtime')"
    week_pg = "created_at >= NOW() - INTERVAL '7 days'"
    week_lite = "datetime(created_at) >= datetime('now','localtime','-7 days')"
    external_events = _analytics_admin_filter_sql()
    rss_mb = _current_process_rss_mb()
    active_15m_pg = f"created_at >= NOW() - INTERVAL '15 minutes' AND {external_events}"
    active_15m_lite = f"datetime(created_at) >= datetime('now','localtime','-15 minutes') AND {external_events}"

    event_labels = {
        "view_dashboard": "Inicio",
        "view_focus": "Modo enfoque",
        "view_analytics": "Analytics estudiante",
        "view_courses": "Cursos",
        "view_quizzes": "Quizzes",
        "view_flashcards": "Tarjetas",
        "view_leaderboard": "Ranking",
        "view_shop": "Tienda",
        "view_canvas": "Canvas",
        "view_grades": "Planilla de notas",
        "view_profile": "Perfil",
        "focus_session_saved": "Sesion de enfoque guardada",
        "quiz_action": "Acciones de quiz",
        "flashcard_action": "Acciones de tarjetas",
        "canvas_action": "Acciones de Canvas",
    }

    def is_noise_path(path: str) -> bool:
        p = (path or "").lower().strip()
        if not p:
            return True
        noise_bits = (
            ".php", "wp-admin", "wp-login", "wordpress", "phpmyadmin",
            ".env", "config.", "xmlrpc", "/cgi-bin", "/vendor/",
        )
        return any(bit in p for bit in noise_bits)

    page_labels = [
        ("/student/focus", "Modo enfoque", "Usuarios entrando a estudiar con temporizador"),
        ("/student/analytics", "Analytics del estudiante", "Usuarios revisando su rendimiento"),
        ("/student/courses", "Cursos", "Usuarios revisando sus ramos y evaluaciones"),
        ("/student/quizzes", "Quizzes", "Usuarios usando quizzes de práctica"),
        ("/student/quiz", "Quizzes", "Usuarios usando quizzes de práctica"),
        ("/student/flashcards", "Tarjetas", "Usuarios usando flashcards"),
        ("/student/leaderboard", "Ranking", "Usuarios mirando la competencia"),
        ("/student/friends", "Amigos", "Usuarios usando funciones sociales"),
        ("/student/shop", "Tienda", "Usuarios revisando planes, coins y cosméticos"),
        ("/student/canvas", "Conexión Canvas", "Usuarios intentando sincronizar Canvas"),
        ("/student/grades", "Planilla de notas", "Usuarios usando cálculo de notas"),
        ("/student/profile", "Perfil", "Usuarios editando o mirando su perfil"),
        ("/set-language/en", "Cambio a inglés", "Usuarios cambiando el idioma a inglés"),
        ("/set-language/es", "Cambio a español", "Usuarios cambiando el idioma a español"),
        ("/register", "Registro", "Usuarios llegando a crear cuenta"),
        ("/login", "Login", "Usuarios entrando a su cuenta"),
        ("/student", "Inicio estudiante", "Usuarios entrando al panel principal"),
        ("/", "Landing page", "Visitantes viendo la página principal"),
    ]

    def describe_page(path: str) -> tuple[str, str]:
        clean = (path or "/").split("?")[0].rstrip("/") or "/"
        for prefix, label, meaning in page_labels:
            if clean == prefix or (prefix != "/" and clean.startswith(prefix + "/")):
                return label, meaning
        if clean.startswith("/api/"):
            return "Acción interna", "Actividad técnica de la app, no una página visible"
        return "Otra página", clean

    def readable_pages(rows: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for row in rows:
            path = str(row.get("path") or "")
            if is_noise_path(path):
                continue
            label, meaning = describe_page(path)
            if label not in grouped:
                grouped[label] = {"page": label, "meaning": meaning, "n": 0}
            grouped[label]["n"] += int(row.get("n") or 0)
        return sorted(grouped.values(), key=lambda r: r["n"], reverse=True)[:12]

    cards = [
        ("RAM servidor", f"{rss_mb:.1f} MB" if rss_mb is not None else "N/D"),
        ("Activos 15 min", _admin_metric(f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {active_15m_pg}", f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {active_15m_lite}")),
        ("Visitas hoy", _admin_metric(f"SELECT COUNT(*) FROM product_analytics_events WHERE event_type='page_view' AND {today_pg} AND {external_events}", f"SELECT COUNT(*) FROM product_analytics_events WHERE event_type='page_view' AND {today_lite} AND {external_events}")),
        ("Usuarios únicos hoy", _admin_metric(f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {today_pg} AND {external_events}", f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {today_lite} AND {external_events}")),
        ("Registros hoy", _admin_metric("SELECT COUNT(*) FROM clients WHERE created_at >= CURRENT_DATE", "SELECT COUNT(*) FROM clients WHERE date(created_at)=date('now','localtime')")),
        ("Focus min hoy", _admin_metric("SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE plan_date::date = CURRENT_DATE", "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE date(plan_date)=date('now','localtime')")),
        ("XP hoy", _admin_metric(f"SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE {today_pg}", f"SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE {today_lite}")),
        ("Quizzes hoy", _admin_metric(f"SELECT COUNT(*) FROM student_quizzes WHERE {today_pg}", f"SELECT COUNT(*) FROM student_quizzes WHERE {today_lite}")),
        ("Mazos hoy", _admin_metric(f"SELECT COUNT(*) FROM student_flashcard_decks WHERE {today_pg}", f"SELECT COUNT(*) FROM student_flashcard_decks WHERE {today_lite}")),
        ("Tarjetas hoy", _admin_metric(f"SELECT COUNT(*) FROM student_flashcards WHERE {today_pg}", f"SELECT COUNT(*) FROM student_flashcards WHERE {today_lite}")),
        ("Apuntes hoy", _admin_metric(f"SELECT COUNT(*) FROM student_notes WHERE {today_pg}", f"SELECT COUNT(*) FROM student_notes WHERE {today_lite}")),
        ("Usuarios totales", _admin_metric("SELECT COUNT(*) FROM clients WHERE COALESCE(account_type,'student')='student'", "SELECT COUNT(*) FROM clients WHERE COALESCE(account_type,'student')='student'")),
        ("Activos 7 días", _admin_metric(f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {week_pg} AND {external_events}", f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {week_lite} AND {external_events}")),
    ]
    card_html = "".join(
        f"<div class='admin-metric'><div class='k'>{_esc(label)}</div><div class='v'>{value:,}</div></div>"
        if isinstance(value, int)
        else f"<div class='admin-metric'><div class='k'>{_esc(label)}</div><div class='v'>{_esc(str(value))}</div></div>"
        for label, value in cards
    )

    top_pages = _admin_rows(
        f"SELECT path, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND {week_pg} AND {external_events} GROUP BY path ORDER BY n DESC LIMIT 80",
        f"SELECT path, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND {week_lite} AND {external_events} GROUP BY path ORDER BY n DESC LIMIT 80",
    )
    top_pages = readable_pages(top_pages)
    feature_rows = _admin_rows(
        f"SELECT event_type, COUNT(*) AS n, COUNT(DISTINCT client_id) AS users FROM product_analytics_events WHERE event_type <> 'page_view' AND {week_pg} AND {external_events} GROUP BY event_type ORDER BY n DESC LIMIT 20",
        f"SELECT event_type, COUNT(*) AS n, COUNT(DISTINCT client_id) AS users FROM product_analytics_events WHERE event_type <> 'page_view' AND {week_lite} AND {external_events} GROUP BY event_type ORDER BY n DESC LIMIT 20",
    )
    for row in feature_rows:
        row["label"] = event_labels.get(str(row.get("event_type") or ""), str(row.get("event_type") or ""))
    user_ram_rows = _admin_user_ram_rows(external_events)
    xp_rows = _admin_rows(
        "SELECT action, COUNT(*) AS n, COALESCE(SUM(xp),0) AS xp FROM student_xp WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY action ORDER BY xp DESC, n DESC LIMIT 16",
        "SELECT action, COUNT(*) AS n, COALESCE(SUM(xp),0) AS xp FROM student_xp WHERE datetime(created_at) >= datetime('now','localtime','-30 days') GROUP BY action ORDER BY xp DESC, n DESC LIMIT 16",
    )
    traffic_daily = _admin_rows(
        f"SELECT created_at::date::text AS d, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND created_at >= CURRENT_DATE - INTERVAL '13 days' AND {external_events} GROUP BY created_at::date ORDER BY d",
        f"SELECT date(created_at) AS d, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND date(created_at) >= date('now','localtime','-13 days') AND {external_events} GROUP BY date(created_at) ORDER BY d",
    )
    focus_daily = _admin_rows(
        "SELECT plan_date::date::text AS d, COALESCE(SUM(focus_minutes),0) AS n FROM student_study_progress WHERE plan_date::date >= CURRENT_DATE - INTERVAL '13 days' GROUP BY plan_date::date ORDER BY d",
        "SELECT date(plan_date) AS d, COALESCE(SUM(focus_minutes),0) AS n FROM student_study_progress WHERE date(plan_date) >= date('now','localtime','-13 days') GROUP BY date(plan_date) ORDER BY d",
    )
    ai_daily = _admin_rows(
        "SELECT created_at::date::text AS d, COUNT(*) AS n FROM student_quizzes WHERE created_at >= CURRENT_DATE - INTERVAL '13 days' GROUP BY created_at::date ORDER BY d",
        "SELECT date(created_at) AS d, COUNT(*) AS n FROM student_quizzes WHERE date(created_at) >= date('now','localtime','-13 days') GROUP BY date(created_at) ORDER BY d",
    )
    flash_daily = _admin_rows(
        "SELECT created_at::date::text AS d, COUNT(*) AS n FROM student_flashcard_decks WHERE created_at >= CURRENT_DATE - INTERVAL '13 days' GROUP BY created_at::date ORDER BY d",
        "SELECT date(created_at) AS d, COUNT(*) AS n FROM student_flashcard_decks WHERE date(created_at) >= date('now','localtime','-13 days') GROUP BY date(created_at) ORDER BY d",
    )
    try:
        from student import db as sdb
        course_outcomes = sdb.get_course_outcomes_admin(limit=80)
        course_outcome_reports = sdb.get_course_outcome_reports_admin(limit=200)
    except Exception:
        course_outcomes = []
        course_outcome_reports = []

    def table(headers, rows, keys):
        if not rows:
            return "<div class='admin-empty'>Sin datos todavía. Desde ahora MachReach empezará a registrar esta señal.</div>"
        head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{_esc(str(r.get(k, '')))}</td>" for k in keys) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def line_chart(title, rows, color="#FF7A3D", suffix=""):
        vals = [int(r.get("n") or 0) for r in rows]
        if not vals:
            return f"<div class='admin-chart'><h2>{_esc(title)}</h2><div class='admin-empty'>Sin datos para graficar todavía.</div></div>"
        max_v = max(vals) or 1
        width, height, pad = 720, 220, 28
        step = (width - pad * 2) / max(1, len(vals) - 1)
        pts = []
        dots = []
        labels = []
        for i, r in enumerate(rows):
            x = pad + i * step
            y = height - pad - ((int(r.get("n") or 0) / max_v) * (height - pad * 2))
            pts.append(f"{x:.1f},{y:.1f}")
            dots.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='{color}'><title>{_esc(str(r.get('d') or ''))}: {int(r.get('n') or 0)}{suffix}</title></circle>")
            if i == 0 or i == len(rows)-1 or len(rows) <= 7 or i % 3 == 0:
                labels.append(f"<text x='{x:.1f}' y='{height-6}' text-anchor='middle' font-size='10' fill='#77756F'>{_esc(str(r.get('d') or '')[-5:])}</text>")
        area = f"{pad},{height-pad} " + " ".join(pts) + f" {width-pad},{height-pad}"
        return f"""
        <div class="admin-chart">
          <h2>{_esc(title)}</h2>
          <svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}">
            <defs><linearGradient id="g{abs(hash(title))}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{color}" stop-opacity=".24"/><stop offset="100%" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>
            <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#E2DCCC" stroke-width="1"/>
            <polyline points="{area}" fill="url(#g{abs(hash(title))})"/>
            <polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
            {''.join(dots)}
            {''.join(labels)}
          </svg>
          <div class="admin-chart-foot"><b>{sum(vals):,}{suffix}</b> acumulado · máximo diario {max_v:,}{suffix}</div>
        </div>
        """

    def bar_chart(title, rows, label_key, value_key, color="#1A1A1F", suffix=""):
        clean = rows[:10]
        if not clean:
            return f"<div class='admin-chart'><h2>{_esc(title)}</h2><div class='admin-empty'>Sin datos para graficar todavía.</div></div>"
        max_v = max([int(r.get(value_key) or 0) for r in clean] or [1]) or 1
        bars = []
        for r in clean:
            value = int(r.get(value_key) or 0)
            pct = max(3, int(value * 100 / max_v))
            bars.append(
                f"<div class='admin-bar-row'><span>{_esc(str(r.get(label_key) or ''))}</span>"
                f"<div class='admin-bar-track'><i style='width:{pct}%;background:{color};'></i></div>"
                f"<b>{value:,}{suffix}</b></div>"
            )
        return f"<div class='admin-chart'><h2>{_esc(title)}</h2>{''.join(bars)}</div>"

    charts_html = (
        '<div class="admin-chart-grid">'
        + line_chart("Tráfico diario · 14 días", traffic_daily, "#FF7A3D")
        + line_chart("Minutos de estudio · 14 días", focus_daily, "#2E9266", " min")
        + line_chart("Quizzes creados · 14 días", ai_daily, "#EF5DA8")
        + line_chart("Mazos de tarjetas · 14 días", flash_daily, "#5B4694")
        + bar_chart("Features más usadas · 7 días", feature_rows, "label", "n", "#FF7A3D")
        + bar_chart("Páginas más vistas · 7 días", top_pages, "page", "n", "#1A1A1F")
        + "</div>"
    )

    body = f"""
    <style>
      .admin-analytics {{ display:grid; gap:18px; }}
      .admin-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }}
      .admin-metric {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:16px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-metric .k {{ color:#77756F; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
      .admin-metric .v {{ font-family:'Bricolage Grotesque',sans-serif; font-size:34px; font-weight:650; margin-top:8px; color:#1A1A1F; }}
      .admin-panel {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-panel h2 {{ margin:0 0 12px; font-family:'Bricolage Grotesque',sans-serif; font-size:25px; }}
      .admin-note {{ color:#77756F; background:#FBF8F0; border:1px solid #E2DCCC; border-radius:14px; padding:12px 14px; margin:0 0 14px; line-height:1.45; }}
      .admin-empty {{ color:#94939C; background:#FBF8F0; border:1px dashed #D8D0BE; border-radius:14px; padding:16px; }}
      .admin-chart-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
      .admin-chart {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04); min-width:0; }}
      .admin-chart h2 {{ margin:0 0 12px; font-family:'Bricolage Grotesque',sans-serif; font-size:24px; font-weight:650; color:#1A1A1F; }}
      .admin-chart svg {{ width:100%; height:auto; display:block; overflow:visible; }}
      .admin-chart-foot {{ margin-top:8px; color:#77756F; font-size:12px; }}
      .admin-bar-row {{ display:grid; grid-template-columns:minmax(110px,1fr) 2.4fr auto; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid #EEE8DA; }}
      .admin-bar-row:last-child {{ border-bottom:0; }}
      .admin-bar-row span {{ color:#5C5C66; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .admin-bar-row b {{ font-variant-numeric:tabular-nums; color:#1A1A1F; font-size:12px; }}
      .admin-bar-track {{ height:12px; background:#F4F1EA; border:1px solid #E2DCCC; border-radius:999px; overflow:hidden; }}
      .admin-bar-track i {{ display:block; height:100%; border-radius:999px; }}
      @media (max-width: 900px) {{ .admin-chart-grid {{ grid-template-columns:1fr; }} .admin-bar-row {{ grid-template-columns:1fr; }} }}
    </style>
    <div class="admin-analytics">
      <div class="admin-grid">{card_html}</div>
      {charts_html}
      <div class="admin-panel">
        <h2>RAM por usuario · últimos 15 min</h2>
        <div class="admin-note">La RAM exacta por usuario no existe en un worker compartido: todos usan el mismo proceso Python. Esta tabla muestra la RAM real del servidor y una estimación de presión temporal por usuario según vistas, acciones y operaciones pesadas recientes. Úsala para detectar quién está generando carga, no como medición contable perfecta.</div>
        {table(["Usuario","Vistas","Acciones","Acciones pesadas","Presión RAM","Nivel","Última actividad"], user_ram_rows, ["user","page_views_15m","actions_15m","heavy_15m","ram_pressure","load_level","last_seen"])}
      </div>
      <div class="admin-panel"><h2>Páginas más vistas · 7 días</h2>{table(["Página","Qué significa","Visitas"], top_pages, ["page","meaning","n"])}</div>
      <div class="admin-panel"><h2>Eventos de producto · 7 días</h2>{table(["Evento","Acciones","Usuarios"], feature_rows, ["label","n","users"])}</div>
      <div class="admin-panel"><h2>XP por fuente · 30 días</h2>{table(["Acción","Eventos","XP"], xp_rows, ["action","n","xp"])}</div>
      <div class="admin-panel"><h2>Resultados por ramo</h2>{table(["Curso","Código","Reportes","Aprobados","Reprobados","Promedio aprobado"], course_outcomes, ["course_name","course_code","reports","passed_reports","failed_reports","avg_pass_hours"])}</div>
      <div class="admin-panel"><h2>Reportes individuales de ramos</h2>{table(["Usuario","Correo","Curso","Código","Resultado","Horas estudiadas","Reportado"], course_outcome_reports, ["user_name","user_email","course_name","course_code","result","total_focus_hours","reported_at"])}</div>
    </div>
    """
    return _render("Admin analytics", _admin_shell(
        "/admin/analytics", "\U0001F4CA Analytics de producto",
        "Tráfico, uso de IA, estudio real y señales para mejorar Free y Plus.",
        body,
    ), active_page="admin", wide=True)


@app.route("/admin/growth/delete/<int:target_id>", methods=["GET", "POST"])
@limiter.limit("6 per minute", methods=["POST"])
def admin_growth_delete(target_id: int):
    """Delete one account from the owner dashboard.

    Same machinery and same guards as the deletion on /admin — reauthentication,
    the exact email typed back, never yourself, never another administrator —
    because a second implementation of an irreversible action is a second set of
    ways to get it wrong.
    """
    if not _is_admin():
        return redirect(url_for("dashboard"))
    if not _admin_secret_ok():
        return redirect(url_for("admin_reauthenticate", next=f"/admin/growth/delete/{target_id}"))
    from student import growth

    target = get_client(target_id)
    if not target:
        # flash() takes the category as its second argument. Passing a
        # (category, message) tuple as the message makes Flask file it under
        # the category "message" with a tuple as the body, which is not what
        # the toast template unpacks.
        flash("That account no longer exists.", "error")
        return redirect(url_for("admin_growth"))

    email = str(target.get("email") or "")
    error = ""
    if request.method == "POST":
        typed = (request.form.get("confirm_email") or "").strip().lower()
        if typed != email.strip().lower():
            error = "Type the account's exact email to confirm."
        elif target_id == session.get("client_id"):
            error = "You cannot delete your own logged-in account."
        else:
            result = _admin_delete_client_account(target_id)
            if result.get("ok"):
                _log_admin_action("delete_user", target=str(target_id), email=email)
                flash(f"Deleted account: {email}", "success")
                return redirect(url_for("admin_growth"))
            error = result.get("error") or "Could not delete that account."

    row = next((r for r in growth.user_rows() if r["id"] == target_id), None)
    losses = []
    if row:
        if row["referred"]:
            losses.append(f"<li>Invitó a <b>{row['referred']}</b> persona(s); "
                          f"<b>{row['referred_paying']}</b> de ellas paga. Esas cuentas se quedan, "
                          "pero el crédito del referido desaparece.</li>")
        if row["paying"]:
            losses.append("<li>Tiene una <b>suscripción activa</b>. Se cancela en el proveedor "
                          "antes de borrar nada; si esa cancelación falla, no se borra la cuenta.</li>")
        if row["xp"]:
            losses.append(f"<li>Pierde <b>{row['xp']:,}</b> XP, su historial de estudio y su puesto en el ranking.</li>")
    losses_html = '<ul class="del-loss">' + "".join(losses) + "</ul>" if losses else ""

    return _render("Eliminar cuenta", f"""
    <div class="card" style="max-width:620px;margin:40px auto;">
      <h1>Eliminar esta cuenta</h1>
      <style>
        .del-go {{ display:inline-flex; align-items:center; gap:8px; padding:11px 18px; border:0;
          border-radius:999px; background:#D7263D; color:#fff; font-weight:800; font-size:14px; cursor:pointer; }}
        .del-go:hover {{ background:#B01E31; }}
        /* The shell's .alert-red renders as bare text here, and an irreversible
           action deserves to look like one. */
        .del-warn {{ background:#FDECEE; border:1px solid #F2B8C0; border-radius:14px;
          padding:12px 14px; margin:14px 0; color:#8C1526; font-weight:700; line-height:1.45; }}
        .del-loss {{ margin:0 0 14px; padding-left:20px; line-height:1.6; color:#5C5C66; }}
      </style>
      <p><b>{_esc(str(target.get('name') or ''))}</b> &mdash; <code>{_esc(email)}</code></p>
      <div class="del-warn">Esto no se puede deshacer. Se borran sus cursos, notas,
        sesiones, quizzes, amistades y su cuenta.</div>
      {losses_html}
      {'<div class="del-warn">' + _esc(error) + '</div>' if error else ''}
      <form method="post">
        {_csrf_hidden_input()}
        <div class="form-group">
          <label>Escribe <code>{_esc(email)}</code> para confirmar</label>
          <input name="confirm_email" autocomplete="off" required>
        </div>
        <button class="del-go" type="submit">Eliminar definitivamente</button>
        <a class="btn btn-outline" href="/admin/growth" style="margin-left:8px;">Cancelar</a>
      </form>
    </div>
    """, active_page="admin")


@app.route("/admin/growth.csv")
def admin_growth_csv():
    """The same table as /admin/growth, for when a spreadsheet is easier."""
    if not _is_admin():
        return redirect(url_for("dashboard"))
    import csv
    import io
    from student import growth

    columns = [
        ("id", "ID"), ("name", "Nombre"), ("email", "Correo"),
        ("university", "Universidad"), ("major", "Carrera"),
        ("created_at", "Registro"), ("verified", "Verificado"),
        ("paying", "Paga Plus"), ("promo_plus", "Plus de regalo"),
        ("referral_code", "Codigo"), ("referred", "Referidos"),
        ("referred_verified", "Referidos verificados"),
        ("referred_paying", "Referidos que pagan"),
        ("weeks_earned", "Semanas ganadas"), ("xp", "XP"),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in growth.user_rows():
        writer.writerow([row.get(key, "") for key, _ in columns])
    _log_admin_action("growth_export")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=machreach-usuarios.csv"},
    )


@app.route("/admin/growth")
def admin_growth():
    """Owner dashboard: every account, what its invite link produced, and who
    is actually paying."""
    if not _is_admin():
        return redirect(url_for("dashboard"))
    from student import growth

    rows = growth.user_rows()
    stats = growth.summary(rows)
    admin_ids = {int(r["id"]) for r in _admin_rows(
        "SELECT id FROM clients WHERE is_admin = 1", "SELECT id FROM clients WHERE is_admin = 1")}
    query = (request.args.get("q") or "").strip()
    only = (request.args.get("only") or "").strip()
    view = rows
    if query:
        needle = query.lower()
        view = [r for r in view if needle in r["name"].lower() or needle in r["email"].lower()
                or needle in r["university"].lower() or needle in r["major"].lower()
                or needle == r["referral_code"].lower()]
    if only == "paying":
        view = [r for r in view if r["paying"]]
    elif only == "inviters":
        view = [r for r in view if r["referred"]]
    elif only == "unverified":
        view = [r for r in view if not r["verified"]]

    def yes_no(value: bool, good: str = "Sí", bad: str = "—") -> str:
        return f'<b style="color:#2E9266">{good}</b>' if value else f'<span style="color:#94939C">{bad}</span>'

    def _academic_cell(value: str) -> str:
        """University and carrera, truncated. Written-out carrera names are long
        enough to push the rest of the row off screen, so the cell keeps the
        full text in a tooltip instead."""
        if not value:
            return '<td class="acad"><span style="color:#94939C">—</span></td>'
        return f'<td class="acad" title="{_esc(value)}"><span>{_esc(value)}</span></td>'

    def _delete_cell(row: dict) -> str:
        """Own account and other administrators are not deletable, so they get
        no button rather than a button that always refuses."""
        if row["id"] == session.get("client_id"):
            return '<span style="color:#94939C">tú</span>'
        if row["id"] in admin_ids:
            return '<span style="color:#94939C">admin</span>'
        return (f'<a href="/admin/growth/delete/{row["id"]}" '
                'style="color:#D7263D;font-weight:800;">Eliminar</a>')

    if view:
        body_rows = "".join(
            "<tr>"
            f"<td>{r['id']}</td>"
            f"<td>{_esc(r['name'])}</td>"
            f"<td><code>{_esc(r['email'])}</code></td>"
            f"{_academic_cell(r['university'])}"
            f"{_academic_cell(r['major'])}"
            f"<td>{_esc(r['created_at'])}</td>"
            f"<td>{yes_no(r['verified'])}</td>"
            f"<td>{yes_no(r['paying'], 'Paga')}{' <small>+regalo</small>' if r['promo_plus'] and r['paying'] else ''}"
            f"{'<small style=\"color:#B58309\">Plus de regalo</small>' if r['promo_plus'] and not r['paying'] else ''}</td>"
            f"<td><code>{_esc(r['referral_code'] or '—')}</code></td>"
            f"<td><b>{r['referred']}</b></td>"
            f"<td>{r['referred_verified']}</td>"
            f"<td><b style=\"color:#2E9266\">{r['referred_paying']}</b></td>"
            f"<td>{r['weeks_earned']}</td>"
            f"<td>{r['xp']:,}</td>"
            f"<td>{_delete_cell(r)}</td>"
            "</tr>"
            for r in view
        )
        table_html = (
            "<table><thead><tr>"
            "<th>ID</th><th>Nombre</th><th>Correo</th><th>Universidad</th><th>Carrera</th>"
            "<th>Registro</th><th>Verificado</th>"
            "<th>Plan</th><th>Código</th><th>Referidos</th><th>Verificados</th>"
            "<th>Referidos que pagan</th><th>Semanas ganadas</th><th>XP</th><th></th>"
            "</tr></thead><tbody>" + body_rows + "</tbody></table>"
        )
    else:
        table_html = "<div class='admin-empty'>Ningún usuario coincide con este filtro.</div>"

    daily = growth.signups_by_day(30)
    peak = max((d["signups"] for d in daily), default=0) or 1
    bars = "".join(
        f'<span title="{_esc(d["day"])}: {d["signups"]}" style="height:{max(3, round(d["signups"] * 100 / peak))}%"></span>'
        for d in daily
    )

    cards = "".join(
        f"<div class='admin-metric'><div class='k'>{_esc(label)}</div>"
        f"<div class='v'>{value}</div>"
        f"<div class='s'>{_esc(sub)}</div></div>"
        for label, value, sub in [
            ("Usuarios", f"{stats['users']:,}", f"{stats['verified']:,} verificados ({stats['verified_pct']}%)"),
            ("Pagan Plus", f"{stats['paying']:,}", f"{stats['paying_pct']}% de los registrados"),
            ("Plus de regalo", f"{stats['promo_plus']:,}", "semanas de referido activas, sin pagar"),
            ("Invitaron a alguien", f"{stats['inviters']:,}", "usuarios con al menos 1 referido"),
            ("Altas por referido", f"{stats['signups_from_referrals']:,}", f"{stats['referral_share_pct']}% de todas las altas"),
            ("Referidos que pagan", f"{stats['referred_paying']:,}", f"convierten al {stats['referred_conversion_pct']}%"),
            ("Conversión general", f"{stats['overall_conversion_pct']}%", "de registro a plan pagado"),
        ]
    )

    channel_note = (
        f"El canal de referidos convierte al <b>{stats['referred_conversion_pct']}%</b> "
        f"frente al <b>{stats['overall_conversion_pct']}%</b> general."
        if stats["signups_from_referrals"] else
        "Todavía no hay altas por enlace de invitación."
    )

    def filter_link(key: str, label: str) -> str:
        # Styled here rather than with the legacy .btn classes, which render
        # white-on-transparent inside the admin shell.
        active = " on" if only == key else ""
        href = f"/admin/growth?only={key}" if key else "/admin/growth"
        return f'<a class="admin-chip{active}" href="{href}">{label}</a>'

    body = f"""
    <style>
      .admin-growth {{ display:grid; gap:18px; }}
      .admin-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; }}
      .admin-metric {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:16px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-metric .k {{ color:#77756F; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
      .admin-metric .v {{ font-family:'Bricolage Grotesque',sans-serif; font-size:32px; font-weight:650; margin-top:6px; color:#1A1A1F; }}
      .admin-metric .s {{ color:#77756F; font-size:12px; margin-top:4px; line-height:1.35; }}
      .admin-panel {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); overflow:auto; }}
      .admin-panel h2 {{ margin:0 0 12px; font-family:'Bricolage Grotesque',sans-serif; font-size:24px; }}
      .admin-note {{ color:#5C5C66; background:#FBF8F0; border:1px solid #E2DCCC; border-radius:14px; padding:12px 14px; margin:0 0 14px; line-height:1.45; }}
      .admin-empty {{ color:#94939C; background:#FBF8F0; border:1px dashed #D8D0BE; border-radius:14px; padding:16px; }}
      .admin-growth table {{ width:100%; border-collapse:collapse; font-size:13px; }}
      .admin-growth th {{ text-align:left; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#77756F; border-bottom:1px solid #E2DCCC; padding:8px 10px; white-space:nowrap; }}
      .admin-growth td {{ padding:8px 10px; border-bottom:1px solid #F1ECE0; vertical-align:middle; white-space:nowrap; }}
      .admin-growth tbody tr:hover {{ background:#FBF8F0; }}
      .admin-growth td.acad span {{ display:block; max-width:170px; overflow:hidden; text-overflow:ellipsis; }}
      .admin-growth code {{ font-size:12px; }}
      .admin-growth small {{ display:block; font-size:10.5px; }}
      .admin-tools {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:14px; }}
      .admin-tools input {{ padding:8px 12px; border:1px solid #E2DCCC; border-radius:999px; font-size:13px; min-width:300px; }}
      .admin-chip {{ display:inline-flex; align-items:center; gap:6px; padding:8px 14px; border-radius:999px; border:1px solid #E2DCCC; background:#fff; color:#1A1A1F; font-size:12.5px; font-weight:800; cursor:pointer; text-decoration:none; }}
      .admin-chip:hover {{ border-color:#FF7A3D; color:#B14A16; }}
      .admin-chip.on {{ background:#FF7A3D; border-color:#FF7A3D; color:#fff; }}
      .spark {{ display:flex; align-items:flex-end; gap:3px; height:90px; }}
      .spark span {{ flex:1; background:#FF7A3D; border-radius:3px 3px 0 0; min-height:3px; }}
    </style>
    <div class="admin-growth">
      <div class="admin-grid">{cards}</div>
      <div class="admin-panel">
        <h2>Altas por día · 30 días</h2>
        <div class="spark">{bars}</div>
      </div>
      <div class="admin-panel">
        <h2>Todos los usuarios</h2>
        <div class="admin-note">
          {channel_note}
          <br>"Pagan" cuenta solo suscripciones reales del proveedor de pago; las semanas
          gratis por referido aparecen aparte porque no son ingresos.
        </div>
        <div class="admin-tools">
          <form method="get" action="/admin/growth" style="display:flex;gap:8px;">
            <input name="q" value="{_esc(query)}" placeholder="Buscar por nombre, correo, universidad, carrera o código">
            <button class="admin-chip" type="submit">Buscar</button>
          </form>
          {filter_link("", "Todos")}
          {filter_link("paying", "Solo los que pagan")}
          {filter_link("inviters", "Solo con referidos")}
          {filter_link("unverified", "Sin verificar")}
          <a class="admin-chip" href="/admin/growth.csv">&#11015; Descargar CSV</a>
        </div>
        {table_html}
      </div>
    </div>
    """
    return _render("Admin crecimiento", _admin_shell(
        "/admin/growth", "\U0001F4C8 Usuarios, referidos y pagos",
        "Quién se registró, a quién trajo con su enlace y quién terminó pagando.",
        body,
    ), active_page="admin", wide=True)


@app.route("/admin/leaderboard-winners-test", methods=["GET", "POST"])
def admin_leaderboard_winners_test():
    """Preview monthly winners with a reauthenticated POST-only send action."""
    if not _is_admin():
        return redirect(url_for("dashboard"))

    from datetime import date
    from student.academic import monthly_winners

    raw = (request.values.get("month") or "").strip()
    if raw:
        try:
            year_s, month_s = raw.split("-", 1)
            year, month = int(year_s), int(month_s)
            if not (1 <= month <= 12):
                raise ValueError
        except (ValueError, AttributeError):
            return ("Invalid month — use YYYY-MM (e.g. 2026-03).", 400)
    else:
        today = date.today()
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1

    if request.method == "POST":
        if not _admin_secret_ok():
            flash(("warning", "Confirm your password and MFA before sending this email."))
            return redirect(url_for("admin_reauthenticate", next=request.full_path.rstrip("?")))
        from worker import send_monthly_leaderboard_email, LEADERBOARD_WINNERS_RECIPIENT
        send_monthly_leaderboard_email(year=year, month=month)
        _log_admin_action("send_monthly_leaderboard_email", month=f"{year:04d}-{month:02d}")
        flash(("success", f"Triggered monthly winners email ({year:04d}-{month:02d}) → {LEADERBOARD_WINNERS_RECIPIENT}"))
        return redirect(url_for("admin_leaderboard_winners_test", month=f"{year:04d}-{month:02d}"))

    data = monthly_winners(year, month, top_n=3)
    summary = data.get("summary", {}) or {}

    def _rows(rows):
        if not rows:
            return "<div style='color:var(--text-muted);font-size:13px;padding:6px 8px;'>(sin participantes este mes)</div>"
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        out = ["<table style='width:100%;border-collapse:collapse;font-size:13px;'>"]
        for r in rows:
            m = medals.get(r["rank"], f"#{r['rank']}")
            out.append(
                f"<tr><td style='padding:4px 8px;width:36px;'>{m}</td>"
                f"<td style='padding:4px 8px;'>{_esc(r['name'])}</td>"
                f"<td style='padding:4px 8px;text-align:right;font-variant-numeric:tabular-nums;'>{r['xp']:,} XP</td>"
                f"<td style='padding:4px 8px;color:var(--text-muted);font-size:11px;'>client #{r['client_id']}</td></tr>"
            )
        out.append("</table>")
        return "".join(out)

    summary_card = (
        "<div class='card' style='padding:16px;margin-bottom:14px;display:grid;"
        "grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;'>"
        f"<div><div style='font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em;'>Total XP otorgado</div>"
        f"<div style='font-size:22px;font-weight:800;margin-top:4px;'>{summary.get('total_xp_awarded', 0):,}</div></div>"
        f"<div><div style='font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em;'>Estudiantes activos</div>"
        f"<div style='font-size:22px;font-weight:800;margin-top:4px;'>{summary.get('active_students', 0)} <span style='color:var(--text-muted);font-size:14px;font-weight:500;'>/ {summary.get('total_students', 0)}</span></div></div>"
        f"<div><div style='font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em;'>Nuevas inscripciones</div>"
        f"<div style='font-size:22px;font-weight:800;margin-top:4px;'>{summary.get('new_students', 0)}</div></div>"
        "</div>"
    )

    sections = [
        f"<h2 style='margin:0 0 4px;font-size:22px;'>🏆 Leaderboard winners — {data['label']}</h2>",
        "<div style='color:var(--text-muted);font-size:13px;margin-bottom:18px;'>"
        f"Period: {data['start']} → {data['end_exclusive']} (exclusive)</div>",
        summary_card,
        # Global leaderboard intentionally hidden while only Chile is active.
    ]

    def _section(title, groups):
        parts = ["<div class='card' style='padding:16px;margin-bottom:14px;'>",
                 f"<div style='font-weight:700;margin-bottom:8px;'>{title}</div>"]
        if not groups:
            parts.append(
                "<div style='color:var(--text-muted);font-size:13px;padding:6px 8px;'>"
                "(sin participantes este mes)</div>"
            )
        else:
            for grp in groups:
                parts.append(
                    f"<div style='margin:10px 0 4px;font-weight:600;font-size:13px;color:var(--text-secondary);'>"
                    f"{_esc(str(grp['label']))}</div>"
                )
                parts.append(_rows(grp["rows"]))
        parts.append("</div>")
        return "".join(parts)

    sections.append(_section("🏳️ Por país", data["by_country"]))
    sections.append(_section("🎓 Por universidad", data["by_university"]))
    sections.append(_section("📚 Por carrera", data["by_major"]))

    # Month switcher + send button
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    nav = (
        f"<div style='display:flex;gap:8px;align-items:center;margin-bottom:18px;flex-wrap:wrap;'>"
        f"<a class='btn btn-outline btn-sm' href='?month={prev_year:04d}-{prev_month:02d}'>← {prev_year:04d}-{prev_month:02d}</a>"
        f"<a class='btn btn-outline btn-sm' href='?month={next_year:04d}-{next_month:02d}'>{next_year:04d}-{next_month:02d} →</a>"
        f"<form method='get' action='' style='display:inline-flex;gap:6px;align-items:center;margin:0;'>"
        f"<input type='month' name='month' value='{year:04d}-{month:02d}' "
        f"style='padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);'>"
        f"<button type='submit' class='btn btn-primary btn-sm'>Load</button>"
        f"</form>"
        f"<form method='post' action='' style='display:inline-flex;margin:0 0 0 auto;' "
        f"onsubmit=\"return confirm('Send the {year:04d}-{month:02d} email to the recipient now?');\">"
        f"{_csrf_hidden_input()}"
        f"<input type='hidden' name='month' value='{year:04d}-{month:02d}'>"
        f"<button class='btn btn-secondary btn-sm' type='submit'>📤 Send email for {year:04d}-{month:02d}</button>"
        f"</form>"
        f"</div>"
    )

    body = nav + "".join(sections)
    return _render("Monthly leaderboard winners", _admin_shell(
        "/admin/leaderboard-winners-test", "\U0001F3C6 Ganadores del mes",
        "Previsualiza el correo mensual de ganadores antes de enviarlo.",
        body,
    ), active_page="admin", wide=True)


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect("/student/settings")

# ---------------------------------------------------------------------------
# Routes — Reply Inbox
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — A/B Test Dashboard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Campaign Calendar
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Campaign CRUD
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Smart Send Times
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Analytics Export
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API — Email Accounts (multi-mailbox)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Retired product API surface intentionally removed.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Routes — Contacts Book
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Billing (Lemon Squeezy hosted checkout)
# ---------------------------------------------------------------------------

def _finish_claimed_ls_event(error: str = "") -> None:
    event_key = getattr(g, "ls_event_key", "")
    if not event_key or getattr(g, "ls_event_finished", False):
        return
    from machreach_core.db import finish_webhook_event
    finish_webhook_event("lemonsqueezy", event_key, error=error)
    g.ls_event_finished = True


@app.teardown_request
def _release_failed_ls_event(exc):
    if exc is None or not getattr(g, "ls_event_key", "") or getattr(g, "ls_event_finished", False):
        return
    try:
        _finish_claimed_ls_event(type(exc).__name__)
    except Exception:
        _log.exception("[LS] failed to release webhook event claim")


@app.route("/billing")
def billing_page():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect("/student/shop")

@app.route("/billing/checkout", methods=["POST"])
@limiter.limit("10 per minute")
def billing_checkout():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect("/student/shop")

@app.route("/billing/downgrade", methods=["POST"])
@limiter.limit("5 per minute")
def billing_downgrade():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect("/student/shop")

@app.route("/webhooks/lemonsqueezy", methods=["POST"])
@csrf.exempt
def lemonsqueezy_webhook():
    """Handle only MachReach Uni subscriptions and coin packs.

    Routing is done via `meta.custom_data.purpose`:
      - "student_sub"   -> student Plus subscription event
      - "coin_pack"     -> one-time coin-pack purchase (pack_key)
    """
    import json as _json
    from machreach_core import config as _cfg
    from machreach_core import lemonsqueezy as ls

    raw = request.get_data() or b""
    sig = request.headers.get("X-Signature", "") or request.headers.get("x-signature", "")
    signature_mode = ls.webhook_signature_mode(raw, sig)
    if not signature_mode:
        _log.warning("[LS] webhook rejected: bad signature")
        return "Invalid signature", 401

    try:
        body = _json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return "Bad JSON", 400

    meta = body.get("meta") or {}
    event_name = meta.get("event_name") or ""
    custom = (meta.get("custom_data") or {})
    data = body.get("data") or {}
    attrs = (data.get("attributes") or {})
    first_order_item = (
        attrs.get("first_order_item")
        if isinstance(attrs.get("first_order_item"), dict)
        else {}
    )
    provider_event_at = str(
        attrs.get("updated_at")
        or attrs.get("created_at")
        or meta.get("event_created_at")
        or ""
    ).strip() or None
    payment_events = {
        "subscription_payment_failed",
        "subscription_payment_success",
        "subscription_payment_recovered",
        "subscription_payment_refunded",
    }
    order_events = {"order_created", "order_refunded"}
    provider_subscription_id = str(
        (attrs.get("subscription_id") if event_name in payment_events else data.get("id"))
        or ""
    )

    purpose = str(custom.get("purpose") or "")
    if purpose not in {"student_sub", "coin_pack"}:
        from machreach_core.db import is_retired_billing_subscription

        if is_retired_billing_subscription(provider_subscription_id):
            _log.info("[LS] acknowledged archived subscription event=%s", event_name)
            return "retired", 200
        _log.warning("[LS] rejected unsupported product purpose=%r event=%s", purpose, event_name)
        return "Unsupported product", 400
    try:
        cid = int(custom.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0

    if not cid:
        _log.warning("[LS] webhook missing client_id in custom_data: %s", event_name)
        return "Missing client_id", 400

    payload_test_mode = bool(meta.get("test_mode") or attrs.get("test_mode"))
    if signature_mode == "test":
        try:
            sandbox_client_id = int(_cfg.LEMON_SQUEEZY_SANDBOX_CLIENT_ID or 0)
        except (TypeError, ValueError):
            sandbox_client_id = 0
        if not payload_test_mode or not sandbox_client_id or cid != sandbox_client_id:
            _log.warning("[LS] rejected sandbox webhook for non-sandbox client=%s", cid)
            return "Sandbox webhook not allowed", 403

    import hashlib as _hashlib
    from machreach_core.db import claim_webhook_event
    provider_event_id = str(
        meta.get("event_id") or meta.get("event_uuid") or ""
    ).strip()
    event_key = (
        f"id:{provider_event_id[:180]}"
        if provider_event_id
        else _hashlib.sha256(raw).hexdigest()
    )
    event_claim = claim_webhook_event("lemonsqueezy", event_key, event_name)
    if not event_claim["claimed"]:
        if event_claim["status"] == "succeeded":
            return "ok", 200
        return "Event already processing", 409
    g.ls_event_key = event_key
    g.ls_event_finished = False

    _log.info("[LS] webhook %s purpose=%s client=%s", event_name, purpose, cid)

    # A cancelled subscription keeps emitting lifecycle events — `updated`, then
    # `expired` at the end of the paid period — long after the account that
    # owned it was deleted. Every table this handler writes is keyed on
    # clients.id, so those late events used to hit the foreign key, return 500,
    # and be retried by the provider against a row that can never come back.
    # Settle the claim as done instead: nothing is owed to an account that no
    # longer exists, and a retry of an already-stuck event now clears itself.
    from machreach_core.db import get_client as _get_client
    if _get_client(cid) is None:
        from machreach_core.db import record_operational_event
        record_operational_event("billing_webhook_unknown_client", event_name[:80])
        _log.warning(
            "[LS] settled %s for unknown client=%s purpose=%s", event_name, cid, purpose
        )
        _finish_claimed_ls_event()
        return "Unknown client", 200

    # ── Student Plus subscription ──────────────────────────────────
    if purpose == "student_sub":
        try:
            from student import subscription as ssub
        except Exception:
            ssub = None
        requested_tier = str(custom.get("tier") or "plus").strip().lower()
        # A checkout emits `order_created` alongside `subscription_created`, and
        # its `data` is an order, not a subscription: the product and variant
        # live in first_order_item — the coin-pack branch below reads them from
        # there for exactly this reason — while `data.id` is an order id. Read
        # only the subscription-shaped fields and both come back empty, no
        # identity can match, and the event is rejected 400 and parked at
        # `failed` for good. The entitlement itself is fine (subscription_created
        # grants it), but the dead row holds /health/operations degraded, which
        # pages the uptime monitor every five minutes until someone clears it.
        identity_attrs = first_order_item if event_name in order_events else attrs
        variant_id = str(identity_attrs.get("variant_id") or "")
        product_id = str(identity_attrs.get("product_id") or "")
        store_id = str(attrs.get("store_id") or "")
        product_name = str(attrs.get("product_name") or "").strip().lower()
        expected_variant = str(
            _cfg.LS_TEST_VARIANT_STUDENT_PLUS
            if signature_mode == "test"
            else _cfg.LS_VARIANT_STUDENT_PLUS
        ).strip()
        expected_product = str(
            _cfg.LS_TEST_PRODUCT_STUDENT_PLUS
            if signature_mode == "test"
            else _cfg.LS_PRODUCT_STUDENT_PLUS
        ).strip()
        expected_store = str(
            _cfg.LEMON_SQUEEZY_TEST_STORE_ID
            if signature_mode == "test"
            else _cfg.LEMON_SQUEEZY_STORE_ID
        ).strip()
        existing_subscription_id = str(
            (ssub.get_subscription_state(cid) if ssub else {}).get("ls_sub_id") or ""
        ).strip()
        has_full_provider_identity = bool(
            expected_store
            and store_id == expected_store
            and expected_product
            and product_id == expected_product
            and expected_variant
            and variant_id == expected_variant
        )
        # Lemon Squeezy currently omits product_id from some subscription
        # lifecycle payloads. Once a subscription was admitted with the full
        # store/product/variant tuple, keep accepting its later signed events
        # only when the immutable subscription binding plus store and variant
        # still match. An unbound/new subscription never gets this fallback.
        has_bound_provider_identity = bool(
            not product_id
            and provider_subscription_id
            and provider_subscription_id == existing_subscription_id
            and expected_store
            and store_id == expected_store
            and expected_variant
            and variant_id == expected_variant
        )
        provider_is_plus = bool(
            has_full_provider_identity
            or has_bound_provider_identity
            or (
                event_name in payment_events
                and provider_subscription_id
                and provider_subscription_id == existing_subscription_id
            )
        )
        removed_legacy_tier = not provider_is_plus and (
            requested_tier == "ultimate" or "ultimate" in product_name
        )
        tier = "plus"
        if not ssub:
            _finish_claimed_ls_event("student-handler-unavailable")
            return "Student subscription handler unavailable", 503
        # Payment webhooks carry a subscription-invoice as `data`. Its `id`
        # is therefore an invoice id and cannot be used for subscription API
        # operations such as cancellation. Lemon includes the real provider
        # subscription id in the invoice attributes instead.
        sub_id = provider_subscription_id
        urls = attrs.get("urls") if isinstance(attrs.get("urls"), dict) else {}
        provider_fields = {
            "ends_at": attrs.get("ends_at"),
            "renews_at": attrs.get("renews_at"),
            "update_payment_method_url": urls.get("update_payment_method"),
            "customer_portal_url": urls.get("customer_portal"),
            "provider_event_at": provider_event_at,
        }
        provider_fields = {
            key: str(value) for key, value in provider_fields.items() if value is not None
        }
        if removed_legacy_tier:
            cancelled = (
                ls.cancel_subscription(sub_id, test_mode=True)
                if signature_mode == "test" and sub_id
                else ls.cancel_subscription(sub_id) if sub_id else True
            )
            if sub_id and not cancelled:
                _finish_claimed_ls_event("retired-tier-cancel-failed")
                return "Retired subscription cancellation failed", 503
            ssub.set_subscription_state(
                cid, tier="free", status="retired", ls_sub_id=sub_id or None
            )
            _finish_claimed_ls_event()
            return "ok", 200
        if not provider_is_plus:
            _finish_claimed_ls_event("unapproved-subscription-variant")
            _log.warning(
                "[LS] rejected subscription variant=%r client=%s", variant_id, cid
            )
            return "Unsupported subscription variant", 400
        if event_name == "subscription_created":
            ssub.set_subscription_state(
                cid,
                tier=tier,
                status=(attrs.get("status") or "active"),
                ls_sub_id=sub_id,
                **provider_fields,
            )
        elif event_name in (
            "subscription_updated",
            "subscription_plan_changed",
            "subscription_resumed",
            "subscription_unpaused",
        ):
            ssub.set_subscription_state(
                cid,
                tier=tier,
                status=(attrs.get("status") or "active"),
                ls_sub_id=sub_id or None,
                **provider_fields,
            )
        elif event_name == "subscription_cancelled":
            ssub.set_subscription_state(
                cid,
                tier="plus",
                status="cancelled",
                ls_sub_id=sub_id or None,
                **provider_fields,
            )
        elif event_name == "subscription_expired":
            ssub.set_subscription_state(
                cid,
                tier="free",
                status="expired",
                ls_sub_id=sub_id or None,
                **provider_fields,
            )
        elif event_name == "subscription_payment_failed":
            ssub.set_subscription_state(
                cid,
                status="past_due",
                ls_sub_id=sub_id or None,
                **provider_fields,
            )
        elif event_name in (
            "subscription_payment_success",
            "subscription_payment_recovered",
        ):
            ssub.set_subscription_state(
                cid,
                tier=tier,
                status="active",
                ls_sub_id=sub_id or None,
                payment_succeeded=True,
                **provider_fields,
            )
        elif event_name == "subscription_payment_refunded":
            refunded_amount = int(attrs.get("refunded_amount") or 0)
            total = int(attrs.get("total") or 0)
            fully_refunded = attrs.get("refunded") is True or (
                total > 0 and refunded_amount >= total
            )
            if fully_refunded:
                # Entitlements are revoked from the signed provider event first.
                # Provider cancellation is reconciliation and must never keep
                # refunded access alive during a provider outage.
                ssub.set_subscription_state(
                    cid, tier="free", status="refunded", ls_sub_id=sub_id or None
                )
                cancelled = (
                    ls.cancel_subscription(sub_id, test_mode=True)
                    if signature_mode == "test" and sub_id
                    else ls.cancel_subscription(sub_id) if sub_id else False
                )
                if not cancelled:
                    from machreach_core.db import record_operational_event
                    record_operational_event("billing_reconciliation_failure", "full_refund")
                    _log.error("[LS] full refund revoked locally but provider cancellation failed: %s", sub_id)
            else:
                from machreach_core.db import record_operational_event
                record_operational_event("billing_refund_manual_review", "partial_subscription")
        _finish_claimed_ls_event()
        return "ok", 200

    # ── One-time coin-pack purchase ────────────────────────────────
    if purpose == "coin_pack":
        if event_name not in {"order_created", "order_refunded"}:
            _finish_claimed_ls_event()
            return "ok", 200
        pack_key = str(custom.get("pack_key") or "")
        if event_name == "order_created" and not pack_key:
            _finish_claimed_ls_event()
            return "ok", 200
        order_id = str(data.get("id") or "").strip()
        if not order_id:
            _log.warning("[LS] coin-pack order missing order id for client %s pack=%s", cid, pack_key)
            _finish_claimed_ls_event()
            return "ok", 200
        try:
            from student import db as sdb
            if event_name == "order_refunded":
                res = sdb.refund_coin_pack(
                    cid,
                    order_id,
                    refunded_amount=int(attrs.get("refunded_amount") or 0),
                    provider_total=int(attrs.get("total") or 0),
                    fully_refunded=attrs.get("refunded") is True,
                )
                if not res.get("ok"):
                    raise RuntimeError(res.get("error") or "refund reconciliation failed")
            else:
                product_map = {
                    "small": _cfg.LS_TEST_PRODUCT_COIN_SMALL if signature_mode == "test" else _cfg.LS_PRODUCT_COIN_SMALL,
                    "medium": _cfg.LS_TEST_PRODUCT_COIN_MEDIUM if signature_mode == "test" else _cfg.LS_PRODUCT_COIN_MEDIUM,
                    "large": _cfg.LS_TEST_PRODUCT_COIN_LARGE if signature_mode == "test" else _cfg.LS_PRODUCT_COIN_LARGE,
                    "mega": _cfg.LS_TEST_PRODUCT_COIN_MEGA if signature_mode == "test" else _cfg.LS_PRODUCT_COIN_MEGA,
                    "ultra": _cfg.LS_TEST_PRODUCT_COIN_ULTRA if signature_mode == "test" else _cfg.LS_PRODUCT_COIN_ULTRA,
                }
                variant_map = {
                    "small": _cfg.LS_TEST_VARIANT_COIN_SMALL if signature_mode == "test" else _cfg.LS_VARIANT_COIN_SMALL,
                    "medium": _cfg.LS_TEST_VARIANT_COIN_MEDIUM if signature_mode == "test" else _cfg.LS_VARIANT_COIN_MEDIUM,
                    "large": _cfg.LS_TEST_VARIANT_COIN_LARGE if signature_mode == "test" else _cfg.LS_VARIANT_COIN_LARGE,
                    "mega": _cfg.LS_TEST_VARIANT_COIN_MEGA if signature_mode == "test" else _cfg.LS_VARIANT_COIN_MEGA,
                    "ultra": _cfg.LS_TEST_VARIANT_COIN_ULTRA if signature_mode == "test" else _cfg.LS_VARIANT_COIN_ULTRA,
                }
                expected_store = str(
                    _cfg.LEMON_SQUEEZY_TEST_STORE_ID
                    if signature_mode == "test"
                    else _cfg.LEMON_SQUEEZY_STORE_ID
                ).strip()
                expected_product = str(product_map.get(pack_key) or "").strip()
                expected_variant = str(variant_map.get(pack_key) or "").strip()
                actual_store = str(attrs.get("store_id") or "").strip()
                actual_product = str(first_order_item.get("product_id") or "").strip()
                actual_variant = str(first_order_item.get("variant_id") or "").strip()
                if not (
                    expected_store
                    and actual_store == expected_store
                    and expected_product
                    and actual_product == expected_product
                    and expected_variant
                    and actual_variant == expected_variant
                ):
                    _finish_claimed_ls_event("unapproved-coin-pack-product")
                    _log.warning(
                        "[LS] rejected coin pack product/variant pack=%r client=%s",
                        pack_key,
                        cid,
                    )
                    return "Unsupported coin pack product", 400
                res = sdb.credit_coin_pack(cid, pack_key, order_id=order_id)
            if res.get("duplicate"):
                _log.info("[LS] duplicate coin-pack order ignored: %s", order_id)
        except Exception as e:
            _log.exception("[LS] coin-pack reconciliation failed: %s", e)
            _finish_claimed_ls_event("coin-reconciliation-failed")
            return "Coin reconciliation failed", 503
        _finish_claimed_ls_event()
        return "ok", 200

    return "Unsupported product", 400
# ---------------------------------------------------------------------------
# Focus Guard (browser extension) download
# ---------------------------------------------------------------------------

@app.route("/download/focus-guard.zip")
def download_focus_guard():
    """Ship the Focus Guard Chrome extension as a zip the user can load-unpack."""
    import io
    import os
    import zipfile
    ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extensions", "focus-guard")
    if not os.path.isdir(ext_dir):
        return "Focus Guard extension bundle not found on this server.", 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(ext_dir):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, ext_dir)
                zf.write(full, arcname=rel)
    buf.seek(0)
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = 'attachment; filename="machreach-focus-guard.zip"'
    return resp


# ---------------------------------------------------------------------------
# Privacy Policy & Terms of Service
# ---------------------------------------------------------------------------

def _public_info_page(title: str, eyebrow: str, intro: str, body_html: str, active_page: str):
    return _render(title, Markup(f"""
    <style>
      .mr-public-info {{
        max-width:1120px;
        margin:0 auto 64px;
        padding:18px clamp(4px,1vw,14px) 34px;
        color:var(--text);
      }}
      .mr-public-hero {{
        position:relative;
        overflow:hidden;
        border:2px solid #1A1A1F;
        border-radius:30px;
        padding:clamp(30px,5vw,54px);
        background:linear-gradient(135deg,#FFFDF8 0%,#FFF2E8 58%,#EAF7DE 100%);
        box-shadow:0 8px 0 #1A1A1F,0 28px 72px rgba(20,18,30,.12);
        margin-bottom:22px;
      }}
      .mr-public-hero:before {{
        content:"";
        position:absolute;
        inset:14px;
        border:1px dashed rgba(26,26,31,.16);
        border-radius:22px;
        pointer-events:none;
      }}
      .mr-public-hero > * {{ position:relative;z-index:1; }}
      .mr-public-kicker {{
        display:inline-flex;
        align-items:center;
        min-height:30px;
        padding:0 13px;
        border:2px solid #FF7A3D;
        border-radius:999px;
        background:#FFF8EE;
        color:#8B3A18;
        font-size:11px;
        font-weight:950;
        letter-spacing:.1em;
        text-transform:uppercase;
        box-shadow:0 3px 0 rgba(26,26,31,.18);
      }}
      .mr-public-hero h1 {{
        margin:18px 0 10px;
        font-family:'Bricolage Grotesque',sans-serif;
        font-size:clamp(42px,6vw,72px);
        line-height:.95;
        font-weight:800;
        letter-spacing:0;
        color:#1A1A1F;
      }}
      .mr-public-hero p {{
        max-width:760px;
        margin:0;
        color:#48443E;
        font-size:17px;
        line-height:1.55;
        font-weight:750;
      }}
      .mr-public-card {{
        border:2px solid #1A1A1F;
        border-radius:24px;
        background:#FFFFFF;
        box-shadow:0 6px 0 #1A1A1F,0 22px 54px rgba(20,18,30,.10);
        padding:clamp(24px,3vw,36px);
        line-height:1.75;
        color:#2D2A26;
        font-size:15px;
      }}
      .mr-public-card h2 {{
        margin:28px 0 10px;
        color:#1A1A1F;
        font-family:'Bricolage Grotesque',sans-serif;
        font-size:24px;
        line-height:1.08;
      }}
      .mr-public-card h2:first-child {{ margin-top:0; }}
      .mr-public-card p {{ margin:0 0 14px; }}
      .mr-public-card ul {{ padding-left:20px;margin:0 0 16px; }}
      .mr-public-card a {{ color:#C24F19;font-weight:850;text-decoration:none; }}
      .mr-public-card .mr-note {{
        background:#FFF3EA;
        border:1px solid #FFD0B5;
        border-radius:16px;
        padding:14px 16px;
        margin-bottom:22px;
        color:#5C4033;
      }}
      :root[data-theme="dark"] .mr-public-hero {{
        background:linear-gradient(135deg,#12101A 0%,#1D1B26 58%,#2A1B16 100%);
        border-color:#FF7A3D;
        box-shadow:0 8px 0 #FF7A3D,0 30px 76px rgba(0,0,0,.36);
      }}
      :root[data-theme="dark"] .mr-public-hero:before {{ border-color:rgba(255,122,61,.24); }}
      :root[data-theme="dark"] .mr-public-kicker {{
        background:rgba(255,122,61,.14);
        color:#FFF8E1;
        border-color:#FF7A3D;
      }}
      :root[data-theme="dark"] .mr-public-hero h1,
      :root[data-theme="dark"] .mr-public-card h2 {{ color:#FFF8E1; }}
      :root[data-theme="dark"] .mr-public-hero p {{ color:#D8D1C8; }}
      :root[data-theme="dark"] .mr-public-card {{
        background:#15141D;
        border-color:#FF7A3D;
        color:#EDE4DA;
        box-shadow:0 6px 0 #FF7A3D,0 24px 58px rgba(0,0,0,.32);
      }}
      :root[data-theme="dark"] .mr-public-card .mr-note {{
        background:rgba(255,122,61,.12);
        border-color:rgba(255,122,61,.42);
        color:#FFE1CB;
      }}
      @media (max-width:700px) {{
        .mr-public-info {{ padding-top:8px; }}
        .mr-public-hero,.mr-public-card {{ border-radius:22px; }}
      }}
    </style>
    <section class="mr-public-info">
      <div class="mr-public-hero">
        <span class="mr-public-kicker">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{intro}</p>
      </div>
      <div class="mr-public-card">{body_html}</div>
    </section>
    """), active_page=active_page)

@app.route("/privacy")
def privacy_page():
    return _public_info_page("Política de Privacidad", "Legal", "Cómo MachReach trata los datos de tu cuenta, tu estudio, la extensión de Canvas y tu suscripción.", """
        <p class="mr-note"><strong>Resumen en simple:</strong> MachReach ayuda a los estudiantes a llevar sus horas de enfoque, ramos, notas, flashcards, quizzes, ranking, rachas, amigos y progreso de estudio. Recolectamos solo lo que el producto necesita, nunca vendemos tus datos, las contraseñas se guardan cifradas con bcrypt, y puedes desconectar Canvas o eliminar tu cuenta desde Ajustes.</p>
        <p><strong>Última actualización:</strong> 28 de julio de 2026</p>
        <p>MachReach se opera desde Santiago de Chile. Las consultas sobre privacidad y derechos sobre tus datos pueden enviarse a <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
        <h2>1. Información que recolectamos</h2>
        <p><strong>Datos de la cuenta:</strong> tu nombre, tu correo institucional o de Canvas, tu universidad y el hash de tu contraseña. Cuando creas la cuenta a través de Canvas, MachReach lee el correo que expone tu perfil de Canvas para que después puedas entrar con ese correo y la contraseña que definiste.</p>
        <p><strong>Datos de Canvas LMS:</strong> si conectas Canvas, la extensión de MachReach lee tu lista de ramos desde tu propia sesión de Canvas y la envía a MachReach para mostrarte tus ramos y armar los rankings por ramo. Solo leemos tu lista de ramos &mdash; no entregamos tareas, no cambiamos notas ni publicamos contenido.</p>
        <p><strong>Ramos que agregas a mano:</strong> si tu universidad no usa Canvas, o simplemente lo prefieres, puedes agregar ramos escribiendo el código y el nombre. Para que otros estudiantes <em>de tu misma universidad</em> los completen más rápido, los códigos y nombres que agregas pueden guardarse en un catálogo de autocompletado compartido y acotado a esa universidad. Ese catálogo guarda solo el código y el nombre del ramo junto con la universidad &mdash; nunca se vincula a tu identidad, a tus notas ni a tu actividad de estudio, y solo se muestra a estudiantes de la misma universidad.</p>
        <p><strong>Material de estudio:</strong> archivos, apuntes y texto que decides subir o escribir para funciones como quizzes y flashcards.</p>
        <p><strong>Actividad de estudio:</strong> sesiones de enfoque, minutos estudiados por ramo, eventos de XP, rachas, insignias, intentos de quiz, repasos de flashcards, posición en el ranking, resultados de ramos, las notas que ingresas y el movimiento de monedas dentro de la app.</p>
        <p><strong>Benchmarks de ramos:</strong> las estadísticas de benchmark combinan únicamente resultados anónimos del mismo ramo canónico, en la misma universidad y en la misma cohorte o versión del ramo. MachReach oculta el agregado hasta que al menos cinco estudiantes hayan reportado un resultado. Tu nota o tu tiempo de estudio individual no se muestran a otros estudiantes.</p>
        <p><strong>Verificación de universidad para premios físicos:</strong> elegir una universidad hoy no verifica que estés matriculado. Si MachReach ofrece premios físicos, equivalentes a dinero u otros premios del mundo real, a los posibles ganadores se les pedirá verificar que pertenecen a la universidad asociada a su cuenta antes de entregar el premio. La información de verificación se limitará a lo necesario para confirmar la elegibilidad y se tratará conforme a esta política.</p>
        <p><strong>Actividad social:</strong> conexiones de amistad y actividad de referidos si invitas amigos. Guardamos con quién eres amigo y cuántas personas se unieron con tu enlace de invitación.</p>
        <p><strong>Extensión Focus Guard:</strong> la configuración de la extensión y el estado de la sesión activa se usan para sostener las sesiones de enfoque. Algunas preferencias pueden quedar guardadas localmente en tu navegador.</p>
        <p><strong>Datos de pago:</strong> el cobro lo procesa Lemon Squeezy. Recibimos el estado y los identificadores de la suscripción, nunca números de tarjeta.</p>
        <p><strong>Datos técnicos:</strong> registros de seguridad y de la aplicación, dirección IP e información del navegador usadas para prevenir abusos, cookies de sesión, tus decisiones de consentimiento, diagnósticos de errores y analítica de producto opcional después de tu consentimiento.</p>
        <h2>2. Cómo usamos tu información</h2>
        <ul>
          <li>Para crear tu cuenta, autenticarte y permitirte restablecer tu contraseña</li>
          <li>Para importar tu lista de ramos de Canvas cuando eliges conectarla con la extensión de MachReach, o para guardar los ramos que agregas a mano</li>
          <li>Para sostener el autocompletado de ramos por universidad, y que quienes estudian en la misma universidad agreguen ramos más rápido</li>
          <li>Para generar y administrar quizzes, flashcards, sesiones de enfoque, seguimiento de notas y analíticas de ramos</li>
          <li>Para llevar XP, rachas, posiciones en el ranking, insignias, monedas, amigos y referidos</li>
          <li>Para procesar suscripciones, compras de packs de monedas, reembolsos, cancelaciones y eliminación de cuentas</li>
          <li>Para enviarte correos de verificación, de restablecimiento de contraseña, operativos y de estudio que hayas activado</li>
          <li>Para prevenir fraude y abuso, diagnosticar fallas y mantener el servicio seguro</li>
        </ul>
        <h2>3. Seguridad de los datos</h2>
        <p>Usamos HTTPS/TLS, protección CSRF, límites de frecuencia, cabeceras de seguridad estrictas, SQL parametrizado, escape de HTML, cookies seguras en producción, contraseñas cifradas y controles de acceso para los datos sensibles de la cuenta.</p>
        <h2>4. Proveedores y procesamiento internacional</h2>
        <ul>
          <li><strong>OpenAI:</strong> el contenido que envías para generar quizzes o flashcards con IA puede enviarse para producir ese material. Según su política de uso de datos de API, OpenAI no entrena con datos de API.</li>
          <li><strong>Instructure / Canvas LMS:</strong> importación opcional de perfil y ramos cuando conectas Canvas.</li>
          <li><strong>Lemon Squeezy:</strong> checkout alojado, suscripciones, compras de packs de monedas y eventos de pago.</li>
          <li><strong>Render:</strong> hosting de la aplicación e infraestructura de base de datos.</li>
          <li><strong>Sentry:</strong> reporte de errores con los campos sensibles depurados cuando es posible, solo si el monitoreo de producción está configurado.</li>
          <li><strong>PostHog:</strong> analítica de producto opcional, cargada solo después de que elijas &ldquo;Permitir analítica&rdquo; en el banner de cookies. Puedes retirar tu consentimiento borrando o cambiando las cookies de tu navegador.</li>
        </ul>
        <p>Estos proveedores pueden procesar datos fuera de Chile bajo sus respectivas salvaguardas contractuales y de seguridad.</p>
        <h2>5. Conservación y eliminación</h2>
        <p>Los datos de tu cuenta y de tu estudio se conservan mientras la cuenta esté activa. La eliminación permanente primero intenta cancelar una suscripción activa y luego elimina los datos vinculados a la cuenta de la base de datos principal de la aplicación. Podemos conservar registros limitados de eventos de facturación, seguridad, prevención de fraude y de la propia eliminación cuando sea necesario para completar la solicitud, resolver disputas, evitar cobros duplicados o cumplir la ley. Pueden quedar copias temporales en respaldos protegidos del proveedor hasta que esos respaldos expiren según su calendario.</p>
        <h2>6. Tus derechos</h2>
        <p>Puedes solicitar acceso, rectificación, eliminación, oposición y portabilidad de tus datos. MachReach ofrece una exportación en JSON y la eliminación permanente desde Ajustes. También puedes desconectar integraciones, darte de baja de los correos de estudio opcionales o escribir a <a href="mailto:support@machreach.com">support@machreach.com</a>. Podemos necesitar verificar tu identidad antes de actuar sobre una solicitud.</p>
        <p>El nuevo marco chileno de datos personales de la Ley 21.719 entra en vigencia el 1 de diciembre de 2026. MachReach está preparando sus procesos para los derechos y obligaciones adicionales que rigen desde esa fecha.</p>
        <h2>7. Menores de edad</h2>
        <p>MachReach no está dirigido a menores de 16 años. Si crees que un menor de 16 creó una cuenta, escríbenos para investigarlo y eliminarla.</p>
        <h2>8. Contacto y cambios</h2>
        <p>Publicaremos los cambios relevantes en esta página y actualizaremos la fecha de arriba. Consultas, reclamos o solicitudes sobre tus datos: <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "privacy")


@app.route("/terms")
def terms_page():
    return _public_info_page("Términos del Servicio", "Legal", "Las reglas para usar MachReach: suscripciones, herramientas de estudio con IA, ranking y seguridad de tu cuenta.", """
        <p><strong>Última actualización:</strong> 28 de julio de 2026</p>
        <h2>1. Aceptación de los términos</h2>
        <p>Al crear una cuenta o usar MachReach, aceptas estos Términos del Servicio. Si no estás de acuerdo, no uses el servicio.</p>
        <h2>2. Descripción del servicio</h2>
        <p>MachReach entrega herramientas de estudio para estudiantes: importación de ramos con la extensión de Canvas, ramos agregados a mano, temporizadores de enfoque, registro de horas de estudio, flashcards y quizzes de práctica generados con IA, seguimiento de notas, XP, rachas, rankings, amigos, monedas y una tienda dentro de la app, analíticas de ramos, un programa de referidos y una extensión opcional de navegador llamada Focus Guard. Algunas funciones requieren el plan de pago Plus.</p>
        <h2>3. Responsabilidades de tu cuenta</h2>
        <ul>
          <li>Debes entregar información veraz al registrarte, incluida tu universidad</li>
          <li>Eres responsable de mantener seguras las credenciales de tu cuenta</li>
          <li>Si conectas Canvas, debes usar tu propia cuenta de Canvas y la extensión de MachReach</li>
          <li>Al agregar ramos a mano, aceptas que puedan guardarse en un catálogo de autocompletado compartido y acotado a tu universidad (solo códigos y nombres de ramos)</li>
          <li>No debes compartir tu cuenta con otras personas</li>
          <li>Debes tener al menos 16 años para usar MachReach</li>
          <li>No debes intentar sondear, escanear ni explotar vulnerabilidades del servicio</li>
        </ul>
        <h2>4. Integridad académica</h2>
        <p>Eres responsable de cumplir las políticas de integridad académica de tu institución. MachReach es una ayuda de estudio; usarlo para plagiar, copiar o violar códigos de honor está prohibido.</p>
        <h2>5. Suscripciones, renovaciones y cobros</h2>
        <p>Plus es una suscripción recurrente procesada por Lemon Squeezy. El checkout muestra el precio, la moneda, el intervalo de cobro, los impuestos y las condiciones de pago antes de comprar. Salvo que el checkout diga otra cosa, las suscripciones se renuevan automáticamente por el mismo intervalo hasta que las canceles.</p>
        <p>Puedes cancelar desde los controles de plan en MachReach o escribiendo a soporte. La cancelación detiene la siguiente renovación; normalmente el acceso continúa hasta el término del período ya pagado y después vuelve al plan gratuito. Si un pago falla, el acceso pagado puede restringirse mientras Lemon Squeezy reintenta o actualiza la suscripción, y se restablece cuando el pago se recupera.</p>
        <p>Eliminar una cuenta con una suscripción activa primero dispara la cancelación con el proveedor. Si esa cancelación no se puede confirmar, MachReach mantiene la cuenta intacta para que puedas reintentar la solicitud sin dejar un cobro recurrente huérfano.</p>
        <h2>6. Packs de monedas</h2>
        <p>Los packs de monedas son compras digitales de una sola vez. Las monedas no tienen valor en dinero y no pueden transferirse, revenderse ni canjearse por dinero. Registramos los identificadores de los eventos del proveedor para evitar que un webhook duplicado o reenviado acredite una compra dos veces.</p>
        <h2>7. Reembolsos y derecho de retracto</h2>
        <p>Las solicitudes de reembolso se envían a <a href="mailto:support@machreach.com">support@machreach.com</a> con el correo de la compra y el identificador de la orden. Nada en estos Términos excluye un reembolso, cancelación, garantía o derecho de retracto que no pueda renunciarse legalmente. Los consumidores en Chile conservan los derechos que les otorga la Ley 19.496 y las normas de comercio electrónico.</p>
        <p>El reembolso total de un pago de la suscripción Plus termina el acceso pagado cuando Lemon Squeezy lo confirma. Un reembolso parcial de la suscripción no cambia el acceso, salvo que soporte te indique otra cosa como parte de la solución acordada. Un pack de monedas reembolsado se revierte en proporción al reembolso acumulado. Si esas monedas ya se gastaron, las monedas que ganes después saldan primero ese ajuste; tu saldo disponible nunca quedará en negativo.</p>
        <h2>8. Funciones con IA</h2>
        <p>Los quizzes, flashcards y demás contenido generado con IA son sugerencias y pueden estar incompletos o equivocados. Es tu responsabilidad revisar el contenido generado antes de apoyarte en él académicamente.</p>
        <h2>9. Rankings, monedas y referidos</h2>
        <p>El XP, las monedas, las rachas, las insignias y las posiciones en el ranking son parte de la capa de juego: no tienen valor en dinero, no pueden transferirse ni venderse entre cuentas y solo pueden canjearse dentro de MachReach. Las recompensas por referidos (como días gratis de Plus) se otorgan solo por registros genuinos. Podemos retener, revertir o reiniciar recompensas, posiciones o crédito de referidos ante sospechas de trampa o abuso.</p>
        <p>Elegir una universidad en tu perfil no es prueba de matrícula. Si MachReach ofrece un premio físico, equivalente a dinero u otro premio del mundo real, la elegibilidad y la entrega quedan condicionadas a que quien lo reciba verifique su matrícula o vínculo con la universidad registrada en su cuenta. Quien no complete la verificación, haya declarado una universidad incorrecta o no sea elegible por otro motivo puede quedar descalificado y el premio puede pasar al siguiente elegible.</p>
        <h2>10. Disponibilidad y responsabilidad</h2>
        <p>MachReach es una ayuda de estudio y no garantiza resultados académicos ni disponibilidad ininterrumpida. En la máxima medida que permita la ley, MachReach no responde por pérdidas indirectas o consecuenciales causadas por confiar en contenido generado o por caídas fuera de su control razonable. Esto no limita responsabilidades ni remedios que no puedan limitarse conforme a la ley del consumidor.</p>
        <h2>11. Ley chilena y cambios</h2>
        <p>Estos Términos se rigen por la ley chilena. Las protecciones obligatorias al consumidor y los derechos de jurisdicción se mantienen intactos. Los cambios relevantes se publicarán aquí con la fecha actualizada y, cuando corresponda, con aviso adicional.</p>
        <h2>12. Contacto</h2>
        <p>¿Dudas sobre estos términos? Escribe a <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "terms")


@app.route("/cookies")
def cookies_page():
    return _public_info_page("Cookies", "Privacidad", "Cookies esenciales del producto y analítica opcional, solo con tu consentimiento.", """
      <p>MachReach usa cookies esenciales para la sesión de inicio de sesión, la protección CSRF, el idioma y algunas preferencias básicas de la interfaz. Si las bloqueas, el inicio de sesión y las funciones de estudiante pueden dejar de funcionar.</p>
      <p>La analítica de producto de PostHog es opcional y no se carga hasta que aceptas explícitamente la analítica en el banner de cookies. No usamos cookies publicitarias ni vendemos datos de analítica.</p>
      <p><button type="button" class="btn btn-outline" onclick="document.cookie='analytics_consent=0;path=/;max-age=31536000;SameSite=Lax';window.location.reload();">Desactivar analítica</button></p>
      <p>Consultas: <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "cookies")


@app.route("/about")
def about_page():
    return _public_info_page("Sobre MachReach", "Hecho en Chile", "Una plataforma de estudio que junta ramos, enfoque, apuntes, quizzes, ranking y progreso en un solo lugar.", """
      <p>MachReach es una plataforma de estudio para estudiantes, hecha en Santiago de Chile. Partimos de una molestia simple: las herramientas para estudiar están repartidas en diez apps distintas &mdash; una para apuntes, otra para flashcards, otra para el temporizador, otra para llevar las notas &mdash; y ninguna se habla con las demás. MachReach junta todo el flujo de estudio en un solo lugar.</p>

      <h2>Qué puedes hacer</h2>
      <ul>
        <li><strong>Sesiones de enfoque</strong> &mdash; temporizadores sin distracciones, con una extensión opcional que bloquea los sitios que te distraen mientras estudias.</li>
        <li><strong>Ramos y extensión de Canvas</strong> &mdash; conecta Canvas con la extensión de MachReach para importar tus ramos reales de la universidad.</li>
        <li><strong>Flashcards y quizzes</strong> &mdash; genera material de estudio y preguntas de práctica desde tus propios archivos.</li>
        <li><strong>Notas y analíticas</strong> &mdash; lleva tus notas en escala 1,0 &ndash; 7,0 y mira cuánto necesitas para aprobar.</li>
        <li><strong>XP, rachas y ranking</strong> &mdash; mantente motivado con tus amigos y con rankings en vivo por país, universidad y carrera.</li>
      </ul>

      <h2>Para quién es</h2>
      <p>Estudiantes de universidad y de colegio que quieren un sistema de estudio ordenado en vez de un montón de apps sueltas. MachReach está pensado para estudiantes chilenos, pero las herramientas funcionan en cualquier parte.</p>

      <h2>Escríbenos</h2>
      <p>¿Dudas, comentarios o ideas de colaboración? Escríbenos a <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "about")


# ---------------------------------------------------------------------------
# SEO / crawler files
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


@app.route("/sw.js")
def service_worker():
    # Served from the root path so the service worker controls the whole app
    # ("/" scope), not just /static/.
    #
    # VERSION is stamped with the deploy identity rather than read as authored.
    # The worker keys its static cache on VERSION, so a hand-maintained constant
    # means any deploy where someone forgets to bump it leaves returning users
    # on old JS/CSS against new HTML.
    source = (Path(app.static_folder) / "sw.js").read_text(encoding="utf-8")
    body = _SW_VERSION_RE.sub(
        lambda m: f"{m.group(1)}{DEPLOY_VERSION}{m.group(3)}", source, count=1
    )
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/manifest.webmanifest")
def web_manifest():
    resp = make_response(app.send_static_file("manifest.webmanifest"))
    resp.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
    return resp


@app.route("/robots.txt")
def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /student/\n"
        "Disallow: /api/\n"
        "Disallow: /admin/\n"
        "Disallow: /design/\n"
        "Sitemap: https://machreach.com/sitemap.xml\n"
    )
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    paths = ["/", "/about", "/privacy", "/terms", "/cookies", "/login", "/register"]
    urls = "".join(
        f"  <url><loc>https://machreach.com{p}</loc></url>\n" for p in paths
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}"
        "</urlset>\n"
    )
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/xml; charset=utf-8"
    return resp


# ---------------------------------------------------------------------------
# API — Usage check
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API — Email provider detection via MX lookup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API — Email deliverability check (SPF / DKIM / DMARC)
# ---------------------------------------------------------------------------

@app.route("/api/export-my-data")
@limiter.limit("3 per hour")
def api_export_my_data():
    """GDPR Art. 20 — Return all user data as downloadable JSON."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    cid = session["client_id"]
    from machreach_core.db import get_client
    from student.db import export_student_data
    client = get_client(cid)
    if not client:
        return jsonify({"error": "not found"}), 404

    profile = {
        k: client[k]
        for k in ("id", "name", "email", "created_at")
        if k in client
    }

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "profile": profile,
        "student": export_student_data(cid),
    }

    resp = make_response(json.dumps(payload, indent=2, default=str))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=machreach-my-data.json"
    return resp

# ─────────────────────────────────────────────────────────────
# Error handlers — polished 404 / 500 / generic error pages
# ─────────────────────────────────────────────────────────────
def _render_error_page(code, heading, message, sub=""):
    """Render a friendly, branded error page."""
    body = f"""
    <style>
      .err-wrap {{ min-height: 70vh; display: flex; align-items: center; justify-content: center; padding: 60px 24px; position: relative; overflow: hidden; }}
      .err-mesh {{ position: absolute; inset: -30% -20%; z-index: 0; pointer-events: none; }}
      .err-blob {{ position: absolute; border-radius: 50%; filter: blur(90px); opacity: .28; animation: errDrift 14s ease-in-out infinite; }}
      .err-blob.b1 {{ width: 420px; height: 420px; background: #A78BFA; top: 10%; left: 12%; }}
      .err-blob.b2 {{ width: 380px; height: 380px; background: #F472B6; top: 30%; right: 14%; animation-delay: -5s; }}
      .err-blob.b3 {{ width: 340px; height: 340px; background: #6366F1; bottom: 8%; left: 40%; animation-delay: -9s; }}
      @keyframes errDrift {{ 0%,100% {{ transform: translate(0,0) scale(1); }} 50% {{ transform: translate(30px,-20px) scale(1.06); }} }}
      .err-card {{ position: relative; z-index: 1; background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 48px 44px; max-width: 560px; text-align: center; box-shadow: var(--shadow-lg); }}
      .err-code {{ font-size: 88px; font-weight: 900; line-height: 1; letter-spacing: -4px; background: linear-gradient(135deg,#6366F1,#A78BFA,#F472B6); -webkit-background-clip: text; background-clip: text; color: transparent; margin-bottom: 8px; animation: errFloat 4s ease-in-out infinite; }}
      @keyframes errFloat {{ 0%,100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-8px); }} }}
      .err-head {{ font-size: 26px; font-weight: 800; letter-spacing: -.5px; margin: 0 0 8px; }}
      .err-msg {{ color: var(--text-secondary); font-size: 15px; line-height: 1.6; margin: 0 0 10px; }}
      .err-sub {{ color: var(--text-muted); font-size: 12.5px; margin: 0 0 28px; font-family: "Nunito",sans-serif; background: var(--border-light); display: inline-block; padding: 4px 10px; border-radius: 6px; }}
      .err-actions {{ display: inline-flex; gap: 10px; flex-wrap: wrap; justify-content: center; }}
      .err-actions a, .err-actions button {{ padding: 11px 22px; border-radius: 10px; font-weight: 600; font-size: 14px; text-decoration: none; border: none; cursor: pointer; transition: transform .2s var(--ease), box-shadow .2s var(--ease); }}
      .err-actions a.primary {{ background: linear-gradient(135deg,#6366F1,#8B5CF6); color: #fff; box-shadow: 0 1px 2px rgba(15,23,42,.14), inset 0 1px 0 rgba(255,255,255,.14); }}
      .err-actions a.primary:hover {{ transform: translateY(-2px); box-shadow: 0 10px 24px rgba(99,102,241,.32); }}
      .err-actions a.ghost, .err-actions button.ghost {{ background: transparent; color: var(--text); border: 1px solid var(--border); }}
      .err-actions a.ghost:hover {{ background: var(--border-light); }}
    </style>
    <div class="err-wrap">
      <div class="err-mesh" aria-hidden="true">
        <div class="err-blob b1"></div>
        <div class="err-blob b2"></div>
        <div class="err-blob b3"></div>
      </div>
      <div class="err-card reveal in-view">
        <div class="err-code">{code}</div>
        <h1 class="err-head">{heading}</h1>
        <p class="err-msg">{message}</p>
        {f'<div class="err-sub">{sub}</div>' if sub else ''}
        <div class="err-actions" style="margin-top: 18px;">
          <a href="/" class="primary">&larr; Volver al inicio</a>
          <button class="ghost" onclick="history.back()">Atr&aacute;s</button>
        </div>
      </div>
    </div>
    """
    return render_template_string(
        LAYOUT,
        title=f"{code} — {heading}",
        logged_in=_logged_in(),
        messages=[],
        active_page="",
        client_name=session.get("client_name", "") if _logged_in() else "",
        nav=t_dict("nav"),
        student_ui=t_dict("student_ui"),
        tr=t,
        lang=session.get("lang", "es"),
        wide=True,
        content=Markup(body),
    ), code


def _wants_json_error() -> bool:
    """API callers must never be handed an HTML error page — the fetch() then
    dies on "Unexpected token '<'" and the real error is lost."""
    return request.path.startswith("/api/") or request.is_json


@app.errorhandler(404)
def _handle_404(e):
    if _wants_json_error():
        return jsonify({"error": "No encontrado"}), 404
    return _render_error_page(
        404,
        "Esta página se perdió.",
        "El enlace que seguiste puede estar roto o la página se movió. Vuelve al inicio o usa ⌘K para navegar rápido.",
        sub=request.path[:80],
    )


@app.errorhandler(500)
def _handle_500(e):
    try:
        app.logger.exception("500 error at %s %s: %s", request.method, request.path, e)
    except Exception:
        pass
    if _wants_json_error():
        return jsonify({"error": "Algo falló de nuestro lado. Inténtalo de nuevo."}), 500
    return _render_error_page(
        500,
        "Algo se rompió de nuestro lado.",
        "Esta es nuestra culpa. Ya registramos el error y casi siempre se arregla reintentando. Si no, escríbenos a support@machreach.com y lo revisamos.",
    )


@app.errorhandler(429)
def _handle_429(e):
    """Too many requests. On a shared campus network this is far more likely to
    be a busy building than an attack, so it says so and offers a retry."""
    if _wants_json_error():
        return jsonify({"error": "Demasiadas solicitudes. Espera un momento."}), 429
    return _render_error_page(
        429,
        "Vamos demasiado rápido.",
        "Recibimos muchas solicitudes desde tu red en muy poco tiempo — pasa cuando "
        "media universidad entra a la vez. Espera un minuto y vuelve a intentarlo; "
        "no perdiste nada.",
    )


@app.errorhandler(403)
def _handle_403(e):
    return _render_error_page(
        403,
        "Esta zona no es para ti.",
        "No tienes permiso para entrar a esta página. Si crees que es un error, escribe a soporte.",
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=port)
