"""One-off helper: grant a user every banner + flag (incl. PLUS) and Ultimate tier.

Usage:
    python _grant_all_cosmetics.py <email>
"""
import json
import sys

from student import db as sdb
from student import subscription as ssub
from student.db import get_db, _exec, _fetchone, _ensure_wallet, BANNERS, FLAGS


def grant_all(email: str) -> None:
    with get_db() as db:
        row = _fetchone(db, "SELECT id, name, mail_preferences FROM clients WHERE email = %s", (email,))
    if not row:
        print(f"[!] No client found for {email}")
        sys.exit(1)
    cid = int(row["id"])
    print(f"[+] Found client #{cid} ({row.get('name')!r})")

    all_banners = list(BANNERS.keys())
    all_flags   = list(FLAGS.keys())

    with get_db() as db:
        _ensure_wallet(db, cid)
        _exec(db, "UPDATE student_wallet SET unlocked_banners = %s WHERE client_id = %s",
              (json.dumps(all_banners), cid))
        # Merge flags into mail_preferences without clobbering anything else.
        raw = (_fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (cid,)) or {}).get("mail_preferences") or ""
        try:
            prefs = json.loads(raw) if raw else {}
            if not isinstance(prefs, dict):
                prefs = {}
        except Exception:
            prefs = {}
        prefs["unlocked_flags"] = all_flags
        _exec(db, "UPDATE clients SET mail_preferences = %s WHERE id = %s",
              (json.dumps(prefs), cid))

    # Set tier = ultimate (covers plus_only post-beta)
    res = ssub.set_tier(cid, "ultimate")
    print(f"[+] Tier set: {res}")
    print(f"[+] Granted {len(all_banners)} banners and {len(all_flags)} flags.")


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "ignaciomachuca2005@gmail.com"
    grant_all(email)
