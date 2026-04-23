"""
Weekly + monthly leaderboard payouts.

Top 5 students in each scope (global / per-country / per-university /
per-major) win coins at the end of every calendar week (Sun -> Mon
boundary) and every calendar month (last -> 1st boundary). Higher-scope
boards pay more; within a scope, rank 1 pays the most. All winners get a
pop-up the next time they log in, and an admin email summarises every
winner across every scope.

Designed to be called lazily (e.g. from the dashboard endpoint) — the
`run_payouts_if_due()` function is idempotent and cheap when nothing is
owed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from outreach.db import get_db, _USE_PG

log = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────

_PRIZE_TABLES_PG = """
CREATE TABLE IF NOT EXISTS student_lb_payout_run (
    id           SERIAL PRIMARY KEY,
    period_kind  TEXT NOT NULL,          -- 'week' | 'month'
    period_key   TEXT NOT NULL,          -- ISO key, e.g. '2026-W17' or '2026-04'
    completed_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(period_kind, period_key)
);

CREATE TABLE IF NOT EXISTS student_lb_prize (
    id            SERIAL PRIMARY KEY,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind   TEXT NOT NULL,         -- 'week' | 'month'
    period_key    TEXT NOT NULL,         -- '2026-W17' or '2026-04'
    scope         TEXT NOT NULL,         -- 'global' | 'country' | 'university' | 'major'
    scope_value   TEXT,                  -- iso code / univ id / major id (NULL for global)
    rank          INTEGER NOT NULL,      -- 1..5
    coins         INTEGER NOT NULL,
    xp_in_period  INTEGER NOT NULL,
    shown         INTEGER DEFAULT 0,     -- pop-up acknowledged
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lb_prize_client ON student_lb_prize(client_id, shown);

CREATE TABLE IF NOT EXISTS student_lb_period_seen (
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    seen_at      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (client_id, period_kind, period_key)
);
"""

_PRIZE_TABLES_SQLITE = """
CREATE TABLE IF NOT EXISTS student_lb_payout_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    completed_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(period_kind, period_key)
);

