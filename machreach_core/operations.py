"""Sanitized operational health checks for monitors and on-call alerts."""
from __future__ import annotations

from datetime import timedelta
import os

from machreach_core.db import (
    _USE_PG,
    _async_job_is_stale,
    _fetchone,
    _fetchval,
    get_db,
)
from student.periods import utc_now


def _count(db, sql: str, params=()) -> int:
    return int(_fetchval(db, sql, params) or 0)


def _signal(count: int) -> dict:
    return {"status": "alert" if count else "ok", "count": int(count)}


def _configured(value: bool) -> dict:
    return {"status": "ok" if value else "not_configured"}


def _dependency_checks() -> dict:
    """Validate critical configuration without returning any secret values."""
    from machreach_core import config

    rate_limit_uri = (os.getenv("RATELIMIT_STORAGE_URI") or "").strip().lower()
    lemon_values = (
        config.LEMON_SQUEEZY_API_KEY,
        config.LEMON_SQUEEZY_STORE_ID,
        config.LEMON_SQUEEZY_WEBHOOK_SECRET,
        config.LS_PRODUCT_STUDENT_PLUS,
        config.LS_VARIANT_STUDENT_PLUS,
    )
    return {
        "openai": _configured(bool(config.OPENAI_API_KEY)),
        "lemon_squeezy": _configured(all(lemon_values)),
        "smtp": _configured(
            bool(config.SMTP_HOST and config.SYSTEM_SMTP_USER and config.SYSTEM_SMTP_PASSWORD)
        ),
        "sentry": _configured(bool(config.SENTRY_DSN)),
        "redis_rate_limit": _configured(
            rate_limit_uri.startswith(("redis://", "rediss://", "valkey://"))
        ),
        "leaderboard_recipient": _configured(bool(config.LEADERBOARD_WINNERS_RECIPIENT)),
    }


def collect_operational_health() -> dict:
    """Return aggregate states only; never expose job, webhook, or user details."""
    with get_db() as db:
        heartbeat = _fetchone(
            db,
            "SELECT updated_at FROM async_jobs "
            "WHERE job_type = %s ORDER BY updated_at DESC LIMIT 1",
            ("worker_heartbeat",),
        )
        worker_stale = not heartbeat or _async_job_is_stale(heartbeat.get("updated_at"), 120)

        stale_boundary = (
            "updated_at < NOW() - INTERVAL '10 minutes'"
            if _USE_PG
            else "updated_at < datetime('now', 'localtime', '-10 minutes')"
        )
        stale_queued = _count(
            db,
            "SELECT COUNT(*) FROM async_jobs WHERE status = %s AND job_type <> %s "
            f"AND {stale_boundary}",
            ("queued", "worker_heartbeat"),
        )
        exhausted = _count(
            db,
            "SELECT COUNT(*) FROM async_jobs "
            "WHERE status = %s AND COALESCE(attempts, 0) >= COALESCE(max_attempts, 3) "
            "AND job_type <> %s",
            ("error", "worker_heartbeat"),
        )
        failed_webhooks = _count(
            db,
            "SELECT COUNT(*) FROM webhook_events WHERE provider = %s AND status = %s",
            ("lemonsqueezy", "failed"),
        )
        invalid_wallets = _count(
            db,
            "SELECT COUNT(*) FROM student_wallet "
            "WHERE coins < 0 OR coin_debt < 0 OR streak_freezes < 0",
        )
        invalid_orders = _count(
            db,
            "SELECT COUNT(*) FROM student_coin_pack_orders o "
            "LEFT JOIN student_wallet w ON w.client_id = o.client_id "
            "WHERE o.coins_credited <= 0 OR o.coins_reversed < 0 "
            "OR o.coins_reversed > o.coins_credited OR w.client_id IS NULL",
        )
        recent = (
            "created_at >= NOW() - INTERVAL '15 minutes'"
            if _USE_PG
            else "created_at >= datetime('now', 'localtime', '-15 minutes')"
        )
        smtp_failures = _count(
            db,
            f"SELECT COUNT(*) FROM operational_events WHERE event_type = %s AND {recent}",
            ("smtp_failure",),
        )
        billing_review = _count(
            db,
            "SELECT COUNT(*) FROM operational_events WHERE event_type IN (%s, %s)",
            ("billing_refund_manual_review", "billing_reconciliation_failure"),
        )
        ai_reservation_boundary = utc_now() - timedelta(minutes=15)
        stale_ai_reservations = _count(
            db,
            "SELECT COUNT(*) FROM student_ai_usage "
            "WHERE status = %s AND reserved_at_utc < %s",
            ("reserved", ai_reservation_boundary),
        )
        withheld_referrals = _count(
            db,
            "SELECT COUNT(*) FROM student_referral_reward_audit "
            "WHERE status = %s",
            ("withheld",),
        )
        if _USE_PG:
            used = _count(db, "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()")
            maximum = int(_fetchval(db, "SELECT current_setting('max_connections')") or 0)
            ratio = round(used / maximum, 3) if maximum else 0
            db_capacity = {
                "status": "alert" if maximum and ratio >= 0.8 else "ok",
                "used": used,
                "maximum": maximum,
                "utilization": ratio,
            }
        else:
            db_capacity = {"status": "not_applicable"}

    checks = {
        "worker_heartbeat": {"status": "alert" if worker_stale else "ok"},
        "queued_jobs": _signal(stale_queued),
        "exhausted_retries": _signal(exhausted),
        "failed_webhooks": _signal(failed_webhooks),
        "coin_ledger": _signal(invalid_wallets + invalid_orders),
        "smtp_failures": _signal(smtp_failures),
        "billing_refund_review": _signal(billing_review),
        "stale_ai_reservations": _signal(stale_ai_reservations),
        "withheld_referral_rewards": _signal(withheld_referrals),
        "database_capacity": db_capacity,
        **_dependency_checks(),
    }
    degraded = any(
        value.get("status") not in {"ok", "not_applicable"}
        for value in checks.values()
    )
    return {
        "status": "degraded" if degraded else "ok",
        "generated_at": utc_now().replace(microsecond=0).isoformat(),
        "checks": checks,
    }
