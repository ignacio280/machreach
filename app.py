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
from outreach.config import SECRET_KEY, SENDER_NAME
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
init_student_db()
register_student_routes(app, csrf, limiter)

# ── MachReach Pro module (productivity toolkit for business accounts) ──
from professional.db import init_professional_db
from professional.routes import register_professional_routes
init_professional_db()
register_professional_routes(app, csrf, limiter)


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
@csrf.exempt
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
@csrf.exempt
@limiter.exempt
def admin_reset_all_accounts():
    """One-time admin action: notify all users and delete all accounts."""
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


def _effective_client_id() -> int:
    """Return the client_id to use for data access.
    If the user is a full-access team member, returns the owner's client_id
    so they see the owner's campaigns, contacts, and inbox."""
    cid = session["client_id"]
    from outreach.db import get_team_owner
    owner = get_team_owner(cid)
    return owner if owner else cid


@app.before_request
def _validate_session():
    if "client_id" in session:
        from outreach.db import get_db, _fetchval
        with get_db() as db:
            row = _fetchval(db, "SELECT 1 FROM clients WHERE id = %s",
                            (session["client_id"],))
            if row is None:
                session.clear()


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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://js.stripe.com https://www.paypal.com https://www.paypalobjects.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' https:; "
        "connect-src 'self' https://api.openai.com https://*.instructure.com https://cdn.jsdelivr.net; "
        "frame-src 'self' https://js.stripe.com https://www.paypal.com https://www.sandbox.paypal.com https://open.spotify.com https://www.youtube.com https://www.youtube-nocookie.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self' https://www.paypal.com https://www.sandbox.paypal.com; "
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
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" onload="if(typeof renderMathInElement==='function')renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}],throwOnError:false});"></script>
  <script>
    // Apply saved theme immediately to prevent flash
    (function(){
      // Legacy dark-mode toggle
      var t = localStorage.getItem('machreach-theme');
      if (t) document.documentElement.setAttribute('data-theme', t);
      // MachReach student theme picker
      var mr = localStorage.getItem('mr_theme');
      if (mr && mr !== 'default') document.documentElement.setAttribute('data-theme', 'mr-' + mr);
    })();
    // Allow the settings page (and anywhere else) to switch themes instantly
    window.applyMrTheme = function(name) {
      try {
        var root = document.documentElement;
        if (!name || name === 'default') {
          // Default = saved dark-mode value or light
          var leg = localStorage.getItem('machreach-theme') || '';
          root.setAttribute('data-theme', leg);
        } else {
          root.setAttribute('data-theme', 'mr-' + name);
        }
        localStorage.setItem('mr_theme', name || 'default');
      } catch (e) { console.error('applyMrTheme failed', e); }
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
      try { var t = await r.text(); return JSON.parse(t); }
      catch(e) { return {error: 'Server error (status ' + r.status + '). Please try again.'}; }
    };
  </script>
  <style>
    :root {
      --bg: #F8FAFC;
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
      --text: #0F172A;
      --text-secondary: #475569;
      --text-muted: #94A3B8;
      --border: #E2E8F0;
      --border-light: #F1F5F9;
      --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.04);
      --shadow-lg: 0 12px 24px rgba(0,0,0,0.08), 0 4px 8px rgba(0,0,0,0.04);
      --radius: 14px;
      --radius-sm: 10px;
      --radius-xs: 7px;
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
      background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
      padding: 0 48px; display: flex; align-items: center; justify-content: space-between;
      height: 58px; position: sticky; top: 0; z-index: 100;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      backdrop-filter: blur(12px);
    }
    .nav .brand { color: #fff; font-weight: 800; font-size: 18px; letter-spacing: -0.5px; display: flex; align-items: center; gap: 10px; text-decoration: none; }
    .nav .brand-icon { width: 30px; height: 30px; background: linear-gradient(135deg, var(--primary), #8B5CF6); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 14px; color: #fff; box-shadow: 0 2px 8px rgba(99,102,241,0.4); }
    .nav-links { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
    .nav-links a { color: #94A3B8; text-decoration: none; font-size: 13px; font-weight: 500; padding: 7px 13px; border-radius: var(--radius-xs); transition: all 0.2s; }
    .nav-links a:hover { color: #F1F5F9; background: rgba(255,255,255,0.08); }
    .nav-links a.active { color: #fff; background: rgba(255,255,255,0.13); }
    .nav-links .nav-divider { width: 1px; height: 20px; background: rgba(255,255,255,0.1); margin: 0 6px; }
    .nav-links .nav-user { color: #64748B; font-size: 12px; margin-right: 4px; }
    /* Nav dropdown */
    .nav-dropdown { position: relative; }
    .nav-dropdown > a { cursor: pointer; }
    .nav-dropdown-menu { display:none; position:absolute; top:100%; left:50%; transform:translateX(-50%) translateY(4px); opacity:0; background:#1e293b; border:1px solid rgba(255,255,255,0.1); border-radius:10px; padding:6px 0; min-width:180px; z-index:300; box-shadow:0 12px 40px rgba(0,0,0,0.5); margin-top:2px; transition:opacity 0.15s,transform 0.15s; }
    .nav-dropdown:hover .nav-dropdown-menu { display:block; opacity:1; transform:translateX(-50%) translateY(0); }
    .nav-dropdown-menu a { display:block; padding:9px 18px !important; font-size:13px !important; color:#94a3b8 !important; border-radius:0 !important; transition:all 0.15s !important; }
    .nav-dropdown-menu a:hover { color:#fff !important; background:rgba(99,102,241,0.15) !important; padding-left:22px !important; }
    /* Floating focus widget */
    #focus-float { position:fixed; bottom:20px; right:20px; background:linear-gradient(135deg,#1e293b,#334155); border:1px solid rgba(255,255,255,0.1); border-radius:16px; padding:12px 18px; z-index:500; box-shadow:0 8px 32px rgba(0,0,0,0.4); display:none; cursor:pointer; color:#fff; font-family:monospace; min-width:140px; text-align:center; transition:all 0.3s; }
    #focus-float:hover { transform:scale(1.05); box-shadow:0 12px 40px rgba(99,102,241,0.3); }
    #focus-float .ff-time { font-size:28px; font-weight:800; letter-spacing:1px; }
    #focus-float .ff-label { font-size:11px; color:#94a3b8; margin-top:2px; }
    #focus-float .ff-close { position:absolute; top:4px; right:8px; font-size:14px; color:#64748b; cursor:pointer; }
    #focus-float .ff-close:hover { color:#ef4444; }

    /* Layout — edge-to-edge with comfortable padding */
    .container { max-width: 1600px; margin: 0 auto; padding: 32px 48px; }
    .container.container-wide { max-width: 100%; padding: 32px 48px; }
    .page-header { margin-bottom: 26px; }
    .page-header h1 { font-size: 28px; font-weight: 800; letter-spacing: -0.7px; }
    .page-header p { color: var(--text-secondary); margin-top: 4px; font-size: 15px; }
    .breadcrumb { font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }
    .breadcrumb a { color: var(--text-muted); text-decoration: none; }
    .breadcrumb a:hover { color: var(--primary); }

    /* Cards */
    .card { background: var(--card); border-radius: var(--radius); padding: 26px; box-shadow: var(--shadow); margin-bottom: 18px; border: 1px solid var(--border); transition: box-shadow 0.2s ease, transform 0.2s ease; }
    .card:hover { box-shadow: var(--shadow-md); }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--border-light); }
    .card-header h2 { font-size: 16px; font-weight: 700; }

    /* Stats */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 22px; }
    .stat-card { background: var(--card); border-radius: var(--radius); padding: 20px; text-align: center; box-shadow: var(--shadow); border: 1px solid var(--border); position: relative; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; cursor: default; }
    .stat-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
    .stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: var(--radius) var(--radius) 0 0; }
    .stat-purple::before { background: linear-gradient(90deg, var(--primary), #8B5CF6); }
    .stat-green::before { background: linear-gradient(90deg, var(--green), #34D399); }
    .stat-blue::before { background: linear-gradient(90deg, var(--blue), #60A5FA); }
    .stat-yellow::before { background: linear-gradient(90deg, var(--yellow), #FBBF24); }
    .stat-red::before { background: linear-gradient(90deg, var(--red), #F87171); }
    .stat-card .num { font-size: 30px; font-weight: 800; letter-spacing: -1px; }
    .stat-card .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-top: 4px; font-weight: 600; }
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
      width: 100%; padding: 10px 14px; border: 1.5px solid var(--border); border-radius: var(--radius-sm);
      font-size: 14px; margin-bottom: 14px; background: var(--card); color: var(--text);
      transition: border-color 0.2s, box-shadow 0.2s; font-family: inherit;
    }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(99,102,241,0.12); }
    textarea { min-height: 90px; resize: vertical; }
    input[type="file"] { padding: 8px; cursor: pointer; }
    .form-hint { font-size: 11px; color: var(--text-muted); margin-top: -10px; margin-bottom: 14px; }
    .form-group { margin-bottom: 2px; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .form-divider { border: none; border-top: 1px dashed var(--border); margin: 16px 0; }

    /* Buttons */
    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 10px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
      text-decoration: none; border: none; border-radius: var(--radius-xs);
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); font-family: inherit; white-space: nowrap;
    }
    .btn-primary { background: linear-gradient(135deg, var(--primary), #7C3AED); color: #fff; box-shadow: 0 2px 8px rgba(99,102,241,0.25); }
    .btn-primary:hover { background: linear-gradient(135deg, var(--primary-hover), #6D28D9); box-shadow: 0 4px 16px rgba(99,102,241,0.35); transform: translateY(-1px); }
    .btn-primary:active { transform: translateY(0); box-shadow: 0 2px 8px rgba(99,102,241,0.25); }
    .btn-green { background: var(--green); color: #fff; }
    .btn-green:hover { background: var(--green-hover); }
    .btn-red { background: var(--red); color: #fff; }
    .btn-red:hover { background: var(--red-hover); }
    .btn-yellow { background: var(--yellow); color: #fff; }
    .btn-yellow:hover { background: #D97706; }
    .btn-outline { background: transparent; color: var(--text-secondary); border: 1.5px solid var(--border); }
    .btn-outline:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-light); }
    .btn-ghost { background: transparent; color: var(--text-muted); padding: 7px 12px; }
    .btn-ghost:hover { color: var(--text); background: var(--border-light); }
    .btn-sm { padding: 6px 14px; font-size: 12px; }
    .btn-lg { padding: 13px 32px; font-size: 15px; border-radius: var(--radius-sm); }
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn-icon { width: 32px; height: 32px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--radius-xs); }

    /* Tables */
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 8px 12px; color: var(--text-muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid var(--border-light); }
    td { padding: 10px 12px; border-bottom: 1px solid var(--border-light); font-size: 13px; }
    tr:last-child td { border-bottom: none; }
    tbody tr { transition: background 0.1s; }
    tbody tr:hover td { background: #FAFBFC; }

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
    .hamburger { display: none; background: none; border: none; cursor: pointer; padding: 8px; color: #94A3B8; font-size: 24px; line-height: 1; z-index: 201; }
    @media (max-width: 1280px) {
      .nav { backdrop-filter: none; }
      .hamburger { display: block; }
      .nav-links { display: none; position: fixed; top: 58px; left: 0; right: 0; bottom: 0; background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%); flex-direction: column; padding: 20px 24px; gap: 4px; overflow-y: auto; z-index: 200; }
      .nav-links.open { display: flex; }
      .nav-links a { font-size: 15px !important; padding: 12px 16px !important; border-radius: var(--radius-xs); }
      .nav-links a.active { background: rgba(255,255,255,0.13); }
      .nav-links .nav-divider { height: 1px; background: rgba(255,255,255,0.08); margin: 8px 0; width: 100%; }
      .nav-links .nav-user { font-size: 14px; padding: 12px 16px; }
      /* On mobile, dropdowns expand inline so every link is reachable by tap */
      .nav-dropdown { width: 100%; position: static; }
      .nav-dropdown > a { display: block; }
      .nav-dropdown-menu { display: block !important; position: static !important; opacity: 1 !important; transform: none !important; background: rgba(255,255,255,0.04) !important; border: none !important; box-shadow: none !important; padding: 4px 0 8px 12px !important; margin-top: 0 !important; min-width: 0 !important; }
      .nav-dropdown-menu a { font-size: 14px !important; padding: 10px 14px !important; }
      .toast-container { right: 12px; left: 12px; max-width: none; }
      table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
      thead, tbody, tr { display: table; width: 100%; table-layout: auto; }
      thead { display: table-header-group; }
      tbody { display: table-row-group; }
    }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/" class="brand">
      <div class="brand-icon">&#9993;</div>
      MachReach
    </a>
    <button class="hamburger" onclick="document.querySelector('.nav-links').classList.toggle('open');this.innerHTML=this.innerHTML==='&#9776;'?'&#10005;':'&#9776;'" aria-label="Menu">&#9776;</button>
    <div class="nav-links">
      {% if logged_in %}
        {% if account_type|default('business') == 'student' %}
        <a href="/student" {% if active_page == 'student_dashboard' %}class="active"{% endif %}>&#127891; Dashboard</a>
        <a href="/student/courses" {% if active_page == 'student_courses' %}class="active"{% endif %}>&#128218; Courses</a>
        <a href="/student/plan" {% if active_page == 'student_plan' %}class="active"{% endif %}>&#128197; Plan</a>
        <div class="nav-dropdown">
          <a href="#" onclick="return false" {% if active_page in ['student_flashcards','student_quizzes','student_notes','student_chat','student_essay','student_practice'] %}class="active"{% endif %}>&#128218; Study Tools &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/flashcards">&#127183; Flashcards</a>
            <a href="/student/quizzes">&#128221; Quizzes</a>
            <a href="/student/notes">&#128214; Notes</a>
            <a href="/student/chat">&#129302; AI Tutor</a>
            <a href="/student/essay">&#9999;&#65039; Essay</a>
            <a href="/student/practice">&#128736; Practice</a>
          </div>
        </div>
        <a href="/student/focus" {% if active_page == 'student_focus' %}class="active"{% endif %}>&#127919; Focus</a>
        <a href="/student/panic" {% if active_page == 'student_panic' %}class="active" style="color:#EF4444;"{% else %}style="color:#EF4444;"{% endif %}>&#128680; Panic</a>
        <div class="nav-divider"></div>
        <div class="nav-dropdown">
          <a href="#" onclick="return false" {% if active_page in ['student_exams','student_schedule','student_weak','student_gpa','student_achievements'] %}class="active"{% endif %}>More &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/student/exams">&#128221; Exams</a>
            <a href="/student/schedule">&#128337; Schedule</a>
            <a href="/student/weak-topics">&#127919; Weak Topics</a>
            <a href="/student/gpa">&#128200; GPA</a>
            <a href="/student/achievements">&#127942; XP &amp; Badges</a>
          </div>
        </div>
        <a href="/student/exchange" {% if active_page == 'student_exchange' %}class="active"{% endif %}>&#128257; Exchange</a>
        <a href="/student/leaderboard" {% if active_page == 'student_leaderboard' %}class="active"{% endif %}>&#127942; Leaderboard</a>
        <div class="nav-divider"></div>
        <a href="/mail-hub" {% if active_page == 'mail_hub' %}class="active"{% endif %}>&#128233; Mail</a>
        <a href="/student/settings" {% if active_page == 'student_settings' %}class="active"{% endif %}>&#9881;</a>
        {% else %}
        <a href="/dashboard" {% if active_page == 'dashboard' %}class="active"{% endif %}>{{nav.dashboard}}</a>
        <a href="/campaign/new" {% if active_page == 'new_campaign' %}class="active"{% endif %}>{{nav.new_campaign}}</a>
        <a href="/inbox" {% if active_page == 'inbox' %}class="active"{% endif %}>{{nav.inbox}}</a>
        <a href="/ab-tests" {% if active_page == 'ab_tests' %}class="active"{% endif %}>{{nav.ab_tests}}</a>
        <a href="/smart-times" {% if active_page == 'smart_times' %}class="active"{% endif %}>&#9201; {{nav.send_times}}</a>
        <a href="/subject-optimizer" {% if active_page == 'subject_optimizer' %}class="active"{% endif %}>&#10024; Subject</a>
        <a href="/reply-intel" {% if active_page == 'reply_intel' %}class="active"{% endif %}>&#129504; Replies</a>
        <a href="/deliverability" {% if active_page == 'deliverability' %}class="active"{% endif %}>&#128737;&#65039; Inbox</a>
        <a href="/calendar" {% if active_page == 'calendar' %}class="active"{% endif %}>{{nav.calendar}}</a>
        <a href="/export" {% if active_page == 'export' %}class="active"{% endif %}>&#128202; {{nav.export}}</a>
        <div class="nav-dropdown">
          <a href="/pro" {% if active_page in ['pro','pro_tasks','pro_invoices','pro_finance','pro_goals','pro_assistant','pro_linkedin','pro_meetings','pro_relationships'] %}class="active"{% endif %}>&#128188; Pro Tools &#9662;</a>
          <div class="nav-dropdown-menu">
            <a href="/pro/tasks">&#9989; Tasks</a>
            <a href="/pro/finance">&#128176; Finance</a>
            <a href="/pro/relationships">&#129504; Relationships</a>
            <a href="/pro/meeting-agenda">&#128197; Meeting Agenda</a>
            <a href="/pro/goals">&#127919; Goals &amp; OKRs</a>
            <a href="/pro/invoices">&#128196; Invoices</a>
            <a href="/pro/assistant">&#9997; Text Polish</a>
            <a href="/pro/linkedin-post">&#128100; LinkedIn Post</a>
          </div>
        </div>
        <div class="nav-divider"></div>
        <a href="/mail-hub" {% if active_page == 'mail_hub' %}class="active"{% endif %} style="{% if active_page == 'mail_hub' %}color:var(--primary);{% endif %}">&#128233; {{nav.mail_hub}}</a>
        <a href="/contacts" {% if active_page == 'contacts' %}class="active"{% endif %} style="{% if active_page == 'contacts' %}color:var(--primary);{% endif %}">&#128101; {{nav.contacts}}</a>
        <a href="/settings" {% if active_page == 'settings' %}class="active"{% endif %}>{{nav.settings}}</a>
        {% endif %}
        {% if is_admin %}<a href="/admin/broadcast" {% if active_page == 'admin' %}class="active"{% endif %} style="color:var(--yellow);">&#128227; Admin</a>{% endif %}
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <div class="nav-divider"></div>
        <span class="nav-user">{{client_name}}</span>
        <a href="/logout" style="color:#EF4444;">{{nav.logout}}</a>
      {% else %}
        <a href="/pricing">{{nav.pricing}}</a>
        <a href="/login">{{nav.login}}</a>
        <a href="/register" class="btn btn-primary btn-sm" style="color:#fff;">{{nav.get_started}}</a>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
      {% endif %}
    </div>
  </div>
  <div class="container{% if wide %} container-wide{% endif %}">
    <div style="background:linear-gradient(135deg,#F59E0B,#D97706);color:#fff;padding:10px 20px;border-radius:8px;margin-bottom:18px;text-align:center;font-size:13px;font-weight:500;display:flex;align-items:center;justify-content:center;gap:8px;">
      &#128679; <b>Beta</b> &mdash; MachReach is in testing mode. Subscriptions are not available yet. All features are free during this period.
    </div>
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
    <span>&copy; 2026 MachReach. All rights reserved.</span>
    <div style="display:flex;gap:18px;">
      <a href="/privacy" style="color:var(--text-muted);text-decoration:none;">Privacy Policy</a>
      <a href="/terms" style="color:var(--text-muted);text-decoration:none;">Terms of Service</a>
      <a href="mailto:support@machreach.com" style="color:var(--text-muted);text-decoration:none;">Contact</a>
    </div>
  </footer>
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
    // Promotion toast — shown when a user ranks up
    window.showPromotionToast = function(promo) {
      if (!promo || !promo.promoted || !promo.rank_after) return;
      var r = promo.rank_after;
      var title = promo.reached_elite ? 'Elite Rank Achieved!'
                : (promo.tier_up ? 'Tier Promotion!' : 'Rank Up!');
      var toast = document.createElement('div');
      toast.style.cssText = 'position:fixed;top:24px;right:24px;z-index:99999;'
        + 'background:linear-gradient(135deg,' + r.color + ',#111827);'
        + 'color:#fff;padding:18px 22px;border-radius:14px;'
        + 'box-shadow:0 18px 40px rgba(0,0,0,.4);min-width:280px;'
        + 'border:2px solid ' + r.color + ';font-family:inherit;'
        + 'animation:promoSlide .5s ease-out;';
      toast.innerHTML =
        '<div style="font-size:12px;opacity:.85;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">' + title + '</div>'
        + '<div style="font-size:22px;font-weight:700;margin-bottom:2px">' + r.full_name + '</div>'
        + '<div style="font-size:13px;opacity:.9">You\'ve earned a new rank. Keep grinding.</div>';
      if (!document.getElementById('promo-toast-style')) {
        var st = document.createElement('style');
        st.id = 'promo-toast-style';
        st.textContent = '@keyframes promoSlide{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}'
          + '@keyframes promoFade{to{opacity:0;transform:translateX(120%)}}';
        document.head.appendChild(st);
      }
      document.body.appendChild(toast);
      setTimeout(function(){ toast.style.animation='promoFade .5s ease-in forwards'; }, 5500);
      setTimeout(function(){ toast.remove(); }, 6100);
    };
    // Theme system — applies named themes via CSS variables on <body>
    window.MR_THEMES = {
      // ── Dark ──
      default: { bg:'#0f172a', card:'#1e293b', border:'#334155', text:'#f1f5f9', textMuted:'#94a3b8', primary:'#6366f1' },
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
      var t = window.MR_THEMES[name] || window.MR_THEMES['default'];
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
        var leg = localStorage.getItem('machreach-theme') || '';
        r.setAttribute('data-theme', leg);
      } else {
        r.setAttribute('data-theme', 'mr-' + name);
      }
      try { localStorage.setItem('mr_theme', name || 'default'); } catch(e) {}
      document.body && document.body.setAttribute('data-theme', name);
    };
    // Apply saved theme on load
    try { window.applyMrTheme(localStorage.getItem('mr_theme') || 'default'); } catch(e) {}

    // ── FOCUS SHIELD (in-app distraction blocker) ──
    // When a focus session is running (localStorage.focus_float.active),
    // any non-focus MachReach page is replaced with a full-screen shield
    // urging the user back to the timer. Prevents using MachReach itself
    // as a distraction.
    (function() {
      function isActive() {
        try {
          var ff = JSON.parse(localStorage.getItem('focus_float') || 'null');
          return !!(ff && ff.active);
        } catch(e) { return false; }
      }
      function onFocusPage() {
        return location.pathname === '/student/focus' || location.pathname === '/student/focus/';
      }
      function mountShield() {
        if (document.getElementById('mr-focus-shield')) return;
        var s = document.createElement('div');
        s.id = 'mr-focus-shield';
        s.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:radial-gradient(circle at top,#1e1b4b,#050816);color:#fff;display:flex;align-items:center;justify-content:center;font-family:Inter,sans-serif;animation:mrFsIn .35s ease-out';
        s.innerHTML = '<style>@keyframes mrFsIn{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:scale(1)}}</style>'
          + '<div style="max-width:520px;padding:40px 32px;text-align:center">'
          +   '<div style="font-size:72px;margin-bottom:12px">🎯</div>'
          +   '<div style="font-size:12px;letter-spacing:3px;color:#A78BFA;text-transform:uppercase;font-weight:700;margin-bottom:8px">Focus session active</div>'
          +   '<h1 style="font-size:30px;margin:0 0 14px;font-weight:800;line-height:1.15">Stay locked in.</h1>'
          +   '<p style="color:#C7D2FE;font-size:15px;line-height:1.6;margin:0 0 26px">You started a focus session. This page is blocked until you finish or pause the timer. No shortcuts.</p>'
          +   '<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">'
          +     '<a href="/student/focus" style="background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;padding:14px 26px;border-radius:12px;text-decoration:none;font-weight:700;font-size:14px;box-shadow:0 10px 30px rgba(99,102,241,.4)">⟵ Back to Focus Timer</a>'
          +     '<button id="mr-focus-shield-break" style="background:transparent;border:1px solid rgba(255,255,255,.2);color:#A5B4FC;padding:14px 20px;border-radius:12px;cursor:pointer;font-weight:600;font-size:13px">End session (lose progress)</button>'
          +   '</div>'
          + '</div>';
        document.body.appendChild(s);
        document.getElementById('mr-focus-shield-break').onclick = function() {
          if (!confirm('End your focus session and forfeit unsaved XP?')) return;
          localStorage.removeItem('focus_float');
          localStorage.removeItem('focus_timer_state');
          location.reload();
        };
      }
      function unmount() {
        var s = document.getElementById('mr-focus-shield');
        if (s) s.remove();
      }
      function tick() {
        if (isActive() && !onFocusPage()) mountShield();
        else unmount();
      }
      // Run immediately + every 2s
      tick();
      setInterval(tick, 2000);
      // React instantly to storage changes across tabs
      window.addEventListener('storage', function(e){ if (e.key === 'focus_float') tick(); });
    })();
    // Loading button handler
    document.querySelectorAll('form[data-loading]').forEach(form => {
      form.addEventListener('submit', () => {
        const btn = form.querySelector('button[type=submit]');
        if (btn) btn.classList.add('loading');
      });
    });
    // Preview modal
    function showPreview(id) {
      document.getElementById('preview-' + id).classList.add('show');
    }
    function hidePreview(id) {
      document.getElementById('preview-' + id).classList.remove('show');
    }
    function loadPreview(sel) {
      var seqId = sel.getAttribute('data-seq-id');
      var campId = sel.getAttribute('data-camp-id');
      var contactId = sel.value;
      var url = '/api/campaign/' + campId + '/preview/' + seqId;
      if (contactId) url += '?contact_id=' + contactId;
      fetch(url).then(function(r) { return r.json(); }).then(function(d) {
        document.getElementById('preview-subj-' + seqId).textContent = d.subject;
        document.getElementById('preview-body-' + seqId).textContent = d.body;
        document.getElementById('preview-hint-' + seqId).textContent =
          'Previewing as: ' + d.contact_name;
      });
    }
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') document.querySelectorAll('.preview-modal.show').forEach(m => m.classList.remove('show'));
    });

    // Live stats polling — refreshes every 15s
    (function() {
      const dashStatEls = {
        total_campaigns: document.querySelector('[data-stat="total_campaigns"]'),
        active_campaigns: document.querySelector('[data-stat="active_campaigns"]'),
        total_sent: document.querySelector('[data-stat="total_sent"]'),
        open_rate_fmt: document.querySelector('[data-stat="open_rate_fmt"]'),
        total_replied: document.querySelector('[data-stat="total_replied"]'),
        reply_rate_fmt: document.querySelector('[data-stat="reply_rate_fmt"]'),
        total_bounced: document.querySelector('[data-stat="total_bounced"]'),
      };
      const hasDashStats = Object.values(dashStatEls).some(el => el !== null);

      const campStatEls = {
        total_contacts: document.querySelector('[data-stat="camp_total_contacts"]'),
        emails_sent: document.querySelector('[data-stat="camp_emails_sent"]'),
        open_rate_fmt: document.querySelector('[data-stat="camp_open_rate_fmt"]'),
        reply_rate_fmt: document.querySelector('[data-stat="camp_reply_rate_fmt"]'),
      };
      const campId = document.querySelector('[data-campaign-id]');
      const hasCampStats = Object.values(campStatEls).some(el => el !== null);

      function refreshDashboardStats() {
        fetch('/api/stats')
          .then(r => r.json())
          .then(data => {
            if (data.error) return;
            for (const [key, el] of Object.entries(dashStatEls)) {
              if (el && data[key] !== undefined) {
                const newVal = String(data[key]);
                if (el.textContent !== newVal) {
                  el.textContent = newVal;
                  el.style.transition = 'color 0.3s';
                  el.style.color = 'var(--green)';
                  setTimeout(() => el.style.color = '', 1000);
                }
              }
            }
          })
          .catch(() => {});
      }

      function refreshCampaignStats() {
        if (!campId) return;
        const cid = campId.dataset.campaignId;
        fetch('/api/campaign/' + cid + '/stats')
          .then(r => r.json())
          .then(data => {
            if (data.error) return;
            for (const [key, el] of Object.entries(campStatEls)) {
              if (el && data[key] !== undefined) {
                const newVal = String(data[key]);
                if (el.textContent !== newVal) {
                  el.textContent = newVal;
                  el.style.transition = 'color 0.3s';
                  el.style.color = 'var(--green)';
                  setTimeout(() => el.style.color = '', 1000);
                }
              }
            }
          })
          .catch(() => {});
      }

      if (hasDashStats) {
        setInterval(refreshDashboardStats, 15000);
      }
      if (hasCampStats) {
        setInterval(refreshCampaignStats, 15000);
      }
    })();

    // --- Dark mode toggle ---
    function toggleDarkMode() {
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      const next = current === 'dark' ? '' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('machreach-theme', next);
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
              window.location = '/mail-hub/' + rows[selectedIdx].dataset.mailId;
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

  <!-- Cookie Consent Banner (GDPR) -->
  <div id="cookie-consent" style="display:none;position:fixed;bottom:0;left:0;right:0;z-index:9999;background:var(--card);border-top:1px solid var(--border-light);box-shadow:0 -2px 16px rgba(0,0,0,.12);padding:16px 24px;font-size:13px;color:var(--text-secondary);">
    <div style="max-width:960px;margin:0 auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <p style="flex:1;margin:0;min-width:200px;">We use essential cookies to keep you signed in and remember your preferences. No tracking or advertising cookies. <a href="/privacy" style="color:var(--primary);text-decoration:underline;">Privacy Policy</a></p>
      <button onclick="acceptCookies()" class="btn btn-primary btn-sm">Accept</button>
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

  <script>
  (function(){
    // Restore floating timer from localStorage
    var d = JSON.parse(localStorage.getItem('focus_float')||'null');
    if(d && d.active){
      var el=document.getElementById('focus-float');
      el.style.display='block';
      function tick(){
        var dd=JSON.parse(localStorage.getItem('focus_float')||'null');
        if(!dd||!dd.active){el.style.display='none';return;}
        if(dd.mode==='countdown'){
          var left=dd.endAt-Date.now();
          if(left<0) left=0;
          var m=Math.floor(left/60000), s=Math.floor((left%60000)/1000);
          document.getElementById('ff-time').textContent=String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
          document.getElementById('ff-label').textContent=dd.label||'Focus';
          if(left<=0){
            // timer ended — check if auto-cycle
            if(dd.nextPhase){
              localStorage.setItem('focus_float',JSON.stringify(dd.nextPhase));
            } else {
              dd.active=false; localStorage.setItem('focus_float',JSON.stringify(dd));
              el.style.display='none';
            }
            return;
          }
        } else {
          var elapsed=Math.floor((Date.now()-dd.startAt)/1000);
          var m=Math.floor(elapsed/60), s=elapsed%60;
          document.getElementById('ff-time').textContent=String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
          document.getElementById('ff-label').textContent=dd.label||'Reading';
        }
        requestAnimationFrame(tick);
      }
      tick(); setInterval(tick,1000);
    }
  })();
  function closeFocusFloat(){
    localStorage.removeItem('focus_float');
    document.getElementById('focus-float').style.display='none';
  }
  </script>

  <!-- Interactive tutorial (students only) -->
  {% if logged_in and account_type|default('business') == 'student' %}
  <style>
  #mr-tut-overlay{position:fixed;inset:0;z-index:999990;pointer-events:none;transition:opacity .3s}
  #mr-tut-overlay.active{pointer-events:auto}
  #mr-tut-backdrop{position:fixed;inset:0;z-index:999991;background:rgba(0,0,0,0.55);transition:opacity .3s}
  #mr-tut-highlight{position:fixed;z-index:999992;border-radius:10px;box-shadow:0 0 0 4000px rgba(0,0,0,0.55),0 0 0 3px #7C3AED,0 0 20px rgba(124,58,237,0.4);transition:all .35s cubic-bezier(.4,0,.2,1);pointer-events:none}
  #mr-tut-tooltip{position:fixed;z-index:999993;background:#1E1B4B;color:#E0E7FF;border-radius:14px;padding:22px 26px 18px;max-width:370px;min-width:280px;box-shadow:0 12px 40px rgba(0,0,0,0.4),0 0 0 1px rgba(124,58,237,0.3);font-family:'Inter',sans-serif;transition:all .35s cubic-bezier(.4,0,.2,1);opacity:0;transform:translateY(8px)}
  #mr-tut-tooltip.show{opacity:1;transform:translateY(0)}
  #mr-tut-tooltip .tut-step{font-size:11px;color:#A5B4FC;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;font-weight:600}
  #mr-tut-tooltip .tut-title{font-size:17px;font-weight:700;margin-bottom:6px;color:#fff}
  #mr-tut-tooltip .tut-desc{font-size:13px;line-height:1.55;color:#C7D2FE;margin-bottom:16px}
  #mr-tut-tooltip .tut-btns{display:flex;gap:8px;justify-content:flex-end;align-items:center;flex-wrap:wrap}
  #mr-tut-tooltip .tut-btns button{border:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s}
  #mr-tut-tooltip .tut-next{background:#7C3AED;color:#fff}
  #mr-tut-tooltip .tut-next:hover{background:#6D28D9}
  #mr-tut-tooltip .tut-skip{background:transparent;color:#A5B4FC;text-decoration:underline}
  #mr-tut-tooltip .tut-skip:hover{color:#fff}
  #mr-tut-tooltip .tut-back{background:rgba(165,180,252,0.15);color:#A5B4FC}
  #mr-tut-tooltip .tut-back:hover{background:rgba(165,180,252,0.25)}
  #mr-tut-arrow{position:fixed;z-index:999993;width:0;height:0;transition:all .35s cubic-bezier(.4,0,.2,1)}
  #mr-tut-progress{display:flex;gap:3px;width:100%;margin-bottom:4px}
  #mr-tut-progress span{width:6px;height:6px;border-radius:50%;background:rgba(165,180,252,0.25);transition:background .2s}
  #mr-tut-progress span.active{background:#7C3AED}
  #mr-tut-progress span.done{background:#A5B4FC}
  .mr-tut-pulse{animation:mr-pulse 2s ease-in-out infinite}
  @keyframes mr-pulse{0%,100%{box-shadow:0 0 0 4000px rgba(0,0,0,0.55),0 0 0 3px #7C3AED,0 0 20px rgba(124,58,237,0.4)}50%{box-shadow:0 0 0 4000px rgba(0,0,0,0.55),0 0 0 5px #7C3AED,0 0 30px rgba(124,58,237,0.6)}}
  #mr-tut-welcome{position:fixed;inset:0;z-index:999999;background:rgba(0,0,0,0.7);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center}
  #mr-tut-welcome .welcome-card{background:linear-gradient(135deg,#1E1B4B,#312E81);border-radius:20px;padding:40px 48px;max-width:460px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.5);color:#E0E7FF;font-family:'Inter',sans-serif}
  #mr-tut-welcome .welcome-card h2{font-size:24px;color:#fff;margin:12px 0 8px}
  #mr-tut-welcome .welcome-card p{font-size:14px;line-height:1.6;color:#C7D2FE;margin-bottom:24px}
  #mr-tut-welcome .welcome-card .wbtn{display:inline-block;padding:12px 32px;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;border:none;transition:all .15s}
  #mr-tut-welcome .welcome-card .wbtn-start{background:#7C3AED;color:#fff;margin-right:12px}
  #mr-tut-welcome .welcome-card .wbtn-start:hover{background:#6D28D9;transform:translateY(-1px)}
  #mr-tut-welcome .welcome-card .wbtn-skip{background:transparent;color:#A5B4FC;text-decoration:underline}
  </style>
  <div id="mr-tut-overlay"><div id="mr-tut-backdrop" style="display:none"></div><div id="mr-tut-highlight" style="display:none"></div><div id="mr-tut-arrow" style="display:none"></div><div id="mr-tut-tooltip" style="display:none"></div></div>
  <script>
  /* =============================================================
     Multi-page Interactive Tutorial — auto-navigates between pages,
     highlights the real element on each page, explains what it does.
     State persists in localStorage so the tour survives navigation.
     ============================================================= */
  (function(){
    var ACCOUNT_TYPE = {% if account_type|default('business') == 'student' %}'student'{% else %}'business'{% endif %};
    var DONE_KEY  = ACCOUNT_TYPE === 'student' ? 'mr-tutorial-done' : 'mr-biz-tutorial-done';
    var STATE_KEY = 'mr-tour-state';
    var START_PAGE = ACCOUNT_TYPE === 'student' ? '/student' : '/dashboard';
    var END_PAGE   = ACCOUNT_TYPE === 'student' ? '/student/settings' : '/settings';

    /* ---------- STEP LISTS ---------- */
    var STUDENT_STEPS = [
      {url:'/student', sel:'h1,h2', title:'Your Student Dashboard', desc:'This is home base. Every stat, upcoming exam, and daily plan item lives here. We\\'ll walk through every feature you have — and take you to each page so you can see it in action.', pos:'bottom'},
      {url:'/student', sel:'.stat-card,.nav-dropdown', title:'Stats at a glance', desc:'Total courses, upcoming exams, focus hours, and streak — always in the header so you know where you stand.', pos:'bottom'},

      {url:'/student/courses', sel:'h1', title:'Courses — your source of truth', desc:'Everything starts with a course. You can sync from Canvas with one click or create courses manually with the "+ New Course" button.', pos:'bottom'},
      {url:'/student/courses', sel:'button[onclick*="showNewCourseModal"],button[onclick*="syncCourses"]', title:'Add courses two ways', desc:'Sync Canvas to pull in everything automatically, or hit "+ New Course" to add one by hand. Upload PDFs and syllabi to each course — the AI needs them to build flashcards, quizzes, and notes.', pos:'bottom'},

      {url:'/student/plan', sel:'h1', title:'AI Study Plan', desc:'Your personalized daily plan. The AI weighs every exam, course difficulty, and weak topic, then schedules exactly what to study today.', pos:'bottom'},

      {url:'/student/focus', sel:'h1', title:'Focus Mode — where XP is earned', desc:'Pomodoro, pages-read, and custom sessions. This is the ONLY way to earn XP now (along with Exchange). The harder the course, the more XP per session.', pos:'bottom'},
      {url:'/student/focus', sel:'.mode-btn,#start-btn', title:'Pick a mode, start the timer', desc:'Pomodoro gives a 1.2× XP multiplier. Pages-read mode earns XP per page. All sessions feed your focus-hour badges.', pos:'bottom'},
      {url:'/student/focus', sel:'#focus-guard-card', title:'Focus Guard — block distractions for real', desc:'Install the free browser extension and the moment you start a timer, Instagram, TikTok, Twitter, Reddit and more get blocked automatically. YouTube stays allowed — because you might be studying.', pos:'top'},

      {url:'/student/flashcards', sel:'h1', title:'AI Flashcards (SRS)', desc:'Flashcards auto-generated from your uploaded course files, scheduled with spaced-repetition. Review daily and you literally cannot forget.', pos:'bottom'},

      {url:'/student/quizzes', sel:'h1', title:'AI Quizzes', desc:'Practice quizzes built from your course materials — perfect exam prep. Every question traces back to your own uploads, not random internet fluff.', pos:'bottom'},

      {url:'/student/notes', sel:'h1', title:'AI Notes', desc:'Drop any PDF or DOCX and get clean, organized study notes in seconds. You can also generate notes from course files with one click.', pos:'bottom'},

      {url:'/student/chat', sel:'h1', title:'AI Tutor (grounded)', desc:'Unlike ChatGPT, this tutor can ONLY answer using the files you uploaded. Zero hallucinations. Ask it to explain chapter 4, quiz you, or walk through a problem.', pos:'bottom'},

      {url:'/student/essay', sel:'h1', title:'Essay Assistant', desc:'Paste any draft. Get brutally honest feedback on thesis strength, structure, grammar, and flow — plus a rewritten intro you can actually use.', pos:'bottom'},

      {url:'/student/panic', sel:'h1', title:'Panic Mode — for exam emergencies', desc:'Exam tomorrow and nothing\\'s done? Fill in hours-available and topics, and get a ruthless, minute-by-minute cram plan. Use it, not abuse it.', pos:'bottom'},

      {url:'/student/exchange', sel:'h1', title:'Study Exchange', desc:'Share your best notes with other students. Fork theirs. Every time someone uses your note, you earn XP. Great notes = great rank.', pos:'bottom'},

      {url:'/student/leaderboard', sel:'h1', title:'Leaderboards & Ranks', desc:'You\\'re looking at the global leaderboard. Filter by your university, or create a private group with friends using the invite codes below.', pos:'bottom'},
      {url:'/student/leaderboard', sel:'table,[class*="card"]', title:'35 ranks to climb', desc:'Initiates IV → Apprentices → Scholars → Researchers → Academics → Masterminds → Grand Scholars → Legends — then the elite tier (Arch Scholars, High Sages, Oracles of Knowledge). Every rank-up pops a toast notification.', pos:'top'},

      {url:'/student/schedule', sel:'h1', title:'Weekly Schedule', desc:'Drag-and-drop your weekly schedule — classes, study blocks, deadlines. Changes save automatically.', pos:'bottom'},

      {url:'/student/exams', sel:'h1', title:'Exams Dashboard', desc:'Every upcoming exam across every course, sorted by urgency. Never blindsided again.', pos:'bottom'},

      {url:'/student/weak-topics', sel:'h1', title:'Weak Topics Radar', desc:'The AI tracks which topics you score lowest on in quizzes and surfaces them here. Focus your review where it matters.', pos:'bottom'},

      {url:'/student/gpa', sel:'h1', title:'GPA Calculator', desc:'Track your current GPA and forecast what grades you need to hit your target. Essential at midterms.', pos:'bottom'},

      {url:'/student/achievements', sel:'h1', title:'XP & Achievements', desc:'Full XP history, earned badges, and your current rank with progress to the next one. Shareable.', pos:'bottom'},

      {url:'/mail-hub', sel:'h1', title:'Mail Hub', desc:'Connect your Gmail/Outlook. AI sorts emails by priority so you don\\'t miss anything from your professors.', pos:'bottom'},

      {url:'/student/settings', sel:'h1', title:'Settings — you\\'re home', desc:'Change your theme, language, daily-email time, and restart this tour anytime. You\\'re ready to crush this semester.', pos:'bottom'}
    ];

    var BUSINESS_STEPS = [
      {url:'/dashboard', sel:'h1,h2', title:'Your Outreach Dashboard', desc:'Command center for every campaign. Pipeline metrics, recent activity, and quick actions all live here. We\\'ll tour every feature and show you each page for real.', pos:'bottom'},
      {url:'/dashboard', sel:'[class*="stat"]', title:'Live metrics', desc:'Total sent, open rate, reply rate, bounced — updated every 15 seconds so you always know campaign health.', pos:'bottom'},

      {url:'/campaigns', sel:'h1', title:'Campaigns', desc:'Every campaign you\\'ve built lives here. Draft, active, paused, or finished — all in one list with live stats.', pos:'bottom'},

      {url:'/campaign/new', sel:'h1', title:'New Campaign — the AI writer', desc:'Describe your audience, tone, and offer. The AI drafts a multi-step sequence with follow-ups and A/B variants. Edit anything before launch.', pos:'bottom'},

      {url:'/contacts', sel:'h1', title:'Contacts', desc:'Your CRM. Import leads via CSV, tag them, segment by industry or status. Bad addresses get auto-flagged before they hurt your deliverability.', pos:'bottom'},

      {url:'/inbox', sel:'h1', title:'Unified Inbox', desc:'Every reply from every campaign lands here. Mark interested leads, snooze, or jump straight to the contact record.', pos:'bottom'},

      {url:'/mail-hub', sel:'h1', title:'Mail Hub', desc:'Connect Gmail, Outlook, or any IMAP account. Monitor deliverability, warm-up status, and sending limits per inbox.', pos:'bottom'},

      {url:'/subject-optimizer', sel:'h1', title:'Subject-Line Optimizer', desc:'Paste a subject line and get an open-rate score, spam risk, and hook-strength rating. Fix it before you send 1,000 emails with a dud.', pos:'bottom'},

      {url:'/reply-intel', sel:'h1', title:'Reply Intelligence', desc:'Paste any reply you got. The AI classifies it as interested / objection / not-a-fit and drafts the perfect follow-up in your tone.', pos:'bottom'},

      {url:'/deliverability', sel:'h1', title:'Deliverability Checker', desc:'Scan any email for spam-trigger words, broken links, SPF/DKIM issues, and HTML problems before you hit send.', pos:'bottom'},

      {url:'/ab-tests', sel:'h1', title:'A/B Tests', desc:'Test subject lines, openers, or CTAs. MachReach picks the winner automatically once it has statistical significance.', pos:'bottom'},

      {url:'/smart-times', sel:'h1', title:'Smart Send Times', desc:'The AI learns when each recipient opens emails and schedules sends to match. Massive open-rate lift, zero effort.', pos:'bottom'},

      {url:'/calendar', sel:'h1', title:'Calendar', desc:'Every scheduled campaign, follow-up, and queued send on a single calendar. Never double-book an audience.', pos:'bottom'},

      {url:'/export', sel:'h1', title:'Export', desc:'Pull your campaigns, contacts, or analytics to CSV for reporting or CRM import. Raw data, no lock-in.', pos:'bottom'},

      {url:'/billing', sel:'h1', title:'Billing', desc:'Manage your plan, view invoices, and update payment methods. Upgrade/downgrade anytime.', pos:'bottom'},

      {url:'/settings', sel:'h1', title:'Settings — you\\'re home', desc:'Connect your email provider, set tracking preferences, manage templates, and restart this tour. You\\'re ready to launch.', pos:'bottom'}
    ];

    var STEPS = ACCOUNT_TYPE === 'student' ? STUDENT_STEPS : BUSINESS_STEPS;

    /* ---------- STATE HELPERS ---------- */
    function loadState() {
      try { return JSON.parse(localStorage.getItem(STATE_KEY) || 'null'); } catch(e) { return null; }
    }
    function saveState(s) { localStorage.setItem(STATE_KEY, JSON.stringify(s)); }
    function clearState() { localStorage.removeItem(STATE_KEY); }

    var state = loadState();
    var path = window.location.pathname.replace(/\\/$/, '') || '/';

    /* Define tour controls UP FRONT so inline onclick handlers always resolve,
       even on the very first step after clicking "Start Tour". */
    window._mrTutNext = function() {
      if (!state) state = { type: ACCOUNT_TYPE, step: 0, running: true };
      state.step++;
      saveState(state);
      if (state.step >= STEPS.length) {
        finishTour();
      } else {
        showStep(state.step);
      }
    };
    window._mrTutPrev = function() {
      if (state && state.step > 0) {
        state.step--;
        saveState(state);
        showStep(state.step);
      }
    };
    window._mrTutEnd = function() {
      localStorage.setItem(DONE_KEY, '1');
      clearState();
      var w = document.getElementById('mr-tut-welcome');
      if (w && w.parentNode) w.remove();
      cleanup();
    };

    /* ---------- WELCOME MODAL (only shown on start page, first time) ---------- */
    function isStartPage() {
      if (ACCOUNT_TYPE === 'student') return path === '/student' || path === '/student/';
      return path === '/dashboard' || path === '/dashboard/';
    }

    if (!state || !state.running) {
      if (localStorage.getItem(DONE_KEY)) return;
      if (!isStartPage()) return;

      var welcome = document.createElement('div');
      welcome.id = 'mr-tut-welcome';
      var emoji = ACCOUNT_TYPE === 'student' ? '&#127891;' : '&#128640;';
      var kind  = ACCOUNT_TYPE === 'student' ? 'study dashboard' : 'outreach dashboard';
      welcome.innerHTML = '<div class="welcome-card">'
        + '<div style="font-size:52px;">' + emoji + '</div>'
        + '<h2>Welcome to MachReach!</h2>'
        + '<p>This is a <strong>guided tour</strong> — we\\'ll walk you through every feature of your ' + kind + ' by actually taking you to each page. Takes ~2 minutes.</p>'
        + '<button class="wbtn wbtn-start" onclick="window._mrTutStart()">Start Tour</button>'
        + '<button class="wbtn wbtn-skip" onclick="window._mrTutEnd()">Skip</button>'
        + '</div>';
      document.body.appendChild(welcome);

      window._mrTutStart = function() {
        welcome.remove();
        state = { type: ACCOUNT_TYPE, step: 0, running: true };
        saveState(state);
        showStep(0);
      };
      return;
    }

    /* Tour is running. Either we're on the right page (show step) or we need to resume after navigation. */
    if (state.type !== ACCOUNT_TYPE) return; /* different account — ignore */

    /* Auto-resume: wait for DOM to be ready-ish, then show step */
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){ setTimeout(function(){ showStep(state.step); }, 250); });
    } else {
      setTimeout(function(){ showStep(state.step); }, 250);
    }

    /* ---------- CORE ENGINE ---------- */
    function cleanup() {
      var ov = document.getElementById('mr-tut-overlay');
      if (ov) ov.classList.remove('active');
      var hl = document.getElementById('mr-tut-highlight');
      if (hl) hl.style.display = 'none';
      var tp = document.getElementById('mr-tut-tooltip');
      if (tp) { tp.style.display = 'none'; tp.classList.remove('show'); }
      var bk = document.getElementById('mr-tut-backdrop');
      if (bk) bk.style.display = 'none';
    }

    function finishTour() {
      localStorage.setItem(DONE_KEY, '1');
      clearState();
      cleanup();
      var fin = document.createElement('div');
      fin.id = 'mr-tut-welcome';
      fin.innerHTML = '<div class="welcome-card">'
        + '<div style="font-size:52px;">&#127881;</div>'
        + '<h2>You\\'re all set!</h2>'
        + '<p>You\\'ve seen every major feature. Time to actually use them.</p>'
        + '<p style="font-size:12px;color:#A5B4FC;">Tip: restart this tour anytime from Settings.</p>'
        + '<button class="wbtn wbtn-start" onclick="window.location=\\'' + END_PAGE + '\\'">Let\\'s Go!</button>'
        + '</div>';
      document.body.appendChild(fin);
    }

    function stepPathMatches(step) {
      var want = (step.url || '').replace(/\\/$/, '') || '/';
      return path === want || path.indexOf(want + '/') === 0;
    }

    function showStep(idx) {
      if (idx >= STEPS.length) { finishTour(); return; }
      var step = STEPS[idx];

      /* Navigate if we're on the wrong page */
      if (!stepPathMatches(step)) {
        window.location = step.url;
        return;
      }

      var el = null;
      var sels = (step.sel || '').split(',');
      for (var i = 0; i < sels.length; i++) {
        var s = sels[i].trim();
        if (!s) continue;
        el = document.querySelector(s);
        if (el) break;
      }
      /* If selector didn't match, retry a few times (page might still be rendering) */
      if (!el && !step._retries) {
        step._retries = 0;
      }
      if (!el) {
        step._retries = (step._retries || 0) + 1;
        if (step._retries < 10) {
          setTimeout(function(){ showStep(idx); }, 300);
          return;
        }
        /* Give up: skip to next */
        state.step++; saveState(state); showStep(state.step); return;
      }

      var ov = document.getElementById('mr-tut-overlay');
      ov.classList.add('active');
      document.getElementById('mr-tut-backdrop').style.display = 'block';

      var rect = el.getBoundingClientRect();
      var pad = 6;

      var hl = document.getElementById('mr-tut-highlight');
      hl.style.display = 'block';
      hl.classList.add('mr-tut-pulse');
      hl.style.top = (rect.top - pad) + 'px';
      hl.style.left = (rect.left - pad) + 'px';
      hl.style.width = (rect.width + pad * 2) + 'px';
      hl.style.height = (rect.height + pad * 2) + 'px';

      var progressDots = '';
      for (var j = 0; j < STEPS.length; j++) {
        var cls = j < idx ? 'done' : (j === idx ? 'active' : '');
        progressDots += '<span class="' + cls + '"></span>';
      }

      var tp = document.getElementById('mr-tut-tooltip');
      tp.style.display = 'block';
      tp.classList.remove('show');
      tp.innerHTML = '<div class="tut-step">Step ' + (idx + 1) + ' of ' + STEPS.length + '</div>'
        + '<div class="tut-title">' + step.title + '</div>'
        + '<div class="tut-desc">' + step.desc + '</div>'
        + '<div class="tut-btns">'
        + '<div id="mr-tut-progress">' + progressDots + '</div>'
        + (idx > 0 ? '<button class="tut-back" onclick="window._mrTutPrev()">Back</button>' : '')
        + '<button class="tut-skip" onclick="window._mrTutEnd()">Skip tour</button>'
        + '<button class="tut-next" onclick="window._mrTutNext()">' + (idx === STEPS.length - 1 ? 'Finish &#10003;' : 'Next &#8594;') + '</button>'
        + '</div>';

      var ttW = 360;
      tp.style.left = '0px'; tp.style.top = '0px'; tp.style.width = ttW + 'px';
      tp.style.visibility = 'hidden';
      var ttH = tp.offsetHeight || 220;
      tp.style.visibility = '';
      var ttLeft = Math.max(12, Math.min(rect.left + rect.width / 2 - ttW / 2, window.innerWidth - ttW - 12));
      var ttTop;
      var gap = 20;
      if (step.pos === 'bottom' && rect.bottom + pad + gap + ttH < window.innerHeight) {
        ttTop = rect.bottom + pad + gap;
      } else if (step.pos === 'top' && rect.top - pad - gap - ttH > 12) {
        ttTop = rect.top - pad - gap - ttH;
      } else {
        ttTop = Math.max(12, Math.min(rect.bottom + pad + gap, window.innerHeight - ttH - 12));
      }
      tp.style.left = ttLeft + 'px';
      tp.style.top = ttTop + 'px';
      tp.style.width = ttW + 'px';
      requestAnimationFrame(function() { tp.classList.add('show'); });

      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && document.getElementById('mr-tut-overlay').classList.contains('active')) {
        window._mrTutEnd();
      }
    });
  })();
  </script>
  {% endif %}

  <!-- Student i18n: Spanish translations (client-side) -->
  {% if lang == 'es' and account_type|default('business') == 'student' %}
  <script>
  (function(){
    var T = {
      // Nav
      "Dashboard": "Panel", "Courses": "Cursos", "Plan": "Plan", "Flashcards": "Tarjetas",
      "Quizzes": "Exámenes", "Notes": "Apuntes", "Tutor": "Tutor", "XP": "XP",
      "Mail": "Correo", "Focus Mode": "Modo Enfoque", "Exams": "Exámenes",
      "GPA Calculator": "Calculadora GPA", "Schedule": "Horario", "Weak Topics": "Temas Débiles",
      "Settings": "Ajustes", "Leaderboard": "Clasificación",
      // Achievements page
      "Achievements & Progress": "Logros y Progreso", "Level": "Nivel",
      "XP to next level": "XP para el siguiente nivel", "Day Streak": "Racha de Días",
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
      "3-day study streak": "Racha de estudio de 3 días",
      "7-day study streak": "Racha de estudio de 7 días",
      "30-day study streak": "Racha de estudio de 30 días",
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
      "Canvas LMS": "Canvas LMS", "Connected": "Conectado",
      "Not connected": "No conectado", "Manage Connection": "Administrar Conexión",
      "Connect Canvas": "Conectar Canvas",
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
      "Loading...": "Cargando...", "Error": "Error", "Success": "Éxito",
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
      "Drop a PDF onto Notes, Flashcards, Quizzes, or the AI Tutor — instant study material from your files.":
        "Suelta un PDF en Apuntes, Tarjetas, Exámenes o el Tutor IA — material de estudio al instante.",

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
      "Generation failed": "Falló la generación",
      "Network error": "Error de red",
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

      // Tutor
      "AI Tutor": "Tutor IA",

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

      // Campaigns
      "Campaigns": "Campañas", "New Campaign": "Nueva Campaña",
      "Campaign Name": "Nombre de Campaña", "Recipients": "Destinatarios",
      "Templates": "Plantillas", "Sequence": "Secuencia",
      "Open Rate": "Tasa de Apertura", "Reply Rate": "Tasa de Respuesta",
      "Sent at": "Enviado a las", "Scheduled": "Programado",
      "Send Now": "Enviar Ahora", "Schedule": "Programar",

      // Pricing / Billing
      "Pricing": "Precios", "Billing": "Facturación",
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
      "Coming soon": "Próximamente",

      // ── Dashboard headings (whole phrases — must come BEFORE word-level keys) ──
      "Today's Study Plan": "Plan de Estudio de Hoy",
      "Today's Plan": "Plan de Hoy",
      "Upcoming Exams": "Próximos Exámenes",
      "Upcoming Examens": "Próximos Exámenes",
      "Plan Progress": "Progreso del Plan",
      "Hours Focused": "Horas de Estudio",
      "Focus Hours": "Horas de Estudio",
      "day streak": "días de racha",
      "Day Streak": "Racha de Días",
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
      "Connect Canvas": "Conectar Canvas",
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
      "Network error": "Error de red",
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

      // ── AI Tutor Chat ──
      "Ask anything about your courses — your AI tutor uses your own notes and course material to help":
        "Pregunta lo que quieras sobre tus cursos — tu tutor de IA usa tus apuntes y materiales para ayudarte",
      "General (no specific course)": "General (sin curso específico)",
      "Select a course...": "Selecciona un curso...",
      "Send": "Enviar", "Clear history": "Borrar historial",
      "Extracting...": "Extrayendo...",
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
      "Connected": "Conectado", "Not connected": "No conectado",
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
      "Disconnect": "Desconectar", "Test Connection": "Probar Conexión",

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

    // Build a single regex of all phrase keys (longest first) — replaces whole
    // phrases only, with word boundaries, so we never mangle untranslated text
    // like "Today's Study Plan" -> "Hoy's Estudiar Plan".
    function _esc(s){ return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); }
    var _keys = Object.keys(T).sort(function(a,b){ return b.length - a.length; });
    var _re = new RegExp(
      '(^|[^A-Za-zÀ-ÿ0-9_])(' + _keys.map(_esc).join('|') + ')(?![A-Za-zÀ-ÿ0-9_])',
      'g'
    );
    function _replaceAll(txt){
      return txt.replace(_re, function(_m, pre, key){ return pre + (T[key] || key); });
    }

    function translate(el) {
      if (el.childElementCount === 0) {
        var raw = el.textContent;
        var txt = raw.trim();
        if (!txt) return;
        if (T[txt]) {
          el.textContent = raw.replace(txt, T[txt]);
          return;
        }
        var translated = _replaceAll(txt);
        if (translated !== txt) el.textContent = raw.replace(txt, translated);
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
    window.alert = function(msg) { origAlert(T[msg] || _replaceAll(String(msg))); };
  })();
  </script>
  {% endif %}

</body>
</html>"""


def _render(title: str, content: str, active_page: str = "", wide: bool = False, **kwargs):
    flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])
    nav = t_dict("nav")
    is_admin = False
    acct_type = session.get("account_type", "business")
    if _logged_in():
        c = get_client(session["client_id"])
        is_admin = bool(c and c.get("is_admin"))
        acct_type = (c.get("account_type") or "business") if c else acct_type
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
        lang=session.get("lang", "en"),
        is_admin=is_admin,
        account_type=acct_type,
    )

@app.route("/")
def index():
    if _logged_in():
        if session.get("account_type") == "student":
            return redirect(url_for("student_dashboard_page"))
        return redirect(url_for("dashboard"))
    return render_template_string(LAYOUT, title="MachReach — Study smarter. Sell faster.", logged_in=False, messages=[], active_page="home", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), wide=True, content=Markup(f"""
    <style>
      .mr-hero{{padding:110px 24px 60px;text-align:center}}
      .mr-hero h1{{font-size:60px;max-width:820px;margin:0 auto 20px;line-height:1.05;font-weight:900;letter-spacing:-1.5px}}
      .mr-hero h1 .g1{{background:linear-gradient(90deg,#A78BFA,#6366F1);-webkit-background-clip:text;background-clip:text;color:transparent}}
      .mr-hero h1 .g2{{background:linear-gradient(90deg,#F472B6,#F59E0B);-webkit-background-clip:text;background-clip:text;color:transparent}}
      .mr-hero p.sub{{max-width:640px;margin:0 auto 28px;color:var(--text-muted);font-size:18px;line-height:1.55}}
      .mr-switch{{display:inline-flex;background:rgba(148,163,184,.08);border:1px solid var(--border);border-radius:999px;padding:4px;margin:0 auto 40px;gap:4px}}
      .mr-switch button{{border:none;background:transparent;color:var(--text-muted);padding:10px 22px;border-radius:999px;cursor:pointer;font-weight:700;font-size:14px;transition:all .2s}}
      .mr-switch button.on{{color:#fff}}
      .mr-switch button.on.biz{{background:linear-gradient(135deg,#6366F1,#8B5CF6);box-shadow:0 8px 24px rgba(99,102,241,.35)}}
      .mr-switch button.on.stu{{background:linear-gradient(135deg,#F472B6,#F59E0B);box-shadow:0 8px 24px rgba(244,114,182,.35)}}
      .mr-panel{{display:none}}
      .mr-panel.on{{display:block;animation:mrFade .4s ease-out}}
      @keyframes mrFade{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
      .mr-cards{{max-width:1100px;margin:0 auto;padding:0 24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}}
      .mr-card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px;transition:all .2s;position:relative;overflow:hidden}}
      .mr-card:hover{{transform:translateY(-3px);border-color:var(--primary);box-shadow:0 16px 40px rgba(99,102,241,.12)}}
      .mr-card .icon{{font-size:28px;margin-bottom:10px}}
      .mr-card h3{{font-size:17px;margin:0 0 6px;font-weight:700}}
      .mr-card p{{font-size:13.5px;line-height:1.55;color:var(--text-muted);margin:0}}
      .mr-card.biz{{background:linear-gradient(135deg,rgba(99,102,241,.06),transparent)}}
      .mr-card.stu{{background:linear-gradient(135deg,rgba(244,114,182,.06),transparent)}}
      .mr-section-title{{text-align:center;margin:80px 0 8px;font-size:14px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--text-muted)}}
      .mr-section-h{{text-align:center;font-size:34px;font-weight:800;margin:0 0 14px;max-width:720px;margin-left:auto;margin-right:auto;letter-spacing:-.5px}}
      .mr-dual-cta{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-top:18px}}
      .mr-pill-biz,.mr-pill-stu{{padding:16px 28px;border-radius:14px;font-weight:700;font-size:15px;text-decoration:none;transition:all .2s;display:inline-flex;align-items:center;gap:8px}}
      .mr-pill-biz{{background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;box-shadow:0 10px 30px rgba(99,102,241,.35)}}
      .mr-pill-stu{{background:linear-gradient(135deg,#F472B6,#F59E0B);color:#fff;box-shadow:0 10px 30px rgba(244,114,182,.35)}}
      .mr-pill-biz:hover,.mr-pill-stu:hover{{transform:translateY(-2px);filter:brightness(1.08)}}
      .mr-stat-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;max-width:900px;margin:0 auto;padding:32px 24px 0}}
      .mr-stat{{text-align:center}}
      .mr-stat .num{{font-size:36px;font-weight:900;background:linear-gradient(135deg,#A78BFA,#F472B6);-webkit-background-clip:text;background-clip:text;color:transparent}}
      .mr-stat .lbl{{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:1.5px;font-weight:700;margin-top:4px}}
    </style>

    <div class="mr-hero">
      <h1>Two products, <span class="g1">one mission</span>.<br>Be the <span class="g2">best</span> at what you do.</h1>
      <p class="sub">MachReach runs AI-powered email outreach for businesses <strong>and</strong> an AI-powered study OS for students. Pick your side.</p>
      <div class="mr-dual-cta">
        <a href="/register?type=business" class="mr-pill-biz">&#128188; I'm a Business &rarr;</a>
        <a href="/register?type=student" class="mr-pill-stu">&#127891; I'm a Student &rarr;</a>
      </div>
      <p style="font-size:12px;color:var(--text-muted);margin-top:14px;">No credit card required &bull; Free plan available forever</p>
    </div>

    <!-- Stat row -->
    <div class="mr-stat-row">
      <div class="mr-stat"><div class="num">40+</div><div class="lbl">AI-powered tools</div></div>
      <div class="mr-stat"><div class="num">2</div><div class="lbl">Complete products</div></div>
      <div class="mr-stat"><div class="num">0$</div><div class="lbl">To get started</div></div>
      <div class="mr-stat"><div class="num">24/7</div><div class="lbl">On autopilot</div></div>
    </div>

    <!-- Product switch -->
    <div style="text-align:center;margin-top:60px">
      <div class="mr-switch">
        <button id="sw-biz" class="on biz" onclick="mrShowPanel('biz')">&#128188; For Business</button>
        <button id="sw-stu" onclick="mrShowPanel('stu')">&#127891; For Students</button>
      </div>
    </div>

    <!-- BUSINESS panel -->
    <div id="panel-biz" class="mr-panel on" style="max-width:1100px;margin:0 auto;padding:0 24px 40px">
      <div class="mr-section-title">For sales teams, agencies &amp; founders</div>
      <h2 class="mr-section-h">Email outreach that <span class="g1">actually gets replies</span>.</h2>
      <p style="text-align:center;color:var(--text-muted);font-size:16px;max-width:640px;margin:0 auto 28px;line-height:1.55">Write, send, track, and follow up on personalized multi-step email campaigns — all on autopilot.</p>

      <div class="mr-cards" style="margin-top:32px">
        <div class="mr-card biz"><div class="icon">&#129302;</div><h3>AI Campaign Writer</h3><p>Describe your audience. GPT-4 drafts a multi-step sequence with A/B variants, follow-ups, and personalization.</p></div>
        <div class="mr-card biz"><div class="icon">&#128233;</div><h3>Unified Mail Hub</h3><p>Connect Gmail, Outlook, and IMAP. Read, reply, and triage every inbox from one screen with AI sorting.</p></div>
        <div class="mr-card biz"><div class="icon">&#128200;</div><h3>Opens, Clicks &amp; Replies</h3><p>Track every interaction. See what's working and what to cut — in real time.</p></div>
        <div class="mr-card biz"><div class="icon">&#9889;</div><h3>Smart Send Times</h3><p>AI learns when each recipient is most likely to open, then schedules sends automatically.</p></div>
        <div class="mr-card biz"><div class="icon">&#128101;</div><h3>CRM &amp; Contacts</h3><p>Import via CSV or CRM, tag leads, segment by industry, and keep relationships organized.</p></div>
        <div class="mr-card biz"><div class="icon">&#128272;</div><h3>A/B Testing</h3><p>Test subject lines and body copy. MachReach picks the winner automatically.</p></div>
        <div class="mr-card biz"><div class="icon">&#128196;</div><h3>Subject-Line Optimizer</h3><p>Rate any subject line for open-rate, spam risk, and hook strength before you hit send.</p></div>
        <div class="mr-card biz"><div class="icon">&#128172;</div><h3>Reply Intelligence</h3><p>Auto-classify replies as interested / objection / not-a-fit and draft the perfect follow-up.</p></div>
        <div class="mr-card biz"><div class="icon">&#128737;</div><h3>Deliverability Checker</h3><p>Scan your message for spam triggers, SPF/DKIM issues, and link problems before it goes out.</p></div>
        <div class="mr-card biz"><div class="icon">&#128197;</div><h3>Scheduled Campaigns</h3><p>Queue campaigns to start at the perfect time. No manual activation, no missed windows.</p></div>
        <div class="mr-card biz"><div class="icon">&#128101;</div><h3>Team Collaboration</h3><p>Invite teammates to share campaigns, inbox, and contacts. Everyone stays in sync.</p></div>
        <div class="mr-card biz"><div class="icon">&#128736;</div><h3>CSV + CRM Import</h3><p>Drop in a spreadsheet or sync from your CRM. MachReach handles dedup, validation, and tagging.</p></div>
      </div>

      <div style="text-align:center;margin-top:32px">
        <a href="/register?type=business" class="mr-pill-biz">Start your first campaign free &rarr;</a>
      </div>
    </div>

    <!-- STUDENT panel -->
    <div id="panel-stu" class="mr-panel" style="max-width:1100px;margin:0 auto;padding:0 24px 40px">
      <div class="mr-section-title">For students who refuse to fail</div>
      <h2 class="mr-section-h">The <span class="g2">AI study OS</span> built for how you actually learn.</h2>
      <p style="text-align:center;color:var(--text-muted);font-size:16px;max-width:640px;margin:0 auto 28px;line-height:1.55">Sync Canvas. Upload your PDFs. Let AI build your plan, quizzes, flashcards, notes — and tutor you through the hard parts.</p>

      <div class="mr-cards" style="margin-top:32px">
        <div class="mr-card stu"><div class="icon">&#128218;</div><h3>Courses + Canvas Sync</h3><p>Auto-import every course, assignment, and due date. Or create courses manually. Upload syllabi &amp; PDFs.</p></div>
        <div class="mr-card stu"><div class="icon">&#128197;</div><h3>AI Study Plan</h3><p>Personalized daily plan built from your exams, workload, and priorities. Ticked off as you go.</p></div>
        <div class="mr-card stu"><div class="icon">&#127917;</div><h3>Focus Mode</h3><p>Pomodoro, pages-read, and custom sessions. Earn XP. Climb the rank ladder.</p></div>
        <div class="mr-card stu"><div class="icon">&#127183;</div><h3>AI Flashcards (SRS)</h3><p>Auto-generated from your uploads. Spaced-repetition so you never forget.</p></div>
        <div class="mr-card stu"><div class="icon">&#128221;</div><h3>AI Quizzes</h3><p>Practice quizzes built from your course files. Real exam prep, no fluff.</p></div>
        <div class="mr-card stu"><div class="icon">&#128214;</div><h3>AI Notes</h3><p>Drop a PDF or DOCX and get clean, organized study notes extracted automatically.</p></div>
        <div class="mr-card stu"><div class="icon">&#129302;</div><h3>AI Tutor (grounded)</h3><p>Chats only from YOUR uploaded files. No hallucinations, no random answers.</p></div>
        <div class="mr-card stu"><div class="icon">&#9999;&#65039;</div><h3>Essay Assistant</h3><p>Brutally honest feedback on thesis, structure, grammar, and flow. Rewritten intros included.</p></div>
        <div class="mr-card stu"><div class="icon">&#128680;</div><h3>Panic Mode</h3><p>Exam tomorrow? Get a ruthless cram plan in 10 seconds. Topic by topic, minute by minute.</p></div>
        <div class="mr-card stu"><div class="icon">&#127942;</div><h3>Leaderboards + Ranks</h3><p>Initiates &rarr; Apprentices &rarr; Scholars &rarr; Masterminds &rarr; Legends. 35 ranks to chase.</p></div>
        <div class="mr-card stu"><div class="icon">&#128218;</div><h3>Study Exchange</h3><p>Publish your notes. Fork other students' notes. Earn XP when yours get used.</p></div>
        <div class="mr-card stu"><div class="icon">&#128337;</div><h3>Schedule &amp; Weekly Planner</h3><p>Drag-and-drop weekly schedule. Block classes, study sessions, and deadlines.</p></div>
        <div class="mr-card stu"><div class="icon">&#127891;</div><h3>GPA Calculator</h3><p>Track current GPA, forecast what grades you need, and plan your semester.</p></div>
        <div class="mr-card stu"><div class="icon">&#127919;</div><h3>Weak Topics Radar</h3><p>AI spots what you struggle with based on quiz performance and focuses your review.</p></div>
        <div class="mr-card stu"><div class="icon">&#128221;</div><h3>Exams Dashboard</h3><p>Every upcoming exam, weighted by difficulty and days remaining. Never blindsided.</p></div>
        <div class="mr-card stu"><div class="icon">&#128736;</div><h3>Practice Problems</h3><p>Unlimited AI-generated practice, graded and explained step by step.</p></div>
        <div class="mr-card stu"><div class="icon">&#128233;</div><h3>Daily Study Email</h3><p>Morning briefing with today's plan, weak topics, and upcoming exams. Delivered to your inbox.</p></div>
        <div class="mr-card stu"><div class="icon">&#128206;</div><h3>Drag &amp; Drop Anywhere</h3><p>Drop a PDF onto Notes, Flashcards, Quizzes, or the AI Tutor — instant study material from your files.</p></div>
      </div>

      <div style="text-align:center;margin-top:32px">
        <a href="/register?type=student" class="mr-pill-stu">Study smarter, free &rarr;</a>
      </div>
    </div>

    <!-- How it works -->
    <div style="max-width:1000px;margin:0 auto;padding:80px 24px 40px;">
      <h2 style="text-align:center;font-size:32px;font-weight:800;margin-bottom:8px;">How it works</h2>
      <p style="text-align:center;color:var(--text-muted);margin-bottom:48px;font-size:16px;">Same simple flow — whether you're selling or studying.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:32px;">
        <div style="text-align:center;">
          <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;margin:0 auto 16px;box-shadow:0 10px 30px rgba(99,102,241,.3)">1</div>
          <h3 style="font-size:18px;margin-bottom:6px;">Tell us what you're doing</h3>
          <p style="font-size:14px;color:var(--text-muted);line-height:1.6;">Your target audience — or your courses and exams. Two minutes, tops.</p>
        </div>
        <div style="text-align:center;">
          <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#F472B6,#F59E0B);color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;margin:0 auto 16px;box-shadow:0 10px 30px rgba(244,114,182,.3)">2</div>
          <h3 style="font-size:18px;margin-bottom:6px;">AI builds everything</h3>
          <p style="font-size:14px;color:var(--text-muted);line-height:1.6;">Campaigns, flashcards, quizzes, notes, plans, feedback — all generated for you.</p>
        </div>
        <div style="text-align:center;">
          <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#22D3EE,#6366F1);color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;margin:0 auto 16px;box-shadow:0 10px 30px rgba(34,211,238,.3)">3</div>
          <h3 style="font-size:18px;margin-bottom:6px;">You win</h3>
          <p style="font-size:14px;color:var(--text-muted);line-height:1.6;">Replies roll in. Grades go up. MachReach tracks everything in the background.</p>
        </div>
      </div>
    </div>

    <!-- Pricing teaser -->
    <div style="max-width:700px;margin:0 auto;padding:40px 24px 72px;text-align:center;">
      <h2 style="font-size:32px;font-weight:800;margin-bottom:8px;">Simple, transparent pricing</h2>
      <p style="color:var(--text-muted);font-size:16px;margin-bottom:32px;">Start free. Upgrade when you're ready.</p>
      <div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap;">
        <div class="card" style="flex:1;min-width:180px;max-width:220px;text-align:center;padding:28px;">
          <div style="font-size:14px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;">Free</div>
          <div style="font-size:40px;font-weight:800;margin:8px 0;">$0</div>
          <div style="font-size:13px;color:var(--text-muted);">Core features, limited AI</div>
        </div>
        <div class="card" style="flex:1;min-width:180px;max-width:220px;text-align:center;padding:28px;border:2px solid var(--primary);">
          <div style="font-size:14px;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:1px;">Pro</div>
          <div style="font-size:40px;font-weight:800;margin:8px 0;">$20</div>
          <div style="font-size:13px;color:var(--text-muted);">Unlock everything</div>
        </div>
        <div class="card" style="flex:1;min-width:180px;max-width:220px;text-align:center;padding:28px;">
          <div style="font-size:14px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;">Unlimited</div>
          <div style="font-size:40px;font-weight:800;margin:8px 0;">$40</div>
          <div style="font-size:13px;color:var(--text-muted);">Zero limits, ever</div>
        </div>
      </div>
      <a href="/pricing" class="btn btn-outline" style="margin-top:24px;">See all plans &rarr;</a>
    </div>

    <!-- Final CTA -->
    <div style="background:linear-gradient(135deg,#6366F1,#8B5CF6 50%,#F472B6);padding:72px 24px;text-align:center;border-radius:var(--radius);margin:0 24px 48px;">
      <h2 style="font-size:36px;font-weight:900;color:#fff;margin-bottom:12px;letter-spacing:-.5px">Pick your side. Start winning.</h2>
      <p style="color:rgba(255,255,255,0.85);font-size:16px;margin-bottom:28px;">Free during beta. No credit card. Full access.</p>
      <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">
        <a href="/register?type=business" class="btn btn-lg" style="background:#fff;color:#6366F1;font-weight:700;font-size:15px;padding:14px 28px;">&#128188; Business account &rarr;</a>
        <a href="/register?type=student" class="btn btn-lg" style="background:rgba(255,255,255,.14);color:#fff;font-weight:700;font-size:15px;padding:14px 28px;border:1px solid rgba(255,255,255,.3)">&#127891; Student account &rarr;</a>
      </div>
    </div>

    <script>
      window.mrShowPanel = function(which) {{
        document.getElementById('panel-biz').classList.toggle('on', which === 'biz');
        document.getElementById('panel-stu').classList.toggle('on', which === 'stu');
        var bb = document.getElementById('sw-biz'), sb = document.getElementById('sw-stu');
        bb.classList.toggle('on', which === 'biz');
        bb.classList.toggle('biz', which === 'biz');
        sb.classList.toggle('on', which === 'stu');
        sb.classList.toggle('stu', which === 'stu');
      }};
    </script>
    """))

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        business = request.form.get("business", "").strip()
        account_type = request.form.get("account_type", "business").strip()
        if account_type not in ("business", "student"):
            account_type = "business"
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
            flash(("success", "Account created! Please check your email to verify your address before logging in."))
            return redirect(url_for("login"))
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
    return render_template_string(LAYOUT, title="Register", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="register", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.create_account")}</h1>
        <p class="subtitle">{t("auth.create_subtitle")}</p>
        <form method="post">
          <div class="form-group">
            <label>I'm signing up as</label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px;" id="type-selector">
              <label style="display:flex;align-items:center;gap:10px;padding:14px 16px;border:2px solid var(--primary);border-radius:var(--radius-sm);cursor:pointer;background:var(--primary-light);transition:all .2s;" id="type-business" onclick="selectType('business')">
                <input type="radio" name="account_type" value="business" checked style="accent-color:var(--primary);">
                <div>
                  <div style="font-weight:700;font-size:14px;color:var(--text);">&#128188; Business</div>
                  <div style="font-size:11px;color:var(--text-muted);font-weight:400;">Email outreach &amp; campaigns</div>
                </div>
              </label>
              <label style="display:flex;align-items:center;gap:10px;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;background:var(--card);transition:all .2s;" id="type-student" onclick="selectType('student')">
                <input type="radio" name="account_type" value="student" style="accent-color:var(--primary);">
                <div>
                  <div style="font-weight:700;font-size:14px;color:var(--text);">&#127891; Student</div>
                  <div style="font-size:11px;color:var(--text-muted);font-weight:400;">AI study planner &amp; Canvas</div>
                </div>
              </label>
            </div>
          </div>
          <div class="form-group"><label>{t("auth.full_name")}</label><input name="name" placeholder="John Doe" required></div>
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="john@company.com" required></div>
          <div class="form-group"><label>{t("auth.password")}</label><input name="password" type="password" placeholder="At least 6 characters" required minlength="6"></div>
          <div class="form-group" id="business-field"><label>{t("auth.business_name")} <span style="font-weight:400;text-transform:none;color:var(--text-muted);">({t("auth.optional")})</span></label><input name="business" placeholder="Acme Inc."></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.create_btn")}</button>
          <p style="font-size:11px;color:var(--text-muted);text-align:center;margin-top:12px;line-height:1.6;">By creating an account, you agree to our <a href="/terms" style="color:var(--primary);">Terms of Service</a> and <a href="/privacy" style="color:var(--primary);">Privacy Policy</a>.</p>
        </form>
        <script>
        function selectType(t){{
          var bus=document.getElementById('type-business'),stu=document.getElementById('type-student'),bf=document.getElementById('business-field');
          if(t==='student'){{stu.style.border='2px solid var(--primary)';stu.style.background='var(--primary-light)';bus.style.border='2px solid var(--border)';bus.style.background='var(--card)';bf.style.display='none';}}
          else{{bus.style.border='2px solid var(--primary)';bus.style.background='var(--primary-light)';stu.style.border='2px solid var(--border)';stu.style.background='var(--card)';bf.style.display='block';}}
        }}
        </script>
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
            flash(("warning", "Please verify your email before logging in. Check your inbox or resend the verification link below."))
            return redirect(url_for("login"))
        _maybe_upgrade_hash(client["id"], password, client["password"])
        _log_security("LOGIN_OK", client_id=client["id"], email=email)
        # Preserve team invite token across session clear
        pending_token = session.get("team_invite_token")
        session.clear()
        session["client_id"] = client["id"]
        session["client_name"] = client["name"]
        session["account_type"] = client.get("account_type", "business")
        # Check for pending team invite
        if pending_token:
            return redirect(url_for("team_accept_invite", token=pending_token))
        if session["account_type"] == "student":
            return redirect(url_for("student_dashboard_page"))
        return redirect(url_for("dashboard"))
    return render_template_string(LAYOUT, title="Login", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="login", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.welcome_back")}</h1>
        <p class="subtitle">{t("auth.sign_in_desc")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="john@company.com" required></div>
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
    flash(("info", "If the email is registered, a new verification link has been sent."))
    return redirect(url_for("login"))


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
        active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"),
        content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.reset_title")}</h1>
        <p class="subtitle">{t("auth.reset_desc")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="john@company.com" required></div>
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
        active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"),
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

@app.route("/api/team/invite", methods=["POST"])
def api_team_invite():
    if not _logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    role = data.get("role", "member")
    if role not in ("member", "viewer"):
        role = "member"
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    from outreach.db import invite_team_member
    client = get_client(session["client_id"])
    if client["email"].lower() == email:
        return jsonify({"error": "You can't invite yourself"}), 400
    campaign_id = data.get("campaign_id")
    if campaign_id is not None:
        campaign_id = int(campaign_id)
    result = invite_team_member(session["client_id"], email, role, campaign_id=campaign_id)
    if "error" in result:
        return jsonify(result), 409
    # Build invite link
    invite_url = f"{request.host_url.rstrip('/')}/team/accept/{result['token']}"

    # Send the invite email using system SMTP (not client's personal account)
    from outreach.sender import send_email as _send_email
    invite_body = (
        f"Hi,\n\n"
        f"{client['name'] or client['email']} has invited you to join their team on MachReach as a {role}.\n\n"
        f"Click the link below to accept the invitation:\n"
        f"{invite_url}\n\n"
        f"If you don't have an account yet, you'll be able to sign up first.\n\n"
        f"— MachReach"
    )
    _send_email(to_email=email, subject=f"You're invited to join {client['name'] or 'a team'} on MachReach",
                body_text=invite_body, from_name="MachReach")

    return jsonify({"ok": True, "invite_url": invite_url, "email": email, "role": role})


@app.route("/team/accept/<token>")
def team_accept_invite(token):
    if not _logged_in():
        flash(("info", "Please log in or sign up to accept the team invite."))
        session["team_invite_token"] = token
        return redirect(url_for("login"))
    from outreach.db import accept_team_invite
    result = accept_team_invite(token, session["client_id"])
    if result:
        flash(("success", "You've joined the team! You can now access shared campaigns and data."))
    else:
        flash(("error", "Invalid or expired invite link."))
    return redirect(url_for("dashboard"))


@app.route("/api/team/<int:member_id>/remove", methods=["DELETE"])
def api_team_remove(member_id):
    if not _logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    from outreach.db import remove_team_member
    ok = remove_team_member(member_id, session["client_id"])
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/team", methods=["GET"])
def api_team_list():
    if not _logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    from outreach.db import get_team_members
    members = get_team_members(session["client_id"])
    return jsonify(members)


# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    if not _logged_in():
        return redirect(url_for("login"))
    if session.get("account_type") == "student":
        return redirect(url_for("student_dashboard_page"))

    data_cid = _effective_client_id()
    campaigns = get_campaigns(data_cid)

    # Also include campaign-scoped team access
    from outreach.db import get_team_campaign_ids
    shared_camp_ids = get_team_campaign_ids(session["client_id"])
    if shared_camp_ids and data_cid == session["client_id"]:
        # Not a full-access member, but has campaign-scoped invites
        shared_camps = [get_campaign(cid) for cid in shared_camp_ids]
        shared_camps = [c for c in shared_camps if c]
        existing_ids = {c["id"] for c in campaigns}
        for c in shared_camps:
            if c["id"] not in existing_ids:
                campaigns.append(c)

    gstats = get_global_stats(data_cid)

    # Check if user has connected an email account
    from outreach.db import get_email_accounts, get_contacts
    accounts = get_email_accounts(data_cid)
    has_accounts = len(accounts) > 0
    has_contacts = len(get_contacts(data_cid)) > 0
    has_campaigns = len(campaigns) > 0
    has_sent = gstats.get("total_sent", 0) > 0

    # Onboarding progress
    onboarding_steps_done = sum([has_accounts, has_contacts, has_campaigns, has_sent])
    onboarding_complete = onboarding_steps_done == 4

    # Usage info for plan banner
    from outreach.db import get_subscription, get_usage
    from outreach.config import PLAN_LIMITS
    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    email_limit = limits["emails_per_month"]
    emails_used = usage.get("emails_sent", 0)
    plan_label = plan.capitalize()
    if email_limit == -1:
        usage_text = f"<b>{plan_label}</b> plan &middot; {emails_used:,} emails sent this month (unlimited)"
    else:
        pct = min(emails_used / email_limit * 100, 100) if email_limit else 0
        usage_text = f"<b>{plan_label}</b> plan &middot; {emails_used:,} / {email_limit:,} emails ({pct:.0f}%)"
    upgrade_cta = "" if plan != "free" else ' &middot; <a href="/billing" style="color:var(--primary);font-weight:600;">Upgrade</a>'

    rows = ""
    for c in campaigns:
        stats = get_campaign_stats(c["id"])
        sc = {"active": "badge-green", "draft": "badge-gray", "paused": "badge-yellow", "completed": "badge-blue"}.get(c["status"], "badge-gray")
        pct = (stats["emails_sent"] / stats["total_contacts"] * 100) if stats["total_contacts"] else 0
        bar_class = "bar-green" if c["status"] == "active" else "bar-purple"
        rows += f"""<tr>
          <td>
            <a href="/campaign/{c['id']}" style="color:var(--primary);font-weight:600;text-decoration:none;">{_esc(c['name'])}</a>
            <div class="progress-wrap" style="width:100px;"><div class="progress-bar {bar_class}" style="width:{min(pct,100):.0f}%"></div></div>
          </td>
          <td><span class="badge {sc}">{c['status']}</span></td>
          <td>{stats['total_contacts']}</td>
          <td>{stats['emails_sent']}</td>
          <td>{stats['open_rate']:.0%}</td>
          <td>{stats['reply_rate']:.0%}</td>
          <td style="text-align:right;">
            <div class="btn-group">
              <a href="/campaign/{c['id']}" class="btn btn-outline btn-sm">View</a>
              <form method="post" action="/campaign/{c['id']}/duplicate" class="confirm-form"><button class="btn btn-ghost btn-sm" title="Duplicate">&#128203;</button></form>
              <form method="post" action="/campaign/{c['id']}/delete" class="confirm-form" onsubmit="return confirm('Delete this campaign and all its data?')"><button class="btn btn-ghost btn-sm" title="Delete" style="color:var(--red);">&#128465;</button></form>
            </div>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="7">
          <div class="empty">
            <div class="empty-icon">&#128235;</div>
            <h3>No campaigns yet</h3>
            <p>Create your first campaign to start sending outreach emails.</p>
            <a href="/campaign/new" class="btn btn-primary mt-2">+ New Campaign</a>
          </div>
        </td></tr>"""

    return _render(t("dash.title"), """
    <div class="page-header">
      <h1>{{page_title}}</h1>
    </div>

    <div style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 20px;margin-bottom:20px;font-size:14px;color:var(--text-muted);display:flex;align-items:center;justify-content:space-between;">
      <span>{{usage_text}}{{upgrade_cta}}</span>
      <a href="/billing" class="btn btn-ghost btn-sm">&#128179; Manage Plan</a>
    </div>

    {% if not onboarding_complete %}
    <div class="card" style="border:2px solid var(--primary);background:linear-gradient(135deg, rgba(99,102,241,0.04), rgba(124,58,237,0.04));">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
        <div>
          <h2 style="margin:0;font-size:20px;">&#127919; Get Started with MachReach</h2>
          <p style="margin:4px 0 0;color:var(--text-muted);font-size:14px;">Complete these steps to launch your first outreach campaign</p>
        </div>
        <div style="background:var(--primary);color:#fff;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:600;">
          {{onboarding_steps_done}} / 4 complete
        </div>
      </div>
      <div style="background:var(--border-light);border-radius:8px;height:8px;margin-bottom:20px;overflow:hidden;">
        <div style="background:linear-gradient(90deg,var(--primary),#7C3AED);height:100%;border-radius:8px;width:{{onboarding_pct}}%;transition:width 0.5s;"></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;">
        <div style="padding:18px;border-radius:10px;border:1px solid {% if has_accounts %}var(--green){% else %}var(--primary){% endif %};background:{% if has_accounts %}var(--green-light){% else %}var(--card){% endif %};">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:18px;">{% if has_accounts %}&#9989;{% else %}1&#65039;&#8419;{% endif %}</span>
            <strong style="font-size:14px;{% if has_accounts %}color:var(--green-dark);{% endif %}">Connect your email</strong>
          </div>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">Add your Gmail, Yahoo, or Outlook account so MachReach can send emails on your behalf.</p>
          {% if not has_accounts %}
          <a href="/settings" class="btn btn-primary btn-sm">&#128231; Add Email Account</a>
          {% endif %}
        </div>
        <div style="padding:18px;border-radius:10px;border:1px solid {% if has_contacts %}var(--green){% elif has_accounts %}var(--primary){% else %}var(--border){% endif %};background:{% if has_contacts %}var(--green-light){% else %}var(--card){% endif %};{% if not has_accounts %}opacity:0.6;{% endif %}">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:18px;">{% if has_contacts %}&#9989;{% else %}2&#65039;&#8419;{% endif %}</span>
            <strong style="font-size:14px;{% if has_contacts %}color:var(--green-dark);{% endif %}">Import contacts</strong>
          </div>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">Add people to reach out to — paste emails, upload a CSV, or add them one by one.</p>
          {% if has_accounts and not has_contacts %}
          <a href="/contacts" class="btn btn-primary btn-sm">&#128101; Add Contacts</a>
          {% endif %}
        </div>
        <div style="padding:18px;border-radius:10px;border:1px solid {% if has_campaigns %}var(--green){% elif has_contacts %}var(--primary){% else %}var(--border){% endif %};background:{% if has_campaigns %}var(--green-light){% else %}var(--card){% endif %};{% if not has_contacts %}opacity:0.6;{% endif %}">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:18px;">{% if has_campaigns %}&#9989;{% else %}3&#65039;&#8419;{% endif %}</span>
            <strong style="font-size:14px;{% if has_campaigns %}color:var(--green-dark);{% endif %}">Create a campaign</strong>
          </div>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">Describe your business and audience — AI generates a personalized email sequence for you.</p>
          {% if has_contacts and not has_campaigns %}
          <a href="/campaign/new" class="btn btn-primary btn-sm">&#128640; Create Campaign</a>
          {% endif %}
        </div>
        <div style="padding:18px;border-radius:10px;border:1px solid {% if has_sent %}var(--green){% elif has_campaigns %}var(--primary){% else %}var(--border){% endif %};background:{% if has_sent %}var(--green-light){% else %}var(--card){% endif %};{% if not has_campaigns %}opacity:0.6;{% endif %}">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:18px;">{% if has_sent %}&#9989;{% else %}4&#65039;&#8419;{% endif %}</span>
            <strong style="font-size:14px;{% if has_sent %}color:var(--green-dark);{% endif %}">Launch &amp; track</strong>
          </div>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">Activate your campaign and monitor opens, replies, and conversions in real time.</p>
          {% if has_campaigns and not has_sent %}
          <a href="/campaign/{{first_campaign_id}}" class="btn btn-primary btn-sm">&#9889; Activate Campaign</a>
          {% endif %}
        </div>
      </div>
    </div>
    {% endif %}

    <div class="stats-grid">
      <div class="stat-card stat-purple"><div class="num" data-stat="total_campaigns">{{g.total_campaigns}}</div><div class="label">{{lbl_campaigns}}</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="active_campaigns">{{g.active_campaigns}}</div><div class="label">{{lbl_active}}</div></div>
      <div class="stat-card stat-blue"><div class="num" data-stat="total_sent">{{g.total_sent}}</div><div class="label">{{lbl_emails_sent}}</div></div>
      <div class="stat-card stat-purple"><div class="num" data-stat="open_rate_fmt">{{g_open_rate}}</div><div class="label">{{lbl_open_rate}}</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="total_replied">{{g.total_replied}}</div><div class="label">{{lbl_replies}}</div></div>
      <div class="stat-card stat-yellow"><div class="num" data-stat="reply_rate_fmt">{{g_reply_rate}}</div><div class="label">Reply Rate</div></div>
      <div class="stat-card stat-red"><div class="num" data-stat="total_bounced">{{g.total_bounced}}</div><div class="label">Bounced</div></div>
    </div>

    <!-- Analytics Chart -->
    <div class="card" style="margin-bottom:22px;">
      <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
        <h2>&#128200; Email Activity</h2>
        <select id="chart-range" onchange="loadChart(this.value)" style="font-size:13px;padding:6px 12px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);color:var(--text);">
          <option value="7">Last 7 days</option>
          <option value="30" selected>Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
      </div>
      <div style="position:relative;height:280px;padding:8px;">
        <canvas id="activityChart"></canvas>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>{{lbl_campaigns}}</h2>
        <a href="/campaign/new" class="btn btn-primary btn-sm">+ {{lbl_new_campaign}}</a>
      </div>
      <table>
        <thead><tr><th>{{lbl_name}}</th><th>{{lbl_status}}</th><th>Contacts</th><th>{{lbl_sent}}</th><th>{{lbl_opened}}</th><th>{{lbl_replied}}</th><th></th></tr></thead>
        <tbody>{{rows}}</tbody>
      </table>
    </div>

    {% raw %}
    <script>
    let actChart = null;
    function loadChart(days) {
      fetch('/api/analytics/daily?days=' + days)
        .then(r => r.json())
        .then(data => {
          if (data.error) return;
          const labels = data.map(d => d.day);
          const sent = data.map(d => d.sent);
          const opened = data.map(d => d.opened);
          const replied = data.map(d => d.replied);
          const bounced = data.map(d => d.bounced);
          const ctx = document.getElementById('activityChart');
          if (!ctx) return;
          if (actChart) actChart.destroy();
          const cs = getComputedStyle(document.documentElement);
          actChart = new Chart(ctx, {
            type: 'line',
            data: {
              labels,
              datasets: [
                {label:'Sent', data:sent, borderColor:cs.getPropertyValue('--blue').trim()||'#3B82F6', backgroundColor:'rgba(59,130,246,0.08)', fill:true, tension:0.3, pointRadius:3},
                {label:'Opened', data:opened, borderColor:cs.getPropertyValue('--green').trim()||'#10B981', backgroundColor:'rgba(16,185,129,0.08)', fill:true, tension:0.3, pointRadius:3},
                {label:'Replied', data:replied, borderColor:cs.getPropertyValue('--primary').trim()||'#7C3AED', backgroundColor:'rgba(124,58,237,0.08)', fill:true, tension:0.3, pointRadius:3},
                {label:'Bounced', data:bounced, borderColor:cs.getPropertyValue('--red').trim()||'#EF4444', backgroundColor:'rgba(239,68,68,0.08)', fill:true, tension:0.3, pointRadius:3},
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              interaction: { mode: 'index', intersect: false },
              plugins: {
                legend: { position: 'top', labels: { usePointStyle: true, padding: 16, font: { family: 'Inter', size: 12 } } },
                tooltip: { backgroundColor: 'rgba(0,0,0,0.8)', titleFont: { family: 'Inter' }, bodyFont: { family: 'Inter' } }
              },
              scales: {
                x: { grid: { display: false }, ticks: { font: { family: 'Inter', size: 11 }, maxRotation: 45 } },
                y: { beginAtZero: true, ticks: { font: { family: 'Inter', size: 11 }, precision: 0 }, grid: { color: 'rgba(128,128,128,0.1)' } }
              }
            }
          });
        }).catch(() => {});
    }
    if (document.getElementById('activityChart')) loadChart(30);
    </script>
    {% endraw %}
    """, active_page="dashboard", rows=Markup(rows), g=gstats,
        g_open_rate=f"{gstats['open_rate']:.0%}", g_reply_rate=f"{gstats['reply_rate']:.0%}",
        usage_text=Markup(usage_text), upgrade_cta=Markup(upgrade_cta),
        has_accounts=has_accounts, has_contacts=has_contacts,
        has_campaigns=has_campaigns, has_sent=has_sent,
        onboarding_complete=onboarding_complete,
        onboarding_steps_done=onboarding_steps_done,
        onboarding_pct=int(onboarding_steps_done / 4 * 100),
        first_campaign_id=campaigns[0]["id"] if campaigns else 0,
        page_title=t("dash.title"),
        lbl_campaigns=t("dash.campaigns"), lbl_active=t("common.active"),
        lbl_emails_sent=t("dash.emails_sent"), lbl_open_rate=t("dash.open_rate"),
        lbl_replies=t("dash.replies"), lbl_new_campaign=t("dash.new_campaign"),
        lbl_name=t("dash.name"), lbl_status=t("dash.status"),
        lbl_sent=t("dash.sent"), lbl_opened=t("dash.opened"),
        lbl_replied=t("dash.replied"))


# ---------------------------------------------------------------------------
# Routes — Admin Broadcast
# ---------------------------------------------------------------------------

def _is_admin():
    """Check if current user is an admin (via is_admin flag or ADMIN_EMAILS env var)."""
    if not _logged_in():
        return False
    client = get_client(session["client_id"])
    if not client:
        return False
    if client.get("is_admin"):
        return True
    # Also check ADMIN_EMAILS env var (comma-separated)
    admin_emails = os.getenv("ADMIN_EMAILS", "")
    if admin_emails:
        admins = [e.strip().lower() for e in admin_emails.split(",") if e.strip()]
        if client["email"].lower() in admins:
            return True
    return False


@app.route("/admin/broadcast", methods=["GET", "POST"])
def admin_broadcast():
    """Send an announcement email to all registered users."""
    if not _is_admin():
        return redirect(url_for("dashboard"))

    from outreach.db import get_all_client_emails
    from outreach.sender import send_email as smtp_send

    users = get_all_client_emails()
    sent_count = 0
    error_msg = ""

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        if not subject or not body:
            error_msg = "Subject and body are required."
        else:
            for u in users:
                try:
                    smtp_send(u["email"], subject, body)
                    sent_count += 1
                except Exception as e:
                    print(f"Broadcast send error to {u['email']}: {e}")
            flash(("success", f"Broadcast sent to {sent_count} of {len(users)} users."))
            return redirect(url_for("admin_broadcast"))

    return _render("Admin Broadcast", f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / Admin Broadcast</div>
    <div class="page-header">
      <h1>&#128227; Admin Broadcast</h1>
      <p class="subtitle">Send an announcement email to all {len(users)} registered users.</p>
    </div>
    {'<div class="alert alert-red" style="margin-bottom:16px;">' + _esc(error_msg) + '</div>' if error_msg else ''}
    <div class="card" style="max-width:700px;">
      <form method="POST">
        <div class="form-group">
          <label>Subject</label>
          <input name="subject" placeholder="Important: MachReach Platform Update" required style="font-size:15px;">
        </div>
        <div class="form-group">
          <label>Message Body</label>
          <textarea name="body" rows="10" placeholder="Hi there,&#10;&#10;We have an important update..." required style="font-size:14px;line-height:1.7;"></textarea>
          <p class="form-hint">Plain text. Will be wrapped in the standard MachReach email template.</p>
        </div>
        <div style="display:flex;gap:12px;align-items:center;">
          <button type="submit" class="btn btn-primary" style="font-size:15px;padding:10px 28px;" onclick="return confirm('Send this email to ALL {len(users)} registered users?')">&#128640; Send to {len(users)} Users</button>
          <a href="/dashboard" class="btn btn-ghost">Cancel</a>
        </div>
      </form>
    </div>

    <div class="card" style="margin-top:20px;max-width:700px;">
      <div class="card-header"><h2>Registered Users ({len(users)})</h2></div>
      <table>
        <thead><tr><th>Name</th><th>Email</th></tr></thead>
        <tbody>
          {''.join(f'<tr><td>{_esc(u["name"])}</td><td style="font-family:monospace;font-size:13px;">{_esc(u["email"])}</td></tr>' for u in users)}
        </tbody>
      </table>
    </div>
    """)


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not _logged_in():
        return redirect(url_for("login"))
    # Student accounts use their own settings page
    if session.get("account_type") == "student":
        return redirect("/student/settings")
    client = get_client(session["client_id"])
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        business = request.form.get("business", "").strip()
        physical_address = request.form.get("physical_address", "").strip()
        if name:
            update_client(session["client_id"], name, business, physical_address)
            session["client_name"] = name
            flash(("success", "Settings saved."))
        return redirect(url_for("settings"))

    from outreach.db import get_email_accounts, get_subscription, get_mail_preferences, get_team_members, get_my_team_memberships
    from outreach.config import PLAN_LIMITS
    accounts = get_email_accounts(session["client_id"])
    sub = get_subscription(session["client_id"])
    plan = sub.get("plan", "free") if sub else "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_mailboxes = limits.get("mailboxes", 1)
    can_add = max_mailboxes == -1 or len(accounts) < max_mailboxes
    current_prefs = get_mail_preferences(session["client_id"])
    team = get_team_members(session["client_id"])
    my_memberships = get_my_team_memberships(session["client_id"])

    accounts_html = ""
    for a in accounts:
        default_badge = '<span class="badge badge-blue" style="font-size:10px;margin-left:6px;">Default</span>' if a["is_default"] else ""
        accounts_html += f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid var(--border-light);">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:16px;">
              {_esc(a['email'][:1].upper())}
            </div>
            <div>
              <div style="font-weight:600;">{_esc(a['label'] or a['email'])}{default_badge}</div>
              <div style="font-size:13px;color:var(--text-muted);">{_esc(a['email'])}</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;">
            {'<button class="btn btn-ghost btn-sm" onclick="setDefault(' + str(a['id']) + ')" style="font-size:12px;">Set Default</button>' if not a['is_default'] else ''}
            <button class="btn btn-ghost btn-sm" onclick="deleteAccount({a['id']})" style="font-size:12px;color:var(--red);">&#128465;</button>
          </div>
        </div>
        """

    if not accounts_html:
        accounts_html = '<p style="color:var(--text-muted);padding:20px 0;text-align:center;">No email accounts connected yet. Add one below to start using Mail Hub and sending emails.</p>'

    limit_text = "Unlimited" if max_mailboxes == -1 else str(max_mailboxes)

    prefs_card = f"""
    <div class="card">
      <div class="card-header"><h2>&#128340; Mail Sorting Rules</h2></div>
      <p style="font-size:13px;color:var(--text-muted);margin-bottom:6px;">Tell the AI how to sort your inbox. You can write both <strong>prioritize</strong> and <strong>deprioritize</strong> rules in plain English.</p>
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:16px;line-height:1.7;background:var(--bg);padding:12px 14px;border-radius:var(--radius-xs);">
        <strong>Examples:</strong><br>
        &#128314; Client emails and sales leads are always urgent<br>
        &#128314; Meeting invites from @company.com are important<br>
        &#128315; Do NOT mark no-reply@render.com as urgent or important<br>
        &#128315; Newsletters and marketing emails are always low priority<br>
        &#128315; Ignore all emails from noreply@github.com
      </div>
      <div class="form-group">
        <textarea id="settings-mail-rules" placeholder="Write your mail sorting rules here...&#10;e.g. Client emails are urgent&#10;Do NOT mark no-reply@render.com as important&#10;Financial emails are important&#10;Ignore newsletters from marketing@" style="height:120px;font-size:13px;">{_esc(current_prefs)}</textarea>
      </div>
      <button class="btn btn-primary" onclick="saveMailRules()" id="save-rules-btn">Save Rules</button>
      <span id="rules-save-status" style="margin-left:10px;font-size:13px;"></span>
    </div>
    """

    # Build team card
    team_rows_html = ""
    for m in team:
        name_display = _esc(m.get("member_name") or m["member_email"])
        status_badge = {
            "active": '<span class="badge badge-green">Active</span>',
            "pending": '<span class="badge badge-yellow">Pending</span>',
        }.get(m["status"], '<span class="badge">' + _esc(m["status"]) + '</span>')
        role_badge = '<span class="badge badge-blue" style="font-size:10px;">' + _esc(m["role"].title()) + '</span>'
        scope_badge = ('<span class="badge" style="font-size:10px;background:#FEF3C7;color:#92400E;">' + _esc(m["campaign_name"]) + '</span>') if m.get("campaign_name") else '<span class="badge badge-green" style="font-size:10px;">Full Access</span>'
        team_rows_html += f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--border-light);">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#8B5CF6,#EC4899);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;">
              {_esc(m['member_email'][:1].upper())}
            </div>
            <div>
              <div style="font-weight:600;font-size:14px;">{name_display} {role_badge} {scope_badge}</div>
              <div style="font-size:12px;color:var(--text-muted);">{_esc(m['member_email'])} &middot; {status_badge}</div>
            </div>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="removeTeamMember({m['id']})" style="font-size:12px;color:var(--red);">Remove</button>
        </div>
        """
    if not team_rows_html:
        team_rows_html = '<p style="color:var(--text-muted);padding:16px 0;text-align:center;font-size:13px;">No team members yet. Invite someone to collaborate!</p>'

    # Build "Your Teams" section for invited members
    memberships_html = ""
    if my_memberships:
        mem_rows = ""
        for mb in my_memberships:
            owner_display = _esc(mb.get("owner_name") or mb["owner_email"])
            mb_role = '<span class="badge badge-blue" style="font-size:10px;">' + _esc(mb["role"].title()) + '</span>'
            mb_scope = ('<span class="badge" style="font-size:10px;background:#FEF3C7;color:#92400E;">' + _esc(mb["campaign_name"]) + '</span>') if mb.get("campaign_name") else '<span class="badge badge-green" style="font-size:10px;">Full Access</span>'
            mem_rows += f"""
            <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--border-light);">
              <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#10B981,#3B82F6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;">
                {_esc(mb['owner_email'][:1].upper())}
              </div>
              <div>
                <div style="font-weight:600;font-size:14px;">{owner_display}'s Team {mb_role} {mb_scope}</div>
                <div style="font-size:12px;color:var(--text-muted);">{_esc(mb['owner_email'])}</div>
              </div>
            </div>
            """
        memberships_html = f"""
        <div style="margin-top:20px;padding-top:16px;border-top:2px solid var(--border-light);">
          <h3 style="font-size:15px;margin-bottom:10px;">&#127919; Teams You Belong To</h3>
          {mem_rows}
        </div>
        """

    # Build campaigns options for scoped invite
    from outreach.db import get_campaigns
    all_campaigns = get_campaigns(session["client_id"])
    campaign_options = '<option value="">Full Access (all campaigns)</option>'
    for c in all_campaigns:
        campaign_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

    team_card = f"""
    <div class="card">
      <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
        <h2>&#128101; Team</h2>
        <span style="font-size:13px;color:var(--text-muted);">{len(team)} member{'s' if len(team) != 1 else ''}</span>
      </div>
      <p style="font-size:13px;color:var(--text-muted);margin-bottom:14px;">Invite team members to share campaigns, contacts, and inbox access.</p>
      <div id="team-list">
        {team_rows_html}
      </div>
      <div style="margin-top:16px;">
        <div id="invite-form" style="display:flex;gap:8px;align-items:end;flex-wrap:wrap;">
          <div class="form-group" style="flex:1;min-width:200px;margin:0;">
            <label style="font-size:12px;">Email address</label>
            <input id="invite-email" type="email" placeholder="colleague@company.com" style="margin:0;">
          </div>
          <div class="form-group" style="margin:0;">
            <label style="font-size:12px;">Role</label>
            <select id="invite-role" style="margin:0;padding:8px 12px;">
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
            </select>
          </div>
          <div class="form-group" style="margin:0;">
            <label style="font-size:12px;">Scope</label>
            <select id="invite-campaign" style="margin:0;padding:8px 12px;">
              {campaign_options}
            </select>
          </div>
          <button class="btn btn-primary" onclick="inviteTeamMember()" id="invite-btn">Send Invite</button>
        </div>
        <div id="invite-status" style="font-size:13px;margin-top:8px;"></div>
      </div>
      {memberships_html}
    </div>
    """

    return _render(t("settings.title"), f"""
    <div class="page-header">
      <h1>{t("settings.title")}</h1>
      <p>Manage your account details and email connections.</p>
    </div>
    <div class="card">
      <div class="card-header"><h2>Profile</h2></div>
      <form method="post">
        <div class="form-row">
          <div class="form-group"><label>Name</label><input name="name" value="{{{{client.name}}}}" required></div>
          <div class="form-group"><label>Business</label><input name="business" value="{{{{client.business or ''}}}}"></div>
        </div>
        <div class="form-group">
          <label>Physical Address <span style="font-weight:400;color:var(--text-muted);">(required by CAN-SPAM)</span></label>
          <input name="physical_address" value="{{{{client.physical_address or ''}}}}" placeholder="123 Main St, Suite 100, Santiago, Chile">
          <p class="form-hint">CAN-SPAM requires a valid physical address in all commercial emails. This will appear in your email footer.</p>
        </div>
        <div class="form-group">
          <label>Email</label>
          <input value="{{{{client.email}}}}" disabled style="background:var(--border-light);color:var(--text-muted);">
          <p class="form-hint">Email cannot be changed.</p>
        </div>
        <button class="btn btn-primary" type="submit">Save Changes</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
        <h2>&#128231; Email Accounts</h2>
        <span style="font-size:13px;color:var(--text-muted);">{len(accounts)}/{limit_text} mailboxes</span>
      </div>
      <div id="accounts-list">
        {accounts_html}
      </div>
      {'<div style="margin-top:16px;"><button class="btn btn-primary" onclick="showAddAccount()" id="add-account-btn">&#43; Add Email Account</button></div>' if can_add else '<p style="margin-top:12px;font-size:13px;color:var(--text-muted);">Mailbox limit reached. <a href="/billing">Upgrade your plan</a> for more.</p>'}

      <div id="add-account-form" style="display:none;margin-top:16px;padding:20px;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--border-light);">
        <h3 style="font-size:16px;margin-bottom:14px;">Add Email Account</h3>
        <div class="form-row">
          <div class="form-group"><label>Label</label><input id="acct-label" placeholder="Work Gmail, Personal, etc." autocomplete="off"></div>
          <div class="form-group"><label>Email Address</label><input id="acct-email" type="email" placeholder="you@example.com" required autocomplete="off" oninput="detectProvider(this.value)"></div>
        </div>
        <div id="provider-badge" style="display:none;margin-bottom:14px;padding:10px 14px;border-radius:var(--radius-xs);font-size:13px;background:#EFF6FF;color:#1E40AF;align-items:center;gap:8px;"></div>
        <div class="form-group">
          <label>App Password</label>
          <input id="acct-password" type="password" placeholder="Paste your App Password here" required autocomplete="new-password">
          <p class="form-hint" id="password-hint">For Gmail, generate an <a href="https://myaccount.google.com/apppasswords" target="_blank">App Password</a>. For Outlook, use your account password with <a href="https://support.microsoft.com/en-us/account-billing/using-app-passwords-with-apps-that-don-t-support-two-step-verification-5896ed9b-4263-e681-128a-a6f2979a7944" target="_blank">app passwords</a>.</p>
        </div>
        <details style="margin-bottom:14px;">
          <summary style="font-size:13px;color:var(--text-muted);cursor:pointer;">Advanced Settings (IMAP/SMTP)</summary>
          <div style="margin-top:10px;">
            <div class="form-row">
              <div class="form-group"><label>IMAP Host</label><input id="acct-imap-host" value="imap.gmail.com"></div>
              <div class="form-group"><label>IMAP Port</label><input id="acct-imap-port" value="993" type="number"></div>
            </div>
            <div class="form-row">
              <div class="form-group"><label>SMTP Host</label><input id="acct-smtp-host" value="smtp.gmail.com"></div>
              <div class="form-group"><label>SMTP Port</label><input id="acct-smtp-port" value="465" type="number"></div>
            </div>
          </div>
        </details>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-primary" onclick="addAccount()" id="save-account-btn">&#128274; Test &amp; Add Account</button>
          <button class="btn btn-ghost" onclick="hideAddAccount()">Cancel</button>
        </div>
        <div id="add-account-status" style="margin-top:10px;font-size:13px;"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>&#127760; Custom Domain Sending</h2>
      </div>
      <p style="font-size:14px;color:var(--text-secondary);margin-bottom:16px;">Send emails from <strong>you@yourcompany.com</strong> instead of a Gmail or Yahoo address. This improves deliverability and looks more professional.</p>

      <details>
        <summary style="font-size:14px;font-weight:600;cursor:pointer;color:var(--primary);margin-bottom:12px;">&#128218; How to set up custom domain email</summary>
        <div style="margin-top:12px;font-size:13px;color:var(--text-secondary);line-height:1.8;">
          <div style="background:var(--bg);border-radius:var(--radius-sm);padding:16px;margin-bottom:14px;">
            <h4 style="font-size:14px;margin:0 0 8px;">Step 1: Get a business email</h4>
            <p style="margin:0;">You need an email address on your own domain (e.g. <code>hello@yourcompany.com</code>). Popular options:</p>
            <ul style="padding-left:18px;margin:8px 0 0;">
              <li><strong>Google Workspace</strong> ($6/mo) — Uses Gmail interface, supports App Passwords</li>
              <li><strong>Microsoft 365</strong> ($6/mo) — Uses Outlook interface</li>
              <li><strong>Zoho Mail</strong> (free tier available) — Good budget option</li>
              <li><strong>Your hosting provider</strong> — Many hosts include email with your domain</li>
            </ul>
          </div>

          <div style="background:var(--bg);border-radius:var(--radius-sm);padding:16px;margin-bottom:14px;">
            <h4 style="font-size:14px;margin:0 0 8px;">Step 2: Set up DNS records</h4>
            <p style="margin:0 0 8px;">Add these records in your domain registrar (Namecheap, GoDaddy, Cloudflare, etc.):</p>
            <table style="width:100%;font-size:12px;border-collapse:collapse;">
              <thead><tr style="border-bottom:2px solid var(--border);text-align:left;">
                <th style="padding:6px 8px;">Record</th><th style="padding:6px 8px;">Purpose</th><th style="padding:6px 8px;">What it does</th>
              </tr></thead>
              <tbody>
                <tr style="border-bottom:1px solid var(--border-light);">
                  <td style="padding:6px 8px;"><strong>SPF</strong></td>
                  <td style="padding:6px 8px;">TXT record</td>
                  <td style="padding:6px 8px;">Tells servers which IPs can send from your domain</td>
                </tr>
                <tr style="border-bottom:1px solid var(--border-light);">
                  <td style="padding:6px 8px;"><strong>DKIM</strong></td>
                  <td style="padding:6px 8px;">TXT record</td>
                  <td style="padding:6px 8px;">Digitally signs your emails to prove authenticity</td>
                </tr>
                <tr>
                  <td style="padding:6px 8px;"><strong>DMARC</strong></td>
                  <td style="padding:6px 8px;">TXT record</td>
                  <td style="padding:6px 8px;">Policy that tells receivers how to handle unauthenticated emails</td>
                </tr>
              </tbody>
            </table>
            <p style="margin:10px 0 0;font-size:12px;color:var(--text-muted);">Your email provider (Google Workspace, Microsoft 365, etc.) will give you the exact values to add.</p>
          </div>

          <div style="background:var(--bg);border-radius:var(--radius-sm);padding:16px;margin-bottom:14px;">
            <h4 style="font-size:14px;margin:0 0 8px;">Step 3: Connect to MachReach</h4>
            <p style="margin:0;">Once your domain email is set up, add it to MachReach just like any other account:</p>
            <ol style="padding-left:18px;margin:8px 0 0;">
              <li>Click <strong>"+ Add Email Account"</strong> above</li>
              <li>Enter your custom domain email (e.g. <code>hello@yourcompany.com</code>)</li>
              <li>If using Google Workspace, it auto-detects Gmail settings</li>
              <li>For other providers, open <strong>Advanced Settings</strong> and enter your IMAP/SMTP details</li>
              <li>Use an App Password if your provider supports it</li>
            </ol>
          </div>

          <div style="background:var(--green-light);border-radius:var(--radius-sm);padding:14px 16px;">
            <strong style="color:var(--green-dark);">&#128161; Pro tip:</strong>
            <span style="color:var(--green-dark);font-size:13px;">Start with a low sending volume (10-20 emails/day) for the first 2 weeks to warm up your domain. This builds reputation and prevents your emails from landing in spam.</span>
          </div>
        </div>
      </details>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>&#128737; Email Deliverability Check</h2>
      </div>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:14px;">Verify your domain's SPF, DKIM, and DMARC records. All three are <strong>required</strong> by Gmail and Yahoo since 2024 for bulk senders.</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px;">
        <input id="dlvr-domain" type="text" placeholder="yourcompany.com" style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:14px;background:var(--bg);" />
        <button id="dlvr-check-btn" class="btn btn-primary" onclick="checkDeliverability()" style="white-space:nowrap;">Check Domain</button>
      </div>
      <div id="dlvr-results" style="display:none;">
        <div id="dlvr-score" style="text-align:center;margin-bottom:16px;padding:14px;border-radius:var(--radius-sm);font-weight:600;font-size:15px;"></div>
        <div style="display:flex;flex-direction:column;gap:8px;" id="dlvr-rows"></div>
        <div id="dlvr-tips" style="margin-top:14px;font-size:12px;color:var(--text-muted);"></div>
      </div>
      <script>
      function checkDeliverability() {{
        const domain = document.getElementById('dlvr-domain').value.trim();
        if (!domain) return;
        const btn = document.getElementById('dlvr-check-btn');
        btn.disabled = true; btn.textContent = 'Checking...';
        document.getElementById('dlvr-results').style.display = 'none';
        fetch('/api/check-deliverability?domain=' + encodeURIComponent(domain))
          .then(r => r.json()).then(d => {{
            btn.disabled = false; btn.textContent = 'Check Domain';
            if (d.error) {{ showToast(d.error, 'error'); return; }}
            document.getElementById('dlvr-results').style.display = 'block';
            const scorePct = Math.round((d.score / d.max_score) * 100);
            const scoreEl = document.getElementById('dlvr-score');
            const colors = {{0: ['#FEE2E2','#DC2626'], 1: ['#FEF3C7','#D97706'], 2: ['#FEF3C7','#D97706'], 3: ['#DCFCE7','#16A34A']}};
            const [bg, fg] = colors[d.score] || colors[0];
            scoreEl.style.background = bg; scoreEl.style.color = fg;
            scoreEl.textContent = d.score + '/3 checks passed — ' + (d.score === 3 ? 'Excellent! Your domain is fully authenticated.' : d.score >= 2 ? 'Good, but fix the remaining issue.' : 'Action needed — your emails may land in spam.');

            const rowsEl = document.getElementById('dlvr-rows');
            rowsEl.innerHTML = '';
            [{{key:'spf', label:'SPF', icon:'&#128274;'}}, {{key:'dkim', label:'DKIM', icon:'&#128273;'}}, {{key:'dmarc', label:'DMARC', icon:'&#128737;'}}].forEach(item => {{
              const c = d[item.key];
              const ok = c && c.status === 'pass';
              const row = document.createElement('div');
              row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:6px;background:' + (ok ? 'var(--green-light)' : '#FEF3C7') + ';';
              row.innerHTML = '<span style="font-size:18px;">' + (ok ? '&#9989;' : '&#9888;&#65039;') + '</span>'
                + '<div style="flex:1;"><strong style="font-size:13px;">' + item.label + '</strong>'
                + (c && c.record ? '<div style="font-size:11px;color:var(--text-muted);margin-top:2px;word-break:break-all;font-family:monospace;">' + (c.record.length > 80 ? c.record.slice(0,80)+'...' : c.record) + '</div>' : '')
                + (c && c.selector ? '<div style="font-size:11px;color:var(--text-muted);">Selector: ' + c.selector + '</div>' : '')
                + (!ok ? '<div style="font-size:11px;color:#D97706;margin-top:2px;">' + (c && c.hint ? c.hint : 'Not found — add this record in your domain DNS settings.') + '</div>' : '')
                + '</div>';
              rowsEl.appendChild(row);
            }});

            const tips = document.getElementById('dlvr-tips');
            if (d.score < 3) {{
              tips.innerHTML = '<strong>How to fix:</strong> Log into your domain registrar (Namecheap, Cloudflare, GoDaddy, etc.) and add the missing DNS TXT records. Your email provider (Google Workspace, Microsoft 365, etc.) will give you the exact values.';
            }} else {{ tips.innerHTML = ''; }}
          }}).catch(() => {{ btn.disabled = false; btn.textContent = 'Check Domain'; showToast('Check failed', 'error'); }});
      }}
      </script>
    </div>

    <div class="card">
      <div class="card-header"><h2>Sending Configuration</h2></div>
      <div style="font-size:13px;color:var(--text-secondary);line-height:1.7;">
        <p><strong>Sender Name:</strong> {{{{sender_name}}}}</p>
        <p><strong>Daily Limit:</strong> {{{{daily_limit}}}} emails/day (based on your plan)</p>
        <p><strong>Delay Between Emails:</strong> 60 seconds</p>
        <p class="text-xs text-muted mt-2">Daily limits scale with your plan: Free=50, Growth=200, Pro=500, Unlimited=∞</p>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>&#128272; {t("settings.security")}</h2></div>
      <div style="display:flex;align-items:center;gap:12px;padding:14px 18px;background:var(--green-light);border-radius:var(--radius-sm);margin-bottom:16px;">
        <span style="font-size:22px;">&#9989;</span>
        <div>
          <div style="font-weight:600;font-size:14px;color:var(--green-dark);">Your account is secure</div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">Password protected with bcrypt encryption. You can change your password below if needed.</div>
        </div>
      </div>
      <details style="cursor:pointer;">
        <summary style="font-size:14px;font-weight:600;color:var(--text-secondary);padding:10px 0;list-style:none;display:flex;align-items:center;gap:8px;">
          <span style="transition:transform 0.2s;display:inline-block;" class="pw-arrow">&#9654;</span> {t("settings.change_password")} <span style="font-size:12px;font-weight:400;color:var(--text-muted);">(optional)</span>
        </summary>
        <div style="padding:16px 0 4px;">
          <form method="post" action="/settings/change-password">
            <div class="form-group"><label>{t("settings.current_password")}</label><input name="current_password" type="password" required></div>
            <div class="form-row">
              <div class="form-group"><label>{t("settings.new_password")}</label><input name="new_password" type="password" required minlength="6"></div>
              <div class="form-group"><label>{t("settings.confirm_password")}</label><input name="confirm_password" type="password" required minlength="6"></div>
            </div>
            <button class="btn btn-outline" type="submit">{t("settings.update_password")}</button>
          </form>
        </div>
      </details>
    </div>

    {prefs_card}

    {team_card}

    <!-- Theme picker -->
    <div class="card">
      <div class="card-header"><h2>&#127912; Theme</h2></div>
      <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">Personalize how MachReach looks. Saved on this device.</p>
      <div id="theme-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">
        <button type="button" class="theme-chip" data-theme="default" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0f172a;color:#fff;text-align:left"><div style="font-weight:700">Default</div><div style="font-size:11px;opacity:.7">Indigo / Slate</div></button>
        <button type="button" class="theme-chip" data-theme="midnight" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#050816;color:#e2e8f0;text-align:left"><div style="font-weight:700">Midnight</div><div style="font-size:11px;opacity:.7">Deep black</div></button>
        <button type="button" class="theme-chip" data-theme="forest" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0b2018;color:#d1fae5;text-align:left"><div style="font-weight:700">Forest</div><div style="font-size:11px;opacity:.7">Calm green</div></button>
        <button type="button" class="theme-chip" data-theme="ocean" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#082f49;color:#e0f2fe;text-align:left"><div style="font-weight:700">Ocean</div><div style="font-size:11px;opacity:.7">Deep blue</div></button>
        <button type="button" class="theme-chip" data-theme="rose" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#3f0a1a;color:#fecdd3;text-align:left"><div style="font-weight:700">Rose</div><div style="font-size:11px;opacity:.7">Warm crimson</div></button>
        <button type="button" class="theme-chip" data-theme="sunset" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:linear-gradient(135deg,#7c2d12,#ea580c);color:#fff;text-align:left"><div style="font-weight:700">Sunset</div><div style="font-size:11px;opacity:.7">Orange / amber</div></button>
        <button type="button" class="theme-chip" data-theme="mono" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#111;color:#fff;text-align:left"><div style="font-weight:700">Mono</div><div style="font-size:11px;opacity:.7">Pure black &amp; white</div></button>
        <button type="button" class="theme-chip" data-theme="light" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f8fafc;color:#111827;text-align:left"><div style="font-weight:700">Light</div><div style="font-size:11px;opacity:.7">Clean &amp; bright</div></button>
        <button type="button" class="theme-chip" data-theme="lavender" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f5f3ff;color:#3b0764;text-align:left"><div style="font-weight:700">Lavender</div><div style="font-size:11px;opacity:.7">Soft purple</div></button>
        <button type="button" class="theme-chip" data-theme="mint" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0fdf4;color:#14532d;text-align:left"><div style="font-weight:700">Mint</div><div style="font-size:11px;opacity:.7">Fresh pastel green</div></button>
        <button type="button" class="theme-chip" data-theme="peach" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff7ed;color:#7c2d12;text-align:left"><div style="font-weight:700">Peach</div><div style="font-size:11px;opacity:.7">Warm pastel orange</div></button>
        <button type="button" class="theme-chip" data-theme="sky" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0f9ff;color:#0c4a6e;text-align:left"><div style="font-weight:700">Sky</div><div style="font-size:11px;opacity:.7">Pastel blue</div></button>
        <button type="button" class="theme-chip" data-theme="butter" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fefce8;color:#713f12;text-align:left"><div style="font-weight:700">Butter</div><div style="font-size:11px;opacity:.7">Soft pastel yellow</div></button>
        <button type="button" class="theme-chip" data-theme="lilac" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf4ff;color:#581c87;text-align:left"><div style="font-weight:700">Lilac</div><div style="font-size:11px;opacity:.7">Pastel violet</div></button>
        <button type="button" class="theme-chip" data-theme="blush" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff1f2;color:#881337;text-align:left"><div style="font-weight:700">Blush</div><div style="font-size:11px;opacity:.7">Soft pastel pink</div></button>
        <button type="button" class="theme-chip" data-theme="sand" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#faf5ee;color:#44342a;text-align:left"><div style="font-weight:700">Sand</div><div style="font-size:11px;opacity:.7">Warm beige</div></button>
        <button type="button" class="theme-chip" data-theme="cottoncandy" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf2f8;color:#831843;text-align:left"><div style="font-weight:700">Cotton Candy</div><div style="font-size:11px;opacity:.7">Bubblegum pink</div></button>
        <button type="button" class="theme-chip" data-theme="seafoam" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#ecfeff;color:#164e63;text-align:left"><div style="font-weight:700">Seafoam</div><div style="font-size:11px;opacity:.7">Pastel cyan</div></button>
      </div>
      <span id="theme-status" style="color:var(--text-muted);font-size:13px;display:inline-block;margin-top:10px"></span>
      <p style="color:var(--text-muted);font-size:12px;margin-top:6px">&#128161; Click any theme to switch instantly.</p>
    </div>

    <!-- Billing & Plan -->
    <div class="card">
      <div class="card-header"><h2>&#128179; Billing &amp; Plan</h2></div>
      <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">Manage your subscription, view invoices, and change payment method.</p>
      <a href="/billing" class="btn btn-primary">&#128179; Open Billing</a>
    </div>

    <div class="card">
      <div class="card-header"><h2>&#128230; Your Data (GDPR)</h2></div>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:14px;">Download a copy of all your personal data stored in MachReach (profile, campaigns, contacts, emails). The export is in JSON format.</p>
      <a class="btn btn-outline" href="/api/export-my-data">&#11015; Export My Data</a>
    </div>

    <div class="card" style="border-color:var(--red);">
      <div class="card-header"><h2 style="color:var(--red);">&#9888;&#65039; Danger Zone</h2></div>
      <div style="margin-bottom:16px;">
        <h3 style="font-size:15px;margin-bottom:6px;">{t("settings.delete_account")}</h3>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:12px;">{t("settings.delete_warning")}</p>
        <button class="btn btn-ghost" style="color:var(--red);border-color:var(--red);" onclick="document.getElementById('delete-confirm-box').style.display='block';this.style.display='none';">{t("settings.delete_account")}</button>
        <div id="delete-confirm-box" style="display:none;margin-top:12px;padding:16px;background:rgba(239,68,68,0.06);border-radius:var(--radius-sm);border:1px solid var(--red);">
          <form method="post" action="/settings/delete-account">
            <p style="font-size:13px;margin-bottom:10px;">{t("settings.delete_confirm")}</p>
            <div class="form-group"><input name="confirm" placeholder="DELETE" required autocomplete="off" style="border-color:var(--red);"></div>
            <button class="btn btn-primary" type="submit" style="background:var(--red);border-color:var(--red);">{t("settings.delete_account")}</button>
          </form>
        </div>
      </div>
    </div>

    <script>
    const EMAIL_PROVIDERS = {{
      'gmail.com': {{imap: 'imap.gmail.com', smtp: 'smtp.gmail.com', imap_port: 993, smtp_port: 465, name: 'Gmail', color: '#EA4335',
        hint: 'Generate an <a href="https://myaccount.google.com/apppasswords" target="_blank">App Password</a> in your Google account.'}},
      'googlemail.com': {{imap: 'imap.gmail.com', smtp: 'smtp.gmail.com', imap_port: 993, smtp_port: 465, name: 'Gmail', color: '#EA4335',
        hint: 'Generate an <a href="https://myaccount.google.com/apppasswords" target="_blank">App Password</a> in your Google account.'}},
      'yahoo.com': {{imap: 'imap.mail.yahoo.com', smtp: 'smtp.mail.yahoo.com', imap_port: 993, smtp_port: 465, name: 'Yahoo Mail', color: '#6001D2',
        hint: 'Generate an <a href="https://login.yahoo.com/account/security" target="_blank">App Password</a> in Yahoo Account Security. Enable 2-Step Verification first.'}},
      'yahoo.es': {{imap: 'imap.mail.yahoo.com', smtp: 'smtp.mail.yahoo.com', imap_port: 993, smtp_port: 465, name: 'Yahoo Mail', color: '#6001D2',
        hint: 'Generate an <a href="https://login.yahoo.com/account/security" target="_blank">App Password</a> in Yahoo Account Security. Enable 2-Step Verification first.'}},
      'yahoo.co.uk': {{imap: 'imap.mail.yahoo.com', smtp: 'smtp.mail.yahoo.com', imap_port: 993, smtp_port: 465, name: 'Yahoo Mail', color: '#6001D2',
        hint: 'Generate an <a href="https://login.yahoo.com/account/security" target="_blank">App Password</a> in Yahoo Account Security. Enable 2-Step Verification first.'}},
      'yahoo.com.ar': {{imap: 'imap.mail.yahoo.com', smtp: 'smtp.mail.yahoo.com', imap_port: 993, smtp_port: 465, name: 'Yahoo Mail', color: '#6001D2',
        hint: 'Generate an <a href="https://login.yahoo.com/account/security" target="_blank">App Password</a> in Yahoo Account Security. Enable 2-Step Verification first.'}},
      'ymail.com': {{imap: 'imap.mail.yahoo.com', smtp: 'smtp.mail.yahoo.com', imap_port: 993, smtp_port: 465, name: 'Yahoo Mail', color: '#6001D2',
        hint: 'Generate an <a href="https://login.yahoo.com/account/security" target="_blank">App Password</a> in Yahoo Account Security. Enable 2-Step Verification first.'}},
      'outlook.com': {{imap: 'imap-mail.outlook.com', smtp: 'smtp-mail.outlook.com', imap_port: 993, smtp_port: 587, name: 'Outlook', color: '#0078D4',
        hint: 'Use your regular password. If 2FA is on, generate an <a href="https://account.live.com/proofs/AppPassword" target="_blank">app password</a>.'}},
      'hotmail.com': {{imap: 'imap-mail.outlook.com', smtp: 'smtp-mail.outlook.com', imap_port: 993, smtp_port: 587, name: 'Outlook', color: '#0078D4',
        hint: 'Use your regular password. If 2FA is on, generate an <a href="https://account.live.com/proofs/AppPassword" target="_blank">app password</a>.'}},
      'live.com': {{imap: 'imap-mail.outlook.com', smtp: 'smtp-mail.outlook.com', imap_port: 993, smtp_port: 587, name: 'Outlook', color: '#0078D4',
        hint: 'Use your regular password. If 2FA is on, generate an <a href="https://account.live.com/proofs/AppPassword" target="_blank">app password</a>.'}},
      'msn.com': {{imap: 'imap-mail.outlook.com', smtp: 'smtp-mail.outlook.com', imap_port: 993, smtp_port: 587, name: 'Outlook', color: '#0078D4',
        hint: 'Use your regular password. If 2FA is on, generate an <a href="https://account.live.com/proofs/AppPassword" target="_blank">app password</a>.'}},
    }};
    let _mxTimeout = null;
    function _applyProvider(p, badge, hint) {{
      document.getElementById('acct-imap-host').value = p.imap;
      document.getElementById('acct-imap-port').value = p.imap_port;
      document.getElementById('acct-smtp-host').value = p.smtp;
      document.getElementById('acct-smtp-port').value = p.smtp_port;
      badge.innerHTML = '<span style="font-weight:600;">' + p.name + ' detected</span> — IMAP/SMTP settings filled automatically.';
      badge.style.display = 'flex';
      badge.style.borderLeft = '3px solid ' + p.color;
      hint.innerHTML = p.hint;
    }}
    function detectProvider(email) {{
      const badge = document.getElementById('provider-badge');
      const hint = document.getElementById('password-hint');
      const at = email.indexOf('@');
      if (at < 0) {{ badge.style.display = 'none'; return; }}
      const domain = email.substring(at + 1).toLowerCase().trim();
      if (!domain || domain.indexOf('.') < 0) {{ badge.style.display = 'none'; return; }}
      const p = EMAIL_PROVIDERS[domain];
      if (p) {{
        _applyProvider(p, badge, hint);
        return;
      }}
      // Unknown domain — debounce MX lookup via server
      clearTimeout(_mxTimeout);
      badge.innerHTML = '<span style="color:var(--text-muted);">&#8987; Detecting provider for <b>' + domain + '</b>...</span>';
      badge.style.display = 'flex';
      badge.style.borderLeft = '3px solid var(--border)';
      _mxTimeout = setTimeout(() => {{
        fetch('/api/detect-provider?domain=' + encodeURIComponent(domain))
          .then(r => r.json())
          .then(data => {{
            if (data.provider) {{
              _applyProvider(data, badge, hint);
            }} else {{
              badge.innerHTML = '<span style="font-weight:600;">Custom provider</span> — open <b>Advanced Settings</b> below and enter your IMAP/SMTP server details. Check with your IT department or email provider.';
              badge.style.display = 'flex';
              badge.style.borderLeft = '3px solid var(--yellow)';
              hint.innerHTML = 'Enter the password for this email account. If your provider supports App Passwords, use one for better security.';
            }}
          }})
          .catch(() => {{
            badge.innerHTML = '<span style="font-weight:600;">Custom provider</span> — open <b>Advanced Settings</b> below and enter your IMAP/SMTP server details.';
            badge.style.display = 'flex';
            badge.style.borderLeft = '3px solid var(--yellow)';
            hint.innerHTML = 'Enter the password for this email account.';
          }});
      }}, 600);
    }}
    function showAddAccount() {{
      document.getElementById('add-account-form').style.display = 'block';
      document.getElementById('add-account-btn').style.display = 'none';
    }}
    function hideAddAccount() {{
      document.getElementById('add-account-form').style.display = 'none';
      document.getElementById('add-account-btn').style.display = '';
    }}
    function addAccount() {{
      const btn = document.getElementById('save-account-btn');
      const status = document.getElementById('add-account-status');
      btn.disabled = true;
      btn.innerHTML = '&#8987; Testing connection...';
      status.innerHTML = '';
      fetch('/api/email-accounts', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          label: document.getElementById('acct-label').value,
          email: document.getElementById('acct-email').value,
          password: document.getElementById('acct-password').value,
          imap_host: document.getElementById('acct-imap-host').value,
          imap_port: parseInt(document.getElementById('acct-imap-port').value),
          smtp_host: document.getElementById('acct-smtp-host').value,
          smtp_port: parseInt(document.getElementById('acct-smtp-port').value),
        }})
      }}).then(r => r.json()).then(data => {{
        if (data.id) {{
          window.location.reload();
        }} else {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed to add account') + '</span>';
          btn.disabled = false;
          btn.innerHTML = '&#128274; Test &amp; Add Account';
        }}
      }}).catch(() => {{
        status.innerHTML = '<span style="color:var(--red);">&#9888; Connection error</span>';
        btn.disabled = false;
        btn.innerHTML = '&#128274; Test &amp; Add Account';
      }});
    }}
    function setDefault(id) {{
      fetch('/api/email-accounts/' + id + '/default', {{method: 'POST'}})
        .then(() => window.location.reload());
    }}
    function deleteAccount(id) {{
      if (!confirm('Remove this email account? Emails already synced will be kept.')) return;
      fetch('/api/email-accounts/' + id, {{method: 'DELETE'}})
        .then(() => window.location.reload());
    }}
    function saveMailRules() {{
      var text = document.getElementById('settings-mail-rules').value.trim();
      var btn = document.getElementById('save-rules-btn');
      var status = document.getElementById('rules-save-status');
      if (!text) {{ status.innerHTML = '<span style="color:var(--red);">Please enter at least one rule</span>'; return; }}
      btn.disabled = true;
      btn.textContent = 'Saving...';
      fetch('/api/mail-preferences', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{preferences: text}})
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        btn.disabled = false;
        btn.textContent = 'Save Rules';
        if (data.ok) {{
          status.innerHTML = '<span style="color:var(--green);">&#10003; Saved! Rules will apply on next sync.</span>';
          setTimeout(function() {{ status.innerHTML = ''; }}, 4000);
        }} else {{
          status.innerHTML = '<span style="color:var(--red);">' + (data.error || 'Failed to save') + '</span>';
        }}
      }}).catch(function() {{
        btn.disabled = false;
        btn.textContent = 'Save Rules';
        status.innerHTML = '<span style="color:var(--red);">Connection error</span>';
      }});
    }}
    function inviteTeamMember() {{
      var email = document.getElementById('invite-email').value.trim();
      var role = document.getElementById('invite-role').value;
      var campaignSel = document.getElementById('invite-campaign');
      var campaignId = campaignSel ? campaignSel.value : '';
      var btn = document.getElementById('invite-btn');
      var status = document.getElementById('invite-status');
      if (!email) {{ status.innerHTML = '<span style="color:var(--red);">Enter an email address</span>'; return; }}
      btn.disabled = true;
      btn.textContent = 'Sending...';
      var payload = {{email: email, role: role}};
      if (campaignId) payload.campaign_id = parseInt(campaignId);
      fetch('/api/team/invite', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload)
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        btn.disabled = false;
        btn.textContent = 'Send Invite';
        if (data.ok) {{
          status.innerHTML = '<span style="color:var(--green);">&#10003; Invite sent! Share this link: <input style="width:260px;font-size:12px;padding:4px 8px;margin-left:6px;" readonly value="' + data.invite_url + '" onclick="this.select()"></span>';
          document.getElementById('invite-email').value = '';
          setTimeout(function() {{ location.reload(); }}, 3000);
        }} else {{
          status.innerHTML = '<span style="color:var(--red);">' + (data.error || 'Failed') + '</span>';
        }}
      }}).catch(function() {{
        btn.disabled = false;
        btn.textContent = 'Send Invite';
        status.innerHTML = '<span style="color:var(--red);">Connection error</span>';
      }});
    }}
    function removeTeamMember(id) {{
      if (!confirm('Remove this team member?')) return;
      fetch('/api/team/' + id + '/remove', {{method: 'DELETE'}})
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
          if (data.ok) location.reload();
          else alert(data.error || 'Failed to remove');
        }});
    }}

    // Theme picker behavior
    (function(){{
      var current = localStorage.getItem('mr_theme') || 'default';
      function mark(){{
        document.querySelectorAll('.theme-chip').forEach(function(b){{
          b.style.outline = (b.dataset.theme === current) ? '3px solid #6366f1' : 'none';
          b.style.outlineOffset = (b.dataset.theme === current) ? '2px' : '0';
        }});
      }}
      document.querySelectorAll('.theme-chip').forEach(function(b){{
        b.addEventListener('click', function(){{
          current = b.dataset.theme;
          localStorage.setItem('mr_theme', current);
          if (window.applyMrTheme) window.applyMrTheme(current);
          mark();
          var s = document.getElementById('theme-status');
          if (s) {{ s.textContent = 'Saved!'; setTimeout(function(){{ if(s) s.textContent=''; }}, 1800); }}
        }});
      }});
      mark();
    }})();


    </script>
    """, active_page="settings", client=client, sender_name=SENDER_NAME,
        daily_limit="Unlimited" if limits["emails_per_day"] == -1 else str(limits["emails_per_day"]))


# ---------------------------------------------------------------------------
# Routes — Reply Inbox
# ---------------------------------------------------------------------------

@app.route("/inbox")
def inbox():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_inbox_all
    emails = get_inbox_all(session["client_id"])

    # Group by contact
    contacts = {}
    for e in emails:
        cid = e["contact_id"]
        if cid not in contacts:
            contacts[cid] = {
                "contact_name": e["contact_name"],
                "contact_email": e["contact_email"],
                "company": e["company"],
                "role": e["role"],
                "contact_status": e["contact_status"],
                "campaign_name": e["campaign_name"],
                "campaign_id": e["campaign_id"],
                "emails": [],
                "reply_sentiment": "",
            }
        contacts[cid]["emails"].append(e)
        # Capture sentiment from the replied email
        if e.get("reply_sentiment"):
            contacts[cid]["reply_sentiment"] = e["reply_sentiment"]

    # Sort: replied first, then by latest email date
    def sort_key(item):
        c = item[1]
        is_replied = 1 if c["contact_status"] == "replied" else 0
        latest = c["emails"][0]["sent_at"] if c["emails"] else ""
        return (-is_replied, latest)

    sorted_contacts = sorted(contacts.items(), key=sort_key, reverse=True)

    # Filter
    filter_val = request.args.get("filter", "all")

    # Build inbox HTML
    thread_cards = ""
    count_all = len(sorted_contacts)
    count_replied = sum(1 for _, c in sorted_contacts if c["contact_status"] == "replied")
    count_opened = sum(1 for _, c in sorted_contacts if any(e["email_status"] in ("opened", "clicked") for e in c["emails"]))
    count_pending = sum(1 for _, c in sorted_contacts if c["contact_status"] == "sent")
    count_positive = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "positive")
    count_negative = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "negative")
    count_neutral = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "neutral")

    for cid, c in sorted_contacts:
        if filter_val == "replied" and c["contact_status"] != "replied":
            continue
        if filter_val == "opened" and not any(e["email_status"] in ("opened", "clicked") for e in c["emails"]):
            continue
        if filter_val == "pending" and c["contact_status"] != "sent":
            continue
        if filter_val == "positive" and c["reply_sentiment"] != "positive":
            continue
        if filter_val == "negative" and c["reply_sentiment"] != "negative":
            continue
        if filter_val == "neutral" and c["reply_sentiment"] != "neutral":
            continue

        status_map = {
            "replied": ("Replied", "badge-green"),
            "sent": ("Sent", "badge-blue"),
            "opened": ("Opened", "badge-blue"),
            "pending": ("Pending", ""),
            "bounced": ("Bounced", "badge-red"),
            "unsubscribed": ("Unsub", "badge-red"),
        }
        st = c["contact_status"]
        badge_text, badge_cls = status_map.get(st, (st.title(), ""))
        # Check if any email was opened
        if st == "sent" and any(e["email_status"] in ("opened", "clicked") for e in c["emails"]):
            badge_text, badge_cls = "Opened", "badge-blue"

        # Sentiment badge
        sentiment_badge = ""
        if c["reply_sentiment"] == "positive":
            sentiment_badge = '<span class="badge badge-green" style="margin-left:4px;">&#128077; Positive</span>'
        elif c["reply_sentiment"] == "negative":
            sentiment_badge = '<span class="badge badge-red" style="margin-left:4px;">&#128078; Negative</span>'
        elif c["reply_sentiment"] == "neutral":
            sentiment_badge = '<span class="badge badge-yellow" style="margin-left:4px;">&#8212; Neutral</span>'

        email_timeline = ""
        for e in reversed(c["emails"]):  # oldest first
            step_label = f"Step {e['step']}" if e["step"] else ""
            time_str = e["sent_at"][:16] if e["sent_at"] else ""
            opened_str = ""
            if e.get("opened_at"):
                opened_str = f' &middot; <span style="color:var(--green);">Opened {e["opened_at"][:16]}</span>'
            replied_str = ""
            if e.get("replied_at"):
                replied_str = f' &middot; <span style="color:var(--green);font-weight:600;">Replied {e["replied_at"][:16]}</span>'

            email_timeline += f"""
            <div style="padding:10px 0;border-bottom:1px solid var(--border-light);">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong style="font-size:13px;">&#9993; You sent: {_esc(e['subject'])}</strong>
                <span style="font-size:11px;color:var(--text-muted);">{step_label} &middot; Variant {e['variant'].upper()}</span>
              </div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                Sent {time_str}{opened_str}{replied_str}
              </div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:6px;line-height:1.5;max-height:60px;overflow:hidden;">
                {_esc(e['body'][:200])}{'...' if len(e['body']) > 200 else ''}
              </div>
            </div>"""

            # Show reply body if exists
            reply_body = e.get("reply_body", "")
            if reply_body:
                sent_color = {"positive": "var(--green-light)", "negative": "var(--red-light)", "neutral": "var(--yellow-light)"}.get(e.get("reply_sentiment", ""), "var(--border-light)")
                sent_border = {"positive": "var(--green)", "negative": "var(--red)", "neutral": "var(--yellow)"}.get(e.get("reply_sentiment", ""), "var(--border)")
                email_timeline += f"""
            <div style="padding:12px;margin:8px 0 8px 20px;background:{sent_color};border-left:3px solid {sent_border};border-radius:0 8px 8px 0;">
              <div style="font-size:11px;font-weight:700;color:var(--text-secondary);margin-bottom:4px;">&#8617; Reply from {_esc(c['contact_name'] or c['contact_email'])}</div>
              <div style="font-size:13px;color:var(--text);line-height:1.6;white-space:pre-wrap;">{_esc(reply_body[:2000])}</div>
            </div>"""

        thread_cards += f"""
        <div class="card" style="margin-bottom:12px;">
          <div style="display:flex;align-items:center;gap:10px;padding-bottom:10px;border-bottom:1px solid var(--border-light);">
            <div style="width:36px;height:36px;background:var(--primary-light);border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--primary);font-size:14px;">
              {_esc(c['contact_name'][:1].upper() if c['contact_name'] else '?')}
            </div>
            <div style="flex:1;">
              <div style="font-weight:600;font-size:14px;">{_esc(c['contact_name'] or c['contact_email'])}</div>
              <div style="font-size:12px;color:var(--text-secondary);">{_esc(c['contact_email'])} &middot; {_esc(c['company'])} &middot; {_esc(c['role'])}</div>
            </div>
            <span class="badge {badge_cls}">{badge_text}</span>
            {sentiment_badge}
            <a href="/campaign/{c['campaign_id']}" style="font-size:11px;color:var(--primary);">{_esc(c['campaign_name'])}</a>
          </div>
          {email_timeline}
          {f'''<div style="padding-top:10px;display:flex;gap:8px;align-items:center;">
            <a href="mailto:{_esc(c["contact_email"])}?subject=Re: {_esc(c["emails"][0]["subject"])}" class="btn btn-outline btn-sm">&#9993; Manual Reply</a>
            <button onclick="generateDraft({c['emails'][0]['sent_id']}, this)" class="btn btn-primary btn-sm">&#129302; AI Draft Reply</button>
          </div>
          <div id="draft-{c['emails'][0]['sent_id']}" style="display:none;margin-top:10px;padding:14px;background:var(--primary-light);border:1px solid var(--primary);border-radius:8px;">
            <div style="font-size:11px;font-weight:700;color:var(--primary-dark);margin-bottom:6px;">&#129302; AI-Suggested Reply</div>
            <div id="draft-text-{c['emails'][0]['sent_id']}" style="font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--text);"></div>
            <div style="margin-top:10px;display:flex;gap:8px;">
              <button onclick="copyDraft({c['emails'][0]['sent_id']})" class="btn btn-green btn-sm">&#128203; Copy to Clipboard</button>
              <button onclick="generateDraft({c['emails'][0]['sent_id']}, this)" class="btn btn-outline btn-sm">&#128260; Regenerate</button>
            </div>
          </div>''' if c['contact_status'] == 'replied' else ''}
        </div>"""

    if not thread_cards:
        thread_cards = """<div class="empty" style="padding:40px;">
          <div class="empty-icon">&#128236;</div>
          <h3>No emails yet</h3>
          <p>Emails will appear here once your campaigns start sending.</p>
        </div>"""

    return _render(t("inbox.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("inbox.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
      <div>
        <h1>&#128236; {t("inbox.title")}</h1>
        <p class="subtitle">Track every email sent, see who opened, and respond to replies.</p>
      </div>
      <button onclick="checkRepliesNow()" class="btn btn-primary btn-sm" id="check-replies-btn">&#128260; Check Replies Now</button>
    </div>

    <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
      <a href="?filter=all" class="btn btn-sm {'btn-primary' if filter_val == 'all' else 'btn-outline'}">All ({count_all})</a>
      <a href="?filter=replied" class="btn btn-sm {'btn-primary' if filter_val == 'replied' else 'btn-outline'}">&#9989; Replied ({count_replied})</a>
      <a href="?filter=opened" class="btn btn-sm {'btn-primary' if filter_val == 'opened' else 'btn-outline'}">&#128065; Opened ({count_opened})</a>
      <a href="?filter=pending" class="btn btn-sm {'btn-primary' if filter_val == 'pending' else 'btn-outline'}">&#9203; Pending ({count_pending})</a>
      <span style="width:1px;background:var(--border);margin:0 4px;"></span>
      <a href="?filter=positive" class="btn btn-sm {'btn-green' if filter_val == 'positive' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'positive' else ''}">&#128077; Positive ({count_positive})</a>
      <a href="?filter=neutral" class="btn btn-sm {'btn-yellow' if filter_val == 'neutral' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'neutral' else ''}">&#8212; Neutral ({count_neutral})</a>
      <a href="?filter=negative" class="btn btn-sm {'btn-red' if filter_val == 'negative' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'negative' else ''}">&#128078; Negative ({count_negative})</a>
    </div>

    {thread_cards}

    <script>
    function checkRepliesNow() {{
      const btn = document.getElementById('check-replies-btn');
      btn.innerHTML = '&#8987; Checking...';
      btn.disabled = true;
      fetch('/api/check-replies', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.new_replies > 0) {{
            btn.innerHTML = '&#9989; Found ' + data.new_replies + ' new reply(s)! Refreshing...';
            setTimeout(() => location.reload(), 1000);
          }} else {{
            btn.innerHTML = '&#10003; Up to date';
            setTimeout(() => {{ btn.innerHTML = '&#128260; Check Replies Now'; btn.disabled = false; }}, 2000);
          }}
        }})
        .catch(() => {{
          btn.innerHTML = '&#9888; Error checking';
          setTimeout(() => {{ btn.innerHTML = '&#128260; Check Replies Now'; btn.disabled = false; }}, 2000);
        }});
    }}

    function generateDraft(sentId, btn) {{
      btn.innerHTML = '&#8987; Generating...';
      btn.disabled = true;
      fetch('/api/reply-draft/' + sentId, {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.draft) {{
            document.getElementById('draft-text-' + sentId).textContent = data.draft;
            document.getElementById('draft-' + sentId).style.display = 'block';
            btn.innerHTML = '&#129302; AI Draft Reply';
            btn.disabled = false;
          }} else {{
            btn.innerHTML = '&#9888; ' + (data.error || 'Failed');
            setTimeout(() => {{ btn.innerHTML = '&#129302; AI Draft Reply'; btn.disabled = false; }}, 2000);
          }}
        }})
        .catch(() => {{
          btn.innerHTML = '&#9888; Error';
          setTimeout(() => {{ btn.innerHTML = '&#129302; AI Draft Reply'; btn.disabled = false; }}, 2000);
        }});
    }}

    function copyDraft(sentId) {{
      const text = document.getElementById('draft-text-' + sentId).textContent;
      navigator.clipboard.writeText(text).then(() => {{
        const btn = event.target;
        btn.innerHTML = '&#9989; Copied!';
        setTimeout(() => btn.innerHTML = '&#128203; Copy to Clipboard', 2000);
      }});
    }}
    </script>
    """, active_page="inbox")


# ---------------------------------------------------------------------------
# Routes — A/B Test Dashboard
# ---------------------------------------------------------------------------

@app.route("/ab-tests")
def ab_tests():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_ab_stats, get_send_time_stats

    raw = get_ab_stats(session["client_id"])
    time_stats = get_send_time_stats(session["client_id"])

    # Group by sequence
    tests = {}
    for r in raw:
        sid = r["seq_id"]
        if sid not in tests:
            tests[sid] = {
                "campaign_name": r["campaign_name"],
                "campaign_id": r["campaign_id"],
                "step": r["step"],
                "subject_a": r["subject_a"],
                "subject_b": r["subject_b"],
                "a": {"sent": 0, "opens": 0, "replies": 0},
                "b": {"sent": 0, "opens": 0, "replies": 0},
            }
        v = r["variant"] or "a"
        if v in ("a", "b"):
            tests[sid][v]["sent"] = r["sent_count"] or 0
            tests[sid][v]["opens"] = r["opens"] or 0
            tests[sid][v]["replies"] = r["replies"] or 0

    # Build test cards
    cards = ""
    for sid, tst in tests.items():
        a, b = tst["a"], tst["b"]
        a_rate = (a["opens"] / a["sent"] * 100) if a["sent"] else 0
        b_rate = (b["opens"] / b["sent"] * 100) if b["sent"] else 0
        a_reply = (a["replies"] / a["sent"] * 100) if a["sent"] else 0
        b_reply = (b["replies"] / b["sent"] * 100) if b["sent"] else 0

        winner = ""
        if a["sent"] >= 5 and b["sent"] >= 5:
            if a_rate > b_rate + 5:
                winner = "a"
            elif b_rate > a_rate + 5:
                winner = "b"

        a_bar_color = "var(--green)" if winner == "a" else "var(--primary)"
        b_bar_color = "var(--green)" if winner == "b" else "var(--primary)"
        a_winner = ' <span style="color:var(--green);font-weight:700;">&#9733; WINNER</span>' if winner == "a" else ""
        b_winner = ' <span style="color:var(--green);font-weight:700;">&#9733; WINNER</span>' if winner == "b" else ""

        cards += f"""
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header">
            <h2>Step {tst['step']} — <a href="/campaign/{tst['campaign_id']}" style="color:var(--primary);">{_esc(tst['campaign_name'])}</a></h2>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:8px;">
            <div style="padding:12px;border:2px solid {'var(--green)' if winner == 'a' else 'var(--border-light)'};border-radius:8px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;">VARIANT A{a_winner}</div>
              <div style="font-size:13px;font-weight:600;margin-bottom:8px;">{_esc(tst['subject_a'])}</div>
              <div style="display:flex;gap:16px;font-size:12px;">
                <div><span style="font-weight:700;font-size:18px;">{a['sent']}</span> sent</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--blue);">{a_rate:.0f}%</span> opens</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--green);">{a_reply:.0f}%</span> replies</div>
              </div>
              <div style="margin-top:8px;background:var(--border-light);border-radius:4px;height:8px;overflow:hidden;">
                <div style="width:{a_rate}%;height:100%;background:{a_bar_color};border-radius:4px;transition:width 0.3s;"></div>
              </div>
            </div>

            <div style="padding:12px;border:2px solid {'var(--green)' if winner == 'b' else 'var(--border-light)'};border-radius:8px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;">VARIANT B{b_winner}</div>
              <div style="font-size:13px;font-weight:600;margin-bottom:8px;">{_esc(tst['subject_b'])}</div>
              <div style="display:flex;gap:16px;font-size:12px;">
                <div><span style="font-weight:700;font-size:18px;">{b['sent']}</span> sent</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--blue);">{b_rate:.0f}%</span> opens</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--green);">{b_reply:.0f}%</span> replies</div>
              </div>
              <div style="margin-top:8px;background:var(--border-light);border-radius:4px;height:8px;overflow:hidden;">
                <div style="width:{b_rate}%;height:100%;background:{b_bar_color};border-radius:4px;transition:width 0.3s;"></div>
              </div>
            </div>
          </div>
        </div>"""

    if not cards:
        cards = """<div class="card"><div class="empty" style="padding:40px;">
          <div class="empty-icon">&#128202;</div>
          <h3>No A/B tests yet</h3>
          <p>Add a B subject line to your email sequences to start testing. Results appear once you've sent at least 5 emails per variant.</p>
        </div></div>"""

    # Build send time heatmap
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    time_grid = {}
    max_rate = 0
    for r in time_stats:
        key = (int(r["dow"]), int(r["hour"]))
        rate = (r["opens"] / r["total"] * 100) if r["total"] else 0
        time_grid[key] = {"total": r["total"], "opens": r["opens"], "rate": rate}
        if rate > max_rate:
            max_rate = rate

    heatmap_rows = ""
    for dow in range(7):
        cells = ""
        for hour in range(6, 22):  # 6am to 10pm
            data = time_grid.get((dow, hour), {"total": 0, "rate": 0})
            if data["total"] == 0:
                bg = "var(--border-light)"
                text_col = "var(--text-muted)"
            else:
                intensity = min(data["rate"] / max(max_rate, 1), 1.0)
                if intensity > 0.7:
                    bg = "var(--green)"
                    text_col = "#fff"
                elif intensity > 0.4:
                    bg = "var(--green-light)"
                    text_col = "var(--green-dark)"
                elif intensity > 0.1:
                    bg = "var(--blue-light)"
                    text_col = "var(--blue)"
                else:
                    bg = "var(--border-light)"
                    text_col = "var(--text-muted)"
            cells += f'<td style="width:36px;height:28px;text-align:center;font-size:10px;background:{bg};color:{text_col};border:1px solid var(--card);" title="{day_names[dow]} {hour}:00 — {data["total"]} sent, {data["rate"]:.0f}% open rate">{data["rate"]:.0f}%</td>'
        heatmap_rows += f"<tr><td style='font-size:11px;font-weight:600;padding-right:8px;color:var(--text-secondary);'>{day_names[dow]}</td>{cells}</tr>"

    hour_headers = "".join(f'<th style="font-size:10px;color:var(--text-muted);font-weight:500;padding:2px;">{h}</th>' for h in range(6, 22))

    heatmap = f"""
    <div class="card" style="margin-top:20px;">
      <div class="card-header"><h2>&#128345; Best Send Times</h2></div>
      <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Open rates by day of week and hour. Green = high open rates.</p>
      <div style="overflow-x:auto;">
        <table style="border-collapse:separate;border-spacing:2px;margin:0;">
          <thead><tr><th></th>{hour_headers}</tr></thead>
          <tbody>{heatmap_rows}</tbody>
        </table>
      </div>
    </div>"""

    return _render(t("ab.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("ab.title")}</div>
    <div class="page-header">
      <h1>&#128202; {t("ab.title")}</h1>
      <p class="subtitle">See which subject lines and send times perform best.</p>
    </div>
    {cards}
    {heatmap}
    """, active_page="ab_tests")


# ---------------------------------------------------------------------------
# Routes — Campaign Calendar
# ---------------------------------------------------------------------------

@app.route("/calendar")
def calendar_view():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_calendar_events
    from collections import defaultdict
    import calendar as cal_module
    from datetime import date, timedelta

    events = get_calendar_events(session["client_id"])

    # Parse month from query string
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month

    # Group events by date
    events_by_date = defaultdict(list)
    for e in events:
        if e.get("date"):
            day_str = e["date"][:10]  # YYYY-MM-DD
            events_by_date[day_str].append(e)

    # Build calendar grid
    first_weekday, num_days = cal_module.monthrange(year, month)
    month_name = cal_module.month_name[month]

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Calendar cells
    cells = ""
    # Empty cells before first day
    for _ in range(first_weekday):
        cells += '<td style="padding:4px;vertical-align:top;background:var(--border-light);"></td>'

    for day in range(1, num_days + 1):
        day_str = f"{year}-{month:02d}-{day:02d}"
        day_events = events_by_date.get(day_str, [])
        is_today = (year == today.year and month == today.month and day == today.day)

        bg = "var(--card)" if not is_today else "var(--primary-light)"
        border = "2px solid var(--primary)" if is_today else "1px solid var(--border-light)"

        event_dots = ""
        sent_count = sum(1 for e in day_events if e["event_type"] == "sent")
        sched_count = sum(1 for e in day_events if e["event_type"] == "scheduled")
        replied_count = sum(1 for e in day_events if e.get("email_status") == "replied")
        opened_count = sum(1 for e in day_events if e.get("email_status") in ("opened", "clicked"))

        if day_events:
            dots = []
            if sent_count:
                dots.append(f'<span style="font-size:10px;color:var(--blue);">{sent_count} sent</span>')
            if sched_count:
                dots.append(f'<span style="font-size:10px;color:var(--yellow);">{sched_count} sched</span>')
            if replied_count:
                dots.append(f'<span style="font-size:10px;color:var(--green);">{replied_count} reply</span>')
            if opened_count:
                dots.append(f'<span style="font-size:10px;color:var(--primary);">{opened_count} open</span>')
            event_dots = "<br>".join(dots)

            # Tooltip with details
            detail_lines = []
            for e in day_events[:5]:
                icon = "&#128232;" if e["event_type"] == "sent" else "&#128197;"
                name = _esc(e.get("contact_name") or e.get("contact_email", ""))
                subj = _esc((e.get("subject") or "")[:30])
                detail_lines.append(f"{icon} {name}: {subj}")
            tooltip = "&#10;".join(detail_lines)
            if len(day_events) > 5:
                tooltip += f"&#10;... and {len(day_events) - 5} more"
        else:
            tooltip = ""

        cells += f'''<td style="padding:6px;vertical-align:top;background:{bg};border:{border};min-height:70px;width:14.28%;" title="{tooltip}">
          <div style="font-weight:{'700' if is_today else '500'};font-size:13px;color:{'var(--primary)' if is_today else 'var(--text)'};">{day}</div>
          <div style="margin-top:2px;line-height:1.4;">{event_dots}</div>
        </td>'''

    # Fill remaining cells
    total_cells = first_weekday + num_days
    remaining = (7 - total_cells % 7) % 7
    for _ in range(remaining):
        cells += '<td style="padding:4px;vertical-align:top;background:var(--border-light);"></td>'

    # Arrange into rows
    all_cells = cells
    rows = ""
    idx = 0
    import re as _re
    cell_list = _re.findall(r'<td[^>]*>.*?</td>', all_cells, _re.DOTALL)
    for i in range(0, len(cell_list), 7):
        rows += "<tr>" + "".join(cell_list[i:i+7]) + "</tr>"

    # Stats sidebar
    total_sent = sum(1 for e in events if e["event_type"] == "sent")
    total_scheduled = sum(1 for e in events if e["event_type"] == "scheduled")
    total_replied = sum(1 for e in events if e.get("email_status") == "replied")

    # Upcoming events list (next 7 days)
    upcoming_html = ""
    for i in range(7):
        d = today + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        day_evts = events_by_date.get(ds, [])
        if day_evts:
            label = "Today" if i == 0 else ("Tomorrow" if i == 1 else d.strftime("%a %b %d"))
            items = ""
            for e in day_evts[:3]:
                icon = "&#9989;" if e.get("email_status") == "replied" else ("&#128232;" if e["event_type"] == "sent" else "&#128197;")
                name = _esc(e.get("contact_name") or e.get("contact_email", ""))
                camp = _esc(e.get("campaign_name", ""))
                items += f'<div style="padding:4px 0;font-size:12px;">{icon} <strong>{name}</strong> — {camp}</div>'
            if len(day_evts) > 3:
                items += f'<div style="font-size:11px;color:var(--text-muted);">+{len(day_evts) - 3} more</div>'
            upcoming_html += f'<div style="margin-bottom:10px;"><div style="font-weight:600;font-size:12px;color:var(--text-secondary);margin-bottom:2px;">{label}</div>{items}</div>'

    if not upcoming_html:
        upcoming_html = '<div style="font-size:13px;color:var(--text-muted);padding:12px 0;">No upcoming sends</div>'

    return _render(t("calendar.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("calendar.title")}</div>
    <div class="page-header">
      <h1>&#128197; {t("calendar.title")}</h1>
      <p class="subtitle">View all sent and scheduled emails across campaigns.</p>
    </div>

    <div style="display:grid;grid-template-columns:1fr 280px;gap:20px;">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
          <a href="?year={prev_year}&month={prev_month}" class="btn btn-outline btn-sm">&larr;</a>
          <h2 style="font-size:18px;">{month_name} {year}</h2>
          <a href="?year={next_year}&month={next_month}" class="btn btn-outline btn-sm">&rarr;</a>
        </div>
        <table style="width:100%;border-collapse:separate;border-spacing:3px;table-layout:fixed;">
          <thead><tr>
            {''.join(f'<th style="font-size:11px;color:var(--text-muted);font-weight:600;padding:4px;text-align:center;">{d}</th>' for d in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])}
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>

      <div>
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header"><h2>Overview</h2></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;">
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--blue);">{total_sent}</div>
              <div style="font-size:11px;color:var(--text-muted);">Emails Sent</div>
            </div>
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--yellow);">{total_scheduled}</div>
              <div style="font-size:11px;color:var(--text-muted);">Scheduled</div>
            </div>
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--green);">{total_replied}</div>
              <div style="font-size:11px;color:var(--text-muted);">Replies</div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>&#128197; Upcoming</h2></div>
          {upcoming_html}
        </div>
      </div>
    </div>
    """, active_page="calendar")


# ---------------------------------------------------------------------------
# Routes — Campaign CRUD
# ---------------------------------------------------------------------------

@app.route("/campaign/new", methods=["GET", "POST"])
def new_campaign():
    if not _logged_in():
        return redirect(url_for("login"))
    if request.method == "POST":
        # Check campaign limit for free plan
        from outreach.db import get_subscription
        from outreach.config import PLAN_LIMITS
        sub = get_subscription(session["client_id"])
        plan = sub.get("plan", "free")
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        camp_limit = limits.get("campaigns", -1)
        if camp_limit != -1:
            existing = get_campaigns(session["client_id"])
            if len(existing) >= camp_limit:
                flash(("error", f"Free plan allows {camp_limit} campaigns. Upgrade to create more."))
                return redirect(url_for("new_campaign"))

        name = request.form.get("name", "").strip()
        btype = request.form.get("business_type", "").strip()
        audience = request.form.get("target_audience", "").strip()
        tone = request.form.get("tone", "professional")
        scheduled_start = request.form.get("scheduled_start", "").strip()
        if not name or not btype or not audience:
            flash(("error", "Please fill in all required fields."))
            return redirect(url_for("new_campaign"))

        # Require physical address for CAN-SPAM compliance
        client = get_client(session["client_id"])
        if not client.get("physical_address", "").strip():
            flash(("error", "A physical mailing address is required to create campaigns (CAN-SPAM). Please add one in Settings."))
            return redirect(url_for("new_campaign"))

        camp_id = create_campaign(session["client_id"], name, btype, audience, tone, scheduled_start)

        try:
            steps = generate_sequence(btype, audience, tone)
            for s in steps:
                save_sequence(
                    camp_id, s["step"], s["subject_a"], s.get("subject_b", ""),
                    s["body"], s.get("body_b", ""), s.get("delay_days", 0),
                )
            flash(("success", f"Campaign created with {len(steps)} email steps! Add contacts to start sending."))
        except Exception as e:
            flash(("error", f"AI generation failed: {e}"))

        return redirect(f"/campaign/{camp_id}")

    # Check if user has any existing campaigns for tutorial
    existing_camps = get_campaigns(session["client_id"])
    show_tutorial = "block" if len(existing_camps) == 0 else "none"

    return _render(t("campaign.new_title"), """
    <div class="breadcrumb"><a href="/dashboard">{{lbl_dashboard}}</a> / {{lbl_new_campaign}}</div>
    <div class="page-header">
      <h1>{{lbl_new_campaign}}</h1>
    </div>

    <div id="campaign-tutorial" class="card" style="display:{{show_tutorial}};margin-bottom:24px;border:2px solid var(--primary);background:linear-gradient(135deg, var(--primary-light), var(--bg));">
      <div style="padding:24px;position:relative;">
        <button onclick="document.getElementById('campaign-tutorial').style.display='none'" style="position:absolute;top:12px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--text-muted);">&times;</button>
        <h2 style="margin:0 0 8px;">&#129302; How Campaigns Work</h2>
        <p style="color:var(--text-secondary);margin-bottom:16px;">MachReach uses AI to create your entire email sequence automatically. Here's what to expect:</p>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">1</span>
            <div>
              <strong>Fill in the form below</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">Tell us your business type, target audience, and preferred tone. The more specific, the better your emails will be.</p>
            </div>
          </div>
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">2</span>
            <div>
              <strong>AI generates a multi-step email sequence</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">Our AI creates 3-5 follow-up emails with A/B test variants, spaced out over days. You can edit any of them before sending.</p>
            </div>
          </div>
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">3</span>
            <div>
              <strong>Add contacts and start sending</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">Import contacts via CSV or add them manually. MachReach sends emails on your behalf and tracks opens, clicks, and replies.</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <form method="post" data-loading>
        <div class="form-group">
          <label>{{lbl_name}}</label>
          <input name="name" placeholder="e.g. Q2 Agency Outreach" required>
        </div>
        <div class="form-group">
          <label>Your Business Type</label>
          <input name="business_type" placeholder="e.g. AI-powered email outreach SaaS" required>
        </div>
        <div class="form-group">
          <label>{{lbl_audience}}</label>
          <textarea name="target_audience" placeholder="e.g. Marketing directors at mid-size e-commerce companies" required></textarea>
        </div>
        <div class="form-group">
          <label>{{lbl_tone}}</label>
          <select name="tone">
            <option value="professional">{{lbl_professional}}</option>
            <option value="casual">{{lbl_casual}}</option>
            <option value="direct">Direct &amp; Concise</option>
            <option value="humorous">Witty &amp; Humorous</option>
          </select>
        </div>
        <div class="form-group">
          <label>&#128197; Schedule Start <span class="text-xs text-muted">(optional)</span></label>
          <input type="datetime-local" name="scheduled_start">
          <p class="form-hint">Leave empty to send immediately when activated. Set a date/time to delay sending until then.</p>
        </div>
        <div class="btn-group mt-2">
          <button class="btn btn-primary" type="submit">
            <span class="btn-text">&#129302; {{lbl_create}}</span>
            <span class="spinner"></span>
          </button>
          <a href="/dashboard" class="btn btn-outline">{{lbl_cancel}}</a>
        </div>
      </form>
    </div>
    """, active_page="new_campaign",
        lbl_dashboard=t("dash.title"), lbl_new_campaign=t("campaign.new_title"),
        lbl_name=t("campaign.name"), lbl_audience=t("campaign.target_audience"),
        lbl_tone=t("campaign.tone"), lbl_professional=t("campaign.tone_professional"),
        lbl_casual=t("campaign.tone_casual"), lbl_create=t("campaign.create_btn"),
        lbl_cancel=t("common.cancel"), show_tutorial=show_tutorial)


@app.route("/campaign/<int:campaign_id>")
def view_campaign(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    camp = get_campaign(campaign_id)
    if not camp:
        return redirect(url_for("dashboard"))

    # Access check: owner, full-team member, or campaign-scoped member
    data_cid = _effective_client_id()
    from outreach.db import get_team_campaign_ids
    shared_camp_ids = get_team_campaign_ids(session["client_id"])
    if camp["client_id"] != session["client_id"] and camp["client_id"] != data_cid and campaign_id not in shared_camp_ids:
        return redirect(url_for("dashboard"))

    tab = request.args.get("tab", "overview")
    stats = get_campaign_stats(campaign_id)
    sequences = get_sequences(campaign_id)
    contacts = get_campaign_contacts(campaign_id)

    from outreach.db import get_ab_stats
    ab_data = get_ab_stats(campaign_id)

    # Status badge
    sc = {"active": "badge-green", "draft": "badge-gray", "paused": "badge-yellow", "completed": "badge-blue"}.get(camp["status"], "badge-gray")
    status_badge = f'<span class="badge {sc}">{camp["status"]}</span>'
    sched = camp.get("scheduled_start") or ""
    if sched:
        status_badge += f' <span class="badge badge-blue" title="Scheduled">&#128197; {_esc(sched)}</span>'

    # Schedule form for draft/paused campaigns
    schedule_html = ""
    if camp["status"] in ("draft", "paused"):
        clear_btn = ""
        if sched:
            clear_btn = f'<form method="post" action="/campaign/{campaign_id}/schedule" style="margin:0;"><input type="hidden" name="scheduled_start" value=""><button class="btn btn-ghost btn-sm" type="submit">Clear</button></form>'
        schedule_html = f"""
        <div class="card" style="margin-top:16px;">
          <div class="card-header"><h2>&#128197; Schedule Start</h2></div>
          <form method="post" action="/campaign/{campaign_id}/schedule" style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap;">
            <div class="form-group" style="margin:0;flex:1;min-width:200px;">
              <label class="text-xs text-muted">Start sending at</label>
              <input type="datetime-local" name="scheduled_start" value="{_esc(sched)}">
            </div>
            <button class="btn btn-outline btn-sm" type="submit">{'Update Schedule' if sched else 'Set Schedule'}</button>
          </form>
          {clear_btn}
        </div>"""

    # Progress
    pct = (stats["emails_sent"] / stats["total_contacts"] * 100) if stats["total_contacts"] else 0

    # Action buttons
    actions = ""
    if camp["status"] == "draft":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="activate"><button class="btn btn-green btn-sm">&#9654; Activate</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>
        <form method="post" action="/campaign/{campaign_id}/delete" class="confirm-form" onsubmit="return confirm('Delete this campaign and all its data?')"><button class="btn btn-red btn-sm">Delete</button></form>"""
    elif camp["status"] == "active":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="pause"><button class="btn btn-yellow btn-sm">&#9208; Pause</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>"""
    elif camp["status"] == "paused":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="activate"><button class="btn btn-green btn-sm">&#9654; Resume</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>
        <form method="post" action="/campaign/{campaign_id}/delete" class="confirm-form" onsubmit="return confirm('Delete this campaign and all its data?')"><button class="btn btn-red btn-sm">Delete</button></form>"""

    # Sequence cards with previews
    seq_html = ""
    sample_contact = {"name": "John", "company": "Acme Inc", "role": "CEO"}
    # Build contact options for preview selector
    contact_options = '<option value="">Sample (John, CEO at Acme Inc)</option>'
    for c in contacts[:100]:
        contact_options += f'<option value="{c["id"]}">{_esc(c["name"])} ({_esc(c["email"])})</option>'
    for s in sequences:
        delay_str = "Initial email" if s["step"] == 1 else f"Follow-up &bull; {s['delay_days']}d delay"
        subj_b = f'<div style="margin-top:3px;"><span class="seq-subject-label">Subject B:</span> <span class="seq-subject">{_esc(s["subject_b"])}</span></div>' if s.get("subject_b") else ""

        # Preview with sample personalization
        preview_subj = personalize_email(s["subject_a"], sample_contact, SENDER_NAME)
        preview_body = personalize_email(s["body_a"], sample_contact, SENDER_NAME)

        seq_html += f"""
        <div class="seq-card">
          <div class="seq-actions">
            <button class="btn btn-ghost btn-sm" onclick="showPreview({s['id']})">&#128065; Preview</button>
            <a href="/campaign/{campaign_id}/sequence/{s['id']}/edit" class="btn btn-ghost btn-sm">&#9998; Edit</a>
          </div>
          <div class="seq-step">Step {s['step']} <span class="seq-delay">&bull; {delay_str}</span></div>
          <div><span class="seq-subject-label">Subject A:</span> <span class="seq-subject">{_esc(s['subject_a'])}</span></div>
          {subj_b}
          <div class="seq-body">{_esc(s['body_a'])}</div>
        </div>
        <div class="preview-modal" id="preview-{s['id']}">
          <div class="preview-content">
            <div class="preview-header">
              <h3>Email Preview (Step {s['step']})</h3>
              <button class="btn btn-ghost btn-sm" onclick="hidePreview({s['id']})">&#10005; Close</button>
            </div>
            <div class="preview-body">
              <div style="margin-bottom:12px;display:flex;align-items:center;gap:8px;">
                <label class="text-xs text-muted" style="white-space:nowrap;">Preview as:</label>
                <select class="preview-contact-select" data-seq-id="{s['id']}" data-camp-id="{campaign_id}" onchange="loadPreview(this)" style="flex:1;font-size:12px;padding:4px 8px;">
                  {contact_options}
                </select>
              </div>
              <p class="text-xs text-muted mb-4" id="preview-hint-{s['id']}">Showing how this email looks with sample data (John, CEO at Acme Inc).</p>
              <div class="preview-field"><div class="pf-label">Subject</div><div class="pf-value" style="font-weight:600;" id="preview-subj-{s['id']}">{_esc(preview_subj)}</div></div>
              <div class="preview-email" id="preview-body-{s['id']}">{_esc(preview_body)}</div>
            </div>
          </div>
        </div>"""

    if not seq_html:
        seq_html = '<div class="empty"><div class="empty-icon">&#128221;</div><h3>No email sequence</h3><p>Something went wrong with AI generation. Try creating a new campaign.</p></div>'

    # Contacts table
    contacts_html = ""
    for c in contacts:
        csc = {"sent": "badge-blue", "pending": "badge-gray", "replied": "badge-green", "bounced": "badge-red", "unsubscribed": "badge-yellow", "opened": "badge-purple"}.get(c["status"], "badge-gray")
        lang = c.get("language", "en") or "en"
        contacts_html += f"""<tr>
          <td><strong>{_esc(c['name'])}</strong></td>
          <td style="font-family:monospace;font-size:12px;">{_esc(c['email'])}</td>
          <td>{_esc(c['company'])}</td>
          <td>{_esc(c.get('role', ''))}</td>
          <td><span class="badge badge-gray" style="font-size:10px;text-transform:uppercase;">{_esc(lang)}</span></td>
          <td><span class="badge {csc}">{c['status']}</span></td>
          <td style="text-align:right;">
            <form method="post" action="/campaign/{campaign_id}/contact/{c['id']}/delete" class="confirm-form" onsubmit="return confirm('Remove this contact?')">
              <button class="btn btn-ghost btn-sm" style="color:var(--red);" title="Remove">&#10005;</button>
            </form>
          </td>
        </tr>"""
    if not contacts_html:
        contacts_html = f"""<tr><td colspan="7">
          <div class="empty" style="padding:28px;">
            <div class="empty-icon">&#128101;</div>
            <h3>No contacts yet</h3>
            <p>Upload a CSV or paste contacts to get started.</p>
          </div>
        </td></tr>"""

    # Count by status
    pending_count = sum(1 for c in contacts if c["status"] == "pending")
    sent_count = sum(1 for c in contacts if c["status"] == "sent")
    replied_count = sum(1 for c in contacts if c["status"] == "replied")

    # Activity feed
    sent_emails = get_sent_emails(campaign_id)
    activity_html = ""
    for e in sent_emails[:20]:
        dot_class = "replied" if e.get("status") == "replied" else ("opened" if e.get("status") == "opened" else "sent")
        action = "replied to" if e.get("status") == "replied" else ("opened" if e.get("status") == "opened" else "sent to")
        activity_html += f"""
        <div class="activity-item">
          <div class="activity-dot {dot_class}"></div>
          <div>
            <div class="activity-text">Email {action} <strong>{_esc(e['contact_name'])}</strong> ({_esc(e['contact_email'])})</div>
            <div class="activity-time">Step {e['step']} &bull; {e.get('sent_at', '')}</div>
          </div>
        </div>"""
    if not activity_html:
        activity_html = '<div class="empty" style="padding:24px;"><div class="empty-icon">&#128172;</div><p>No activity yet. Activate the campaign to start sending.</p></div>'

    # A/B test results
    ab_html = ""
    # Group ab_data by step
    ab_by_step = {}
    for row in ab_data:
        step = row["step"]
        ab_by_step.setdefault(step, {})[row["variant"]] = row
    if ab_by_step:
        for step in sorted(ab_by_step.keys()):
            variants = ab_by_step[step]
            a = variants.get("a", {"sent": 0, "opened": 0, "replied": 0, "bounced": 0})
            b = variants.get("b", {"sent": 0, "opened": 0, "replied": 0, "bounced": 0})
            # Find matching sequence for subject lines
            seq_match = next((s for s in sequences if s["step"] == step), None)
            subj_a = _esc(seq_match["subject_a"]) if seq_match else "Variant A"
            subj_b = _esc(seq_match.get("subject_b", "")) if seq_match else "Variant B"
            a_sent = a.get("sent", 0) or 0
            b_sent = b.get("sent", 0) or 0
            a_open_r = (a.get("opened", 0) / a_sent * 100) if a_sent else 0
            b_open_r = (b.get("opened", 0) / b_sent * 100) if b_sent else 0
            a_reply_r = (a.get("replied", 0) / a_sent * 100) if a_sent else 0
            b_reply_r = (b.get("replied", 0) / b_sent * 100) if b_sent else 0
            # Determine winner
            winner = ""
            if a_sent >= 5 and b_sent >= 5:
                if a_reply_r > b_reply_r:
                    winner = "a"
                elif b_reply_r > a_reply_r:
                    winner = "b"
                elif a_open_r > b_open_r:
                    winner = "a"
                elif b_open_r > a_open_r:
                    winner = "b"
            win_a = "border:2px solid var(--green);" if winner == "a" else ""
            win_b = "border:2px solid var(--green);" if winner == "b" else ""
            trophy_a = ' <span style="color:var(--green);font-weight:700;">&#127942; Winner</span>' if winner == "a" else ""
            trophy_b = ' <span style="color:var(--green);font-weight:700;">&#127942; Winner</span>' if winner == "b" else ""

            if not subj_b:
                ab_html += f"""
                <div class="card" style="padding:20px;margin-bottom:16px;">
                  <h3 style="font-size:16px;font-weight:700;margin-bottom:8px;">Step {step}</h3>
                  <p style="font-size:13px;color:var(--text-muted);">No B variant configured for this step. <a href="/campaign/{campaign_id}/sequence/{seq_match['id']}/edit" style="color:var(--primary);">Add a Subject B</a> to start A/B testing.</p>
                </div>"""
                continue

            # Build bar widths
            max_open = max(a_open_r, b_open_r, 1)
            max_reply = max(a_reply_r, b_reply_r, 1)

            ab_html += f"""
            <div class="card" style="padding:24px;margin-bottom:16px;">
              <h3 style="font-size:16px;font-weight:700;margin-bottom:16px;">Step {step} — A/B Comparison</h3>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div style="padding:16px;border-radius:var(--radius-xs);background:var(--bg);{win_a}">
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                    <span class="badge badge-blue" style="font-size:11px;">A</span>
                    <span style="font-size:13px;font-weight:600;">{subj_a}</span>{trophy_a}
                  </div>
                  <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">Sent: <strong>{a_sent}</strong></div>
                  <div style="margin-bottom:8px;">
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:3px;">Open Rate: <strong>{a_open_r:.1f}%</strong> ({a.get('opened',0)})</div>
                    <div style="height:8px;background:var(--border-light);border-radius:4px;overflow:hidden;"><div style="height:100%;width:{a_open_r/max_open*100:.0f}%;background:var(--green);border-radius:4px;"></div></div>
                  </div>
                  <div>
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:3px;">Reply Rate: <strong>{a_reply_r:.1f}%</strong> ({a.get('replied',0)})</div>
                    <div style="height:8px;background:var(--border-light);border-radius:4px;overflow:hidden;"><div style="height:100%;width:{a_reply_r/max_reply*100:.0f}%;background:var(--primary);border-radius:4px;"></div></div>
                  </div>
                </div>
                <div style="padding:16px;border-radius:var(--radius-xs);background:var(--bg);{win_b}">
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                    <span class="badge badge-purple" style="font-size:11px;">B</span>
                    <span style="font-size:13px;font-weight:600;">{subj_b}</span>{trophy_b}
                  </div>
                  <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">Sent: <strong>{b_sent}</strong></div>
                  <div style="margin-bottom:8px;">
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:3px;">Open Rate: <strong>{b_open_r:.1f}%</strong> ({b.get('opened',0)})</div>
                    <div style="height:8px;background:var(--border-light);border-radius:4px;overflow:hidden;"><div style="height:100%;width:{b_open_r/max_open*100:.0f}%;background:var(--green);border-radius:4px;"></div></div>
                  </div>
                  <div>
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:3px;">Reply Rate: <strong>{b_reply_r:.1f}%</strong> ({b.get('replied',0)})</div>
                    <div style="height:8px;background:var(--border-light);border-radius:4px;overflow:hidden;"><div style="height:100%;width:{b_reply_r/max_reply*100:.0f}%;background:var(--primary);border-radius:4px;"></div></div>
                  </div>
                </div>
              </div>
              {'<p style="font-size:11px;color:var(--text-muted);margin-top:10px;text-align:center;">Need at least 5 sends per variant to declare a winner.</p>' if not winner else ''}
            </div>"""
    else:
        ab_html = '<div class="empty" style="padding:40px;"><div class="empty-icon">&#128202;</div><h3>No A/B data yet</h3><p>Start sending emails to see variant performance comparison.</p></div>'

    return _render(_esc(camp["name"]), """
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / {{camp_name}}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="display:flex;align-items:center;gap:10px;">
          <h1>{{camp_name}}</h1>
          {{status_badge}}
        </div>
      </div>
      <div class="btn-group">{{actions}}</div>
    </div>

    <div class="stats-grid" data-campaign-id="{{camp_id}}">
      <div class="stat-card stat-purple">
        <div class="num" data-stat="camp_total_contacts">{{stats.total_contacts}}</div><div class="label">Contacts</div>
        <div class="progress-wrap"><div class="progress-bar bar-purple" style="width:{{pct_fmt}}%"></div></div>
      </div>
      <div class="stat-card stat-blue"><div class="num" data-stat="camp_emails_sent">{{stats.emails_sent}}</div><div class="label">Sent</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="camp_open_rate_fmt">{{open_rate_fmt}}</div><div class="label">Open Rate</div></div>
      <div class="stat-card stat-purple"><div class="num" data-stat="camp_reply_rate_fmt">{{reply_rate_fmt}}</div><div class="label">Reply Rate</div></div>
    </div>

    <div class="tabs">
      <a class="tab {% if tab == 'overview' %}active{% endif %}" href="?tab=overview">Sequence <span class="tab-count">{{num_sequences}}</span></a>
      <a class="tab {% if tab == 'ab' %}active{% endif %}" href="?tab=ab">A/B Results</a>
      <a class="tab {% if tab == 'contacts' %}active{% endif %}" href="?tab=contacts">Contacts <span class="tab-count">{{stats.total_contacts}}</span></a>
      <a class="tab {% if tab == 'activity' %}active{% endif %}" href="?tab=activity">Activity <span class="tab-count">{{num_sent}}</span></a>
    </div>

    {% if tab == 'overview' %}
      {{seq_html}}
      {{schedule_html}}

    {% elif tab == 'ab' %}
      {{ab_html}}

    {% elif tab == 'contacts' %}
      <div class="card">
        <div class="card-header"><h2>Add Contacts</h2></div>
        <form method="post" action="/campaign/{{camp_id}}/contacts" enctype="multipart/form-data">
          <div class="form-group">
            <label>Upload CSV file</label>
            <input type="file" name="csv_file" accept=".csv">
            <p class="form-hint">CSV columns: name, email, company, role, language (header row auto-skipped). Language is optional (default: en).</p>
          </div>
          <hr class="form-divider">
          <div class="form-group">
            <label>Or paste manually</label>
            <textarea name="contacts_csv" placeholder="John Doe,john@company.com,Acme Inc,CEO,en&#10;Maria Garcia,maria@empresa.com,Empresa SA,Directora,es" style="min-height:70px;font-family:monospace;font-size:12px;"></textarea>
          </div>
          <label style="display:flex;align-items:center;gap:8px;margin-bottom:12px;font-size:13px;">
            <input type="checkbox" name="consent" value="1" required>
            I confirm these contacts have opted in to receive emails or I have a legitimate business interest (CAN-SPAM / GDPR).
          </label>
          <button class="btn btn-green" type="submit">Add Contacts</button>
        </form>
        <hr class="form-divider">
        <div style="display:flex;align-items:center;gap:12px;">
          <button class="btn btn-outline" onclick="document.getElementById('crm-import-modal').style.display='flex'">&#128209; Import from Contacts Book</button>
          <span class="text-xs text-muted">Pick contacts from your CRM</span>
        </div>
      </div>

      <!-- CRM Import Modal -->
      <div id="crm-import-modal" style="display:none;position:fixed;inset:0;z-index:999;background:rgba(0,0,0,.5);align-items:center;justify-content:center;">
        <div style="background:var(--card);border-radius:var(--radius);max-width:640px;width:90%;max-height:80vh;display:flex;flex-direction:column;box-shadow:var(--shadow-lg);">
          <div style="padding:20px 24px;border-bottom:1px solid var(--border-light);display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;">Import from Contacts Book</h3>
            <button class="btn btn-ghost btn-sm" onclick="document.getElementById('crm-import-modal').style.display='none'">&#10005;</button>
          </div>
          <div style="padding:16px 24px;">
            <input id="crm-search" type="text" placeholder="Search contacts..." style="width:100%;" oninput="searchCrmContacts(this.value)">
          </div>
          <div id="crm-contacts-list" style="overflow-y:auto;flex:1;padding:0 24px 16px;">
            <p class="text-muted text-xs">Loading contacts...</p>
          </div>
          <div style="padding:16px 24px;border-top:1px solid var(--border-light);display:flex;justify-content:space-between;align-items:center;">
            <label style="font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer;">
              <input type="checkbox" id="crm-select-all" onchange="toggleAllCrm(this.checked)"> Select all
            </label>
            <div class="btn-group">
              <button class="btn btn-outline btn-sm" onclick="document.getElementById('crm-import-modal').style.display='none'">Cancel</button>
              <button class="btn btn-green btn-sm" onclick="importCrmContacts()">Import Selected</button>
            </div>
          </div>
        </div>
      </div>
      <script>var _campId = {{camp_id}};</script>
      {% raw %}
      <script>
      var _crmCache = [];
      function searchCrmContacts(q) {
        var list = document.getElementById('crm-contacts-list');
        var filtered = _crmCache.filter(function(c) {
          var s = (c.name + ' ' + c.email + ' ' + (c.company||'') + ' ' + (c.tags||'')).toLowerCase();
          return s.indexOf(q.toLowerCase()) >= 0;
        });
        renderCrmList(filtered);
      }
      function renderCrmList(contacts) {
        var list = document.getElementById('crm-contacts-list');
        if (!contacts.length) { list.innerHTML = '<p class="text-muted text-xs">No contacts found.</p>'; return; }
        var html = '<div style="display:flex;flex-direction:column;gap:6px;">';
        contacts.forEach(function(c) {
          html += '<label style="display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:var(--radius-xs);cursor:pointer;border:1px solid var(--border-light);transition:background .15s;">' +
            '<input type="checkbox" class="crm-cb" value="' + c.id + '" style="width:16px;height:16px;flex-shrink:0;accent-color:var(--primary);cursor:pointer;">' +
            '<div style="flex:1;min-width:0;overflow:hidden;">' +
              '<div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + (c.name||c.email) + '</div>' +
              '<div style="font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + c.email + (c.company ? ' &bull; ' + c.company : '') + (c.role ? ' &bull; ' + c.role : '') + '</div>' +
            '</div>' +
            (c.tags ? '<span class="badge badge-gray" style="font-size:10px;flex-shrink:0;">' + c.tags + '</span>' : '') +
          '</label>';
        });
        html += '</div>';
        list.innerHTML = html;
      }
      function toggleAllCrm(checked) {
        document.querySelectorAll('.crm-cb').forEach(function(cb) { cb.checked = checked; });
      }
      function importCrmContacts() {
        var ids = [];
        document.querySelectorAll('.crm-cb:checked').forEach(function(cb) { ids.push(parseInt(cb.value)); });
        if (!ids.length) { alert('Select at least one contact.'); return; }
        fetch('/campaign/' + _campId + '/import-contacts', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({contact_ids: ids})
        }).then(function(r) { return r.json(); }).then(function(d) {
          if (d.ok) { location.reload(); } else { alert(d.error || 'Import failed'); }
        });
      }
      // Load CRM contacts when modal opens
      (function loadCrm() {
        fetch('/api/contacts-book/list')
          .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then(function(data) {
            _crmCache = data.contacts || [];
            renderCrmList(_crmCache);
          })
          .catch(function(err) {
            document.getElementById('crm-contacts-list').innerHTML =
              '<p class="text-muted text-xs" style="color:var(--red);">Failed to load contacts: ' + err.message + '</p>';
          });
      })();
      </script>
      {% endraw %}

      <div class="card">
        <div class="card-header">
          <h2>Contact List</h2>
          <div class="text-xs text-muted">
            <span class="badge badge-gray">{{pending}} pending</span>
            <span class="badge badge-blue">{{sent_c}} sent</span>
            <span class="badge badge-green">{{replied}} replied</span>
          </div>
        </div>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Lang</th><th>Status</th><th></th></tr></thead>
          <tbody>{{contacts_html}}</tbody>
        </table>
      </div>

    {% elif tab == 'activity' %}
      <div class="card">
        <div class="card-header"><h2>Recent Activity</h2></div>
        {{activity_html}}
      </div>
    {% endif %}
    """, camp_name=_esc(camp["name"]), status_badge=Markup(status_badge), actions=Markup(actions),
        stats=stats, seq_html=Markup(seq_html), contacts_html=Markup(contacts_html),
        activity_html=Markup(activity_html), ab_html=Markup(ab_html),
        schedule_html=Markup(schedule_html),
        camp_id=campaign_id, tab=tab, camp_status=camp["status"],
        pct_fmt=f"{min(pct,100):.0f}", num_sequences=len(sequences), num_sent=len(sent_emails),
        pending=pending_count, sent_c=sent_count, replied=replied_count,
        open_rate_fmt=f"{stats['open_rate']:.0%}", reply_rate_fmt=f"{stats['reply_rate']:.0%}")

@app.route("/campaign/<int:cid>/sequence/<int:sid>/edit", methods=["GET", "POST"])
def edit_sequence(cid, sid):
    if not _logged_in():
        return redirect(url_for("login"))
    sequences = get_sequences(cid)
    seq = None
    for s in sequences:
        if s["id"] == sid:
            seq = s
            break
    if not seq:
        return redirect(f"/campaign/{cid}")

    if request.method == "POST":
        subject_a = request.form.get("subject_a", "").strip()
        subject_b = request.form.get("subject_b", "").strip()
        body_a = request.form.get("body_a", "").strip()
        delay = int(request.form.get("delay_days", 0))
        if subject_a and body_a:
            update_sequence(sid, subject_a, subject_b, body_a, delay)
            flash(("success", f"Step {seq['step']} updated."))
        return redirect(f"/campaign/{cid}")

    return _render("Edit Sequence", """
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/campaign/{{cid}}">Campaign</a> / Edit Step {{seq.step}}</div>
    <div class="page-header">
      <h1>Edit Step {{seq.step}}</h1>
    </div>
    <div class="card">
      <form method="post">
        <div class="form-group"><label>Subject Line A</label><input name="subject_a" value="{{seq.subject_a}}" required></div>
        <div class="form-group"><label>Subject Line B (A/B test)</label><input name="subject_b" value="{{seq.subject_b or ''}}"></div>
        <div class="form-group"><label>Email Body</label><textarea name="body_a" style="min-height:180px;font-family:monospace;font-size:13px;" required>{{seq.body_a}}</textarea></div>
        <div class="form-group" style="max-width:200px;"><label>Delay (days)</label><input name="delay_days" type="number" min="0" value="{{seq.delay_days}}"></div>
        <p class="form-hint">Use placeholders: <code>{{name}}</code> <code>{{company}}</code> <code>{{role}}</code> <code>{{sender_name}}</code></p>
        <div class="btn-group mt-2">
          <button class="btn btn-primary" type="submit">Save Changes</button>
          <a href="/campaign/{{cid}}" class="btn btn-outline">Cancel</a>
        </div>
      </form>
    </div>
    """, seq=seq, cid=cid)

@app.route("/campaign/<int:campaign_id>/contacts", methods=["POST"])
def upload_contacts(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    if not request.form.get("consent"):
        flash(("error", "You must confirm contacts have opted in before importing."))
        return redirect(f"/campaign/{campaign_id}?tab=contacts")
    raw = request.form.get("contacts_csv", "")
    csv_file = request.files.get("csv_file")
    if csv_file and csv_file.filename:
        raw = csv_file.read().decode("utf-8-sig")

    contacts = []
    for line in raw.strip().splitlines():
        if line.lower().startswith("name,") or line.lower().startswith("name\t"):
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2 and "@" in parts[1]:
            contacts.append({
                "name": parts[0],
                "email": parts[1],
                "company": parts[2] if len(parts) > 2 else "",
                "role": parts[3] if len(parts) > 3 else "",
                "language": parts[4] if len(parts) > 4 else "en",
            })
    if contacts:
        count = add_contacts(campaign_id, contacts)
        flash(("success", f"Added {count} contact{'s' if count != 1 else ''}."))
    else:
        flash(("error", "No valid contacts found. Format: name, email, company, role, language"))
    return redirect(f"/campaign/{campaign_id}?tab=contacts")


@app.route("/api/contacts-book/list")
def api_contacts_book_list():
    if not _logged_in():
        return jsonify({"error": "Not logged in"}), 401
    try:
        from outreach.db import get_contacts as get_crm_contacts
        contacts = get_crm_contacts(session["client_id"], search=request.args.get("q", ""))
        return jsonify({"contacts": [
            {"id": c["id"], "name": c.get("name", ""), "email": c["email"],
             "company": c.get("company", ""), "role": c.get("role", ""),
             "tags": c.get("tags", "")}
            for c in contacts
        ]})
    except Exception as e:
        return jsonify({"error": str(e), "contacts": []}), 500


@app.route("/campaign/<int:campaign_id>/import-contacts", methods=["POST"])
def import_crm_contacts(campaign_id):
    if not _logged_in():
        return jsonify({"error": "Not logged in"}), 401
    data = request.get_json(silent=True) or {}
    contact_ids = data.get("contact_ids", [])
    if not contact_ids or not isinstance(contact_ids, list):
        return jsonify({"error": "No contacts selected"}), 400
    from outreach.db import get_contacts as get_crm_contacts
    crm_contacts = get_crm_contacts(session["client_id"])
    crm_map = {c["id"]: c for c in crm_contacts}
    to_add = []
    for cid in contact_ids:
        if not isinstance(cid, int):
            continue
        c = crm_map.get(cid)
        if c:
            to_add.append({
                "name": c.get("name", ""),
                "email": c["email"],
                "company": c.get("company", ""),
                "role": c.get("role", ""),
                "language": c.get("language", "") or "en",
            })
    if to_add:
        count = add_contacts(campaign_id, to_add)
        flash(("success", f"Imported {count} contact{'s' if count != 1 else ''} from Contacts Book."))
        return jsonify({"ok": True, "count": count})
    return jsonify({"error": "No valid contacts found"}), 400


@app.route("/api/campaign/<int:campaign_id>/preview/<int:seq_id>")
def api_preview_email(campaign_id, seq_id):
    if not _logged_in():
        return jsonify({"error": "Not logged in"}), 401
    contact_id = request.args.get("contact_id", type=int)
    sequences = get_sequences(campaign_id)
    seq = next((s for s in sequences if s["id"] == seq_id), None)
    if not seq:
        return jsonify({"error": "Sequence not found"}), 404
    if contact_id:
        contacts = get_campaign_contacts(campaign_id)
        contact = next((c for c in contacts if c["id"] == contact_id), None)
        if not contact:
            return jsonify({"error": "Contact not found"}), 404
        sample = {"name": contact["name"], "company": contact.get("company", ""),
                  "role": contact.get("role", "")}
    else:
        sample = {"name": "John", "company": "Acme Inc", "role": "CEO"}
    sender = get_client(session["client_id"]).get("name", SENDER_NAME)
    preview_subj = personalize_email(seq["subject_a"], sample, sender)
    preview_body = personalize_email(seq["body_a"], sample, sender)
    return jsonify({"subject": preview_subj, "body": preview_body,
                    "contact_name": sample["name"]})


@app.route("/campaign/<int:campaign_id>/schedule", methods=["POST"])
def campaign_schedule(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import update_campaign_schedule
    scheduled_start = request.form.get("scheduled_start", "").strip()
    update_campaign_schedule(campaign_id, scheduled_start)
    if scheduled_start:
        flash(("success", f"Campaign scheduled to start at {scheduled_start}."))
    else:
        flash(("success", "Schedule cleared. Campaign will send immediately when activated."))
    return redirect(f"/campaign/{campaign_id}")


@app.route("/campaign/<int:campaign_id>/status", methods=["POST"])
def campaign_status(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    action = request.form.get("action", "")
    msgs = {
        "activate": ("active", "Campaign activated! Emails are being sent now."),
        "pause": ("paused", "Campaign paused."),
        "complete": ("completed", "Campaign completed."),
    }
    if action in msgs:
        status, msg = msgs[action]
        update_campaign_status(campaign_id, status)
        if action == "activate":
            _trigger_campaign_send(campaign_id)
        flash(("success", msg))
    return redirect(f"/campaign/{campaign_id}")


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


@app.route("/api/campaign/<int:campaign_id>/live-stats")
def api_campaign_live_stats(campaign_id):
    """Live polling endpoint for campaign sending progress."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    contacts = get_campaign_contacts(campaign_id)
    pending = sum(1 for c in contacts if c["status"] == "pending")
    sent = sum(1 for c in contacts if c["status"] in ("sent", "opened"))
    replied = sum(1 for c in contacts if c["status"] == "replied")
    total = len(contacts)
    job = _campaign_sends.get(campaign_id, {})
    sending = job.get("status") == "sending"
    return jsonify({
        "pending": pending, "sent": sent, "replied": replied, "total": total,
        "sending": sending, "batch_sent": job.get("sent", 0), "batch_total": job.get("total", 0),
    })


@app.route("/campaign/<int:campaign_id>/activate", methods=["POST"])
def activate_campaign(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    update_campaign_status(campaign_id, "active")
    _trigger_campaign_send(campaign_id)
    flash(("success", "Campaign activated! Emails are being sent now."))
    return redirect(f"/campaign/{campaign_id}")


@app.route("/campaign/<int:campaign_id>/delete", methods=["POST"])
def campaign_delete(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    delete_campaign(campaign_id)
    flash(("success", "Campaign deleted."))
    return redirect(url_for("dashboard"))


@app.route("/campaign/<int:campaign_id>/duplicate", methods=["POST"])
def campaign_dup(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    new_id = duplicate_campaign(campaign_id, session["client_id"])
    if new_id:
        flash(("success", "Campaign duplicated as draft."))
        return redirect(f"/campaign/{new_id}")
    flash(("error", "Could not duplicate campaign."))
    return redirect(url_for("dashboard"))


@app.route("/campaign/<int:cid>/contact/<int:contact_id>/delete", methods=["POST"])
def contact_delete(cid, contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    delete_contact(contact_id)
    flash(("success", "Contact removed."))
    return redirect(f"/campaign/{cid}?tab=contacts")

@app.route("/track/open/<int:sent_email_id>")
def track_open(sent_email_id):
    from outreach.tracker import TRACKING_PIXEL, handle_open
    handle_open(sent_email_id)
    return Response(TRACKING_PIXEL, mimetype="image/gif")


@app.route("/unsubscribe/<int:contact_id>", methods=["GET", "POST"])
@csrf.exempt  # Unsubscribe must work without CSRF (external email clients)
def unsubscribe(contact_id):
    from outreach.db import get_db, _exec, _fetchone, add_suppression
    with get_db() as db:
        # Get the contact's email before updating
        contact = _fetchone(db, "SELECT email, campaign_id FROM contacts WHERE id = %s", (contact_id,))
        # Mark the campaign contact as unsubscribed
        _exec(db, "UPDATE contacts SET status = 'unsubscribed' WHERE id = %s", (contact_id,))
        # Also block in contacts_book (across all campaigns) if we know who they are
        if contact:
            email_addr = contact["email"]
            # Find the client_id via the campaign
            camp = _fetchone(db, "SELECT client_id FROM campaigns WHERE id = %s", (contact["campaign_id"],))
            if camp:
                # Mark all campaign contacts with this email as unsubscribed for this client
                _exec(db, """UPDATE contacts SET status = 'unsubscribed'
                    WHERE email = %s AND campaign_id IN (SELECT id FROM campaigns WHERE client_id = %s)
                    AND status != 'unsubscribed'""", (email_addr, camp["client_id"]))
                # Add to global suppression list
                add_suppression(camp["client_id"], email_addr, reason="unsubscribed", source="campaign_unsubscribe")
    # RFC 8058: POST = one-click unsubscribe (email client auto-sends)
    if request.method == "POST":
        return "", 200
    return render_template_string(LAYOUT, title="Unsubscribed", logged_in=False, messages=[], active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup("""
    <div style="text-align:center;padding:80px 24px;">
      <div style="font-size:48px;margin-bottom:16px;opacity:0.4;">&#9993;</div>
      <h1 style="font-size:22px;margin-bottom:8px;">You've been unsubscribed</h1>
      <p style="color:var(--text-secondary);font-size:14px;">You won't receive any more emails from this sender.</p>
    </div>
    """))


@app.route("/unsubscribe/g/<token>", methods=["GET", "POST"])
@csrf.exempt
def unsubscribe_global(token):
    """Global unsubscribe for non-campaign emails (group sends, scheduled)."""
    import hashlib
    from outreach.db import get_db, _fetchone, add_suppression
    # Token format: sha256(client_id:email:secret)
    # We look up by brute-checking — or store the token. For simplicity, decode from query params.
    email = request.args.get("e", "")
    cid = request.args.get("c", "")
    if email and cid:
        try:
            client_id = int(cid)
            expected = hashlib.sha256(f"{client_id}:{email}:{app.secret_key}".encode()).hexdigest()[:16]
            if token == expected:
                add_suppression(client_id, email, reason="unsubscribed", source="global_unsubscribe")
        except (ValueError, Exception):
            pass
    if request.method == "POST":
        return "", 200
    return render_template_string(LAYOUT, title="Unsubscribed", logged_in=False, messages=[], active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup("""
    <div style="text-align:center;padding:80px 24px;">
      <div style="font-size:48px;margin-bottom:16px;opacity:0.4;">&#9993;</div>
      <h1 style="font-size:22px;margin-bottom:8px;">You've been unsubscribed</h1>
      <p style="color:var(--text-secondary);font-size:14px;">You won't receive any more emails from this sender.</p>
    </div>
    """))

@app.route("/api/stats")
def api_global_stats():
    """Return global stats as JSON for live dashboard refresh."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    gstats = get_global_stats(session["client_id"])
    gstats["open_rate_fmt"] = f"{gstats['open_rate']:.0%}"
    gstats["reply_rate_fmt"] = f"{gstats['reply_rate']:.0%}"
    gstats["bounce_rate_fmt"] = f"{gstats['bounce_rate']:.0%}"
    return jsonify(gstats)


@app.route("/api/analytics/daily")
def api_analytics_daily():
    """Return daily time-series data for charts."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_daily_analytics
    days = request.args.get("days", 30, type=int)
    days = min(max(days, 7), 365)
    data = get_daily_analytics(session["client_id"], days)
    return jsonify(data)


@app.route("/api/campaign/<int:campaign_id>/stats")
def api_campaign_stats(campaign_id):
    """Return campaign stats as JSON for live refresh."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    stats = get_campaign_stats(campaign_id)
    stats["open_rate_fmt"] = f"{stats['open_rate']:.0%}"
    stats["reply_rate_fmt"] = f"{stats['reply_rate']:.0%}"
    stats["bounce_rate_fmt"] = f"{stats['bounce_rate']:.0%}"
    return jsonify(stats)


@app.route("/api/check-replies", methods=["POST"])
def api_check_replies():
    """Trigger an immediate reply check and return updated stats."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.reply_checker import check_replies
    try:
        n = check_replies()
        gstats = get_global_stats(session["client_id"])
        gstats["open_rate_fmt"] = f"{gstats['open_rate']:.0%}"
        gstats["reply_rate_fmt"] = f"{gstats['reply_rate']:.0%}"
        gstats["bounce_rate_fmt"] = f"{gstats['bounce_rate']:.0%}"
        return jsonify({"new_replies": n, "stats": gstats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reply-draft/<int:sent_email_id>", methods=["POST"])
def api_reply_draft(sent_email_id):
    """Generate an AI-powered reply draft for a replied email."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401

    ctx = get_reply_context(sent_email_id)
    if not ctx:
        return jsonify({"error": "Email not found or not replied"}), 404

    if not ctx.get("reply_body"):
        return jsonify({"error": "No reply body to respond to"}), 400

    try:
        draft = generate_reply_draft(
            original_subject=ctx["subject"],
            original_body=ctx["body"],
            reply_body=ctx["reply_body"],
            reply_sentiment=ctx.get("reply_sentiment", "neutral"),
            contact_name=ctx["contact_name"],
            contact_company=ctx["company"],
            sender_name=get_client(session["client_id"]).get("name", SENDER_NAME),
            business_context=ctx.get("business_type", ""),
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Routes — Smart Send Times
# ---------------------------------------------------------------------------

@app.route("/smart-times")
def smart_times():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_send_time_stats
    time_stats = get_send_time_stats(session["client_id"])
    insights = get_optimal_send_hour(time_stats)

    # Build the best hours visual
    hours_html = ""
    if insights["best_hours"]:
        for h, rate in insights["best_hours"]:
            bar_w = min(rate / max(insights["best_hours"][0][1], 1) * 100, 100)
            color = "var(--green)" if rate >= 50 else ("var(--blue)" if rate >= 25 else "var(--yellow)")
            hours_html += f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
              <div style="width:60px;font-size:14px;font-weight:700;text-align:right;color:var(--text);">{h}:00</div>
              <div style="flex:1;background:var(--border-light);border-radius:4px;height:28px;overflow:hidden;position:relative;">
                <div style="width:{bar_w}%;height:100%;background:{color};border-radius:4px;transition:width 0.5s;display:flex;align-items:center;padding-left:10px;">
                  <span style="font-size:12px;font-weight:700;color:#fff;">{rate}%</span>
                </div>
              </div>
            </div>"""
    else:
        hours_html = '<div class="empty" style="padding:20px;"><p>Not enough data yet. Send at least 3 emails per time slot.</p></div>'

    # Best days visual
    days_html = ""
    if insights["best_days"]:
        for d, rate in insights["best_days"]:
            bar_w = min(rate / max(insights["best_days"][0][1], 1) * 100, 100)
            color = "var(--green)" if rate >= 50 else ("var(--blue)" if rate >= 25 else "var(--yellow)")
            days_html += f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
              <div style="width:60px;font-size:14px;font-weight:700;text-align:right;color:var(--text);">{d}</div>
              <div style="flex:1;background:var(--border-light);border-radius:4px;height:28px;overflow:hidden;position:relative;">
                <div style="width:{bar_w}%;height:100%;background:{color};border-radius:4px;transition:width 0.5s;display:flex;align-items:center;padding-left:10px;">
                  <span style="font-size:12px;font-weight:700;color:#fff;">{rate}%</span>
                </div>
              </div>
            </div>"""

    # Heatmap (same as A/B tests page but more prominent)
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    time_grid = {}
    max_rate = 0
    for r in time_stats:
        key = (int(r["dow"]), int(r["hour"]))
        rate = (r["opens"] / r["total"] * 100) if r["total"] else 0
        time_grid[key] = {"total": r["total"], "opens": r["opens"], "rate": rate}
        if rate > max_rate:
            max_rate = rate

    heatmap_rows = ""
    for dow in range(7):
        cells = ""
        for hour in range(6, 22):
            data = time_grid.get((dow, hour), {"total": 0, "rate": 0})
            if data["total"] == 0:
                bg = "var(--border-light)"
                text_col = "var(--text-muted)"
            else:
                intensity = min(data["rate"] / max(max_rate, 1), 1.0)
                if intensity > 0.7:
                    bg = "var(--green)"
                    text_col = "#fff"
                elif intensity > 0.4:
                    bg = "var(--green-light)"
                    text_col = "var(--green-dark)"
                elif intensity > 0.1:
                    bg = "var(--blue-light)"
                    text_col = "var(--blue)"
                else:
                    bg = "var(--border-light)"
                    text_col = "var(--text-muted)"
            cells += f'<td style="width:36px;height:32px;text-align:center;font-size:10px;background:{bg};color:{text_col};border:2px solid var(--card);border-radius:4px;" title="{day_names[dow]} {hour}:00 — {data["total"]} sent, {data["rate"]:.0f}% opens">{data["rate"]:.0f}%</td>'
        heatmap_rows += f"<tr><td style='font-size:12px;font-weight:600;padding-right:10px;color:var(--text-secondary);'>{day_names[dow]}</td>{cells}</tr>"

    hour_headers = "".join(f'<th style="font-size:10px;color:var(--text-muted);font-weight:500;padding:2px 0;">{h}</th>' for h in range(6, 22))

    return _render(t("smart.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("smart.title")}</div>
    <div class="page-header">
      <h1>&#9201; {t("smart.title")}</h1>
      <p class="subtitle">AI-analyzed optimal sending windows based on your open rate data.</p>
    </div>

    <div class="card" style="background:linear-gradient(135deg, var(--primary-light) 0%, #F0F0FF 100%);border:1px solid var(--primary);margin-bottom:20px;">
      <div style="display:flex;align-items:center;gap:12px;">
        <div style="font-size:32px;">&#129302;</div>
        <div>
          <div style="font-weight:700;font-size:15px;color:var(--primary-dark);">AI Recommendation</div>
          <div style="font-size:14px;color:var(--text);margin-top:2px;">{_esc(insights['recommendation'])}</div>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;">
      <div class="card">
        <div class="card-header"><h2>&#128200; Best Hours to Send</h2></div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Ranked by open rate. Only hours with 3+ emails shown.</p>
        {hours_html}
      </div>
      <div class="card">
        <div class="card-header"><h2>&#128197; Best Days to Send</h2></div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Ranked by open rate across all campaigns.</p>
        {days_html}
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>&#128345; Open Rate Heatmap</h2></div>
      <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Green = high open rates. Gray = no data or low volume.</p>
      <div style="overflow-x:auto;">
        <table style="border-collapse:separate;border-spacing:2px;margin:0;">
          <thead><tr><th></th>{hour_headers}</tr></thead>
          <tbody>{heatmap_rows}</tbody>
        </table>
      </div>
    </div>
    """, active_page="smart_times")


# ---------------------------------------------------------------------------
# Routes — Analytics Export
# ---------------------------------------------------------------------------

@app.route("/export")
def export_page():
    if not _logged_in():
        return redirect(url_for("login"))

    campaigns = get_campaigns(session["client_id"])
    options = '<option value="">All Campaigns</option>'
    for c in campaigns:
        options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

    return _render(t("export.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("export.title")}</div>
    <div class="page-header">
      <h1>&#128202; {t("export.title")}</h1>
      <p class="subtitle">Download campaign data as CSV for reporting and analysis.</p>
    </div>

    <div class="card">
      <div class="card-header"><h2>Download Report</h2></div>
      <form method="get" action="/export/csv" style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;">
        <div class="form-group" style="flex:1;min-width:200px;">
          <label>Campaign</label>
          <select name="campaign_id" style="margin-bottom:0;">{options}</select>
        </div>
        <button class="btn btn-primary" type="submit" style="margin-bottom:14px;">&#11015; Download CSV</button>
      </form>
      <p class="form-hint" style="margin-top:8px;">Includes: contact info, email status, open/reply times, reply sentiment, and reply content.</p>
    </div>

    <div class="card" style="margin-top:16px;">
      <div class="card-header"><h2>Quick Stats Summary</h2></div>
      <div id="summary-stats">Loading...</div>
    </div>

    <script>
      fetch('/api/stats')
        .then(r => r.json())
        .then(d => {{
          if (d.error) return;
          document.getElementById('summary-stats').innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));gap:12px;">
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--blue);">${{d.total_sent}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Emails Sent</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--green);">${{d.total_opened}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Opens (${{d.open_rate_fmt}})</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--primary);">${{d.total_replied}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Replies (${{d.reply_rate_fmt}})</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--red);">${{d.total_bounced}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Bounced (${{d.bounce_rate_fmt}})</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--yellow);">${{d.total_contacts}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Total Contacts</div>
              </div>
            </div>`;
        }});
    </script>
    """, active_page="export")


@app.route("/export/csv")
def export_csv():
    if not _logged_in():
        return redirect(url_for("login"))

    import csv
    import io

    campaign_id = request.args.get("campaign_id", type=int)
    data = get_export_data(session["client_id"], campaign_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Campaign", "Contact Name", "Contact Email", "Company", "Role",
        "Contact Status", "Step", "Subject", "Variant", "Email Status",
        "Sent At", "Opened At", "Replied At", "Reply Sentiment", "Reply Body",
    ])
    for row in data:
        writer.writerow([
            row.get("campaign_name", ""),
            row.get("contact_name", ""),
            row.get("contact_email", ""),
            row.get("company", ""),
            row.get("role", ""),
            row.get("contact_status", ""),
            row.get("step", ""),
            row.get("subject", ""),
            row.get("variant", ""),
            row.get("email_status", ""),
            row.get("sent_at", ""),
            row.get("opened_at", ""),
            row.get("replied_at", ""),
            row.get("reply_sentiment", ""),
            (row.get("reply_body", "") or "")[:500],
        ])

    csv_content = output.getvalue()
    filename = "outreach-export"
    if campaign_id:
        camp = get_campaign(campaign_id)
        if camp:
            safe_name = "".join(c for c in camp["name"] if c.isalnum() or c in " -_").strip()
            filename = f"outreach-{safe_name}"
    filename += ".csv"

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@app.route("/mail-hub")
def mail_hub():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_mail_inbox, get_mail_stats, search_mail_inbox, get_scheduled_emails, get_email_accounts, get_subscription, get_top_senders
    data_cid = _effective_client_id()
    filter_by = request.args.get("filter", "unread")
    category = request.args.get("category")
    search_q = request.args.get("q", "").strip()
    account_filter = request.args.get("account")
    account_id = int(account_filter) if account_filter and account_filter.isdigit() else None
    sender_filter = request.args.get("sender", "").strip().lower()

    stats = get_mail_stats(data_cid)
    accounts = get_email_accounts(data_cid)
    top_senders = get_top_senders(data_cid)
    sub = get_subscription(data_cid)
    user_plan = sub.get("plan", "free") if sub else "free"
    is_paid = user_plan in ("growth", "pro", "unlimited")
    # Build account lookup for labels/colors
    acct_map = {a["id"]: a for a in accounts}

    if search_q:
        emails = search_mail_inbox(data_cid, search_q)
    else:
        emails = get_mail_inbox(data_cid, filter_by=filter_by, category=category, account_id=account_id, sender=sender_filter or None)

    scheduled = get_scheduled_emails(data_cid, status="pending")
    sched_count = len(scheduled)

    # Priority colors/icons
    pri_config = {
        "urgent": ("&#128308;", "var(--red)", "badge-red"),
        "important": ("&#128992;", "var(--yellow)", "badge-yellow"),
        "normal": ("&#128309;", "var(--blue)", "badge-blue"),
        "low": ("&#11035;", "var(--text-muted)", "badge-gray"),
    }
    cat_config = {
        "action_required": ("&#9889;", "Action Required"),
        "meeting": ("&#128197;", "Meeting"),
        "fyi": ("&#128196;", "FYI"),
        "newsletter": ("&#128240;", "Newsletter"),
        "personal": ("&#128100;", "Personal"),
        "spam": ("&#128681;", "Spam"),
        "uncategorized": ("&#128233;", "Uncategorized"),
    }

    # Build email rows
    email_rows = ""
    acct_colors = ["#6366F1", "#EC4899", "#14B8A6", "#F59E0B", "#8B5CF6", "#EF4444", "#06B6D4", "#84CC16"]
    for e in emails:
        pi, pc, pb = pri_config.get(e["priority"], ("&#128309;", "var(--blue)", "badge-blue"))
        ci, cl = cat_config.get(e["category"], ("&#128233;", e["category"]))
        star_cls = "color:var(--yellow);" if e["is_starred"] else "color:var(--text-muted);opacity:0.3;"
        read_weight = "400" if e["is_read"] else "600"
        read_bg = "" if e["is_read"] else "background:var(--primary-light);"
        # Account badge
        acct_badge = ""
        if e.get("account_id") and e["account_id"] in acct_map:
            a = acct_map[e["account_id"]]
            acct_idx = list(acct_map.keys()).index(e["account_id"]) % len(acct_colors)
            acct_badge = f' <span class="badge" style="font-size:10px;background:{acct_colors[acct_idx]}20;color:{acct_colors[acct_idx]};border:1px solid {acct_colors[acct_idx]}40;">{_esc(a["label"] or a["email"].split("@")[0])}</span>'
        from_display = _esc(e["from_name"]) if e["from_name"] else _esc(e["from_email"])
        time_str = e["received_at"][:16] if e["received_at"] else ""
        summary = f'<div style="font-size:13px;color:var(--primary);margin-top:4px;font-style:italic;">{_esc(e["ai_summary"])}</div>' if e.get("ai_summary") else ""

        # Snooze badge: active vs resurfaced with reminder
        snooze_badge = ""
        if e.get("snooze_until"):
            from datetime import datetime
            try:
                snooze_dt = datetime.strptime(e["snooze_until"][:19], "%Y-%m-%d %H:%M:%S")
                if snooze_dt > datetime.now():
                    snooze_badge = f' <span class="badge badge-yellow" style="font-size:11px;">&#128340; Snoozed until {e["snooze_until"][:16]}</span>'
                else:
                    snooze_badge = ' <span class="badge badge-purple" style="font-size:11px;">&#128276; Resurfaced</span>'
            except Exception:
                snooze_badge = ' <span class="badge badge-yellow" style="font-size:11px;">&#128340; Snoozed</span>'
        snooze_note_badge = ""
        if e.get("snooze_note"):
            snooze_note_badge = f' <span class="badge badge-blue" style="font-size:11px;" title="{_esc(e["snooze_note"])}">&#128221; {_esc(e["snooze_note"][:30])}</span>'

        email_rows += f"""
        <tr style="{read_bg}cursor:pointer;" data-mail-id="{e['id']}" onclick="if(event.ctrlKey){{handleShiftClick(this,event);return;}} if(!event.target.closest('button,span[onclick],input[type=checkbox],label'))window.location='/mail-hub/{e['id']}'">
          <td style="width:36px;text-align:center;padding:12px 6px;">
            <input type="checkbox" class="mail-checkbox" value="{e['id']}" onclick="event.stopPropagation();handleCheckboxClick(this,event);" style="width:16px;height:16px;cursor:pointer;accent-color:var(--primary);">
          </td>
          <td style="width:26px;text-align:center;padding:12px 6px;">
            <span style="cursor:pointer;font-size:20px;{star_cls}" onclick="toggleStar({e['id']}, this)">&#9733;</span>
          </td>
          <td style="width:26px;text-align:center;font-size:14px;padding:12px 4px;" title="{e['priority']}">{pi}</td>
          <td style="width:180px;padding:12px 8px;">
            <div style="font-weight:{read_weight};font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px;">{from_display}</div>
            <div style="font-size:12px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px;">{_esc(e['from_email'])}</div>
          </td>
          <td style="padding:12px 8px;">
            <div style="font-weight:{read_weight};font-size:15px;">{_esc(e['subject'] or '(no subject)')}</div>
            <div style="font-size:13px;color:var(--text-secondary);max-height:20px;overflow:hidden;margin-top:2px;">{_esc(e['body_preview'][:120])}</div>
            <div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap;">
              <span class="badge {pb}" style="font-size:11px;">{e['priority']}</span>
              <span class="badge badge-gray" style="font-size:11px;">{ci} {cl}</span>
              {acct_badge}{snooze_badge}{snooze_note_badge}
            </div>
            {summary}
          </td>
          <td style="width:140px;padding:12px 8px;text-align:right;vertical-align:top;">
            <div style="font-size:12px;color:var(--text-muted);white-space:nowrap;margin-bottom:6px;">{time_str}</div>
            <div style="display:flex;gap:4px;justify-content:flex-end;">
              <button class="btn btn-ghost btn-sm" onclick="markRead({e['id']}, this)" title="{'Mark unread' if e['is_read'] else 'Mark read'}" style="font-size:14px;padding:4px 8px;">{'&#128065;' if not e['is_read'] else '&#9898;'}</button>
              <button class="btn btn-ghost btn-sm" onclick="openSnoozeModal({e['id']})" title="Snooze / Remind" style="font-size:14px;padding:4px 8px;">&#128340;</button>
              <button class="btn btn-ghost btn-sm" onclick="archiveEmail({e['id']}, this)" title="Archive" style="font-size:14px;padding:4px 8px;">&#128230;</button>
            </div>
          </td>
        </tr>"""

    if not email_rows:
        email_rows = f"""<tr><td colspan="6">
          <div class="empty" style="padding:40px;">
            <div class="empty-icon">&#128233;</div>
            <h3>{'No emails match "' + _esc(search_q) + '"' if search_q else ('No emails match this filter' if filter_by != 'all' or category else 'Inbox empty')}</h3>
            <p>{'Try different search terms.' if search_q else ('Try a different filter or sync your inbox.' if filter_by != 'all' or category else 'Click "Sync Inbox" to fetch your latest emails.')}</p>
          </div>
        </td></tr>"""

    # Category sidebar items
    cat_sidebar = ""
    for cat_key, (cat_icon, cat_label) in cat_config.items():
        cnt = stats["categories"].get(cat_key, 0)
        if cnt == 0 and cat_key == "uncategorized":
            continue
        active = "background:var(--primary-light);color:var(--primary);font-weight:600;" if category == cat_key else ""
        cat_sidebar += f'<a href="?category={cat_key}" style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:8px;text-decoration:none;font-size:14px;color:var(--text-secondary);{active}"><span style="font-size:16px;">{cat_icon}</span> {cat_label} <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{cnt}</span></a>'

    # Sender sidebar items
    sender_sidebar = ""
    for s in top_senders:
        s_email = s["email"].lower()
        s_name = _esc(s["name"])
        s_active = "background:var(--primary-light);color:var(--primary);font-weight:600;" if sender_filter == s_email else ""
        sender_sidebar += f'<a href="?sender={_esc(s_email)}" style="display:flex;align-items:center;gap:10px;padding:8px 14px;border-radius:8px;text-decoration:none;font-size:13px;color:var(--text-secondary);{s_active}" title="{_esc(s_email)}"><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px;">{s_name}</span> <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{s["count"]}</span></a>'

    # Build scheduled emails section
    sched_html = ""
    for s in scheduled:
        sched_html += f'<div style="padding:8px 0;border-bottom:1px solid var(--border-light);font-size:13px;display:flex;align-items:center;gap:6px;"><div style="flex:1;min-width:0;"><div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_esc(s["to_email"])}</div><div style="color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_esc(s["subject"][:40])}</div><div style="color:var(--primary);font-size:12px;" class="sched-utc" data-utc="{s["scheduled_at"][:19]}">&#128340; {s["scheduled_at"][:16]}</div></div><button onclick="cancelScheduled({s["id"]})" style="background:none;border:none;cursor:pointer;color:var(--red);font-size:14px;padding:4px;flex-shrink:0;" title="Cancel this email">&#10005;</button></div>'

    # Show tutorial if user has no email accounts (first time visiting Mail Hub)
    mail_hub_tutorial = ""
    if not accounts:
        mail_hub_tutorial = f"""
    <div class="card" style="margin-bottom:24px;border:2px solid var(--primary);background:linear-gradient(135deg, var(--primary-light), var(--bg));">
      <div style="padding:24px;">
        <h2 style="margin:0 0 8px;">&#128231; {t("mail.title")} — Getting Started</h2>
        <p style="color:var(--text-secondary);margin-bottom:20px;">Follow these steps to set up your Mail Hub inbox:</p>
        <div style="display:flex;flex-direction:column;gap:16px;">
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">1</span>
            <div>
              <strong>Connect your email account</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">Go to <a href="/settings" style="color:var(--primary);font-weight:600;">Settings</a> and click "+ Add Email Account". You'll need a Gmail <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:var(--primary);">App Password</a> (not your regular password).</p>
            </div>
          </div>
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">2</span>
            <div>
              <strong>Enable 2-Step Verification first</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">App Passwords require 2FA. Go to <a href="https://myaccount.google.com/security" target="_blank" style="color:var(--primary);">Google Security</a> → enable 2-Step Verification → then create an App Password.</p>
            </div>
          </div>
          <div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="background:var(--primary);color:white;border-radius:50%;min-width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">3</span>
            <div>
              <strong>Sync your inbox</strong>
              <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">Once connected, come back here and click "Sync" to pull in your emails. AI will automatically classify and prioritize them.</p>
            </div>
          </div>
        </div>
        <div style="margin-top:20px;">
          <a href="/settings" class="btn btn-primary">&#9881; Go to Settings</a>
        </div>
      </div>
    </div>
    """

    # Show preferences popup if user has accounts but no mail preferences set yet
    from outreach.db import get_mail_preferences
    user_prefs = get_mail_preferences(session["client_id"])
    prefs_popup = ""
    if accounts and not user_prefs:
        prefs_popup = """
    <div id="prefs-modal" style="display:flex;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000;align-items:center;justify-content:center;">
      <div class="card" style="max-width:560px;width:90%;max-height:90vh;overflow-y:auto;margin:0;">
        <div style="padding:28px;">
          <h2 style="margin:0 0 6px;">&#127919; What matters most to you?</h2>
          <p style="color:var(--text-secondary);margin-bottom:20px;font-size:14px;">Help our AI prioritize your inbox better. Select the topics that are important to you and add any custom priorities.</p>

          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;" id="pref-chips">
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Client emails" style="display:none;"><span>&#128188; Client emails</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Sales leads & prospects" style="display:none;"><span>&#128176; Sales leads & prospects</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Meeting invites & scheduling" style="display:none;"><span>&#128197; Meeting invites & scheduling</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Urgent deadlines" style="display:none;"><span>&#9200; Urgent deadlines</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Team & coworker messages" style="display:none;"><span>&#129309; Team & coworker messages</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Financial & billing" style="display:none;"><span>&#128179; Financial & billing</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Support tickets & customer issues" style="display:none;"><span>&#127384; Support tickets & customer issues</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Job applications & recruiting" style="display:none;"><span>&#128188; Job applications & recruiting</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Legal & contracts" style="display:none;"><span>&#128220; Legal & contracts</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:20px;border:2px solid var(--border);cursor:pointer;font-size:13px;transition:all 0.2s;" class="pref-chip">
              <input type="checkbox" value="Shipping & orders" style="display:none;"><span>&#128230; Shipping & orders</span>
            </label>
          </div>

          <div class="form-group" style="margin-bottom:20px;">
            <label style="font-size:13px;font-weight:600;">Custom priorities (optional)</label>
            <textarea id="pref-custom" placeholder="e.g. Emails from @bigclient.com are always urgent, partnership proposals are important..." style="width:100%;min-height:70px;font-size:13px;"></textarea>
          </div>

          <div style="display:flex;gap:8px;">
            <button class="btn btn-primary" onclick="savePreferences()">&#10003; Save Preferences</button>
            <button class="btn btn-ghost" onclick="document.getElementById('prefs-modal').style.display='none'">Skip for now</button>
          </div>
        </div>
      </div>
    </div>

    <style>
      .pref-chip:has(input:checked) { border-color: var(--primary); background: var(--primary-light); color: var(--primary); font-weight: 600; }
    </style>

    <script>
    function savePreferences() {
      const chips = document.querySelectorAll('#pref-chips input:checked');
      const selected = Array.from(chips).map(c => c.value);
      const custom = document.getElementById('pref-custom').value.trim();
      const prefs = selected.join(', ') + (custom ? '. ' + custom : '');
      if (!prefs.trim()) { alert('Please select at least one priority or add a custom one.'); return; }
      fetch('/api/mail-preferences', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({preferences: prefs})
      }).then(r => r.json()).then(data => {
        if (data.ok) {
          document.getElementById('prefs-modal').style.display = 'none';
        }
      });
    }
    </script>
    """

    return _render(t("mail.title"), f"""
    {mail_hub_tutorial}
    {prefs_popup}
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / {t("mail.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;">
      <div>
        <h1 style="font-size:30px;">&#128233; {t("mail.title")}</h1>
        <p class="subtitle" style="font-size:16px;">AI-powered inbox triage — your emails organized by what matters. <span style="font-size:13px;color:var(--text-muted);cursor:pointer;" onclick="document.getElementById('shortcuts-modal').style.display='flex'">Press <kbd style="background:var(--border-light);padding:1px 6px;border-radius:4px;font-size:12px;border:1px solid var(--border);">?</kbd> for shortcuts</span></p>
      </div>
      <div class="btn-group" style="display:flex;align-items:center;gap:12px;">
        <span id="peek-badge" style="display:none;font-size:13px;font-weight:600;color:var(--primary);background:var(--primary-light,rgba(99,102,241,0.1));padding:6px 14px;border-radius:var(--radius-xs);white-space:nowrap;"></span>
        <button onclick="openComposeModal()" class="btn btn-green" style="font-size:15px;padding:10px 22px;">&#9997; Compose</button>
        <button onclick="syncInbox()" class="btn btn-primary" id="sync-btn" style="font-size:15px;padding:10px 22px;">&#128260; Sync Inbox</button>
      </div>
    </div>

    <!-- Search bar -->
    <div class="card" style="padding:12px 16px;margin-bottom:20px;display:flex;align-items:center;gap:12px;">
      <form method="GET" style="display:flex;align-items:center;gap:10px;flex:1;">
        <span style="font-size:18px;color:var(--text-muted);">&#128269;</span>
        <input type="text" name="q" id="mail-search-input" value="{_esc(search_q)}" placeholder="Search emails by subject, sender, body... (press / to focus)" style="flex:1;font-size:14px;padding:10px 14px;border:1px solid var(--border-light);border-radius:var(--radius-xs);margin-bottom:0;">
        <button type="submit" class="btn btn-primary btn-sm" style="font-size:13px;">Search</button>
        {'<a href="/mail-hub" class="btn btn-ghost btn-sm">Clear</a>' if search_q else ''}
      </form>
    </div>

    {"" if len(accounts) < 2 else '<div style="margin-bottom:16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;"><span style=' + '"' + 'font-size:13px;font-weight:600;color:var(--text-muted);' + '"' + '>Mailbox:</span><a href="/mail-hub" class="badge ' + ('badge-blue' if not account_id else 'badge-gray') + '" style="font-size:12px;text-decoration:none;padding:6px 12px;cursor:pointer;">All</a>' + ''.join(f'<a href="?account={a["id"]}" class="badge {("badge-blue" if account_id == a["id"] else "badge-gray")}" style="font-size:12px;text-decoration:none;padding:6px 12px;cursor:pointer;">{_esc(a["label"] or a["email"].split("@")[0])}</a>' for a in accounts) + '</div>'}

    <div class="stats-grid" style="margin-bottom:24px;">
      <div class="stat-card stat-blue" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['total']}</div><div class="label" style="font-size:12px;">Total</div></div>
      <div class="stat-card stat-red" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['unread']}</div><div class="label" style="font-size:12px;">Unread</div></div>
      <div class="stat-card stat-yellow" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['urgent']}</div><div class="label" style="font-size:12px;">Urgent</div></div>
      <div class="stat-card stat-purple" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['starred']}</div><div class="label" style="font-size:12px;">Starred</div></div>
      <div class="stat-card stat-green" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['snoozed']}</div><div class="label" style="font-size:12px;">Snoozed</div></div>
    </div>

    <!-- Bulk action bar (hidden until checkboxes selected) -->
    <div id="bulk-bar" style="display:none;position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--card);border:2px solid var(--primary);border-radius:var(--radius);padding:12px 24px;box-shadow:var(--shadow-lg);z-index:150;display:none;align-items:center;gap:12px;">
      <span id="bulk-count" style="font-weight:700;font-size:14px;color:var(--primary);">0 selected</span>
      <div style="width:1px;height:24px;background:var(--border);"></div>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_read', 1)">&#128065; Mark Read</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_read', 0)">&#9898; Mark Unread</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_starred', 1)">&#9733; Star</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_archived', 1)" style="color:var(--red);">&#128230; Archive</button>
      <button class="btn btn-ghost btn-sm" onclick="clearSelection()">&#10005; Clear</button>
    </div>

    <div style="display:grid;grid-template-columns:240px 1fr;gap:24px;">
      <!-- Sidebar -->
      <div>
        <div class="card" style="padding:18px;position:sticky;top:76px;max-height:calc(100vh - 96px);overflow-y:auto;">
          <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">Filters</div>
          <a href="?filter=all" class="{'btn btn-primary' if filter_by == 'all' and not category and not search_q else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128233; All ({stats['unread']})</a>
          <a href="?filter=unread" class="{'btn btn-primary' if filter_by == 'unread' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128308; Unread ({stats['unread']})</a>
          <a href="?filter=read" class="{'btn btn-primary' if filter_by == 'read' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128065; Read ({stats['read']})</a>
          <a href="?filter=starred" class="{'btn btn-primary' if filter_by == 'starred' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#9733; Starred ({stats['starred']})</a>
          <a href="?filter=urgent" class="{'btn btn-primary' if filter_by == 'urgent' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128680; Urgent ({stats['urgent']})</a>
          <a href="?filter=snoozed" class="{'btn btn-primary' if filter_by == 'snoozed' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128340; Snoozed ({stats['snoozed']})</a>

          <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">Categories</div>
          {cat_sidebar}

          {'<div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">&#128101; Saved Contacts</div>' + sender_sidebar + ('<a href="/mail-hub" style="display:block;text-align:center;font-size:12px;color:var(--primary);margin-top:4px;text-decoration:none;">Clear filter</a>' if sender_filter else '') if top_senders else ''}

          {'<div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">&#128340; Scheduled (' + str(sched_count) + ')</div>' + sched_html if sched_count > 0 else ''}
        </div>
      </div>

      <!-- Email list -->
      <div class="card" style="padding:0;overflow:hidden;">
        <table style="margin:0;table-layout:fixed;width:100%;">
          <thead>
            <tr style="background:var(--bg);">
              <th style="width:36px;"><input type="checkbox" id="select-all" onchange="toggleSelectAll(this)" style="width:16px;height:16px;cursor:pointer;accent-color:var(--primary);"></th>
              <th style="width:26px;"></th>
              <th style="width:26px;"></th>
              <th style="width:180px;font-size:13px;">From</th>
              <th style="font-size:13px;">Subject / Tags</th>
              <th style="width:140px;text-align:right;font-size:13px;">Date / Actions</th>
            </tr>
          </thead>
          <tbody>{email_rows}</tbody>
        </table>
      </div>
    </div>

    <!-- Snooze modal -->
    <div id="snooze-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:460px;max-width:90vw;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#128340; Snooze & Remind</h2>
        <input type="hidden" id="snooze-mail-id">
        <div style="font-size:13px;color:var(--text-muted);margin-bottom:12px;">Choose when this email should resurface as <strong style="color:var(--yellow);">important</strong>:</div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px;">
          <button class="btn btn-outline" onclick="snoozePreset('today')" style="justify-content:center;padding:12px;">&#127769; Later Today<br><span style="font-size:11px;color:var(--text-muted);">6:00 PM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('tomorrow')" style="justify-content:center;padding:12px;">&#9728; Tomorrow AM<br><span style="font-size:11px;color:var(--text-muted);">9:00 AM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('tomorrow-eve')" style="justify-content:center;padding:12px;">&#127769; Tomorrow PM<br><span style="font-size:11px;color:var(--text-muted);">6:00 PM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('next-monday')" style="justify-content:center;padding:12px;">&#128197; Next Monday<br><span style="font-size:11px;color:var(--text-muted);">9:00 AM</span></button>
        </div>

        <div class="form-group">
          <label>Custom Date & Time</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <input type="date" id="snooze-date" style="margin-bottom:0;">
            <input type="time" id="snooze-time" value="09:00" style="margin-bottom:0;">
          </div>
        </div>

        <div class="form-group" style="margin-top:12px;">
          <label>&#128221; Reminder Note <span style="font-weight:400;text-transform:none;color:var(--text-muted);">(optional)</span></label>
          <textarea id="snooze-note" rows="2" placeholder="e.g. Follow up on proposal, Check contract status..." style="font-size:14px;resize:vertical;"></textarea>
        </div>

        <div style="display:flex;gap:8px;margin-top:16px;">
          <button class="btn btn-primary" onclick="submitSnooze()" style="flex:1;font-size:15px;">&#128340; Set Reminder</button>
          <button class="btn btn-ghost" onclick="document.getElementById('snooze-modal').style.display='none'" style="font-size:15px;">Cancel</button>
        </div>
      </div>
    </div>

    <!-- Compose modal -->
    <div id="compose-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:600px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#9997; Compose Email</h2>

        {"" if len(accounts) < 2 else '<div class="form-group"><label>From</label><select id="compose-account" style="font-size:14px;">' + ''.join(f'<option value="{a["id"]}" {"selected" if a["is_default"] else ""}>{_esc(a["label"] or a["email"])} ({_esc(a["email"])})</option>' for a in accounts) + '</select></div>'}

        <div class="form-group">
          <label>To</label>
          <input type="email" id="compose-to" placeholder="recipient@example.com" style="font-size:14px;">
        </div>
        <div class="form-group">
          <label>Subject</label>
          <input type="text" id="compose-subject" placeholder="Email subject..." style="font-size:14px;">
        </div>
        <div class="form-group">
          <label>Message</label>
          <textarea id="compose-body" rows="10" placeholder="Write your email..." style="font-size:14px;line-height:1.6;font-family:inherit;resize:vertical;"></textarea>
        </div>

        <div class="form-group">
          <label style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--text-muted);">
            &#128206; Attachments
          </label>
          <input type="file" id="compose-attachments" multiple style="font-size:13px;margin-top:4px;">
          <div id="compose-file-list" style="font-size:12px;color:var(--text-muted);margin-top:4px;"></div>
        </div>

        <div id="schedule-section" style="display:none;margin-bottom:14px;">
          <label>&#128197; Schedule for</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <input type="date" id="compose-date" style="margin-bottom:0;font-size:14px;">
            <input type="time" id="compose-time" value="09:00" style="margin-bottom:0;font-size:14px;">
          </div>
        </div>

        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn btn-primary" onclick="sendCompose('now')" id="compose-send-btn" style="flex:1;font-size:15px;">&#9993; Send Now</button>
          <button class="btn btn-yellow" onclick="toggleSchedule()" id="compose-schedule-toggle" style="font-size:15px;">&#128340; Schedule</button>
          <button class="btn btn-ghost" onclick="document.getElementById('compose-modal').style.display='none'" style="font-size:15px;">Cancel</button>
        </div>
        <div id="compose-status" style="margin-top:10px;font-size:13px;text-align:center;"></div>
      </div>
    </div>

    <!-- Keyboard shortcuts modal -->
    <div id="shortcuts-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:420px;max-width:90vw;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#9000; Keyboard Shortcuts</h2>
        <table style="width:100%;">
          <tbody>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">j</kbd> / <kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">k</kbd></td><td style="padding:6px 0;font-size:14px;">Navigate up/down</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">o</kbd> / <kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">Enter</kbd></td><td style="padding:6px 0;font-size:14px;">Open email</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">x</kbd></td><td style="padding:6px 0;font-size:14px;">Select/deselect</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">s</kbd></td><td style="padding:6px 0;font-size:14px;">Toggle star</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">e</kbd></td><td style="padding:6px 0;font-size:14px;">Archive</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">/</kbd></td><td style="padding:6px 0;font-size:14px;">Focus search</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">?</kbd></td><td style="padding:6px 0;font-size:14px;">Show this help</td></tr>
          </tbody>
        </table>
        <button class="btn btn-ghost" onclick="document.getElementById('shortcuts-modal').style.display='none'" style="width:100%;margin-top:16px;font-size:14px;">Close</button>
      </div>
    </div>

    <script>
    // --- Sync with progress polling ---
    var _syncPoll = null;
    function syncInbox() {{
      var btn = document.getElementById('sync-btn');
      btn.innerHTML = '&#8987; Syncing...';
      btn.disabled = true;
      // Show skeleton loading indicator
      var emailList = document.querySelector('.mail-list-body, table tbody');
      if (emailList && emailList.children.length === 0) {{
        emailList.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:40px;"><div class="sync-spinner"></div><p style="color:var(--text-muted);font-size:13px;margin-top:12px;">Fetching &amp; classifying emails...</p></td></tr>';
      }}
      fetch('/api/mail-hub/sync', {{method: 'POST'}})
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
          if (data.error) {{
            btn.innerHTML = '&#9888; ' + data.error;
            setTimeout(function() {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 3000);
            return;
          }}
          // Start polling
          _syncPoll = setInterval(function() {{
            fetch('/api/mail-hub/sync-status')
              .then(function(r) {{ return r.json(); }})
              .then(function(s) {{
                if (s.status === 'done') {{
                  clearInterval(_syncPoll);
                  peekAutoSynced = false;  // re-arm so next peek can trigger again
                  if (s.new_emails > 0) {{
                    btn.innerHTML = '&#9989; ' + s.new_emails + ' new! Refreshing...';
                    showToast(s.new_emails + ' new email(s) synced &amp; classified.', 'success');
                    setTimeout(function() {{ location.reload(); }}, 800);
                  }} else {{
                    btn.innerHTML = '&#10003; Already up to date';
                    setTimeout(function() {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 2000);
                  }}
                }} else if (s.status === 'error') {{
                  clearInterval(_syncPoll);
                  peekAutoSynced = false;
                  btn.innerHTML = '&#9888; ' + (s.error || 'Sync failed');
                  setTimeout(function() {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 3000);
                }}
                // else status === 'syncing', keep polling
              }});
          }}, 1500);
        }})
        .catch(function() {{
          btn.innerHTML = '&#9888; Sync failed';
          setTimeout(function() {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 2000);
        }});
    }}

    // --- Peek for new mail (lightweight, no sync cost) ---
    const isPaid = {'true' if is_paid else 'false'};
    let peekAutoSynced = false;  // re-armed after each sync completes

    function peekInbox() {{
      fetch('/api/mail-hub/peek', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          const badge = document.getElementById('peek-badge');
          if (data.imap_error) {{
            badge.textContent = '&#9888; IMAP connection failed — check email account settings';
            badge.style.display = 'inline-block';
            badge.style.background = 'var(--red-light, #fee)';
            badge.style.color = 'var(--red, #e53e3e)';
          }} else if (data.unseen > 0) {{
            badge.textContent = data.unseen + ' new email' + (data.unseen === 1 ? '' : 's') + ' waiting';
            badge.style.display = 'inline-block';
            badge.style.background = '';
            badge.style.color = '';
            // Auto-sync immediately on every detection (re-armed after each sync)
            if (!peekAutoSynced) {{
              peekAutoSynced = true;
              syncInbox();
            }}
          }} else {{
            badge.style.display = 'none';
          }}
        }})
        .catch(() => {{}});
    }}

    // Peek immediately on page load, then every 20s for near-real-time delivery
    peekInbox();
    setInterval(peekInbox, 20000);

    // Convert scheduled email times from UTC to local
    document.querySelectorAll('.sched-utc').forEach(function(el) {{
      try {{
        const utc = el.getAttribute('data-utc');
        const d = new Date(utc.replace(' ', 'T') + 'Z');
        const local = d.getFullYear() + '-' +
          String(d.getMonth()+1).padStart(2,'0') + '-' +
          String(d.getDate()).padStart(2,'0') + ' ' +
          String(d.getHours()).padStart(2,'0') + ':' +
          String(d.getMinutes()).padStart(2,'0');
        el.innerHTML = '&#128340; ' + local;
      }} catch(e) {{}}
    }});

    // --- Cancel scheduled email ---
    function cancelScheduled(id) {{
      if (!confirm('Cancel this scheduled email?')) return;
      fetch('/api/mail-hub/scheduled/' + id + '/delete', {{method: 'POST'}})
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{ if (data.ok) location.reload(); }});
    }}

    // --- Star ---
    function toggleStar(id, el) {{
      const isStarred = el.style.color.includes('text-muted') || el.style.opacity === '0.3';
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_starred', value: isStarred ? 1 : 0}})
      }}).then(() => {{
        if (isStarred) {{
          el.style.color = 'var(--yellow)';
          el.style.opacity = '1';
        }} else {{
          el.style.color = 'var(--text-muted)';
          el.style.opacity = '0.3';
        }}
      }});
    }}

    // --- Read ---
    function markRead(id, btn) {{
      const row = btn.closest('tr');
      const isUnread = row.style.background.includes('primary');
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_read', value: isUnread ? 1 : 0}})
      }}).then(() => {{
        if (isUnread) {{
          row.style.background = '';
          btn.innerHTML = '&#9898;';
        }} else {{
          row.style.background = 'var(--primary-light)';
          btn.innerHTML = '&#128065;';
        }}
      }});
    }}

    // --- Archive ---
    function archiveEmail(id, btn) {{
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_archived', value: 1}})
      }}).then(() => {{
        const row = document.querySelector('tr[data-mail-id="' + id + '"]');
        if (row) {{
          row.style.opacity = '0';
          row.style.transition = 'opacity 0.3s';
          setTimeout(() => row.remove(), 300);
        }}
        updateBulkBar();
      }});
    }}

    // --- Snooze modal ---
    function openSnoozeModal(id) {{
      document.getElementById('snooze-mail-id').value = id;
      document.getElementById('snooze-note').value = '';
      document.getElementById('snooze-date').value = '';
      document.getElementById('snooze-time').value = '09:00';
      document.getElementById('snooze-modal').style.display = 'flex';
    }}

    function snoozePreset(preset) {{
      const now = new Date();
      let target;
      switch(preset) {{
        case 'today':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0, 0);
          if (target <= now) target.setDate(target.getDate() + 1);
          break;
        case 'tomorrow':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0, 0);
          break;
        case 'tomorrow-eve':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 18, 0, 0);
          break;
        case 'next-monday':
          target = new Date(now);
          const day = target.getDay();
          const daysUntilMon = day === 0 ? 1 : (8 - day);
          target.setDate(target.getDate() + daysUntilMon);
          target.setHours(9, 0, 0, 0);
          break;
      }}
      const dateStr = target.getFullYear() + '-' + String(target.getMonth()+1).padStart(2,'0') + '-' + String(target.getDate()).padStart(2,'0');
      document.getElementById('snooze-date').value = dateStr;
      document.getElementById('snooze-time').value = String(target.getHours()).padStart(2,'0') + ':' + String(target.getMinutes()).padStart(2,'0');
      submitSnooze();
    }}

    function submitSnooze() {{
      const id = document.getElementById('snooze-mail-id').value;
      const date = document.getElementById('snooze-date').value;
      const time = document.getElementById('snooze-time').value || '09:00';
      const note = document.getElementById('snooze-note').value.trim();

      if (!date) {{
        alert('Please select a date');
        return;
      }}
      const snoozeUntil = date + ' ' + time + ':00';

      // Set snooze_until
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'snooze_until', value: snoozeUntil}})
      }}).then(() => {{
        // Set snooze_note if provided
        if (note) {{
          return fetch('/api/mail-hub/' + id + '/update', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{field: 'snooze_note', value: note}})
          }});
        }}
      }}).then(() => {{
        document.getElementById('snooze-modal').style.display = 'none';
        const row = document.querySelector('tr[data-mail-id="' + id + '"]');
        if (row) {{
          row.style.opacity = '0.3';
          setTimeout(() => row.remove(), 500);
        }}
      }});
    }}

    // --- Ctrl-click multi-select ---
    var _lastCheckedIdx = null;

    function handleCheckboxClick(cb, event) {{
      const boxes = Array.from(document.querySelectorAll('.mail-checkbox'));
      const idx = boxes.indexOf(cb);
      if (event.ctrlKey && _lastCheckedIdx !== null && _lastCheckedIdx !== idx) {{
        const start = Math.min(_lastCheckedIdx, idx);
        const end = Math.max(_lastCheckedIdx, idx);
        const state = cb.checked;
        for (let i = start; i <= end; i++) {{
          boxes[i].checked = state;
        }}
      }}
      _lastCheckedIdx = idx;
      updateBulkBar();
    }}

    function handleShiftClick(row, event) {{
      event.preventDefault();
      const cb = row.querySelector('.mail-checkbox');
      if (!cb) return;
      cb.checked = !cb.checked;
      handleCheckboxClick(cb, event);
    }}

    // --- Bulk actions ---
    function toggleSelectAll(el) {{
      document.querySelectorAll('.mail-checkbox').forEach(cb => cb.checked = el.checked);
      _lastCheckedIdx = null;
      updateBulkBar();
    }}

    function updateBulkBar() {{
      const checked = document.querySelectorAll('.mail-checkbox:checked');
      const bar = document.getElementById('bulk-bar');
      if (checked.length > 0) {{
        bar.style.display = 'flex';
        document.getElementById('bulk-count').textContent = checked.length + ' selected';
      }} else {{
        bar.style.display = 'none';
      }}
    }}

    function clearSelection() {{
      document.querySelectorAll('.mail-checkbox').forEach(cb => cb.checked = false);
      document.getElementById('select-all').checked = false;
      updateBulkBar();
    }}

    function bulkAction(field, value) {{
      const ids = Array.from(document.querySelectorAll('.mail-checkbox:checked')).map(cb => parseInt(cb.value));
      if (ids.length === 0) return;
      fetch('/api/mail-hub/bulk', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ids: ids, field: field, value: value}})
      }}).then(r => r.json()).then(data => {{
        if (data.updated > 0) {{
          if (field === 'is_archived') {{
            ids.forEach(id => {{
              const row = document.querySelector('tr[data-mail-id="' + id + '"]');
              if (row) {{ row.style.opacity = '0'; setTimeout(() => row.remove(), 300); }}
            }});
          }} else {{
            location.reload();
          }}
          clearSelection();
        }}
      }});
    }}

    // --- Compose ---
    let scheduleMode = false;
    function openComposeModal() {{
      document.getElementById('compose-to').value = '';
      document.getElementById('compose-subject').value = '';
      document.getElementById('compose-body').value = '';
      document.getElementById('compose-status').innerHTML = '';
      scheduleMode = false;
      document.getElementById('schedule-section').style.display = 'none';
      document.getElementById('compose-send-btn').innerHTML = '&#9993; Send Now';
      document.getElementById('compose-modal').style.display = 'flex';
    }}

    function toggleSchedule() {{
      scheduleMode = !scheduleMode;
      const sec = document.getElementById('schedule-section');
      const btn = document.getElementById('compose-send-btn');
      if (scheduleMode) {{
        sec.style.display = 'block';
        btn.innerHTML = '&#128340; Schedule Send';
        // Default to tomorrow 9am
        const tom = new Date();
        tom.setDate(tom.getDate() + 1);
        document.getElementById('compose-date').value = tom.getFullYear() + '-' + String(tom.getMonth()+1).padStart(2,'0') + '-' + String(tom.getDate()).padStart(2,'0');
        document.getElementById('compose-time').value = '09:00';
      }} else {{
        sec.style.display = 'none';
        btn.innerHTML = '&#9993; Send Now';
      }}
    }}

    function sendCompose(mode) {{
      const to = document.getElementById('compose-to').value.trim();
      const subject = document.getElementById('compose-subject').value.trim();
      const body = document.getElementById('compose-body').value.trim();
      const status = document.getElementById('compose-status');
      const btn = document.getElementById('compose-send-btn');
      const acctSelect = document.getElementById('compose-account');
      const accountId = acctSelect ? acctSelect.value : null;

      if (!to || !subject || !body) {{
        status.innerHTML = '<span style="color:var(--red);">Please fill in all fields.</span>';
        return;
      }}

      if (scheduleMode) {{
        const date = document.getElementById('compose-date').value;
        const time = document.getElementById('compose-time').value || '09:00';
        if (!date) {{
          status.innerHTML = '<span style="color:var(--red);">Please select a date for scheduling.</span>';
          return;
        }}
        // Convert local time to UTC for server comparison against NOW()
        const localDt = new Date(date + 'T' + time + ':00');
        const utcStr = localDt.getUTCFullYear() + '-' +
          String(localDt.getUTCMonth()+1).padStart(2,'0') + '-' +
          String(localDt.getUTCDate()).padStart(2,'0') + ' ' +
          String(localDt.getUTCHours()).padStart(2,'0') + ':' +
          String(localDt.getUTCMinutes()).padStart(2,'0') + ':00';
        btn.disabled = true;
        btn.innerHTML = '&#8987; Scheduling...';
        fetch('/api/mail-hub/schedule', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{to_email: to, subject: subject, body: body, scheduled_at: utcStr, account_id: accountId}})
        }}).then(r => r.json()).then(data => {{
          if (data.id) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Scheduled for ' + date + ' ' + time + '</span>';
            btn.innerHTML = '&#10003; Scheduled';
            setTimeout(() => location.reload(), 1500);
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed') + '</span>';
            btn.innerHTML = '&#128340; Schedule Send';
            btn.disabled = false;
          }}
        }}).catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#128340; Schedule Send';
          btn.disabled = false;
        }});
      }} else {{
        btn.disabled = true;
        btn.innerHTML = '&#8987; Sending...';
        const fileInput = document.getElementById('compose-attachments');
        const fd = new FormData();
        fd.append('to_email', to);
        fd.append('subject', subject);
        fd.append('body', body);
        if (accountId) fd.append('account_id', accountId);
        if (fileInput && fileInput.files.length > 0) {{
          for (const f of fileInput.files) fd.append('attachments', f);
        }}
        fetch('/api/mail-hub/send-compose', {{
          method: 'POST',
          body: fd
        }}).then(r => r.json()).then(data => {{
          if (data.ok) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Email sent!</span>';
            btn.innerHTML = '&#10003; Sent';
            setTimeout(() => {{ document.getElementById('compose-modal').style.display = 'none'; }}, 1500);
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Send failed') + '</span>';
            btn.innerHTML = '&#9993; Send Now';
            btn.disabled = false;
          }}
        }}).catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#9993; Send Now';
          btn.disabled = false;
        }});
      }}
    }}

    // File list preview
    document.getElementById('compose-attachments').addEventListener('change', function() {{
      const list = document.getElementById('compose-file-list');
      if (this.files.length === 0) {{ list.innerHTML = ''; return; }}
      const names = Array.from(this.files).map(f => f.name + ' (' + (f.size/1024 < 1024 ? (f.size/1024).toFixed(0) + ' KB' : (f.size/1024/1024).toFixed(1) + ' MB') + ')');
      list.innerHTML = '&#128206; ' + names.join(', ');
    }});
    </script>
    """, active_page="mail_hub", wide=True)


# ---------------------------------------------------------------------------
# API — Email Accounts (multi-mailbox)
# ---------------------------------------------------------------------------

@app.route("/api/email-accounts", methods=["POST"])
@limiter.limit("10 per minute")
def api_add_email_account():
    """Add a new email account after testing IMAP + SMTP connection."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_email_accounts, create_email_account, get_subscription
    from outreach.config import PLAN_LIMITS

    data = request.get_json()
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password are required"}), 400

    # Check mailbox limit
    sub = get_subscription(session["client_id"])
    plan = sub.get("plan", "free") if sub else "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_mb = limits.get("mailboxes", 1)
    existing = get_email_accounts(session["client_id"])
    if max_mb != -1 and len(existing) >= max_mb:
        return jsonify({"error": f"Mailbox limit reached ({max_mb}). Upgrade your plan for more."}), 429

    email_addr = data["email"].strip().lower()
    password = data["password"].strip()
    imap_host = data.get("imap_host", "imap.gmail.com")
    imap_port = int(data.get("imap_port", 993))
    smtp_host = data.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(data.get("smtp_port", 465))
    label = data.get("label", "").strip() or email_addr.split("@")[0].title()

    # Test IMAP connection
    import imaplib
    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=15)
        imap.login(email_addr, password)
        imap.logout()
    except Exception as e:
        return jsonify({"error": f"IMAP connection failed: {str(e)[:100]}"}), 400

    # Test SMTP connection — try SSL (465) first, fall back to STARTTLS (587)
    import smtplib
    smtp_connected = False
    smtp_error = ""
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as srv:
            srv.login(email_addr, password)
        smtp_connected = True
    except Exception as e:
        smtp_error = str(e)[:100]
        # Fallback: try STARTTLS on port 587
        if smtp_port == 465:
            try:
                with smtplib.SMTP(smtp_host, 587, timeout=15) as srv:
                    srv.starttls()
                    srv.login(email_addr, password)
                smtp_connected = True
                smtp_port = 587  # Save the working port
            except Exception:
                pass
    if not smtp_connected:
        return jsonify({"error": f"SMTP connection failed: {smtp_error}"}), 400

    try:
        acct_id = create_email_account(
            client_id=session["client_id"],
            label=label, email=email_addr, password=password,
            imap_host=imap_host, imap_port=imap_port,
            smtp_host=smtp_host, smtp_port=smtp_port,
        )
        return jsonify({"id": acct_id})
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": "This email is already connected"}), 400
        return jsonify({"error": str(e)}), 500


@app.route("/api/email-accounts/<int:account_id>/default", methods=["POST"])
def api_set_default_account(account_id):
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import update_email_account
    update_email_account(account_id, session["client_id"], is_default=1)
    return jsonify({"ok": True})


@app.route("/api/email-accounts/<int:account_id>", methods=["DELETE"])
def api_delete_email_account(account_id):
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import delete_email_account
    delete_email_account(account_id, session["client_id"])
    return jsonify({"ok": True})


@app.route("/api/mail-preferences", methods=["POST"])
def api_save_mail_preferences():
    """Save the user's mail sorting preferences."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    prefs = data.get("preferences", "").strip()
    if not prefs:
        return jsonify({"error": "Preferences cannot be empty"}), 400
    from outreach.db import update_mail_preferences
    update_mail_preferences(session["client_id"], prefs[:2000])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — Mail Hub
# ---------------------------------------------------------------------------

@app.route("/api/mail-hub/peek", methods=["POST"])
def api_mail_peek():
    """Quick IMAP check: count emails arrived since the last sync."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        from outreach.db import get_email_accounts, get_db, _fetchval
        from outreach.mail_hub import peek_unseen
        from datetime import datetime, timedelta

        # Find the date of the most recent synced email
        with get_db() as db:
            last_synced = _fetchval(db,
                "SELECT MAX(received_at) FROM mail_inbox WHERE client_id = %s",
                (session["client_id"],))

        # Convert last synced date to IMAP format; count DB emails from that date
        if last_synced:
            try:
                dt = datetime.strptime(last_synced[:10], "%Y-%m-%d")
            except ValueError:
                dt = datetime.now()
            imap_since = dt.strftime("%d-%b-%Y")
            db_since = dt.strftime("%Y-%m-%d")
        else:
            # No emails synced yet — check last 3 days
            dt = datetime.now() - timedelta(days=3)
            imap_since = dt.strftime("%d-%b-%Y")
            db_since = dt.strftime("%Y-%m-%d")

        # Count DB emails from that date onward
        with get_db() as db:
            db_count = _fetchval(db,
                "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND received_at >= %s",
                (session["client_id"], db_since))

        # Count IMAP emails from that date onward
        accounts = get_email_accounts(session["client_id"])
        imap_total = 0
        imap_errors = []
        if accounts:
            for acct in accounts:
                n = peek_unseen(
                    imap_host=acct["imap_host"], imap_port=acct["imap_port"],
                    imap_user=acct["email"], imap_password=acct["password"],
                    since_date=imap_since)
                if n == -1:
                    imap_errors.append(acct["email"])
                    print(f"[PEEK] IMAP failed for {acct['email']} ({acct['imap_host']}:{acct['imap_port']})", flush=True)
                elif n > 0:
                    imap_total += n

        # New = IMAP count since last sync minus what's already in DB
        waiting = max(imap_total - db_count, 0)
        result = {"unseen": waiting}
        if imap_errors:
            result["imap_error"] = True
            result["failed_accounts"] = imap_errors
        return jsonify(result)
    except Exception as e:
        print(f"[PEEK] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


# In-memory sync job tracker
import threading
_sync_jobs: dict[int, dict] = {}  # client_id -> {status, new_emails, error}
_campaign_sends: dict[int, dict] = {}  # campaign_id -> {status, sent, total}

@app.route("/api/mail-hub/sync", methods=["POST"])
def api_mail_sync():
    """Start inbox sync in background — returns immediately."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    client_id = session["client_id"]
    # If already syncing, return current status
    job = _sync_jobs.get(client_id)
    if job and job["status"] == "syncing":
        return jsonify({"status": "syncing"})
    try:
        from outreach.db import check_limit, get_email_accounts
        allowed, used, limit = check_limit(client_id, "mail_hub_syncs")
        if not allowed:
            return jsonify({"error": f"Monthly Mail Hub sync limit reached ({used}/{limit}). Upgrade your plan for unlimited syncs."}), 429

        accounts = get_email_accounts(client_id)
        _sync_jobs[client_id] = {"status": "syncing", "new_emails": 0, "error": None}

        def _bg_sync():
            try:
                from outreach.mail_hub import sync_inbox
                from outreach.db import increment_usage
                total_new = 0
                if not accounts:
                    _sync_jobs[client_id] = {"status": "done", "new_emails": 0, "error": "No email account connected. Add one in Settings."}
                    return
                for acct in accounts:
                    n = sync_inbox(client_id, days=3, account_id=acct["id"])
                    total_new += n
                if total_new > 0:
                    increment_usage(client_id, "mail_hub_syncs")
                _sync_jobs[client_id] = {"status": "done", "new_emails": total_new, "error": None}
            except Exception as e:
                _sync_jobs[client_id] = {"status": "error", "new_emails": 0, "error": str(e)}
        threading.Thread(target=_bg_sync, daemon=True).start()
        return jsonify({"status": "syncing"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/sync-status")
def api_mail_sync_status():
    """Poll sync job progress."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    job = _sync_jobs.get(session["client_id"])
    if not job:
        return jsonify({"status": "idle"})
    return jsonify(job)


@app.route("/api/mail-hub/<int:mail_id>/update", methods=["POST"])
def api_mail_update(mail_id):
    """Update a mail item field (star, read, archive, snooze, priority, category)."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import update_mail_field
    data = request.get_json()
    if not data or "field" not in data:
        return jsonify({"error": "missing field"}), 400
    ok = update_mail_field(mail_id, session["client_id"], data["field"], data.get("value"))
    return jsonify({"ok": ok})


@app.route("/api/mail-hub/bulk", methods=["POST"])
def api_mail_bulk():
    """Bulk update multiple mail items."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import bulk_update_mail
    data = request.get_json()
    if not data or "ids" not in data or "field" not in data:
        return jsonify({"error": "missing ids or field"}), 400
    ids = [int(i) for i in data["ids"]]
    updated = bulk_update_mail(ids, session["client_id"], data["field"], data.get("value"))
    return jsonify({"updated": updated})


@app.route("/api/mail-hub/send-compose", methods=["POST"])
def api_mail_send_compose():
    """Send a composed email immediately via SMTP — supports account_id and file attachments."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401

    # Support both JSON and multipart/form-data (for attachments)
    if request.content_type and 'multipart/form-data' in request.content_type:
        to_email = request.form.get("to_email", "")
        subject = request.form.get("subject", "")
        body = request.form.get("body", "")
        acct_id_raw = request.form.get("account_id")
        files = request.files.getlist("attachments")
    else:
        data = request.get_json() or {}
        to_email = data.get("to_email", "")
        subject = data.get("subject", "")
        body = data.get("body", "")
        acct_id_raw = data.get("account_id")
        files = []

    if not to_email or not subject or not body:
        return jsonify({"error": "to_email, subject, and body are required"}), 400

    MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25 MB total
    total_size = sum(f.seek(0, 2) or f.tell() for f in files)
    for f in files:
        f.seek(0)
    if total_size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "Attachments exceed 25 MB limit."}), 400

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        from outreach.db import get_email_account, get_default_email_account

        smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        if acct_id_raw:
            acct = get_email_account(int(acct_id_raw), session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]
        elif not smtp_user:
            acct = get_default_email_account(session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for f in files:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            safe_name = f.filename.replace('"', '_') if f.filename else "attachment"
            part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            msg.attach(part)

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                srv.starttls()
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/schedule", methods=["POST"])
def api_mail_schedule():
    """Schedule an email to be sent later."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import create_scheduled_email
    data = request.get_json()
    if not data or not data.get("to_email") or not data.get("subject") or not data.get("body") or not data.get("scheduled_at"):
        return jsonify({"error": "to_email, subject, body, and scheduled_at are required"}), 400
    email_id = create_scheduled_email(
        session["client_id"],
        to_email=data["to_email"],
        subject=data["subject"],
        body=data["body"],
        scheduled_at=data["scheduled_at"],
        to_name=data.get("to_name", ""),
        reply_to_mail_id=data.get("reply_to_mail_id"),
        account_id=data.get("account_id"),
    )
    return jsonify({"id": email_id})


@app.route("/api/mail-hub/scheduled/<int:email_id>/delete", methods=["POST"])
def api_scheduled_delete(email_id):
    """Cancel a pending scheduled email."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import delete_scheduled_email
    delete_scheduled_email(email_id, session["client_id"])
    return jsonify({"ok": True})


@app.route("/api/debug/scheduled")
def api_debug_scheduled():
    """Diagnostic: show what the web app sees in scheduled_emails. Login required."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_db, _fetchall, _fetchval, _USE_PG, _db_fingerprint
    result = {"engine": "PG" if _USE_PG else "SQLite", "db_fingerprint": _db_fingerprint()}
    try:
        with get_db() as db:
            if _USE_PG:
                result["db_name"] = _fetchval(db, "SELECT current_database()")
                result["pg_now"] = _fetchval(db, "SELECT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")
            result["total_rows"] = _fetchval(db, "SELECT COUNT(*) FROM scheduled_emails")
            result["pending_rows"] = _fetchval(db, "SELECT COUNT(*) FROM scheduled_emails WHERE status = 'pending'")
            rows = _fetchall(db, "SELECT id, to_email, scheduled_at, status, client_id FROM scheduled_emails ORDER BY id DESC LIMIT 10")
            result["last_10"] = [{k: str(v) for k, v in r.items()} for r in rows]
    except Exception as e:
        result["error"] = str(e)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Route — Mail Hub detail view
# ---------------------------------------------------------------------------

@app.route("/mail-hub/<int:mail_id>")
def mail_hub_detail(mail_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_mail_item, update_mail_field, get_contact_by_email, get_email_account, get_email_accounts

    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        session.setdefault("_flashes", []).append(("error", "Email not found."))
        return redirect(url_for("mail_hub"))

    # Auto-mark as read
    if not mail["is_read"]:
        update_mail_field(mail_id, session["client_id"], "is_read", 1)

    # Check if sender is a saved contact
    saved_contact = get_contact_by_email(session["client_id"], mail["from_email"])

    # Get account info for this email
    mail_account = get_email_account(mail["account_id"], session["client_id"]) if mail.get("account_id") else None
    all_accounts = get_email_accounts(session["client_id"])

    pri_config = {
        "urgent": ("&#128308;", "badge-red"),
        "important": ("&#128992;", "badge-yellow"),
        "normal": ("&#128309;", "badge-blue"),
        "low": ("&#11035;", "badge-gray"),
    }
    cat_config = {
        "action_required": ("&#9889;", "Action Required"),
        "meeting": ("&#128197;", "Meeting"),
        "fyi": ("&#128196;", "FYI"),
        "newsletter": ("&#128240;", "Newsletter"),
        "personal": ("&#128100;", "Personal"),
        "spam": ("&#128681;", "Spam"),
        "uncategorized": ("&#128233;", "Uncategorized"),
    }

    pi, pb = pri_config.get(mail["priority"], ("&#128309;", "badge-blue"))
    ci, cl = cat_config.get(mail["category"], ("&#128233;", mail["category"]))
    from_display = _esc(mail["from_name"]) if mail["from_name"] else _esc(mail["from_email"])
    star_icon = "&#9733;" if mail["is_starred"] else "&#9734;"
    star_color = "color:var(--yellow);" if mail["is_starred"] else "color:var(--text-muted);"
    body_html = _esc(mail["body_preview"]).replace("\\n", "<br>")
    subject = _esc(mail["subject"] or "(no subject)")
    time_str = mail["received_at"][:19] if mail["received_at"] else ""

    return _render(t("mail.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/mail-hub">{t("mail.title")}</a> / Email</div>

    <div style="display:grid;grid-template-columns:1fr 380px;gap:24px;align-items:start;">
      <!-- Main email view -->
      <div style="min-width:0;">
        <div class="card" style="padding:0;overflow:hidden;">
          <!-- Header -->
          <div style="padding:28px 32px 20px;border-bottom:1px solid var(--border-light);">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;">
              <div style="flex:1;">
                <h1 style="font-size:24px;font-weight:700;margin-bottom:12px;line-height:1.3;">{subject}</h1>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span class="badge {pb}" style="font-size:12px;">{pi} {mail['priority'].title()}</span>
                  <span class="badge badge-gray" style="font-size:12px;">{ci} {cl}</span>
                </div>
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-shrink:0;">
                <span style="cursor:pointer;font-size:24px;{star_color}" id="detail-star"
                      onclick="toggleDetailStar({mail['id']}, this);">{star_icon}</span>
                <button class="btn btn-ghost btn-sm" onclick="archiveAndBack({mail['id']})" title="Archive" style="font-size:16px;">&#128230;</button>
              </div>
            </div>
          </div>

          <!-- Sender info -->
          <div style="padding:20px 32px;border-bottom:1px solid var(--border-light);display:flex;justify-content:space-between;align-items:center;">
            <div style="display:flex;align-items:center;gap:14px;">
              <div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:20px;">
                {_esc(from_display[:1].upper())}
              </div>
              <div>
                <div style="font-weight:600;font-size:16px;">{from_display}{'  <a href="/contacts/' + str(saved_contact['id']) + '" class="badge badge-blue" style="font-size:10px;text-decoration:none;vertical-align:middle;">' + _esc(saved_contact.get('relationship','').title() or 'Contact') + ' &#8599;</a>' if saved_contact else ''}</div>
                <div style="font-size:13px;color:var(--text-muted);">{_esc(mail['from_email'])}</div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px;">
              <div style="text-align:right;">
                <div style="font-size:13px;color:var(--text-muted);">{time_str}</div>
                <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">To: {_esc(mail['to_email'])}</div>
              </div>
              {'<a href="/contacts/' + str(saved_contact['id']) + '" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128101; View Contact</a>' if saved_contact else '<button class="btn btn-primary btn-sm" onclick="saveContact()" id="save-contact-btn" style="font-size:12px;">&#128101; Save Contact</button>'}
            </div>
          </div>

          <!-- AI Summary -->
          {"<div style='padding:16px 32px;background:var(--primary-light);border-bottom:1px solid var(--border-light);'><div style=font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;>AI Summary</div><div style=font-size:14px;color:var(--primary);font-style:italic;>" + _esc(mail['ai_summary']) + "</div></div>" if mail.get('ai_summary') else ""}

          <!-- Email body -->
          <div style="padding:28px 32px;font-size:15px;line-height:1.8;color:var(--text-secondary);word-break:break-word;overflow-wrap:break-word;overflow-x:auto;max-width:100%;">
            {body_html}
          </div>
        </div>

        <!-- Back link -->
        <div style="margin-top:8px;">
          <a href="/mail-hub" class="btn btn-ghost" style="font-size:14px;">&#8592; Back to Inbox</a>
        </div>
      </div>

      <!-- Reply panel -->
      <div style="position:sticky;top:76px;">
        <div class="card" style="padding:24px;">
          <h3 style="font-size:18px;font-weight:700;margin-bottom:16px;">&#9993; Reply</h3>

          {"" if len(all_accounts) < 2 else '<div style="margin-bottom:14px;"><label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">From</label><select id="reply-account" style="font-size:14px;width:100%;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);">' + ''.join(f'<option value="{a["id"]}" {"selected" if (mail_account and a["id"] == mail_account["id"]) or (not mail_account and a["is_default"]) else ""}>{_esc(a["label"] or a["email"])} ({_esc(a["email"])})</option>' for a in all_accounts) + '</select></div>'}

          <div style="margin-bottom:14px;">
            <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">To</label>
            <div style="font-size:14px;padding:10px 14px;background:var(--bg);border-radius:var(--radius-xs);border:1px solid var(--border-light);">{_esc(mail['from_email'])}</div>
          </div>

          <div style="margin-bottom:14px;">
            <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">Subject</label>
            <input type="text" id="reply-subject" value="Re: {subject}" style="font-size:14px;width:100%;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);">
          </div>

          <div style="margin-bottom:14px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">Message</label>
              <button class="btn btn-ghost btn-sm" onclick="generateDraft({mail['id']})" id="ai-draft-btn" style="font-size:12px;">&#9889; AI Draft</button>
            </div>
            <textarea id="reply-body" rows="10" placeholder="Type your reply..." style="font-size:14px;width:100%;padding:12px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);resize:vertical;line-height:1.6;font-family:inherit;"></textarea>
          </div>

          <div style="margin-bottom:14px;">
            <label style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">
              &#128206; Attachments
            </label>
            <input type="file" id="reply-attachments" multiple style="font-size:13px;margin-top:4px;width:100%;">
            <div id="reply-file-list" style="font-size:12px;color:var(--text-muted);margin-top:4px;"></div>
          </div>

          <div style="display:flex;gap:8px;">
            <button class="btn btn-primary" onclick="sendReply({mail['id']})" id="send-btn" style="flex:1;font-size:15px;padding:12px 0;">&#9993; Send Reply</button>
            <button class="btn btn-yellow" onclick="toggleReplySchedule()" id="reply-sched-toggle" style="font-size:14px;padding:12px 14px;">&#128340;</button>
          </div>
          <div id="reply-schedule-section" style="display:none;margin-top:10px;">
            <label style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;">Schedule for</label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
              <input type="date" id="reply-sched-date" style="margin-bottom:0;font-size:13px;">
              <input type="time" id="reply-sched-time" value="09:00" style="margin-bottom:0;font-size:13px;">
            </div>
          </div>
          <div id="reply-status" style="margin-top:10px;font-size:13px;text-align:center;"></div>
        </div>
      </div>
    </div>

    <script>
    function toggleDetailStar(id, el) {{
      const isStarred = el.innerHTML.charCodeAt(0) === 9734;
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_starred', value: isStarred ? 1 : 0}})
      }}).then(() => {{
        el.innerHTML = isStarred ? '&#9733;' : '&#9734;';
        el.style.color = isStarred ? 'var(--yellow)' : 'var(--text-muted)';
      }});
    }}

    function archiveAndBack(id) {{
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_archived', value: 1}})
      }}).then(() => window.location = '/mail-hub');
    }}

    function saveContact() {{
      const btn = document.getElementById('save-contact-btn');
      btn.innerHTML = '&#8987; Saving...';
      btn.disabled = true;
      fetch('/api/contacts/quick-save', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{email: '{mail["from_email"]}', name: '{_esc(mail.get("from_name", ""))}', from_mail_id: {mail['id']}}})
      }})
        .then(r => r.json())
        .then(data => {{
          if (data.id) {{
            btn.outerHTML = '<a href="/contacts/' + data.id + '" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128101; View Contact</a>';
          }} else {{
            btn.innerHTML = '&#9888; Failed';
            btn.disabled = false;
          }}
        }})
        .catch(() => {{ btn.innerHTML = '&#128101; Save Contact'; btn.disabled = false; }});
    }}

    function generateDraft(id) {{
      const btn = document.getElementById('ai-draft-btn');
      btn.innerHTML = '&#8987; Generating...';
      btn.disabled = true;
      fetch('/api/mail-hub/' + id + '/draft', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.draft) {{
            document.getElementById('reply-body').value = data.draft;
            btn.innerHTML = '&#9889; Regenerate';
          }} else {{
            btn.innerHTML = '&#9888; Failed';
          }}
          btn.disabled = false;
        }})
        .catch(() => {{
          btn.innerHTML = '&#9889; AI Draft';
          btn.disabled = false;
        }});
    }}

    function sendReply(id) {{
      const btn = document.getElementById('send-btn');
      const body = document.getElementById('reply-body').value.trim();
      const subject = document.getElementById('reply-subject').value.trim();
      const status = document.getElementById('reply-status');
      const acctSelect = document.getElementById('reply-account');
      const accountId = acctSelect ? acctSelect.value : null;

      if (!body) {{
        status.innerHTML = '<span style="color:var(--red);">Please write a message first.</span>';
        return;
      }}

      // Check if scheduling
      const schedSection = document.getElementById('reply-schedule-section');
      if (schedSection.style.display !== 'none') {{
        const date = document.getElementById('reply-sched-date').value;
        const time = document.getElementById('reply-sched-time').value || '09:00';
        if (!date) {{
          status.innerHTML = '<span style="color:var(--red);">Please select a date for scheduling.</span>';
          return;
        }}
        btn.innerHTML = '&#8987; Scheduling...';
        btn.disabled = true;
        // Convert local time to UTC
        const localDt = new Date(date + 'T' + time + ':00');
        const utcStr = localDt.getUTCFullYear() + '-' +
          String(localDt.getUTCMonth()+1).padStart(2,'0') + '-' +
          String(localDt.getUTCDate()).padStart(2,'0') + ' ' +
          String(localDt.getUTCHours()).padStart(2,'0') + ':' +
          String(localDt.getUTCMinutes()).padStart(2,'0') + ':00';
        fetch('/api/mail-hub/schedule', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{to_email: '{mail["from_email"]}', subject: subject, body: body, scheduled_at: utcStr, reply_to_mail_id: id, account_id: accountId}})
        }}).then(r => r.json()).then(data => {{
          if (data.id) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Reply scheduled for ' + date + ' ' + time + '</span>';
            btn.innerHTML = '&#10003; Scheduled';
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed') + '</span>';
            btn.innerHTML = '&#128340; Schedule Reply';
            btn.disabled = false;
          }}
        }});
        return;
      }}

      btn.innerHTML = '&#8987; Sending...';
      btn.disabled = true;
      const fileInput = document.getElementById('reply-attachments');
      const fd = new FormData();
      fd.append('subject', subject);
      fd.append('body', body);
      if (accountId) fd.append('account_id', accountId);
      if (fileInput && fileInput.files.length > 0) {{
        for (const f of fileInput.files) fd.append('attachments', f);
      }}
      fetch('/api/mail-hub/' + id + '/send-reply', {{
        method: 'POST',
        body: fd
      }})
        .then(r => r.json())
        .then(data => {{
          if (data.ok) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Reply sent successfully!</span>';
            btn.innerHTML = '&#10003; Sent';
            document.getElementById('reply-body').value = '';
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Send failed') + '</span>';
            btn.innerHTML = '&#9993; Send Reply';
            btn.disabled = false;
          }}
        }})
        .catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#9993; Send Reply';
          btn.disabled = false;
        }});
    }}

    let replyScheduleMode = false;
    function toggleReplySchedule() {{
      replyScheduleMode = !replyScheduleMode;
      const sec = document.getElementById('reply-schedule-section');
      const btn = document.getElementById('send-btn');
      if (replyScheduleMode) {{
        sec.style.display = 'block';
        btn.innerHTML = '&#128340; Schedule Reply';
        const tom = new Date();
        tom.setDate(tom.getDate() + 1);
        document.getElementById('reply-sched-date').value = tom.getFullYear() + '-' + String(tom.getMonth()+1).padStart(2,'0') + '-' + String(tom.getDate()).padStart(2,'0');
      }} else {{
        sec.style.display = 'none';
        btn.innerHTML = '&#9993; Send Reply';
      }}
    }}

    // File list preview
    document.getElementById('reply-attachments').addEventListener('change', function() {{
      const list = document.getElementById('reply-file-list');
      if (this.files.length === 0) {{ list.innerHTML = ''; return; }}
      const names = Array.from(this.files).map(f => f.name + ' (' + (f.size/1024 < 1024 ? (f.size/1024).toFixed(0) + ' KB' : (f.size/1024/1024).toFixed(1) + ' MB') + ')');
      list.innerHTML = '&#128206; ' + names.join(', ');
    }});
    </script>
    """, active_page="mail_hub", wide=True)


# ---------------------------------------------------------------------------
# API — Mail Hub: AI draft & send reply
# ---------------------------------------------------------------------------

@app.route("/api/mail-hub/<int:mail_id>/draft", methods=["POST"])
def api_mail_draft(mail_id):
    """Generate an AI reply draft for a mail hub email, enriched with contact context."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_mail_item, get_contact_by_email
    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        return jsonify({"error": "not found"}), 404
    try:
        contact = get_contact_by_email(session["client_id"], mail["from_email"])
        from outreach.ai import _get_mail_reply_draft
        draft = _get_mail_reply_draft(
            from_name=mail["from_name"],
            from_email=mail["from_email"],
            subject=mail["subject"],
            body=mail["body_preview"],
            priority=mail["priority"],
            category=mail["category"],
            sender_name=get_client(session["client_id"]).get("name", SENDER_NAME),
            contact_context=contact,
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/<int:mail_id>/send-reply", methods=["POST"])
def api_mail_send_reply(mail_id):
    """Send a reply to a mail hub email via SMTP — supports file attachments."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_mail_item, get_email_account, get_default_email_account
    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        return jsonify({"error": "not found"}), 404

    # Support both JSON and multipart/form-data (for attachments)
    if request.content_type and 'multipart/form-data' in request.content_type:
        body = request.form.get("body", "")
        subject = request.form.get("subject", f"Re: {mail['subject']}")
        acct_id = request.form.get("account_id") or mail.get("account_id")
        files = request.files.getlist("attachments")
    else:
        data = request.get_json() or {}
        body = data.get("body", "")
        subject = data.get("subject", f"Re: {mail['subject']}")
        acct_id = data.get("account_id") or mail.get("account_id")
        files = []

    if not body:
        return jsonify({"error": "empty body"}), 400

    MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25 MB total
    total_size = sum(f.seek(0, 2) or f.tell() for f in files)
    for f in files:
        f.seek(0)
    if total_size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "Attachments exceed 25 MB limit."}), 400

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

        # Use the account the email was received on, or a specified account, or default
        smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        if acct_id:
            acct = get_email_account(acct_id, session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]
        elif not smtp_user:
            acct = get_default_email_account(session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = mail["from_email"]
        msg["Subject"] = subject
        if mail.get("message_id"):
            msg["In-Reply-To"] = mail["message_id"]
            msg["References"] = mail["message_id"]
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for f in files:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            safe_name = f.filename.replace('"', '_') if f.filename else "attachment"
            part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            msg.attach(part)

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                srv.starttls()
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)

        # Update last_contacted on the contact if they exist
        from outreach.db import get_contact_by_email, update_contact
        contact = get_contact_by_email(session["client_id"], mail["from_email"])
        if contact:
            from datetime import datetime
            update_contact(contact["id"], session["client_id"],
                           last_contacted=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Routes — Contacts Book
# ---------------------------------------------------------------------------

@app.route("/contacts")
def contacts_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_contacts

    search = request.args.get("q", "")
    rel_filter = request.args.get("rel", "")
    tag_filter = request.args.get("tag", "")
    data_cid = _effective_client_id()
    contacts = get_contacts(data_cid, search=search,
                            relationship=rel_filter, tag=tag_filter)

    # Collect all unique tags and relationships for filters (need full list for counts)
    all_tags_count = {}
    all_rels = set()
    all_contacts_full = get_contacts(data_cid)
    for c in all_contacts_full:
        if c.get("tags"):
            for tg in c["tags"].split(","):
                tg = tg.strip()
                if tg:
                    all_tags_count[tg] = all_tags_count.get(tg, 0) + 1
        if c.get("relationship"):
            all_rels.add(c["relationship"])

    rel_options = ""
    for r in sorted(all_rels):
        sel = "selected" if rel_filter == r else ""
        rel_options += f'<option value="{_esc(r)}" {sel}>{_esc(r.title())}</option>'

    tag_badges = ""
    for tg in sorted(all_tags_count.keys()):
        active = "background:var(--primary);color:#fff;" if tag_filter == tg else ""
        tag_badges += f'<a href="?tag={_esc(tg)}" class="badge badge-gray" style="text-decoration:none;font-size:12px;cursor:pointer;{active}">{_esc(tg)} <span style="opacity:0.7;">({all_tags_count[tg]})</span></a> '

    # Build contact cards
    contact_cards = ""
    for c in contacts:
        initials = (c["name"][:1] if c["name"] else c["email"][:1]).upper()
        rel_badge = f'<span class="badge badge-blue" style="font-size:10px;">{_esc(c["relationship"].title())}</span>' if c.get("relationship") else ""
        lang_badge = f'<span class="badge badge-gray" style="font-size:9px;">&#127760; {_esc(c["language"])}</span>' if c.get("language") else ""
        tags_html = ""
        if c.get("tags"):
            for tg in c["tags"].split(","):
                tg = tg.strip()
                if tg:
                    tags_html += f'<span class="badge badge-gray" style="font-size:9px;">{_esc(tg)}</span> '
        last = c["last_contacted"][:10] if c.get("last_contacted") else "Never"
        notes_preview = _esc(c["notes"][:80]) + "..." if len(c.get("notes", "")) > 80 else _esc(c.get("notes", ""))

        contact_cards += f"""
        <div class="card" style="display:flex;align-items:flex-start;gap:16px;padding:20px;position:relative;">
          <input type="checkbox" class="bulk-sel" data-cid="{c['id']}" style="position:absolute;top:12px;left:12px;width:16px;height:16px;cursor:pointer;accent-color:var(--primary);display:none;" onclick="event.stopPropagation();">
          <a href="/contacts/{c['id']}" style="text-decoration:none;color:inherit;display:flex;align-items:flex-start;gap:16px;flex:1;cursor:pointer;">
          <div style="width:50px;height:50px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:20px;flex-shrink:0;">
            {initials}
          </div>
          <div style="flex:1;min-width:0;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span style="font-weight:600;font-size:16px;">{_esc(c['name'] or c['email'])}</span>
              {rel_badge}
              {lang_badge}
            </div>
            <div style="font-size:13px;color:var(--text-muted);margin-top:2px;">{_esc(c['email'])}</div>
            {'<div style="font-size:13px;color:var(--text-secondary);margin-top:2px;">' + _esc(c['company']) + (' — ' + _esc(c['role']) if c.get('role') else '') + '</div>' if c.get('company') else ''}
            {'<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;font-style:italic;">' + notes_preview + '</div>' if notes_preview else ''}
            <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
              {tags_html}
              <span style="font-size:11px;color:var(--text-muted);margin-left:auto;">Last contact: {last}</span>
            </div>
          </div>
          </a>
        </div>"""

    if not contact_cards:
        contact_cards = f"""
        <div class="empty" style="padding:60px;">
          <div class="empty-icon">&#128101;</div>
          <h3>{'No contacts match your search' if search or rel_filter or tag_filter else 'No contacts yet'}</h3>
          <p>{'Try different filters.' if search or rel_filter or tag_filter else 'Open an email in Mail Hub and click "Save Contact" to start building your contact book.'}</p>
        </div>"""

    # Build groups section
    from outreach.db import get_contact_groups
    groups = get_contact_groups(data_cid)
    groups_html = ""
    if groups:
        group_cards = ""
        for g in groups:
            from urllib.parse import quote as _urlquote
            gname = _esc(g["name"])
            gcount = g["count"]
            gurl = _urlquote(g["name"])
            group_cards += (
                f'<div class="card" style="padding:14px 18px;display:flex;align-items:center;'
                f'justify-content:space-between;gap:12px;min-width:220px;">'
                f'<div><div style="font-weight:600;font-size:14px;">&#128193; {gname}</div>'
                f'<div style="color:var(--text-muted);font-size:12px;">{gcount} contact{"s" if gcount != 1 else ""}</div></div>'
                f'<a href="/contacts/group/{gurl}/send" class="btn btn-primary btn-sm" style="font-size:12px;white-space:nowrap;">&#9993; Send</a>'
                f'</div>'
            )
        groups_html = (
            '<div style="margin-bottom:20px;">'
            '<h3 style="font-size:16px;margin-bottom:10px;">&#128193; Groups</h3>'
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;">{group_cards}</div>'
            '<p style="font-size:12px;color:var(--text-muted);margin-top:8px;">Tag contacts to create groups, then send personalized emails to entire groups at once.</p>'
            '</div>'
        )

    return _render(t("contacts.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("contacts.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;">
      <div>
        <h1 style="font-size:30px;">&#128101; {t("contacts.title")}</h1>
        <p class="subtitle" style="font-size:16px;">Your personal contact book — AI uses this context to write perfect replies.</p>
      </div>
      <div class="btn-group">
        <button onclick="toggleBulkMode()" class="btn btn-ghost" id="bulk-btn" style="font-size:14px;padding:10px 18px;">&#9745; Select</button>
        <button onclick="document.getElementById('add-modal').style.display='flex'" class="btn btn-primary" style="font-size:15px;padding:10px 22px;">&#43; Add Contact</button>
      </div>
    </div>

    <!-- Filters -->
    <div class="card" style="padding:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:20px;">
      <form method="GET" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;flex:1;">
        <input type="text" name="q" value="{_esc(search)}" placeholder="Search name, email, company..." style="font-size:14px;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);min-width:250px;">
        <select name="rel" style="font-size:14px;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);">
          <option value="">All Relationships</option>
          {rel_options}
        </select>
        <button type="submit" class="btn btn-ghost" style="font-size:14px;">&#128269; Filter</button>
        {'<a href="/contacts" class="btn btn-ghost" style="font-size:13px;">Clear</a>' if search or rel_filter or tag_filter else ''}
      </form>
    </div>
    {'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;">' + tag_badges + '</div>' if tag_badges else ''}

    {groups_html}

    <div style="font-size:13px;color:var(--text-muted);margin-bottom:12px;">{len(contacts)} contact{'s' if len(contacts) != 1 else ''}</div>

    <!-- Contact cards -->
    <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(480px, 1fr));gap:12px;">
      {contact_cards}
    </div>

    <!-- Add Contact Modal -->
    <div id="add-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:500px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#128101; New Contact</h2>
        <form method="POST" action="/contacts/add">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div class="form-group"><label>Email *</label><input name="email" type="email" required style="font-size:14px;"></div>
            <div class="form-group"><label>Name</label><input name="name" style="font-size:14px;"></div>
            <div class="form-group"><label>Company</label><input name="company" style="font-size:14px;"></div>
            <div class="form-group"><label>Role</label><input name="role" style="font-size:14px;"></div>
            <div class="form-group"><label>Relationship</label>
              <select name="relationship" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="client">Client</option>
                <option value="colleague">Colleague</option>
                <option value="vendor">Vendor</option>
                <option value="lead">Lead</option>
                <option value="friend">Friend</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div class="form-group"><label>Tags</label><input name="tags" placeholder="comma,separated" style="font-size:14px;"></div>
            <div class="form-group"><label>Language</label>
              <select name="language" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="English">English</option>
                <option value="Spanish">Spanish</option>
                <option value="French">French</option>
                <option value="Portuguese">Portuguese</option>
                <option value="German">German</option>
                <option value="Italian">Italian</option>
                <option value="Dutch">Dutch</option>
                <option value="Japanese">Japanese</option>
                <option value="Chinese">Chinese</option>
                <option value="Korean">Korean</option>
                <option value="Arabic">Arabic</option>
                <option value="Hindi">Hindi</option>
                <option value="Russian">Russian</option>
                <option value="Other">Other</option>
              </select>
            </div>
          </div>
          <div class="form-group" style="margin-top:8px;"><label>Notes</label><textarea name="notes" rows="3" placeholder="Who are they? What do they care about?" style="font-size:14px;"></textarea></div>
          <div class="form-group"><label>Personality / Communication Style</label><textarea name="personality" rows="2" placeholder="e.g. 'Direct and concise, prefers bullet points, casual tone'" style="font-size:14px;"></textarea></div>
          <div style="display:flex;gap:8px;margin-top:16px;">
            <button type="submit" class="btn btn-primary" style="flex:1;font-size:15px;">Save Contact</button>
            <button type="button" class="btn btn-ghost" onclick="document.getElementById('add-modal').style.display='none'" style="font-size:15px;">Cancel</button>
          </div>
        </form>
      </div>
    </div>

    <!-- Bulk action bar -->
    <div id="bulk-bar" style="display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:2px solid var(--primary);padding:14px 24px;z-index:190;box-shadow:0 -4px 20px rgba(0,0,0,0.15);display:none;align-items:center;gap:12px;flex-wrap:wrap;">
      <span id="bulk-count" style="font-weight:700;font-size:14px;color:var(--primary);">0 selected</span>
      <input type="text" id="bulk-tag-input" placeholder="Enter tag name..." style="font-size:14px;padding:8px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);min-width:180px;">
      <button onclick="bulkTag('add')" class="btn btn-primary btn-sm" style="font-size:13px;">+ Add Tag</button>
      <button onclick="bulkTag('remove')" class="btn btn-ghost btn-sm" style="font-size:13px;color:var(--red);">- Remove Tag</button>
      <div style="margin-left:8px;display:flex;gap:6px;flex-wrap:wrap;" id="quick-tags">
        <button onclick="document.getElementById('bulk-tag-input').value='VIP';bulkTag('add')" class="btn btn-ghost btn-sm" style="font-size:11px;">VIP</button>
        <button onclick="document.getElementById('bulk-tag-input').value='Hot Lead';bulkTag('add')" class="btn btn-ghost btn-sm" style="font-size:11px;">Hot Lead</button>
        <button onclick="document.getElementById('bulk-tag-input').value='Follow Up';bulkTag('add')" class="btn btn-ghost btn-sm" style="font-size:11px;">Follow Up</button>
        <button onclick="document.getElementById('bulk-tag-input').value='Priority';bulkTag('add')" class="btn btn-ghost btn-sm" style="font-size:11px;">Priority</button>
      </div>
      <button onclick="toggleBulkMode()" class="btn btn-ghost btn-sm" style="margin-left:auto;font-size:13px;">Cancel</button>
    </div>

    <script>
    let bulkMode = false;
    function toggleBulkMode() {{
      bulkMode = !bulkMode;
      const boxes = document.querySelectorAll('.bulk-sel');
      const bar = document.getElementById('bulk-bar');
      const btn = document.getElementById('bulk-btn');
      boxes.forEach(b => {{ b.style.display = bulkMode ? 'block' : 'none'; b.checked = false; }});
      bar.style.display = bulkMode ? 'flex' : 'none';
      btn.textContent = bulkMode ? '\u2716 Cancel' : '\u2611 Select';
      updateBulkCount();
    }}
    document.addEventListener('change', function(e) {{
      if (e.target.classList.contains('bulk-sel')) updateBulkCount();
    }});
    function updateBulkCount() {{
      const n = document.querySelectorAll('.bulk-sel:checked').length;
      document.getElementById('bulk-count').textContent = n + ' selected';
    }}
    function bulkTag(action) {{
      const tag = document.getElementById('bulk-tag-input').value.trim();
      if (!tag) return alert('Enter a tag name first.');
      const ids = [...document.querySelectorAll('.bulk-sel:checked')].map(b => parseInt(b.dataset.cid));
      if (!ids.length) return alert('Select at least one contact.');
      fetch('/api/contacts/bulk-tag', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ids, tag, action}})
      }}).then(r => r.json()).then(d => {{
        if (d.ok) location.reload();
        else alert(d.error || 'Error');
      }}).catch(() => alert('Network error'));
    }}
    </script>
    """, active_page="contacts", wide=True)


@app.route("/contacts/add", methods=["POST"])
def contacts_add():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import upsert_contact
    email = request.form.get("email", "").strip()
    if not email:
        session.setdefault("_flashes", []).append(("error", "Email is required."))
        return redirect(url_for("contacts_page"))
    upsert_contact(
        session["client_id"], email,
        name=request.form.get("name", "").strip(),
        company=request.form.get("company", "").strip(),
        role=request.form.get("role", "").strip(),
        relationship=request.form.get("relationship", "").strip(),
        notes=request.form.get("notes", "").strip(),
        personality=request.form.get("personality", "").strip(),
        tags=request.form.get("tags", "").strip(),
        language=request.form.get("language", "").strip(),
    )
    session.setdefault("_flashes", []).append(("success", f"Contact {email} saved."))
    return redirect(url_for("contacts_page"))


@app.route("/contacts/<int:contact_id>")
def contact_detail(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_contact, get_contact_email_history

    contact = get_contact(contact_id, session["client_id"])
    if not contact:
        session.setdefault("_flashes", []).append(("error", "Contact not found."))
        return redirect(url_for("contacts_page"))

    history = get_contact_email_history(session["client_id"], contact["email"])
    initials = (contact["name"][:1] if contact["name"] else contact["email"][:1]).upper()

    # Check if this contact's emails are already marked important
    all_important = all(h.get("priority") == "important" for h in history) if history else False

    # Build email history
    history_html = ""
    for h in history:
        pri_colors = {"urgent": "badge-red", "important": "badge-yellow", "normal": "badge-blue", "low": "badge-gray"}
        pb = pri_colors.get(h["priority"], "badge-blue")
        history_html += f"""
        <a href="/mail-hub/{h['id']}" class="card" style="text-decoration:none;color:inherit;padding:14px 20px;display:block;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div style="flex:1;">
              <div style="font-weight:600;font-size:14px;">{_esc(h['subject'] or '(no subject)')}</div>
              <div style="font-size:13px;color:var(--text-secondary);margin-top:3px;max-height:18px;overflow:hidden;">{_esc(h['body_preview'][:100])}</div>
              {'<div style="font-size:12px;color:var(--primary);margin-top:3px;font-style:italic;">' + _esc(h['ai_summary']) + '</div>' if h.get('ai_summary') else ''}
            </div>
            <div style="text-align:right;flex-shrink:0;margin-left:16px;">
              <span class="badge {pb}" style="font-size:10px;">{h['priority']}</span>
              <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{h['received_at'][:10] if h.get('received_at') else ''}</div>
            </div>
          </div>
        </a>"""

    if not history_html:
        history_html = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:14px;">No emails from this contact yet.</div>'

    # Tags display
    tags_html = ""
    if contact.get("tags"):
        for tg in contact["tags"].split(","):
            tg = tg.strip()
            if tg:
                tags_html += f'<span class="badge badge-gray" style="font-size:12px;">{_esc(tg)}</span> '

    rel_badge = f'<span class="badge badge-blue" style="font-size:14px;">{_esc(contact["relationship"].title())}</span>' if contact.get("relationship") else ""

    return _render("Contact", f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/contacts">{t("contacts.title")}</a> / {_esc(contact['name'] or contact['email'])}</div>

    <div style="display:grid;grid-template-columns:1fr 400px;gap:24px;align-items:start;">
      <!-- Left: Profile -->
      <div>
        <div class="card" style="padding:0;overflow:hidden;">
          <!-- Header -->
          <div style="padding:32px;background:linear-gradient(135deg,var(--primary-light),#F0EAFF);border-bottom:1px solid var(--border-light);">
            <div style="display:flex;align-items:center;gap:20px;">
              <div style="width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:28px;flex-shrink:0;">
                {initials}
              </div>
              <div>
                <h1 style="font-size:26px;font-weight:700;">{_esc(contact['name'] or contact['email'])}</h1>
                <div style="font-size:14px;color:var(--text-muted);margin-top:2px;">{_esc(contact['email'])}</div>
                <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">
                  {rel_badge}
                  {tags_html}
                </div>
              </div>
            </div>
          </div>

          <!-- Details -->
          <div style="padding:24px 32px;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Company</div>
                <div style="font-size:15px;">{_esc(contact['company']) or '<span style="color:var(--text-muted);">Not set</span>'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Role</div>
                <div style="font-size:15px;">{_esc(contact['role']) or '<span style="color:var(--text-muted);">Not set</span>'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Added</div>
                <div style="font-size:15px;">{contact['created_at'][:10] if contact.get('created_at') else '—'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Last Contacted</div>
                <div style="font-size:15px;">{contact['last_contacted'][:10] if contact.get('last_contacted') else 'Never'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Language</div>
                <div style="font-size:15px;">{_esc(contact.get('language','')) or '<span style="color:var(--text-muted);">Not set</span>'}</div>
              </div>
            </div>

            {'<div style="margin-top:20px;"><div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Notes</div><div style="font-size:14px;line-height:1.6;color:var(--text-secondary);background:var(--bg);padding:14px;border-radius:var(--radius-xs);">' + _esc(contact['notes']).replace(chr(10), '<br>') + '</div></div>' if contact.get('notes') else ''}

            {'<div style="margin-top:16px;"><div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">&#129504; AI Personality Guide</div><div style="font-size:14px;line-height:1.6;color:var(--primary);font-style:italic;background:var(--primary-light);padding:14px;border-radius:var(--radius-xs);">' + _esc(contact['personality']) + '</div></div>' if contact.get('personality') else ''}
          </div>
        </div>

        <!-- Back -->
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <a href="/contacts" class="btn btn-ghost" style="font-size:14px;">&#8592; Back to Contacts</a>
          <button class="btn {'btn-ghost' if all_important else 'btn-yellow'} btn-sm" onclick="toggleImportant({contact['id']}, {'true' if all_important else 'false'})" id="mark-imp-btn" title="{'Remove important priority' if all_important else 'Mark all emails from this contact as important'}">{'&#10003; Marked Important' if all_important else '&#11088; Mark All Emails Important'}</button>
        </div>
      </div>

      <!-- Right column -->
      <div style="position:sticky;top:76px;">
        <!-- Edit card -->
        <div class="card" style="padding:24px;margin-bottom:16px;">
          <h3 style="font-size:16px;font-weight:700;margin-bottom:14px;">&#9998; Edit Contact</h3>
          <form method="POST" action="/contacts/{contact['id']}/edit">
            <div class="form-group"><label>Name</label><input name="name" value="{_esc(contact['name'])}" style="font-size:14px;"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
              <div class="form-group"><label>Company</label><input name="company" value="{_esc(contact['company'])}" style="font-size:14px;"></div>
              <div class="form-group"><label>Role</label><input name="role" value="{_esc(contact['role'])}" style="font-size:14px;"></div>
            </div>
            <div class="form-group"><label>Relationship</label>
              <select name="relationship" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="client" {'selected' if contact.get('relationship')=='client' else ''}>Client</option>
                <option value="colleague" {'selected' if contact.get('relationship')=='colleague' else ''}>Colleague</option>
                <option value="vendor" {'selected' if contact.get('relationship')=='vendor' else ''}>Vendor</option>
                <option value="lead" {'selected' if contact.get('relationship')=='lead' else ''}>Lead</option>
                <option value="friend" {'selected' if contact.get('relationship')=='friend' else ''}>Friend</option>
                <option value="other" {'selected' if contact.get('relationship')=='other' else ''}>Other</option>
              </select>
            </div>
            <div class="form-group"><label>Tags</label><input name="tags" value="{_esc(contact['tags'])}" placeholder="comma,separated" style="font-size:14px;"></div>
            <div class="form-group"><label>Language</label>
              <select name="language" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="English" {'selected' if contact.get('language')=='English' else ''}>English</option>
                <option value="Spanish" {'selected' if contact.get('language')=='Spanish' else ''}>Spanish</option>
                <option value="French" {'selected' if contact.get('language')=='French' else ''}>French</option>
                <option value="Portuguese" {'selected' if contact.get('language')=='Portuguese' else ''}>Portuguese</option>
                <option value="German" {'selected' if contact.get('language')=='German' else ''}>German</option>
                <option value="Italian" {'selected' if contact.get('language')=='Italian' else ''}>Italian</option>
                <option value="Dutch" {'selected' if contact.get('language')=='Dutch' else ''}>Dutch</option>
                <option value="Japanese" {'selected' if contact.get('language')=='Japanese' else ''}>Japanese</option>
                <option value="Chinese" {'selected' if contact.get('language')=='Chinese' else ''}>Chinese</option>
                <option value="Korean" {'selected' if contact.get('language')=='Korean' else ''}>Korean</option>
                <option value="Arabic" {'selected' if contact.get('language')=='Arabic' else ''}>Arabic</option>
                <option value="Hindi" {'selected' if contact.get('language')=='Hindi' else ''}>Hindi</option>
                <option value="Russian" {'selected' if contact.get('language')=='Russian' else ''}>Russian</option>
                <option value="Other" {'selected' if contact.get('language')=='Other' else ''}>Other</option>
              </select>
            </div>
            <div class="form-group"><label>Notes</label><textarea name="notes" rows="3" style="font-size:14px;">{_esc(contact['notes'])}</textarea></div>
            <div class="form-group"><label>Personality / Communication Style</label><textarea name="personality" rows="2" style="font-size:14px;" placeholder="e.g. 'Direct, prefers bullet points, casual tone'">{_esc(contact['personality'])}</textarea></div>
            <button type="submit" class="btn btn-primary" style="width:100%;font-size:14px;">Save Changes</button>
          </form>
          <form method="POST" action="/contacts/{contact['id']}/delete" style="margin-top:8px;" onsubmit="return confirm('Delete this contact?')">
            <button type="submit" class="btn btn-ghost" style="width:100%;font-size:13px;color:var(--red);">&#128465; Delete Contact</button>
          </form>
        </div>

        <!-- Email History -->
        <div class="card" style="padding:0;overflow:hidden;">
          <div style="padding:16px 20px;border-bottom:1px solid var(--border-light);">
            <h3 style="font-size:16px;font-weight:700;">&#128236; Email History ({len(history)})</h3>
          </div>
          <div style="max-height:400px;overflow-y:auto;">
            {history_html}
          </div>
        </div>
      </div>
    </div>
    <script>
    function toggleImportant(contactId, isCurrentlyImportant) {{
      const btn = document.getElementById('mark-imp-btn');
      btn.disabled = true;
      btn.textContent = 'Updating...';
      const newPriority = isCurrentlyImportant ? 'normal' : 'important';
      fetch('/api/contacts/' + contactId + '/mark-important', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{priority: newPriority}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) {{
          if (newPriority === 'important') {{
            btn.textContent = '\u2705 ' + data.updated + ' emails marked important';
            btn.style.background = 'var(--green)';
          }} else {{
            btn.textContent = '\u2705 ' + data.updated + ' emails set to normal';
            btn.style.background = 'var(--card)';
          }}
          setTimeout(() => location.reload(), 1200);
        }} else {{
          btn.textContent = 'Error';
          btn.disabled = false;
        }}
      }}).catch(() => {{
        btn.textContent = 'Error';
        btn.disabled = false;
      }});
    }}
    </script>
    """, active_page="contacts", wide=True)


@app.route("/contacts/<int:contact_id>/edit", methods=["POST"])
def contacts_book_edit(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import update_contact
    update_contact(
        contact_id, session["client_id"],
        name=request.form.get("name", "").strip(),
        company=request.form.get("company", "").strip(),
        role=request.form.get("role", "").strip(),
        relationship=request.form.get("relationship", "").strip(),
        notes=request.form.get("notes", "").strip(),
        personality=request.form.get("personality", "").strip(),
        tags=request.form.get("tags", "").strip(),
        language=request.form.get("language", "").strip(),
    )
    session.setdefault("_flashes", []).append(("success", "Contact updated."))
    return redirect(url_for("contact_detail", contact_id=contact_id))


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
def contacts_book_delete(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import delete_contact_book
    delete_contact_book(contact_id, session["client_id"])
    session.setdefault("_flashes", []).append(("success", "Contact deleted."))
    return redirect(url_for("contacts_page"))


@app.route("/api/contacts/quick-save", methods=["POST"])
def api_contact_quick_save():
    """Quick-save a contact from the Mail Hub detail page."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import upsert_contact, get_contact_by_email
    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "email required"}), 400
    upsert_contact(
        session["client_id"],
        data["email"],
        name=data.get("name", ""),
    )
    c = get_contact_by_email(session["client_id"], data["email"])
    return jsonify({"id": c["id"] if c else None})


@app.route("/api/contacts/bulk-tag", methods=["POST"])
def api_contacts_bulk_tag():
    """Add or remove a tag from multiple contacts at once."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_contact, update_contact
    data = request.get_json()
    if not data:
        return jsonify({"error": "invalid request"}), 400
    ids = data.get("ids", [])
    tag = data.get("tag", "").strip()
    action = data.get("action", "add")  # "add" or "remove"
    if not tag or not ids:
        return jsonify({"error": "tag and ids required"}), 400
    updated = 0
    for cid in ids:
        c = get_contact(int(cid), session["client_id"])
        if not c:
            continue
        existing = set(t.strip() for t in (c.get("tags") or "").split(",") if t.strip())
        if action == "add":
            existing.add(tag)
        elif action == "remove":
            existing.discard(tag)
        new_tags = ",".join(sorted(existing))
        update_contact(int(cid), session["client_id"],
                       name=c["name"], company=c["company"], role=c["role"],
                       relationship=c["relationship"], notes=c["notes"],
                       personality=c.get("personality", ""), tags=new_tags)
        updated += 1
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/contacts/<int:contact_id>/mark-important", methods=["POST"])
def api_contact_mark_important(contact_id):
    """Mark all emails from this contact as important."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_contact, mark_contact_emails_priority
    contact = get_contact(contact_id, session["client_id"])
    if not contact:
        return jsonify({"error": "not found"}), 404
    data = request.get_json() or {}
    priority = data.get("priority", "important")
    count = mark_contact_emails_priority(session["client_id"], contact["email"], priority)
    return jsonify({"ok": True, "updated": count})

@app.route("/contacts/group/<group_name>/send", methods=["GET", "POST"])
def group_send(group_name):
    """Send personalized emails directly to all contacts in a group (no campaign)."""
    if not _logged_in():
        return redirect(url_for("login"))
    from urllib.parse import unquote
    group_name = unquote(group_name)
    from outreach.db import get_contacts_by_group, get_default_email_account, filter_suppressed
    data_cid = _effective_client_id()
    contacts = get_contacts_by_group(data_cid, group_name)

    if not contacts:
        flash(("error", f"No contacts found in group '{group_name}'."))
        return redirect(url_for("contacts_page"))

    if request.method == "POST":
        subject_tpl = request.form.get("subject", "").strip()
        body_tpl = request.form.get("body", "").strip()
        use_ai = request.form.get("use_ai") == "1"
        ai_idea = request.form.get("ai_idea", "").strip()
        schedule_date = request.form.get("schedule_date", "").strip()
        schedule_time = request.form.get("schedule_time", "").strip() or "09:00"

        # AI generate a single email template
        if use_ai and ai_idea:
            try:
                from outreach.ai import generate_sequence
                tone = request.form.get("tone", "professional")
                seq = generate_sequence(ai_idea, group_name, tone=tone, num_steps=1)
                if seq:
                    subject_tpl = seq[0].get("subject_a", "")
                    body_tpl = seq[0].get("body_a", "")
            except Exception as e:
                flash(("error", f"AI generation failed: {e}"))
                return redirect(request.url)

        if not subject_tpl or not body_tpl:
            flash(("error", "Subject and body are required."))
            return redirect(request.url)

        # Collect file attachments
        uploaded_files = request.files.getlist("attachments")
        attachment_data = []
        for f in uploaded_files:
            if f and f.filename:
                attachment_data.append({
                    "filename": f.filename,
                    "content": f.read(),
                    "content_type": f.content_type or "application/octet-stream",
                })

        # Resolve SMTP credentials
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        try:
            acct = get_default_email_account(session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]
        except Exception:
            pass

        if not smtp_user or not smtp_pw:
            flash(("error", "No email account configured. Set up SMTP credentials first."))
            return redirect(request.url)

        # Filter out suppressed/unsubscribed emails (CAN-SPAM compliance)
        allowed_emails = filter_suppressed(data_cid, [c["email"] for c in contacts])
        original_count = len(contacts)
        contacts = [c for c in contacts if c["email"] in allowed_emails]
        suppressed_count = original_count - len(contacts)
        if not contacts:
            flash(("error", f"All {original_count} contacts are on the suppression list (unsubscribed)."))
            return redirect(request.url)

        # Build CAN-SPAM footer with unsubscribe link + physical address
        import hashlib
        client = get_client(session["client_id"])
        physical_addr = client.get("physical_address", "") if client else ""

        def _build_unsub_footer(to_email):
            token = hashlib.sha256(f"{data_cid}:{to_email}:{app.secret_key}".encode()).hexdigest()[:16]
            from outreach.config import BASE_URL
            unsub_url = f"{BASE_URL}/unsubscribe/g/{token}?e={to_email}&c={data_cid}"
            addr_line = f'<div style="color:#A0AEC0;font-size:10px;margin-top:4px;">{physical_addr}</div>' if physical_addr else ''
            return (f'<div style="text-align:center;padding:16px 0 8px;margin-top:20px;border-top:1px solid #E2E8F0;font-size:11px;color:#A0AEC0;">'
                    f'<a href="{unsub_url}" style="color:#A0AEC0;font-size:11px;text-decoration:underline;">Unsubscribe</a>'
                    f'{addr_line}</div>'), unsub_url

        # If scheduled, store in scheduled_emails table for the worker to send later
        if schedule_date:
            from outreach.db import create_scheduled_email
            from datetime import datetime as _dt_cls, timedelta
            # Convert local time to UTC using browser-provided timezone offset
            tz_offset_min = request.form.get("tz_offset", "")
            try:
                local_dt = _dt_cls.strptime(f"{schedule_date} {schedule_time}:00", "%Y-%m-%d %H:%M:%S")
                if tz_offset_min:
                    # tz_offset is in minutes ahead of UTC (e.g. -240 for UTC-4)
                    utc_dt = local_dt + timedelta(minutes=int(tz_offset_min))
                    utc_str = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    utc_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                utc_str = f"{schedule_date} {schedule_time}:00"

            scheduled_count = 0
            for c in contacts:
                psubject = subject_tpl
                pbody = body_tpl
                for old, new in {
                    "{{name}}": c.get("name") or "there", "{{company}}": c.get("company") or "your company",
                    "{{role}}": c.get("role") or "", "{name}": c.get("name") or "there",
                    "{company}": c.get("company") or "your company", "{role}": c.get("role") or "",
                }.items():
                    psubject = psubject.replace(old, new)
                    pbody = pbody.replace(old, new)
                # Note: attachments not supported for scheduled emails (worker limitation)
                create_scheduled_email(
                    session["client_id"], to_email=c["email"], subject=psubject,
                    body=pbody, scheduled_at=utc_str, to_name=c.get("name", ""),
                )
                scheduled_count += 1
            flash(("success", f"Scheduled {scheduled_count} email{'s' if scheduled_count != 1 else ''} for {schedule_date} {schedule_time}"))
            return redirect(url_for("contacts_page"))

        # Send immediately with attachments
        import smtplib as _smtplib
        from email.mime.text import MIMEText as _MIMEText
        from email.mime.multipart import MIMEMultipart as _MMP
        from email.mime.base import MIMEBase as _MIMEBase
        from email import encoders as _encoders
        sent_count = 0
        fail_count = 0
        for c in contacts:
            try:
                psubject = subject_tpl
                pbody = body_tpl
                replacements = {
                    "{{name}}": c.get("name") or "there",
                    "{{company}}": c.get("company") or "your company",
                    "{{role}}": c.get("role") or "",
                    "{name}": c.get("name") or "there",
                    "{company}": c.get("company") or "your company",
                    "{role}": c.get("role") or "",
                }
                for old, new in replacements.items():
                    psubject = psubject.replace(old, new)
                    pbody = pbody.replace(old, new)

                msg = _MMP("mixed")
                msg["Subject"] = psubject
                msg["From"] = smtp_user
                msg["To"] = c["email"]

                # CAN-SPAM: unsubscribe headers (RFC 8058)
                unsub_footer, unsub_url = _build_unsub_footer(c["email"])
                msg["List-Unsubscribe"] = f"<{unsub_url}>"
                msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

                # Text + HTML body with CAN-SPAM footer
                body_part = _MMP("alternative")
                plain_unsub = f"\n\n---\nUnsubscribe: {unsub_url}" + (f"\n{physical_addr}" if physical_addr else "")
                body_part.attach(_MIMEText(pbody + plain_unsub, "plain", "utf-8"))
                body_html = pbody.replace("\\n", "<br>").replace("\n", "<br>")
                body_part.attach(_MIMEText(f'<div style="font-family:sans-serif;font-size:14px;line-height:1.6;">{body_html}{unsub_footer}</div>', "html", "utf-8"))
                msg.attach(body_part)

                # Attachments
                for att in attachment_data:
                    part = _MIMEBase("application", "octet-stream")
                    part.set_payload(att["content"])
                    _encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f'attachment; filename="{att["filename"]}"')
                    msg.attach(part)

                if smtp_port == 587:
                    with _smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                        srv.starttls()
                        srv.login(smtp_user, smtp_pw)
                        srv.send_message(msg)
                else:
                    with _smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
                        srv.login(smtp_user, smtp_pw)
                        srv.send_message(msg)
                sent_count += 1
            except Exception as e:
                print(f"[GROUP SEND] Failed to send to {c['email']}: {e}", flush=True)
                fail_count += 1

        if sent_count:
            suppressed_msg = f" ({suppressed_count} skipped — unsubscribed)" if suppressed_count else ""
            flash(("success", f"Sent {sent_count} email{'s' if sent_count != 1 else ''} to group '{group_name}'!{suppressed_msg}" + (f" ({fail_count} failed)" if fail_count else "")))
        else:
            flash(("error", f"All {fail_count} emails failed to send."))
        return redirect(url_for("contacts_page"))

    # GET — show the send form
    contact_rows = ""
    for c in contacts:
        contact_rows += f'<tr><td>{_esc(c["name"] or "-")}</td><td>{_esc(c["email"])}</td><td>{_esc(c.get("company", "") or "-")}</td></tr>'

    return _render(f"Send to {_esc(group_name)}", f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/contacts">Contacts</a> / Send to {_esc(group_name)}</div>
    <h1 style="font-size:28px;">&#9993; Send to Group: {_esc(group_name)}</h1>
    <p class="subtitle">{len(contacts)} contact{"s" if len(contacts) != 1 else ""} in this group. Each email is personalized with their name, company, and role.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:24px;">
      <!-- AI Generate -->
      <div class="card" style="padding:28px;">
        <h3 style="font-size:18px;margin-bottom:16px;">&#129302; AI Generate</h3>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">Describe your email idea and AI writes it. Each contact gets a personalized copy.</p>
        <form method="POST" enctype="multipart/form-data">
          <input type="hidden" name="use_ai" value="1">
          <div class="form-group">
            <label>Email Idea *</label>
            <textarea name="ai_idea" rows="4" placeholder="e.g. Invite everyone to Friday's team standup at 3pm..." style="font-size:14px;" required></textarea>
          </div>
          <div class="form-group">
            <label>Tone</label>
            <select name="tone" style="font-size:14px;">
              <option value="professional">Professional</option>
              <option value="friendly" selected>Friendly</option>
              <option value="casual">Casual</option>
              <option value="formal">Formal</option>
            </select>
          </div>
          <div class="form-group">
            <label>&#128206; Attachments <span style="font-weight:400;color:var(--text-muted);">(optional)</span></label>
            <input type="file" name="attachments" multiple style="font-size:13px;">
          </div>
          <div class="form-group">
            <label>&#128340; Schedule <span style="font-weight:400;color:var(--text-muted);">(leave empty to send now)</span></label>
            <div style="display:flex;gap:8px;">
              <input type="date" name="schedule_date" style="font-size:13px;flex:1;">
              <input type="time" name="schedule_time" value="09:00" style="font-size:13px;width:100px;">
            </div>
            <input type="hidden" name="tz_offset" class="tz-offset-input">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;font-size:15px;">&#129302; Generate &amp; Send</button>
        </form>
      </div>

      <!-- Manual Compose -->
      <div class="card" style="padding:28px;">
        <h3 style="font-size:18px;margin-bottom:16px;">&#9997; Write Yourself</h3>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">Use <code>{{{{name}}}}</code>, <code>{{{{company}}}}</code>, <code>{{{{role}}}}</code> and each contact gets their own personalized version.</p>
        <form method="POST" enctype="multipart/form-data">
          <input type="hidden" name="use_ai" value="0">
          <div class="form-group">
            <label>Subject *</label>
            <input name="subject" placeholder="e.g. Hey {{{{name}}}}, quick update" style="font-size:14px;" required>
          </div>
          <div class="form-group">
            <label>Body *</label>
            <textarea name="body" rows="8" placeholder="Hi {{{{name}}}},&#10;&#10;Just wanted to let you know..." style="font-size:14px;" required></textarea>
          </div>
          <div class="form-group">
            <label>&#128206; Attachments <span style="font-weight:400;color:var(--text-muted);">(optional)</span></label>
            <input type="file" name="attachments" multiple style="font-size:13px;">
          </div>
          <div class="form-group">
            <label>&#128340; Schedule <span style="font-weight:400;color:var(--text-muted);">(leave empty to send now)</span></label>
            <div style="display:flex;gap:8px;">
              <input type="date" name="schedule_date" style="font-size:13px;flex:1;">
              <input type="time" name="schedule_time" value="09:00" style="font-size:13px;width:100px;">
            </div>
            <input type="hidden" name="tz_offset" class="tz-offset-input">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;font-size:15px;">&#9993; Send</button>
        </form>
      </div>
    </div>

    <!-- Contacts in this group -->
    <div class="card" style="padding:24px;margin-top:24px;">
      <h3 style="font-size:16px;margin-bottom:12px;">&#128101; Contacts in this group ({len(contacts)})</h3>
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <thead><tr style="border-bottom:2px solid var(--border-light);">
          <th style="text-align:left;padding:8px;">Name</th>
          <th style="text-align:left;padding:8px;">Email</th>
          <th style="text-align:left;padding:8px;">Company</th>
        </tr></thead>
        <tbody>{contact_rows}</tbody>
      </table>
    </div>
    <script>document.querySelectorAll('.tz-offset-input').forEach(function(el){{el.value=new Date().getTimezoneOffset();}});</script>
    """, active_page="contacts")


# ---------------------------------------------------------------------------
# Routes — Billing & PayPal
# ---------------------------------------------------------------------------

@app.route("/billing")
def billing_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_subscription, get_usage, check_limit
    from outreach.config import PLAN_LIMITS, PAYPAL_CLIENT_ID, PAYPAL_PLAN_GROWTH, PAYPAL_PLAN_PRO, PAYPAL_PLAN_UNLIMITED

    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    # Flash messages for redirect params
    if request.args.get("upgraded"):
        flash(("success", "Payment successful! Your plan has been upgraded."))
    if request.args.get("canceled"):
        flash(("info", "Checkout canceled. No changes made."))

    # Build usage bars
    def _bar(used, limit, label):
        if limit == -1:
            return f'<div class="usage-row"><span class="usage-label">{label}</span><span class="usage-val">{used} used &middot; <b>Unlimited</b></span><div class="progress-wrap" style="width:100%;margin-top:4px;"><div class="progress-bar bar-green" style="width:0%"></div></div></div>'
        pct = min(used / limit * 100, 100) if limit else 100
        color = "bar-green" if pct < 75 else ("bar-yellow" if pct < 90 else "bar-red")
        return f'<div class="usage-row"><span class="usage-label">{label}</span><span class="usage-val">{used} / {limit:,}</span><div class="progress-wrap" style="width:100%;margin-top:4px;"><div class="progress-bar {color}" style="width:{pct:.0f}%"></div></div></div>'

    usage_html = _bar(usage.get("emails_sent", 0), limits["emails_per_month"], "Emails Sent")
    usage_html += _bar(usage.get("mail_hub_syncs", 0), limits["mail_hub_syncs"], "Mail Hub Syncs")

    # Plan cards with PayPal buttons
    plan_order = ["free", "growth", "pro", "unlimited"]
    plan_labels = {"free": "Free", "growth": "Growth", "pro": "Pro", "unlimited": "Unlimited"}
    plan_prices = {"free": "$0", "growth": "$8", "pro": "$20", "unlimited": "$40"}
    plan_ids = {"growth": PAYPAL_PLAN_GROWTH, "pro": PAYPAL_PLAN_PRO, "unlimited": PAYPAL_PLAN_UNLIMITED}
    plan_features = {
        "free": ["200 emails/month", "1 mailbox", "2 campaigns", "10 Mail Hub syncs/month", "Basic analytics"],
        "growth": ["2,000 emails/month", "3 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs", "AI email classification", "Reply sentiment analysis"],
        "pro": ["10,000 emails/month", "5 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs", "AI everything", "Priority support", "A/B testing insights"],
        "unlimited": ["Unlimited emails", "Unlimited mailboxes", "Unlimited everything", "AI everything", "Priority support", "CSV export", "All future features"],
    }

    cards = ""
    for p in plan_order:
        is_current = (p == plan)
        badge = ' <span class="badge badge-green">Current</span>' if is_current else ""
        features_html = "".join(f"<li>{f}</li>" for f in plan_features[p])

        if is_current:
            btn = '<button class="btn btn-outline btn-sm" disabled style="width:100%;justify-content:center;opacity:0.5;">Current Plan</button>'
        elif p == "free":
            btn = '<form method="post" action="/billing/downgrade"><button class="btn btn-outline btn-sm" style="width:100%;justify-content:center;">Downgrade</button></form>'
        else:
            pp_id = plan_ids.get(p, "")
            btn = f'<div id="paypal-btn-{p}" style="margin-top:8px;"></div>' if pp_id else '<button class="btn btn-outline btn-sm" disabled style="width:100%;">Not available</button>'

        border = "border:2px solid var(--primary);" if is_current else ""
        cards += f"""
        <div class="card" style="flex:1;min-width:220px;max-width:280px;{border}">
          <div style="padding:20px;text-align:center;">
            <h3 style="margin:0;">{plan_labels[p]}{badge}</h3>
            <div style="font-size:36px;font-weight:800;margin:12px 0;">{plan_prices[p]}<span style="font-size:14px;font-weight:400;color:var(--text-muted);"> USD/mo</span></div>
            <ul style="text-align:left;list-style:none;padding:0;margin:16px 0;">
              {features_html}
            </ul>
            {btn}
          </div>
        </div>"""

    status_badge = {"active": "badge-green", "past_due": "badge-yellow", "canceled": "badge-red"}.get(sub.get("status", "active"), "badge-gray")

    # PayPal JS SDK script + button renderers
    paypal_script = ""
    if PAYPAL_CLIENT_ID:
        paypal_buttons_js = ""
        for p_name, p_id in plan_ids.items():
            if p_id and p_name != plan:
                paypal_buttons_js += f"""
        if (document.getElementById('paypal-btn-{p_name}')) {{
          paypal.Buttons({{
            style: {{ shape: 'rect', color: 'blue', layout: 'vertical', label: 'subscribe' }},
            createSubscription: function(data, actions) {{
              return actions.subscription.create({{ plan_id: '{p_id}' }});
            }},
            onApprove: function(data) {{
              fetch('/billing/activate', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{ subscription_id: data.subscriptionID, plan: '{p_name}' }})
              }}).then(function() {{ window.location = '/billing?upgraded=1'; }});
            }},
            onCancel: function() {{ window.location = '/billing?canceled=1'; }},
            onError: function(err) {{ console.error('PayPal error:', err); alert('Payment error. Please try again.'); }}
          }}).render('#paypal-btn-{p_name}');
        }}
"""
        paypal_script = f"""
    <script src="https://www.paypal.com/sdk/js?client-id={PAYPAL_CLIENT_ID}&vault=true&intent=subscription" data-sdk-integration-source="button-factory"></script>
    <script>{paypal_buttons_js}</script>
    """

    return _render(t("billing.title"), f"""
    <div class="page-header">
      <h1>&#128179; {t("billing.title")}</h1>
    </div>

    <div style="background:linear-gradient(135deg,#F59E0B,#D97706);color:#fff;padding:16px 24px;border-radius:10px;margin-bottom:24px;text-align:center;">
      <div style="font-size:18px;font-weight:700;margin-bottom:4px;">&#128679; Testing Mode</div>
      <div style="font-size:14px;opacity:0.95;">Subscriptions and paid plans are <b>not available</b> during the beta period. All features are currently free. We'll announce when plans go live!</div>
    </div>

    <div class="card" style="margin-bottom:24px;">
      <div class="card-header"><h2>{t("billing.current_usage")}</h2><span class="badge {status_badge}">{sub.get('status', 'active').replace('_', ' ').title()}</span></div>
      <div style="padding:20px;">
        {usage_html}
      </div>
    </div>

    <h2 style="margin-bottom:16px;">{t("billing.choose_plan")}</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap;justify-content:center;margin-bottom:32px;">
      {cards}
    </div>

    {paypal_script}

    <style>
      .usage-row {{ margin-bottom:16px; }}
      .usage-label {{ font-weight:600;font-size:14px; }}
      .usage-val {{ float:right;font-size:13px;color:var(--text-muted); }}
      .bar-red {{ background:var(--red, #EF4444) !important; }}
      ul li {{ padding:6px 0;font-size:14px;color:var(--text-muted);border-bottom:1px solid var(--border); }}
      ul li:last-child {{ border-bottom:none; }}
    </style>
    """, active_page="billing")


@app.route("/billing/activate", methods=["POST"])
@limiter.limit("5 per minute")
def billing_activate():
    """Activate a PayPal subscription after user approval."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    pp_sub_id = data.get("subscription_id", "")
    plan = data.get("plan", "")

    if not pp_sub_id or plan not in ("growth", "pro", "unlimited"):
        return jsonify({"error": "invalid"}), 400

    from outreach.db import update_subscription
    update_subscription(session["client_id"],
                        plan=plan,
                        stripe_subscription_id=pp_sub_id,
                        status="active")
    print(f"[PAYPAL] Client {session['client_id']} upgraded to {plan} (sub={pp_sub_id})")
    return jsonify({"ok": True})


@app.route("/billing/downgrade", methods=["POST"])
@limiter.limit("5 per minute")
def billing_downgrade():
    """Cancel PayPal subscription and revert to free plan."""
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.config import PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_MODE
    from outreach.db import get_subscription, update_subscription

    sub = get_subscription(session["client_id"])
    pp_sub_id = sub.get("stripe_subscription_id")

    if pp_sub_id and PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET:
        try:
            import requests as rq
            base = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
            # Get access token
            auth_resp = rq.post(f"{base}/v1/oauth2/token",
                                auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
                                data={"grant_type": "client_credentials"},
                                headers={"Accept": "application/json"})
            token = auth_resp.json().get("access_token", "")
            if token:
                rq.post(f"{base}/v1/billing/subscriptions/{pp_sub_id}/cancel",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"reason": "Customer requested downgrade"})
        except Exception as e:
            print(f"[PAYPAL] Cancel error: {e}")

    update_subscription(session["client_id"], plan="free", stripe_subscription_id="", status="active")
    flash(("success", "Downgraded to Free plan."))
    return redirect(url_for("billing_page"))


@app.route("/paypal/webhook", methods=["POST"])
@csrf.exempt
def paypal_webhook():
    """Handle PayPal webhook events for subscription lifecycle."""
    import json

    try:
        body = json.loads(request.get_data())
    except Exception:
        return "Bad JSON", 400

    # Verify webhook signature with PayPal API (mandatory)
    from outreach.config import PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_WEBHOOK_ID, PAYPAL_MODE
    if not PAYPAL_WEBHOOK_ID or not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        print("[PAYPAL] Webhook rejected — credentials not configured")
        return "Webhook not configured", 500
    try:
        import requests as rq
        base = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
        auth_resp = rq.post(f"{base}/v1/oauth2/token",
                            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
                            data={"grant_type": "client_credentials"},
                            headers={"Accept": "application/json"})
        token = auth_resp.json().get("access_token", "")
        if not token:
            print("[PAYPAL] Could not obtain access token for webhook verification")
            return "Verification failed", 500
        verify_resp = rq.post(f"{base}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "auth_algo": request.headers.get("PAYPAL-AUTH-ALGO", ""),
                "cert_url": request.headers.get("PAYPAL-CERT-URL", ""),
                "transmission_id": request.headers.get("PAYPAL-TRANSMISSION-ID", ""),
                "transmission_sig": request.headers.get("PAYPAL-TRANSMISSION-SIG", ""),
                "transmission_time": request.headers.get("PAYPAL-TRANSMISSION-TIME", ""),
                "webhook_id": PAYPAL_WEBHOOK_ID,
                "webhook_event": body,
            })
        if verify_resp.json().get("verification_status") != "SUCCESS":
            print("[PAYPAL] Webhook signature verification failed")
            return "Invalid signature", 400
    except Exception as e:
        print(f"[PAYPAL] Webhook verify error: {e}")
        return "Verification error", 500

    event_type = body.get("event_type", "")
    resource = body.get("resource", {})
    pp_sub_id = resource.get("id", "") or resource.get("billing_agreement_id", "")

    from outreach.db import update_subscription, get_subscription_by_stripe_sub

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        sub_rec = get_subscription_by_stripe_sub(pp_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], status="active")
            print(f"[PAYPAL] Subscription {pp_sub_id} activated")

    elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        sub_rec = get_subscription_by_stripe_sub(pp_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], plan="free",
                                stripe_subscription_id="", status="active")
            print(f"[PAYPAL] Client {sub_rec['client_id']} subscription cancelled -> free")

    elif event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
        sub_rec = get_subscription_by_stripe_sub(pp_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], status="past_due")
            print(f"[PAYPAL] Subscription {pp_sub_id} suspended")

    elif event_type == "BILLING.SUBSCRIPTION.EXPIRED":
        sub_rec = get_subscription_by_stripe_sub(pp_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], plan="free",
                                stripe_subscription_id="", status="active")
            print(f"[PAYPAL] Subscription {pp_sub_id} expired -> free")

    elif event_type == "PAYMENT.SALE.COMPLETED":
        pp_sub_id = resource.get("billing_agreement_id", "")
        if pp_sub_id:
            sub_rec = get_subscription_by_stripe_sub(pp_sub_id)
            if sub_rec and sub_rec.get("status") == "past_due":
                update_subscription(sub_rec["client_id"], status="active")
                print(f"[PAYPAL] Payment received, reactivated client {sub_rec['client_id']}")

    return "ok", 200


@app.route("/pricing")
def pricing_page():
    """Public pricing page."""
    plan_data = [
        ("Free", "$0", "Get started with the basics", [
            "200 emails/month", "1 mailbox", "2 campaigns", "10 Mail Hub syncs/month",
            "Open tracking", "Reply detection", "Basic analytics"
        ]),
        ("Growth", "$8", "Scale your outreach", [
            "2,000 emails/month", "3 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs",
            "AI email classification", "AI reply sentiment", "A/B testing"
        ]),
        ("Pro", "$20", "Full power for pros", [
            "10,000 emails/month", "5 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs",
            "AI everything", "Smart send times", "Priority support", "CSV export"
        ]),
        ("Unlimited", "$40", "No limits, ever", [
            "Unlimited emails", "Unlimited mailboxes", "Unlimited everything", "AI everything",
            "Priority support", "All current & future features", "Custom integrations"
        ]),
    ]

    cards = ""
    for name, price, desc, features in plan_data:
        feat_html = "".join(f"<li>&#10003; {f}</li>" for f in features)
        highlight = "border:2px solid var(--primary);" if name == "Pro" else ""
        pop = ' <span class="badge badge-green" style="font-size:10px;">POPULAR</span>' if name == "Pro" else ""
        cards += f"""
        <div class="card" style="flex:1;min-width:240px;max-width:300px;{highlight}">
          <div style="padding:24px;text-align:center;">
            <h3 style="margin:0;font-size:20px;">{name}{pop}</h3>
            <p style="color:var(--text-muted);font-size:13px;margin:4px 0 16px;">{desc}</p>
            <div style="font-size:42px;font-weight:800;">{price}<span style="font-size:14px;font-weight:400;color:var(--text-muted);"> USD/mo</span></div>
            <ul style="text-align:left;list-style:none;padding:0;margin:20px 0;">
              {feat_html}
            </ul>
            <a href="/register" class="btn btn-primary btn-sm" style="width:100%;justify-content:center;">{t("landing.start_free") if name == 'Free' else t("nav.get_started")}</a>
          </div>
        </div>"""

    return render_template_string(LAYOUT, title="Pricing", logged_in=_logged_in(),
        messages=[], active_page="pricing", client_name=session.get("client_name", ""),
        nav=t_dict("nav"), lang=session.get("lang", "en"),
        content=Markup(f"""
    <div style="max-width:1200px;margin:0 auto;padding:40px 20px;">
      <div style="text-align:center;margin-bottom:40px;">
        <h1 style="font-size:36px;margin-bottom:8px;">{t("pricing.title")}</h1>
        <p style="color:var(--text-muted);font-size:18px;">{t("pricing.subtitle")}</p>
        <div style="background:linear-gradient(135deg,#F59E0B,#D97706);color:#fff;padding:12px 24px;border-radius:10px;margin-top:16px;display:inline-block;font-size:14px;font-weight:600;">
          &#128679; We're in beta! All features are free during testing. Paid plans coming soon.
        </div>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap;justify-content:center;">
        {cards}
      </div>
      <div style="text-align:center;margin-top:32px;color:var(--text-muted);font-size:14px;">
        All plans include open tracking, reply detection, and AI-generated sequences.<br>
        Cancel anytime. No contracts.
      </div>
    </div>
    <style>
      ul li {{ padding:8px 0;font-size:14px;color:var(--text-muted);border-bottom:1px solid var(--border); }}
      ul li:last-child {{ border-bottom:none; }}
    </style>
    """))


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
      <p style="color:var(--text-muted);margin-bottom:32px;">Last updated: April 17, 2026</p>

      <div style="line-height:1.8;color:var(--text-secondary);font-size:15px;">
        <p style="background:rgba(139,92,246,.08);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:24px;"><strong>Plain-English summary:</strong> MachReach has two sides &mdash; a <em>Business</em> outreach platform and a <em>Student</em> study platform. We collect only what the features require, we never sell your data, passwords are hashed with bcrypt, email credentials are encrypted with AES-256, and you can export or delete everything at any time from Settings.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">1. Information We Collect</h2>
        <p><strong>Account information:</strong> name, email address, and password (hashed with bcrypt cost&nbsp;12 &mdash; we cannot read it).</p>
        <p><strong>Business side &mdash; email account credentials:</strong> when you connect an email account we store the email address and app password. App passwords are encrypted at rest using AES-256 (Fernet) and are never stored in plaintext.</p>
        <p><strong>Business side &mdash; email content:</strong> we access your email via IMAP solely to sync your inbox for Mail Hub and to detect replies to your outreach campaigns. We do not read, analyze, or sell your email content for advertising purposes.</p>
        <p><strong>Business side &mdash; contact data:</strong> names, emails, and companies you upload for your campaigns. You are responsible for ensuring you have proper consent or legitimate interest to contact those individuals.</p>
        <p><strong>Student side &mdash; Canvas LMS data:</strong> when you connect Canvas we fetch your courses, assignments, and exam dates via OAuth. Your Canvas access token is encrypted at rest. You can disconnect Canvas at any time in Settings, which immediately deletes the token.</p>
        <p><strong>Student side &mdash; uploaded study materials:</strong> PDFs, DOCX files, notes, and text you provide for flashcards, quizzes, AI notes, and the AI tutor. These are stored in your account and used only to generate study features for you.</p>
        <p><strong>Student side &mdash; gamification data:</strong> XP events, study streak, badges, quiz scores, flashcard reviews, and focus-session data (duration, type of timer). This feeds your level, global leaderboard, personal (fair-play) leaderboards, and progress charts.</p>
        <p><strong>Student side &mdash; study exchange:</strong> notes you explicitly mark as shared become viewable to other MachReach students. You can unshare them at any time. Other users' forks of your notes are tracked only as anonymous counters for XP.</p>
        <p><strong>Focus Shield browser extension:</strong> the Focus Guard extension runs locally in your browser. It stores your blocklist and active-session state in <code>chrome.storage</code> on your device. It does <strong>not</strong> send your browsing history to our servers.</p>
        <p><strong>Usage data:</strong> aggregate metrics about how you use MachReach (features opened, campaigns created, quizzes generated) to improve the service. We do not use third-party advertising analytics.</p>
        <p><strong>Payment data:</strong> billing is processed by PayPal. We receive only a subscription ID and status &mdash; never card numbers.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">2. How We Use Your Information</h2>
        <ul style="padding-left:20px;">
          <li>To provide the MachReach service (outreach campaigns, study plans, quizzes, chat, exchange)</li>
          <li>To send outreach emails on your behalf through <em>your</em> connected email accounts</li>
          <li>To sync and classify your inbox in Mail Hub</li>
          <li>To generate AI-powered study plans, flashcards, quizzes, and tutor answers based on your own materials</li>
          <li>To track XP, streaks, and leaderboard rankings</li>
          <li>To process payments and manage your subscription</li>
          <li>To send you service-related notifications (password resets, security alerts, daily study emails you opted into)</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">3. Data Security</h2>
        <p>We take security seriously:</p>
        <ul style="padding-left:20px;">
          <li>Passwords hashed with <strong>bcrypt</strong> (cost factor 12)</li>
          <li>Email credentials and Canvas OAuth tokens encrypted with <strong>AES-256</strong> (Fernet) at rest</li>
          <li>All connections use <strong>HTTPS/TLS</strong> (HSTS with 2-year max-age, preload-ready)</li>
          <li><strong>CSRF</strong> protection on every form and state-changing API endpoint</li>
          <li><strong>Rate limiting</strong> on authentication, password-reset, registration, and AI endpoints</li>
          <li><strong>Content-Security-Policy</strong>, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, and a strict Permissions-Policy on every response</li>
          <li>Cookies set with <strong>HttpOnly</strong>, <strong>SameSite=Lax</strong>, and <strong>Secure</strong> flags in production</li>
          <li>Parameterized SQL queries everywhere &mdash; no string concatenation &mdash; to prevent SQL injection</li>
          <li>All user-rendered text is HTML-escaped before output to prevent XSS</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">4. Data Sharing &amp; Sub-processors</h2>
        <p>We do <strong>not</strong> sell, rent, or share your personal information with third parties, except:</p>
        <ul style="padding-left:20px;">
          <li><strong>OpenAI (US):</strong> Business side &mdash; email subjects and snippets may be sent for classification and reply generation. Student side &mdash; excerpts of your uploaded study materials and your questions are sent to generate plans, quizzes, flashcards, notes, tutor answers, essay feedback, and panic-mode plans. OpenAI does not train on API data per its API data-usage policy.</li>
          <li><strong>Instructure / Canvas LMS:</strong> OAuth provider for importing your courses and exams. We request only read scopes.</li>
          <li><strong>Google (Gmail OAuth) / Microsoft (Outlook OAuth):</strong> used only if you connect those accounts for Mail Hub or email sending.</li>
          <li><strong>PayPal:</strong> processes payments. We do not store card numbers.</li>
          <li><strong>Render (US):</strong> our application server and PostgreSQL database are hosted on Render's US infrastructure.</li>
          <li><strong>Sentry:</strong> error reporting. Stack traces may include request paths but we scrub form fields and secrets.</li>
          <li><strong>Legal requirements:</strong> we may disclose information if required by law or to protect our rights.</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">5. Data Storage &amp; Location</h2>
        <p>Your data is stored on servers located in the <strong>United States</strong>, operated by Render Inc. By using MachReach, you consent to the transfer and storage of your data in the United States. We take appropriate safeguards to protect your data during international transfers (standard contractual clauses with our sub-processors where applicable).</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">6. Data Retention</h2>
        <p>Your data is retained as long as your account is active. When you delete your account, all associated data (campaigns, contacts, email accounts, synced emails, study materials, notes, quizzes, flashcards, XP history, Canvas tokens) is permanently deleted within 30 days. Encrypted backups roll off within 35 days.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7. Your Rights</h2>
        <p>You can:</p>
        <ul style="padding-left:20px;">
          <li>Access and export your data at any time via Settings &gt; Export My Data (machine-readable JSON)</li>
          <li>Update or correct your personal information in Settings</li>
          <li>Delete your account and all associated data</li>
          <li>Disconnect email or Canvas accounts at any time (credentials/tokens are immediately deleted)</li>
          <li>Unshare or delete any note you previously shared in Study Exchange</li>
          <li>Leave or delete any personal leaderboard you created</li>
          <li>Opt out of daily study-email digests in Settings</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">8. International Users (GDPR / UK&nbsp;GDPR)</h2>
        <p>If you are located in the European Economic Area, United Kingdom, or Switzerland, you have additional rights under the GDPR / UK GDPR:</p>
        <ul style="padding-left:20px;">
          <li><strong>Right of access:</strong> request a copy of your personal data</li>
          <li><strong>Right to rectification:</strong> correct inaccurate personal data</li>
          <li><strong>Right to erasure ("right to be forgotten"):</strong> request deletion of your personal data</li>
          <li><strong>Right to data portability:</strong> receive your data in JSON format</li>
          <li><strong>Right to object:</strong> object to processing of your personal data</li>
          <li><strong>Right to restrict processing:</strong> limit how we use your data</li>
          <li><strong>Right to lodge a complaint</strong> with your local data-protection authority</li>
        </ul>
        <p>Our lawful bases for processing are <strong>contract</strong> (to provide the service you signed up for), <strong>legitimate interest</strong> (security, fraud prevention, product improvement), and <strong>consent</strong> (for optional features like Canvas sync or daily emails, which you can revoke at any time).</p>
        <p>To exercise any right, email <a href="mailto:support@machreach.com">support@machreach.com</a> or use the in-app export/delete tools.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">9. California Residents (CCPA / CPRA)</h2>
        <p>California residents have the right to know what personal information we collect, to request deletion, to opt out of any "sale" or "sharing" of personal information, and to non-discrimination for exercising these rights. <strong>MachReach does not sell or share personal information</strong> as those terms are defined under the CCPA/CPRA.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">10. Children's Privacy</h2>
        <p>MachReach is intended for users <strong>16 or older</strong>. We do not knowingly collect personal information from children under 16. If you believe a child has provided us with information, contact us and we will delete it.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">11. Cookies &amp; Local Storage</h2>
        <p>We use <strong>essential cookies only</strong> for authentication and remembering your preferences (theme, language). No tracking or advertising cookies. Cookies are set with <strong>HttpOnly</strong>, <strong>SameSite=Lax</strong>, and <strong>Secure</strong> (in production). The Focus Guard extension uses <code>chrome.storage.local</code> on your device only.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">12. Breach Notification</h2>
        <p>If we become aware of a security incident that compromises your personal data, we will notify affected users by email within 72&nbsp;hours of confirmation and, where required, notify the relevant supervisory authority.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">13. Changes to This Policy</h2>
        <p>We may update this policy from time to time. We will notify you of material changes via email or an in-app notice at least 7&nbsp;days before they take effect.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">14. Governing Law</h2>
        <p>This Privacy Policy is governed by the laws of the Republic of Chile, including Ley&nbsp;19.628 on the Protection of Private Life and Ley&nbsp;21.719 (2024). For users in the EU/UK we also comply with the GDPR / UK GDPR. Any disputes will be resolved in the courts of Santiago, Chile, without prejudice to the mandatory consumer-protection rights you may have in your country of residence.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">15. Contact</h2>
        <p>Questions or data-rights requests: <a href="mailto:support@machreach.com">support@machreach.com</a>. Data Protection Contact: same address.</p>
      </div>
    </div>
    """), active_page="privacy")


@app.route("/terms")
def terms_page():
    return _render("Terms of Service", Markup("""
    <div style="max-width:800px;margin:0 auto;padding:40px 20px;">
      <h1 style="font-size:32px;margin-bottom:8px;">Terms of Service</h1>
      <p style="color:var(--text-muted);margin-bottom:32px;">Last updated: April 17, 2026</p>

      <div style="line-height:1.8;color:var(--text-secondary);font-size:15px;">
        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">1. Acceptance of Terms</h2>
        <p>By creating an account or using MachReach, you agree to these Terms of Service. If you do not agree, do not use the service.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">2. Description of Service</h2>
        <p>MachReach is a dual-purpose platform:</p>
        <ul style="padding-left:20px;">
          <li><strong>Business side:</strong> email outreach, campaign management, inbox sync via IMAP/SMTP, AI-assisted writing and reply classification.</li>
          <li><strong>Student side:</strong> Canvas LMS integration, AI-generated study plans, flashcards, practice quizzes, AI tutor, essay feedback, panic-mode cram plans, weekly schedule, focus-mode timers, XP/leaderboards, and the optional Focus Guard browser extension.</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">3. Account Responsibilities</h2>
        <ul style="padding-left:20px;">
          <li>You must provide accurate information when registering</li>
          <li>You are responsible for maintaining the security of your account credentials</li>
          <li>You must not share your account with others</li>
          <li>You must be at least 16 years old to use MachReach</li>
          <li>You are responsible for all activity under your account</li>
          <li>You must not attempt to probe, scan, or exploit vulnerabilities in the service. Responsible disclosure is welcome at <a href="mailto:security@machreach.com">security@machreach.com</a>.</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">4. Acceptable Use</h2>
        <p>You agree <strong>not</strong> to use MachReach to:</p>
        <ul style="padding-left:20px;">
          <li>Send spam, unsolicited bulk email, or messages that violate CAN-SPAM, GDPR, or any applicable anti-spam laws</li>
          <li>Send emails containing malware, phishing links, or fraudulent content</li>
          <li>Harass, threaten, or abuse recipients</li>
          <li>Impersonate other individuals or organizations</li>
          <li>Violate any applicable local, national, or international laws</li>
          <li>Scrape or harvest email addresses without consent</li>
          <li>Exceed reasonable usage limits or abuse shared infrastructure</li>
        </ul>
        <p>We reserve the right to suspend or terminate accounts that violate these terms without notice.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">5. Email Sending & Compliance</h2>
        <p>You are solely responsible for the content of emails sent through MachReach and for complying with all applicable email regulations (CAN-SPAM Act, GDPR, CASL, etc.). This includes:</p>
        <ul style="padding-left:20px;">
          <li>Including a valid physical address in commercial emails</li>
          <li>Providing a clear unsubscribe mechanism</li>
          <li>Honoring opt-out requests promptly</li>
          <li>Having proper consent or legitimate interest for contacting recipients</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">6. Subscriptions & Billing</h2>
        <ul style="padding-left:20px;">
          <li>Free plans are available with limited features</li>
          <li>Paid plans are billed monthly through PayPal</li>
          <li>You can cancel your subscription at any time; access continues until the end of the billing period</li>
          <li>Refunds are handled on a case-by-case basis</li>
          <li>We reserve the right to change pricing with 30 days' notice</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7. AI Features</h2>
        <p>MachReach uses AI (powered by OpenAI) for email classification, reply generation, study plans, quizzes, flashcards, notes, the AI tutor, essay feedback, and panic-mode plans. AI output is provided as <em>suggestion</em> &mdash; you are responsible for reviewing and approving all content before sending, submitting, or using it academically. The AI tutor is grounded on materials you upload; it is not a substitute for professional advice, and MachReach does not guarantee factual accuracy.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7a. Academic Integrity</h2>
        <p>You are solely responsible for complying with your institution&rsquo;s academic-integrity policies. MachReach is a study aid; using its output to commit plagiarism, cheat on exams, or violate honor codes is a breach of these Terms and of your relationship with your institution.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7b. Study Exchange &amp; Shared Content</h2>
        <p>When you publish a note to Study Exchange, you grant other MachReach students a non-exclusive, revocable license to view and fork that note. You must own the content or have the right to share it. You can unshare or delete any note at any time. Leaderboards (global, university, and fair-play personal) display only your first name, university, and XP totals.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">7c. Focus Guard Browser Extension</h2>
        <p>The Focus Guard Chrome extension is provided free of charge and runs locally in your browser. You install it at your own discretion and may uninstall it at any time. It does not transmit your browsing history to MachReach.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">8. Limitation of Liability</h2>
        <p>MachReach is provided "as is" without warranties of any kind. We are not liable for:</p>
        <ul style="padding-left:20px;">
          <li>Email deliverability issues (bounces, spam filtering, etc.)</li>
          <li>Consequences of emails sent through the platform</li>
          <li>Data loss due to circumstances beyond our control</li>
          <li>Service interruptions or downtime</li>
          <li>Any indirect, incidental, or consequential damages</li>
        </ul>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">9. Account Termination</h2>
        <p>You may delete your account at any time from Settings. We may suspend or terminate accounts that violate these terms. Upon termination, your data will be permanently deleted within 30 days.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">10. Changes to Terms</h2>
        <p>We may update these terms from time to time. Continued use of MachReach after changes constitutes acceptance of the updated terms. We will notify you of significant changes via email.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">11. Governing Law</h2>
        <p>These Terms of Service are governed by the laws of the Republic of Chile. Any disputes arising from the use of MachReach will be resolved in the courts of Santiago, Chile.</p>

        <h2 style="font-size:20px;color:var(--text);margin:28px 0 12px;">12. Contact</h2>
        <p>Questions about these terms? Contact us at <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
      </div>
    </div>
    """), active_page="terms")


# ---------------------------------------------------------------------------
# API — Usage check
# ---------------------------------------------------------------------------

@app.route("/api/usage")
def api_usage():
    """Return current usage and limits for the logged-in client."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_subscription, get_usage
    from outreach.config import PLAN_LIMITS
    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    return jsonify({"plan": plan, "usage": usage, "limits": limits})


# ---------------------------------------------------------------------------
# API — Email provider detection via MX lookup
# ---------------------------------------------------------------------------

@app.route("/api/detect-provider")
@limiter.limit("30 per minute")
def api_detect_provider():
    """Detect email provider from domain MX records."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    import dns.resolver
    domain = request.args.get("domain", "").strip().lower()
    if not domain or len(domain) > 253:
        return jsonify({"error": "invalid domain"}), 400

    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_hosts = [str(r.exchange).lower().rstrip(".") for r in answers]
    except Exception:
        return jsonify({"provider": None, "mx": []})

    # Check MX records for known providers
    for mx in mx_hosts:
        if "google" in mx or "gmail" in mx or "aspmx" in mx:
            return jsonify({"provider": "google", "name": "Google Workspace",
                "imap": "imap.gmail.com", "smtp": "smtp.gmail.com",
                "imap_port": 993, "smtp_port": 465, "color": "#EA4335",
                "hint": "This domain uses Google Workspace. Generate an <a href='https://myaccount.google.com/apppasswords' target='_blank'>App Password</a> in your Google account.",
                "mx": mx_hosts})
        if "outlook" in mx or "microsoft" in mx or "protection.outlook" in mx:
            return jsonify({"provider": "microsoft", "name": "Microsoft 365",
                "imap": "imap-mail.outlook.com", "smtp": "smtp-mail.outlook.com",
                "imap_port": 993, "smtp_port": 587, "color": "#0078D4",
                "hint": "This domain uses Microsoft 365. Use your regular password, or generate an <a href='https://account.live.com/proofs/AppPassword' target='_blank'>app password</a> if 2FA is on.",
                "mx": mx_hosts})
        if "yahoodns" in mx or "yahoo" in mx:
            return jsonify({"provider": "yahoo", "name": "Yahoo Mail",
                "imap": "imap.mail.yahoo.com", "smtp": "smtp.mail.yahoo.com",
                "imap_port": 993, "smtp_port": 465, "color": "#6001D2",
                "hint": "This domain uses Yahoo. Generate an <a href='https://login.yahoo.com/account/security' target='_blank'>App Password</a> in Yahoo Account Security.",
                "mx": mx_hosts})
        if "zoho" in mx:
            return jsonify({"provider": "zoho", "name": "Zoho Mail",
                "imap": "imap.zoho.com", "smtp": "smtp.zoho.com",
                "imap_port": 993, "smtp_port": 465, "color": "#F0483E",
                "hint": "This domain uses Zoho Mail. Go to Zoho Mail Settings → Security → App Passwords to generate one.",
                "mx": mx_hosts})

    return jsonify({"provider": None, "mx": mx_hosts})


# ---------------------------------------------------------------------------
# API — Email deliverability check (SPF / DKIM / DMARC)
# ---------------------------------------------------------------------------

@app.route("/api/check-deliverability")
@limiter.limit("10 per minute")
def api_check_deliverability():
    """Check SPF, DKIM, and DMARC DNS records for a domain."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    import dns.resolver
    import re as _re
    domain = request.args.get("domain", "").strip().lower()
    if not domain or len(domain) > 253 or not _re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$', domain):
        return jsonify({"error": "invalid domain"}), 400

    result = {"domain": domain, "spf": None, "dkim": None, "dmarc": None}

    # --- SPF ---
    try:
        answers = dns.resolver.resolve(domain, "TXT")
        for rdata in answers:
            txt = rdata.to_text().strip('"')
            if txt.startswith("v=spf1"):
                result["spf"] = {"status": "pass", "record": txt}
                break
        if not result["spf"]:
            result["spf"] = {"status": "missing", "record": None}
    except dns.resolver.NXDOMAIN:
        result["spf"] = {"status": "missing", "record": None}
    except dns.resolver.NoAnswer:
        result["spf"] = {"status": "missing", "record": None}
    except Exception as e:
        result["spf"] = {"status": "error", "record": None, "error": str(e)}

    # --- DKIM (check common selectors) ---
    dkim_selectors = ["google", "default", "selector1", "selector2", "dkim", "mail", "k1", "s1", "s2"]
    dkim_found = False
    for sel in dkim_selectors:
        try:
            answers = dns.resolver.resolve(f"{sel}._domainkey.{domain}", "TXT")
            for rdata in answers:
                txt = rdata.to_text().strip('"')
                if "p=" in txt:
                    result["dkim"] = {"status": "pass", "selector": sel, "record": txt[:120] + "..."}
                    dkim_found = True
                    break
        except Exception:
            pass
        if dkim_found:
            break
    if not dkim_found:
        try:
            dns.resolver.resolve(f"_domainkey.{domain}", "TXT")
            result["dkim"] = {"status": "pass", "selector": "_domainkey", "record": "(base record found)"}
        except Exception:
            result["dkim"] = {"status": "missing", "record": None,
                              "hint": "No DKIM record found for common selectors. Your email provider should give you the selector and key to add."}

    # --- DMARC ---
    try:
        answers = dns.resolver.resolve(f"_dmarc.{domain}", "TXT")
        for rdata in answers:
            txt = rdata.to_text().strip('"')
            if txt.startswith("v=DMARC1"):
                policy = "none"
                if "p=reject" in txt:
                    policy = "reject"
                elif "p=quarantine" in txt:
                    policy = "quarantine"
                elif "p=none" in txt:
                    policy = "none"
                result["dmarc"] = {"status": "pass", "record": txt, "policy": policy}
                break
        if not result["dmarc"]:
            result["dmarc"] = {"status": "missing", "record": None}
    except dns.resolver.NXDOMAIN:
        result["dmarc"] = {"status": "missing", "record": None}
    except dns.resolver.NoAnswer:
        result["dmarc"] = {"status": "missing", "record": None}
    except Exception as e:
        result["dmarc"] = {"status": "error", "record": None, "error": str(e)}

    # Overall score
    checks = [result["spf"], result["dkim"], result["dmarc"]]
    passed = sum(1 for c in checks if c and c.get("status") == "pass")
    result["score"] = passed
    result["max_score"] = 3
    return jsonify(result)


# ── GDPR: Data Export ──────────────────────────────────────────
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


# ---------------------------------------------------------------------------
# KILLER BUSINESS FEATURES — Subject Line Optimizer, Reply Intel, Deliverability
# ---------------------------------------------------------------------------

@app.route("/subject-optimizer", methods=["GET", "POST"])
def subject_optimizer():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.ai import optimize_subject_line
    result = None
    original_subject = ""
    body_preview = ""
    audience = "cold_prospects"
    goal = "get_opened"
    if request.method == "POST":
        original_subject = (request.form.get("subject") or "").strip()
        body_preview = (request.form.get("body") or "").strip()
        audience = request.form.get("audience") or "cold_prospects"
        goal = request.form.get("goal") or "get_opened"
        if original_subject:
            try:
                result = optimize_subject_line(original_subject, body_preview[:300], audience, goal)
            except Exception as e:
                result = {"error": str(e)}

    result_html = ""
    if result:
        if result.get("error"):
            result_html = f'<div class="card" style="padding:20px;color:#EF4444;">{result["error"]}</div>'
        else:
            score = result.get("score", 0)
            color = "#10B981" if score >= 80 else "#F59E0B" if score >= 60 else "#EF4444"
            suggestions_html = ""
            for s in (result.get("suggestions") or []):
                subj = (s.get("subject") or "").replace("<", "&lt;")
                why = (s.get("why") or "").replace("<", "&lt;")
                sc = s.get("predicted_score", 0)
                suggestions_html += f"""
                <div style="padding:14px;border:1px solid var(--border);border-radius:8px;margin-bottom:10px;">
                  <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;">
                    <strong style="font-size:15px;">{subj}</strong>
                    <span style="background:#10B981;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;">{sc}/100</span>
                  </div>
                  <div style="font-size:13px;color:var(--gray);margin-top:4px;">{why}</div>
                  <button class="btn btn-ghost btn-sm" style="margin-top:8px;" onclick="navigator.clipboard.writeText({subj!r});this.textContent='Copied!'">Copy</button>
                </div>
                """
            issues_html = ""
            for iss in (result.get("issues") or []):
                issues_html += f'<li>{iss}</li>'
            result_html = f"""
            <div class="card" style="padding:24px;margin-top:20px;">
              <div style="display:flex;align-items:center;gap:20px;margin-bottom:18px;">
                <div style="font-size:64px;font-weight:800;color:{color};line-height:1;">{score}<span style="font-size:22px;color:var(--gray);">/100</span></div>
                <div><div style="font-size:14px;color:var(--gray);">Current subject score</div><div style="font-weight:600;">{original_subject}</div></div>
              </div>
              {'<div style="margin-bottom:16px;"><strong>Issues:</strong><ul>' + issues_html + '</ul></div>' if issues_html else ''}
              <h3 style="margin:0 0 12px;">&#10024; Optimized suggestions</h3>
              {suggestions_html}
            </div>
            """

    html = f"""
    <div style="max-width:900px;margin:0 auto;">
      <div style="text-align:center;margin-bottom:30px;">
        <h1 style="font-size:36px;margin:0;background:linear-gradient(135deg,#8B5CF6,#3B82F6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">&#10024; Subject Line Optimizer</h1>
        <p style="color:var(--gray);font-size:16px;margin-top:8px;">AI scores your subject and rewrites it for higher open rates.</p>
      </div>

      <form method="POST" class="card" style="padding:24px;">
        <input type="hidden" name="csrf_token" value="{session.get('csrf_token','')}" />
        <label style="font-weight:600;display:block;margin-bottom:8px;">Your subject line</label>
        <input name="subject" value="{original_subject}" type="text" required style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);margin-bottom:16px;" />

        <label style="font-weight:600;display:block;margin-bottom:8px;">First lines of email body (for context)</label>
        <textarea name="body" rows="4" style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);margin-bottom:16px;resize:vertical;">{body_preview}</textarea>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
          <div>
            <label style="font-weight:600;display:block;margin-bottom:8px;">Audience</label>
            <select name="audience" style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);">
              <option value="cold_prospects" {'selected' if audience=='cold_prospects' else ''}>Cold prospects</option>
              <option value="warm_leads" {'selected' if audience=='warm_leads' else ''}>Warm leads</option>
              <option value="existing_customers" {'selected' if audience=='existing_customers' else ''}>Existing customers</option>
              <option value="executives" {'selected' if audience=='executives' else ''}>Executives / C-level</option>
            </select>
          </div>
          <div>
            <label style="font-weight:600;display:block;margin-bottom:8px;">Goal</label>
            <select name="goal" style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);">
              <option value="get_opened" {'selected' if goal=='get_opened' else ''}>Maximize open rate</option>
              <option value="get_reply" {'selected' if goal=='get_reply' else ''}>Maximize reply rate</option>
              <option value="book_meeting" {'selected' if goal=='book_meeting' else ''}>Book a meeting</option>
            </select>
          </div>
        </div>

        <div style="display:flex;justify-content:flex-end;"><button type="submit" class="btn btn-primary">&#9889; Optimize</button></div>
      </form>
      {result_html}
    </div>
    """
    return _render("Subject Optimizer", html, active_page="subject_optimizer")


@app.route("/reply-intel")
def reply_intel_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_replies
    from outreach.ai import classify_reply
    cid = session["client_id"]
    try:
        replies = get_replies(cid, limit=25)
    except Exception:
        replies = []

    analyzed = []
    for r in (replies or [])[:15]:
        body = r.get("reply_body") or r.get("body") or ""
        if not body:
            continue
        try:
            cls = classify_reply(body[:1500], r.get("subject", ""), r.get("original_body", "")[:500])
        except Exception:
            cls = {"intent": "unknown", "sentiment": "neutral", "urgency": "normal", "buying_signal": 0}
        analyzed.append({"reply": r, "cls": cls})

    def badge(label, color):
        return f'<span style="display:inline-block;padding:3px 10px;background:{color};color:#fff;border-radius:12px;font-size:11px;font-weight:600;">{label}</span>'

    intent_colors = {"interested": "#10B981", "not_interested": "#EF4444", "objection": "#F59E0B", "question": "#3B82F6", "out_of_office": "#64748b", "referral": "#8B5CF6", "unsubscribe": "#991B1B"}

    cards_html = ""
    for item in analyzed:
        r = item["reply"]
        c = item["cls"]
        intent = c.get("intent", "unknown")
        senti = c.get("sentiment", "neutral")
        urg = c.get("urgency", "normal")
        buying = int(c.get("buying_signal", 0))
        email = r.get("from_email", "") or r.get("contact_email", "")
        subj = r.get("subject", "(no subject)")
        preview = (r.get("reply_body") or r.get("body") or "")[:200].replace("<", "&lt;")
        next_action = c.get("next_action") or ""
        objections = c.get("objections") or []
        obj_html = ""
        if objections:
            obj_html = '<div style="margin-top:6px;font-size:12px;"><strong>Objections:</strong> ' + ", ".join(objections) + '</div>'
        cards_html += f"""
        <div class="card" style="padding:16px;margin-bottom:12px;border-left:4px solid {intent_colors.get(intent,'#64748b')};">
          <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:8px;flex-wrap:wrap;">
            <div><strong>{email}</strong> <span style="color:var(--gray);font-size:13px;">— {subj}</span></div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              {badge(intent.replace('_',' '), intent_colors.get(intent,'#64748b'))}
              {badge(senti, '#10B981' if senti=='positive' else '#EF4444' if senti=='negative' else '#64748b')}
              {badge(urg, '#EF4444' if urg=='high' else '#F59E0B' if urg=='normal' else '#64748b')}
              <span style="background:var(--bg);padding:3px 10px;border-radius:12px;font-size:11px;">&#128176; buying: {buying}/10</span>
            </div>
          </div>
          <div style="color:var(--gray);font-size:13px;margin-bottom:8px;">{preview}...</div>
          {f'<div style="padding:8px 12px;background:var(--bg);border-radius:6px;font-size:13px;"><strong>&#128073; Next action:</strong> {next_action}</div>' if next_action else ''}
          {obj_html}
        </div>
        """

    if not cards_html:
        cards_html = '<div class="card" style="padding:24px;text-align:center;color:var(--gray);">No replies yet. Once prospects reply, they\'ll be classified here.</div>'

    html = f"""
    <div style="max-width:1000px;margin:0 auto;">
      <div style="text-align:center;margin-bottom:30px;">
        <h1 style="font-size:36px;margin:0;background:linear-gradient(135deg,#10B981,#3B82F6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">&#129504; Reply Intelligence</h1>
        <p style="color:var(--gray);font-size:16px;margin-top:8px;">Auto-classified replies with intent, sentiment, buying signal, and next action.</p>
      </div>
      {cards_html}
    </div>
    """
    return _render("Reply Intelligence", html, active_page="reply_intel")


@app.route("/deliverability", methods=["GET", "POST"])
def deliverability_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.ai import analyze_email_content
    subject = ""
    body = ""
    result = None
    if request.method == "POST":
        subject = (request.form.get("subject") or "").strip()
        body = (request.form.get("body") or "").strip()
        if subject or body:
            try:
                result = analyze_email_content(subject, body)
            except Exception as e:
                result = {"error": str(e)}

    result_html = ""
    if result:
        if result.get("error"):
            result_html = f'<div class="card" style="padding:20px;color:#EF4444;">{result["error"]}</div>'
        else:
            spam = int(result.get("spam_score", 0))
            color = "#10B981" if spam < 30 else "#F59E0B" if spam < 60 else "#EF4444"
            verdict = "&#9989; Likely to land in inbox" if spam < 30 else "&#9888;&#65039; May hit promotions" if spam < 60 else "&#128680; Likely spam"
            issues = result.get("issues") or []
            suggestions = result.get("suggestions") or []
            triggers = result.get("spam_triggers") or []
            iss_html = "".join(f'<li>{i}</li>' for i in issues) or '<li style="color:var(--gray);">No issues found</li>'
            sug_html = "".join(f'<li>{s}</li>' for s in suggestions) or ''
            trig_html = ""
            if triggers:
                trig_html = '<div style="margin-top:12px;"><strong>Spam trigger words:</strong> ' + ", ".join(f'<code style="background:rgba(239,68,68,0.15);padding:2px 6px;border-radius:4px;">{t}</code>' for t in triggers) + '</div>'
            metrics = result.get("metrics") or {}
            metric_html = "".join(f'<div style="padding:10px;background:var(--bg);border-radius:6px;text-align:center;"><div style="font-size:22px;font-weight:700;">{v}</div><div style="font-size:11px;color:var(--gray);">{k.replace("_"," ")}</div></div>' for k, v in metrics.items())
            result_html = f"""
            <div class="card" style="padding:24px;margin-top:20px;">
              <div style="display:flex;align-items:center;gap:20px;margin-bottom:18px;">
                <div style="font-size:64px;font-weight:800;color:{color};line-height:1;">{spam}<span style="font-size:22px;color:var(--gray);">/100</span></div>
                <div><div style="font-size:13px;color:var(--gray);">Spam score (lower is better)</div><div style="font-size:18px;font-weight:700;color:{color};">{verdict}</div></div>
              </div>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:18px;">{metric_html}</div>
              <div style="margin-bottom:14px;"><strong>Issues found</strong><ul>{iss_html}</ul></div>
              {'<div><strong>Suggestions</strong><ul>' + sug_html + '</ul></div>' if sug_html else ''}
              {trig_html}
            </div>
            """

    html = f"""
    <div style="max-width:900px;margin:0 auto;">
      <div style="text-align:center;margin-bottom:30px;">
        <h1 style="font-size:36px;margin:0;background:linear-gradient(135deg,#10B981,#059669);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">&#128737;&#65039; Deliverability Checker</h1>
        <p style="color:var(--gray);font-size:16px;margin-top:8px;">Paste your email. Get an instant spam score with specific fixes. Free. No AI cost.</p>
      </div>

      <form method="POST" class="card" style="padding:24px;">
        <input type="hidden" name="csrf_token" value="{generate_csrf()}" />
        <label style="font-weight:600;display:block;margin-bottom:8px;">Subject</label>
        <input name="subject" type="text" value="{subject}" style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);margin-bottom:16px;" />

        <label style="font-weight:600;display:block;margin-bottom:8px;">Body (HTML or plain text)</label>
        <textarea name="body" rows="12" style="width:100%;padding:12px;border-radius:8px;border:1px solid var(--border);font-family:inherit;resize:vertical;">{body}</textarea>

        <div style="margin-top:16px;display:flex;justify-content:flex-end;"><button type="submit" class="btn btn-primary">&#128269; Check Deliverability</button></div>
      </form>
      {result_html}
    </div>
    """
    return _render("Deliverability", html, active_page="deliverability")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=port)