CREATE TABLE IF NOT EXISTS student_lb_prize (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind   TEXT NOT NULL,
    period_key    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    scope_value   TEXT,
    rank          INTEGER NOT NULL,
    coins         INTEGER NOT NULL,
    xp_in_period  INTEGER NOT NULL,
    shown         INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_lb_prize_client ON student_lb_prize(client_id, shown);

CREATE TABLE IF NOT EXISTS student_lb_period_seen (
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    seen_at      TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (client_id, period_kind, period_key)
);
"""


def init_prize_tables() -> None:
    """Create the prize-tracking tables. Idempotent."""
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(_PRIZE_TABLES_PG)
        else:
            db.executescript(_PRIZE_TABLES_SQLITE)


# ── Prize amounts ───────────────────────────────────────────────────────

# Coins per (scope, rank). Monthly payout uses these directly; weekly is
# half (rounded down) so the month is always more rewarding than 4 weeks.
_PRIZE_TABLE: dict[str, dict[int, int]] = {
    "global":     {1: 500, 2: 300, 3: 200, 4: 100, 5: 50},
    "country":    {1: 300, 2: 200, 3: 100, 4:  60, 5: 30},
    "university": {1: 150, 2: 100, 3:  60, 4:  40, 5: 20},
    "major":      {1:  80, 2:  50, 3:  30, 4:  20, 5: 10},
}


def _coins_for(period_kind: str, scope: str, rank: int) -> int:
    base = _PRIZE_TABLE.get(scope, {}).get(rank, 0)
    if period_kind == "week":
        return base // 2
    return base


# ── Period bookkeeping ──────────────────────────────────────────────────

def _period_key(kind: str, ref: date | None = None) -> str:
    """Stable identifier for the period that JUST ENDED.

    Called right after a boundary (Mon 00:01 / 1st of month 00:01), the key
    refers to the period that closed seconds ago — the one whose winners we
    are about to pay out.
    """
    ref = ref or date.today()
    if kind == "week":
        # The week that just ended is "yesterday's ISO week" relative to a
        # Monday-morning run. Yesterday is always inside the closed week.
        y = ref - timedelta(days=1)
        iso = y.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if kind == "month":
        # The month that ended is the month of yesterday.
        y = ref - timedelta(days=1)
        return f"{y.year:04d}-{y.month:02d}"
    raise ValueError(f"Unknown period kind: {kind}")


def _period_window(kind: str, period_key: str) -> tuple[str, str]:
    """Return (start_iso_dt, end_iso_dt) for the period_key — half-open."""
    if kind == "week":
        # period_key like '2026-W17'
        year, wk = period_key.split("-W")
        # ISO Monday of that week
        monday = datetime.fromisocalendar(int(year), int(wk), 1).date()
        next_monday = monday + timedelta(days=7)
        return (monday.isoformat() + " 00:00:00",
                next_monday.isoformat() + " 00:00:00")
    if kind == "month":
        year, mo = period_key.split("-")
        first = date(int(year), int(mo), 1)
        if first.month == 12:
            next_first = date(first.year + 1, 1, 1)
        else:
            next_first = date(first.year, first.month + 1, 1)
        return (first.isoformat() + " 00:00:00",
                next_first.isoformat() + " 00:00:00")
    raise ValueError(f"Unknown period kind: {kind}")


def _is_due(kind: str, today: date | None = None) -> bool:
    """Has the boundary just passed AND we haven't run the payout yet?"""
    today = today or date.today()
    # Weekly payout fires on Mondays (ISO weekday 1). Monthly fires on
    # day 1 of the month. We tolerate the whole day so a missed run still
    # pays out later.
    if kind == "week" and today.isoweekday() != 1:
        return False
    if kind == "month" and today.day != 1:
        return False
    key = _period_key(kind, today)
    with get_db() as db:
        from outreach.db import _fetchval
        existing = _fetchval(
            db,
            "SELECT id FROM student_lb_payout_run WHERE period_kind = %s AND period_key = %s",
            (kind, key),
        )
    return not existing


def _mark_run(kind: str, period_key: str) -> None:
    with get_db() as db:
        from outreach.db import _exec
        _exec(
            db,
            "INSERT INTO student_lb_payout_run (period_kind, period_key) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (kind, period_key),
        )


# ── Top-N query (per scope bucket) ──────────────────────────────────────

def _top5_per_bucket(scope: str, period_kind: str, period_key: str) -> list[dict]:
    """Top 5 per scope bucket within the closed period. Returns rows of
    {client_id, name, scope_value, rank, xp}. For scope='global' there's
    one bucket; for the others, a bucket per country/univ/major."""
    start_iso, end_iso = _period_window(period_kind, period_key)

    if scope == "global":
        bucket_col = "NULL"
        partition_by = ""
        group_extra = ""
    elif scope == "country":
        bucket_col = "c.country_iso"
        partition_by = "PARTITION BY c.country_iso"
        group_extra = ", c.country_iso"
    elif scope == "university":
        bucket_col = "CAST(c.university_id AS TEXT)"
        partition_by = "PARTITION BY c.university_id"
        group_extra = ", c.university_id"
    elif scope == "major":
        bucket_col = "CAST(c.major_id AS TEXT)"
        partition_by = "PARTITION BY c.major_id"
        group_extra = ", c.major_id"
    else:
        return []

    bucket_filter = ""
    if scope == "country":
        bucket_filter = " AND c.country_iso IS NOT NULL "
    elif scope == "university":
        bucket_filter = " AND c.university_id IS NOT NULL "
    elif scope == "major":
        bucket_filter = " AND c.major_id IS NOT NULL "

    if scope == "global":
        # Single bucket — no window function needed.
        q = (
            f"SELECT c.id AS client_id, c.name AS display_name, "
            f"       {bucket_col} AS bucket, "
            f"       COALESCE(SUM(x.xp), 0) AS total_xp "
            f"FROM clients c "
            f"LEFT JOIN student_xp x ON x.client_id = c.id "
            f"  AND x.created_at >= %s AND x.created_at < %s "
            f"WHERE c.account_type = 'student' AND COALESCE(c.retired,0) = 0 "
            f"{bucket_filter}"
            f"GROUP BY c.id, c.name "
            f"HAVING COALESCE(SUM(x.xp),0) > 0 "
            f"ORDER BY total_xp DESC, c.id ASC LIMIT 5"
        )
        params: tuple = (start_iso, end_iso)
        with get_db() as db:
            from outreach.db import _fetchall
            rows = _fetchall(db, q, params)
        return [
            {
                "client_id": r["client_id"],
                "name": r.get("display_name") or "Student",
                "scope_value": None,
                "rank": idx + 1,
                "xp": int(r.get("total_xp") or 0),
            }
            for idx, r in enumerate(rows)
        ]

    # Bucketed scopes — use ROW_NUMBER() over each bucket and keep top 5.
    inner = (
        f"SELECT c.id AS client_id, c.name AS display_name, "
        f"       {bucket_col} AS bucket, "
        f"       COALESCE(SUM(x.xp),0) AS total_xp "
        f"FROM clients c "
        f"LEFT JOIN student_xp x ON x.client_id = c.id "
        f"  AND x.created_at >= %s AND x.created_at < %s "
        f"WHERE c.account_type = 'student' AND COALESCE(c.retired,0) = 0 "
        f"{bucket_filter}"
        f"GROUP BY c.id, c.name{group_extra} "
        f"HAVING COALESCE(SUM(x.xp),0) > 0"
    )
    q = (
        f"SELECT * FROM ("
        f"  SELECT *, ROW_NUMBER() OVER ({partition_by} ORDER BY total_xp DESC, client_id ASC) AS rn "
        f"  FROM ({inner}) sub"
        f") ranked WHERE rn <= 5 ORDER BY bucket, rn"
    )
    with get_db() as db:
        from outreach.db import _fetchall
        rows = _fetchall(db, q, (start_iso, end_iso))
    return [
        {
            "client_id": r["client_id"],
            "name": r.get("display_name") or "Student",
            "scope_value": r.get("bucket"),
            "rank": int(r.get("rn") or 0),
            "xp": int(r.get("total_xp") or 0),
        }
        for r in rows
    ]


# ── Award + email ───────────────────────────────────────────────────────

def _award_winners(period_kind: str, period_key: str) -> dict:
    """Insert prize records and credit coins. Returns a summary dict
    keyed by scope used by the email body."""
    from . import db as sdb
    from outreach.db import _exec

    summary: dict[str, list[dict]] = {"global": [], "country": [],
                                       "university": [], "major": []}
    for scope in ("global", "country", "university", "major"):
        winners = _top5_per_bucket(scope, period_kind, period_key)
        for w in winners:
            coins = _coins_for(period_kind, scope, w["rank"])
            if coins <= 0:
                continue
            with get_db() as db:
                _exec(
                    db,
                    "INSERT INTO student_lb_prize "
                    "(client_id, period_kind, period_key, scope, scope_value, rank, coins, xp_in_period) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (w["client_id"], period_kind, period_key, scope,
                     str(w["scope_value"]) if w["scope_value"] is not None else None,
                     w["rank"], coins, w["xp"]),
                )
            sdb.add_coins(w["client_id"], coins,
                          f"lb_{period_kind}_{scope}_rank{w['rank']}")
            summary[scope].append({**w, "coins": coins})
    return summary


