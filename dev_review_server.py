"""Throwaway launcher for UI review (safe sandbox).

Runs the app against an ISOLATED temp SQLite DB (never touches data/outreach.db),
with SMTP / OpenAI / Postgres disabled so nothing real can be sent or billed.
Seeds one verified test account:  review@test.local / reviewpass123

Delete this file after the review.
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

_db = Path(tempfile.gettempdir()) / "machreach_review.db"

# Must be set BEFORE outreach.config loads .env (load_dotenv never overrides
# existing env vars).
os.environ["DATABASE_URL"] = ""                 # force SQLite fallback
os.environ["DATABASE_PATH"] = str(_db)          # isolated throwaway DB
os.environ["SMTP_USER"] = ""                    # no real email can be sent
os.environ["SMTP_PASSWORD"] = ""
os.environ["IMAP_USER"] = ""
os.environ["IMAP_PASSWORD"] = ""
os.environ["OPENAI_API_KEY"] = ""               # no paid AI calls

import app as appmod  # noqa: E402  (env must be set first)

appmod.init_db()

import bcrypt  # noqa: E402
from outreach.db import get_db, _exec, _fetchone  # noqa: E402

with get_db() as db:
    if not _fetchone(db, "SELECT id FROM clients WHERE email = %s", ("review@test.local",)):
        pw_hash = bcrypt.hashpw(b"reviewpass123", bcrypt.gensalt(12)).decode()
        _exec(
            db,
            "INSERT INTO clients (name, email, password, account_type, email_verified, academic_setup_complete) "
            "VALUES (%s, %s, %s, %s, 1, 1)",
            ("Review Tester", "review@test.local", pw_hash, "student"),
        )
        print(f"[review] seeded test user review@test.local in {_db}", flush=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5123))
    print(f"[review] starting on http://127.0.0.1:{port} (db: {_db})", flush=True)
    appmod.app.run(debug=False, host="127.0.0.1", port=port)
