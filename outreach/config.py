"""
Configuration — loads environment, defines constants.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

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
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "outreach.db"))

# App
_secret = os.getenv("SECRET_KEY", "")
if not _secret and os.getenv("RENDER", ""):
    raise RuntimeError("SECRET_KEY must be set in production")
SECRET_KEY = _secret or "dev-secret-change-me"
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
SENDER_NAME = os.getenv("SENDER_NAME", "Ignacio")

# Encryption key for email account passwords at rest (Fernet, 32-byte base64)
_enc_key = os.getenv("ENCRYPTION_KEY", "")
if not _enc_key and os.getenv("RENDER", ""):
    raise RuntimeError("ENCRYPTION_KEY must be set in production")
ENCRYPTION_KEY = _enc_key or "RGV2LWVuY3J5cHRpb24ta2V5LW5vdC1mb3ItcHJvZA=="  # dev-only placeholder

# Sending limits
DELAY_BETWEEN_EMAILS_SEC = int(os.getenv("DELAY_BETWEEN_EMAILS_SEC", "5"))  # seconds between sends
FOLLOWUP_DELAY_DAYS = [3, 7, 14]  # Days after initial email for follow-ups

# Apollo.io (prospect finder — free tier: 10k credits/month)
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")

# PayPal (billing — subscriptions via PayPal JS SDK)
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
PAYPAL_PLAN_GROWTH = os.getenv("PAYPAL_PLAN_GROWTH", "")         # PayPal Plan ID
PAYPAL_PLAN_PRO = os.getenv("PAYPAL_PLAN_PRO", "")
PAYPAL_PLAN_UNLIMITED = os.getenv("PAYPAL_PLAN_UNLIMITED", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")               # sandbox or live

# Sentry (error tracking — set SENTRY_DSN in production)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Plan limits
PLAN_LIMITS = {
    "free":      {"emails_per_month": 200,   "emails_per_day": 50,  "campaigns": 2,  "mail_hub_syncs": 10,  "ai_classify": False, "mailboxes": 1,  "price": 0},
    "growth":    {"emails_per_month": 2000,  "emails_per_day": 200, "campaigns": -1, "mail_hub_syncs": -1,  "ai_classify": True,  "mailboxes": 3,  "price": 800},
    "pro":       {"emails_per_month": 10000, "emails_per_day": 500, "campaigns": -1, "mail_hub_syncs": -1,  "ai_classify": True,  "mailboxes": 5,  "price": 2000},
    "unlimited": {"emails_per_month": -1,    "emails_per_day": -1,  "campaigns": -1, "mail_hub_syncs": -1,  "ai_classify": True,  "mailboxes": -1, "price": 4000},
}