def _email_winners(period_kind: str, period_key: str, summary: dict) -> None:
    """Email each winner a personal congrats."""
    try:
        from app import _send_system_email
    except Exception:
        log.warning("Could not import _send_system_email; skipping winner emails")
        return

    from outreach.db import _fetchone
    label = "weekly" if period_kind == "week" else "monthly"
    scope_pretty = {
        "global": "Global",
        "country": "Country",
        "university": "University",
        "major": "Major",
    }
    # Aggregate per-client across scopes so each winner gets ONE email
    # listing every category they placed in.
    per_client: dict[int, list[dict]] = {}
    for scope, winners in summary.items():
        for w in winners:
            per_client.setdefault(w["client_id"], []).append(
                {**w, "scope": scope}
            )
    for client_id, prizes in per_client.items():
        try:
            with get_db() as db:
                row = _fetchone(
                    db, "SELECT email, name FROM clients WHERE id = %s", (client_id,)
                )
            email = (row or {}).get("email") if row else None
            name = (row or {}).get("name") if row else None
            if not email:
                continue
            total = sum(p["coins"] for p in prizes)
            lines = [
                f"Hi {name or 'there'},",
                "",
                f"Great news — you placed on the {label} MachReach leaderboards ({period_key}):",
                "",
            ]
            for p in sorted(prizes, key=lambda x: (-x["coins"], x["scope"])):
                bucket = (
                    f" [{p.get('scope_value')}]" if p.get("scope_value") else ""
                )
                lines.append(
                    f"  • {scope_pretty.get(p['scope'], p['scope'])}{bucket} "
                    f"— rank #{p['rank']}  →  +{p['coins']} coins"
                )
            lines += [
                "",
                f"Total reward: {total} coins. They've already been credited to your wallet.",
                "",
                "Keep grinding — see you on next period's board!",
                "",
                "— MachReach",
            ]
            _send_system_email(
                email,
                f"You won {total} coins on the {label} leaderboard!",
                "\n".join(lines),
            )
        except Exception as e:
            log.warning("Winner email to client %s failed: %s", client_id, e)


