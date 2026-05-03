"""
Flask web dashboard — client-facing campaign management.
"""
from __future__ import annotations

import bcrypt
import hashlib
import html as html_module
import os

import json
from datetime import datetime

from flask import Flask, flash, jsonify, make_response, redirect, render_template_string, request, session, url_for, Response
from markupsafe import Markup

from outreach.ai import generate_sequence, personalize_email, generate_reply_draft, get_optimal_send_hour
from outreach.config import ADMIN_ACTION_SECRET, ADMIN_EMAILS, SECRET_KEY, SENDER_NAME
from outreach.i18n import t, t_dict

# ── Sentry error tracking (production only — set SENTRY_DSN env var) ──
from outreach.config import SENTRY_DSN
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment="production" if os.getenv("RENDER", "") else "development",
    )

from outreach.db import (
    add_contacts,
    create_campaign,
    create_client,
    create_reset_token,
    create_verification_token,
    delete_campaign,
    delete_contact,
    delete_sequence,
    duplicate_campaign,
    get_campaign,
    get_campaign_stats,
    get_campaigns,
    get_client,
    get_client_by_email,
    get_campaign_contacts,
    get_contacts,
    get_export_data,
    get_global_stats,
    get_reply_context,
    get_sent_emails,
    get_sequences,
    get_valid_reset_token,
    get_valid_verification_token,
    init_db,
    mark_email_verified,
    mark_reset_token_used,
    save_sequence,
    update_campaign_status,
    update_client,
    update_client_password,
    update_sequence,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── Security: session cookie hardening ──
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# HTTPS-only cookies in production (Render always runs behind TLS)
_IS_PRODUCTION = bool(os.getenv("RENDER", "")) or os.getenv("FLASK_ENV") == "production"
app.config["SESSION_COOKIE_SECURE"] = _IS_PRODUCTION
app.config["SESSION_COOKIE_NAME"] = "machreach_sess"
# Trust Render/Heroku-style proxy headers so secure-cookie detection works
if _IS_PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours max session
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB upload limit

# ── Security: CSRF protection ──
from flask_wtf.csrf import CSRFProtect, generate_csrf
csrf = CSRFProtect(app)

# ── Security: Rate limiting ──
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# ── Startup diagnostic — log DB path so we can debug persistence ──
import logging
from outreach.config import DATABASE_PATH
logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("machreach")
_log.info(f"DATABASE_PATH = {DATABASE_PATH}")
_log.info(f"DATABASE_PATH exists = {DATABASE_PATH.exists()}")
_log.info(f"/data dir exists = {os.path.isdir('/data')}")
if os.path.isdir('/data'):
    _log.info(f"/data contents = {os.listdir('/data')}")

# Ensure DB is initialized (for gunicorn and direct run)
init_db()

# ── MachReach Student module ──
from student.db import init_student_db
from student.routes import register_student_routes
from student.academic_routes import register_academic_routes
init_student_db()
register_student_routes(app, csrf, limiter)
register_academic_routes(app, csrf, limiter)


# ---------------------------------------------------------------------------
# System email helper — sends transactional emails from support@machreach.com
# ---------------------------------------------------------------------------

def _send_system_email(to: str, subject: str, body: str) -> bool:
    """Send a transactional email (verification, reset, invite) from the system account.
    Returns True on success."""
    from outreach.config import SMTP_HOST, SMTP_PORT
    from outreach.config import SYSTEM_FROM_EMAIL, SYSTEM_FROM_NAME, SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    print(f"[SYSTEM EMAIL] Attempting to send to {to} via {SMTP_HOST}:{SMTP_PORT} as {SYSTEM_SMTP_USER}", flush=True)
    if not SYSTEM_SMTP_USER or not SYSTEM_SMTP_PASSWORD:
        print(f"[SYSTEM EMAIL] SMTP credentials not set — SYSTEM_SMTP_USER={'set' if SYSTEM_SMTP_USER else 'EMPTY'}, SYSTEM_SMTP_PASSWORD={'set' if SYSTEM_SMTP_PASSWORD else 'EMPTY'}", flush=True)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SYSTEM_FROM_NAME} <{SYSTEM_FROM_EMAIL}>" if SYSTEM_FROM_EMAIL else SYSTEM_SMTP_USER
    msg["To"] = to
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
        print(f"[SYSTEM EMAIL] Send FAILED ({to}): {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Health check — Render uses this to know if the app is alive
# ---------------------------------------------------------------------------

@app.route("/health")
@limiter.exempt
def health_check():
    """Lightweight health probe for Render / load balancers."""
    try:
        from outreach.db import get_db, _fetchval
        with get_db() as db:
            _fetchval(db, "SELECT 1")
        return jsonify({"status": "ok", "db": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 503


@app.route("/api/debug/smtp-test")
@limiter.exempt
def debug_smtp_test():
    """Diagnose SMTP — test connection without sending."""
    from outreach.config import SMTP_HOST, SMTP_PORT, SYSTEM_FROM_EMAIL, SYSTEM_SMTP_USER, SYSTEM_SMTP_PASSWORD
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
@limiter.exempt
def debug_smtp_send_test():
    """Actually send a test email to support@machreach.com to verify delivery."""
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
@limiter.exempt
def admin_check_db():
    from outreach.config import SECRET_KEY
    auth = request.headers.get("X-Admin-Key", "")
    if auth != SECRET_KEY:
        return jsonify({"error": "unauthorized"}), 403

    from outreach.db import get_db, _fetchall, _USE_PG, _db_fingerprint
    from outreach.config import DATABASE_URL

    with get_db() as db:
        clients = _fetchall(db, "SELECT id, name, email FROM clients")

    return jsonify({
        "using_pg": _USE_PG,
        "db_fingerprint": _db_fingerprint(),
        "db_url_prefix": (DATABASE_URL[:40] + "...") if DATABASE_URL else "NOT SET",
        "client_count": len(clients),
        "clients": [{"id": c["id"], "name": c["name"], "email": c["email"]} for c in clients],
    })


# ---------------------------------------------------------------------------
# ONE-TIME: Account reset — delete all accounts and notify users
# Remove this endpoint after use!
# ---------------------------------------------------------------------------

@app.route("/api/admin/reset-all-accounts", methods=["POST"])
@limiter.exempt
def admin_reset_all_accounts():
    """One-time admin action: notify all users and delete all accounts."""
    return jsonify({"error": "This one-time destructive endpoint has been removed."}), 410
    from outreach.config import SECRET_KEY
    auth = request.headers.get("X-Admin-Key", "")
    if auth != SECRET_KEY:
        return jsonify({"error": "unauthorized"}), 403

    from outreach.db import get_db, _fetchall, _exec

    # 1. Collect all user emails and names
    with get_db() as db:
        clients = _fetchall(db, "SELECT id, name, email FROM clients")

    if not clients:
        return jsonify({"message": "No accounts found", "total": 0})

    # 2. Send notification email to each user
    sent_count = 0
    failed = []
    for c in clients:
        body = (
            f"Hi {c['name'] or 'there'},\n\n"
            f"Due to a critical update to our security and email verification system, "
            f"all MachReach accounts have been reset.\n\n"
            f"We identified a privacy issue that required an immediate platform-wide reset "
            f"to protect our users. As part of this fix, all existing accounts have been removed.\n\n"
            f"We welcome you to create a new account at:\n"
            f"https://machreach.onrender.com/register\n\n"
            f"We sincerely apologize for the inconvenience and appreciate your understanding. "
            f"Your data security is our top priority.\n\n"
            f"If you have any questions, reply to this email or contact us at support@machreach.com.\n\n"
            f"— The MachReach Team"
        )
        ok = _send_system_email(c["email"], "MachReach — Important Account Update", body)
        if ok:
            sent_count += 1
        else:
            failed.append(c["email"])

    # 3. Delete ALL data
    with get_db() as db:
        # Order matters — foreign keys
        _exec(db, "DELETE FROM sent_emails")
        _exec(db, "DELETE FROM email_sequences")
        _exec(db, "DELETE FROM contacts")
        _exec(db, "DELETE FROM campaigns")
        _exec(db, "DELETE FROM contacts_book")
        _exec(db, "DELETE FROM mail_inbox")
        _exec(db, "DELETE FROM scheduled_emails")
        _exec(db, "DELETE FROM password_reset_tokens")
        _exec(db, "DELETE FROM email_verification_tokens")
        _exec(db, "DELETE FROM email_accounts")
        _exec(db, "DELETE FROM usage_tracking")
        _exec(db, "DELETE FROM subscriptions")
        _exec(db, "DELETE FROM team_members")
        _exec(db, "DELETE FROM clients")

    return jsonify({
        "message": "All accounts deleted",
        "total_users": len(clients),
        "emails_sent": sent_count,
        "emails_failed": failed,
    })


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

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
        update_client_password(client_id, _hash_pw(pw))


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
    email = (c.get("email") or "").strip().lower()
    owner_emails = {e.strip().lower() for e in ADMIN_EMAILS}
    owner_emails.add("ignaciomachuca2005@gmail.com")
    return bool(c.get("is_admin")) or email in owner_emails


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
    """Optional second admin factor for production/admin consoles."""
    if not ADMIN_ACTION_SECRET:
        return True
    return request.form.get("admin_secret", "") == ADMIN_ACTION_SECRET


def _effective_client_id() -> int:
    """Return the client_id to use for data access.
    If the user is a full-access team member, returns the owner's client_id
    so they see the owner's campaigns, contacts, and inbox."""
    cid = session["client_id"]
    from outreach.db import get_team_owner
    owner = get_team_owner(cid)
    return owner if owner else cid


_PRESENCE_LAST_TOUCH = {}  # cid -> last unix-second we wrote a heartbeat
_PRESENCE_TOUCH_THROTTLE = 25  # don't UPDATE more often than every 25s per user


@app.before_request
def _validate_session():
    if "client_id" in session:
        from outreach.db import get_db, _fetchval
        with get_db() as db:
            row = _fetchval(db, "SELECT 1 FROM clients WHERE id = %s",
                            (session["client_id"],))
            if row is None:
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
        except Exception:
            pass


def _esc(text: str) -> str:
    """HTML-escape user content to prevent XSS."""
    return html_module.escape(str(text)) if text else ""


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
    # Content Security Policy — restricts where scripts/styles/images/frames can load from.
    # 'unsafe-inline' is required because MachReach renders heavy inline HTML/CSS/JS
    # via Jinja/f-strings. Everything else is locked down.
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' https:; "
        "connect-src 'self' https://api.openai.com https://*.instructure.com https://cdn.jsdelivr.net; "
        "frame-src 'self' https://open.spotify.com https://www.youtube.com https://www.youtube-nocookie.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self' https://*.lemonsqueezy.com; "
        "object-src 'none'; "
        "upgrade-insecure-requests"
    )
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

LAYOUT = """<!DOCTYPE html>
<html lang="{{lang}}" data-theme="">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="csrf-token" content="{{ csrf_token() }}">
  <title>MachReach — {{title}}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" onload="if(typeof renderMathInElement==='function')renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}],throwOnError:false});"></script>
  <script>
    // Keep the Claude design system consistent across the app.
    (function(){
      try {
        localStorage.removeItem('machreach-theme');
        localStorage.removeItem('mr_theme');
        document.documentElement.removeAttribute('data-theme');
      } catch(e) {}
    })();
    window.applyMrTheme = function(name) {
      try {
        localStorage.removeItem('machreach-theme');
        localStorage.removeItem('mr_theme');
        document.documentElement.removeAttribute('data-theme');
      } catch (e) {}
    };
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
  <style>
    :root {
      --bg: #FAFAFB;
      --card: #FFFFFF;
      --primary: #6366F1;
      --primary-hover: #4F46E5;
      --primary-light: #EEF2FF;
      --primary-dark: #3730A3;
      --green: #10B981;
      --green-hover: #059669;
      --green-light: #D1FAE5;
      --green-dark: #065F46;
      --red: #EF4444;
      --red-hover: #DC2626;
      --red-light: #FEE2E2;
      --yellow: #F59E0B;
      --yellow-light: #FEF3C7;
      --blue: #3B82F6;
      --blue-light: #DBEAFE;
      --text: #0B1220;
      --text-secondary: #475569;
      --text-muted: #94A3B8;
      --border: #E7EAF0;
      --border-light: #F1F3F7;
      --shadow-xs: 0 1px 2px rgba(15,23,42,0.04);
      --shadow: 0 1px 2px rgba(15,23,42,0.04), 0 1px 3px rgba(15,23,42,0.03);
      --shadow-md: 0 4px 10px rgba(15,23,42,0.06), 0 2px 4px rgba(15,23,42,0.03);
      --shadow-lg: 0 16px 40px rgba(15,23,42,0.10);
      --ring: 0 0 0 3px rgba(99,102,241,0.22);
      --radius: 12px;
      --radius-sm: 9px;
      --radius-xs: 7px;
      --ease: cubic-bezier(.22,.61,.36,1);
    }

    /* Dark mode */
    :root[data-theme="dark"] {
      --bg: #0F172A;
      --card: #1E293B;
      --primary-light: #312E81;
      --primary-dark: #C7D2FE;
      --green-light: #064E3B;
      --green-dark: #6EE7B7;
      --red-light: #7F1D1D;
      --yellow-light: #78350F;
      --blue-light: #1E3A5F;
      --text: #E2E8F0;
      --text-secondary: #94A3B8;
      --text-muted: #64748B;
      --border: #334155;
      --border-light: #283548;
      --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
      --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.4), 0 2px 4px -2px rgba(0,0,0,0.3);
      --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.5), 0 4px 6px -4px rgba(0,0,0,0.4);
    }
    :root[data-theme="dark"] .nav {
      background: linear-gradient(135deg, #020617 0%, #0F172A 100%);
      border-bottom-color: rgba(255,255,255,0.04);
    }
    :root[data-theme="dark"] tbody tr:hover td { background: #334155; }
    :root[data-theme="dark"] .hero h1 span { -webkit-text-fill-color: #A5B4FC; }
    :root[data-theme="dark"] .badge-yellow { background: #422006; color: #FCD34D; }
    :root[data-theme="dark"] .badge-blue { background: #172554; color: #93C5FD; }
    :root[data-theme="dark"] .badge-red { background: #450A0A; color: #FCA5A5; }
    :root[data-theme="dark"] .badge-green { background: #052E16; color: #6EE7B7; }
    :root[data-theme="dark"] .badge-gray { background: #334155; color: #CBD5E1; }
    :root[data-theme="dark"] .badge-purple { background: #312E81; color: #C7D2FE; }
    :root[data-theme="dark"] .stat-card { border-color: #334155; }
    :root[data-theme="dark"] .toast-success { background: #064E3B; color: #6EE7B7; border-color: #065F46; }
    :root[data-theme="dark"] .toast-success .toast-progress { background: #6EE7B7; }
    :root[data-theme="dark"] .toast-error { background: #450A0A; color: #FCA5A5; border-color: #7F1D1D; }
    :root[data-theme="dark"] .toast-error .toast-progress { background: #FCA5A5; }
    :root[data-theme="dark"] .toast-info { background: #172554; color: #93C5FD; border-color: #1E3A5F; }
    :root[data-theme="dark"] .toast-info .toast-progress { background: #93C5FD; }
    :root[data-theme="dark"] .seq-body { background: #0F172A; border-color: #334155; }
    :root[data-theme="dark"] .seq-card { border-left-color: #818CF8; }
    :root[data-theme="dark"] .auth-card { border-color: #334155; }
    :root[data-theme="dark"] .tab { color: #94A3B8; }
    :root[data-theme="dark"] .tab:hover { color: #E2E8F0; }
    :root[data-theme="dark"] .tab.active { color: #A5B4FC; border-bottom-color: #818CF8; }
    :root[data-theme="dark"] .feature { border-color: #334155; }
    :root[data-theme="dark"] input, :root[data-theme="dark"] textarea, :root[data-theme="dark"] select {
      background: #0F172A; color: #E2E8F0; border-color: #334155;
    }
    :root[data-theme="dark"] input::placeholder, :root[data-theme="dark"] textarea::placeholder {
      color: #475569;
    }

    /* ─── MachReach student themes ─── */
    /* mr-default = light default; mr-dark inherits dark mode vars */
    :root[data-theme="mr-light"] {
      --bg:#F8FAFC; --card:#FFFFFF; --text:#0F172A; --text-muted:#64748B;
      --border:#E2E8F0; --primary:#6366F1; --primary-hover:#4F46E5;
    }
    :root[data-theme="mr-midnight"] {
      --bg:#050816; --card:#0F172A; --text:#E2E8F0; --text-secondary:#CBD5E1; --text-muted:#94A3B8;
      --border:#1E293B; --border-light:#0F172A; --primary:#8B5CF6; --primary-hover:#7C3AED; --primary-light:#1E1B4B;
      --card-bg:#0F172A;
    }
    :root[data-theme="mr-midnight"] body { background:#050816; }
    :root[data-theme="mr-midnight"] input, :root[data-theme="mr-midnight"] textarea, :root[data-theme="mr-midnight"] select { background:#0F172A; color:#E2E8F0; border-color:#1E293B; }

    :root[data-theme="mr-forest"] {
      --bg:#0b2018; --card:#0f2a20; --text:#d1fae5; --text-secondary:#a7f3d0; --text-muted:#6ee7b7;
      --border:#14532d; --border-light:#0f2a20; --primary:#10B981; --primary-hover:#059669; --primary-light:#064e3b;
    }
    :root[data-theme="mr-forest"] body { background:#0b2018; }
    :root[data-theme="mr-forest"] input, :root[data-theme="mr-forest"] textarea, :root[data-theme="mr-forest"] select { background:#0f2a20; color:#d1fae5; border-color:#14532d; }

    :root[data-theme="mr-ocean"] {
      --bg:#082f49; --card:#0c4a6e; --text:#e0f2fe; --text-secondary:#bae6fd; --text-muted:#7dd3fc;
      --border:#075985; --border-light:#0c4a6e; --primary:#0ea5e9; --primary-hover:#0284c7; --primary-light:#0c4a6e;
    }
    :root[data-theme="mr-ocean"] body { background:#082f49; }
    :root[data-theme="mr-ocean"] input, :root[data-theme="mr-ocean"] textarea, :root[data-theme="mr-ocean"] select { background:#0c4a6e; color:#e0f2fe; border-color:#075985; }

    :root[data-theme="mr-rose"] {
      --bg:#3f0a1a; --card:#500724; --text:#fecdd3; --text-secondary:#fda4af; --text-muted:#fb7185;
      --border:#881337; --border-light:#500724; --primary:#f43f5e; --primary-hover:#e11d48; --primary-light:#4c0519;
    }
    :root[data-theme="mr-rose"] body { background:#3f0a1a; }
    :root[data-theme="mr-rose"] input, :root[data-theme="mr-rose"] textarea, :root[data-theme="mr-rose"] select { background:#500724; color:#fecdd3; border-color:#881337; }

    :root[data-theme="mr-sunset"] {
      --bg:#431407; --card:#7c2d12; --text:#fff7ed; --text-secondary:#fed7aa; --text-muted:#fdba74;
      --border:#9a3412; --border-light:#7c2d12; --primary:#f97316; --primary-hover:#ea580c; --primary-light:#7c2d12;
    }
    :root[data-theme="mr-sunset"] body { background:linear-gradient(135deg,#7c2d12,#431407); }
    :root[data-theme="mr-sunset"] input, :root[data-theme="mr-sunset"] textarea, :root[data-theme="mr-sunset"] select { background:#7c2d12; color:#fff7ed; border-color:#9a3412; }

    :root[data-theme="mr-mono"] {
      --bg:#0a0a0a; --card:#171717; --text:#fafafa; --text-secondary:#d4d4d4; --text-muted:#a3a3a3;
      --border:#262626; --border-light:#171717; --primary:#ffffff; --primary-hover:#e5e5e5; --primary-light:#262626;
    }
    :root[data-theme="mr-mono"] body { background:#0a0a0a; }
    :root[data-theme="mr-mono"] input, :root[data-theme="mr-mono"] textarea, :root[data-theme="mr-mono"] select { background:#171717; color:#fafafa; border-color:#262626; }
    :root[data-theme="mr-mono"] .btn-primary { background:#fff; color:#000; }

    /* Nav theming — each theme gets its own gradient so the top bar matches */
    :root[data-theme="mr-midnight"] .nav { background: linear-gradient(135deg,#020617 0%,#0f172a 100%); }
    :root[data-theme="mr-forest"]   .nav { background: linear-gradient(135deg,#052e1a 0%,#0f2a20 100%); }
    :root[data-theme="mr-ocean"]    .nav { background: linear-gradient(135deg,#0c1e38 0%,#082f49 100%); }
    :root[data-theme="mr-rose"]     .nav { background: linear-gradient(135deg,#2a0612 0%,#500724 100%); }
    :root[data-theme="mr-sunset"]   .nav { background: linear-gradient(135deg,#7c2d12 0%,#b45309 100%); }
    :root[data-theme="mr-mono"]     .nav { background: linear-gradient(135deg,#000 0%,#0a0a0a 100%); }
    :root[data-theme="mr-light"]    .nav { background: linear-gradient(135deg,#ffffff 0%,#f1f5f9 100%); border-bottom:1px solid #e2e8f0; }
    :root[data-theme="mr-light"]    .nav-links a { color:#475569; }
    :root[data-theme="mr-light"]    .nav-links a:hover { color:#0f172a; background: rgba(15,23,42,0.06); }
    :root[data-theme="mr-light"]    .nav-links a.active { color:#0f172a; background: rgba(99,102,241,0.12); }
    :root[data-theme="mr-light"]    .nav-dropdown-menu { background:#ffffff; border-color:#e2e8f0; box-shadow:0 12px 40px rgba(15,23,42,.12); }
    :root[data-theme="mr-light"]    .nav-dropdown-menu a { color:#475569 !important; }
    :root[data-theme="mr-light"]    .nav-dropdown-menu a:hover { color:#0f172a !important; background:rgba(99,102,241,.1) !important; }
    :root[data-theme="mr-light"]    .nav-user { color:#64748b; }
    :root[data-theme="mr-light"]    .nav-logo { color:#0f172a; }

    /* ── Pastel themes (light, colored) ── */
    /* Each pastel sets vars + body bg + inputs + nav gradient + nav link colors. */
    :root[data-theme="mr-lavender"] {
      --bg:#ede9fe; --card:#f5f3ff; --text:#3b0764; --text-secondary:#5b21b6; --text-muted:#6d28d9;
      --border:#c4b5fd; --border-light:#ddd6fe; --primary:#7c3aed; --primary-hover:#6d28d9; --primary-light:#ede9fe;
    }
    :root[data-theme="mr-mint"] {
      --bg:#bbf7d0; --card:#dcfce7; --text:#14532d; --text-secondary:#166534; --text-muted:#15803d;
      --border:#86efac; --border-light:#bbf7d0; --primary:#16a34a; --primary-hover:#15803d; --primary-light:#dcfce7;
    }
    :root[data-theme="mr-peach"] {
      --bg:#fed7aa; --card:#ffedd5; --text:#7c2d12; --text-secondary:#9a3412; --text-muted:#c2410c;
      --border:#fdba74; --border-light:#fed7aa; --primary:#ea580c; --primary-hover:#c2410c; --primary-light:#ffedd5;
    }
    :root[data-theme="mr-sky"] {
      --bg:#bae6fd; --card:#e0f2fe; --text:#0c4a6e; --text-secondary:#075985; --text-muted:#0369a1;
      --border:#7dd3fc; --border-light:#bae6fd; --primary:#0284c7; --primary-hover:#0369a1; --primary-light:#e0f2fe;
    }
    :root[data-theme="mr-butter"] {
      --bg:#fef9c3; --card:#fefce8; --text:#713f12; --text-secondary:#854d0e; --text-muted:#a16207;
      --border:#fde047; --border-light:#fef08a; --primary:#ca8a04; --primary-hover:#a16207; --primary-light:#fefce8;
    }
    :root[data-theme="mr-lilac"] {
      --bg:#f5d0fe; --card:#fae8ff; --text:#581c87; --text-secondary:#7e22ce; --text-muted:#9333ea;
      --border:#e879f9; --border-light:#f0abfc; --primary:#c026d3; --primary-hover:#a21caf; --primary-light:#fae8ff;
    }
    :root[data-theme="mr-blush"] {
      --bg:#fecdd3; --card:#ffe4e6; --text:#881337; --text-secondary:#9f1239; --text-muted:#be123c;
      --border:#fda4af; --border-light:#fecdd3; --primary:#e11d48; --primary-hover:#be123c; --primary-light:#ffe4e6;
    }
    :root[data-theme="mr-sand"] {
      --bg:#e7d9c2; --card:#f4ead7; --text:#44342a; --text-secondary:#5b4636; --text-muted:#78603e;
      --border:#c8a47a; --border-light:#d4be99; --primary:#a16207; --primary-hover:#78603e; --primary-light:#f4ead7;
    }
    :root[data-theme="mr-cottoncandy"] {
      --bg:#fbcfe8; --card:#fce7f3; --text:#831843; --text-secondary:#9d174d; --text-muted:#be185d;
      --border:#f9a8d4; --border-light:#fbcfe8; --primary:#db2777; --primary-hover:#be185d; --primary-light:#fce7f3;
    }
    :root[data-theme="mr-seafoam"] {
      --bg:#a5f3fc; --card:#cffafe; --text:#164e63; --text-secondary:#155e75; --text-muted:#0e7490;
      --border:#67e8f9; --border-light:#a5f3fc; --primary:#0891b2; --primary-hover:#0e7490; --primary-light:#cffafe;
    }
    /* Body backgrounds */
    :root[data-theme="mr-lavender"]    body { background:#ede9fe; }
    :root[data-theme="mr-mint"]        body { background:#bbf7d0; }
    :root[data-theme="mr-peach"]       body { background:#fed7aa; }
    :root[data-theme="mr-sky"]         body { background:#bae6fd; }
    :root[data-theme="mr-butter"]      body { background:#fef9c3; }
    :root[data-theme="mr-lilac"]       body { background:#f5d0fe; }
    :root[data-theme="mr-blush"]       body { background:#fecdd3; }
    :root[data-theme="mr-sand"]        body { background:#e7d9c2; }
    :root[data-theme="mr-cottoncandy"] body { background:#fbcfe8; }
    :root[data-theme="mr-seafoam"]     body { background:#a5f3fc; }
    /* Inputs */
    :root[data-theme="mr-lavender"]    input, :root[data-theme="mr-lavender"]    textarea, :root[data-theme="mr-lavender"]    select { background:#f5f3ff; color:#3b0764; border-color:#c4b5fd; }
    :root[data-theme="mr-mint"]        input, :root[data-theme="mr-mint"]        textarea, :root[data-theme="mr-mint"]        select { background:#dcfce7; color:#14532d; border-color:#86efac; }
    :root[data-theme="mr-peach"]       input, :root[data-theme="mr-peach"]       textarea, :root[data-theme="mr-peach"]       select { background:#ffedd5; color:#7c2d12; border-color:#fdba74; }
    :root[data-theme="mr-sky"]         input, :root[data-theme="mr-sky"]         textarea, :root[data-theme="mr-sky"]         select { background:#e0f2fe; color:#0c4a6e; border-color:#7dd3fc; }
    :root[data-theme="mr-butter"]      input, :root[data-theme="mr-butter"]      textarea, :root[data-theme="mr-butter"]      select { background:#fefce8; color:#713f12; border-color:#fde047; }
    :root[data-theme="mr-lilac"]       input, :root[data-theme="mr-lilac"]       textarea, :root[data-theme="mr-lilac"]       select { background:#fae8ff; color:#581c87; border-color:#e879f9; }
    :root[data-theme="mr-blush"]       input, :root[data-theme="mr-blush"]       textarea, :root[data-theme="mr-blush"]       select { background:#ffe4e6; color:#881337; border-color:#fda4af; }
    :root[data-theme="mr-sand"]        input, :root[data-theme="mr-sand"]        textarea, :root[data-theme="mr-sand"]        select { background:#f4ead7; color:#44342a; border-color:#c8a47a; }
    :root[data-theme="mr-cottoncandy"] input, :root[data-theme="mr-cottoncandy"] textarea, :root[data-theme="mr-cottoncandy"] select { background:#fce7f3; color:#831843; border-color:#f9a8d4; }
    :root[data-theme="mr-seafoam"]     input, :root[data-theme="mr-seafoam"]     textarea, :root[data-theme="mr-seafoam"]     select { background:#cffafe; color:#164e63; border-color:#67e8f9; }
    /* Pastel nav: tinted gradient + dark text on light bg */
    :root[data-theme="mr-lavender"]    .nav { background: linear-gradient(135deg,#ddd6fe 0%,#ede9fe 100%); border-bottom:1px solid #c4b5fd; }
    :root[data-theme="mr-mint"]        .nav { background: linear-gradient(135deg,#86efac 0%,#bbf7d0 100%); border-bottom:1px solid #86efac; }
    :root[data-theme="mr-peach"]       .nav { background: linear-gradient(135deg,#fdba74 0%,#fed7aa 100%); border-bottom:1px solid #fdba74; }
    :root[data-theme="mr-sky"]         .nav { background: linear-gradient(135deg,#7dd3fc 0%,#bae6fd 100%); border-bottom:1px solid #7dd3fc; }
    :root[data-theme="mr-butter"]      .nav { background: linear-gradient(135deg,#fde047 0%,#fef9c3 100%); border-bottom:1px solid #fde047; }
    :root[data-theme="mr-lilac"]       .nav { background: linear-gradient(135deg,#e879f9 0%,#f5d0fe 100%); border-bottom:1px solid #e879f9; }
    :root[data-theme="mr-blush"]       .nav { background: linear-gradient(135deg,#fda4af 0%,#fecdd3 100%); border-bottom:1px solid #fda4af; }
    :root[data-theme="mr-sand"]        .nav { background: linear-gradient(135deg,#c8a47a 0%,#e7d9c2 100%); border-bottom:1px solid #c8a47a; }
    :root[data-theme="mr-cottoncandy"] .nav { background: linear-gradient(135deg,#f9a8d4 0%,#fbcfe8 100%); border-bottom:1px solid #f9a8d4; }
    :root[data-theme="mr-seafoam"]     .nav { background: linear-gradient(135deg,#67e8f9 0%,#a5f3fc 100%); border-bottom:1px solid #67e8f9; }
    /* Pastel nav: dark text on light backgrounds + dropdown menu styling */
    :root[data-theme="mr-lavender"]    .brand, :root[data-theme="mr-mint"] .brand, :root[data-theme="mr-peach"] .brand, :root[data-theme="mr-sky"] .brand, :root[data-theme="mr-butter"] .brand, :root[data-theme="mr-lilac"] .brand, :root[data-theme="mr-blush"] .brand, :root[data-theme="mr-sand"] .brand, :root[data-theme="mr-cottoncandy"] .brand, :root[data-theme="mr-seafoam"] .brand { color: var(--text); }
    :root[data-theme="mr-lavender"]    .nav-links a, :root[data-theme="mr-mint"] .nav-links a, :root[data-theme="mr-peach"] .nav-links a, :root[data-theme="mr-sky"] .nav-links a, :root[data-theme="mr-butter"] .nav-links a, :root[data-theme="mr-lilac"] .nav-links a, :root[data-theme="mr-blush"] .nav-links a, :root[data-theme="mr-sand"] .nav-links a, :root[data-theme="mr-cottoncandy"] .nav-links a, :root[data-theme="mr-seafoam"] .nav-links a { color: var(--text-secondary); }
    :root[data-theme="mr-lavender"]    .nav-links a:hover, :root[data-theme="mr-mint"] .nav-links a:hover, :root[data-theme="mr-peach"] .nav-links a:hover, :root[data-theme="mr-sky"] .nav-links a:hover, :root[data-theme="mr-butter"] .nav-links a:hover, :root[data-theme="mr-lilac"] .nav-links a:hover, :root[data-theme="mr-blush"] .nav-links a:hover, :root[data-theme="mr-sand"] .nav-links a:hover, :root[data-theme="mr-cottoncandy"] .nav-links a:hover, :root[data-theme="mr-seafoam"] .nav-links a:hover { color: var(--text); background: rgba(0,0,0,0.06); }
    :root[data-theme="mr-lavender"]    .nav-links a.active, :root[data-theme="mr-mint"] .nav-links a.active, :root[data-theme="mr-peach"] .nav-links a.active, :root[data-theme="mr-sky"] .nav-links a.active, :root[data-theme="mr-butter"] .nav-links a.active, :root[data-theme="mr-lilac"] .nav-links a.active, :root[data-theme="mr-blush"] .nav-links a.active, :root[data-theme="mr-sand"] .nav-links a.active, :root[data-theme="mr-cottoncandy"] .nav-links a.active, :root[data-theme="mr-seafoam"] .nav-links a.active { color: var(--text); background: rgba(0,0,0,0.1); }
    :root[data-theme="mr-lavender"]    .nav-dropdown-menu, :root[data-theme="mr-mint"] .nav-dropdown-menu, :root[data-theme="mr-peach"] .nav-dropdown-menu, :root[data-theme="mr-sky"] .nav-dropdown-menu, :root[data-theme="mr-butter"] .nav-dropdown-menu, :root[data-theme="mr-lilac"] .nav-dropdown-menu, :root[data-theme="mr-blush"] .nav-dropdown-menu, :root[data-theme="mr-sand"] .nav-dropdown-menu, :root[data-theme="mr-cottoncandy"] .nav-dropdown-menu, :root[data-theme="mr-seafoam"] .nav-dropdown-menu { background: var(--card); border-color: var(--border); box-shadow: 0 12px 40px rgba(0,0,0,0.12); }
    :root[data-theme="mr-lavender"]    .nav-dropdown-menu a, :root[data-theme="mr-mint"] .nav-dropdown-menu a, :root[data-theme="mr-peach"] .nav-dropdown-menu a, :root[data-theme="mr-sky"] .nav-dropdown-menu a, :root[data-theme="mr-butter"] .nav-dropdown-menu a, :root[data-theme="mr-lilac"] .nav-dropdown-menu a, :root[data-theme="mr-blush"] .nav-dropdown-menu a, :root[data-theme="mr-sand"] .nav-dropdown-menu a, :root[data-theme="mr-cottoncandy"] .nav-dropdown-menu a, :root[data-theme="mr-seafoam"] .nav-dropdown-menu a { color: var(--text-secondary) !important; }
    :root[data-theme="mr-lavender"]    .nav-dropdown-menu a:hover, :root[data-theme="mr-mint"] .nav-dropdown-menu a:hover, :root[data-theme="mr-peach"] .nav-dropdown-menu a:hover, :root[data-theme="mr-sky"] .nav-dropdown-menu a:hover, :root[data-theme="mr-butter"] .nav-dropdown-menu a:hover, :root[data-theme="mr-lilac"] .nav-dropdown-menu a:hover, :root[data-theme="mr-blush"] .nav-dropdown-menu a:hover, :root[data-theme="mr-sand"] .nav-dropdown-menu a:hover, :root[data-theme="mr-cottoncandy"] .nav-dropdown-menu a:hover, :root[data-theme="mr-seafoam"] .nav-dropdown-menu a:hover { color: var(--text) !important; background: rgba(0,0,0,0.08) !important; }
    :root[data-theme="mr-lavender"]    .nav-user, :root[data-theme="mr-mint"] .nav-user, :root[data-theme="mr-peach"] .nav-user, :root[data-theme="mr-sky"] .nav-user, :root[data-theme="mr-butter"] .nav-user, :root[data-theme="mr-lilac"] .nav-user, :root[data-theme="mr-blush"] .nav-user, :root[data-theme="mr-sand"] .nav-user, :root[data-theme="mr-cottoncandy"] .nav-user, :root[data-theme="mr-seafoam"] .nav-user { color: var(--text-muted); }

    /* ─── end themes ─── */

    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased; overflow-x: hidden; }
    html { overflow-x: hidden; }

    /* Nav */
    .nav {
      background: #0B1220;
      padding: 0 32px; display: flex; align-items: center; justify-content: space-between;
      height: 60px; position: sticky; top: 0; z-index: 2000; overflow: visible;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      backdrop-filter: saturate(140%) blur(12px);
    }
    .nav .brand { color: #fff; font-weight: 700; font-size: 16px; letter-spacing: -0.3px; display: flex; align-items: center; gap: 10px; text-decoration: none; transition: opacity .18s var(--ease); }
    .nav .brand:hover { opacity: .88; }
    .nav .brand-icon { width: 28px; height: 28px; background: linear-gradient(135deg, var(--primary) 0%, #8B5CF6 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 13px; color: #fff; box-shadow: 0 0 0 1px rgba(255,255,255,0.06) inset; }
    .nav-links { display: flex; align-items: center; gap: 2px; flex-shrink: 0; overflow: visible; }
    .nav-links a { color: #A0AAB8; text-decoration: none; font-size: 13px; font-weight: 500; padding: 7px 12px; border-radius: 8px; transition: color .15s var(--ease), background .15s var(--ease); display:flex; align-items:center; height:34px; line-height:1; box-sizing:border-box; position:relative; }
    .nav-links a:hover { color: #F8FAFC; background: rgba(255,255,255,0.06); }
    .nav-links a.active { color: #fff; background: rgba(255,255,255,0.09); box-shadow: 0 0 0 1px rgba(255,255,255,0.05) inset; }
    .nav-links a:focus-visible { outline: none; box-shadow: 0 0 0 2px rgba(129,140,248,0.55); }
    .nav-links .nav-divider { width: 1px; height: 18px; background: rgba(255,255,255,0.08); margin: 0 6px; }
    .nav-links .nav-user { color: #64748B; font-size: 12px; margin-right: 4px; }
    /* Nav dropdown */
    .nav-dropdown { position: relative; z-index: 2100; }
    .nav-dropdown::after { content:""; position:absolute; left:-6px; right:-6px; top:100%; height:12px; }
    .nav-dropdown > a { cursor: pointer; text-decoration: none; outline: none; user-select: none; }
    .nav-dropdown > a:focus, .nav-dropdown > a:focus-visible, .nav-dropdown > a:active { outline: none !important; box-shadow: none !important; text-decoration: none !important; }
    .nav-dropdown-menu { position:absolute; top:100%; left:0; transform:translateY(4px); opacity:0; visibility:hidden; pointer-events:none; background:#0F172A; border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:6px; min-width:220px; z-index:5000; box-shadow:0 18px 45px rgba(0,0,0,0.38), 0 0 0 1px rgba(255,255,255,0.02); transition:opacity .15s var(--ease), transform .15s var(--ease), visibility 0s linear .15s; }
    .nav-dropdown:hover .nav-dropdown-menu, .nav-dropdown:focus-within .nav-dropdown-menu, .nav-dropdown.open .nav-dropdown-menu { opacity:1; visibility:visible; pointer-events:auto; transform:translateY(0); transition-delay:0s; }
    .nav-dropdown-menu a { display:block; height:auto; padding:8px 12px; font-size:13px; color:#A0AAB8; border-radius:8px; transition: color .15s var(--ease), background .15s var(--ease); }
    .nav-dropdown-menu a:hover { color:#fff; background:rgba(99,102,241,0.18); }
    /* Floating focus widget */
    #focus-float { position:fixed; bottom:20px; right:20px; background:linear-gradient(135deg,#1e293b,#334155); border:1px solid rgba(255,255,255,0.1); border-radius:16px; padding:12px 18px; z-index:500; box-shadow:0 8px 32px rgba(0,0,0,0.4); display:none; cursor:pointer; color:#fff; font-family:monospace; min-width:140px; text-align:center; transition:all 0.3s; }
    #focus-float:hover { transform:scale(1.05); box-shadow:0 12px 40px rgba(99,102,241,0.3); }
    #focus-float .ff-time { font-size:28px; font-weight:800; letter-spacing:1px; }
    #focus-float .ff-label { font-size:11px; color:#94a3b8; margin-top:2px; }
    #focus-float .ff-close { position:absolute; top:4px; right:8px; font-size:14px; color:#64748b; cursor:pointer; }
    #focus-float .ff-close:hover { color:#ef4444; }

    /* Layout — edge-to-edge with comfortable padding */
    .container { max-width: 1440px; margin: 0 auto; padding: 28px 40px; }
    .container.container-wide { max-width: 100%; padding: 28px 40px; }
    @media (max-width: 768px) { .container, .container.container-wide { padding: 20px 16px; } }
    .page-header { margin-bottom: 24px; }
    .page-header h1 { font-size: 26px; font-weight: 700; letter-spacing: -0.6px; line-height: 1.15; }
    .page-header p { color: var(--text-secondary); margin-top: 6px; font-size: 14.5px; }
    .breadcrumb { font-size: 12.5px; color: var(--text-muted); margin-bottom: 14px; }
    .breadcrumb a { color: var(--text-muted); text-decoration: none; transition: color .15s var(--ease); }
    .breadcrumb a:hover { color: var(--primary); }

    /* Cards */
    .card { background: var(--card); border-radius: var(--radius); padding: 24px; box-shadow: var(--shadow-xs); margin-bottom: 18px; border: 1px solid var(--border); transition: box-shadow .2s var(--ease), border-color .2s var(--ease), transform .2s var(--ease); }
    .card:hover { box-shadow: var(--shadow); border-color: #DEE3EC; }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; padding-bottom: 12px; border-bottom: 1px solid var(--border-light); }
    .card-header h2 { font-size: 15px; font-weight: 700; letter-spacing: -0.2px; }

    /* Stats */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 22px; }
    .stat-card { background: var(--card); border-radius: var(--radius); padding: 18px 20px; text-align: left; box-shadow: var(--shadow-xs); border: 1px solid var(--border); position: relative; overflow: hidden; transition: transform .2s var(--ease), box-shadow .2s var(--ease), border-color .2s var(--ease); cursor: default; }
    .stat-card:hover { transform: translateY(-1px); box-shadow: var(--shadow); border-color: #DEE3EC; }
    .stat-card::before { content: ''; position: absolute; top: 0; left: 0; bottom: 0; width: 2px; }
    .stat-purple::before { background: var(--primary); }
    .stat-green::before  { background: var(--green); }
    .stat-blue::before   { background: var(--blue); }
    .stat-yellow::before { background: var(--yellow); }
    .stat-red::before    { background: var(--red); }
    .stat-card .num { font-size: 28px; font-weight: 700; letter-spacing: -0.8px; line-height: 1.1; color: var(--text); }
    .stat-card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-muted); margin-top: 6px; font-weight: 600; }
    .stat-purple .num, .stat-green .num, .stat-blue .num, .stat-yellow .num, .stat-red .num { color: var(--text); }
    .stat-purple .num { color: var(--primary); }
    .stat-green .num { color: var(--green); }
    .stat-blue .num { color: var(--blue); }
    .stat-yellow .num { color: var(--yellow); }
    .stat-red .num { color: var(--red); }

    /* Progress bar */
    .progress-wrap { background: var(--border-light); border-radius: 20px; height: 6px; overflow: hidden; margin-top: 8px; }
    .progress-bar { height: 100%; border-radius: 20px; transition: width 0.5s ease; }
    .progress-bar.bar-purple { background: linear-gradient(90deg, var(--primary), #8B5CF6); }
    .progress-bar.bar-green { background: linear-gradient(90deg, var(--green), #34D399); }

    /* Forms */
    label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    input, textarea, select {
      width: 100%; padding: 10px 13px; border: 1px solid var(--border); border-radius: 9px;
      font-size: 14px; margin-bottom: 14px; background: var(--card); color: var(--text);
      transition: border-color .15s var(--ease), box-shadow .15s var(--ease), background .15s var(--ease); font-family: inherit;
    }
    input:hover, textarea:hover, select:hover { border-color: #CFD5E0; }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--primary); box-shadow: var(--ring); }
    textarea { min-height: 90px; resize: vertical; }
    input[type="file"] { padding: 8px; cursor: pointer; }
    .form-hint { font-size: 11px; color: var(--text-muted); margin-top: -10px; margin-bottom: 14px; }
    .form-group { margin-bottom: 2px; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .form-divider { border: none; border-top: 1px dashed var(--border); margin: 16px 0; }

    /* Buttons */
    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
      text-decoration: none; border: 1px solid transparent; border-radius: 9px;
      transition: background .15s var(--ease), border-color .15s var(--ease), box-shadow .15s var(--ease), transform .15s var(--ease), color .15s var(--ease);
      font-family: inherit; white-space: nowrap; line-height: 1.25;
    }
    .btn:focus-visible { outline: none; box-shadow: var(--ring); }
    .btn-primary { background: var(--primary); color: #fff; box-shadow: 0 1px 2px rgba(15,23,42,0.12), inset 0 1px 0 rgba(255,255,255,0.14); }
    .btn-primary:hover { background: var(--primary-hover); box-shadow: 0 2px 6px rgba(79,70,229,0.28), inset 0 1px 0 rgba(255,255,255,0.14); transform: translateY(-1px); }
    .btn-primary:active { transform: translateY(0); box-shadow: 0 1px 2px rgba(15,23,42,0.14); }
    .btn-green { background: var(--green); color: #fff; box-shadow: inset 0 1px 0 rgba(255,255,255,0.12); }
    .btn-green:hover { background: var(--green-hover); }
    .btn-red { background: var(--red); color: #fff; box-shadow: inset 0 1px 0 rgba(255,255,255,0.12); }
    .btn-red:hover { background: var(--red-hover); }
    .btn-yellow { background: var(--yellow); color: #fff; box-shadow: inset 0 1px 0 rgba(255,255,255,0.14); }
    .btn-yellow:hover { background: #D97706; }
    .btn-outline { background: transparent; color: var(--text-secondary); border-color: var(--border); }
    .btn-outline:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-light); }
    .btn-ghost { background: transparent; color: var(--text-muted); padding: 7px 12px; }
    .btn-ghost:hover { color: var(--text); background: var(--border-light); }
    .btn-sm { padding: 6px 12px; font-size: 12px; border-radius: 7px; }
    .btn-lg { padding: 12px 26px; font-size: 14.5px; border-radius: 11px; }
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn-icon { width: 32px; height: 32px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--radius-xs); }

    /* Tables */
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 10px 12px; color: var(--text-muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; border-bottom: 1px solid var(--border); background: var(--border-light); }
    td { padding: 12px; border-bottom: 1px solid var(--border-light); font-size: 13px; }
    tr:last-child td { border-bottom: none; }
    tbody tr { transition: background .12s var(--ease); }
    tbody tr:hover td { background: var(--border-light); }

    /* Badges */
    .badge { padding: 2px 9px; border-radius: 20px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; display: inline-block; }
    .badge-green { background: var(--green-light); color: var(--green-dark); }
    .badge-yellow { background: var(--yellow-light); color: #92400E; }
    .badge-gray { background: var(--border-light); color: var(--text-secondary); }
    .badge-blue { background: var(--blue-light); color: #1E40AF; }
    .badge-red { background: var(--red-light); color: #991B1B; }
    .badge-purple { background: var(--primary-light); color: var(--primary-dark); }

    /* Toast notifications */
    .toast-container { position: fixed; top: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; pointer-events: none; max-width: 400px; }
    .toast { padding: 14px 20px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 10px; pointer-events: auto; cursor: pointer; box-shadow: 0 4px 20px rgba(0,0,0,.15); animation: toastIn 0.35s ease; position: relative; overflow: hidden; }
    .toast .toast-progress { position: absolute; bottom: 0; left: 0; height: 3px; border-radius: 0 0 var(--radius-sm) var(--radius-sm); animation: toastTimer 4s linear forwards; }
    .toast-success { background: var(--green-light); color: var(--green-dark); border: 1px solid #A7F3D0; }
    .toast-success .toast-progress { background: var(--green-dark); }
    .toast-error { background: var(--red-light); color: #991B1B; border: 1px solid #FECACA; }
    .toast-error .toast-progress { background: #991B1B; }
    .toast-info { background: var(--blue-light); color: #1E40AF; border: 1px solid #93C5FD; }
    .toast-info .toast-progress { background: #1E40AF; }
    .toast.toast-out { animation: toastOut 0.3s ease forwards; }
    .toast-close { margin-left: auto; background: none; border: none; color: inherit; font-size: 16px; cursor: pointer; opacity: .6; padding: 0 2px; }
    .toast-close:hover { opacity: 1; }

    /* Sequence cards */
    .seq-card { background: var(--card); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); margin-bottom: 12px; border: 1px solid var(--border-light); border-left: 3px solid var(--primary); position: relative; }
    .seq-card .seq-actions { position: absolute; top: 16px; right: 16px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.15s; }
    .seq-card:hover .seq-actions { opacity: 1; }
    .seq-step { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--primary); margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
    .seq-step .seq-delay { color: var(--text-muted); font-weight: 500; }
    .seq-subject { font-size: 13px; font-weight: 600; color: var(--text); }
    .seq-subject-label { font-size: 10px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; }
    .seq-body { background: var(--bg); padding: 14px; border-radius: var(--radius-sm); font-size: 12px; line-height: 1.7; white-space: pre-wrap; margin-top: 10px; color: var(--text-secondary); border: 1px solid var(--border-light); }

    /* Tabs */
    .tabs { display: flex; border-bottom: 2px solid var(--border-light); margin-bottom: 20px; gap: 0; overflow-x: auto; }
    .tab { padding: 10px 18px; font-size: 13px; font-weight: 600; color: var(--text-muted); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.15s; text-decoration: none; white-space: nowrap; }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--primary); border-bottom-color: var(--primary); }
    .tab .tab-count { background: var(--border-light); color: var(--text-muted); font-size: 10px; padding: 1px 6px; border-radius: 10px; margin-left: 4px; font-weight: 700; }
    .tab.active .tab-count { background: var(--primary-light); color: var(--primary); }

    /* Empty state */
    .empty { text-align: center; padding: 40px 24px; color: var(--text-muted); }
    .empty-icon { font-size: 40px; margin-bottom: 8px; opacity: 0.4; }
    .empty h3 { color: var(--text-secondary); margin-bottom: 6px; font-size: 15px; }
    .empty p { font-size: 13px; max-width: 300px; margin: 0 auto; }

    /* ─── Polish pack ────────────────────────────────────────────
       Global UI upgrades applied across the whole platform. */

    /* Cards: smoother lift on hover */
    .card { transition: box-shadow 0.25s ease, transform 0.25s ease, border-color 0.2s; }
    .card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); border-color: var(--border); }

    /* Stat cards: nicer hover, bigger number weight */
    .stat-card { transition: transform 0.25s ease, box-shadow 0.25s ease, border-color 0.2s; }
    .stat-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-lg); }
    .stat-card .num { transition: color 0.2s; }

    /* Buttons: crisper active state, subtle depth */
    .btn { letter-spacing: 0.1px; }
    .btn:active { transform: translateY(1px) scale(0.98); }
    .btn-primary { background: linear-gradient(135deg, var(--primary) 0%, #8B5CF6 55%, #A855F7 100%); background-size: 150% 150%; background-position: 0% 0%; transition: all 0.25s ease, background-position 0.4s ease; }
    .btn-primary:hover { background-position: 100% 100%; box-shadow: 0 6px 20px rgba(124,58,237,0.35); transform: translateY(-1px); }
    .btn-outline:hover { box-shadow: 0 2px 8px rgba(99,102,241,0.12); }

    /* Inputs: softer focus ring */
    input:focus, textarea:focus, select:focus { box-shadow: 0 0 0 3px rgba(99,102,241,0.15); }
    :root[data-theme="dark"] input:focus, :root[data-theme="dark"] textarea:focus, :root[data-theme="dark"] select:focus { box-shadow: 0 0 0 3px rgba(129,140,248,0.25); }

    /* Progress bars: shimmer animation */
    .progress-bar { position: relative; overflow: hidden; }
    .progress-bar::after { content:''; position:absolute; inset:0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.35), transparent); animation: shimmer 2.2s infinite; }
    @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

    /* Skeleton loading */
    .skel { background: linear-gradient(90deg, var(--border-light) 0%, var(--border) 50%, var(--border-light) 100%); background-size: 200% 100%; animation: skelShift 1.4s ease infinite; border-radius: var(--radius-xs); display: inline-block; }
    .skel-line { height: 12px; width: 100%; margin: 6px 0; }
    .skel-card { height: 80px; width: 100%; margin-bottom: 10px; border-radius: var(--radius-sm); }
    @keyframes skelShift { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

    /* Mobile-responsive table wrapper */
    .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -12px; padding: 0 12px; border-radius: var(--radius-sm); }
    .table-wrap table { min-width: 560px; }
    @media (max-width: 640px) {
      .container { padding: 20px 16px !important; }
      .container.container-wide { padding: 20px 16px !important; }
      .page-header h1 { font-size: 22px !important; }
      .card { padding: 18px !important; }
      .stats-grid, [style*="grid-template-columns:repeat(4,1fr)"], [style*="grid-template-columns: repeat(4, 1fr)"] { grid-template-columns: repeat(2, 1fr) !important; }
      [style*="grid-template-columns:1fr 1fr"], [style*="grid-template-columns: 1fr 1fr"] { grid-template-columns: 1fr !important; }
    }

    /* Hover lift for link-cards (dashboard XP bar, nav tiles, etc.) */
    .hover-lift { transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s; }
    .hover-lift:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); border-color: var(--primary) !important; }

    /* Gradient text helper */
    .gradient-text { background: linear-gradient(135deg, var(--primary), #8B5CF6, #EC4899); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }

    /* Streak flame pulse */
    .streak-flame { display: inline-block; animation: flamePulse 2s ease-in-out infinite; }
    @keyframes flamePulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }

    /* Fade-in helper for dynamic content */
    .fade-in { animation: fadeIn 0.35s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    /* Stat number pop when updated */
    .num.num-pop { animation: numPop 0.45s cubic-bezier(0.34, 1.56, 0.64, 1); }
    @keyframes numPop { 0% { transform: scale(1); } 50% { transform: scale(1.2); color: var(--primary); } 100% { transform: scale(1); } }

    /* Check/strikethrough animation for plan cards */
    .strike-done { text-decoration: line-through; opacity: 0.55; transition: all 0.35s ease; }

    /* Confetti particle */
    .confetti { position: fixed; width: 8px; height: 14px; top: -20px; z-index: 9999; pointer-events: none; opacity: 0; animation: confettiFall 2.4s ease-out forwards; }
    @keyframes confettiFall {
      0% { opacity: 1; transform: translateY(0) rotate(0deg); }
      100% { opacity: 0; transform: translateY(100vh) rotate(720deg); }
    }

    /* Auth pages */
    .auth-wrapper { max-width: 440px; margin: 60px auto; padding: 0 24px; }
    .auth-card { background: var(--card); border-radius: var(--radius); padding: 40px; box-shadow: var(--shadow-lg); border: 1px solid var(--border-light); }
    .auth-card h1 { font-size: 24px; text-align: center; margin-bottom: 6px; font-weight: 800; }
    .auth-card .subtitle { text-align: center; color: var(--text-muted); margin-bottom: 28px; font-size: 14px; }
    .auth-footer { text-align: center; margin-top: 20px; font-size: 13px; color: var(--text-muted); }
    .auth-footer a { color: var(--primary); font-weight: 600; text-decoration: none; }

    /* Hero */
    .hero { text-align: center; padding: 80px 24px 52px; }
    .hero h1 { font-size: 48px; font-weight: 800; letter-spacing: -2px; line-height: 1.08; margin-bottom: 18px; }
    .hero h1 span { background: linear-gradient(135deg, var(--primary), #8B5CF6, #EC4899); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
    .hero p { font-size: 18px; color: var(--text-secondary); max-width: 500px; margin: 0 auto 32px; line-height: 1.7; }
    .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 18px; max-width: 860px; margin: 0 auto; }
    .feature { background: var(--card); border-radius: var(--radius); padding: 32px 22px; text-align: center; box-shadow: var(--shadow); border: 1px solid var(--border-light); transition: transform 0.2s, box-shadow 0.2s; }
    .feature:hover { transform: translateY(-4px); box-shadow: var(--shadow-md); }
    .feature-icon { font-size: 32px; margin-bottom: 12px; }
    .feature h3 { font-size: 15px; margin-bottom: 6px; font-weight: 700; }
    .feature p { font-size: 13px; color: var(--text-muted); line-height: 1.6; }

    /* Activity */
    .activity-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border-light); }
    .activity-item:last-child { border-bottom: none; }
    .activity-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
    .activity-dot.sent { background: var(--blue); }
    .activity-dot.opened { background: var(--green); }
    .activity-dot.replied { background: var(--primary); }
    .activity-text { font-size: 13px; color: var(--text-secondary); }
    .activity-text strong { color: var(--text); font-weight: 600; }
    .activity-time { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

    /* Preview modal */
    .preview-modal { display: none; position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,0.5); backdrop-filter: blur(4px); justify-content: center; align-items: center; }
    .preview-modal.show { display: flex; }
    .preview-content { background: var(--card); border-radius: var(--radius); width: 90%; max-width: 640px; max-height: 80vh; overflow-y: auto; box-shadow: var(--shadow-lg); }
    .preview-header { padding: 16px 20px; border-bottom: 1px solid var(--border-light); display: flex; justify-content: space-between; align-items: center; }
    .preview-header h3 { font-size: 15px; }
    .preview-body { padding: 24px; }
    .preview-field { margin-bottom: 12px; }
    .preview-field .pf-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); font-weight: 600; margin-bottom: 2px; }
    .preview-field .pf-value { font-size: 14px; }
    .preview-email { background: var(--bg); border: 1px solid var(--border-light); border-radius: var(--radius-sm); padding: 20px; font-size: 14px; line-height: 1.7; white-space: pre-wrap; }

    /* Misc */
    .confirm-form { display: inline; }
    .divider { border: none; border-top: 1px solid var(--border-light); margin: 20px 0; }
    .text-muted { color: var(--text-muted); }

    /* Collapsible details */
    details[open] .pw-arrow { transform: rotate(90deg); }
    details summary::-webkit-details-marker { display: none; }
    details summary::marker { display: none; content: ''; }
    .text-sm { font-size: 13px; }
    .text-xs { font-size: 11px; }
    .mt-2 { margin-top: 8px; }
    .mt-4 { margin-top: 16px; }
    .mb-4 { margin-bottom: 16px; }

    /* Spinner */
    .spinner { display: none; width: 18px; height: 18px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.6s linear infinite; }
    .btn.loading .spinner { display: inline-block; }
    .btn.loading .btn-text { display: none; }
    @keyframes spin { to { transform: rotate(360deg); } }

    @keyframes slideDown { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes toastIn { from { opacity: 0; transform: translateX(80px); } to { opacity: 1; transform: translateX(0); } }
    @keyframes toastOut { from { opacity: 1; transform: translateX(0); } to { opacity: 0; transform: translateX(80px); } }
    @keyframes toastTimer { from { width: 100%; } to { width: 0%; } }

    /* Entrance animations */
    @keyframes fadeInUp {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @keyframes scaleIn {
      from { opacity: 0; transform: scale(0.95); }
      to { opacity: 1; transform: scale(1); }
    }

    /* Apply animations — fast & subtle */
    .container { animation: fadeIn 0.15s ease; }
    .page-header { animation: fadeInUp 0.2s ease both; }

    .stat-card {
      animation: scaleIn 0.2s ease both;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .stat-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); }

    .card {
      animation: fadeIn 0.2s ease both;
    }
    .card:hover { box-shadow: var(--shadow-md); }

    .badge { transition: transform 0.1s ease; }
    .badge:hover { transform: scale(1.05); }

    .btn {
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .seq-card {
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .seq-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); border-left-color: #8B5CF6; }

    /* Smooth focus glow */
    input:focus, textarea:focus, select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(99,102,241,0.12);
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }

    /* Sync spinner */
    .sync-spinner { width:32px;height:32px;border:3px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:spin .8s linear infinite;margin:0 auto; }
    @keyframes spin { to { transform:rotate(360deg); } }

    /* Progress bar animation */
    .progress-bar { transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1); }

    /* Hover lift for clickable cards/links */
    a.card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }

    /* Smooth row hover */
    tbody tr { transition: background 0.1s ease; }

    /* Scrollbar styling */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    @media (max-width: 1200px) {
      .container, .container.container-wide { padding: 24px 28px; }
      .nav { padding: 0 28px; }
    }
    @media (max-width: 900px) {
      .container, .container.container-wide { padding: 20px 20px; }
      .nav { padding: 0 16px; }
      .hero h1 { font-size: 36px; }
    }
    @media (max-width: 640px) {
      .container, .container.container-wide { padding: 16px; }
      .stats-grid { grid-template-columns: repeat(2, 1fr); }
      .form-row { grid-template-columns: 1fr; }
      .features { grid-template-columns: 1fr; }
      .hero { padding: 48px 16px 32px; }
      .hero h1 { font-size: 28px; letter-spacing: -1px; }
      .hero p { font-size: 15px; }
      .nav { padding: 0 12px; }
      .seq-card .seq-actions { opacity: 1; }
      .auth-card { padding: 28px 20px; }
    }
    /* Mobile hamburger */
    .hamburger { display: none; background: none; border: none; cursor: pointer; padding: 8px; color: #94A3B8; font-size: 22px; line-height: 1; z-index: 201; border-radius: 8px; transition: background .15s var(--ease), color .15s var(--ease); }
    .hamburger:hover { color: #F8FAFC; background: rgba(255,255,255,0.08); }
    .hamburger:focus-visible { outline: none; box-shadow: 0 0 0 2px rgba(129,140,248,0.55); }
    @media (max-width: 820px) {
      .nav { padding: 0 20px; backdrop-filter: none; }
      .hamburger { display: block; }
      .nav-links { display: none; position: fixed; top: 60px; left: 0; right: 0; bottom: 0; background: #0B1220; flex-direction: column; padding: 20px 20px; gap: 4px; overflow-y: auto; z-index: 200; border-top: 1px solid rgba(255,255,255,0.06); }
      .nav-links.open { display: flex; }
      .nav .nav-links a { font-size: 15px; padding: 12px 16px; border-radius: 9px; }
      .nav .nav-links a.active { background: rgba(255,255,255,0.09); }
      .nav-links .nav-divider { height: 1px; background: rgba(255,255,255,0.08); margin: 8px 0; width: 100%; }
      .nav-links .nav-user { font-size: 14px; padding: 12px 16px; }
      /* On mobile, dropdowns expand inline so every link is reachable by tap */
      .nav-dropdown { width: 100%; position: static; }
      .nav-dropdown::after { display: none; }
      .nav-dropdown > a { display: block; }
      .nav .nav-dropdown .nav-dropdown-menu { display: block; position: static; opacity: 1; visibility: visible; pointer-events: auto; transform: none; background: rgba(255,255,255,0.04); border: none; box-shadow: none; padding: 4px 0 8px 12px; margin-top: 0; min-width: 0; }
      .nav .nav-dropdown .nav-dropdown-menu a { font-size: 14px; padding: 10px 14px; }
      .toast-container { right: 12px; left: 12px; max-width: none; }
      table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
      thead, tbody, tr { display: table; width: 100%; table-layout: auto; }
      thead { display: table-header-group; }
      tbody { display: table-row-group; }
    }

    /* ─── Global animation system ─── */
    html { scroll-behavior: smooth; }
    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
      *, *::before, *::after { animation-duration: .001ms !important; animation-delay: 0ms !important; transition-duration: .001ms !important; }
    }

    /* Scroll-reveal base: elements start hidden, fade in when .in-view is set */
    .reveal { opacity: 0; transform: translateY(18px); transition: opacity .7s var(--ease), transform .7s var(--ease); will-change: opacity, transform; }
    .reveal.in-view { opacity: 1; transform: translateY(0); }
    .reveal-fade { opacity: 0; transition: opacity .7s var(--ease); }
    .reveal-fade.in-view { opacity: 1; }
    .reveal-scale { opacity: 0; transform: scale(.96); transition: opacity .7s var(--ease), transform .7s var(--ease); }
    .reveal-scale.in-view { opacity: 1; transform: scale(1); }
    .reveal-left { opacity: 0; transform: translateX(-22px); transition: opacity .7s var(--ease), transform .7s var(--ease); }
    .reveal-left.in-view { opacity: 1; transform: translateX(0); }
    .reveal-right { opacity: 0; transform: translateX(22px); transition: opacity .7s var(--ease), transform .7s var(--ease); }
    .reveal-right.in-view { opacity: 1; transform: translateX(0); }
    .r-delay-1 { transition-delay: .08s; }
    .r-delay-2 { transition-delay: .16s; }
    .r-delay-3 { transition-delay: .24s; }
    .r-delay-4 { transition-delay: .32s; }
    .r-delay-5 { transition-delay: .40s; }
    .r-delay-6 { transition-delay: .48s; }

    /* Shimmer skeleton for loading states */
    @keyframes mrShimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
    .skeleton { background: linear-gradient(90deg, var(--border-light) 0%, #eef0f4 50%, var(--border-light) 100%); background-size: 800px 100%; animation: mrShimmer 1.4s linear infinite; border-radius: 8px; color: transparent !important; pointer-events: none; user-select: none; }
    .skeleton-line { height: 12px; border-radius: 999px; margin: 8px 0; }
    .skeleton-block { height: 80px; border-radius: 12px; }

    /* Num pop (used when live stats update) */
    @keyframes numPop { 0% { transform: scale(1); } 30% { transform: scale(1.18); color: var(--primary); } 100% { transform: scale(1); } }
    .num-pop { animation: numPop .6s var(--ease); }

    /* Floating drift for decorative elements */
    @keyframes mrDrift { 0% { transform: translate3d(0,0,0); } 50% { transform: translate3d(0,-14px,0); } 100% { transform: translate3d(0,0,0); } }
    @keyframes mrDriftSlow { 0% { transform: translate3d(0,0,0) rotate(0deg); } 50% { transform: translate3d(8px,-10px,0) rotate(3deg); } 100% { transform: translate3d(0,0,0) rotate(0deg); } }
    .drift { animation: mrDrift 7s ease-in-out infinite; }
    .drift-slow { animation: mrDriftSlow 11s ease-in-out infinite; }

    /* Animated gradient mesh (used on hero) */
    @keyframes meshShift { 0% { transform: translate3d(0,0,0) scale(1); } 50% { transform: translate3d(2%,-3%,0) scale(1.06); } 100% { transform: translate3d(0,0,0) scale(1); } }
    .mesh-bg { position: absolute; inset: -40% -20%; z-index: 0; pointer-events: none; opacity: .55; filter: blur(70px); }
    .mesh-blob { position: absolute; width: 520px; height: 520px; border-radius: 50%; animation: meshShift 14s ease-in-out infinite; }
    .mesh-blob.b1 { background: radial-gradient(circle, rgba(99,102,241,.55), transparent 60%); top: -10%; left: 5%; }
    .mesh-blob.b2 { background: radial-gradient(circle, rgba(139,92,246,.45), transparent 60%); top: 10%; right: 5%; animation-duration: 18s; animation-delay: -4s; }
    .mesh-blob.b3 { background: radial-gradient(circle, rgba(236,72,153,.35), transparent 60%); bottom: -20%; left: 20%; animation-duration: 22s; animation-delay: -9s; }
    .mesh-blob.b4 { background: radial-gradient(circle, rgba(34,211,238,.35), transparent 60%); bottom: -10%; right: 25%; animation-duration: 20s; animation-delay: -11s; }
    :root[data-theme="dark"] .mesh-bg,
    :root[data-theme="mr-midnight"] .mesh-bg { opacity: .7; }

    /* Marquee for social-proof logos */
    @keyframes mrMarquee { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
    .marquee { overflow: hidden; mask-image: linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent); -webkit-mask-image: linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent); }
    .marquee-track { display: inline-flex; gap: 56px; animation: mrMarquee 38s linear infinite; white-space: nowrap; padding-right: 56px; }
    .marquee:hover .marquee-track { animation-play-state: paused; }

    /* Tilt card: subtle 3D on hover */
    .tilt-card { transition: transform .35s var(--ease), box-shadow .35s var(--ease), border-color .35s var(--ease); transform-style: preserve-3d; }
    .tilt-card:hover { transform: perspective(900px) rotateX(2deg) rotateY(-2deg) translateY(-4px); }

    /* Nav on scroll (collapses shadow + tightens height) */
    .nav.is-scrolled { box-shadow: 0 6px 24px rgba(0,0,0,0.25); border-bottom-color: rgba(255,255,255,0.1); }

    /* Animated underline for nav active state */
    .nav-links a.active::after { content: ''; position:absolute; left:12px; right:12px; bottom:-3px; height: 2px; background: linear-gradient(90deg, var(--primary), #8B5CF6); border-radius: 2px; animation: mrUnderlineIn .4s var(--ease); }
    @keyframes mrUnderlineIn { from { transform: scaleX(.2); opacity: 0; } to { transform: scaleX(1); opacity: 1; } }

    /* Spotlight hover: soft glow follows cursor on cards */
    .spotlight { position: relative; overflow: hidden; isolation: isolate; }
    .spotlight::before {
      content: ''; position: absolute; inset: 0; z-index: -1; pointer-events: none;
      background: radial-gradient(500px circle at var(--mx, 50%) var(--my, 50%), rgba(99,102,241,.10), transparent 40%);
      opacity: 0; transition: opacity .3s var(--ease);
    }
    .spotlight:hover::before { opacity: 1; }

    /* Command palette */
    .cmdk-overlay { position: fixed; inset: 0; background: rgba(2,6,23,.55); backdrop-filter: blur(8px); z-index: 9998; display: none; align-items: flex-start; justify-content: center; padding-top: 12vh; animation: mrFade .15s var(--ease); }
    .cmdk-overlay.open { display: flex; }
    @keyframes mrFade { from { opacity: 0; } to { opacity: 1; } }
    .cmdk-panel { width: 92%; max-width: 620px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow-lg); overflow: hidden; transform: translateY(-10px); animation: cmdkIn .25s var(--ease) forwards; }
    @keyframes cmdkIn { to { transform: translateY(0); } }
    .cmdk-input-wrap { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--border); }
    .cmdk-input-wrap input { border: none; outline: none; box-shadow: none; font-size: 15px; padding: 2px 0; margin: 0; background: transparent; color: var(--text); }
    .cmdk-input-wrap input:focus { box-shadow: none; }
    .cmdk-kbd { font-size: 11px; padding: 2px 6px; border: 1px solid var(--border); border-radius: 5px; color: var(--text-muted); font-family: inherit; background: var(--border-light); }
    .cmdk-list { max-height: 50vh; overflow: auto; padding: 8px; }
    .cmdk-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 9px; cursor: pointer; color: var(--text); font-size: 14px; transition: background .1s var(--ease); }
    .cmdk-item .cmdk-icon { width: 24px; text-align: center; font-size: 15px; opacity: .85; }
    .cmdk-item .cmdk-hint { margin-left: auto; color: var(--text-muted); font-size: 11.5px; }
    .cmdk-item:hover, .cmdk-item.selected { background: var(--primary-light); color: var(--primary-dark); }
    .cmdk-empty { padding: 22px; text-align: center; color: var(--text-muted); font-size: 13px; }
    .cmdk-section-title { font-size: 10.5px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); padding: 10px 12px 4px; font-weight: 700; }

    /* ─── Skeleton loaders ─── */
    @keyframes skShimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
    .skeleton { display: inline-block; background: linear-gradient(90deg, var(--border-light) 0%, rgba(148,163,184,.18) 50%, var(--border-light) 100%); background-size: 800px 100%; animation: skShimmer 1.4s linear infinite; border-radius: 6px; color: transparent !important; user-select: none; }
    .skeleton-line { display: block; height: 12px; margin: 8px 0; border-radius: 4px; }
    .skeleton-line.lg { height: 20px; }
    .skeleton-line.xl { height: 32px; }
    .skeleton-line.w-25 { width: 25%; }
    .skeleton-line.w-40 { width: 40%; }
    .skeleton-line.w-60 { width: 60%; }
    .skeleton-line.w-80 { width: 80%; }
    .skeleton-line.w-100 { width: 100%; }
    .skeleton-circle { display: inline-block; width: 40px; height: 40px; border-radius: 50%; }
    .skeleton-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
    .skeleton-stat { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }

    /* ─── Empty states ─── */
    .empty-state { text-align: center; padding: 56px 28px; max-width: 520px; margin: 32px auto; border: 1.5px dashed var(--border); border-radius: 16px; background: linear-gradient(180deg, var(--card), transparent 110%); position: relative; overflow: hidden; }
    .empty-state::before { content: ''; position: absolute; top: -40%; left: 50%; width: 200px; height: 200px; transform: translateX(-50%); background: radial-gradient(circle, rgba(99,102,241,.08), transparent 70%); pointer-events: none; }
    .empty-state .empty-icon { font-size: 44px; margin-bottom: 16px; display: inline-flex; width: 72px; height: 72px; align-items: center; justify-content: center; border-radius: 50%; background: var(--primary-light); color: var(--primary); position: relative; z-index: 1; }
    .empty-state h3 { font-size: 20px; font-weight: 700; margin: 0 0 8px; letter-spacing: -.3px; position: relative; z-index: 1; }
    .empty-state p { color: var(--text-secondary); font-size: 14.5px; line-height: 1.6; margin: 0 0 22px; position: relative; z-index: 1; }
    .empty-state .empty-actions { display: inline-flex; gap: 10px; flex-wrap: wrap; justify-content: center; position: relative; z-index: 1; }
    .empty-state .empty-actions a, .empty-state .empty-actions button { padding: 10px 18px; border-radius: 10px; font-weight: 600; font-size: 13.5px; text-decoration: none; transition: transform .2s var(--ease), box-shadow .2s var(--ease); border: none; cursor: pointer; }
    .empty-state .empty-actions a.primary, .empty-state .empty-actions button.primary { background: var(--primary); color: #fff; box-shadow: 0 1px 2px rgba(15,23,42,.12); }
    .empty-state .empty-actions a.primary:hover, .empty-state .empty-actions button.primary:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(99,102,241,.28); }
    .empty-state .empty-actions a.ghost, .empty-state .empty-actions button.ghost { background: transparent; color: var(--text); border: 1px solid var(--border); }
    .empty-state .empty-actions a.ghost:hover, .empty-state .empty-actions button.ghost:hover { background: var(--border-light); }
    .empty-state .empty-hint { margin-top: 16px; font-size: 12px; color: var(--text-muted); position: relative; z-index: 1; }
    .empty-state.compact { padding: 36px 22px; margin: 20px 0; }
    .empty-state.compact h3 { font-size: 17px; }
    .empty-state.compact p { font-size: 13.5px; }

    /* ─── Top progress bar ─── */
    #topbar-progress { position: fixed; top: 0; left: 0; right: 0; height: 2px; background: transparent; z-index: 10000; pointer-events: none; }
    #topbar-progress .bar { height: 100%; width: 0%; background: linear-gradient(90deg, #6366F1, #A78BFA, #F472B6); background-size: 200% 100%; transition: width .35s var(--ease), opacity .25s var(--ease); box-shadow: 0 0 10px rgba(124,58,237,.55), 0 0 4px rgba(99,102,241,.45); animation: topbarShimmer 2s linear infinite; }
    #topbar-progress.done .bar { opacity: 0; }
    @keyframes topbarShimmer { 0% { background-position: 0% 50%; } 100% { background-position: 200% 50%; } }

    /* Claude-style MachReach app skin. This sits after the legacy theme so the
       whole logged-in product shares the landing/dashboard visual language. */
    :root {
      --bg: #F4F1EA;
      --card: #FFFFFF;
      --card-bg: #FFFFFF;
      --text: #1A1A1F;
      --text-secondary: #5C5C66;
      --text-muted: #94939C;
      --border: #E2DCCC;
      --border-light: #EEE8DA;
      --primary: #5B4694;
      --primary-hover: #493777;
      --primary-light: #ECE6FB;
      --primary-dark: #3E2E69;
      --green: #2E9266;
      --green-light: #D7EDDF;
      --red: #E04A4A;
      --red-light: #FBDADA;
      --yellow: #F4B73A;
      --yellow-light: #FFF0C6;
      --radius: 18px;
      --radius-sm: 12px;
      --radius-xs: 10px;
      --shadow: 0 1px 0 rgba(20,18,30,.04), 0 2px 6px rgba(20,18,30,.04);
      --shadow-md: 0 2px 0 rgba(20,18,30,.04), 0 8px 22px rgba(20,18,30,.06);
      --shadow-lg: 0 6px 0 rgba(20,18,30,.05), 0 18px 44px rgba(20,18,30,.08);
    }
    body {
      font-family: "Plus Jakarta Sans", "Inter", system-ui, -apple-system, sans-serif;
      background: #F4F1EA !important;
      color: #1A1A1F;
      font-feature-settings: "ss01", "ss02";
    }
    .nav {
      height: 68px;
      padding: 0 32px;
      background: rgba(255,255,255,0.88) !important;
      border-bottom: 1px solid #E2DCCC !important;
      box-shadow: 0 1px 0 rgba(20,18,30,.03);
      backdrop-filter: blur(18px) saturate(150%);
    }
    .nav .brand {
      color: #1A1A1F !important;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .nav .brand-icon {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: #1A1A1F;
      color: #FFF8E1;
      box-shadow: inset 0 -3px 0 rgba(0,0,0,.22), 0 2px 0 rgba(20,18,30,.1);
    }
    .nav-links { gap: 4px; align-items: center; }
    .nav-links a,
    .nav-dropdown > a {
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      color: #5C5C66 !important;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 700;
      padding: 8px 11px;
      line-height: 1;
      vertical-align: middle;
    }
    .nav-links a:hover,
    .nav-dropdown:hover > a {
      color: #1A1A1F !important;
      background: #EDE7DA !important;
    }
    .nav-links a.active,
    .nav-dropdown > a.active {
      color: #5B4694 !important;
      background: #ECE6FB !important;
    }
    .nav-links a.active::after { display: none; }
    .nav-divider { height: 28px; background: #E2DCCC; opacity: 1; }
    .nav-user { color: #94939C !important; }
    .nav-dropdown::after { height: 18px; }
    .nav-dropdown-menu {
      top: calc(100% + 8px);
      background: #FFFFFF !important;
      border: 1px solid #E2DCCC !important;
      border-radius: 14px;
      padding: 8px;
      box-shadow: 0 18px 42px rgba(20,18,30,.12), 0 2px 0 rgba(20,18,30,.04) !important;
      min-width: 236px;
    }
    .nav-dropdown-menu a {
      display: flex;
      width: 100%;
      color: #5C5C66 !important;
      padding: 11px 12px;
      border-radius: 10px;
    }
    .nav-dropdown-menu a:hover {
      color: #1A1A1F !important;
      background: #F4F1EA !important;
    }
    .container {
      width: 100%;
      max-width: 1440px;
      margin: 0 auto;
      padding: 28px 32px 70px;
    }
    .container-wide { max-width: 1560px; }
    .card, .stat-card, .auth-card, .feature, .preview-content, .cmdk-panel,
    .empty-state, .skeleton-card, .skeleton-stat {
      background: #FFFFFF;
      border-color: #E2DCCC;
      border-radius: 18px;
      box-shadow: 0 1px 0 rgba(20,18,30,.04), 0 2px 6px rgba(20,18,30,.04);
    }
    .page-header h1, .card-header h2, h1, h2 { letter-spacing: -0.03em; }
    .page-header h1 {
      font-family: "Fraunces", Georgia, serif;
      font-size: clamp(30px, 3vw, 48px) !important;
      font-weight: 600;
      color: #1A1A1F;
    }
    .page-header p { color: #5C5C66; }
    input, textarea, select {
      background: #FBF8F0 !important;
      color: #1A1A1F !important;
      border: 1px solid #D8D0BE !important;
      border-radius: 12px !important;
      box-shadow: none !important;
    }
    input:focus, textarea:focus, select:focus {
      border-color: #5B4694 !important;
      box-shadow: 0 0 0 4px rgba(91,70,148,.14) !important;
    }
    .btn, button, input[type="submit"] { border-radius: 12px; font-weight: 800; }
    .btn-primary, button.primary, .empty-state .empty-actions a.primary,
    .empty-state .empty-actions button.primary {
      background: #1A1A1F !important;
      color: #FFF8E1 !important;
      border-color: #1A1A1F !important;
      box-shadow: 0 4px 0 rgba(0,0,0,.18), 0 10px 24px rgba(20,18,30,.12) !important;
    }
    .btn-primary:hover, button.primary:hover {
      transform: translateY(-1px);
      box-shadow: 0 5px 0 rgba(0,0,0,.18), 0 14px 30px rgba(20,18,30,.16) !important;
    }
    table { border-color: #E2DCCC; }
    th {
      color: #77756F;
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
      background: #FBF8F0;
    }
    td { border-bottom-color: #E2DCCC; }
    tbody tr:hover td { background: #FBF8F0; }
    .toast { border-radius: 14px; box-shadow: 0 18px 40px rgba(20,18,30,.14); }
    @media (max-width: 768px) {
      .nav { padding: 0 16px; height: 60px; }
      .container { padding: 22px 16px 54px; }
      .nav-links {
        background: #FFFFFF;
        border: 1px solid #E2DCCC;
        box-shadow: 0 18px 42px rgba(20,18,30,.12);
      }
      .nav .nav-dropdown .nav-dropdown-menu { background: #FBF8F0 !important; }
    }

    /* Exact Claude dashboard shell */
    .mr-app-shell.app {
      display: grid;
      grid-template-columns: 1fr;
      min-height: 100vh;
      background: #F4F1EA;
      color: #1A1A1F;
      font-family: "Plus Jakarta Sans", system-ui, -apple-system, sans-serif;
    }
    .mr-app-shell, .mr-app-shell button, .mr-app-shell input, .mr-app-shell textarea, .mr-app-shell select {
      font-family: "Plus Jakarta Sans", "Inter", system-ui, -apple-system, sans-serif;
    }
    .mr-app-shell h1, .mr-app-shell h2, .mr-app-shell .page-title-cd, .mr-app-shell .fc-page-title {
      font-family: "Fraunces", Georgia, serif;
      font-weight: 600;
      letter-spacing: -0.03em;
    }
    .mr-app-shell .side {
      background: #FFFFFF;
      border-right: 1px solid #E2DCCC;
      padding: 22px 16px;
      position: fixed;
      left: 0;
      top: 0;
      bottom: 0;
      width: 240px;
      height: 100dvh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      z-index: 2200;
    }
    .mr-app-shell .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 8px 22px;
      text-decoration: none;
      color: #1A1A1F;
    }
    .mr-app-shell .brand-mark {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: #1A1A1F;
      color: #FFF8E1;
      display: grid;
      place-items: center;
      font-family: "Fraunces", Georgia, serif;
      font-weight: 700;
      font-size: 22px;
      letter-spacing: -0.05em;
      box-shadow: inset 0 -3px 0 rgba(0,0,0,.25), 0 2px 0 rgba(20,18,30,.1);
      position: relative;
    }
    .mr-app-shell .brand-mark::after {
      content: "";
      position: absolute;
      right: 4px;
      top: 4px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #FF7A3D;
    }
    .mr-app-shell .brand-name { font-weight: 800; font-size: 17px; letter-spacing: -0.02em; }
    .mr-app-shell .brand-name span { color: #FF7A3D; }
    .mr-side-nav { display: flex; flex-direction: column; gap: 2px; }
    .mr-app-shell .nav-section {
      font-size: 10px;
      font-weight: 700;
      color: #94939C;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      padding: 14px 10px 6px;
    }
    .mr-app-shell .nav-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px;
      border-radius: 10px;
      font-weight: 600;
      font-size: 14px;
      color: #5C5C66;
      text-decoration: none;
      transition: background .15s, color .15s;
      position: relative;
      min-height: 40px;
    }
    .mr-app-shell .nav-item:hover { background: #EDE7DA; color: #1A1A1F; }
    .mr-app-shell .nav-item.active { background: #ECE6FB; color: #5B4694; }
    .mr-app-shell .nav-item.active::before {
      content: "";
      position: absolute;
      left: -16px;
      top: 8px;
      bottom: 8px;
      width: 3px;
      background: #5B4694;
      border-radius: 2px;
    }
    .mr-app-shell .nav-item .ic { width: 20px; height: 20px; flex-shrink: 0; display: inline-grid; place-items: center; }
    .mr-app-shell .side-foot { margin-top: auto; padding-top: 16px; border-top: 1px solid #E2DCCC; }
    .mr-app-shell .me-card {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px;
      border-radius: 12px;
      background: #FBF8F0;
      text-decoration: none;
      color: #1A1A1F;
    }
    .mr-app-shell .me-avatar {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: linear-gradient(135deg, #FFB199, #FF6CAB);
      color: #fff;
      font-weight: 800;
      font-size: 14px;
      display: grid;
      place-items: center;
    }
    .mr-app-shell .me-info { font-size: 13px; line-height: 1.2; min-width: 0; flex: 1; }
    .mr-app-shell .me-name { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .mr-app-shell .me-meta { font-size: 11px; color: #94939C; display: flex; align-items: center; gap: 6px; margin-top: 2px; }
    .mr-app-shell .main { min-width: 0; display: flex; flex-direction: column; margin-left: 240px; }
    .mr-app-shell .topbar {
      position: sticky;
      top: 0;
      z-index: 2100;
      background: rgba(244,241,234,0.85);
      backdrop-filter: saturate(140%) blur(12px);
      border-bottom: 1px solid #E2DCCC;
      padding: 12px 28px;
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .mr-app-shell .greet { font-weight: 700; font-size: 17px; letter-spacing: -0.01em; }
    .mr-app-shell .greet .em { color: #FF7A3D; }
    .mr-app-shell .greet small { display: block; font-size: 12px; color: #94939C; font-weight: 500; margin-top: 1px; }
    .mr-app-shell .topbar-stats { margin-left: auto; display: flex; align-items: center; gap: 8px; }
    .mr-app-shell .stat-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      background: #FFFFFF;
      border: 1px solid #E2DCCC;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      color: #1A1A1F;
      text-decoration: none;
      transition: transform .15s, box-shadow .15s;
    }
    .mr-app-shell .stat-pill:hover { transform: translateY(-1px); box-shadow: 0 1px 0 rgba(20,18,30,.04), 0 2px 6px rgba(20,18,30,.04); }
    .mr-app-shell .stat-pill.streak { background: linear-gradient(135deg, #FFE9D6, #FFD1A4); border-color: #F8B97A; color: #863500; }
    .mr-app-shell .stat-pill.coins { background: #FFF6D6; border-color: #F4B73A; color: #C98A0E; }
    .mr-app-shell .xp-pill {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 5px 14px 5px 5px;
      background: #FFFFFF;
      border: 1px solid #E2DCCC;
      border-radius: 999px;
      color: #1A1A1F;
      text-decoration: none;
    }
    .mr-app-shell .xp-ring { width: 30px; height: 30px; position: relative; display: inline-block; }
    .mr-app-shell .xp-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
    .mr-app-shell .xp-ring .ring-bg { stroke: #E2DCCC; }
    .mr-app-shell .xp-ring .ring-fg { stroke: #5B4694; transition: stroke-dashoffset 1s ease; }
    .mr-app-shell .xp-ring .lvl { position: absolute; inset: 0; display: grid; place-items: center; font-size: 10px; font-weight: 800; color: #5B4694; }
    .mr-app-shell .xp-meta { line-height: 1.1; display: flex; flex-direction: column; }
    .mr-app-shell .league-name { font-size: 13px; font-weight: 700; }
    .mr-app-shell .xp-num { font-size: 10px; color: #94939C; font-variant-numeric: tabular-nums; }
    .mr-app-shell .top-icon-btn {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: #FFFFFF;
      border: 1px solid #E2DCCC;
      display: grid;
      place-items: center;
      color: #1A1A1F;
      text-decoration: none;
      font-size: 12px;
      font-weight: 800;
    }
    .mr-app-shell .top-icon-btn:hover { background: #EDE7DA; }
    :root[data-theme="dark"] .mr-app-shell.app {
      background: #11131A;
      color: #F7F0E4;
    }
    :root[data-theme="dark"] .mr-app-shell .side,
    :root[data-theme="dark"] .mr-app-shell .topbar {
      background: rgba(24,26,36,0.92);
      border-color: #34313A;
    }
    :root[data-theme="dark"] .mr-app-shell .content {
      background: #11131A;
    }
    :root[data-theme="dark"] .mr-app-shell .brand,
    :root[data-theme="dark"] .mr-app-shell .nav-item,
    :root[data-theme="dark"] .mr-app-shell .greet,
    :root[data-theme="dark"] .mr-app-shell .stat-pill,
    :root[data-theme="dark"] .mr-app-shell .xp-pill,
    :root[data-theme="dark"] .mr-app-shell .top-icon-btn,
    :root[data-theme="dark"] .mr-app-shell .me-card {
      color: #F7F0E4;
    }
    :root[data-theme="dark"] .mr-app-shell .nav-item:hover,
    :root[data-theme="dark"] .mr-app-shell .nav-item.active,
    :root[data-theme="dark"] .mr-app-shell .top-icon-btn:hover {
      background: #2A2630;
      color: #FFB17C;
    }
    :root[data-theme="dark"] .mr-app-shell .nav-item.active::before {
      background: #FF7A3D;
    }
    :root[data-theme="dark"] .mr-app-shell .card,
    :root[data-theme="dark"] .mr-app-shell .admin-metric,
    :root[data-theme="dark"] .mr-app-shell .admin-panel,
    :root[data-theme="dark"] .mr-app-shell .me-card,
    :root[data-theme="dark"] .mr-app-shell .stat-pill,
    :root[data-theme="dark"] .mr-app-shell .xp-pill,
    :root[data-theme="dark"] .mr-app-shell .top-icon-btn {
      background: #1D202A;
      border-color: #34313A;
    }
    :root[data-theme="dark"] .mr-app-shell input,
    :root[data-theme="dark"] .mr-app-shell textarea,
    :root[data-theme="dark"] .mr-app-shell select {
      background: #141720 !important;
      color: #F7F0E4 !important;
      border-color: #3B3741 !important;
    }
    /* Warm Claude theme cleanup for older inline student pages. Several
       legacy widgets carried navy cards into the new paper/orange product. */
    :root:not([data-theme="dark"]) .pl-wrap {
      --card:#FFFFFF; --bg:#F4F1EA; --text:#1A1A1F; --text-muted:#77756F; --border:#E2DCCC; --border-light:#EEE8DA; --primary:#FF7A3D;
    }
    :root:not([data-theme="dark"]) .pl-card,
    :root:not([data-theme="dark"]) .pl-course,
    :root:not([data-theme="dark"]) .pl-help,
    :root:not([data-theme="dark"]) .pl-empty {
      background:#FFFFFF !important; color:#1A1A1F !important; border-color:#E2DCCC !important;
      box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04) !important;
    }
    :root:not([data-theme="dark"]) .pl-btn.primary,
    :root:not([data-theme="dark"]) .pl-tab.active,
    :root:not([data-theme="dark"]) .pl-add-course:hover {
      border-color:#FF7A3D !important; color:#FF7A3D !important;
    }
    :root:not([data-theme="dark"]) .pl-btn.primary {
      background:#1A1A1F !important; color:#FFF8E1 !important; border-color:#1A1A1F !important;
    }
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="background:#0B1220"] {
      background:#FFFFFF !important; border-color:#E2DCCC !important; box-shadow:0 6px 0 rgba(20,18,30,.05),0 18px 44px rgba(20,18,30,.08) !important;
    }
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="border:3px solid #0B1220"] {
      border-color:#FFFFFF !important;
    }
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="color:#fff"],
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="color:#fff;"],
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="color:#fff;font"] {
      color:#1A1A1F !important;
    }
    :root:not([data-theme="dark"]) .student-profile-wrap .me-avatar,
    :root:not([data-theme="dark"]) .student-profile-wrap [style*="background:linear-gradient(135deg,#3B4A7A,#5B4694)"] {
      color:#FFFFFF !important;
    }
    :root:not([data-theme="dark"]) .shop-cd [style*="background:#0f172a"],
    :root:not([data-theme="dark"]) .shop-cd [style*="background:#374151"],
    :root:not([data-theme="dark"]) .shop-cd [style*="background:#1e293b"],
    :root:not([data-theme="dark"]) .shop-cd [style*="background:#111827"] {
      background:#FBF8F0 !important; color:#1A1A1F !important; border:1px solid #E2DCCC !important;
    }
    :root:not([data-theme="dark"]) .shop-cd [style*="color:#334155"],
    :root:not([data-theme="dark"]) .shop-cd [style*="color:#94a3b8"],
    :root:not([data-theme="dark"]) .shop-cd [style*="color:#64748b"] {
      color:#77756F !important;
    }
    :root:not([data-theme="dark"]) .shop-cd .stat-card,
    :root:not([data-theme="dark"]) .student-profile-wrap .stat-card {
      background:#FFFFFF !important; border-color:#E2DCCC !important; color:#1A1A1F !important;
    }
    .mr-app-shell .content {
      padding: 28px 28px 80px;
      max-width: 1400px;
      width: 100%;
      margin: 0;
    }
    .mr-app-shell .content-wide { max-width: 1560px; }
    .mr-mobile-menu { display: none; background: #FFFFFF; border: 1px solid #E2DCCC; border-radius: 10px; width: 38px; height: 38px; }
    @media (max-width: 1100px) {
      .mr-app-shell.app { grid-template-columns: 1fr; }
      .mr-app-shell .side { padding: 22px 10px; width: 80px; }
      .mr-app-shell .main { margin-left: 80px; }
      .mr-app-shell .brand-name, .mr-app-shell .nav-item span:not(.ic), .mr-app-shell .nav-section, .mr-app-shell .me-info { display: none; }
      .mr-app-shell .nav-item { justify-content: center; }
      .mr-app-shell .me-card { justify-content: center; padding: 4px; }
      .mr-app-shell .nav-item.active::before { left: -10px; }
    }
    @media (max-width: 720px) {
      .mr-app-shell.app { grid-template-columns: 1fr; }
      .mr-app-shell .main { margin-left: 0; }
      .mr-app-shell .side {
        position: fixed;
        left: 0;
        top: 0;
        transform: translateX(-100%);
        transition: transform .2s ease;
        width: 260px;
      }
      .mr-app-shell.side-open .side { transform: translateX(0); }
      .mr-mobile-menu { display: grid; place-items: center; }
      .mr-app-shell .topbar-stats .stat-pill { display: none; }
      .mr-app-shell .topbar-stats .xp-meta { display: none; }
      .mr-app-shell .greet { font-size: 14px; }
      .mr-app-shell .greet small { display: none; }
      .mr-app-shell .content { padding: 18px 16px 100px; }
    }
  </style>
</head>
<body>
  <div id="topbar-progress"><div class="bar"></div></div>
  <script>
    window.__IS_LOGGED_IN__ = {% if logged_in %}true{% else %}false{% endif %};
    window.__ACCOUNT_TYPE__ = "{{ account_type|default('student') }}";
    // Top progress bar controller
    (function(){
      var tp = null, bar = null, timer = null, progress = 0;
      function init(){ tp = document.getElementById('topbar-progress'); bar = tp && tp.querySelector('.bar'); }
      if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
      window.topbarStart = function(){
        if (!bar) init();
        if (!bar) return;
        progress = 8; tp.classList.remove('done'); bar.style.width = '8%';
        clearInterval(timer);
        timer = setInterval(function(){
          // Asymptotic approach to 90%
          progress += (92 - progress) * 0.08;
          if (bar) bar.style.width = progress.toFixed(1) + '%';
          if (progress > 91.5) clearInterval(timer);
        }, 220);
      };
      window.topbarDone = function(){
        if (!bar) return;
        clearInterval(timer);
        bar.style.width = '100%';
        setTimeout(function(){ tp.classList.add('done'); setTimeout(function(){ bar.style.width = '0%'; }, 260); }, 180);
      };
      // Trigger on link clicks (same-origin, non-modifier)
      document.addEventListener('click', function(e){
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        var href = a.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) return;
        if (a.target === '_blank') return;
        try { var u = new URL(a.href, location.href); if (u.origin !== location.origin) return; } catch(_) {}
        window.topbarStart();
      }, true);
      // Trigger on form submissions
      document.addEventListener('submit', function(){ window.topbarStart(); }, true);
      // Complete on pageshow (also handles back-forward cache)
      window.addEventListener('pageshow', function(){ window.topbarDone(); });
      // Close any open nav dropdown when clicking outside or pressing Escape.
      document.addEventListener('click', function(e){
        document.querySelectorAll('.nav-dropdown.open').forEach(function(d){
          if (!d.contains(e.target)) d.classList.remove('open');
        });
      });
      document.addEventListener('keydown', function(e){
        if (e.key === 'Escape') document.querySelectorAll('.nav-dropdown.open').forEach(function(d){ d.classList.remove('open'); });
      });
    })();
  </script>
  {% if logged_in and account_type|default('student') == 'student' %}
  <div class="mr-app-shell app">
    <aside class="side">
      <a href="/student" class="brand">
        <div class="brand-mark">M</div>
        <div class="brand-name">Mach<span>Reach</span></div>
      </a>
      <nav class="mr-side-nav">
        <div class="nav-section">{{ student_ui.main }}</div>
        <a class="nav-item {% if active_page == 'student_dashboard' %}active{% endif %}" href="/student"><span class="ic">&#127891;</span><span>{{ student_ui.home }}</span></a>
        {% if is_admin %}<a class="nav-item {% if active_page == 'admin' %}active{% endif %}" href="/admin"><span class="ic">&#128227;</span><span>{{ student_ui.admin }}</span></a>{% endif %}
        <a class="nav-item {% if active_page == 'student_focus' %}active{% endif %}" href="/student/focus"><span class="ic">&#127919;</span><span>{{ student_ui.focus }}</span></a>
        <a class="nav-item {% if active_page == 'student_courses' %}active{% endif %}" href="/student/courses"><span class="ic">&#128218;</span><span>{{ student_ui.courses }}</span></a>

        <div class="nav-section">{{ student_ui.study }}</div>
        <a class="nav-item {% if active_page == 'student_quizzes' %}active{% endif %}" href="/student/quizzes"><span class="ic">&#128221;</span><span>{{ student_ui.quizzes }}</span></a>
        <a class="nav-item {% if active_page == 'student_flashcards' %}active{% endif %}" href="/student/flashcards"><span class="ic">&#127183;</span><span>{{ student_ui.flashcards }}</span></a>
        <a class="nav-item {% if active_page == 'student_essay' %}active{% endif %}" href="/student/essay"><span class="ic">&#9999;</span><span>{{ student_ui.essays }}</span></a>

        <div class="nav-section">{{ student_ui.community }}</div>
        <a class="nav-item {% if active_page == 'student_leaderboard' %}active{% endif %}" href="/student/leaderboard"><span class="ic">&#127942;</span><span>{{ student_ui.leaderboard }}</span></a>
        <a class="nav-item {% if active_page == 'student_friends' %}active{% endif %}" href="/student/friends"><span class="ic">&#128101;</span><span>{{ student_ui.friends }}</span></a>
        <a class="nav-item {% if active_page == 'student_marketplace' %}active{% endif %}" href="/student/marketplace"><span class="ic">&#128722;</span><span>{{ student_ui.marketplace }}</span></a>
        <a class="nav-item {% if active_page == 'student_shop' %}active{% endif %}" href="/student/shop"><span class="ic">&#129534;</span><span>{{ student_ui.shop }}</span></a>

        <div class="nav-section">{{ student_ui.account }}</div>
        <a class="nav-item {% if active_page == 'student_gpa' %}active{% endif %}" href="/student/gpa"><span class="ic">&#128200;</span><span>{{ student_ui.grades }}</span></a>
        <a class="nav-item {% if active_page == 'student_achievements' %}active{% endif %}" href="/student/achievements"><span class="ic">&#127941;</span><span>{{ student_ui.xp }}</span></a>
        <a class="nav-item {% if active_page == 'student_settings' %}active{% endif %}" href="/student/settings"><span class="ic">&#9881;</span><span>{{ student_ui.settings }}</span></a>
      </nav>
      <div class="side-foot">
        <a class="me-card" href="/student/profile">
          <div class="me-avatar">{{ (client_name[:1] or 'M')|upper }}</div>
          <div class="me-info">
            <div class="me-name">{{client_name}}</div>
            <div class="me-meta"><span class="league-crest">&#127942;</span> MachReach</div>
          </div>
        </a>
      </div>
    </aside>

    <main class="main">
      <div class="topbar">
        <button class="mr-mobile-menu" onclick="document.querySelector('.mr-app-shell').classList.toggle('side-open')" aria-label="Menu">&#9776;</button>
        <div class="greet">
          {% set first_name = (client_name.split()[0] if client_name else student_ui.student_fallback) %}
          {{ student_ui.greeting|replace('{name}', first_name)|safe }}
          <small>{{ student_ui.ready }}</small>
        </div>
        <div class="topbar-stats">
          <a class="stat-pill coins" href="/student/shop">&#129689; <span class="num">Coins</span></a>
          <a class="stat-pill streak" href="/student/analytics">&#128293; <span class="num">Racha 🔥</span></a>
          <a class="xp-pill" href="/student/achievements">
            <span class="xp-ring"><svg viewBox="0 0 36 36"><circle class="ring-bg" cx="18" cy="18" r="15" fill="none" stroke-width="4"/><circle class="ring-fg" cx="18" cy="18" r="15" fill="none" stroke-width="4" stroke-dasharray="94" stroke-dashoffset="34"/></svg><span class="lvl">XP</span></span>
            <span class="xp-meta"><span class="league-name">{{ student_ui.active_league }}</span><span class="xp-num">{{ student_ui.keep_climbing }}</span></span>
          </a>
          <button id="theme-toggle" class="top-icon-btn" type="button" onclick="toggleDarkMode()" title="{{ student_ui.toggle_theme }}">&#127769;</button>
          <a class="top-icon-btn" href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
          <a class="top-icon-btn" href="/logout" title="{{nav.logout}}">&#10162;</a>
        </div>
      </div>
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
      </div>
    </main>
  </div>
  {% else %}
  <div class="nav">
    <a href="/" class="brand">
      <div class="brand-icon">&#9993;</div>
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
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_flashcards','student_quizzes','student_essay'] %}class="active"{% endif %}>&#128218; Herramientas de Estudio &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/flashcards">&#127183; Tarjetas</a>
            <a href="/student/quizzes">&#128221; Quizzes</a>
            <a href="/student/essay">&#9999;&#65039; Ensayos</a>
          </div>
        </div>
        <a href="/student/focus" {% if active_page == 'student_focus' %}class="active"{% endif %}>&#127919; Enfoque</a>
        <a href="/student/marketplace" {% if active_page == 'student_marketplace' %}class="active"{% endif %}>&#128722; Mercado</a>
        <div class="nav-divider"></div>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_gpa','student_achievements','student_friends','student_shop'] %}class="active"{% endif %}>Más &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/friends">&#128101; Amigos</a>
            <a href="/student/shop">&#129534; Tienda</a>
            <a href="/student/gpa">&#128200; Planilla de Notas</a>
            <a href="/student/achievements">&#127942; XP e Insignias</a>
          </div>
        </div>
        <a href="/student/leaderboard" {% if active_page == 'student_leaderboard' %}class="active"{% endif %}>&#127942; Ranking</a>
        {% else %}
        <a href="/student" {% if active_page == 'student_dashboard' %}class="active"{% endif %}>&#127891; Dashboard</a>
        {% if is_admin %}<a href="/admin" {% if active_page == 'admin' %}class="active"{% endif %} style="color:var(--yellow);">&#128227; Admin</a>{% endif %}
        <a href="/student/courses" {% if active_page == 'student_courses' %}class="active"{% endif %}>&#128218; Courses</a>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_flashcards','student_quizzes','student_essay'] %}class="active"{% endif %}>&#128218; Study Tools &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/flashcards">&#127183; Flashcards</a>
            <a href="/student/quizzes">&#128221; Quizzes</a>
            <a href="/student/essay">&#9999;&#65039; Essay</a>
          </div>
        </div>
        <a href="/student/focus" {% if active_page == 'student_focus' %}class="active"{% endif %}>&#127919; Focus</a>
        <a href="/student/marketplace" {% if active_page == 'student_marketplace' %}class="active"{% endif %}>&#128722; Marketplace</a>
        <div class="nav-divider"></div>
        <div class="nav-dropdown">
          <a href="javascript:void(0)" {% if active_page in ['student_gpa','student_achievements','student_friends','student_shop'] %}class="active"{% endif %}>More &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/friends">&#128101; Friends</a>
            <a href="/student/shop">&#129534; Shop</a>
            <a href="/student/gpa">&#128200; Grade Sheet</a>
            <a href="/student/achievements">&#127942; XP &amp; Badges</a>
          </div>
        </div>
        <a href="/student/leaderboard" {% if active_page == 'student_leaderboard' %}class="active"{% endif %}>&#127942; Leaderboard</a>
        {% endif %}
        <a href="/student/settings" {% if active_page == 'student_settings' %}class="active"{% endif %}>&#9881;</a>
        {% endif %}
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <div class="nav-divider"></div>
        <a href="/student/profile" class="nav-user" style="text-decoration:none;cursor:pointer;color:#94A3B8;" title="My profile">{{client_name}}</a>
        <a href="/logout" style="color:#EF4444;">{{nav.logout}}</a>
      {% else %}
        <a href="/login">{{nav.login}}</a>
        <a href="/register" class="btn btn-primary btn-sm" style="color:#fff;">{{nav.get_started}}</a>
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
  <script>
    (function(){
      // 1) Scroll-reveal observer: any element with [.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right]
      if ('IntersectionObserver' in window) {
        var io = new IntersectionObserver(function(entries){
          entries.forEach(function(e){
            if (e.isIntersecting) {
              e.target.classList.add('in-view');
              // Trigger count-up if element has data-count
              var el = e.target;
              if (el.dataset && el.dataset.count && !el.dataset.countDone) {
                el.dataset.countDone = '1';
                var target = parseFloat(el.dataset.count);
                var suffix = el.dataset.countSuffix || '';
                var prefix = el.dataset.countPrefix || '';
                var duration = parseInt(el.dataset.countDuration || '1500', 10);
                var decimals = parseInt(el.dataset.countDecimals || '0', 10);
                var start = performance.now();
                function step(now) {
                  var p = Math.min(1, (now - start) / duration);
                  var eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
                  var val = target * eased;
                  el.textContent = prefix + val.toFixed(decimals) + suffix;
                  if (p < 1) requestAnimationFrame(step);
                  else el.textContent = prefix + target.toFixed(decimals) + suffix;
                }
                requestAnimationFrame(step);
              }
              io.unobserve(e.target);
            }
          });
        }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
        document.querySelectorAll('.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right, [data-count]').forEach(function(el){ io.observe(el); });
      } else {
        // Fallback: reveal everything instantly
        document.querySelectorAll('.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right').forEach(function(el){ el.classList.add('in-view'); });
      }

      // 2) Nav scroll state
      var nav = document.querySelector('.nav');
      function onScroll() {
        if (!nav) return;
        if (window.scrollY > 8) nav.classList.add('is-scrolled'); else nav.classList.remove('is-scrolled');
      }
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();

      // 3) Spotlight cursor tracking on .spotlight cards
      document.addEventListener('mousemove', function(e){
        var t = e.target.closest && e.target.closest('.spotlight');
        if (!t) return;
        var r = t.getBoundingClientRect();
        t.style.setProperty('--mx', (e.clientX - r.left) + 'px');
        t.style.setProperty('--my', (e.clientY - r.top) + 'px');
      });

      // 4) Command palette (Cmd+K / Ctrl+K)
      var CMDK_ITEMS = window.__IS_LOGGED_IN__ ? [
        {t:'Student Dashboard', u:'/student', i:'🎓', s:'Main'},
        {t:'Courses', u:'/student/courses', i:'📚', s:'Main'},
        {t:'Study Plan', u:'/student/plan', i:'📅', s:'Main'},
        {t:'Flashcards', u:'/student/flashcards', i:'📇', s:'Study'},
        {t:'Quizzes', u:'/student/quizzes', i:'📝', s:'Study'},
        {t:'Notes', u:'/student/notes', i:'📖', s:'Study'},
        {t:'Essay Assistant', u:'/student/essay', i:'\u270F\uFE0F', s:'Study'},
        {t:'Focus Mode', u:'/student/focus', i:'🎯', s:'Tools'},
        {t:'Panic Mode', u:'/student/panic', i:'🚨', s:'Tools'},
        {t:'Grade Sheet', u:'/student/gpa', i:'📈', s:'Tools'},
        {t:'Leaderboard', u:'/student/leaderboard', i:'🏆', s:'Social'},
        {t:'Marketplace', u:'/student/marketplace', i:'🛒', s:'Social'},
        {t:'Settings', u:'/student/settings', i:'\u2699\uFE0F', s:'Other'},
        {t:'Log out', u:'/logout', i:'🚪', s:'Other'},
      ] : [
        {t:'Home', u:'/', i:'🏠', s:'Public'},
        {t:'Log in', u:'/login', i:'🔑', s:'Public'},
        {t:'Sign up', u:'/register', i:'\u2728', s:'Public'},
      ]);

      function buildCmdK() {
        if (document.getElementById('cmdk-overlay')) return;
        var o = document.createElement('div');
        o.id = 'cmdk-overlay';
        o.className = 'cmdk-overlay';
        o.innerHTML =
          '<div class="cmdk-panel" role="dialog" aria-label="Command palette">'
          + '<div class="cmdk-input-wrap">'
          + '<span style="color:var(--text-muted);">🔍</span>'
          + '<input id="cmdk-input" type="text" placeholder="Jump to a page or feature…" autocomplete="off" />'
          + '<span class="cmdk-kbd">ESC</span>'
          + '</div>'
          + '<div id="cmdk-list" class="cmdk-list"></div>'
          + '</div>';
        document.body.appendChild(o);
        o.addEventListener('click', function(e){ if (e.target === o) closeCmdK(); });
        var input = o.querySelector('#cmdk-input');
        input.addEventListener('input', function(){ renderCmdK(input.value); });
        input.addEventListener('keydown', function(e){
          var items = o.querySelectorAll('.cmdk-item');
          var sel = o.querySelector('.cmdk-item.selected');
          var idx = Array.prototype.indexOf.call(items, sel);
          if (e.key === 'ArrowDown') { e.preventDefault(); var next = items[(idx+1+items.length)%items.length]; if (sel) sel.classList.remove('selected'); if (next) { next.classList.add('selected'); next.scrollIntoView({block:'nearest'}); } }
          else if (e.key === 'ArrowUp') { e.preventDefault(); var prev = items[(idx-1+items.length)%items.length]; if (sel) sel.classList.remove('selected'); if (prev) { prev.classList.add('selected'); prev.scrollIntoView({block:'nearest'}); } }
          else if (e.key === 'Enter') { e.preventDefault(); if (sel) window.location.href = sel.dataset.url; }
          else if (e.key === 'Escape') { e.preventDefault(); closeCmdK(); }
        });
      }
      function renderCmdK(q) {
        q = (q||'').trim().toLowerCase();
        var list = document.getElementById('cmdk-list');
        if (!list) return;
        var matches = CMDK_ITEMS.filter(function(it){ return !q || it.t.toLowerCase().indexOf(q) !== -1 || (it.s||'').toLowerCase().indexOf(q) !== -1; });
        if (!matches.length) { list.innerHTML = '<div class="cmdk-empty">No matches. Try a different keyword.</div>'; return; }
        var groups = {};
        matches.forEach(function(it){ (groups[it.s]=groups[it.s]||[]).push(it); });
        var html = '';
        Object.keys(groups).forEach(function(sec){
          html += '<div class="cmdk-section-title">' + sec + '</div>';
          groups[sec].forEach(function(it){
            html += '<div class="cmdk-item" data-url="' + it.u + '">'
              + '<span class="cmdk-icon">' + it.i + '</span>'
              + '<span>' + it.t + '</span>'
              + '<span class="cmdk-hint">\u21B5</span>'
              + '</div>';
          });
        });
        list.innerHTML = html;
        var first = list.querySelector('.cmdk-item');
        if (first) first.classList.add('selected');
        list.querySelectorAll('.cmdk-item').forEach(function(it){
          it.addEventListener('click', function(){ window.location.href = it.dataset.url; });
          it.addEventListener('mouseenter', function(){
            list.querySelectorAll('.cmdk-item').forEach(function(x){ x.classList.remove('selected'); });
            it.classList.add('selected');
          });
        });
      }
      function openCmdK() {
        buildCmdK();
        var o = document.getElementById('cmdk-overlay');
        o.classList.add('open');
        var input = document.getElementById('cmdk-input');
        if (input) { input.value = ''; input.focus(); }
        renderCmdK('');
      }
      function closeCmdK() {
        var o = document.getElementById('cmdk-overlay');
        if (o) o.classList.remove('open');
      }
      window.openCmdK = openCmdK;
      window.closeCmdK = closeCmdK;
      document.addEventListener('keydown', function(e){
        if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
          // Don't hijack if user is typing in another input
          e.preventDefault();
          var o = document.getElementById('cmdk-overlay');
          if (o && o.classList.contains('open')) closeCmdK(); else openCmdK();
        }
      });
    })();
  </script>

  <!-- Cookie Consent Banner (GDPR) -->
  <div id="cookie-consent" style="display:none;position:fixed;bottom:0;left:0;right:0;z-index:9999;background:var(--card);border-top:1px solid var(--border-light);box-shadow:0 -2px 16px rgba(0,0,0,.12);padding:16px 24px;font-size:13px;color:var(--text-secondary);">
    <div style="max-width:960px;margin:0 auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <p style="flex:1;margin:0;min-width:200px;">Usamos cookies esenciales para mantener tu sesión y recordar tus preferencias. Sin cookies de tracking ni publicidad. <a href="/privacy" style="color:var(--primary);text-decoration:underline;">Privacy Policy</a></p>
      <button onclick="acceptCookies()" class="btn btn-primary btn-sm">Aceptar</button>
    </div>
  </div>
  <script>
  (function(){
    if(!document.cookie.match(/(?:^|;\\s*)cookie_consent=1/)){
      var el=document.getElementById('cookie-consent');
      if(el) el.style.display='block';
    }
  })();
  function acceptCookies(){
    document.cookie='cookie_consent=1;path=/;max-age=31536000;SameSite=Lax';
    var el=document.getElementById('cookie-consent');
    if(el) el.style.display='none';
  }
  </script>

  <!-- Floating focus timer widget (persists across pages) -->
  <div id="focus-float" onclick="window.location='/student/focus'">
    <span class="ff-close" onclick="event.stopPropagation();closeFocusFloat();">&times;</span>
    <div class="ff-time" id="ff-time">--:--</div>
    <div class="ff-label" id="ff-label">Focus</div>
  </div>

  <!-- Persistent silent <audio> element used for keepalive (prevents Chrome
       from throttling/freezing this tab while a focus session is running on
       another MachReach tab). The actual src is set programmatically. -->
  <audio id="focus-keepalive" loop preload="auto" style="display:none"></audio>
  <audio id="focus-alarm" preload="auto" style="display:none"></audio>

  <script>
  /* ============================================================
   * GLOBAL FOCUS CONTROLLER
   * Runs on EVERY page so that when the user navigates away from
   * /student/focus the timer keeps ticking, the alarm fires, XP
   * gets credited, and pomodoro phases auto-advance — even though
   * the focus page itself has been unloaded.
   * ============================================================ */
  (function(){
    if (window.__focusGlobalCtrl) return; // singleton
    window.__focusGlobalCtrl = true;

    var keepEl = document.getElementById('focus-keepalive');
    var alarmEl = document.getElementById('focus-alarm');
    var widget = document.getElementById('focus-float');
    if (!widget) return;

    var alarmDataUri = null;
    var silenceDataUri = null;

    function buildSilenceWavDataUri(){
      var sr = 8000, n = sr;
      var buf = new ArrayBuffer(44 + n*2);
      var v = new DataView(buf);
      function w(o,s){ for(var i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); }
      w(0,'RIFF'); v.setUint32(4, 36+n*2, true);
      w(8,'WAVEfmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
      v.setUint16(22,1,true); v.setUint32(24,sr,true);
      v.setUint32(28,sr*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
      w(36,'data'); v.setUint32(40, n*2, true);
      var b = new Uint8Array(buf), s = '';
      for (var j=0; j<b.length; j++) s += String.fromCharCode(b[j]);
      return 'data:audio/wav;base64,' + btoa(s);
    }
    function buildAlarmWavDataUri(){
      var sr = 22050, dur = 1.4, n = Math.floor(sr*dur);
      var buf = new ArrayBuffer(44 + n*2);
      var v = new DataView(buf);
      function w(o,s){ for(var i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); }
      w(0,'RIFF'); v.setUint32(4, 36+n*2, true);
      w(8,'WAVEfmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
      v.setUint16(22,1,true); v.setUint32(24,sr,true);
      v.setUint32(28,sr*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
      w(36,'data'); v.setUint32(40, n*2, true);
      var freqs = [523.25, 659.25, 783.99];
      for (var i=0; i<n; i++){
        var t = i/sr, sa = 0;
        for (var k=0;k<3;k++){
          var st = k*0.35;
          if (t>=st && t<st+0.6){
            var lo = t-st, env = Math.exp(-lo*5);
            sa += Math.sin(2*Math.PI*freqs[k]*lo)*env*0.3;
          }
        }
        var val = Math.max(-1, Math.min(1, sa));
        v.setInt16(44 + i*2, val*0x7FFF, true);
      }
      var b = new Uint8Array(buf), s = '';
      for (var j=0; j<b.length; j++) s += String.fromCharCode(b[j]);
      return 'data:audio/wav;base64,' + btoa(s);
    }

    function ensureAudioReady(){
      if (!silenceDataUri){ silenceDataUri = buildSilenceWavDataUri(); keepEl.src = silenceDataUri; keepEl.volume = 0.001; }
      if (!alarmDataUri){ alarmDataUri = buildAlarmWavDataUri(); alarmEl.src = alarmDataUri; alarmEl.volume = 0.7; }
    }

    // WebAudio fallback alarm — much more reliable than HTML5 Audio when
    // it comes to autoplay policies, because once the AudioContext is
    // resumed via a user gesture it stays unlocked for the whole document.
    var alarmCtx = null;
    function ensureAlarmCtx(){
      try {
        if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch(e){}
      return alarmCtx;
    }
    function resumeAlarmCtx(){
      try {
        var c = ensureAlarmCtx();
        if (c && c.state === 'suspended' && c.resume) c.resume().catch(function(){});
      } catch(e){}
    }
    function playAlarmWebAudio(){
      try {
        var ctx = ensureAlarmCtx();
        if (!ctx) return false;
        if (ctx.state === 'suspended') {
          // Try to resume; if blocked the bell won't play but no-op is fine.
          ctx.resume().catch(function(){});
        }
        var now = ctx.currentTime;
        var freqs = [523.25, 659.25, 783.99]; // C5 E5 G5
        freqs.forEach(function(f, i){
          var osc = ctx.createOscillator();
          var g = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.value = f;
          var t0 = now + i*0.5;
          g.gain.setValueAtTime(0, t0);
          g.gain.linearRampToValueAtTime(0.3, t0 + 0.05);
          g.gain.exponentialRampToValueAtTime(0.001, t0 + 1.5);
          osc.connect(g); g.connect(ctx.destination);
          osc.start(t0); osc.stop(t0 + 1.6);
        });
        return true;
      } catch(e){ return false; }
    }

    function startKeepalive(){
      try {
        ensureAudioReady();
        var p = keepEl.play();
        if (p && p.catch) p.catch(function(){});
      } catch(e){}
    }
    function stopKeepalive(){
      try { keepEl.pause(); keepEl.currentTime = 0; } catch(e){}
    }
    function playAlarm(){
      // Belt-and-suspenders: fire BOTH the WebAudio bell AND the HTML5 audio
      // sample so at least one of them produces sound regardless of which
      // unlock path the browser honored on this page.
      playAlarmWebAudio();
      try {
        ensureAudioReady();
        alarmEl.currentTime = 0;
        var p = alarmEl.play();
        if (p && p.catch) p.catch(function(){});
      } catch(e){}
    }
    function showNotif(title, body){
      try {
        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
        var n = new Notification(title, { body: body, tag: 'machreach-focus' });
        n.onclick = function(){ window.focus(); window.location='/student/focus'; n.close(); };
      } catch(e){}
    }

    // Re-prime audio on EVERY user gesture on EVERY page.
    // We don't remove the listeners after one trigger because the audio
    // context can become suspended again (e.g. after long idle).
    function primeOnGesture(){
      ensureAudioReady();
      // Resume WebAudio context (this is what actually unlocks the alarm).
      resumeAlarmCtx();
      // Briefly play+pause keepalive at audible volume to mark it as a
      // genuine user-initiated playback (mute trick is unreliable in Chrome).
      try {
        var prev = keepEl.volume;
        keepEl.volume = 0.001;
        var p = keepEl.play();
        if (p && p.then) p.then(function(){
          // If a session is active, leave it playing as the keepalive.
          var d = readState();
          if (!(d && d.active)) {
            keepEl.pause(); keepEl.currentTime = 0;
          }
          keepEl.volume = prev;
        }).catch(function(){ keepEl.volume = prev; });
      } catch(e){}
      // If a session is active, ensure keepalive is running.
      var d2 = readState();
      if (d2 && d2.active) startKeepalive();
      // Request notification permission once.
      if (typeof Notification !== 'undefined' && Notification.permission === 'default'){
        try { Notification.requestPermission().catch(function(){}); } catch(e){}
      }
    }
    window.addEventListener('click', primeOnGesture, true);
    window.addEventListener('keydown', primeOnGesture, true);
    window.addEventListener('touchstart', primeOnGesture, true);

    function readState(){
      try { return JSON.parse(localStorage.getItem('focus_float')||'null'); } catch(e){ return null; }
    }
    function writeState(s){
      try { localStorage.setItem('focus_float', JSON.stringify(s)); } catch(e){}
    }
    function markPhaseSaved(id){
      try {
        var arr = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');
        if (arr.indexOf(id) === -1) arr.push(id);
        if (arr.length > 200) arr = arr.slice(-200);
        localStorage.setItem('focus_saved_phases', JSON.stringify(arr));
      } catch(e){}
    }
    function isPhaseSaved(id){
      try {
        var arr = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');
        return arr.indexOf(id) !== -1;
      } catch(e){ return false; }
    }

    function creditPhase(d){
      // d: a focus_float phase object that just ended.
      // New model: phases are NOT saved automatically. They accumulate in
      // `focus_pending_phases` and the user must click "Reclamar" on
      // /student/focus to actually post them. Anti-cheat: every 4th work
      // phase opens a 30-min mandatory claim window enforced by that page.
      if (!d || !d.phaseId) return;
      if (isPhaseSaved(d.phaseId)) return;
      if (!d.workMinutes || d.workMinutes <= 0) return; // breaks don't credit
      if (d.workMinutes > 480) return;
      markPhaseSaved(d.phaseId);
      try {
        var arr = JSON.parse(localStorage.getItem('focus_pending_phases')||'[]');
        if (!Array.isArray(arr)) arr = [];
        arr.push({
          minutes: d.workMinutes,
          courseId: d.courseId || null,
          examId: d.examId || null,
          courseName: d.course || '',
          mode: d.originalMode || 'pomodoro',
          ts: Date.now(),
          phaseId: d.phaseId
        });
        localStorage.setItem('focus_pending_phases', JSON.stringify(arr));
      } catch(e){}
    }

    var phaseEndedFlag = {}; // {phaseId: true}
    function tick(){
      var d = readState();
      if (!d || !d.active){
        widget.style.display = 'none';
        stopKeepalive();
        return;
      }

      // Abandonment guard. If the saved focus_float is more than 12h old
      // (e.g. user closed the browser overnight, started a session days ago
      // and never returned, etc.) DO NOT credit it. Crediting a stale phase
      // is exactly how phantom hours appeared on real users' dashboards.
      // Clear the state silently so it can't auto-fire on the next tick.
      var ABANDON_MS = 12 * 60 * 60 * 1000;
      var refTs = 0;
      if (d.mode === 'stopwatch' && d.startAt) {
        refTs = d.startAt;
      } else if (d.mode === 'countdown' && d.endAt) {
        var w = (d.workMinutes && d.workMinutes > 0) ? d.workMinutes : 25;
        refTs = d.endAt - w * 60 * 1000;
      }
      if (refTs && (Date.now() - refTs) > ABANDON_MS) {
        try { localStorage.removeItem('focus_float'); } catch(e) {}
        widget.style.display = 'none';
        stopKeepalive();
        return;
      }

      widget.style.display = 'block';
      // Make sure keepalive stays running (browser may have paused it).
      if (keepEl && keepEl.paused) startKeepalive();

      if (d.mode === 'countdown'){
        var left = d.endAt - Date.now();
        if (left < 0) left = 0;
        var m = Math.floor(left/60000), s = Math.floor((left%60000)/1000);
        var t = document.getElementById('ff-time');
        var l = document.getElementById('ff-label');
        if (t) t.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
        if (l) l.textContent = d.label || 'Focus';
        if (left <= 0 && d.phaseId && !phaseEndedFlag[d.phaseId]){
          phaseEndedFlag[d.phaseId] = true;
          // When the student is actively on /student/focus, the page-level
          // controller owns credit + audio + chain advancement. If we ALSO
          // credit/advance here we race with it: this widget calls
          // markPhaseSaved + writeState(nextPhase), and the page-level
          // saveFocusSession then reads the FRESH focus_float.phaseId, marks
          // the next-phase id as saved, and the actual next phase's save is
          // blocked by the dedupe ("only the first session counts" bug).
          var onFocusPage = (typeof window !== 'undefined' && window.location && window.location.pathname === '/student/focus');
          if (onFocusPage) return;
          // Credit XP for work phases.
          creditPhase(d);
          // Audible + visual alert (works in background because keepalive kept us unthrottled).
          playAlarm();
          if (d.workMinutes > 0){
            showNotif('Sesión de focus completada', 'Time for a break!');
          } else {
            showNotif('Break over', 'Back to focus!');
          }
          // Advance to next phase or end.
          // Stop the chain at the long-break boundary: the focus page owns
          // the mandatory 30-min claim window. Detect via the chained label
          // ("Descanso largo" / "Long Break"), which is set by the focus page.
          var isLongBreakNext = !!(d.nextPhase && d.nextPhase.label &&
            (d.nextPhase.label.indexOf('largo') !== -1 || d.nextPhase.label.indexOf('Long') !== -1));
          if (isLongBreakNext && d.workMinutes > 0){
            try { localStorage.setItem('focus_mandatory_until', String(Date.now() + 30*60*1000)); } catch(e){}
            d.active = false;
            writeState(d);
            widget.style.display = 'none';
            stopKeepalive();
            showNotif('¡Reclama tu descanso largo!', 'Tienes 30 min para reclamar tus recompensas en el Modo Enfoque.');
          } else if (d.nextPhase){
            // Re-base nextPhase.endAt off NOW so a long pause doesn't make it instantly expire.
            var np = d.nextPhase;
            // The original endAt was relative to the previous phase's endAt; preserve duration.
            // We don't know the duration directly — recompute from workMinutes (work) or label (best-effort 5min default for break is wrong).
            // Safer: nextPhase.endAt was already absolute; if it's already in the past, just skip ahead.
            if (np.endAt && np.endAt > Date.now()){
              writeState(np);
            } else {
              // Compute a sensible new endAt: workMinutes for work phases, fall back to 5min.
              var dur = (np.workMinutes && np.workMinutes>0) ? np.workMinutes*60*1000 : 5*60*1000;
              np.endAt = Date.now() + dur;
              writeState(np);
            }
            startKeepalive();
          } else {
            d.active = false;
            writeState(d);
            widget.style.display = 'none';
            stopKeepalive();
          }
        }
      } else {
        // stopwatch
        var elapsed = Math.floor((Date.now()-d.startAt)/1000);
        var m2 = Math.floor(elapsed/60), s2 = elapsed%60;
        var t2 = document.getElementById('ff-time');
        var l2 = document.getElementById('ff-label');
        if (t2) t2.textContent = String(m2).padStart(2,'0')+':'+String(s2).padStart(2,'0');
        if (l2) l2.textContent = d.label || 'Reading';
      }
    }

    // Boot: if a session is already active when this page loads, start keepalive immediately.
    var initial = readState();
    if (initial && initial.active){
      ensureAudioReady();
      startKeepalive();
      widget.style.display = 'block';
    }

    setInterval(tick, 1000);
    tick();

    window.addEventListener('storage', function(e){ if (e.key === 'focus_float') tick(); });
  })();

  function closeFocusFloat(){
    try { localStorage.removeItem('focus_float'); } catch(e){}
    var el = document.getElementById('focus-float');
    if (el) el.style.display='none';
    var k = document.getElementById('focus-keepalive');
    if (k){ try { k.pause(); k.currentTime = 0; } catch(e){} }
  }
  </script>

  <!-- Student i18n: Spanish translations (client-side) -->
  {% if lang == 'es' and account_type|default('student') == 'student' %}
  <script>
  (function(){
    var T = {
      // Compound phrases first — longer keys win the longest-match regex.
      // Without these, "Study Tools" rendered as "Estudiar Tools" because
      // only the bare word "Study" was in the map.
      "Study Tools": "Herramientas de Estudio",
      "Grade Sheet": "Planilla de Notas",
      "Tools": "Herramientas",
      "Social": "Social",
      "Pro": "Pro",
      "Study Analytics": "Analítica de Estudio",
      "Your study analytics": "Tu analítica de estudio",
      "How much, when, and on what — all time.": "Cuánto, cuándo y en qué — histórico.",
      "Study time per course": "Tiempo de estudio por curso",
      "Click any bar to see the day-by-day breakdown.": "Haz clic en una barra para ver el desglose día a día.",
      "Total hours": "Horas totales",
      "Pages read": "Páginas leídas",
      "Current streak": "Racha actual 🔥",
      "Best day": "Mejor día",
      "Last 14 days": "Últimos 14 días",
      "Day-of-week (this week)": "Día de la semana (esta semana)",
      "this week": "esta semana",
      "No course-tagged focus sessions yet.": "Aún no hay sesiones etiquetadas con un curso.",
      "Previous week": "Semana anterior",
      "Next week": "Semana siguiente",
      "Back": "Volver",
      // Nav
      "Dashboard": "Panel", "Courses": "Cursos", "Plan": "Plan", "Flashcards": "Tarjetas",
      "Quizzes": "Exámenes", "Notes": "Apuntes", "Tutor": "Tutor", "XP": "XP",
      "Mail": "Correo", "Focus Mode": "Modo Enfoque", "Exams": "Exámenes",
      "GPA Calculator": "Calculadora GPA", "Schedule": "Horario", "Weak Topics": "Temas Débiles",
      "Settings": "Ajustes", "Leaderboard": "Clasificación",
      // Achievements page
      "Achievements & Progress": "Logros y Progreso", "Level": "Nivel",
      "XP to next level": "XP para el siguiente nivel", "Day Streak": "Racha de Días 🔥",
      "Badges Earned": "Insignias Obtenidas", "Your Badges": "Tus Insignias",
      "All Badges": "Todas las Insignias", "Recent Activity": "Actividad Reciente",
      "No badges yet — keep studying!": "¡Aún no tienes insignias — sigue estudiando!",
      "No activity yet.": "Aún no hay actividad.",
      "Earned!": "¡Obtenida!", "Not yet earned": "Aún no obtenida",
      // Badge names
      "Welcome!": "¡Bienvenido!", "Quiz Rookie": "Novato en Exámenes",
      "Quiz Master": "Maestro de Exámenes", "Flashcard Fan": "Fan de Tarjetas",
      "On Fire!": "¡En Llamas!", "Unstoppable": "¡Imparable!",
      "Diamond Student": "Estudiante Diamante", "Note Taker": "Tomador de Apuntes",
      "Rising Star": "Estrella Naciente", "Shining Star": "Estrella Brillante",
      "Superstar": "Superestrella", "Focused": "Enfocado", "Deep Focus": "Enfoque Profundo",
      "Focus Master": "Maestro del Enfoque", "Page Turner": "Lector Ávido",
      "Quiz Pro": "Pro de Exámenes",
      // Badge descriptions
      "Logged in for the first time": "Iniciaste sesión por primera vez",
      "Completed your first quiz": "Completaste tu primer examen",
      "Scored 100% on a quiz": "Obtuviste 100% en un examen",
      "Reviewed 100 flashcards": "Revisaste 100 tarjetas",
      "3-day study streak": "Racha 🔥 de estudio de 3 días",
      "7-day study streak": "Racha 🔥 de estudio de 7 días",
      "30-day study streak": "Racha 🔥 de estudio de 30 días",
      "Created 10 notes": "Creaste 10 apuntes",
      "Earned 100 XP": "Ganaste 100 XP", "Earned 500 XP": "Ganaste 500 XP",
      "Earned 1000 XP": "Ganaste 1000 XP",
      "1 hour of total focus time": "1 hora de tiempo de enfoque total",
      "10 hours of total focus time": "10 horas de tiempo de enfoque total",
      "50 hours of total focus time": "50 horas de tiempo de enfoque total",
      "Read 100 pages": "Leíste 100 páginas",
      "Completed 10 quizzes": "Completaste 10 exámenes",
      // Levels
      "Freshman": "Novato", "Sophomore": "Aprendiz", "Junior": "Intermedio",
      "Senior": "Avanzado", "Scholar": "Erudito", "Master": "Maestro", "Professor": "Profesor",
      // Focus page
      "Study Timer": "Temporizador de Estudio", "Pomodoro": "Pomodoro",
      "Page Method": "Método de Páginas", "Custom": "Personalizado",
      "Work (min)": "Trabajo (min)", "Break (min)": "Descanso (min)",
      "Long break (min)": "Descanso largo (min)",
      "Long break after every 4 sessions.": "Descanso largo después de cada 4 sesiones.",
      "Target pages": "Páginas objetivo", "Page Completed!": "¡Página Completada!",
      "Ready to focus": "Listo para enfocarte", "Start": "Iniciar", "Pause": "Pausar",
      "Reset": "Reiniciar", "Study Music": "Música de Estudio",
      "Quick Flashcards": "Tarjetas Rápidas", "Quick Notes": "Notas Rápidas",
      "Hours Focused": "Horas Enfocado", "Sessions": "Sesiones",
      "Pages Read": "Páginas Leídas",
      // Settings
      "Profile": "Perfil", "Name": "Nombre", "Email": "Correo",
      "Email cannot be changed.": "El correo no se puede cambiar.",
      "Save Changes": "Guardar Cambios",
      "University & Studies": "Universidad y Estudios",
      "University": "Universidad", "Field of Study": "Carrera",
      "View Leaderboard": "Ver Clasificación",
      "Canvas LMS": "Canvas LMS", "Conectado": "Conectado",
      "Sin conectar": "No conectado", "Manage Connection": "Administrar Conexión",
      "Conectar Canvas": "Conectar Canvas",
      "Email Accounts": "Cuentas de Correo", "Manage in Mail Hub": "Administrar en Correo",
      "Daily Study Email": "Email Diario de Estudio",
      "Get a morning email with your study plan, upcoming exams, and weak topics to review.":
        "Recibe un email matutino con tu plan de estudio, próximos exámenes y temas a repasar.",
      "Enable daily study email": "Activar email diario de estudio",
      "Send at (hour)": "Enviar a las (hora)", "Timezone": "Zona Horaria",
      "Save Preferences": "Guardar Preferencias", "Saved!": "¡Guardado!",
      "Error saving.": "Error al guardar.",
      // Leaderboard
      "Student Rankings": "Clasificación de Estudiantes",
      "Compete with other students! Earn XP from focus sessions, quizzes, and flashcards.":
        "¡Compite con otros estudiantes! Gana XP con sesiones de enfoque, exámenes y tarjetas.",
      "Your Rank": "Tu Posición", "Total XP": "XP Total", "All Students": "Todos",
      "Rank": "Posición", "Student": "Estudiante",
      "No students on the leaderboard yet. Start earning XP!":
        "Aún no hay estudiantes en la clasificación. ¡Empieza a ganar XP!",
      // Smart Import
      "Smart Import": "Importación Inteligente",
      "Drop a PDF or DOCX — we'll auto-generate notes, flashcards, and a quiz":
        "Sube un PDF o DOCX — generaremos apuntes, tarjetas y un examen automáticamente",
      "Drag & Drop your file here": "Arrastra y suelta tu archivo aquí",
      "or click to browse": "o haz clic para buscar",
      "Generate Notes + Flashcards + Quiz": "Generar Apuntes + Tarjetas + Examen",
      "Processing your document...": "Procesando tu documento...",
      "Study materials created!": "¡Materiales de estudio creados!",
      "Import": "Importar",
      // Study Exchange
      "Study Exchange": "Intercambio de Apuntes",
      "Browse & share study notes with other students":
        "Navega y comparte apuntes con otros estudiantes",
      "My Shared Notes": "Mis Apuntes Compartidos",
      "Search notes...": "Buscar apuntes...", "Subject/Course": "Materia/Curso",
      "Share": "Compartir", "Unpublish": "Despublicar",
      "Public": "Público", "Private": "Privado",
      "Fork to My Notes": "Copiar a Mis Apuntes", "Exchange": "Intercambio",
      "No shared notes yet. Be the first to share!":
        "Aún no hay apuntes compartidos. ¡Sé el primero en compartir!",
      // Exam Simulator
      "Exam Simulator": "Simulador de Examen",
      "Start Exam": "Iniciar Examen",
      "Exam Rules:": "Reglas del Examen:",
      "Lock In Answer": "Confirmar Respuesta",
      "Exam Complete!": "¡Examen Completado!",
      "Question Review": "Revisión de Preguntas",
      "Retake Exam": "Repetir Examen",
      "Analytics": "Análisis",
      "Avg per question": "Promedio por pregunta",
      "Fastest answer": "Respuesta más rápida",
      "Slowest answer": "Respuesta más lenta",
      // SRS
      "Spaced Repetition": "Repetición Espaciada",
      "due": "pendientes", "Again": "Otra vez", "Hard": "Difícil",
      "Good": "Bien", "Easy": "Fácil",
      // Dashboard
      "Today's Plan": "Plan de Hoy", "Upcoming Exams": "Próximos Exámenes",
      "Study Stats": "Estadísticas de Estudio", "Quick Actions": "Acciones Rápidas",
      // Quizzes
      "Generate Quiz": "Generar Examen", "Take Quiz": "Hacer Examen",
      "Your Quizzes": "Tus Exámenes", "Score": "Puntuación", "Attempts": "Intentos",
      "Best Score": "Mejor Puntuación", "Delete": "Eliminar",
      // Flashcards
      "Your Flashcard Decks": "Tus Mazos de Tarjetas", "Study": "Estudiar",
      "cards": "tarjetas", "Generate Flashcards": "Generar Tarjetas",
      // Notes
      "Your Notes": "Tus Apuntes", "Generate Notes": "Generar Apuntes",
      // Common
      "Cargando...": "Cargando...", "Error": "Error", "Success": "Éxito",
      "Cancel": "Cancelar", "Confirm": "Confirmar", "Save": "Guardar",
      "Back": "Volver", "Next": "Siguiente", "Previous": "Anterior",
      "Search": "Buscar", "Filter": "Filtrar", "Sort": "Ordenar",
      "Select a course": "Selecciona un curso", "No courses yet": "Aún no hay cursos",

      // ── Extended UI vocabulary ──
      // Generic actions
      "Edit": "Editar", "Update": "Actualizar", "Add": "Agregar", "Create": "Crear",
      "Remove": "Quitar", "Submit": "Enviar", "Send": "Enviar", "Close": "Cerrar",
      "Open": "Abrir", "Continue": "Continuar", "Finish": "Finalizar", "Done": "Listo",
      "Apply": "Aplicar", "Reload": "Recargar", "Refresh": "Actualizar", "Generate": "Generar",
      "Analyze": "Analizar", "Upload": "Subir", "Download": "Descargar",
      "Browse": "Examinar", "Choose": "Elegir", "Select": "Seleccionar",
      "Yes": "Sí", "No": "No", "OK": "OK", "Got it": "Entendido",
      "Logout": "Cerrar Sesión", "Login": "Iniciar Sesión", "Sign in": "Iniciar Sesión",
      "Sign up": "Registrarse", "Register": "Registrarse",
      "Free": "Gratis", "Pro": "Pro", "Premium": "Premium", "Upgrade": "Mejorar",
      "Active": "Activo", "Inactive": "Inactivo", "Pending": "Pendiente",
      "Completed": "Completado", "Failed": "Falló", "Sent": "Enviado",
      "Draft": "Borrador", "Archive": "Archivar", "Archived": "Archivado",
      "All": "Todos", "None": "Ninguno", "Other": "Otro",
      "Today": "Hoy", "Yesterday": "Ayer", "Tomorrow": "Mañana",
      "This Week": "Esta Semana", "This Month": "Este Mes",
      "Date": "Fecha", "Time": "Hora", "Duration": "Duración",
      "Created": "Creado", "Updated": "Actualizado", "Last Updated": "Última Actualización",
      "Type": "Tipo", "Title": "Título", "Description": "Descripción",
      "Notes": "Apuntes", "Tags": "Etiquetas", "Category": "Categoría",
      "Public": "Público", "Private": "Privado",

      // Drag & drop / files
      "Drop a PDF / DOCX / TXT here": "Suelta un PDF / DOCX / TXT aquí",
      "or click to browse": "o haz clic para buscar",
      "Drag & drop PDF or DOCX files here": "Arrastra y suelta PDF o DOCX aquí",
      "we'll generate flashcards directly from the file (no course needed)":
        "generaremos tarjetas directamente del archivo (no se necesita curso)",
      "we'll generate quiz questions directly from the file (no course needed)":
        "generaremos preguntas directamente del archivo (no se necesita curso)",
      "— or pick from your courses —": "— o elige de tus cursos —",
      "multi-chapter PDFs fully supported": "PDFs con múltiples capítulos totalmente soportados",
      "AI-summarize into structured notes (recommended for textbooks & multi-chapter PDFs)":
        "Resumir con IA en apuntes estructurados (recomendado para libros y PDFs con varios capítulos)",
      "Drop a PDF / DOCX / TXT": "Suelta un PDF / DOCX / TXT",
      "we'll extract the text into the editor below": "extraeremos el texto en el editor de abajo",
      "Or drop your essay file": "O suelta tu archivo de ensayo",
      "Attach a file (PDF/DOCX/TXT)": "Adjuntar un archivo (PDF/DOCX/TXT)",
      "Ask your tutor... (or drag a PDF onto the chat)":
        "Pregúntale a tu tutor... (o arrastra un PDF al chat)",
      "Drag & Drop Anywhere": "Arrastra y Suelta en Cualquier Lugar",
      "Drop a PDF onto Notes, Flashcards, or Quizzes — instant study material from your files.":
        "Suelta un PDF en Apuntes, Tarjetas o Exámenes — material de estudio al instante.",

      // Course / Exam / Quiz / Flashcard / Notes shared labels
      "Course": "Curso", "Courses": "Cursos", "Exam": "Examen", "Topic": "Tema", "Topics": "Temas",
      "Question": "Pregunta", "Questions": "Preguntas", "Answer": "Respuesta", "Answers": "Respuestas",
      "Number of cards": "Cantidad de tarjetas", "Number of questions": "Cantidad de preguntas",
      "Custom title (optional)": "Título personalizado (opcional)",
      "Auto-generated if empty": "Generado automáticamente si está vacío",
      "Difficulty": "Dificultad",
      "Easy — Basic recall": "Fácil — Recuerdo básico",
      "Medium — Exam-level": "Medio — Nivel de examen",
      "Hard — Challenge": "Difícil — Desafío",
      "Generate AI Flashcards": "Generar Tarjetas con IA",
      "Generate AI Quiz": "Generar Examen con IA",
      "Generate AI Notes": "Generar Apuntes con IA",
      "AI Flashcards": "Tarjetas IA",
      "AI Study Tutor": "Tutor de Estudio IA",
      "Practice Quizzes": "Exámenes de Práctica",
      "Smart spaced repetition · Generated from your course materials":
        "Repetición espaciada · Generadas desde tus materiales de curso",
      "Unlimited AI-generated questions · Adjustable difficulty":
        "Preguntas ilimitadas con IA · Dificultad ajustable",
      "Ask anything about your courses — your AI tutor uses your own notes and course material to help.":
        "Pregunta lo que sea sobre tus cursos — tu tutor IA usa tus propios apuntes para ayudarte.",
      "General (no specific course)": "General (sin curso específico)",
      "Up to 100. Large quizzes generate in batches — give it a few seconds.":
        "Hasta 100. Los exámenes grandes se generan por lotes — dale unos segundos.",
      "All topics": "Todos los temas",
      "Exam (optional)": "Examen (opcional)",
      "Not taken": "No realizado",
      "attempts": "intentos", "attempt": "intento",
      "questions": "preguntas", "question": "pregunta",
      "due": "pendientes",
      "Drop a file or select a course": "Suelta un archivo o selecciona un curso",
      "Generated %d flashcards!": "¡%d tarjetas generadas!",
      "Error al generar": "Falló la generación",
      "Error de red": "Error de red",
      "Failed to add card": "Error al agregar tarjeta",
      "Failed to delete": "Error al eliminar",
      "Delete this flashcard deck?": "¿Eliminar este mazo de tarjetas?",
      "Delete this card?": "¿Eliminar esta tarjeta?",
      "Delete this note?": "¿Eliminar este apunte?",
      "Delete this quiz?": "¿Eliminar este examen?",
      "Clear chat history?": "¿Borrar historial de chat?",
      "No flashcard decks yet. Generate your first set from a course!":
        "Aún no tienes mazos. ¡Genera el primero desde un curso!",
      "No quizzes yet. Generate your first practice quiz from a course!":
        "Aún no tienes exámenes. ¡Genera el primero desde un curso!",
      "Hi! I'm your AI study tutor. Ask me anything about your course material! 📚":
        "¡Hola! Soy tu tutor IA. ¡Pregúntame lo que sea sobre tus materiales! 📚",
      "Please summarize and explain the attached document.":
        "Por favor resume y explica el documento adjunto.",
      "PDF, DOCX, or TXT only": "Solo PDF, DOCX, o TXT",
      "File too large (max 15MB)": "Archivo demasiado grande (máx 15MB)",
      "Only PDF, DOCX, and TXT files": "Solo archivos PDF, DOCX y TXT",

      // Essay assistant
      "Essay Assistant": "Asistente de Ensayos",
      "Paste your draft. Get brutally honest feedback on thesis, structure, grammar, and flow.":
        "Pega tu borrador. Recibe feedback honesto sobre tesis, estructura, gramática y flujo.",
      "Assignment prompt": "Enunciado del trabajo",
      "What was the essay supposed to answer?": "¿Qué debía responder el ensayo?",
      "Your essay": "Tu ensayo",
      "Paste your draft here...": "Pega tu borrador aquí...",
      "This takes ~10 seconds.": "Esto tarda ~10 segundos.",
      "Thesis": "Tesis", "Structure": "Estructura", "Grammar": "Gramática",
      "Clarity": "Claridad", "Words": "Palabras", "Level": "Nivel",
      "Strengths": "Fortalezas", "Weaknesses": "Debilidades",
      "Grammar & Style": "Gramática y Estilo", "Rewritten Intro": "Introducción Reescrita",
      "Thesis Feedback": "Feedback de Tesis", "Overall": "General",
      "No major grammar issues detected.": "No se detectaron problemas graves de gramática.",
      "Paste at least a couple of paragraphs.": "Pega al menos un par de párrafos.",
      "Analyzing...": "Analizando...",

      // Panic mode
      "Panic Mode": "Modo Pánico",
      "Exam tomorrow and nothing's done? Get a ruthless cram plan in 10 seconds.":
        "¿Examen mañana y nada hecho? Obtén un plan de estudio en 10 segundos.",

      // Notes page
      "Generated notes": "Apuntes generados",
      "AI Study Notes": "Apuntes de Estudio IA",

      // Mail Hub / Inbox common
      "Inbox": "Bandeja", "Sent": "Enviados", "Outbox": "Salida",
      "Trash": "Papelera", "Spam": "Spam", "Drafts": "Borradores",
      "Reply": "Responder", "Reply All": "Responder a Todos", "Forward": "Reenviar",
      "Compose": "Redactar", "New Email": "Nuevo Correo",
      "From": "De", "To": "Para", "Cc": "Cc", "Bcc": "Cco", "Subject": "Asunto",
      "Body": "Cuerpo", "Attachments": "Adjuntos",
      "Mail Hub": "Centro de Correo",

      // Contacts
      "Contacts": "Contactos", "Add Contact": "Agregar Contacto",
      "First Name": "Nombre", "Last Name": "Apellido",
      "Company": "Empresa", "Phone": "Teléfono", "Notes": "Notas",

      "Templates": "Plantillas", "Sequence": "Secuencia",
      "Open Rate": "Tasa de Apertura", "Reply Rate": "Tasa de Respuesta",
      "Sent at": "Enviado a las", "Scheduled": "Programado",
      "Send Now": "Enviar Ahora", "Schedule": "Programar",

      "Plan": "Plan", "Current Plan": "Plan Actual",
      "Upgrade Plan": "Mejorar Plan", "Downgrade": "Bajar Plan",
      "Cancel Subscription": "Cancelar Suscripción",
      "per month": "por mes", "per year": "por año",
      "Free Forever": "Gratis Para Siempre",
      "Most Popular": "Más Popular",

      // Dashboard widgets
      "Today's Tasks": "Tareas de Hoy", "Recent Activity": "Actividad Reciente",
      "Quick Stats": "Estadísticas Rápidas", "Performance": "Rendimiento",
      "Welcome back": "Bienvenido de vuelta",

      // GPA / Schedule / Weak topics
      "GPA Calculator": "Calculadora GPA", "Add Course": "Agregar Curso",
      "Weight": "Peso", "Grade": "Nota", "Credit": "Crédito",
      "Total GPA": "GPA Total", "Semester": "Semestre",
      "Class Schedule": "Horario de Clases",
      "Add to Schedule": "Agregar al Horario",
      "Weak Topics": "Temas Débiles",
      "Topics you've struggled with": "Temas con los que has tenido dificultad",

      // Achievements
      "Achievements": "Logros", "XP & Badges": "XP e Insignias",
      "Earn XP by studying!": "¡Gana XP estudiando!",

      // Settings sections
      "Theme": "Tema", "Language": "Idioma", "Currency": "Moneda",
      "Notifications": "Notificaciones", "Privacy": "Privacidad",
      "Account": "Cuenta", "Danger Zone": "Zona de Peligro",

      // Empty states
      "Nothing here yet.": "Nada por aquí todavía.",
      "Get started by creating one": "Empieza creando uno",

      // ── Dashboard headings (whole phrases — must come BEFORE word-level keys) ──
      "Today's Study Plan": "Plan de Estudio de Hoy",
      "Today's Plan": "Plan de Hoy",
      "Upcoming Exams": "Próximos Exámenes",
      "Upcoming Examens": "Próximos Exámenes",
      "Plan Progress": "Progreso del Plan",
      "Hours Focused": "Horas de Estudio",
      "Focus Hours": "Horas de Estudio",
      "day streak": "días de racha 🔥",
      "Day Streak": "Racha de Días 🔥",
      "XP to next level": "XP para el siguiente nivel",
      "What can I do here?": "¿Qué puedo hacer aquí?",
      "A visual map of every feature — click any card to jump there.":
        "Un mapa visual de cada función — haz clic en cualquier tarjeta para ir.",
      "Show": "Mostrar", "Hide": "Ocultar",
      "No study sessions yet": "Aún no hay sesiones de estudio",
      "Sync your courses and generate a plan to get a personalized study schedule for today.":
        "Sincroniza tus cursos y genera un plan para obtener un horario de estudio personalizado para hoy.",
      "No upcoming exams": "No hay exámenes próximos",
      "Sync your courses to automatically detect exam dates from Canvas.":
        "Sincroniza tus cursos para detectar automáticamente las fechas de examen desde Canvas.",
      "Conectar Canvas": "Conectar Canvas",
      "Generate Plan": "Generar Plan",
      "Sync Canvas": "Sincronizar Canvas",
      "Mark Today Complete": "Marcar Hoy Como Completo",
      "AI Recommendations": "Recomendaciones de IA",
      "Starting sync...": "Iniciando sincronización...",
      "Syncing...": "Sincronizando...",
      "Take a break — this may take a while depending on how many files your courses have.":
        "Tómate un descanso — esto puede tardar dependiendo de cuántos archivos tengan tus cursos.",
      "Sync complete!": "¡Sincronización completada!",
      "Sync failed": "La sincronización falló",
      "Error de red": "Error de red",
      "Stats at a glance": "Estadísticas de un vistazo",
      "Your Student Dashboard": "Tu Panel de Estudiante",
      "Exams Dashboard": "Panel de Exámenes",
      "Every upcoming exam, sorted by urgency.": "Todos los exámenes próximos, ordenados por urgencia.",

      // ── Courses page ──
      "My Courses": "Mis Cursos", "Canvas Integration": "Integración con Canvas",
      "Course Sync": "Sincronización de Cursos", "New Course": "Nuevo Curso",
      "Create Course": "Crear Curso", "Create a course": "Crear un curso",
      "Create course manually": "Crear curso manualmente",
      "Send to Canvas": "Enviar a Canvas",
      "Sync Now": "Sincronizar Ahora", "View Materials": "Ver Materiales",
      "Course name": "Nombre del curso", "Code": "Código", "Term": "Periodo",
      "Last Synced": "Última sincronización",
      "Files": "Archivos", "Grading": "Calificación",
      "No courses yet": "Aún no tienes cursos",
      "No courses synced yet": "Aún no se han sincronizado cursos",
      "Sync your courses first": "Sincroniza tus cursos primero",
      "No files uploaded": "No hay archivos subidos",

      // ── Study Plan page ──
      "Study Plan": "Plan de Estudio",
      "Weekly Schedule": "Horario Semanal",
      "Course Difficulty": "Dificultad del Curso",
      "Edit Schedule": "Editar Horario",
      "Free day": "Día libre",
      "Check off each assignment as you complete it":
        "Marca cada tarea a medida que la completes",
      "No study plan yet": "Aún no hay plan de estudio",
      "Sync your Canvas courses first to generate a plan.":
        "Sincroniza tus cursos de Canvas primero para generar un plan.",
      "Complete": "Completar", "Remaining": "Restante",

      // ── Focus Mode page ──
      "Focus Mode": "Modo Enfoque", "Focus Guard": "Guardián de Enfoque",
      "Quick Access": "Acceso Rápido", "Studying for:": "Estudiando para:",
      "Long break after every 4 sessions": "Descanso largo cada 4 sesiones",
      "Space flip": "Espacio para voltear",
      "1 incorrect": "1 incorrecto", "2 correct": "2 correcto",
      "Pages Read": "Páginas Leídas",

      // ── Flashcards page ──
      "Smart spaced repetition": "Repetición espaciada inteligente",
      "Study Mode": "Modo de Estudio", "Edit Cards": "Editar Tarjetas",
      "Add Card": "Agregar Tarjeta", "Study Again": "Estudiar de Nuevo",
      "Start Studying": "Comenzar a Estudiar",
      "Undo last": "Deshacer último",
      "Click to flip": "Haz clic para voltear",
      "Incorrect": "Incorrecto", "Correct": "Correcto",
      "Reviewing again tomorrow": "Repasando de nuevo mañana",
      "Good learning pace": "Buen ritmo de aprendizaje",
      "No flashcard decks yet": "Aún no tienes mazos de tarjetas",
      "Generate your first set from a course!":
        "¡Genera tu primer conjunto desde un curso!",
      "Exam (optional)": "Examen (opcional)",
      "Custom title": "Título personalizado",

      // ── Quizzes page ──
      "Ready to start?": "¿Listo para empezar?",
      "Quiz complete": "Examen completado",
      "Start Quiz": "Iniciar Examen", "See Results": "Ver Resultados",
      "Retake quiz": "Repetir examen", "Retake wrong only": "Repetir solo errores",
      "Back to quizzes": "Volver a exámenes",
      "Enable timer": "Activar temporizador", "Mode": "Modo",
      "Total time for whole quiz": "Tiempo total para el examen",
      "Time per question": "Tiempo por pregunta",
      "60s / question": "60s / pregunta", "90s / question": "90s / pregunta",
      "2m / question": "2m / pregunta", "Realistic exam": "Examen realista",
      "Total time": "Tiempo total", "Avg / question": "Prom. / pregunta",
      "Fastest": "Más rápida", "Slowest": "Más lenta",
      "Mastery": "Dominio", "Solid": "Sólido", "Shaky": "Inestable",
      "Struggling": "Con dificultad",
      "Strengths": "Fortalezas", "Needs work": "Necesita trabajo",
      "Mistake patterns": "Patrones de error", "Do this next": "Haz esto a continuación",
      "30-minute follow-up plan": "Plan de seguimiento de 30 minutos",
      "Question-by-question review": "Revisión pregunta por pregunta",
      "Analyzing...": "Analizando...", "Topic breakdown": "Desglose por tema",

      // ── Exam Simulator ──
      "Time Limit (minutes)": "Tiempo Límite (minutos)",
      "You cannot go back to previous questions":
        "No puedes regresar a preguntas anteriores",
      "Timer runs continuously — no pausing":
        "El temporizador corre sin parar — sin pausas",
      "Answers are final once submitted":
        "Las respuestas son finales al enviarse",
      "Detailed analytics provided at the end":
        "Análisis detallado al finalizar",
      "Back to Quizzes": "Volver a Exámenes",

      // ── Notes page ──
      "AI Study Notes": "Apuntes de Estudio con IA",
      "Comprehensive notes generated from your course materials":
        "Apuntes completos generados desde tus materiales de curso",
      "Generate AI Study Notes": "Generar Apuntes con IA",
      "Export PDF": "Exportar PDF", "Print": "Imprimir",
      "Uploading...": "Subiendo...", "AI-summarizing...": "Resumiendo con IA...",
      "No notes yet": "Aún no hay apuntes",
      "Generate AI study notes from your course materials!":
        "¡Genera apuntes con IA desde tus materiales de curso!",
      "Back to Notes": "Volver a Apuntes",
      "Bold (B)": "Negrita (B)", "Italic (I)": "Cursiva (I)",
      "Underline (U)": "Subrayado (U)",
      "Heading 2 (H2)": "Encabezado 2 (H2)", "Heading 3 (H3)": "Encabezado 3 (H3)",
      "Paragraph (P)": "Párrafo (P)",
      "Bullet list": "Lista con viñetas", "Numbered list": "Lista numerada",
      "Clear formatting": "Quitar formato",

      "Attached:": "Adjuntado:", "No file attached": "Sin archivo adjunto",
      "Hi! I'm your AI study tutor. Ask me anything about your course material!":
        "¡Hola! Soy tu tutor de estudio con IA. ¡Pregúntame lo que quieras sobre tus materiales!",
      "Thinking...": "Pensando...",
      "Please summarize and explain the attached document":
        "Por favor resume y explica el documento adjunto",

      // ── Weak Topics ──
      "Weak Topic Detector": "Detector de Temas Débiles",
      "Based on your flashcard accuracy and quiz scores, here are the topics that need more attention":
        "Basado en tu precisión en tarjetas y exámenes, estos son los temas que necesitan más atención",
      "Recommendations": "Recomendaciones",
      "Next steps to improve": "Próximos pasos para mejorar",
      "Not enough data yet": "Aún no hay suficientes datos",
      "Complete some quizzes and review flashcards to see your weak spots":
        "Completa algunos exámenes y repasa tarjetas para ver tus puntos débiles",

      // ── Achievements ──
      "XP / Total": "XP / Total",

      // ── Leaderboard ──
      "Personal Leaderboards": "Clasificaciones Personales",
      "Fair-play": "Juego Limpio", "Fair-play group": "Grupo de juego limpio",
      "Everyone starts at 0 XP": "Todos comienzan en 0 XP",
      "Create a Group": "Crear un Grupo", "Join with Code": "Unirse con Código",
      "Group Name": "Nombre del Grupo", "Enter group name": "Ingresa el nombre del grupo",
      "Invite Code": "Código de Invitación", "Members": "Miembros",
      "Copy Invite": "Copiar Invitación", "Delete Group": "Eliminar Grupo",
      "Leave": "Salir", "Join": "Unirse",

      // ── Study Exchange ──
      "Share to Exchange": "Compartir al Intercambio",
      "Unpublish from Exchange": "Despublicar del Intercambio",
      "Back to Exchange": "Volver al Intercambio",
      "No notes to share": "No hay apuntes para compartir",
      "Create notes first!": "¡Crea apuntes primero!",

      // ── Settings page ──
      "Mail Sorting Rules": "Reglas de Clasificación de Correo",
      "Interactive Tutorial": "Tutorial Interactivo",
      "Account Security": "Seguridad de la Cuenta",
      "Add Email Account": "Agregar Cuenta de Correo",
      "Save Rules": "Guardar Reglas",
      "Restart Tutorial": "Reiniciar Tutorial",
      "Change password": "Cambiar contraseña",
      "Update Password": "Actualizar Contraseña",
      "Delete My Account": "Eliminar Mi Cuenta",
      "Conectado": "Conectado", "Sin conectar": "No conectado",
      "Your account is secure": "Tu cuenta está segura",
      "mailboxes": "buzones",
      "Write your mail sorting rules here...":
        "Escribe aquí tus reglas de clasificación de correo...",
      "e.g. MIT, Stanford, UNAM...": "ej. MIT, Stanford, UNAM...",
      "e.g. Computer Science, Medicine...": "ej. Ingeniería, Medicina...",
      "Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action cannot be undone":
        "Eliminar permanentemente tu cuenta y todos los datos asociados (cursos, exámenes, apuntes, tarjetas, exámenes, historial de chat, XP, insignias). Esta acción no se puede deshacer",
      "Permanently Delete Account": "Eliminar Cuenta Permanentemente",
      "Current Password": "Contraseña Actual",
      "New Password": "Contraseña Nueva",
      "Confirm Password": "Confirmar Contraseña",
      "Minimum 6 characters": "Mínimo 6 caracteres",
      "Replay the guided walkthrough to rediscover all the features available to you":
        "Reproduce el recorrido guiado para redescubrir todas las funciones disponibles",
      "Emails from my professors are always urgent":
        "Los correos de mis profesores siempre son urgentes",
      "Meeting invites from @university.edu are important":
        "Las invitaciones de reuniones desde @university.edu son importantes",
      "Newsletters and marketing emails are always low priority":
        "Los boletines y correos de marketing siempre son de baja prioridad",

      // ── Canvas settings ──
      "Canvas Connection": "Conexión con Canvas",
      "Canvas LMS Integration": "Integración con Canvas LMS",
      "Canvas URL": "URL de Canvas",
      "API Access Token": "Token de Acceso API",
      "Desconectar": "Desconectar", "Test Connection": "Probar Conexión",

      // ── GPA Calculator ──
      "Your GPA": "Tu GPA", "What-If": "Simulador",
      "Credits": "Créditos", "Calculate GPA": "Calcular GPA",
      "GPA Scale": "Escala de GPA",

      // ── Practice / Schedule ──
      "Practice Problems": "Ejercicios de Práctica",
      "AI-Generated Exercises": "Ejercicios Generados por IA",
      "Schedule & Study Time": "Horario y Tiempo de Estudio",
      "Weekly Availability": "Disponibilidad Semanal",
      "Time slots for study": "Bloques de tiempo para estudiar",
      "Days of the week": "Días de la semana",
      "Hours per day": "Horas por día",

      // ── Headers / brand ──
      "MachReach Student": "MachReach Estudiante",
      "AI-powered study planner · Canvas integration":
        "Planificador de estudio con IA · Integración con Canvas",
      "View All": "Ver Todos",

      // ── Themes ──
      "Default": "Predeterminado", "Midnight": "Medianoche", "Forest": "Bosque",
      "Ocean": "Océano", "Rose": "Rosa", "Sunset": "Atardecer",
      "Mono": "Monocromo", "Light": "Claro", "Lavender": "Lavanda",
      "Mint": "Menta", "Peach": "Durazno", "Sky": "Cielo",
      "Butter": "Mantequilla", "Lilac": "Lila", "Blush": "Rubor",
      "Sand": "Arena", "Cotton Candy": "Algodón de Azúcar", "Seafoam": "Espuma",
    };

    // EXACT-match only. We used to fall back to a partial-word regex which
    // produced "Activo duels", "Todos-time", "Estudiar marathon invites",
    // "Academic Perfil" etc. — bare entries like "Active": "Activo" would
    // grab one word and leave the rest English. Now if a phrase isn't in
    // T verbatim it stays English (and we translate it at the source).
    function translate(el) {
      if (el.childElementCount === 0) {
        var raw = el.textContent;
        var txt = raw.trim();
        if (!txt) return;
        if (T[txt]) {
          el.textContent = raw.replace(txt, T[txt]);
        }
      }
      if (el.placeholder && T[el.placeholder]) el.placeholder = T[el.placeholder];
      if (el.title && T[el.title]) el.title = T[el.title];
    }

    function runTranslate(){
      var root = document.querySelector('.container') || document.body;
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
      while(walker.nextNode()) translate(walker.currentNode);
      // Belt-and-suspenders pass for common containers
      document.querySelectorAll('h1,h2,h3,h4,h5,label,button,a,th,td,li,p,span,div,option,summary,figcaption,small,strong,em,b,i').forEach(translate);
      // Translate <input type=button|submit value="...">
      document.querySelectorAll('input[type="button"],input[type="submit"]').forEach(function(el){
        if (el.value && T[el.value]) el.value = T[el.value];
      });
    }
    runTranslate();
    setTimeout(runTranslate, 400);
    setTimeout(runTranslate, 1200);
    setTimeout(runTranslate, 3000);
    // Re-translate when DOM changes (modals, async loads, tab switches)
    try {
      var _mo = new MutationObserver(function(muts){
        var any = false;
        for (var i=0; i<muts.length; i++){
          if (muts[i].addedNodes && muts[i].addedNodes.length){ any = true; break; }
        }
        if (any) { clearTimeout(window._mrTrTimer); window._mrTrTimer = setTimeout(runTranslate, 150); }
      });
      _mo.observe(document.body, {childList:true, subtree:true});
    } catch(_){}

    var origAlert = window.alert;
    window.alert = function(msg) {
      var raw = String(msg || '').trim();
      origAlert(T[raw] || raw);
    };
  })();
  </script>
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
      "Ensayos": "Essays",
      "Comunidad": "Community",
      "Ranking": "Leaderboard",
      "Amigos": "Friends",
      "Mercado": "Marketplace",
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
      "URL DE CANVAS": "CANVAS URL",
      "TOKEN DE ACCESO API": "API ACCESS TOKEN",
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
      "Mejorar a Ultimate": "Upgrade to Ultimate",
      "Gasta monedas en congeladores de racha 🔥, banners de perfil y boosts temporales. Gana monedas completando sesiones de enfoque, quizzes, tarjetas y duelos.": "Spend coins on streak 🔥 freezes, profile banners, and temporary boosts. Earn coins by completing focus sessions, quizzes, flashcards, and duels.",

      "Mercado": "Marketplace",
      "Comprar": "Buy",
      "Vender": "Sell",
      "Buscar": "Search",
      "Mis publicaciones": "My listings",
      "Vender archivo": "Sell a file",
      "Aún no hay apuntes compartidos.": "No shared notes yet.",

      "Borrador": "Draft",
      "Asistente de escritura": "Writing assistant",
      "Ensayos sin vueltas.": "Essays, no fluff.",
      "Suelta tu archivo": "Drop your file",
      "Sube un archivo": "Upload a file",
      "Corregir ensayo": "Review essay",

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
      "Apuntes/ensayos hoy": "Notes/essays today",
      "Mercado ventas hoy": "Marketplace sales today",
      "Usuarios totales": "Total users",
      "Activos 7 días": "Active 7 days"
    };

    Object.assign(T, {{ student_en_visible|default({})|tojson }});

    function trText(txt) {
      if (!txt) return null;
      if (T[txt]) return T[txt];
      var out = txt;
      Object.keys(T).sort(function(a,b){ return b.length - a.length; }).forEach(function(k){
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

  {% if logged_in and account_type|default('student') == 'student' %}
  <!-- ── Academic onboarding modal + preserved-XP welcome banner ── -->
  <div id="mrXpBanner" style="display:none;position:fixed;left:50%;top:18px;transform:translateX(-50%);z-index:9998;
       background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;padding:12px 20px;border-radius:12px;
       box-shadow:0 10px 40px rgba(99,102,241,.4);font-weight:500;align-items:center;gap:12px;
       max-width:90vw;animation:mrSlideDown .5s cubic-bezier(.22,.61,.36,1);">
    <span style="font-size:22px;">🎉</span>
    <span>Welcome back — <strong>your previous progress has been preserved.</strong> All your XP is intact.</span>
    <button id="mrXpBannerClose" style="background:rgba(255,255,255,.2);border:0;color:#fff;width:26px;height:26px;border-radius:50%;cursor:pointer;font-size:16px;line-height:1;">×</button>
  </div>

  <div id="mrOnboardingModal" style="display:none;position:fixed;inset:0;z-index:9999;
       background:rgba(10,14,26,.88);backdrop-filter:blur(14px);
       align-items:center;justify-content:center;padding:20px;">
    <div style="background:linear-gradient(135deg,#10172A 0%,#1A2340 100%);border:1px solid rgba(148,163,184,.15);
         border-radius:24px;max-width:560px;width:100%;padding:36px;color:#E5EAF5;
         box-shadow:0 40px 80px rgba(0,0,0,.6);animation:mrModalIn .45s cubic-bezier(.22,.61,.36,1);">
      <div id="mrStepIndicator" style="display:flex;gap:8px;margin-bottom:22px;">
        <div class="mr-step-dot active"></div><div class="mr-step-dot"></div><div class="mr-step-dot"></div><div class="mr-step-dot"></div>
      </div>
      <div id="mrStepContent"></div>
      <div style="display:flex;gap:10px;margin-top:26px;justify-content:space-between;align-items:center;">
        <button id="mrStepBack" style="background:transparent;color:#8B93A7;border:0;cursor:pointer;font-size:14px;">← Back</button>
        <button id="mrStepNext" style="background:linear-gradient(135deg,#7C9CFF,#C084FC);color:#0A0E1A;
                border:0;padding:12px 28px;border-radius:12px;font-weight:700;cursor:pointer;font-size:15px;
                box-shadow:0 8px 24px rgba(124,156,255,.4);">Continue →</button>
      </div>
    </div>
  </div>

  <style>
    @keyframes mrSlideDown { from { transform:translate(-50%,-30px); opacity:0;} to { transform:translate(-50%,0); opacity:1;}}
    @keyframes mrModalIn { from { transform:scale(.92); opacity:0;} to { transform:scale(1); opacity:1;}}
    .mr-step-dot { flex:1; height:4px; background:rgba(148,163,184,.2); border-radius:2px; transition:all .3s;}
    .mr-step-dot.active { background:linear-gradient(90deg,#7C9CFF,#C084FC); box-shadow:0 0 12px rgba(124,156,255,.5);}
    .mr-input { width:100%; background:rgba(255,255,255,.04); border:1px solid rgba(148,163,184,.18);
      border-radius:12px; padding:13px 16px; color:#E5EAF5; font-size:15px; outline:none; transition:all .2s;
      font-family:inherit;}
    .mr-input:focus { border-color:#7C9CFF; box-shadow:0 0 0 3px rgba(124,156,255,.2); background:rgba(255,255,255,.06);}
    .mr-results { max-height:240px; overflow-y:auto; margin-top:10px; border-radius:12px; background:rgba(0,0,0,.25);}
    .mr-result { padding:12px 16px; cursor:pointer; transition:background .15s; border-bottom:1px solid rgba(148,163,184,.08);
      display:flex; justify-content:space-between; align-items:center;}
    .mr-result:hover, .mr-result.focus { background:rgba(124,156,255,.15);}
    .mr-result.selected { background:linear-gradient(90deg,rgba(124,156,255,.25),rgba(192,132,252,.15)); border-left:3px solid #7C9CFF;}
    .mr-result .tag { font-size:11px; color:#8B93A7; background:rgba(148,163,184,.1); padding:3px 8px; border-radius:999px;}
    .mr-create-new { padding:12px 16px; color:#7C9CFF; cursor:pointer; font-weight:600; text-align:center;
      border-top:1px solid rgba(148,163,184,.15);}
    .mr-create-new:hover { background:rgba(124,156,255,.1);}
  </style>

  <script>
  (function(){
    const modal = document.getElementById('mrOnboardingModal');
    const banner = document.getElementById('mrXpBanner');
    const stepContent = document.getElementById('mrStepContent');
    const stepDots = document.querySelectorAll('.mr-step-dot');
    const nextBtn = document.getElementById('mrStepNext');
    const backBtn = document.getElementById('mrStepBack');

    let state = {
      step: 0,
      country_iso: '',
      country_name: '',
      university_id: null,
      university_name: '',
      major_id: null,
      major_name: '',
      canvas_url: '',
      canvas_token: '',
      countries: [],
    };

    const STEPS = [
      { title:'Where are you studying?',
        sub:'Pick your country. This sets up your country leaderboard.',
        render: renderCountry },
      { title:'Which university?',
        sub:'Start typing. Create yours if you don\'t see it.',
        render: renderUniversity },
      { title:'What do you study?',
        sub:'Major, program, or field. We normalize duplicates.',
        render: renderMajor },
      { title:'Conectar Canvas (optional)',
        sub:'Paste your Canvas personal API token to auto-sync courses and assignments. Skip and do it later from Settings.',
        render: renderCanvas },
    ];

    function renderHeader(title, sub) {
      return `<h2 style="margin:0 0 8px;font-size:26px;letter-spacing:-.02em;">${title}</h2>
              <p style="margin:0 0 20px;color:#8B93A7;font-size:14px;">${sub}</p>`;
    }

    async function renderCountry() {
      if (!state.countries.length) {
        const r = await fetch('/api/academic/countries');
        const j = await r.json();
        state.countries = j.countries || [];
      }
      const hdr = renderHeader(STEPS[0].title, STEPS[0].sub);
      const opts = state.countries.map(c =>
        `<div class="mr-result ${state.country_iso===c.iso_code?'selected':''}" data-iso="${c.iso_code}" data-name="${c.name}">
          <span>${c.flag_emoji||''} ${c.name}</span>
          <span class="tag">${c.region||''}</span>
         </div>`).join('');
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrCountrySearch" placeholder="Search countries…" autocomplete="off">
         <div class="mr-results" id="mrCountryList">${opts}</div>`;
      const list = document.getElementById('mrCountryList');
      document.getElementById('mrCountrySearch').addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        list.querySelectorAll('.mr-result').forEach(el => {
          el.style.display = el.dataset.name.toLowerCase().includes(q) ? '' : 'none';
        });
      });
      list.addEventListener('click', e => {
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.country_iso = el.dataset.iso;
        state.country_name = el.dataset.name;
        list.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    async function renderUniversity() {
      const hdr = renderHeader(STEPS[1].title, `${STEPS[1].sub} — Country: ${state.country_name}`);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrUnivSearch" placeholder="e.g. Stanford, PUC, UTFSM…" autocomplete="off">
         <div class="mr-results" id="mrUnivList"><div style="padding:20px;color:#8B93A7;text-align:center;">Start typing to search</div></div>`;
      const searchEl = document.getElementById('mrUnivSearch');
      const listEl = document.getElementById('mrUnivList');
      let debounce;
      const doSearch = async () => {
        const q = searchEl.value.trim();
        const r = await fetch(`/api/academic/universities?country=${encodeURIComponent(state.country_iso)}&q=${encodeURIComponent(q)}`);
        const j = await r.json();
        const rows = j.universities || [];
        let html = rows.map(u =>
          `<div class="mr-result ${state.university_id===u.id?'selected':''}" data-id="${u.id}" data-name="${u.name.replace(/"/g,'&quot;')}">
             <span>${u.name}${u.short_name?' <span class="tag">'+u.short_name+'</span>':''}</span>
             ${u.status==='pending'?'<span class="tag">pending</span>':''}
           </div>`).join('');
        if (q.length >= 3) {
          html += `<div class="mr-create-new" id="mrCreateUniv">＋ Create "${q}"</div>`;
        }
        listEl.innerHTML = html || `<div style="padding:20px;color:#8B93A7;text-align:center;">No matches${q.length>=3?' — create above':''}</div>`;
      };
      searchEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(doSearch, 200); });
      listEl.addEventListener('click', async e => {
        const createEl = e.target.closest('#mrCreateUniv');
        if (createEl) {
          const name = searchEl.value.trim();
          const r = await fetch('/api/academic/universities', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ name, country_iso: state.country_iso })
          });
          const j = await r.json();
          if (j.ok && j.university) {
            state.university_id = j.university.id;
            state.university_name = j.university.name;
            doSearch();
          }
          return;
        }
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.university_id = parseInt(el.dataset.id, 10);
        state.university_name = el.dataset.name;
        listEl.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    async function renderMajor() {
      const hdr = renderHeader(STEPS[2].title, STEPS[2].sub);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrMajorSearch" placeholder="e.g. Computer Science, Medicine, Economics…" autocomplete="off">
         <div class="mr-results" id="mrMajorList"><div style="padding:20px;color:#8B93A7;text-align:center;">Start typing your major</div></div>`;
      const searchEl = document.getElementById('mrMajorSearch');
      const listEl = document.getElementById('mrMajorList');
      let debounce;
      const doSearch = async () => {
        const q = searchEl.value.trim();
        if (!q) { listEl.innerHTML = '<div style="padding:20px;color:#8B93A7;text-align:center;">Start typing</div>'; return; }
        const r = await fetch(`/api/academic/majors?q=${encodeURIComponent(q)}&university_id=${state.university_id||''}`);
        const j = await r.json();
        const rows = j.majors || [];
        let html = rows.map(m =>
          `<div class="mr-result ${state.major_id===m.id?'selected':''}" data-id="${m.id}" data-name="${m.name.replace(/"/g,'&quot;')}">
             <span>${m.name}</span>${m.university_id?'<span class="tag">univ-specific</span>':'<span class="tag">global</span>'}
           </div>`).join('');
        if (q.length >= 2) {
          html += `<div class="mr-create-new" id="mrCreateMajor">＋ Add "${q}" as new major</div>`;
        }
        listEl.innerHTML = html;
      };
      searchEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(doSearch, 200); });
      listEl.addEventListener('click', async e => {
        const createEl = e.target.closest('#mrCreateMajor');
        if (createEl) {
          const name = searchEl.value.trim();
          const r = await fetch('/api/academic/majors', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ name, university_id: state.university_id })
          });
          const j = await r.json();
          if (j.ok && j.major) {
            state.major_id = j.major.id;
            state.major_name = j.major.name;
            doSearch();
          }
          return;
        }
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.major_id = parseInt(el.dataset.id, 10);
        state.major_name = el.dataset.name;
        listEl.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    function renderCanvas() {
      const hdr = renderHeader(STEPS[3].title, STEPS[3].sub);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrCanvasUrl" placeholder="https://canvas.instructure.com (or your school's)" value="${state.canvas_url||''}">
         <input class="mr-input" id="mrCanvasToken" type="password" placeholder="Canvas personal API token" style="margin-top:10px;" value="${state.canvas_token||''}">
         <p style="margin:14px 0 0;font-size:12px;color:#8B93A7;line-height:1.55;">
           Generate a token in Canvas: <strong>Account → Settings → + New Access Token</strong>.
           Stored encrypted; revoke anytime in Canvas.
         </p>`;
      document.getElementById('mrCanvasUrl').addEventListener('input', e => state.canvas_url = e.target.value.trim());
      document.getElementById('mrCanvasToken').addEventListener('input', e => state.canvas_token = e.target.value.trim());
      nextBtn.textContent = 'Finish →';
    }

    function go(step) {
      state.step = Math.max(0, Math.min(STEPS.length - 1, step));
      stepDots.forEach((d, i) => d.classList.toggle('active', i <= state.step));
      backBtn.style.visibility = state.step === 0 ? 'hidden' : 'visible';
      nextBtn.textContent = state.step === STEPS.length - 1 ? 'Finish →' : 'Continue →';
      STEPS[state.step].render();
    }

    async function finish() {
      nextBtn.disabled = true;
      nextBtn.textContent = 'Saving…';
      try {
        const r = await fetch('/api/academic/profile', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            country_iso: state.country_iso,
            university_id: state.university_id,
            major_id: state.major_id,
            canvas_url: state.canvas_url,
            canvas_token: state.canvas_token,
          })
        });
        const j = await r.json();
        if (j.ok) {
          modal.style.display = 'none';
          document.body.style.overflow = '';
          // Reload to refresh any rank/league widgets
          setTimeout(() => mrReload(), 300);
        } else {
          nextBtn.disabled = false;
          nextBtn.textContent = 'Finish →';
          alert(j.error || 'Save failed');
        }
      } catch(e) {
        nextBtn.disabled = false;
        nextBtn.textContent = 'Finish →';
        mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.');
      }
    }

    nextBtn.addEventListener('click', () => {
      if (state.step === 0 && !state.country_iso) { alert('Select a country'); return; }
      if (state.step === 1 && !state.university_id) { alert('Select or create a university'); return; }
      if (state.step === 2 && !state.major_id) { alert('Select or add a major'); return; }
      if (state.step === STEPS.length - 1) { finish(); return; }
      go(state.step + 1);
    });
    backBtn.addEventListener('click', () => go(state.step - 1));

    // Block all shortcuts that would bypass the modal
    function blockKeys(e) {
      if (modal.style.display === 'flex' && (e.key === 'Escape')) { e.preventDefault(); e.stopPropagation(); }
    }
    document.addEventListener('keydown', blockKeys, true);

    // Always wire the close button up front so it works no matter what branch runs
    const bannerCloseBtn = document.getElementById('mrXpBannerClose');
    function hideBanner() {
      // Use setProperty + !important so nothing in the global stylesheet can
      // accidentally re-show the banner once the user dismisses it.
      banner.style.setProperty('display', 'none', 'important');
      banner.setAttribute('hidden', '');
      try { fetch('/api/academic/banner/seen', { method:'POST' }); } catch(_){}
    }
    if (bannerCloseBtn) {
      ['click','pointerup','touchend'].forEach(ev =>
        bannerCloseBtn.addEventListener(ev, function(e){ e.preventDefault(); e.stopPropagation(); hideBanner(); }, true)
      );
    }

    // Init: check whether we need to show the modal / banner
    async function init() {
      try {
        const r = await fetch('/api/academic/profile');
        if (!r.ok) return;
        const j = await r.json();
        // Priority 1: if setup isn't complete, show modal and hide banner entirely.
        if (j.needs_setup) {
          banner.style.display = 'none';
          modal.style.display = 'flex';
          document.body.style.overflow = 'hidden';
          go(0);
          return;
        }
        // Setup IS complete. Only show the 'previous progress preserved' banner if
        // the user has actual prior XP (i.e. a pre-existing account) and hasn't seen it.
        const hasPriorXp = (j.prior_xp || 0) > 0;
        if (hasPriorXp && !j.xp_preserve_banner_seen) {
          banner.style.display = 'flex';
          setTimeout(hideBanner, 8000);
        }
      } catch(_){}
    }
    init();
  })();
  </script>
  {% endif %}

</body>
</html>"""


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
    return render_template_string(
        LAYOUT,
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
        if session.get("account_type") == "student":
            return redirect(url_for("student_dashboard_page"))
        return redirect(url_for("dashboard"))

    lang = session.get("lang", "es")
    from student.landing_design import render_landing_page
    return make_response(render_landing_page(lang))

    is_es = (lang == "es")

    # ── i18n copy (en + es) ────────────────────────────────────
    if is_es:
        page_title = "MachReach — Estudia más inteligente. Domina cada día."
        hero_kicker = "ESTUDIO IMPULSADO POR IA  ·  TUTOR  ·  DUELOS  ·  MARKETPLACE"
        hero_h1_a = "Tu cerebro,"
        hero_h1_b = "supercargado."
        hero_sub = "MachReach es la suite de estudio con IA para estudiantes universitarios: generador de quizzes, flashcards, temporizador de enfoque, duelos, ligas y un marketplace para ganar monedas vendiendo tus apuntes."
        cta_primary = "Empieza gratis"
        cta_secondary = "Iniciar sesión"
        stats = [
            ("∞", "Quizzes con IA en planes pagos"),
            ("∞", "Marketplace de apuntes activo"),
            ("3", "Niveles: Free · Plus · Ultimate"),
            ("100%", "Hecho para estudiantes"),
        ]
        f_h = "Todo lo que necesitas para conquistar el semestre"
        f_sub = "Una sola app. Cero distracciones. Solo herramientas que funcionan."
        features = [
            ("&#128221;", "Quizzes infinitos", "Sube un PDF y genera quizzes ilimitados al instante. Plus/Ultimate sin límites diarios."),
            ("&#127919;", "Flashcards inteligentes", "Tarjetas de repaso con repetición espaciada. Dominas el material en menos tiempo."),
            ("&#9201;&#65039;", "Focus Timer + cursos", "Pomodoro con seguimiento por curso. Acumula racha, monedas y XP por cada sesión."),
            ("&#9876;&#65039;", "Duelos de Quiz", "Reta a tus amigos a duelos 1v1 con preguntas IA. Gana monedas, sube en la liga."),
            ("&#128722;", "Marketplace", "Sube tus apuntes con un precio en monedas. Otros estudiantes los compran y tú ganas."),
            ("&#127942;", "Ligas y badges", "Sube de rango cada temporada compitiendo con otros estudiantes en XP."),
            ("&#10024;", "Plan Ultimate", "Todo Plus + acceso gratis a TODO el marketplace + herramientas de correo."),
        ]
        how_h = "Cómo funciona en 3 pasos"
        how_steps = [
            ("1", "Crea tu cuenta gratis", "Sin tarjeta. Empieza a estudiar en menos de 30 segundos."),
            ("2", "Sube tus materiales", "Apuntes, PDFs, slides — la IA los entiende y te genera quizzes y resúmenes."),
            ("3", "Estudia, gana y compite", "Acumula XP, sube en la liga, gana monedas en duelos y compra apuntes en el marketplace."),
        ]
        plans_h = "Planes pensados para estudiantes"
        plans_sub = "Empieza gratis. Sube de plan cuando quieras desbloquear lo ilimitado."
        plans = [
            ("Free", "$0", "/ siempre", [
                "1 quiz IA al día (hasta 30 preguntas)",
                "1 set de flashcards al día (hasta 30)",
                "Focus timer, rachas, ligas, duelos",
                "Marketplace (comprar y vender)",
            ], "Empezar gratis", "/register", False),
            ("Plus", "$4.99", "/ mes", [
                "Quizzes y flashcards ILIMITADOS",
                "Sin límites por generación",
                "300 monedas extra cada mes",
                "Badge PLUS y cosméticos exclusivos",
                "Reportes y estadísticas avanzadas",
            ], "Probar Plus", "/register", True),
            ("Ultimate", "$9.99", "/ mes", [
                "Todo lo de Plus",
                "Marketplace 100% gratis (todos los archivos)",
                "Cosméticos y perks Ultimate",
                "Soporte prioritario",
            ], "Ir a Ultimate", "/register", False),
        ]
        final_h = "Tu semestre empieza ahora."
        final_sub = "Cero tarjeta. Cero compromiso. Solo herramientas que te ayudan a aprobar."
        final_cta = "Crear cuenta gratis"
    else:
        page_title = "MachReach — Study smarter. Win every day."
        hero_kicker = "AI-POWERED STUDY  ·  TUTOR  ·  DUELS  ·  MARKETPLACE"
        hero_h1_a = "Your brain,"
        hero_h1_b = "supercharged."
        hero_sub = "MachReach is the AI study suite built for college students: quiz & flashcard generator, focus timer, duels, leagues, and a marketplace where you earn coins selling your notes."
        cta_primary = "Start free"
        cta_secondary = "Log in"
        stats = [
            ("∞", "AI quizzes on paid plans"),
            ("∞", "Notes marketplace open 24/7"),
            ("3", "Tiers: Free · Plus · Ultimate"),
            ("100%", "Built for students"),
        ]
        f_h = "Everything you need to crush the semester"
        f_sub = "One app. Zero distractions. Tools that actually work."
        features = [
            ("&#128221;", "Unlimited Quizzes", "Drop a PDF and generate quizzes instantly. Plus/Ultimate get no daily caps."),
            ("&#127919;", "Smart Flashcards", "Spaced-repetition cards. Master the material in less time."),
            ("&#9201;&#65039;", "Focus Timer + Courses", "Pomodoro with per-course tracking. Build streaks, earn coins and XP."),
            ("&#9876;&#65039;", "Quiz Duels", "Challenge friends to AI-generated 1v1 duels. Win coins, climb the league."),
            ("&#128722;", "Marketplace", "List your notes for a coin price. Other students buy them and you cash in."),
            ("&#127942;", "Leagues & Badges", "Climb seasonal ranks competing for XP with other students."),
            ("&#10024;", "Ultimate plan", "Everything in Plus + FREE access to every marketplace file + mail tools."),
        ]
        how_h = "How it works in 3 steps"
        how_steps = [
            ("1", "Create a free account", "No card. Start studying in under 30 seconds."),
            ("2", "Drop your materials", "Notes, PDFs, slides — the AI reads them and generates quizzes & summaries."),
            ("3", "Study, earn, compete", "Stack XP, climb the league, win coins in duels, buy notes on the marketplace."),
        ]
        plans_h = "Plans built for students"
        plans_sub = "Start free. Upgrade when you want unlimited."
        plans = [
            ("Free", "$0", "/ forever", [
                "1 AI quiz / day (up to 30 questions)",
                "1 flashcard set / day (up to 30 cards)",
                "Focus timer, streaks, leagues, duels",
                "Marketplace (buy & sell)",
            ], "Start free", "/register", False),
            ("Plus", "$4.99", "/ month", [
                "UNLIMITED quizzes & flashcards",
                "No per-generation cap",
                "300 bonus coins every month",
                "PLUS badge & exclusive cosmetics",
                "Detailed analytics & reports",
            ], "Try Plus", "/register", True),
            ("Ultimate", "$9.99", "/ month", [
                "Everything in Plus",
                "Marketplace 100% FREE (every file)",
                "Ultimate cosmetics and perks",
                "Priority support",
            ], "Go Ultimate", "/register", False),
        ]
        final_h = "Your semester starts now."
        final_sub = "No card. No commitment. Just tools that help you pass."
        final_cta = "Create free account"

    # ── Build HTML blocks ──────────────────────────────────────
    stats_html = "".join(
        f'<div class="lp-stat"><div class="num">{n}</div><div class="lbl">{l}</div></div>'
        for (n, l) in stats
    )
    features_html = "".join(
        f'<div class="lp-card"><div class="icon">{i}</div><h3>{t}</h3><p>{p}</p></div>'
        for (i, t, p) in features
    )
    steps_html = "".join(
        f'<div class="lp-step"><div class="lp-step-n">{n}</div><h3>{t}</h3><p>{d}</p></div>'
        for (n, t, d) in how_steps
    )
    plans_html = ""
    for (name, price, period, feats, cta, href, highlight) in plans:
        feats_html = "".join(f'<li>&#10003; {f}</li>' for f in feats)
        cls = "lp-plan featured" if highlight else "lp-plan"
        ribbon = ('<div class="lp-plan-ribbon">' + ("MÁS POPULAR" if is_es else "MOST POPULAR") + '</div>') if highlight else ""
        plans_html += (
            f'<div class="{cls}">{ribbon}'
            f'<h3>{name}</h3>'
            f'<div class="lp-price"><span class="amt">{price}</span><span class="per">{period}</span></div>'
            f'<ul>{feats_html}</ul>'
            f'<a href="{href}" class="lp-plan-cta">{cta}</a>'
            f'</div>'
        )

    return render_template_string(LAYOUT, title=page_title, logged_in=False, messages=[], active_page="home", client_name="", nav=t_dict("nav"), lang=lang, wide=True, content=Markup(f"""
    <style>
      .lp-hero {{ position: relative; padding: 100px 24px 70px; text-align: center; overflow: hidden; }}
      .lp-hero::before {{ content: ''; position: absolute; inset: 0; background:
          radial-gradient(circle at 20% 20%, rgba(167,139,250,.18), transparent 45%),
          radial-gradient(circle at 80% 30%, rgba(244,114,182,.16), transparent 45%),
          radial-gradient(circle at 50% 100%, rgba(99,102,241,.18), transparent 50%);
        z-index: 0; }}
      .lp-kicker {{ position: relative; z-index: 1; display: inline-block; padding: 6px 14px; border-radius: 999px;
        background: rgba(99,102,241,.12); color: var(--primary); font-size: 11px; font-weight: 800; letter-spacing: 2px; margin-bottom: 22px; }}
      .lp-hero h1 {{ position: relative; z-index: 1; font-size: clamp(46px, 7vw, 80px); max-width: 980px; margin: 0 auto 22px;
        line-height: 1; font-weight: 900; letter-spacing: -2.4px; }}
      .lp-hero h1 .g1 {{ background: linear-gradient(90deg,#A78BFA,#6366F1); -webkit-background-clip: text; background-clip: text; color: transparent; }}
      .lp-hero h1 .g2 {{ background: linear-gradient(90deg,#F472B6,#F59E0B); -webkit-background-clip: text; background-clip: text; color: transparent; }}
      .lp-hero p.sub {{ position: relative; z-index: 1; max-width: 720px; margin: 0 auto 30px;
        color: var(--text-secondary); font-size: 18px; line-height: 1.6; }}
      .lp-cta {{ position: relative; z-index: 1; display: inline-flex; gap: 12px; flex-wrap: wrap; justify-content: center; }}
      .lp-btn {{ padding: 15px 30px; border-radius: 12px; font-weight: 700; font-size: 15px; text-decoration: none; display: inline-flex; align-items: center; gap: 10px;
        transition: transform .2s, box-shadow .2s, filter .2s; }}
      .lp-btn-primary {{ background: linear-gradient(135deg,#F472B6,#F59E0B); color: #fff; box-shadow: 0 6px 22px rgba(244,114,182,.32); }}
      .lp-btn-primary:hover {{ transform: translateY(-2px); filter: brightness(1.06); box-shadow: 0 10px 32px rgba(244,114,182,.42); }}
      .lp-btn-ghost {{ background: rgba(255,255,255,.04); color: var(--text); border: 1px solid var(--border); }}
      .lp-btn-ghost:hover {{ transform: translateY(-2px); border-color: var(--primary); }}


      .lp-stats {{ position: relative; z-index: 1; display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 24px;
        max-width: 960px; margin: 56px auto 0; padding: 0 24px; }}
      .lp-stat {{ text-align: center; }}
      .lp-stat .num {{ font-size: clamp(32px, 4vw, 46px); font-weight: 900;
        background: linear-gradient(135deg,#A78BFA,#F472B6); -webkit-background-clip: text; background-clip: text; color: transparent;
        letter-spacing: -1.2px; line-height: 1; }}
      .lp-stat .lbl {{ font-size: 11.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.6px; font-weight: 700; margin-top: 8px; }}

      .lp-section-title {{ text-align: center; font-size: 11.5px; font-weight: 800; letter-spacing: 2.2px;
        text-transform: uppercase; color: var(--primary); margin: 100px 0 10px; }}
      .lp-section-h {{ text-align: center; font-size: clamp(30px, 3.6vw, 42px); font-weight: 900; max-width: 760px; margin: 0 auto 16px;
        letter-spacing: -1px; line-height: 1.1; }}
      .lp-section-sub {{ text-align: center; color: var(--text-secondary); font-size: 16px; max-width: 620px; margin: 0 auto 36px; line-height: 1.6; }}

      .lp-cards {{ max-width: 1180px; margin: 0 auto; padding: 0 24px;
        display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }}
      .lp-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 22px;
        transition: transform .25s, border-color .25s, box-shadow .25s; }}
      .lp-card:hover {{ transform: translateY(-4px); border-color: var(--primary); box-shadow: 0 14px 32px rgba(99,102,241,.10); }}
      .lp-card .icon {{ font-size: 28px; width: 52px; height: 52px; border-radius: 12px;
        background: linear-gradient(135deg, rgba(244,114,182,.15), rgba(245,158,11,.15));
        display: inline-flex; align-items: center; justify-content: center; margin-bottom: 12px; }}
      .lp-card h3 {{ font-size: 17px; margin: 0 0 6px; font-weight: 800; letter-spacing: -.3px; }}
      .lp-card p {{ font-size: 14px; line-height: 1.55; color: var(--text-secondary); margin: 0; }}

      .lp-steps {{ max-width: 1100px; margin: 0 auto; padding: 0 24px;
        display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 22px; }}
      .lp-step {{ text-align: center; padding: 28px 22px; }}
      .lp-step-n {{ width: 52px; height: 52px; border-radius: 50%; margin: 0 auto 16px;
        background: linear-gradient(135deg,#A78BFA,#F472B6); color: #fff;
        font-size: 22px; font-weight: 900; display: inline-flex; align-items: center; justify-content: center;
        box-shadow: 0 8px 22px rgba(167,139,250,.32); }}
      .lp-step h3 {{ font-size: 18px; margin: 0 0 8px; font-weight: 800; }}
      .lp-step p {{ font-size: 14px; color: var(--text-secondary); line-height: 1.55; margin: 0; }}

      .lp-plans {{ max-width: 1100px; margin: 0 auto; padding: 0 24px;
        display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; align-items: stretch; }}
      .lp-plan {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 28px 24px;
        position: relative; display: flex; flex-direction: column; }}
      .lp-plan.featured {{ border-color: #F472B6; box-shadow: 0 14px 40px rgba(244,114,182,.18); transform: translateY(-6px); }}
      .lp-plan-ribbon {{ position: absolute; top: -12px; left: 50%; transform: translateX(-50%);
        background: linear-gradient(135deg,#F472B6,#F59E0B); color: #fff; font-size: 11px; font-weight: 800; letter-spacing: 1px;
        padding: 5px 14px; border-radius: 999px; }}
      .lp-plan h3 {{ font-size: 22px; font-weight: 900; margin: 0 0 4px; letter-spacing: -.4px; }}
      .lp-price {{ display: flex; align-items: baseline; gap: 6px; margin: 6px 0 16px; }}
      .lp-price .amt {{ font-size: 36px; font-weight: 900; letter-spacing: -1px; }}
      .lp-price .per {{ font-size: 13px; color: var(--text-muted); font-weight: 600; }}
      .lp-plan ul {{ list-style: none; padding: 0; margin: 0 0 22px; flex: 1; }}
      .lp-plan ul li {{ font-size: 14px; padding: 7px 0; color: var(--text-secondary); border-bottom: 1px dashed var(--border-light); }}
      .lp-plan-cta {{ display: block; text-align: center; padding: 14px; border-radius: 10px; background: var(--bg-elev);
        color: var(--text); font-weight: 700; text-decoration: none; transition: transform .2s, background .2s; }}
      .lp-plan.featured .lp-plan-cta {{ background: linear-gradient(135deg,#F472B6,#F59E0B); color: #fff; }}
      .lp-plan-cta:hover {{ transform: translateY(-2px); }}

      .lp-final {{ margin: 110px auto 60px; padding: 60px 24px; text-align: center; max-width: 900px;
        border-radius: 24px; background: linear-gradient(135deg, rgba(244,114,182,.10), rgba(99,102,241,.12));
        border: 1px solid var(--border); }}
      .lp-final h2 {{ font-size: clamp(34px, 4vw, 48px); font-weight: 900; margin: 0 0 12px; letter-spacing: -1px; }}
      .lp-final p {{ font-size: 17px; color: var(--text-secondary); margin: 0 0 24px; }}
    </style>

    <section class="lp-hero">
      <div class="lp-kicker">{hero_kicker}</div>
      <h1><span class="g1">{hero_h1_a}</span><br><span class="g2">{hero_h1_b}</span></h1>
      <p class="sub">{hero_sub}</p>
      <div class="lp-cta">
        <a href="/register" class="lp-btn lp-btn-primary">{cta_primary} &rarr;</a>
        <a href="/login" class="lp-btn lp-btn-ghost">{cta_secondary}</a>
      </div>
      <div class="lp-stats">{stats_html}</div>
    </section>

    <div class="lp-section-title">{("CARACTERÍSTICAS" if is_es else "FEATURES")}</div>
    <h2 class="lp-section-h">{f_h}</h2>
    <p class="lp-section-sub">{f_sub}</p>
    <div class="lp-cards">{features_html}</div>

    <div class="lp-section-title">{("CÓMO FUNCIONA" if is_es else "HOW IT WORKS")}</div>
    <h2 class="lp-section-h">{how_h}</h2>
    <div class="lp-steps">{steps_html}</div>

    <div class="lp-section-title">{("PRECIOS" if is_es else "PRICING")}</div>
    <h2 class="lp-section-h">{plans_h}</h2>
    <p class="lp-section-sub">{plans_sub}</p>
    <div class="lp-plans">{plans_html}</div>

    <section class="lp-final">
      <h2>{final_h}</h2>
      <p>{final_sub}</p>
      <a href="/register" class="lp-btn lp-btn-primary">{final_cta} &rarr;</a>
    </section>
    """))

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        business = ""
        account_type = "student"
        if not name or not email or not password:
            flash(("error", t("auth.all_required")))
            return redirect(url_for("register"))
        if get_client_by_email(email):
            _log_security("REGISTER_DUPLICATE", email=email)
            flash(("error", t("auth.email_exists")))
            return redirect(url_for("register"))
        client_id = create_client(name, email, _hash_pw(password), business, account_type)
        _log_security("REGISTER_OK", client_id=client_id, email=email)

        # Send verification email
        email_sent = False
        try:
            import secrets as _secrets
            from datetime import timedelta
            from outreach.config import BASE_URL as _base_url
            token = _secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            create_verification_token(client_id, token, expires)
            verify_link = f"{_base_url}/verify-email/{token}"
            body = (
                f"Hi {name},\n\n"
                f"Welcome to MachReach! Please verify your email address:\n\n"
                f"{verify_link}\n\n"
                f"This link expires in 24 hours.\n\n"
                f"— MachReach"
            )
            email_sent = _send_system_email(email, "MachReach — Verify Your Email", body)
        except Exception as e:
            import traceback
            print(f"[VERIFY] Verification flow failed for {email}: {e}", flush=True)
            traceback.print_exc()

        if email_sent:
            return redirect(url_for("verify_email_pending", email=email, created="1"))
        else:
            # Verification email failed — delete the account so it's not half-created
            try:
                from outreach.db import get_db, _exec
                with get_db() as db:
                    _exec(db, "DELETE FROM email_verification_tokens WHERE client_id = %s", (client_id,))
                    _exec(db, "DELETE FROM clients WHERE id = %s", (client_id,))
                print(f"[REGISTER] Rolled back account for {email} — verification email failed", flush=True)
            except Exception:
                pass
            flash(("error", "We couldn't send the verification email. Please check your email address and try again, or contact support@machreach.com."))
            return redirect(url_for("register"))
    return render_template_string(LAYOUT, title="Register", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="register", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.create_account")}</h1>
        <p class="subtitle">{t("auth.create_subtitle")}</p>
        <form method="post">
          <input type="hidden" name="account_type" value="student">
          <div class="form-group"><label>{t("auth.full_name")}</label><input name="name" placeholder="Alex Garcia" required></div>
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="you@school.edu" required></div>
          <div class="form-group"><label>{t("auth.password")}</label><input name="password" type="password" placeholder="At least 6 characters" required minlength="6"></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.create_btn")}</button>
          <p style="font-size:11px;color:var(--text-muted);text-align:center;margin-top:12px;line-height:1.6;">By creating an account, you agree to our <a href="/terms" style="color:var(--primary);">Terms of Service</a> and <a href="/privacy" style="color:var(--primary);">Privacy Policy</a>.</p>
        </form>
        <div class="auth-footer">{t("auth.have_account")} <a href="/login">{t("auth.log_in")}</a></div>
      </div>
    </div>
    """))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
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
        # Preserve team invite token across session clear
        pending_token = session.get("team_invite_token")
        session.clear()
        session["client_id"] = client["id"]
        session["client_name"] = client["name"]
        session["account_type"] = client.get("account_type") or "student"
        # Check for pending team invite
        if pending_token:
            return redirect(url_for("team_accept_invite", token=pending_token))
        return redirect(url_for("student_dashboard_page"))
    return render_template_string(LAYOUT, title="Login", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="login", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.welcome_back")}</h1>
        <p class="subtitle">{t("auth.sign_in_desc")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="you@school.edu" required></div>
          <div class="form-group"><label>{t("auth.password")}</label><input name="password" type="password" required></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.sign_in")}</button>
        </form>
        <div style="text-align:center;margin-top:12px;"><a href="/forgot-password" style="font-size:13px;color:var(--text-muted);">{t("auth.forgot_password")}</a></div>
        <details style="text-align:center;margin-top:8px;">
          <summary style="font-size:12px;color:var(--text-muted);cursor:pointer;list-style:none;">Didn't get verification email?</summary>
          <form method="post" action="/resend-verification" style="margin-top:8px;display:flex;gap:8px;justify-content:center;">
            <input name="email" type="email" placeholder="your@email.com" required style="font-size:12px;padding:6px 10px;max-width:200px;">
            <button class="btn btn-outline btn-sm" type="submit">Resend</button>
          </form>
        </details>
        <div class="auth-footer">{t("auth.no_account")} <a href="/register">{t("auth.sign_up_free")}</a></div>
      </div>
    </div>
    """))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/verify-email/<token>")
def verify_email(token):
    rec = get_valid_verification_token(token)
    if not rec:
        flash(("error", "Invalid or expired verification link. Please request a new one."))
        return redirect(url_for("login"))
    mark_email_verified(rec["client_id"])
    client = get_client(rec["client_id"])
    flash(("success", f"Email verified! Welcome, {_esc(client['name']) if client else ''}. You can now log in."))
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["POST"])
@limiter.limit("3 per minute")
def resend_verification():
    email = request.form.get("email", "").strip()
    client = get_client_by_email(email)
    if client and not client.get("email_verified"):
        import secrets as _secrets
        from outreach.config import BASE_URL as _base_url
        token = _secrets.token_urlsafe(32)
        expires = (datetime.now() + __import__("datetime").timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        create_verification_token(client["id"], token, expires)
        verify_link = f"{_base_url}/verify-email/{token}"
        body = f"Hi {client['name']},\n\nVerify your MachReach email:\n\n{verify_link}\n\nExpires in 24 hours.\n\n— MachReach"
        try:
            _send_system_email(email, "MachReach — Verify Your Email", body)
        except Exception:
            pass
    # If we know an email, take the user back to the dedicated pending page
    # so they land somewhere meaningful — the toast on /login was easy to miss.
    if email:
        return redirect(url_for("verify_email_pending", email=email, sent="1"))
    flash(("info", "If the email is registered, a new verification link has been sent."))
    return redirect(url_for("login"))


@app.route("/verify-email-pending")
def verify_email_pending():
    """Dedicated landing page for users whose email isn't verified yet.
    Replaces the easy-to-miss flash toast shown on /login — this is the full
    page, with the resend form front and center."""
    email = (request.args.get("email") or "").strip()
    just_created = request.args.get("created") == "1"
    just_sent = request.args.get("sent") == "1"
    safe_email = _esc(email)
    headline = ("&#127881; Account created!" if just_created
                else "&#128231; Verify your email to continue")
    sub = (
        "We just sent you a verification link. Click it and you're in."
        if just_created else
        "A new verification link is on its way — check your inbox."
        if just_sent else
        "Your email isn't verified yet. Click the link we sent to your inbox "
        "to log in. Can't find it? Resend below."
    )
    resend_notice = (
        '<div class="vep-flash">A new verification link has been sent. '
        'Check your inbox (and your spam folder).</div>' if just_sent else ""
    )
    return render_template_string(LAYOUT, title="Verify your email", logged_in=False,
        messages=list(session.pop("_flashes", []) if "_flashes" in session else []),
        active_page="login", client_name="", nav=t_dict("nav"),
        lang=session.get("lang", "es"),
        content=Markup(f"""
    <style>
      .vep-wrap {{
        max-width: 520px; margin: 48px auto; padding: 0 16px;
      }}
      .vep-card {{
        background: var(--card); border: 1px solid var(--border);
        border-radius: 16px; padding: 32px 28px;
        box-shadow: 0 8px 40px rgba(0,0,0,.08);
      }}
      .vep-icon {{
        width: 68px; height: 68px; margin: 0 auto 16px;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 32px;
        background: linear-gradient(135deg, #6366f1, #8b5cf6);
        color: #fff;
      }}
      .vep-card h1 {{
        text-align: center; font-size: 22px; margin: 0 0 8px;
      }}
      .vep-card .vep-sub {{
        text-align: center; color: var(--text-muted);
        font-size: 14px; line-height: 1.55; margin: 0 0 22px;
      }}
      .vep-email {{
        background: var(--bg); border: 1px solid var(--border);
        border-radius: 10px; padding: 10px 14px;
        font-size: 14px; text-align: center; color: var(--text);
        margin-bottom: 20px; word-break: break-all;
      }}
      .vep-steps {{
        background: var(--bg); border: 1px solid var(--border);
        border-radius: 10px; padding: 14px 16px; margin-bottom: 22px;
      }}
      .vep-steps ol {{ margin: 0; padding-left: 20px; font-size: 13px; color: var(--text-muted); line-height: 1.8; }}
      .vep-steps strong {{ color: var(--text); }}
      .vep-flash {{
        background: #d1fae5; color: #065f46;
        border: 1px solid #34d399; border-radius: 10px;
        padding: 10px 14px; font-size: 13px;
        margin-bottom: 18px; text-align: center;
      }}
      .vep-form {{ display: flex; gap: 8px; margin-bottom: 16px; }}
      .vep-form input {{
        flex: 1; padding: 10px 12px;
        border: 1px solid var(--border); border-radius: 10px;
        background: var(--bg); color: var(--text); font-size: 14px;
      }}
      .vep-form button {{
        padding: 10px 16px; border-radius: 10px;
        background: linear-gradient(135deg,#6366f1,#8b5cf6);
        color: #fff; font-weight: 600; font-size: 14px;
        border: none; cursor: pointer;
      }}
      .vep-foot {{ text-align: center; font-size: 13px; color: var(--text-muted); margin-top: 20px; }}
      .vep-foot a {{ color: var(--primary); font-weight: 600; }}
    </style>
    <div class="vep-wrap">
      <div class="vep-card">
        <div class="vep-icon">&#128231;</div>
        <h1>{headline}</h1>
        <p class="vep-sub">{sub}</p>
        {resend_notice}
        {(f'<div class="vep-email">Sent to <strong>{safe_email}</strong></div>' if email else '')}
        <div class="vep-steps">
          <ol>
            <li>Open the email from <strong>MachReach</strong></li>
            <li>Click <strong>Verify email</strong></li>
            <li>Log in and start studying.</li>
          </ol>
        </div>
        <form method="post" action="/resend-verification" class="vep-form">
          <input name="email" type="email" placeholder="your@email.com"
                 value="{safe_email}" required>
          <button type="submit">Resend link</button>
        </form>
        <div class="vep-foot">
          Already verified? <a href="/login">Log in</a>
        </div>
      </div>
    </div>
    """))


@app.route("/set-language/<lang>")
def set_language(lang):
    if lang in ("en", "es"):
        session["lang"] = lang
    return redirect(request.referrer or url_for("index"))


# ---------------------------------------------------------------------------
# Routes — Forgot / Reset Password
# ---------------------------------------------------------------------------

@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per minute", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        client = get_client_by_email(email)
        if client:
            import secrets
            from datetime import datetime, timedelta
            token = secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            create_reset_token(client["id"], token, expires)
            from outreach.config import BASE_URL
            reset_link = f"{BASE_URL}/reset-password/{token}"
            body = f"Click here to reset your MachReach password:\n\n{reset_link}\n\nThis link expires in 1 hour.\n\nIf you didn't request this, ignore this email."
            try:
                _send_system_email(email, "MachReach — Password Reset", body)
            except Exception:
                pass  # Don't reveal whether email was sent
        # Always show same message to prevent email enumeration
        flash(("success", t("auth.reset_sent")))
        return redirect(url_for("forgot_password"))
    return render_template_string(LAYOUT, title="Forgot Password", logged_in=False,
        messages=list(session.pop("_flashes", []) if "_flashes" in session else []),
        active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"),
        content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.reset_title")}</h1>
        <p class="subtitle">{t("auth.reset_desc")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="you@school.edu" required></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.send_reset")}</button>
        </form>
        <div class="auth-footer"><a href="/login">{t("auth.log_in")}</a></div>
      </div>
    </div>
    """))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def reset_password(token):
    reset = get_valid_reset_token(token)
    if not reset:
        flash(("error", t("auth.reset_invalid")))
        return redirect(url_for("login"))
    if request.method == "POST":
        pw1 = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if pw1 != pw2:
            flash(("error", t("auth.passwords_no_match")))
            return redirect(f"/reset-password/{token}")
        if len(pw1) < 6:
            flash(("error", t("auth.all_required")))
            return redirect(f"/reset-password/{token}")
        update_client_password(reset["client_id"], _hash_pw(pw1))
        mark_reset_token_used(token)
        _log_security("PASSWORD_RESET_OK", client_id=reset["client_id"])
        flash(("success", t("auth.reset_success")))
        return redirect(url_for("login"))
    return render_template_string(LAYOUT, title="Reset Password", logged_in=False,
        messages=list(session.pop("_flashes", []) if "_flashes" in session else []),
        active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "es"),
        content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.reset_btn")}</h1>
        <form method="post">
          <div class="form-group"><label>{t("auth.new_password")}</label><input name="password" type="password" placeholder="At least 6 characters" required minlength="6"></div>
          <div class="form-group"><label>{t("auth.confirm_password")}</label><input name="password2" type="password" required minlength="6"></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.reset_btn")}</button>
        </form>
      </div>
    </div>
    """))


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
    if len(new_pw) < 6:
        flash(("error", t("auth.all_required")))
        return redirect(url_for(redir))
    update_client_password(session["client_id"], _hash_pw(new_pw))
    _log_security("PASSWORD_CHANGE_OK", client_id=session["client_id"])
    flash(("success", t("settings.password_updated")))
    return redirect(url_for(redir))


# ---------------------------------------------------------------------------
# Routes — Delete Account
# ---------------------------------------------------------------------------

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
    from outreach.db import get_db, _exec
    with get_db() as db:
        # Student data (flashcards & quiz_questions cascade-delete via their parent tables)
        for tbl in ["student_chat_messages", "student_quizzes",
                     "student_flashcard_decks", "student_notes",
                     "student_course_files", "student_exams", "student_study_progress",
                     "student_study_plans", "student_assignment_progress",
                     "student_schedule_settings", "student_youtube_imports",
                     "student_xp", "student_badges", "student_email_prefs",
                     "student_canvas_tokens", "student_courses"]:
            try:
                _exec(db, f"DELETE FROM {tbl} WHERE client_id = %s", (client_id,))
            except Exception:
                pass
        # Business data
        for tbl2 in ["password_reset_tokens", "email_verification_tokens",
                      "email_accounts", "subscriptions", "usage_tracking"]:
            try:
                _exec(db, f"DELETE FROM {tbl2} WHERE client_id = %s", (client_id,))
            except Exception:
                pass
        try:
            _exec(db, "DELETE FROM team_members WHERE owner_id = %s OR member_client_id = %s", (client_id, client_id))
        except Exception:
            pass
        # Delete campaigns and related data
        try:
            camp_ids = [r["id"] for r in _exec(db, "SELECT id FROM campaigns WHERE client_id = %s", (client_id,)).fetchall()]
            for cid in camp_ids:
                contact_ids = [r["id"] for r in _exec(db, "SELECT id FROM contacts WHERE campaign_id = %s", (cid,)).fetchall()]
                for ct_id in contact_ids:
                    _exec(db, "DELETE FROM sent_emails WHERE contact_id = %s", (ct_id,))
                _exec(db, "DELETE FROM email_sequences WHERE campaign_id = %s", (cid,))
                _exec(db, "DELETE FROM contacts WHERE campaign_id = %s", (cid,))
            _exec(db, "DELETE FROM campaigns WHERE client_id = %s", (client_id,))
        except Exception:
            pass
        for tbl3 in ["contacts_book", "mail_inbox", "scheduled_emails"]:
            try:
                _exec(db, f"DELETE FROM {tbl3} WHERE client_id = %s", (client_id,))
            except Exception:
                pass
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
    from outreach.db import get_db, _exec, _fetchall, _fetchone, _USE_PG

    target = get_client(client_id)
    if not target:
        return {"ok": False, "error": "User not found."}
    protected_admins = {e.strip().lower() for e in ADMIN_EMAILS}
    protected_admins.add("ignaciomachuca2005@gmail.com")
    if (target.get("email") or "").strip().lower() in protected_admins:
        return {"ok": False, "error": "The owner admin account cannot be deleted from the panel."}

    deleted_steps = []
    with get_db() as db:
        for column in ("owner_id", "member_client_id"):
            try:
                _exec(db, f"DELETE FROM team_members WHERE {column} = %s", (client_id,))
            except Exception:
                pass

        campaigns = _fetchall(db, "SELECT id FROM campaigns WHERE client_id = %s", (client_id,))
        for campaign in campaigns:
            campaign_id = campaign["id"]
            contacts = _fetchall(db, "SELECT id FROM contacts WHERE campaign_id = %s", (campaign_id,))
            for contact in contacts:
                _exec(db, "DELETE FROM sent_emails WHERE contact_id = %s", (contact["id"],))
            _exec(db, "DELETE FROM email_sequences WHERE campaign_id = %s", (campaign_id,))
            _exec(db, "DELETE FROM contacts WHERE campaign_id = %s", (campaign_id,))
        _exec(db, "DELETE FROM campaigns WHERE client_id = %s", (client_id,))
        deleted_steps.append("campaign data")

        for table, column in [
            ("email_verification_tokens", "client_id"),
            ("password_reset_tokens", "client_id"),
            ("contacts_book", "client_id"),
            ("mail_inbox", "client_id"),
            ("scheduled_emails", "client_id"),
            ("email_accounts", "client_id"),
            ("usage_tracking", "client_id"),
            ("subscriptions", "client_id"),
            ("email_suppressions", "client_id"),
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
                if table == "clients":
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

    from outreach.db import get_all_client_emails

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
                error_msg = "Admin action secret is incorrect."
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
                error_msg = "Admin action secret is incorrect."
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
          <td style="font-family:monospace;font-size:13px;">{_esc(u.get("email") or "")}</td>
          <td>{_esc(str(u.get("id") or ""))}</td>
          <td style="width:320px;">
            <form method="POST" style="display:grid;grid-template-columns:1fr 120px auto;gap:8px;align-items:center;">
              <input type="hidden" name="action" value="delete_user">
              <input type="hidden" name="client_id" value="{_esc(str(u.get("id") or ""))}">
              <input name="confirm_email" placeholder="Type email to delete" autocomplete="off" style="font-size:12px;padding:8px;">
              <input name="confirm_phrase" placeholder="DELETE USER" autocomplete="off" style="font-size:12px;padding:8px;">
              {'<input name="admin_secret" placeholder="Admin secret" autocomplete="off" style="font-size:12px;padding:8px;grid-column:1 / -1;">' if ADMIN_ACTION_SECRET else ''}
              <button class="btn btn-outline btn-sm" style="color:var(--red);border-color:var(--red);" onclick="return confirm('Delete this user and their data?')">Delete</button>
            </form>
          </td>
        </tr>
        """
        for u in users
    )

    return _render("Admin", f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / Admin</div>
    <div class="page-header">
      <h1>&#128227; Admin</h1>
      <p class="subtitle">Broadcast to users and manage accounts. Admin access comes from configured admin emails and is audited.</p>
    </div>
    <div style="margin-bottom:16px;display:flex;gap:8px;flex-wrap:wrap;">
      <a class="btn btn-primary btn-sm" href="/admin/analytics">&#128202; Analytics de producto</a>
      <a class="btn btn-outline btn-sm" href="/admin/leaderboard-winners-test">&#127942; Preview monthly leaderboard winners email</a>
    </div>
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
        {'<div class="form-group"><label>Admin action secret</label><input name="admin_secret" type="password" autocomplete="off" required style="font-size:15px;"></div>' if ADMIN_ACTION_SECRET else ''}
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
    """, active_page="admin", wide=True)


_ANALYTICS_TABLE_READY = False


def _ensure_product_analytics_table():
    """Create lightweight product analytics storage lazily."""
    global _ANALYTICS_TABLE_READY
    if _ANALYTICS_TABLE_READY:
        return
    try:
        from outreach.db import get_db, _USE_PG, _exec
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
        from outreach.db import get_db, _exec
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
    emails = {str(e).strip().lower() for e in (ADMIN_EMAILS or set()) if str(e).strip()}
    emails.add("ignaciomachuca2005@gmail.com")
    quoted = ",".join("'" + email.replace("'", "''") + "'" for email in sorted(emails))
    return f"(client_id IS NULL OR client_id NOT IN (SELECT id FROM clients WHERE LOWER(email) IN ({quoted})))"


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
        ("/student/essay", "view_essays"),
        ("/student/leaderboard", "view_leaderboard"),
        ("/student/marketplace", "view_marketplace"),
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
        ("/api/student/essay", "essay_action"),
        ("/student/essay", "essay_action"),
        ("/api/student/marketplace", "marketplace_action"),
        ("/student/marketplace", "marketplace_action"),
        ("/api/student/canvas", "canvas_action"),
    ]
    for prefix, event in feature_map:
        if path.startswith(prefix):
            _record_product_event(event)
            return


def _admin_metric(sql_pg: str, sql_lite: str | None = None, params=()) -> int:
    try:
        from outreach.db import get_db, _USE_PG, _fetchval
        with get_db() as db:
            return int(_fetchval(db, sql_pg if _USE_PG else (sql_lite or sql_pg), params) or 0)
    except Exception:
        return 0


def _admin_rows(sql_pg: str, sql_lite: str | None = None, params=()) -> list[dict]:
    try:
        from outreach.db import get_db, _USE_PG, _fetchall
        with get_db() as db:
            return _fetchall(db, sql_pg if _USE_PG else (sql_lite or sql_pg), params) or []
    except Exception:
        return []


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

    event_labels = {
        "view_dashboard": "Inicio",
        "view_focus": "Modo enfoque",
        "view_analytics": "Analytics estudiante",
        "view_courses": "Cursos",
        "view_quizzes": "Quizzes",
        "view_flashcards": "Tarjetas",
        "view_essays": "Ensayos",
        "view_leaderboard": "Ranking",
        "view_marketplace": "Mercado",
        "view_shop": "Tienda",
        "view_canvas": "Canvas",
        "view_grades": "Planilla de notas",
        "view_profile": "Perfil",
        "focus_session_saved": "Sesion de enfoque guardada",
        "quiz_action": "Acciones de quiz",
        "flashcard_action": "Acciones de tarjetas",
        "essay_action": "Acciones de ensayo",
        "marketplace_action": "Acciones de mercado",
        "canvas_action": "Acciones de Canvas",
    }

    cards = [
        ("Visitas hoy", _admin_metric(f"SELECT COUNT(*) FROM product_analytics_events WHERE event_type='page_view' AND {today_pg} AND {external_events}", f"SELECT COUNT(*) FROM product_analytics_events WHERE event_type='page_view' AND {today_lite} AND {external_events}")),
        ("Usuarios únicos hoy", _admin_metric(f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {today_pg} AND {external_events}", f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {today_lite} AND {external_events}")),
        ("Registros hoy", _admin_metric(f"SELECT COUNT(*) FROM clients WHERE created_at >= CURRENT_DATE", "SELECT COUNT(*) FROM clients WHERE date(created_at)=date('now','localtime')")),
        ("Focus min hoy", _admin_metric("SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE plan_date::date = CURRENT_DATE", "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE date(plan_date)=date('now','localtime')")),
        ("XP hoy", _admin_metric(f"SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE {today_pg}", f"SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE {today_lite}")),
        ("Quizzes hoy", _admin_metric(f"SELECT COUNT(*) FROM student_quizzes WHERE {today_pg}", f"SELECT COUNT(*) FROM student_quizzes WHERE {today_lite}")),
        ("Mazos hoy", _admin_metric(f"SELECT COUNT(*) FROM student_flashcard_decks WHERE {today_pg}", f"SELECT COUNT(*) FROM student_flashcard_decks WHERE {today_lite}")),
        ("Tarjetas hoy", _admin_metric(f"SELECT COUNT(*) FROM student_flashcards WHERE {today_pg}", f"SELECT COUNT(*) FROM student_flashcards WHERE {today_lite}")),
        ("Apuntes/ensayos hoy", _admin_metric(f"SELECT COUNT(*) FROM student_notes WHERE {today_pg}", f"SELECT COUNT(*) FROM student_notes WHERE {today_lite}")),
        ("Mercado ventas hoy", _admin_metric(f"SELECT COUNT(*) FROM student_marketplace_purchases WHERE {today_pg}", f"SELECT COUNT(*) FROM student_marketplace_purchases WHERE {today_lite}")),
        ("Usuarios totales", _admin_metric("SELECT COUNT(*) FROM clients WHERE COALESCE(account_type,'student')='student'", "SELECT COUNT(*) FROM clients WHERE COALESCE(account_type,'student')='student'")),
        ("Activos 7 días", _admin_metric(f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {week_pg} AND {external_events}", f"SELECT COUNT(DISTINCT client_id) FROM product_analytics_events WHERE client_id IS NOT NULL AND {week_lite} AND {external_events}")),
    ]
    card_html = "".join(
        f"<div class='admin-metric'><div class='k'>{_esc(label)}</div><div class='v'>{value:,}</div></div>"
        for label, value in cards
    )

    top_pages = _admin_rows(
        f"SELECT path, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND {week_pg} AND {external_events} GROUP BY path ORDER BY n DESC LIMIT 12",
        f"SELECT path, COUNT(*) AS n FROM product_analytics_events WHERE event_type='page_view' AND {week_lite} AND {external_events} GROUP BY path ORDER BY n DESC LIMIT 12",
    )
    feature_rows = _admin_rows(
        f"SELECT event_type, COUNT(*) AS n, COUNT(DISTINCT client_id) AS users FROM product_analytics_events WHERE event_type <> 'page_view' AND {week_pg} AND {external_events} GROUP BY event_type ORDER BY n DESC LIMIT 20",
        f"SELECT event_type, COUNT(*) AS n, COUNT(DISTINCT client_id) AS users FROM product_analytics_events WHERE event_type <> 'page_view' AND {week_lite} AND {external_events} GROUP BY event_type ORDER BY n DESC LIMIT 20",
    )
    for row in feature_rows:
        row["label"] = event_labels.get(str(row.get("event_type") or ""), str(row.get("event_type") or ""))
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
        + bar_chart("Páginas más vistas · 7 días", top_pages, "path", "n", "#1A1A1F")
        + "</div>"
    )

    body = f"""
    <style>
      .admin-analytics {{ display:grid; gap:18px; }}
      .admin-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }}
      .admin-metric {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:16px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-metric .k {{ color:#77756F; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
      .admin-metric .v {{ font-family:Fraunces,Georgia,serif; font-size:34px; font-weight:650; margin-top:8px; color:#1A1A1F; }}
      .admin-panel {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
      .admin-panel h2 {{ margin:0 0 12px; font-family:Fraunces,Georgia,serif; font-size:25px; }}
      .admin-empty {{ color:#94939C; background:#FBF8F0; border:1px dashed #D8D0BE; border-radius:14px; padding:16px; }}
      .admin-chart-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
      .admin-chart {{ background:#fff; border:1px solid #E2DCCC; border-radius:18px; padding:18px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04); min-width:0; }}
      .admin-chart h2 {{ margin:0 0 12px; font-family:Fraunces,Georgia,serif; font-size:24px; font-weight:650; color:#1A1A1F; }}
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
      <div class="breadcrumb"><a href="/admin">Admin</a> / Analytics</div>
      <div class="page-header"><h1>&#128202; Analytics de producto</h1><p class="subtitle">Tráfico, uso de IA, estudio real y señales para decidir qué merece ser Plus o Ultimate.</p></div>
      <div class="admin-grid">{card_html}</div>
      {charts_html}
      <div class="admin-panel"><h2>Páginas más vistas · 7 días</h2>{table(["Ruta","Visitas"], top_pages, ["path","n"])}</div>
      <div class="admin-panel"><h2>Eventos de producto · 7 días</h2>{table(["Evento","Acciones","Usuarios"], feature_rows, ["label","n","users"])}</div>
      <div class="admin-panel"><h2>XP por fuente · 30 días</h2>{table(["Acción","Eventos","XP"], xp_rows, ["action","n","xp"])}</div>
    </div>
    """
    return _render("Admin analytics", body, active_page="admin", wide=True)


@app.route("/admin/leaderboard-winners-test")
def admin_leaderboard_winners_test():
    """Admin preview / dry-run for the monthly leaderboard winners email.

    Query params:
        month=YYYY-MM   — calendar month to preview (defaults to last month)
        send=1          — actually send the email to the configured recipient
                          instead of just rendering the preview
    """
    if not _is_admin():
        return redirect(url_for("dashboard"))

    from datetime import date
    from student.academic import monthly_winners

    raw = (request.args.get("month") or "").strip()
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

    if (request.args.get("send") or "").strip() in ("1", "true", "yes"):
        from worker import send_monthly_leaderboard_email, LEADERBOARD_WINNERS_RECIPIENT
        send_monthly_leaderboard_email(year=year, month=month)
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
        f"<div style='color:var(--text-muted);font-size:13px;margin-bottom:18px;'>"
        f"Period: {data['start']} → {data['end_exclusive']} (exclusive)</div>",
        summary_card,
        # Global leaderboard intentionally hidden while only Chile is active.
    ]

    def _section(title, groups):
        parts = [f"<div class='card' style='padding:16px;margin-bottom:14px;'>",
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
        f"<a class='btn btn-secondary btn-sm' href='?month={year:04d}-{month:02d}&send=1' "
        f"onclick=\"return confirm('Send the {year:04d}-{month:02d} email to the recipient now?');\" "
        f"style='margin-left:auto;'>📤 Send email for {year:04d}-{month:02d}</a>"
        f"</div>"
    )

    body = nav + "".join(sections)
    return _render("Monthly leaderboard winners", body, active_page="admin", wide=True)


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not _logged_in():
        return redirect(url_for("login"))
    return redirect("/student/settings")

# ---------------------------------------------------------------------------
# Google Calendar — OAuth + API (shared by student + business)
# ---------------------------------------------------------------------------
from outreach import gcal as _gcal


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

def _trigger_campaign_send(campaign_id):
    """Kick off a background thread to send pending emails for this campaign immediately."""
    job = _campaign_sends.get(campaign_id)
    if job and job.get("status") == "sending":
        return  # already running

    _campaign_sends[campaign_id] = {"status": "sending", "sent": 0, "total": 0}

    def _bg_send():
        import time as _time
        from outreach.db import get_db, record_sent, delete_sent_email, check_limit, increment_usage, get_default_email_account, _exec, _fetchone, _now_expr
        from outreach.ai import personalize_email, personalize_subject, translate_email
        from outreach.config import DELAY_BETWEEN_EMAILS_SEC, SENDER_NAME
        from outreach.sender import pick_variant, send_email

        try:
            with get_db() as db:
                rows = _exec(db, f"""
                    SELECT c.id as contact_id, c.name, c.email, c.company, c.role,
                           c.language, c.campaign_id,
                           es.id as sequence_id, es.subject_a, es.subject_b,
                           es.body_a, es.body_b, es.step
                    FROM contacts c
                    JOIN campaigns camp ON c.campaign_id = camp.id
                    JOIN email_sequences es ON es.campaign_id = camp.id AND es.step = 1
                    WHERE camp.id = %s AND camp.status = 'active'
                      AND c.status = 'pending'
                      AND c.id NOT IN (SELECT contact_id FROM sent_emails)
                      AND (camp.scheduled_start IS NULL OR camp.scheduled_start <= {_now_expr()})
                    LIMIT 30
                """, (campaign_id,)).fetchall()
                batch = [dict(r) for r in rows]

                # Get client_id
                camp_row = _fetchone(db, "SELECT client_id FROM campaigns WHERE id = %s", (campaign_id,))
                client_id = camp_row["client_id"] if camp_row else None

            # Resolve SMTP credentials and physical address from user's account
            acct_smtp = {}
            _physical_address = ""
            if client_id:
                acct = get_default_email_account(client_id)
                if acct:
                    acct_smtp = {
                        "smtp_host": acct["smtp_host"],
                        "smtp_port": acct["smtp_port"],
                        "smtp_user": acct["email"],
                        "smtp_password": acct["password"],
                        "from_name": acct.get("label", "") or "",
                    }
                _client = get_client(client_id)
                if _client:
                    _physical_address = _client.get("physical_address", "")

            _campaign_sends[campaign_id]["total"] = len(batch)

            for item in batch:
                # Check campaign still active
                with get_db() as db:
                    st = _fetchone(db, "SELECT status FROM campaigns WHERE id = %s", (campaign_id,))
                    if not st or st["status"] != "active":
                        break

                # Check limits
                if client_id:
                    allowed, used, limit = check_limit(client_id, "emails_sent")
                    if not allowed:
                        break

                variant = pick_variant()
                if variant == "b" and item.get("subject_b"):
                    subject = item["subject_b"]
                    body = item.get("body_b") or item["body_a"]
                else:
                    variant = "a"
                    subject = item["subject_a"]
                    body = item["body_a"]

                contact = {"name": item["name"], "company": item["company"], "role": item["role"]}
                subject = personalize_subject(subject, contact, SENDER_NAME)
                body = personalize_email(body, contact, SENDER_NAME)

                lang = item.get("language", "en")
                if lang and lang.lower() not in ("en", "english"):
                    try:
                        subject, body = translate_email(subject, body, lang)
                    except Exception:
                        pass

                sent_id = record_sent(
                    contact_id=item["contact_id"], sequence_id=item["sequence_id"],
                    variant=variant, subject=subject, body=body,
                )
                success = send_email(
                    to_email=item["email"], subject=subject, body_text=body,
                    contact_id=item["contact_id"], tracking_id=sent_id,
                    physical_address=_physical_address,
                    **acct_smtp,
                )

                if success:
                    _campaign_sends[campaign_id]["sent"] += 1
                    if client_id:
                        try:
                            increment_usage(client_id, "emails_sent")
                        except Exception:
                            pass
                else:
                    delete_sent_email(sent_id, item["contact_id"])

                _time.sleep(DELAY_BETWEEN_EMAILS_SEC)

        except Exception as e:
            print(f"[CAMPAIGN SEND] Error for campaign {campaign_id}: {e}")
        finally:
            _campaign_sends[campaign_id]["status"] = "done"

    threading.Thread(target=_bg_send, daemon=True).start()


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
# API — Mail Hub
# ---------------------------------------------------------------------------

# In-memory sync job tracker
import threading
_sync_jobs: dict[int, dict] = {}  # client_id -> {status, new_emails, error}
_campaign_sends: dict[int, dict] = {}  # campaign_id -> {status, sent, total}

# ---------------------------------------------------------------------------
# API — Mail Hub: AI draft & send reply
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Contacts Book
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Billing (Lemon Squeezy hosted checkout)
# ---------------------------------------------------------------------------

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
    """Single webhook for outreach subs, student PLUS subs, and coin packs.

    Routing is done via `meta.custom_data.purpose`:
      - "outreach_sub"  -> outreach subscription event (plan = growth/pro/unlimited)
      - "student_sub"   -> student PLUS/Ultimate subscription event (tier)
      - "coin_pack"     -> one-time coin-pack purchase (pack_key)
    """
    import json as _json
    from outreach import lemonsqueezy as ls

    raw = request.get_data() or b""
    sig = request.headers.get("X-Signature", "") or request.headers.get("x-signature", "")
    if not ls.verify_webhook(raw, sig):
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

    purpose = str(custom.get("purpose") or "")
    try:
        cid = int(custom.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0

    if not cid:
        _log.warning("[LS] webhook missing client_id in custom_data: %s", event_name)
        return "ok", 200  # ack so LS doesn't retry forever

    _log.info("[LS] webhook %s purpose=%s client=%s", event_name, purpose, cid)

    # ── Outreach SaaS subscription ─────────────────────────────────
    if purpose == "outreach_sub":
        from outreach.db import update_subscription, get_subscription_by_stripe_sub
        sub_id = str(data.get("id") or "")
        plan = str(custom.get("plan") or "")
        if event_name == "subscription_created" and plan in ("growth", "pro", "unlimited"):
            update_subscription(cid, plan=plan, stripe_subscription_id=sub_id, status="active")
        elif event_name == "subscription_updated":
            status = (attrs.get("status") or "").lower()
            mapped = {"active": "active", "on_trial": "active", "paused": "past_due",
                      "past_due": "past_due", "unpaid": "past_due",
                      "cancelled": "canceled", "expired": "canceled"}.get(status, "active")
            update_subscription(cid, status=mapped)
        elif event_name in ("subscription_cancelled", "subscription_expired"):
            rec = get_subscription_by_stripe_sub(sub_id)
            target_cid = (rec or {}).get("client_id") or cid
            update_subscription(target_cid, plan="free", stripe_subscription_id="", status="active")
        elif event_name == "subscription_payment_success":
            update_subscription(cid, status="active")
        elif event_name == "subscription_payment_failed":
            update_subscription(cid, status="past_due")
        return "ok", 200

    # ── Student PLUS / Ultimate subscription ───────────────────────
    if purpose == "student_sub":
        try:
            from student import subscription as ssub
        except Exception:
            ssub = None
        tier = str(custom.get("tier") or "plus").lower()
        if tier not in ("plus", "ultimate"):
            tier = "plus"
        if not ssub:
            return "ok", 200
        sub_id = str(data.get("id") or "")
        if event_name == "subscription_created":
            ssub.set_tier(cid, tier)
            # Stash the LS subscription id so we can cancel it later.
            try:
                from outreach.db import get_db, _fetchone, _exec
                import json as _json
                with get_db() as db:
                    row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (cid,))
                    prefs = {}
                    try:
                        prefs = _json.loads((row or {}).get("mail_preferences") or "{}")
                    except Exception:
                        prefs = {}
                    sub = prefs.get("subscription") or {}
                    sub["ls_sub_id"] = sub_id
                    prefs["subscription"] = sub
                    _exec(db, "UPDATE clients SET mail_preferences = %s WHERE id = %s", (_json.dumps(prefs), cid))
            except Exception:
                _log.exception("[LS] could not persist ls_sub_id for student %s", cid)
        elif event_name in ("subscription_cancelled", "subscription_expired"):
            ssub.set_tier(cid, "free")
        # payment_success / payment_failed don't change the tier (LS already
        # toggles subscription status which we mirror on the next update).
        return "ok", 200

    # ── One-time coin-pack purchase ────────────────────────────────
    if purpose == "coin_pack":
        if event_name != "order_created":
            return "ok", 200  # we only credit on the initial order
        pack_key = str(custom.get("pack_key") or "")
        if not pack_key:
            return "ok", 200
        try:
            from student import db as sdb
            sdb.credit_coin_pack(cid, pack_key)
        except Exception as e:
            _log.exception("[LS] coin-pack credit failed: %s", e)
        return "ok", 200

    _log.info("[LS] webhook unknown purpose=%r event=%s", purpose, event_name)
    return "ok", 200
# ---------------------------------------------------------------------------
# Focus Guard (browser extension) download
# ---------------------------------------------------------------------------

@app.route("/download/focus-guard.zip")
def download_focus_guard():
    """Ship the Focus Guard Chrome extension as a zip the user can load-unpack."""
    import io, os, zipfile
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

@app.route("/privacy")
def privacy_page():
    return _render("Privacy Policy", Markup("""
    <div style="max-width:800px;margin:0 auto;padding:40px 20px;">
      <h1 style="font-size:32px;margin-bottom:8px;">Privacy Policy</h1>
      <p style="color:var(--text-muted);margin-bottom:32px;">Last updated: April 28, 2026</p>
      <div style="line-height:1.8;color:var(--text-secondary);font-size:15px;">
        <p style="background:rgba(139,92,246,.08);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:24px;"><strong>Plain-English summary:</strong> MachReach is focused on student study tools. We collect only what those tools need, we never sell your data, passwords are hashed with bcrypt, and you can export or delete your data from Settings.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">1. Information We Collect</h2>
        <p><strong>Account information:</strong> name, email address, and password hashed with bcrypt.</p>
        <p><strong>Canvas LMS data:</strong> if you connect Canvas, we fetch your courses, assignments, and exam dates. Your Canvas access token is encrypted at rest and can be disconnected in Settings.</p>
        <p><strong>Study materials:</strong> PDFs, DOCX files, notes, and text you provide for flashcards, quizzes, AI notes, and the AI tutor.</p>
        <p><strong>Gamification data:</strong> XP events, study streaks, badges, quiz scores, flashcard reviews, focus sessions, leaderboard rank, and in-app coin activity.</p>
        <p><strong>Focus Guard extension:</strong> blocklists and active-session state are stored locally in your browser and are not sent to our servers.</p>
        <p><strong>Payment data:</strong> billing is processed by Lemon Squeezy. We receive subscription status and IDs, never card numbers.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">2. How We Use Your Information</h2>
        <ul style="padding-left:20px;">
          <li>To generate study plans, flashcards, quizzes, notes, tutor answers, essay feedback, and panic-mode plans</li>
          <li>To track XP, streaks, leaderboard rankings, and coin payouts</li>
          <li>To process subscriptions and service notifications such as password resets and study emails you opted into</li>
          <li>To keep the service secure, reliable, and improving over time</li>
        </ul>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">3. Data Security</h2>
        <p>We use HTTPS/TLS, CSRF protection, rate limiting, strict security headers, parameterized SQL, HTML escaping, secure cookies in production, and encryption for sensitive tokens.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">4. Sub-processors</h2>
        <ul style="padding-left:20px;">
          <li><strong>OpenAI:</strong> study materials and questions may be sent to generate student AI features. OpenAI does not train on API data per its API data-usage policy.</li>
          <li><strong>Instructure / Canvas LMS:</strong> optional course and assignment import.</li>
          <li><strong>Lemon Squeezy:</strong> payment and subscription processing.</li>
          <li><strong>Render:</strong> application hosting and database infrastructure.</li>
          <li><strong>Sentry:</strong> error reporting with sensitive fields scrubbed where possible.</li>
        </ul>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">5. Your Rights</h2>
        <p>You can access, export, correct, or delete your data from Settings, disconnect Canvas at any time, opt out of optional study emails, or contact <a href="mailto:support@machreach.com">support@machreach.com</a> for data-rights requests.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">6. Contact</h2>
        <p>Questions or data-rights requests: <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
      </div>
    </div>
    """), active_page="privacy")


@app.route("/terms")
def terms_page():
    return _render("Terms of Service", Markup("""
    <div style="max-width:800px;margin:0 auto;padding:40px 20px;">
      <h1 style="font-size:32px;margin-bottom:8px;">Terms of Service</h1>
      <p style="color:var(--text-muted);margin-bottom:32px;">Last updated: April 28, 2026</p>
      <div style="line-height:1.8;color:var(--text-secondary);font-size:15px;">
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">1. Acceptance of Terms</h2>
        <p>By creating an account or using MachReach, you agree to these Terms of Service. If you do not agree, do not use the service.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">2. Description of Service</h2>
        <p>MachReach provides student study tools including Canvas LMS integration, AI-generated study plans, flashcards, practice quizzes, AI tutor support, essay feedback, panic-mode cram plans, weekly schedule tools, focus timers, XP, leaderboards, coin rewards, a student marketplace, and the optional Focus Guard browser extension.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">3. Account Responsibilities</h2>
        <ul style="padding-left:20px;">
          <li>You must provide accurate information when registering</li>
          <li>You are responsible for maintaining the security of your account credentials</li>
          <li>You must not share your account with others</li>
          <li>You must be at least 16 years old to use MachReach</li>
          <li>You must not attempt to probe, scan, or exploit vulnerabilities in the service</li>
        </ul>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">4. Academic Integrity</h2>
        <p>You are responsible for complying with your institution's academic-integrity policies. MachReach is a study aid; using it to plagiarize, cheat, or violate honor codes is prohibited.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">5. Subscriptions and Billing</h2>
        <p>Paid student plans are billed through Lemon Squeezy. You can cancel at any time; access continues until the end of the billing period. Refunds are handled case by case.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">6. AI Features</h2>
        <p>AI output is provided as a suggestion. You are responsible for reviewing it before relying on it academically. The AI tutor is not a substitute for professional, academic, legal, medical, or financial advice.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7. Leaderboards and Coins</h2>
        <p>Coins have no cash value, cannot be transferred between accounts, and can be redeemed only inside MachReach. We may withhold or reverse rewards for suspected abuse.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">8. Limitation of Liability</h2>
        <p>MachReach is provided as is without warranties of any kind. We are not liable for service interruptions, data loss beyond our control, or indirect, incidental, or consequential damages.</p>
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">9. Contact</h2>
        <p>Questions about these terms? Contact <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
      </div>
    </div>
    """), active_page="terms")


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
    from outreach.db import (
        get_client, get_campaigns, get_contacts, get_sent_emails,
        get_email_accounts, get_subscription, get_usage,
    )
    client = get_client(cid)
    if not client:
        return jsonify({"error": "not found"}), 404

    profile = {k: client[k] for k in ("id", "name", "email", "business", "physical_address", "created_at") if k in client}
    campaigns = [dict(c) for c in get_campaigns(cid)]
    contacts_all = []
    for camp in campaigns:
        contacts_all.extend([dict(c) for c in get_contacts(cid, campaign_id=camp["id"])])
    sent = get_export_data(cid)
    accounts = [{"id": a["id"], "email": a["email"], "smtp_host": a["smtp_host"]} for a in (get_email_accounts(cid) or [])]
    sub = get_subscription(cid)
    usage = get_usage(cid)

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "profile": profile,
        "subscription": dict(sub) if sub else None,
        "usage": dict(usage) if usage else None,
        "email_accounts": accounts,
        "campaigns": campaigns,
        "contacts": contacts_all,
        "sent_emails": sent,
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
      .err-sub {{ color: var(--text-muted); font-size: 12.5px; margin: 0 0 28px; font-family: ui-monospace,SFMono-Regular,Menlo,monospace; background: var(--border-light); display: inline-block; padding: 4px 10px; border-radius: 6px; }}
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
          <a href="/" class="primary">&larr; Back to home</a>
          <button class="ghost" onclick="history.back()">Go back</button>
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


@app.errorhandler(404)
def _handle_404(e):
    return _render_error_page(
        404,
        "This page wandered off.",
        "The link you followed may be broken, or the page has moved. Try heading back home or using ⌘K for quick navigation.",
        sub=request.path[:80],
    )


@app.errorhandler(500)
def _handle_500(e):
    try:
        app.logger.exception("500 error at %s %s: %s", request.method, request.path, e)
    except Exception:
        pass
    return _render_error_page(
        500,
        "Something broke on our end.",
        "This one's on us. We've logged the error — in most cases a quick retry will fix it. If not, send us a note at support@machreach.com and we'll dig in.",
    )


@app.errorhandler(403)
def _handle_403(e):
    return _render_error_page(
        403,
        "That area is off-limits.",
        "You don't have permission to access this page. If you think this is a mistake, contact your account admin or support.",
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=port)
