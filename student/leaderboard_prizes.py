"""
Weekly + monthly leaderboard payouts.

Top 5 students in each scope (global / per-country / per-university /
per-major) win coins at the end of every calendar week (Sun -> Mon
boundary) and every calendar month (last -> 1st boundary). Higher-scope
boards pay more; within a scope, rank 1 pays the most. All winners get a
pop-up the next time they log in, and an admin email summarises every
winner across every scope.

The background worker polls this module. Database claims, a unique prize
ledger, atomic wallet credits, and a durable email outbox make concurrent
execution and crash recovery safe.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from machreach_core.db import get_db, _USE_PG
from student.periods import (
    latest_closed_leaderboard_period,
    leaderboard_period_key,
    leaderboard_period_window,
    next_leaderboard_period,
    utc_now,
    zoned_date,
)

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """UTC-aware timestamp for payout persistence and comparisons."""
    return utc_now()


# ── Schema ──────────────────────────────────────────────────────────────

_PRIZE_TABLES_PG = """
CREATE TABLE IF NOT EXISTS student_lb_payout_run (
    id           SERIAL PRIMARY KEY,
    period_kind  TEXT NOT NULL,          -- 'week' | 'month'
    period_key   TEXT NOT NULL,          -- ISO key, e.g. '2026-W17' or '2026-04'
    status       TEXT NOT NULL DEFAULT 'completed',
    claim_token  TEXT,
    claimed_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT NOT NULL DEFAULT '',
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
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lb_prize_client ON student_lb_prize(client_id, shown);

CREATE TABLE IF NOT EXISTS student_lb_payout_cursor (
    period_kind    TEXT PRIMARY KEY,
    next_period_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student_lb_email_outbox (
    id            SERIAL PRIMARY KEY,
    event_key     TEXT NOT NULL UNIQUE,
    period_kind   TEXT NOT NULL,
    period_key    TEXT NOT NULL,
    client_id     INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    recipient     TEXT NOT NULL,
    subject       TEXT NOT NULL,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    claimed_at    TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    sent_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lb_email_outbox_pending
ON student_lb_email_outbox(status, id);

CREATE TABLE IF NOT EXISTS student_lb_period_seen (
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    seen_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (client_id, period_kind, period_key)
);
"""

_PRIZE_TABLES_SQLITE = """
CREATE TABLE IF NOT EXISTS student_lb_payout_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'completed',
    claim_token  TEXT,
    claimed_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT NOT NULL DEFAULT '',
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
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lb_prize_client ON student_lb_prize(client_id, shown);

CREATE TABLE IF NOT EXISTS student_lb_payout_cursor (
    period_kind     TEXT PRIMARY KEY,
    next_period_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student_lb_email_outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key     TEXT NOT NULL UNIQUE,
    period_kind   TEXT NOT NULL,
    period_key    TEXT NOT NULL,
    client_id     INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    recipient     TEXT NOT NULL,
    subject       TEXT NOT NULL,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT NOT NULL DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    claimed_at    TEXT,
    next_attempt_at TEXT,
    sent_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lb_email_outbox_pending
ON student_lb_email_outbox(status, id);

CREATE TABLE IF NOT EXISTS student_lb_period_seen (
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period_kind  TEXT NOT NULL,
    period_key   TEXT NOT NULL,
    seen_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (client_id, period_kind, period_key)
);
"""


def init_prize_tables() -> None:
    """Create the prize-tracking tables. Idempotent."""
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(_PRIZE_TABLES_PG)
            for table, columns in {
                "student_lb_payout_run": ("claimed_at", "completed_at"),
                "student_lb_prize": ("created_at",),
                "student_lb_email_outbox": (
                    "created_at",
                    "claimed_at",
                    "next_attempt_at",
                    "sent_at",
                ),
                "student_lb_period_seen": ("seen_at",),
            }.items():
                for column in columns:
                    cur.execute(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = %s AND column_name = %s",
                        (table, column),
                    )
                    row = cur.fetchone()
                    data_type = (
                        row.get("data_type") if hasattr(row, "get") else row[0]
                    ) if row else None
                    if data_type == "timestamp without time zone":
                        cur.execute(
                            f"ALTER TABLE {table} ALTER COLUMN {column} "
                            f"TYPE TIMESTAMPTZ USING {column} AT TIME ZONE 'UTC'"
                        )
        else:
            db.executescript(_PRIZE_TABLES_SQLITE)
    # Upgrade installations created before payout claims and the outbox existed.
    from machreach_core.db import _exec, _is_duplicate_column_error
    for column, definition in (
        ("status", "TEXT NOT NULL DEFAULT 'completed'"),
        ("claim_token", "TEXT"),
        ("claimed_at", "TIMESTAMP"),
        ("attempts", "INTEGER NOT NULL DEFAULT 1"),
        ("last_error", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE student_lb_payout_run ADD COLUMN {column} {definition}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    from machreach_core.db import _fetchall
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_lb_payout_run SET status = 'completed' "
            "WHERE status IS NULL OR status = ''",
        )
        duplicates = _fetchall(
            db,
            "SELECT client_id, period_kind, period_key, scope, "
            "COALESCE(scope_value, '') AS scope_value, COUNT(*) AS duplicate_count "
            "FROM student_lb_prize "
            "GROUP BY client_id, period_kind, period_key, scope, COALESCE(scope_value, '') "
            "HAVING COUNT(*) > 1 LIMIT 10",
        )
        if duplicates:
            raise RuntimeError(
                "Leaderboard prize migration blocked: duplicate legacy awards "
                f"require wallet reconciliation before enforcing uniqueness: {duplicates}"
            )
        duplicate_ranks = _fetchall(
            db,
            "SELECT period_kind, period_key, scope, COALESCE(scope_value, '') AS scope_value, "
            "rank, COUNT(*) AS duplicate_count FROM student_lb_prize "
            "GROUP BY period_kind, period_key, scope, COALESCE(scope_value, ''), rank "
            "HAVING COUNT(*) > 1 LIMIT 10",
        )
        if duplicate_ranks:
            raise RuntimeError(
                "Leaderboard prize migration blocked: duplicate legacy rank awards "
                f"require wallet reconciliation: {duplicate_ranks}"
            )
        _exec(
            db,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_lb_prize_award "
            "ON student_lb_prize(client_id, period_kind, period_key, scope, "
            "COALESCE(scope_value, ''))",
        )
        _exec(
            db,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_lb_prize_rank "
            "ON student_lb_prize(period_kind, period_key, scope, "
            "COALESCE(scope_value, ''), rank)",
        )


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
    ref = ref or zoned_date("America/Santiago")
    if kind == "week":
        # The week that just ended is "yesterday's ISO week" relative to a
        # Monday-morning run. Yesterday is always inside the closed week.
        y = ref - timedelta(days=1)
        return leaderboard_period_key("weekly", y)
    if kind == "month":
        # The month that ended is the month of yesterday.
        y = ref - timedelta(days=1)
        return leaderboard_period_key("monthly", y)
    raise ValueError(f"Unknown period kind: {kind}")


def _period_window(kind: str, period_key: str) -> tuple[str, str]:
    """Return (start_iso_dt, end_iso_dt) for the period_key — half-open."""
    normalized_kind = "weekly" if kind == "week" else "monthly"
    start, end = leaderboard_period_window(normalized_kind, period_key)
    return start.isoformat() + " 00:00:00", end.isoformat() + " 00:00:00"


def _xp_period_window(
    kind: str, period_key: str, *, postgres: bool | None = None
) -> tuple[str, str]:
    """Convert the shared Santiago calendar period to UTC instants."""
    start, end = _period_window(kind, period_key)
    del postgres
    timezone = ZoneInfo("America/Santiago")
    converted = []
    for value in (start, end):
        local = datetime.fromisoformat(value).replace(tzinfo=timezone)
        converted.append(local.astimezone(UTC).isoformat())
    return converted[0], converted[1]


def _latest_closed_period_key(kind: str, as_of: date) -> str:
    """Return the most recent fully closed period on any day of the week/month."""
    normalized_kind = "weekly" if kind == "week" else "monthly"
    return latest_closed_leaderboard_period(normalized_kind, as_of)


def _next_period_key(kind: str, period_key: str) -> str:
    normalized_kind = "weekly" if kind == "week" else "monthly"
    return next_leaderboard_period(normalized_kind, period_key)


def _periods_to_process(kind: str, latest: str, limit: int) -> list[str]:
    """Read or seed the durable cursor, then return an ordered catch-up batch."""
    from machreach_core.db import _exec, _fetchone

    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        last = _fetchone(
            db,
            "SELECT period_key FROM student_lb_payout_run "
            "WHERE period_kind = %s AND status = 'completed' "
            "ORDER BY period_key DESC LIMIT 1",
            (kind,),
        )
        last_key = str((last or {}).get("period_key") or "")
        initial = _next_period_key(kind, last_key) if last_key and last_key < latest else latest
        _exec(
            db,
            "INSERT INTO student_lb_payout_cursor (period_kind, next_period_key) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (kind, initial),
        )
        row = _fetchone(
            db,
            "SELECT next_period_key FROM student_lb_payout_cursor WHERE period_kind = %s",
            (kind,),
        )
    current = str((row or {}).get("next_period_key") or latest)
    periods: list[str] = []
    while current <= latest and len(periods) < max(1, int(limit)):
        periods.append(current)
        current = _next_period_key(kind, current)
    return periods


def _advance_cursor(kind: str, period_key: str, db=None) -> None:
    from machreach_core.db import _exec

    next_key = _next_period_key(kind, period_key)
    if db is not None:
        _exec(
            db,
            "UPDATE student_lb_payout_cursor SET next_period_key = %s "
            "WHERE period_kind = %s AND next_period_key = %s",
            (next_key, kind, period_key),
        )
        return
    with get_db() as owned_db:
        _advance_cursor(kind, period_key, owned_db)


def _claim_run(kind: str, period_key: str) -> str | None:
    """Durably claim a period. Failed and stale claims are safely recoverable."""
    from machreach_core.db import _exec, _fetchone

    stale_before = _utcnow() - timedelta(minutes=15)
    token = uuid4().hex
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        cur = _exec(
            db,
            "INSERT INTO student_lb_payout_run "
            "(period_kind, period_key, status, claim_token, claimed_at, "
            "completed_at, attempts, last_error) "
            "VALUES (%s, %s, 'processing', %s, %s, NULL, 1, '') "
            "ON CONFLICT DO NOTHING",
            (kind, period_key, token, _utcnow()),
        )
        if cur.rowcount == 1:
            return token
        row = _fetchone(
            db,
            "SELECT id, status, claimed_at, attempts FROM student_lb_payout_run "
            "WHERE period_kind = %s AND period_key = %s",
            (kind, period_key),
        )
        if not row or row.get("status") == "completed":
            return None
        claimed_at = row.get("claimed_at")
        if isinstance(claimed_at, str):
            try:
                claimed_at = datetime.fromisoformat(claimed_at)
            except ValueError:
                claimed_at = None
        reclaimable = row.get("status") == "failed" or not claimed_at or claimed_at < stale_before
        if not reclaimable:
            return None
        cur = _exec(
            db,
            "UPDATE student_lb_payout_run "
            "SET status = 'processing', claim_token = %s, claimed_at = %s, completed_at = NULL, "
            "attempts = attempts + 1, last_error = '' "
            "WHERE id = %s AND status = %s AND attempts = %s",
            (token, _utcnow(), row["id"], row["status"], row["attempts"]),
        )
        return token if cur.rowcount == 1 else None


def _fail_run(kind: str, period_key: str, claim_token: str, error: Exception) -> None:
    from machreach_core.db import _exec
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_lb_payout_run SET status = 'failed', last_error = %s "
            "WHERE period_kind = %s AND period_key = %s "
            "AND status = 'processing' AND claim_token = %s",
            (str(error)[:1000], kind, period_key, claim_token),
        )


# ── Top-N query (per scope bucket) ──────────────────────────────────────

def _top5_per_bucket(
    scope: str, period_kind: str, period_key: str, db=None
) -> list[dict]:
    """Top 5 per scope bucket within the closed period. Returns rows of
    {client_id, name, scope_value, rank, xp}. For scope='global' there's
    one bucket; for the others, a bucket per country/univ/major."""
    start_iso, end_iso = _xp_period_window(period_kind, period_key)
    from machreach_core.db import _fetchall
    from student.academic import _leaderboard_visible_clause
    visible_clause = _leaderboard_visible_clause("c")

    def fetch_rows(query: str, params: tuple) -> list[dict]:
        if db is not None:
            return _fetchall(db, query, params)
        with get_db() as owned_db:
            return _fetchall(owned_db, query, params)

    if scope == "global":
        bucket_col = "NULL"
        partition_by = ""
        group_extra = ""
    elif scope == "country":
        bucket_col = "c.country_iso"
        partition_by = "PARTITION BY bucket"
        group_extra = ", c.country_iso"
    elif scope == "university":
        bucket_col = "CAST(c.university_id AS TEXT)"
        partition_by = "PARTITION BY bucket"
        group_extra = ", c.university_id"
    elif scope == "major":
        bucket_col = "CAST(c.major_id AS TEXT)"
        partition_by = "PARTITION BY bucket"
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
            f"  AND x.occurred_at_utc >= %s AND x.occurred_at_utc < %s "
            f"WHERE c.account_type = 'student' AND COALESCE(c.retired,0) = 0 "
            f"{visible_clause}"
            f"{bucket_filter}"
            f"GROUP BY c.id, c.name "
            f"HAVING COALESCE(SUM(x.xp),0) > 0 "
            f"ORDER BY total_xp DESC, c.id ASC LIMIT 5"
        )
        params: tuple = (start_iso, end_iso)
        rows = fetch_rows(q, params)
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
        f"  AND x.occurred_at_utc >= %s AND x.occurred_at_utc < %s "
        f"WHERE c.account_type = 'student' AND COALESCE(c.retired,0) = 0 "
        f"{visible_clause}"
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
    rows = fetch_rows(q, (start_iso, end_iso))
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

def _award_winners(period_kind: str, period_key: str, claim_token: str) -> dict:
    """Award a complete period atomically while holding the claimed run lock."""
    from . import db as sdb
    from machreach_core.db import _exec, _fetchall, _fetchone

    summary: dict[str, list[dict]] = {"global": [], "country": [],
                                       "university": [], "major": []}
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        suffix = " FOR UPDATE" if _USE_PG else ""
        run = _fetchone(
            db,
            "SELECT status, claim_token FROM student_lb_payout_run "
            "WHERE period_kind = %s AND period_key = %s" + suffix,
            (period_kind, period_key),
        )
        if (
            not run
            or run.get("status") != "processing"
            or run.get("claim_token") != claim_token
        ):
            raise RuntimeError("payout claim ownership was lost before awarding")
        _exec(
            db,
            "UPDATE student_lb_payout_run SET claimed_at = %s "
            "WHERE period_kind = %s AND period_key = %s AND claim_token = %s",
            (_utcnow(), period_kind, period_key, claim_token),
        )
        for scope in ("global", "country", "university", "major"):
            winners = _top5_per_bucket(scope, period_kind, period_key, db)
            for w in winners:
                coins = _coins_for(period_kind, scope, w["rank"])
                if coins <= 0:
                    continue
                scope_value = (
                    str(w["scope_value"]) if w["scope_value"] is not None else None
                )
                inserted = _exec(
                    db,
                    "INSERT INTO student_lb_prize "
                    "(client_id, period_kind, period_key, scope, scope_value, "
                    "rank, coins, xp_in_period, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (w["client_id"], period_kind, period_key, scope,
                     scope_value,
                     w["rank"], coins, w["xp"], _utcnow()),
                )
                if inserted.rowcount == 1:
                    sdb._ensure_wallet(db, int(w["client_id"]))
                    # Credited directly rather than through add_coins, so the
                    # ledger value always equals what lands in the wallet.
                    sdb._credit_wallet_with_debt(db, int(w["client_id"]), coins)
        stored = _fetchall(
            db,
            "SELECT p.client_id, c.name, p.scope_value, p.scope, p.rank, "
            "p.xp_in_period AS xp, p.coins FROM student_lb_prize p "
            "JOIN clients c ON c.id = p.client_id "
            "WHERE p.period_kind = %s AND p.period_key = %s "
            "ORDER BY p.scope, p.scope_value, p.rank",
            (period_kind, period_key),
        )
        for winner in stored:
            summary[winner["scope"]].append(dict(winner))
    return summary


def _notification_payloads(
    period_kind: str, period_key: str, summary: dict
) -> list[tuple[str, int | None, str, str, str]]:
    """Build deterministic outbox rows."""
    from machreach_core.config import LEADERBOARD_WINNERS_RECIPIENT
    from machreach_core.db import _fetchone

    label = "weekly" if period_kind == "week" else "monthly"
    pretty = {"global": "Global", "country": "Country",
              "university": "University", "major": "Major"}
    per_client: dict[int, list[dict]] = {}
    for scope, winners in summary.items():
        for winner in winners:
            per_client.setdefault(int(winner["client_id"]), []).append(
                {**winner, "scope": scope}
            )

    payloads: list[tuple[str, int | None, str, str, str]] = []
    for client_id, wins in per_client.items():
        with get_db() as db:
            client = _fetchone(
                db, "SELECT email, name FROM clients WHERE id = %s", (client_id,)
            )
        if not client or not client.get("email"):
            continue
        total = sum(int(win["coins"]) for win in wins)
        lines = [
            f"Hi {client.get('name') or 'there'},", "",
            f"Great news — you placed on the {label} MachReach leaderboards ({period_key}):",
            "",
        ]
        for win in sorted(wins, key=lambda item: (-item["coins"], item["scope"])):
            bucket = f" [{win['scope_value']}]" if win.get("scope_value") else ""
            lines.append(
                f"  • {pretty[win['scope']]}{bucket} — rank #{win['rank']} "
                f"→ +{win['coins']} coins"
            )
        lines += ["", f"Total reward: {total} coins. It is already in your wallet.",
                  "", "— MachReach"]
        payloads.append((
            f"lb:{period_kind}:{period_key}:winner:{client_id}",
            client_id,
            str(client["email"]),
            f"You won {total} coins on the {label} leaderboard!",
            "\n".join(lines),
        ))

    if LEADERBOARD_WINNERS_RECIPIENT:
        lines = [f"{label.title()} leaderboard payouts complete — {period_key}", ""]
        for scope in ("global", "country", "university", "major"):
            for winner in summary.get(scope) or []:
                lines.append(
                    f"{scope.upper()} #{winner['rank']} {winner['name']} "
                    f"(id={winner['client_id']}) — {winner['xp']} XP → {winner['coins']} coins"
                )
        payloads.append((
            f"lb:{period_kind}:{period_key}:admin",
            None,
            LEADERBOARD_WINNERS_RECIPIENT,
            f"[MachReach] {label.title()} leaderboard payouts — {period_key}",
            "\n".join(lines),
        ))
    return payloads


def _complete_run(
    period_kind: str, period_key: str, claim_token: str, summary: dict
) -> bool:
    """Commit the completion marker and deduplicated email intents together."""
    from machreach_core.db import _exec

    payloads = _notification_payloads(period_kind, period_key, summary)
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        for event_key, client_id, recipient, subject, body in payloads:
            _exec(
                db,
                "INSERT INTO student_lb_email_outbox "
                "(event_key, period_kind, period_key, client_id, recipient, subject, body) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (event_key, period_kind, period_key, client_id, recipient, subject, body),
            )
        completed = _exec(
            db,
            "UPDATE student_lb_payout_run "
            "SET status = 'completed', completed_at = %s, last_error = '' "
            "WHERE period_kind = %s AND period_key = %s "
            "AND status = 'processing' AND claim_token = %s",
            (_utcnow(), period_kind, period_key, claim_token),
        )
        if completed.rowcount != 1:
            raise RuntimeError("payout claim ownership was lost before completion")
        _advance_cursor(period_kind, period_key, db)
    return True


# ── Public entrypoint ───────────────────────────────────────────────────

def run_due_payouts(as_of: date | None = None, max_periods: int = 8) -> dict:
    """Catch up the latest closed weekly and monthly periods.

    The worker may call this on any day. Database claims make concurrent
    workers and retries safe.
    """
    out: dict[str, dict[str, Any] | None] = {"week": None, "month": None}
    today = as_of or zoned_date("America/Santiago")
    for kind in ("week", "month"):
        latest = _latest_closed_period_key(kind, today)
        for key in _periods_to_process(kind, latest, max_periods):
            claim_token = _claim_run(kind, key)
            if not claim_token:
                from machreach_core.db import _fetchval
                with get_db() as db:
                    status = _fetchval(
                        db,
                        "SELECT status FROM student_lb_payout_run "
                        "WHERE period_kind = %s AND period_key = %s",
                        (kind, key),
                    )
                if status == "completed":
                    _advance_cursor(kind, key)
                    continue
                break
            try:
                log.info("Running %s leaderboard payout for %s", kind, key)
                summary = _award_winners(kind, key, claim_token)
                _complete_run(kind, key, claim_token, summary)
                out[kind] = {
                    "period_key": key,
                    "winners": sum(len(v) for v in summary.values()),
                }
            except Exception as e:
                _fail_run(kind, key, claim_token, e)
                log.exception("Leaderboard payout (%s) failed: %s", kind, e)
                break
    return out


def run_payouts_if_due() -> dict:
    """Backward-compatible worker entrypoint."""
    return run_due_payouts()


def _claim_outbox_message() -> dict | None:
    from machreach_core.db import _exec, _fetchone

    stale_before = _utcnow() - timedelta(minutes=15)
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        suffix = " FOR UPDATE SKIP LOCKED" if _USE_PG else ""
        row = _fetchone(
            db,
            "SELECT * FROM student_lb_email_outbox "
            "WHERE (status = 'pending' AND "
            "      (next_attempt_at IS NULL OR next_attempt_at <= %s)) "
            "OR (status = 'sending' AND claimed_at < %s) "
            f"ORDER BY id LIMIT 1{suffix}",
            (_utcnow(), stale_before),
        )
        if not row:
            return None
        _exec(
            db,
            "UPDATE student_lb_email_outbox "
            "SET status = 'sending', claimed_at = %s, attempts = attempts + 1 "
            "WHERE id = %s",
            (_utcnow(), row["id"]),
        )
        return row


def dispatch_payout_notifications(limit: int = 25) -> dict[str, int]:
    """Deliver due payout mail.

    Outbox creation is exactly-once. SMTP delivery is intentionally at-least-once:
    a process crash after SMTP accepts a message but before ``sent`` is committed
    can resend it because the current provider has no idempotency-key API.
    """
    from app import _send_system_email
    from machreach_core.db import _exec

    sent = failed = 0
    for _ in range(max(0, int(limit))):
        row = _claim_outbox_message()
        if not row:
            break
        try:
            ok = bool(_send_system_email(
                row["recipient"],
                row["subject"],
                row["body"],
                idempotency_key=row["event_key"],
            ))
            if not ok:
                raise RuntimeError("email sender returned false")
            with get_db() as db:
                _exec(
                    db,
                    "UPDATE student_lb_email_outbox "
                    "SET status = 'sent', sent_at = %s, last_error = '' WHERE id = %s",
                    (_utcnow(), row["id"]),
                )
            sent += 1
        except Exception as exc:
            attempts = int(row.get("attempts") or 0) + 1
            dead = attempts >= 5
            retry_at = _utcnow() + timedelta(minutes=min(2 ** attempts, 60))
            with get_db() as db:
                _exec(
                    db,
                    "UPDATE student_lb_email_outbox "
                    "SET status = %s, next_attempt_at = %s, last_error = %s "
                    "WHERE id = %s",
                    ("dead" if dead else "pending", retry_at,
                     str(exc)[:1000], row["id"]),
                )
            failed += 1
            log.warning("Leaderboard payout email %s failed: %s", row["id"], exc)
    return {"sent": sent, "failed": failed}


def cleanup_payout_outbox() -> None:
    """Apply the PII retention policy for delivered/dead payout mail."""
    from machreach_core.db import _exec

    sent_before = _utcnow() - timedelta(days=30)
    dead_before = _utcnow() - timedelta(days=90)
    with get_db() as db:
        _exec(
            db,
            "DELETE FROM student_lb_email_outbox "
            "WHERE (status = 'sent' AND sent_at < %s) "
            "OR (status = 'dead' AND created_at < %s)",
            (sent_before, dead_before),
        )


# ── Pop-up support: pending winnings for a single user ──────────────────





# ── Pop-up support: every-user "here's how you placed" results ──────────

def _user_rank_in_scope(client_id: int, scope: str, period_kind: str,
                        period_key: str) -> dict | None:
    """Compute (rank, total_in_bucket, xp) for one user in one scope.
    Returns None if the user is not eligible for this scope (e.g. no
    country set) or had 0 XP in the period."""
    start_iso, end_iso = _xp_period_window(period_kind, period_key)
    from machreach_core.db import _fetchone, _fetchall
    from student.academic import _leaderboard_visible_clause
    visible_clause = _leaderboard_visible_clause("c")

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
        f"    AND x.occurred_at_utc >= %s AND x.occurred_at_utc < %s "
        f"  WHERE c.account_type = 'student' AND COALESCE(c.retired, 0) = 0 "
        f"  {visible_clause} "
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


def _account_created_on(client_id: int) -> date | None:
    """The day this account was opened, as a plain date."""
    from machreach_core.db import _fetchval

    with get_db() as db:
        raw = _fetchval(db, "SELECT created_at FROM clients WHERE id = %s", (client_id,))
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T", 1)).date()
    except ValueError:
        return None


def get_pending_period_results(client_id: int) -> list[dict]:
    """Return a list of fully-rendered period summaries the user has not
    yet acknowledged. Each entry contains ranks across all scopes plus
    any prize amounts they won in that period.

    Periods that closed before the account existed, and periods the user
    finished with no XP at all, are skipped: a brand-new student was told
    "Sin clasificación" for every board of every week since launch. They are
    marked as seen on the way past so the work is done once.
    """
    from machreach_core.db import _fetchall

    # Grab all completed payout runs the user hasn't seen.
    with get_db() as db:
        runs = _fetchall(
            db,
            "SELECT period_kind, period_key, completed_at FROM student_lb_payout_run "
            "WHERE status = 'completed' AND NOT EXISTS ("
            "  SELECT 1 FROM student_lb_period_seen s "
            "  WHERE s.client_id = %s AND s.period_kind = student_lb_payout_run.period_kind "
            "    AND s.period_key = student_lb_payout_run.period_key"
            ") ORDER BY completed_at ASC",
            (client_id,),
        )

    joined_on = _account_created_on(client_id)
    out: list[dict] = []
    for run in runs:
        kind = run.get("period_kind")
        key = run.get("period_key")
        if joined_on is not None:
            try:
                # _period_window speaks this module's "week"/"month" kinds.
                period_end = date.fromisoformat(_period_window(kind, key)[1][:10])
            except ValueError:
                period_end = None
            if period_end is not None and joined_on >= period_end:
                mark_period_seen(client_id, kind, key)
                continue
        # Per-scope rank lookup
        scopes_data: dict[str, dict | None] = {}
        for scope in ("global", "country", "university", "major"):
            scopes_data[scope] = _user_rank_in_scope(client_id, scope, kind, key)
        if not any(scopes_data.values()):
            # No XP anywhere in the period — there is nothing to report.
            mark_period_seen(client_id, kind, key)
            continue
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
    from machreach_core.db import _exec
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_period_seen "
            "(client_id, period_kind, period_key, seen_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (client_id, period_kind, period_key, _utcnow()),
        )