def _email_admin(period_kind: str, period_key: str, summary: dict) -> None:
    """Send admin a digest of every winner across every scope."""
    try:
        from app import _send_system_email
    except Exception:
        log.warning("Could not import _send_system_email; skipping admin notify")
        return

    label = "Weekly" if period_kind == "week" else "Monthly"
    lines = [
        f"{label} leaderboard payouts complete — {period_key}",
        "",
    ]
    for scope in ("global", "country", "university", "major"):
        winners = summary.get(scope) or []
        if not winners:
            continue
        lines.append(f"=== {scope.upper()} ===")
        # Group by bucket (scope_value) for non-global scopes
        if scope == "global":
            for w in winners:
                lines.append(
                    f"  #{w['rank']}  {w['name']} (id={w['client_id']})  "
                    f"— {w['xp']} XP  → {w['coins']} coins"
                )
        else:
            buckets: dict[str, list[dict]] = {}
            for w in winners:
                buckets.setdefault(str(w.get("scope_value") or "?"), []).append(w)
            for bucket, ws in buckets.items():
                lines.append(f"  [{scope}={bucket}]")
                for w in ws:
                    lines.append(
                        f"    #{w['rank']}  {w['name']} (id={w['client_id']})  "
                        f"— {w['xp']} XP  → {w['coins']} coins"
                    )
        lines.append("")
    body = "\n".join(lines)
    try:
        _send_system_email(
            "ignaciomachuca2005@gmail.com",
            f"[MachReach] {label} leaderboard payouts — {period_key}",
            body,
        )
    except Exception as e:
        log.warning("Admin payout email failed: %s", e)


# ── Public entrypoint ───────────────────────────────────────────────────

def run_payouts_if_due() -> dict:
    """Cheap check + run for both weekly and monthly. Safe to call on
    every dashboard load."""
    init_prize_tables()
    out = {"week": None, "month": None}
    today = date.today()
    for kind in ("week", "month"):
        try:
            if not _is_due(kind, today):
                continue
            key = _period_key(kind, today)
            log.info("Running %s leaderboard payout for %s", kind, key)
            summary = _award_winners(kind, key)
            _email_admin(kind, key, summary)
            _email_winners(kind, key, summary)
            _mark_run(kind, key)
            out[kind] = {"period_key": key, "winners": sum(len(v) for v in summary.values())}
        except Exception as e:
            log.exception("Leaderboard payout (%s) failed: %s", kind, e)
    return out


# ── Pop-up support: pending winnings for a single user ──────────────────

def get_unshown_prizes(client_id: int) -> list[dict]:
    """Return prize records the user hasn't seen the pop-up for yet."""
    init_prize_tables()
    from outreach.db import _fetchall
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT id, period_kind, period_key, scope, scope_value, rank, coins, xp_in_period "
            "FROM student_lb_prize WHERE client_id = %s AND shown = 0 "
            "ORDER BY id ASC",
            (client_id,),
        )
    return [dict(r) for r in rows]


def mark_prizes_shown(client_id: int, prize_ids: list[int]) -> None:
    if not prize_ids:
        return
    from outreach.db import _exec
    placeholders = ",".join(["%s"] * len(prize_ids))
    with get_db() as db:
        _exec(
            db,
            f"UPDATE student_lb_prize SET shown = 1 "
            f"WHERE client_id = %s AND id IN ({placeholders})",
            (client_id, *prize_ids),
        )


# ── Pop-up support: every-user "here's how you placed" results ──────────

