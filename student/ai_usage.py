"""Durable, idempotent AI quota reservations.

Model work must reserve capacity before contacting a provider.  Reservations
serialize on the client row in PostgreSQL (and BEGIN IMMEDIATE in SQLite), so
concurrent workers cannot both consume the final available generation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os

from machreach_core.db import _exec, _fetchone, _fetchval, get_db
from student.periods import day_window_utc, month_window_utc


class AIQuotaExceeded(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_schema(db) -> None:
    from student.db import _USE_PG

    timestamp = "TIMESTAMPTZ" if _USE_PG else "TEXT"
    _exec(
        db,
        f"""CREATE TABLE IF NOT EXISTS student_ai_usage (
            id {"BIGSERIAL" if _USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"},
            request_key TEXT NOT NULL UNIQUE,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            feature TEXT NOT NULL,
            usage_kind TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
            estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_output_tokens INTEGER NOT NULL DEFAULT 0,
            tier TEXT NOT NULL,
            status TEXT NOT NULL,
            period_start_utc {timestamp} NOT NULL,
            period_end_utc {timestamp} NOT NULL,
            reserved_at_utc {timestamp} NOT NULL,
            settled_at_utc {timestamp},
            failed_at_utc {timestamp},
            error TEXT NOT NULL DEFAULT ''
            ,result_json TEXT NOT NULL DEFAULT '{{}}'
        )""",
    )
    _exec(
        db,
        "CREATE INDEX IF NOT EXISTS idx_student_ai_usage_quota "
        "ON student_ai_usage(client_id, period_start_utc, period_end_utc, status)",
    )


def init_schema() -> None:
    with get_db() as db:
        _ensure_schema(db)


def reserve(
    client_id: int,
    *,
    request_key: str,
    feature: str,
    usage_kind: str,
    model: str = "gpt-4o-mini",
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
) -> dict:
    """Reserve one generation, returning the durable reservation.

    Reusing a request key is idempotent. Failed keys may be reserved again for
    a bounded queue retry; settled keys remain settled and must not rerun.
    """
    from student import subscription
    from student.db import _USE_PG

    request_key = str(request_key).strip()
    if not request_key:
        raise ValueError("request_key is required")
    now = _now()
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        _ensure_schema(db)
        _fetchone(
            db,
            "SELECT id FROM clients WHERE id = %s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (client_id,),
        )
        existing = _fetchone(
            db, "SELECT * FROM student_ai_usage WHERE request_key = %s", (request_key,)
        )
        if existing and existing.get("status") != "failed":
            return dict(existing)

        utc_day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        user_daily_max = max(1, int(os.getenv("AI_USER_DAILY_MAX", "120")))
        global_daily_max = max(1, int(os.getenv("AI_GLOBAL_DAILY_MAX", "5000")))
        user_token_max = max(
            1, int(os.getenv("AI_USER_DAILY_TOKEN_MAX", "1000000"))
        )
        global_token_max = max(
            1, int(os.getenv("AI_GLOBAL_DAILY_TOKEN_MAX", "25000000"))
        )
        user_today = int(
            _fetchval(
                db,
                "SELECT COUNT(*) FROM student_ai_usage WHERE client_id=%s "
                "AND reserved_at_utc >= %s AND status IN ('reserved','settled')",
                (client_id, utc_day_start.isoformat()),
            )
            or 0
        )
        global_today = int(
            _fetchval(
                db,
                "SELECT COUNT(*) FROM student_ai_usage WHERE reserved_at_utc >= %s "
                "AND status IN ('reserved','settled')",
                (utc_day_start.isoformat(),),
            )
            or 0
        )
        user_tokens = int(
            _fetchval(
                db,
                "SELECT COALESCE(SUM(estimated_input_tokens + "
                "estimated_output_tokens),0) FROM student_ai_usage "
                "WHERE client_id=%s AND reserved_at_utc >= %s "
                "AND status IN ('reserved','settled')",
                (client_id, utc_day_start.isoformat()),
            )
            or 0
        )
        global_tokens = int(
            _fetchval(
                db,
                "SELECT COALESCE(SUM(estimated_input_tokens + "
                "estimated_output_tokens),0) FROM student_ai_usage "
                "WHERE reserved_at_utc >= %s AND status IN ('reserved','settled')",
                (utc_day_start.isoformat(),),
            )
            or 0
        )
        estimated_total = max(0, int(estimated_input_tokens)) + max(
            0, int(estimated_output_tokens)
        )
        if (
            user_today >= user_daily_max
            or global_today >= global_daily_max
            or user_tokens + estimated_total > user_token_max
            or global_tokens + estimated_total > global_token_max
        ):
            raise AIQuotaExceeded("AI generation temporarily unavailable")

        tier = subscription._effective_tier(
            {
                "subscription": subscription._load_subscription_row(db, client_id),
                "plus_until": subscription._load_promotional_entitlement(db, client_id),
            }
        )
        if tier == "plus":
            state = subscription._load_subscription_row(db, client_id)
            start = subscription._parse_iso(state.get("quota_period_start"))
            end = subscription._parse_iso(
                state.get("quota_reset_at") or state.get("renews_at")
            )
            if not start or not end:
                start, end = month_window_utc(client_id, at=now, db=db)
            if end <= now:
                raise AIQuotaExceeded("AI generation limit reached")
            limit = int(os.getenv("AI_PLUS_MONTHLY_LIMIT", subscription.PLUS_MONTHLY_GENERATIONS))
            legacy_used = int(
                _fetchval(
                    db,
                    "SELECT COUNT(*) FROM student_xp WHERE client_id=%s "
                    "AND action IN (%s,%s,%s) AND occurred_at_utc >= %s "
                    "AND occurred_at_utc < %s",
                    (
                        client_id,
                        "quiz_generated",
                        "flashcards_generated",
                        "ai_tool_generated",
                        start.isoformat(),
                        end.isoformat(),
                    ),
                )
                or 0
            )
            kind_filter = ""
            params: tuple = (client_id, start.isoformat(), end.isoformat())
        else:
            start, end = day_window_utc(client_id, at=now, db=db)
            limit = (
                subscription.FREE_DAILY_QUIZZES
                if usage_kind == "quiz_generated"
                else subscription.FREE_DAILY_FLASHCARD_SETS
            )
            legacy_used = int(
                _fetchval(
                    db,
                    "SELECT COUNT(*) FROM student_xp WHERE client_id=%s "
                    "AND action=%s AND occurred_at_utc >= %s AND occurred_at_utc < %s",
                    (client_id, usage_kind, start.isoformat(), end.isoformat()),
                )
                or 0
            )
            kind_filter = " AND usage_kind = %s"
            params = (client_id, start.isoformat(), end.isoformat(), usage_kind)

        active = int(
            _fetchval(
                db,
                "SELECT COUNT(*) FROM student_ai_usage WHERE client_id = %s "
                "AND period_start_utc = %s AND period_end_utc = %s "
                "AND status IN ('reserved','settled')" + kind_filter,
                params,
            )
            or 0
        )
        # Settled ledger rows also have a legacy XP row, so subtract them once.
        settled = int(
            _fetchval(
                db,
                "SELECT COUNT(*) FROM student_ai_usage WHERE client_id = %s "
                "AND period_start_utc = %s AND period_end_utc = %s "
                "AND status = 'settled'" + kind_filter,
                params,
            )
            or 0
        )
        consumed = legacy_used + active - settled
        if consumed >= limit:
            raise AIQuotaExceeded("AI generation limit reached")

        values = (
            request_key,
            client_id,
            feature,
            usage_kind,
            str(model)[:80],
            max(0, int(estimated_input_tokens)),
            max(0, int(estimated_output_tokens)),
            tier,
            start.isoformat(),
            end.isoformat(),
            now.isoformat(),
        )
        if existing:
            _exec(
                db,
                "UPDATE student_ai_usage SET status='reserved', error='', "
                "reserved_at_utc=%s, failed_at_utc=NULL WHERE request_key=%s",
                (now.isoformat(), request_key),
            )
        else:
            _exec(
                db,
                "INSERT INTO student_ai_usage "
                "(request_key,client_id,feature,usage_kind,model,"
                "estimated_input_tokens,estimated_output_tokens,tier,status,"
                "period_start_utc,period_end_utc,reserved_at_utc) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s,%s,%s)",
                values,
            )
        return dict(
            _fetchone(
                db, "SELECT * FROM student_ai_usage WHERE request_key=%s", (request_key,)
            )
        )


def settle(request_key: str, result: dict | None = None) -> None:
    """Atomically settle a reservation and write its compatibility usage row."""
    with get_db() as db:
        _ensure_schema(db)
        row = _fetchone(
            db, "SELECT * FROM student_ai_usage WHERE request_key=%s", (request_key,)
        )
        if not row or row.get("status") == "settled":
            return
        if row.get("status") != "reserved":
            raise RuntimeError("AI reservation is not active")
        now = _now().isoformat()
        _exec(
            db,
            "INSERT INTO student_xp "
            "(client_id, action, xp, detail, occurred_at_utc) VALUES (%s,%s,0,%s,%s)",
            (row["client_id"], row["usage_kind"], request_key, now),
        )
        _exec(
            db,
            "UPDATE student_ai_usage SET status='settled', settled_at_utc=%s, "
            "result_json=%s "
            "WHERE request_key=%s AND status='reserved'",
            (now, json.dumps(result or {}, separators=(",", ":")), request_key),
        )


def fail(request_key: str, error: str = "") -> None:
    with get_db() as db:
        _ensure_schema(db)
        _exec(
            db,
            "UPDATE student_ai_usage SET status='failed', failed_at_utc=%s, error=%s "
            "WHERE request_key=%s AND status='reserved'",
            (_now().isoformat(), str(error)[:500], request_key),
        )


def reconcile_stale_reservations(max_age_minutes: int = 30) -> int:
    """Release reservations left behind by crashed request/worker processes."""
    cutoff = (_now() - timedelta(minutes=max(1, max_age_minutes))).isoformat()
    with get_db() as db:
        _ensure_schema(db)
        result = _exec(
            db,
            "UPDATE student_ai_usage SET status='failed', failed_at_utc=%s, "
            "error='stale reservation recovered' WHERE status='reserved' "
            "AND reserved_at_utc < %s",
            (_now().isoformat(), cutoff),
        )
        return int(result.rowcount or 0)
