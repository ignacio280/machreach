"""
Google Calendar integration — OAuth + read/write events.

Used by both student and business sides so the AI can schedule around
existing events and write new ones (study blocks, focus sessions,
follow-ups, meetings).

Setup (env vars):
- GOOGLE_OAUTH_CLIENT_ID
- GOOGLE_OAUTH_CLIENT_SECRET
- GOOGLE_OAUTH_REDIRECT_URI   (e.g. https://machreach.com/gcal/callback)

DB table `google_calendar_tokens` stores per-client encrypted refresh
tokens so we can keep access without re-prompting users.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import requests

from outreach.db import _USE_PG, get_db, encrypt_password, decrypt_password, _exec, _fetchone

log = logging.getLogger("machreach.gcal")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")

# Read events + manage user-created events on primary calendar
GCAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "email",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"


# ────────────────────────────────────────────────────────────────────
# Schema (applied lazily on first read/write)
# ────────────────────────────────────────────────────────────────────
_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS google_calendar_tokens (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL UNIQUE,
    google_email    TEXT DEFAULT '',
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TIMESTAMP,
    scopes          TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gcal_client ON google_calendar_tokens(client_id);
"""
_SCHEMA_SQLITE = _SCHEMA_PG.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT") \
    .replace("TIMESTAMP DEFAULT NOW()", "TEXT DEFAULT (datetime('now', 'localtime'))") \
    .replace("TIMESTAMP", "TEXT")

_SCHEMA_INITIALIZED = False


def _ensure_schema() -> None:
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    try:
        with get_db() as db:
            if _USE_PG:
                db.cursor().execute(_SCHEMA_PG)
            else:
                db.executescript(_SCHEMA_SQLITE)
        _SCHEMA_INITIALIZED = True
    except Exception as e:
        log.warning("gcal schema init failed: %s", e)


# ────────────────────────────────────────────────────────────────────
# Token storage
# ────────────────────────────────────────────────────────────────────
def save_tokens(client_id: int, access_token: str, refresh_token: str,
                expires_in: int, google_email: str = "", scopes: str = "") -> None:
    _ensure_schema()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, int(expires_in or 0)))
    enc_access = encrypt_password(access_token)
    enc_refresh = encrypt_password(refresh_token) if refresh_token else ""
    with get_db() as db:
        existing = _fetchone(db, "SELECT id, refresh_token FROM google_calendar_tokens WHERE client_id = %s",
                             (client_id,))
        if existing:
            # Keep existing refresh_token if Google didn't send a new one (it often doesn't on re-consent)
            final_refresh = enc_refresh or existing["refresh_token"]
            _exec(db,
                  "UPDATE google_calendar_tokens SET google_email=%s, access_token=%s, refresh_token=%s, "
                  "expires_at=%s, scopes=%s WHERE client_id=%s",
                  (google_email, enc_access, final_refresh, expires_at, scopes, client_id))
        else:
            if not enc_refresh:
                log.warning("gcal: no refresh_token returned for new connection (client=%s)", client_id)
            _exec(db,
                  "INSERT INTO google_calendar_tokens (client_id, google_email, access_token, refresh_token, "
                  "expires_at, scopes) VALUES (%s, %s, %s, %s, %s, %s)",
                  (client_id, google_email, enc_access, enc_refresh, expires_at, scopes))


def get_token_record(client_id: int) -> Optional[dict]:
    _ensure_schema()
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM google_calendar_tokens WHERE client_id = %s", (client_id,))


def delete_tokens(client_id: int) -> None:
    _ensure_schema()
    with get_db() as db:
        _exec(db, "DELETE FROM google_calendar_tokens WHERE client_id = %s", (client_id,))


def is_connected(client_id: int) -> bool:
    rec = get_token_record(client_id)
    return bool(rec and rec.get("refresh_token"))


def get_connected_email(client_id: int) -> str:
    rec = get_token_record(client_id)
    return (rec or {}).get("google_email", "") if rec else ""


# ────────────────────────────────────────────────────────────────────
# OAuth helpers
# ────────────────────────────────────────────────────────────────────
def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