def _user_rank_in_scope(client_id: int, scope: str, period_kind: str,
                        period_key: str) -> dict | None:
    """Compute (rank, total_in_bucket, xp) for one user in one scope.
    Returns None if the user is not eligible for this scope (e.g. no
    country set) or had 0 XP in the period."""
    start_iso, end_iso = _period_window(period_kind, period_key)
    from outreach.db import _fetchone, _fetchall

    # Find the user's bucket value (country/uni/major) so we can scope the
    # ranking query appropriately.
    bucket_filter = ""
    if scope == "country":
        with get_db() as db:
            r = _fetchone(db, "SELECT country_iso FROM clients WHERE id = %s",
                          (client_id,))
        bucket = (r or {}).get("country_iso") if r else None
        if not bucket:
            return None
        bucket_filter = " AND c.country_iso = %s "
        bucket_args: tuple = (bucket,)
    elif scope == "university":
        with get_db() as db:
            r = _fetchone(db, "SELECT university_id FROM clients WHERE id = %s",
                          (client_id,))
        bucket = (r or {}).get("university_id") if r else None
        if not bucket:
            return None
        bucket_filter = " AND c.university_id = %s "
        bucket_args = (bucket,)
    elif scope == "major":
        with get_db() as db:
            r = _fetchone(db, "SELECT major_id FROM clients WHERE id = %s",
                          (client_id,))
        bucket = (r or {}).get("major_id") if r else None
        if not bucket:
            return None
        bucket_filter = " AND c.major_id = %s "
        bucket_args = (bucket,)
    elif scope == "global":
        bucket_args = ()
    else:
        return None

    q = (
        f"WITH ranked AS ("
        f"  SELECT c.id AS client_id, COALESCE(SUM(x.xp), 0) AS total_xp "
        f"  FROM clients c "
        f"  LEFT JOIN student_xp x ON x.client_id = c.id "
        f"    AND x.created_at >= %s AND x.created_at < %s "
        f"  WHERE c.account_type = 'student' AND COALESCE(c.retired, 0) = 0 "
        f"  {bucket_filter} "
        f"  GROUP BY c.id "
        f"  HAVING COALESCE(SUM(x.xp), 0) > 0 "
        f") "
        f"SELECT client_id, total_xp, "
        f"       RANK() OVER (ORDER BY total_xp DESC, client_id ASC) AS rnk, "
        f"       (SELECT COUNT(*) FROM ranked) AS bucket_total "
        f"FROM ranked"
    )
    params = (start_iso, end_iso, *bucket_args)
    try:
        with get_db() as db:
            rows = _fetchall(db, q, params)
    except Exception as e:
        log.warning("rank query failed (%s/%s): %s", scope, period_kind, e)
        return None
    me = next((r for r in rows if int(r.get("client_id") or 0) == int(client_id)), None)
    if not me:
        return None
    return {
        "rank": int(me.get("rnk") or 0),
        "total_in_bucket": int(me.get("bucket_total") or 0),
        "xp": int(me.get("total_xp") or 0),
    }


def get_pending_period_results(client_id: int) -> list[dict]:
    """Return a list of fully-rendered period summaries the user has not
    yet acknowledged. Each entry contains ranks across all scopes plus
    any prize amounts they won in that period."""
    init_prize_tables()
    from outreach.db import _fetchall

    # Grab all completed payout runs the user hasn't seen.
    with get_db() as db:
        runs = _fetchall(
            db,
            "SELECT period_kind, period_key, completed_at FROM student_lb_payout_run "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM student_lb_period_seen s "
            "  WHERE s.client_id = %s AND s.period_kind = student_lb_payout_run.period_kind "
            "    AND s.period_key = student_lb_payout_run.period_key"
            ") ORDER BY completed_at ASC",
            (client_id,),
        )

    out: list[dict] = []
    for run in runs:
        kind = run.get("period_kind")
        key = run.get("period_key")
        # Per-scope rank lookup
        scopes_data: dict[str, dict | None] = {}
        for scope in ("global", "country", "university", "major"):
            scopes_data[scope] = _user_rank_in_scope(client_id, scope, kind, key)
        # Prizes in this period
        with get_db() as db:
            prize_rows = _fetchall(
                db,
                "SELECT scope, scope_value, rank, coins, xp_in_period "
                "FROM student_lb_prize WHERE client_id = %s "
                "AND period_kind = %s AND period_key = %s",
                (client_id, kind, key),
            )
        prizes_by_scope: dict[str, dict] = {}
        total_coins = 0
        for p in prize_rows:
            prizes_by_scope[p["scope"]] = dict(p)
            total_coins += int(p.get("coins") or 0)

        out.append({
            "period_kind": kind,
            "period_key": key,
            "scopes": scopes_data,
            "prizes_by_scope": {k: dict(v) for k, v in prizes_by_scope.items()},
            "total_coins_won": total_coins,
        })
    return out


def mark_period_seen(client_id: int, period_kind: str, period_key: str) -> None:
    from outreach.db import _exec
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_period_seen (client_id, period_kind, period_key) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (client_id, period_kind, period_key),
        )
