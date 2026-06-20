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

from flask import Flask, flash, jsonify, make_response, redirect, render_template_string, request, session, url_for
from markupsafe import Markup

from outreach.config import ADMIN_ACTION_SECRET, ADMIN_EMAILS, SECRET_KEY
from outreach.i18n import t, t_dict

# ── Sentry error tracking (production only — set SENTRY_DSN env var) ──
from outreach.config import SENTRY_DSN
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
    )

from outreach.db import (
    create_client,
    create_reset_token,
    create_verification_token,
    get_client,
    get_client_by_email,
    get_export_data,
    get_valid_reset_token,
    get_valid_verification_token,
    init_db,
    mark_email_verified,
    mark_reset_token_used,
    update_client_password,
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
from flask_wtf.csrf import CSRFProtect
# Don't expire CSRF tokens faster than the session. The Flask-WTF default of
# 1 hour caused "The CSRF tokens do not match" when a page was left open and
# submitted later (very common on mobile). The token is still bound to the
# session secret, so CSRF protection is unchanged — only the early timeout is
# removed. It now lasts the session lifetime (24h cookie).
app.config["WTF_CSRF_TIME_LIMIT"] = None
csrf = CSRFProtect(app)


@app.before_request
def _canonical_host_redirect():
    """Send www.* to the bare apex (machreach.com) with a 301, preserving path
    and query. Keeps everyone on one host so the session/CSRF cookie can't split
    across www vs apex. Registered first so it runs before session/CSRF logic.
    """
    raw = request.host or ""
    if raw.lower().startswith("www."):
        return redirect(request.url.replace("://" + raw, "://" + raw[4:], 1), code=301)

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
    owner_emails.update({"ignaciomachuca2005@gmail.com", "fernanda.machuca@uc.cl"})
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
    return {
        "posthog_key": os.getenv("POSTHOG_KEY", ""),
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
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
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
  <link rel="stylesheet" href="/static/machreach_layout/layout-base.css?v=2"/>
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
        <a class="mr-tb-link {% if active_page == 'student_achievements' %}active{% endif %}" href="/student/achievements">{{ student_ui.xp }}</a>
        {% if is_admin %}<a class="mr-tb-link {% if active_page == 'admin' %}active{% endif %}" href="/admin">{{ student_ui.admin }}</a>{% endif %}
      </nav>
      <div class="mr-tb-right">
        <button id="theme-toggle" class="mr-tb-icon" type="button" onclick="toggleDarkMode()" title="{{ student_ui.toggle_theme }}">&#127769;</button>
        <a class="mr-tb-icon" href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <a class="mr-tb-icon" href="/logout" title="{{nav.logout}}">&#10162;</a>
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
        <link rel="stylesheet" href="/static/machreach_layout/layout-dark.css"/>
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
      <button class="mr-tab {% if active_page in ['student_planner','student_quizzes','student_flashcards','student_reviews','student_friends','student_shop','student_gpa','student_achievements','student_profile','student_settings','admin'] %}active{% endif %}" type="button" onclick="mrToggleMore()" aria-haspopup="dialog">
        <svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
        <span>{% if lang == 'en' %}More{% else %}Más{% endif %}</span>
      </button>
    </nav>
    <div class="mr-more" id="mrMore">
      <div class="mr-more-backdrop" onclick="mrToggleMore(false)"></div>
      <div class="mr-more-panel" role="dialog" aria-label="{% if lang == 'en' %}More{% else %}Más{% endif %}">
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
          <a href="/student/achievements"><span class="mr-more-ic">&#127942;</span>{{ student_ui.xp }}<span class="mr-more-chev">&rsaquo;</span></a>
          {% if is_admin %}<a href="/admin"><span class="mr-more-ic">&#128227;</span>{{ student_ui.admin }}<span class="mr-more-chev">&rsaquo;</span></a>{% endif %}
        </nav>
        <div class="mr-more-actions">
          <button type="button" class="mr-more-act" onclick="if(typeof toggleDarkMode==='function')toggleDarkMode()"><span class="mr-more-ic">&#127769;</span>{{ student_ui.toggle_theme }}</button>
          <a class="mr-more-act" href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}"><span class="mr-more-ic">&#127760;</span>{% if lang == 'en' %}Español{% else %}English{% endif %}</a>
          <a class="mr-more-act" href="/student/settings"><span class="mr-more-ic">&#9881;</span>{{ student_ui.settings }}</a>
          <a class="mr-more-act mr-more-logout" href="/logout"><span class="mr-more-ic">&#9211;</span>{{ nav.logout }}</a>
        </div>
      </div>
    </div>
    <script>
      function mrToggleMore(open){
        var m = document.getElementById('mrMore');
        if (!m) return;
        var willOpen = (open === undefined) ? !m.classList.contains('open') : !!open;
        m.classList.toggle('open', willOpen);
        document.body.style.overflow = willOpen ? 'hidden' : '';
      }
      document.addEventListener('keydown', function(e){ if (e.key === 'Escape') mrToggleMore(false); });
    </script>
    {% endif %}
  </div>
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
        <button id="theme-toggle" class="nav-theme-toggle" type="button" onclick="toggleDarkMode()" title="{{ student_ui.toggle_theme }}">&#127769;</button>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <div class="nav-divider"></div>
        <a href="/student/profile" class="nav-user" style="text-decoration:none;cursor:pointer;color:#94A3B8;" title="My profile">{{client_name}}</a>
        <a href="/logout" style="color:#EF4444;">{{nav.logout}}</a>
      {% else %}
        <a href="/login">{{nav.login}}</a>
        <a href="/register" class="btn btn-primary btn-sm" style="color:#fff;">{{nav.get_started}}</a>
        <button id="theme-toggle" class="nav-theme-toggle" type="button" onclick="toggleDarkMode()" title="{{nav.toggle_theme|default('Cambiar modo')}}">&#127769;</button>
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
  <script src="/static/machreach_layout/layout-1.js?v=20260604-tooltip2"></script>

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

  <script src="/static/machreach_layout/layout-2.js"></script>

  <!-- Student i18n: Spanish translations (client-side) -->
  {% if lang == 'es' and account_type|default('student') == 'student' %}
  <script src="/static/machreach_layout/layout-3.js"></script>
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
      "Mejorar a Ultimate": "Upgrade to Ultimate",
      "Gasta monedas en congeladores de racha 🔥, banners de perfil y boosts temporales. Gana monedas completando sesiones de enfoque, quizzes y tarjetas.": "Spend coins on streak 🔥 freezes, profile banners, and temporary boosts. Earn coins by completing focus sessions, quizzes, and flashcards.",

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

  {% if logged_in and account_type|default('student') == 'student' %}
  <!-- ── Preserved-XP welcome banner (academic setup lives on /student/setup) ── -->
  <div id="mrXpBanner" style="display:none;position:fixed;left:50%;top:calc(92px + env(safe-area-inset-top, 0px));transform:translateX(-50%);z-index:1900;
       background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;padding:12px 20px;border-radius:12px;
       box-shadow:0 10px 40px rgba(99,102,241,.4);font-weight:500;align-items:center;gap:12px;
       max-width:90vw;animation:mrSlideDown .5s cubic-bezier(.22,.61,.36,1);">
    <span style="font-size:22px;">🎉</span>
    <span>Welcome back — <strong>your previous progress has been preserved.</strong> All your XP is intact.</span>
    <button id="mrXpBannerClose" style="background:rgba(255,255,255,.2);border:0;color:#fff;width:26px;height:26px;border-radius:50%;cursor:pointer;font-size:16px;line-height:1;">×</button>
  </div>

  <style>
    @keyframes mrSlideDown { from { transform:translate(-50%,-30px); opacity:0;} to { transform:translate(-50%,0); opacity:1;}}
  </style>

  <script src="/static/machreach_layout/layout-4.js"></script>
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
        if session.get("account_type") == "student":
            return redirect(url_for("student_dashboard_page"))
        return redirect(url_for("dashboard"))

    lang = session.get("lang", "es")
    from student.landing_design import render_landing_page
    resp = make_response(render_landing_page(lang))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
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

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
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
        if len(password) < 6:
            flash(("error", "Password must be at least 6 characters." if session.get("lang") == "en" else "La contraseña debe tener al menos 6 caracteres."))
            return redirect(url_for("register"))
        if password2 and password2 != password:
            flash(("error", "Passwords do not match." if session.get("lang") == "en" else "Las contraseñas no coinciden."))
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
    # Referral capture: a shared link is /register?ref=CODE. Keep it in the
    # session so it survives the preview->submit roundtrip, and pass it into the
    # Canvas signup form as a hidden field.
    _ref_code = (request.args.get("ref") or session.get("referral_ref") or "").strip().upper()[:16]
    if _ref_code:
        session["referral_ref"] = _ref_code
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
    _name_label = "Full name" if _is_en else "Nombre"
    _name_ph = "Your name" if _is_en else "Tu nombre"
    _password_label = "Password" if _is_en else "Contrase&ntilde;a"
    _password_ph = "At least 6 characters" if _is_en else "M&iacute;nimo 6 caracteres"
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
            {_ref_hidden}
            <div class="auth-field"><label>{_name_label}</label><input name="name" type="text" required autocomplete="name" placeholder="{_name_ph}"></div>
            <div class="auth-field"><label>{"Email" if _is_en else "Correo"}</label><input name="email" type="email" required autocomplete="username" placeholder="tu@correo.com"></div>
            <div class="auth-field"><label>{_password_label}</label><input name="password" type="password" required minlength="6" autocomplete="new-password" placeholder="{_password_ph}"></div>
            <div class="auth-field"><label>{_confirm_label}</label><input name="password2" type="password" required minlength="6" autocomplete="new-password" placeholder="{_confirm_ph}"></div>
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
    _is_en = session.get("lang") == "en"
    _story = _auth_story_panel("login", session.get("lang", "es"))
    _resend_summary = "Didn't get verification email?" if _is_en else "&iquest;No recibiste el correo de verificaci&oacute;n?"
    _resend_button = "Resend" if _is_en else "Reenviar"
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
            <div class="auth-field"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="you@school.edu" autocomplete="username" required></div>
            <div class="auth-field"><label>{t("auth.password")}</label><input name="password" type="password" autocomplete="current-password" required></div>
            <button class="btn btn-primary auth-submit" type="submit">{t("auth.sign_in")}</button>
          </form>
          <div class="auth-link-row"><a href="/forgot-password">{t("auth.forgot_password")}</a></div>
          <details class="auth-details">
            <summary>{_resend_summary}</summary>
            <form class="auth-resend-form" method="post" action="/resend-verification">
              <input name="email" type="email" placeholder="your@email.com" required>
              <button class="btn btn-outline btn-sm" type="submit">{_resend_button}</button>
            </form>
          </details>
          <div class="auth-footer">{t("auth.no_account")} <a href="/register">{t("auth.sign_up_free")}</a></div>
        </div>
      </section>
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
    return render_layout(title="Verify your email", logged_in=False,
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
    return render_layout(title="Forgot Password", logged_in=False,
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
    return render_layout(title="Reset Password", logged_in=False,
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
        for tbl in ["student_quizzes",
                     "student_flashcard_decks", "student_notes",
                     "student_course_files", "student_exams", "student_study_progress",
                     "student_study_plans", "student_assignment_progress",
                     "student_schedule_settings",
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
    from outreach.db import get_db, _exec, _fetchall, _USE_PG

    target = get_client(client_id)
    if not target:
        return {"ok": False, "error": "User not found."}
    protected_admins = {e.strip().lower() for e in ADMIN_EMAILS}
    protected_admins.update({"ignaciomachuca2005@gmail.com", "fernanda.machuca@uc.cl"})
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
          <td style="font-family:'Nunito',sans-serif;font-size:13px;">{_esc(u.get("email") or "")}</td>
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
    emails.update({"ignaciomachuca2005@gmail.com", "fernanda.machuca@uc.cl"})
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
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
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
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
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
        ("Registros hoy", _admin_metric(f"SELECT COUNT(*) FROM clients WHERE created_at >= CURRENT_DATE", "SELECT COUNT(*) FROM clients WHERE date(created_at)=date('now','localtime')")),
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
      <div class="breadcrumb"><a href="/admin">Admin</a> / Analytics</div>
      <div class="page-header"><h1>&#128202; Analytics de producto</h1><p class="subtitle">Tráfico, uso de IA, estudio real y señales para decidir qué merece ser Plus o Ultimate.</p></div>
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
    return _public_info_page("Privacy Policy", "Legal", "How MachReach handles account, study, Canvas extension, and subscription data.", """
        <p class="mr-note"><strong>Plain-English summary:</strong> MachReach helps students track focus time, courses, grades, flashcards, quizzes, rankings, streaks, friends, and study progress. We collect only what the product needs, we never sell your data, passwords are hashed with bcrypt, and you can disconnect Canvas or delete your account from Settings.</p>
        <p><strong>Last updated:</strong> June 15, 2026</p>
        <h2>1. Information We Collect</h2>
        <p><strong>Account information:</strong> your name, institutional or Canvas email address, your university, and your password hash. When you create an account through Canvas, MachReach reads the email address exposed by your Canvas profile so you can log in later with that email and the password you set.</p>
        <p><strong>Canvas LMS data:</strong> if you connect Canvas, the MachReach browser extension reads your course list from your own logged-in Canvas session and sends it to MachReach so we can show your classes and power class-level leaderboards. We only read your course list — we do not submit assignments, change grades, or publish content.</p>
        <p><strong>Courses you add manually:</strong> if your university doesn't use Canvas, or you simply prefer to, you can add courses by typing a course code and name. To help other students at <em>your own university</em> fill these in faster, course codes and names you add may be saved to a shared, university-scoped autofill catalog. This catalog stores only the course code and course name together with the university — it is never linked to your identity, your grades, or your study activity, and it is only ever shown to other students at the same university.</p>
        <p><strong>Study materials:</strong> files, notes, and text you choose to upload or type for features such as quizzes and flashcards.</p>
        <p><strong>Study activity:</strong> focus sessions, minutes studied per course, XP events, streaks, badges, quiz attempts, flashcard reviews, leaderboard rank, course outcomes, grades you enter, and in-app coin activity.</p>
        <p><strong>Social activity:</strong> friend connections and referral activity if you invite friends. We store who you are friends with and how many people joined with your referral link.</p>
        <p><strong>Focus Guard extension:</strong> extension settings and active-session state are used to support focus sessions. Some settings may be stored locally in your browser.</p>
        <p><strong>Payment data:</strong> billing is processed by Lemon Squeezy. We receive subscription status and IDs, never card numbers.</p>
        <h2>2. How We Use Your Information</h2>
        <ul>
          <li>To create your account, authenticate you, and let you reset your password</li>
          <li>To import your Canvas course list when you choose to connect through the MachReach extension, or to save courses you add manually</li>
          <li>To power university-scoped course autofill so students at the same university can add courses faster</li>
          <li>To generate and manage quizzes, flashcards, focus sessions, grade tracking, and course analytics</li>
          <li>To track XP, streaks, leaderboard rankings, badges, coins, friends, and referrals</li>
          <li>To process subscriptions and service notifications such as password resets and study emails you opted into</li>
          <li>To keep the service secure, reliable, and improving over time</li>
        </ul>
        <h2>3. Data Security</h2>
        <p>We use HTTPS/TLS, CSRF protection, rate limiting, strict security headers, parameterized SQL, HTML escaping, secure cookies in production, hashed passwords, and access controls for sensitive account data.</p>
        <h2>4. Sub-processors</h2>
        <ul>
          <li><strong>OpenAI:</strong> content you submit for AI-generated quizzes or flashcards may be sent to generate those study tools. OpenAI does not train on API data per its API data-usage policy.</li>
          <li><strong>Instructure / Canvas LMS:</strong> optional profile and course import when you connect Canvas.</li>
          <li><strong>Lemon Squeezy:</strong> payment and subscription processing.</li>
          <li><strong>Render:</strong> application hosting and database infrastructure.</li>
          <li><strong>Sentry:</strong> error reporting with sensitive fields scrubbed where possible.</li>
        </ul>
        <h2>5. Your Rights</h2>
        <p>You can access, export, correct, or delete your data from Settings, disconnect Canvas at any time, opt out of optional study emails, or contact <a href="mailto:support@machreach.com">support@machreach.com</a> for data-rights requests.</p>
        <h2>6. Contact</h2>
        <p>Questions or data-rights requests: <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "privacy")


@app.route("/terms")
def terms_page():
    return _public_info_page("Terms of Service", "Legal", "The rules for using MachReach, subscriptions, AI study tools, rankings, and account security.", """
        <p><strong>Last updated:</strong> June 15, 2026</p>
        <h2>1. Acceptance of Terms</h2>
        <p>By creating an account or using MachReach, you agree to these Terms of Service. If you do not agree, do not use the service.</p>
        <h2>2. Description of Service</h2>
        <p>MachReach provides student study tools including Canvas extension course import, manually added courses, focus timers, study-time tracking, AI-generated flashcards and practice quizzes, grade tracking, XP, streaks, leaderboards, friends, coins and an in-app shop, course analytics, a referral program, and an optional Focus Guard browser extension. Some features require a paid Plus or Ultimate plan.</p>
        <h2>3. Account Responsibilities</h2>
        <ul>
          <li>You must provide accurate information when registering, including your university</li>
          <li>You are responsible for maintaining the security of your account credentials</li>
          <li>If you connect Canvas, you must use your own Canvas account and the MachReach browser extension</li>
          <li>When you add courses manually, you agree they may be saved to a shared autofill catalog scoped to your university (course codes and names only)</li>
          <li>You must not share your account with others</li>
          <li>You must be at least 16 years old to use MachReach</li>
          <li>You must not attempt to probe, scan, or exploit vulnerabilities in the service</li>
        </ul>
        <h2>4. Academic Integrity</h2>
        <p>You are responsible for complying with your institution's academic-integrity policies. MachReach is a study aid; using it to plagiarize, cheat, or violate honor codes is prohibited.</p>
        <h2>5. Subscriptions and Billing</h2>
        <p>Paid student plans (Plus and Ultimate) are billed through Lemon Squeezy. You can cancel at any time; access continues until the end of the billing period. Refunds are handled case by case.</p>
        <h2>6. AI Features</h2>
        <p>AI-generated quizzes, flashcards, or other study content are provided as suggestions and may be incomplete or incorrect. You are responsible for reviewing generated content before relying on it academically.</p>
        <h2>7. Leaderboards, Coins and Referrals</h2>
        <p>XP, coins, streaks, badges, and leaderboard ranks are part of the game layer and have no cash value, cannot be transferred or sold between accounts, and can be redeemed only inside MachReach. Referral rewards (such as free Plus time) are granted for genuine sign-ups only. We may withhold, reverse, or reset rewards, ranks, or referral credit for suspected cheating or abuse.</p>
        <h2>8. Limitation of Liability</h2>
        <p>MachReach is provided as is without warranties of any kind. We are not liable for service interruptions, data loss beyond our control, or indirect, incidental, or consequential damages.</p>
        <h2>9. Contact</h2>
        <p>Questions about these terms? Contact <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "terms")


@app.route("/cookies")
def cookies_page():
    return _public_info_page("Cookies", "Privacy", "A short version: MachReach only uses cookies needed to keep the product working.", """
      <p>MachReach uses only essential cookies for login sessions, CSRF protection, language preference, and basic UI preferences. We do not use advertising cookies.</p>
      <p>If you block essential cookies, login and protected student features may stop working. Questions: <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "cookies")


@app.route("/status")
def public_status_page():
    return _public_info_page("Status", "System", "The current public status of MachReach services.", """
      <p class="mr-note"><strong>Current public status:</strong> operational.</p>
      <p>For incidents or support, contact <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "status")


@app.route("/about")
def about_page():
    return _public_info_page("About MachReach", "Built in Chile", "A study platform that pulls courses, focus, notes, quizzes, rankings, and progress into one place.", """
      <p>MachReach is a student study platform built in Santiago, Chile. We started from a simple frustration: studying tools are scattered across a dozen apps — one for notes, one for flashcards, one for timers, one for tracking grades — and none of them talk to each other. MachReach pulls the whole study workflow into a single place.</p>

      <h2>What you can do</h2>
      <ul>
        <li><strong>Focus sessions</strong> — distraction-free study timers, with an optional browser extension that blocks distracting sites while a session is active.</li>
        <li><strong>Courses &amp; Canvas extension</strong> — connect Canvas with the MachReach extension to import your real university courses.</li>
        <li><strong>Flashcards &amp; quizzes</strong> — generate study sets and practice questions from your own material.</li>
        <li><strong>Grades &amp; analytics</strong> — track marks on the Chilean 1.0–7.0 scale and see what you need to pass.</li>
        <li><strong>XP, streaks &amp; leaderboards</strong> — stay motivated with friends and live rankings by country, university, and major.</li>
      </ul>

      <h2>Who it's for</h2>
      <p>University and high-school students who want one organized study system instead of a pile of disconnected apps. MachReach is built with Chilean students in mind, but the tools work anywhere.</p>

      <h2>Get in touch</h2>
      <p>Questions, feedback, or partnership ideas? Email us at <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>
    """, "about")


@app.route("/blog")
def blog_page():
    return _public_info_page("Blog", "Updates", "Product notes and study-system ideas will live here once the public blog opens.", """
      <p>We don't run a public blog yet — for now, product updates and new features are announced directly inside MachReach when you log in, so you never miss them.</p>

      <h2>What we'll write about here</h2>
      <ul>
        <li>Study techniques and how to get the most out of focus sessions</li>
        <li>New features and product updates</li>
        <li>How students are using MachReach to stay on top of the semester</li>
      </ul>

      <p style="margin-top:24px;">Want updates by email? Reach out at <a href="mailto:support@machreach.com">support@machreach.com</a> and we'll keep you posted.</p>
    """, "blog")


@app.route("/press")
def press_page():
    return _public_info_page("Press", "Media", "Short facts for anyone writing about MachReach or exploring partnerships.", """
      <p>Writing about MachReach or interested in a partnership? Here's the essentials. For interviews, assets, or anything else, email <a href="mailto:support@machreach.com">support@machreach.com</a>.</p>

      <h2>Boilerplate</h2>
      <p>MachReach is a student study platform built in Santiago, Chile, that brings focus sessions, course tracking, Canvas extension import, flashcards, quizzes, grade analytics, and motivational features like XP, streaks, and leaderboards into a single study system.</p>

      <h2>Fast facts</h2>
      <ul>
        <li><strong>What:</strong> all-in-one study platform for students</li>
        <li><strong>Based in:</strong> Santiago, Chile</li>
        <li><strong>Key features:</strong> focus mode, Canvas extension course import, flashcards, quizzes, grade tracking, leaderboards</li>
        <li><strong>Website:</strong> <a href="https://machreach.com">machreach.com</a></li>
      </ul>

      <h2>Media contact</h2>
      <p><a href="mailto:support@machreach.com">support@machreach.com</a></p>
    """, "press")


@app.route("/roadmap")
def roadmap_page():
    return _public_info_page("Roadmap", "Next", "Where the product is heading next.", """
      <p class="mr-note">Next priorities: stronger Focus mode, course benchmarks, richer friend rankings, and smarter Plus analytics.</p>
      <h2>What that means</h2>
      <ul>
        <li><strong>Focus mode:</strong> richer session recovery, better reward moments, and clearer timer state.</li>
        <li><strong>Course benchmarks:</strong> more useful comparisons once enough real outcomes exist.</li>
        <li><strong>Friend rankings:</strong> tighter friend circles, private comparisons, and collaborative pressure.</li>
        <li><strong>Plus analytics:</strong> deeper patterns without making the dashboard feel heavy.</li>
      </ul>
    """, "roadmap")


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
    resp = make_response(app.send_static_file("sw.js"))
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
        "Sitemap: https://machreach.com/sitemap.xml\n"
    )
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    paths = ["/", "/about", "/blog", "/press", "/roadmap",
             "/privacy", "/terms", "/cookies", "/status", "/login", "/register"]
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
    from outreach.db import (
        get_client, get_campaigns, get_contacts, get_email_accounts, get_subscription, get_usage,
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