def build_auth_url(state: str) -> str:
    """Return the URL to redirect the user to for Google's consent screen."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GCAL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token return
        "state": state,
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(code: str) -> dict:
    """Exchange auth code for tokens. Returns dict with access_token, refresh_token, expires_in."""
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    try:
        r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("gcal userinfo failed: %s", e)
        return {}


def refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "refresh_token": refresh_token,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _valid_access_token(client_id: int) -> Optional[str]:
    """Return a usable access token, refreshing if needed. None if not connected."""
    rec = get_token_record(client_id)
    if not rec:
        return None
    refresh_token = decrypt_password(rec["refresh_token"]) if rec.get("refresh_token") else ""
    if not refresh_token:
        return None
    # Check expiry
    expires_at = rec.get("expires_at")
    needs_refresh = True
    if expires_at:
        try:
            exp = expires_at if isinstance(expires_at, datetime) else datetime.fromisoformat(str(expires_at))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            needs_refresh = exp <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except Exception:
            needs_refresh = True
    if not needs_refresh and rec.get("access_token"):
        try:
            return decrypt_password(rec["access_token"])
        except Exception:
            pass
    # Refresh
    try:
        new = refresh_access_token(refresh_token)
        save_tokens(
            client_id,
            access_token=new["access_token"],
            refresh_token=refresh_token,  # reuse
            expires_in=int(new.get("expires_in") or 3600),
            google_email=rec.get("google_email", ""),
            scopes=rec.get("scopes", ""),
        )
        return new["access_token"]
    except Exception as e:
        log.warning("gcal token refresh failed (client=%s): %s", client_id, e)
        return None


# ────────────────────────────────────────────────────────────────────
# Calendar API
# ────────────────────────────────────────────────────────────────────
def list_events(client_id: int, days_ahead: int = 14, max_results: int = 50,
                calendar_id: str = "primary") -> list[dict]:
    """Return upcoming events as a normalised list."""
    token = _valid_access_token(client_id)
    if not token:
        return []
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=max(1, days_ahead))
    params = {
        "timeMin": now.isoformat().replace("+00:00", "Z"),
        "timeMax": end.isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(250, int(max_results))),
    }
    try:
        r = requests.get(
            EVENTS_URL.format(cal=calendar_id),
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception as e:
        log.warning("gcal list_events failed (client=%s): %s", client_id, e)
        return []
    out = []
    for ev in items:
        start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
        end_t = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date") or ""
        out.append({
            "id": ev.get("id", ""),
            "title": ev.get("summary", "(no title)"),
            "start": start,
            "end": end_t,
            "location": ev.get("location", ""),
            "description": (ev.get("description") or "")[:500],
            "html_link": ev.get("htmlLink", ""),
            "attendees": [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")],
            "all_day": "date" in (ev.get("start") or {}),
        })
    return out


def create_event(client_id: int, title: str, start_iso: str, end_iso: str,
                 description: str = "", location: str = "",
                 attendee_emails: list[str] | None = None,
                 calendar_id: str = "primary") -> Optional[dict]:
    """Create a calendar event. Times must be ISO 8601 with timezone."""
    token = _valid_access_token(client_id)
    if not token:
        return None
    body = {
        "summary": title,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendee_emails:
        body["attendees"] = [{"email": e} for e in attendee_emails]
    try:
        r = requests.post(
            EVENTS_URL.format(cal=calendar_id),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("gcal create_event failed (client=%s): %s", client_id, e)
        return None


def delete_event(client_id: int, event_id: str, calendar_id: str = "primary") -> bool:
    token = _valid_access_token(client_id)
    if not token or not event_id:
        return False
    try:
        r = requests.delete(
            EVENTS_URL.format(cal=calendar_id) + f"/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        log.warning("gcal delete_event failed: %s", e)
        return False


# ────────────────────────────────────────────────────────────────────
# Helpers for AI prompts (used by student/professional planners)
# ────────────────────────────────────────────────────────────────────
def events_summary_for_ai(client_id: int, days_ahead: int = 7) -> str:
    """Compact human-readable summary of upcoming events for AI context."""
    events = list_events(client_id, days_ahead=days_ahead, max_results=40)
    if not events:
        return ""
    lines = []
    for e in events[:30]:
        when = e["start"][:16].replace("T", " ")
        title = e["title"]
        loc = f" @ {e['location']}" if e.get("location") else ""
        lines.append(f"- {when}: {title}{loc}")
    return "\n".join(lines)
