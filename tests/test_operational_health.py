from machreach_core.db import (
    _exec,
    enqueue_async_job,
    fail_async_job,
    finish_webhook_event,
    get_db,
    record_operational_event,
    record_worker_heartbeat,
)
from machreach_core.operations import collect_operational_health


def _clear_operational_state():
    with get_db() as db:
        _exec(db, "DELETE FROM operational_events")
        _exec(db, "DELETE FROM webhook_events")
        _exec(db, "DELETE FROM async_jobs")


def _configure_dependencies(monkeypatch):
    from machreach_core import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_API_KEY", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_STORE_ID", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(config, "LS_VARIANT_STUDENT_PLUS", "configured")
    monkeypatch.setattr(config, "SYSTEM_SMTP_USER", "configured")
    monkeypatch.setattr(config, "SYSTEM_SMTP_PASSWORD", "configured")
    monkeypatch.setattr(config, "SENTRY_DSN", "configured")
    monkeypatch.setattr(config, "LEADERBOARD_WINNERS_RECIPIENT", "configured")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://configured")


def _record_fresh_backup():
    record_operational_event("backup_success", "test")


def test_operational_health_is_sanitized_and_healthy_with_fresh_worker(monkeypatch):
    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    _record_fresh_backup()

    snapshot = collect_operational_health()

    assert snapshot["status"] == "ok"
    assert snapshot["checks"]["worker_heartbeat"]["status"] == "ok"
    assert snapshot["checks"]["queued_jobs"]["count"] == 0
    assert snapshot["checks"]["failed_webhooks"]["count"] == 0
    assert "error" not in str(snapshot).lower()
    assert snapshot["checks"]["sentry"]["status"] == "ok"
    assert snapshot["checks"]["backup_freshness"]["status"] == "ok"


def test_operational_health_reports_missing_dependencies(monkeypatch):
    _clear_operational_state()
    record_worker_heartbeat()
    from machreach_core import config

    for name in (
        "OPENAI_API_KEY",
        "LEMON_SQUEEZY_API_KEY",
        "LEMON_SQUEEZY_STORE_ID",
        "LEMON_SQUEEZY_WEBHOOK_SECRET",
        "LS_VARIANT_STUDENT_PLUS",
        "SYSTEM_SMTP_USER",
        "SYSTEM_SMTP_PASSWORD",
        "SENTRY_DSN",
        "LEADERBOARD_WINNERS_RECIPIENT",
    ):
        monkeypatch.setattr(config, name, "")
    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)

    snapshot = collect_operational_health()

    assert snapshot["status"] == "degraded"
    assert snapshot["checks"]["openai"]["status"] == "not_configured"
    assert snapshot["checks"]["redis_rate_limit"]["status"] == "not_configured"


def test_operational_health_detects_stale_queue_exhausted_jobs_and_provider_failures():
    _clear_operational_state()
    record_worker_heartbeat()
    enqueue_async_job("student_quiz_generation", "old-queued")
    enqueue_async_job("student_flashcard_generation", "exhausted", max_attempts=1)
    from machreach_core.db import claim_async_jobs
    claim_async_jobs("student_flashcard_generation")
    fail_async_job("student_flashcard_generation", "exhausted", "provider timeout")
    with get_db() as db:
        _exec(
            db,
            "UPDATE async_jobs SET updated_at = %s WHERE job_type = %s AND job_key = %s",
            ("2000-01-01 00:00:00", "student_quiz_generation", "old-queued"),
        )
        _exec(
            db,
            "INSERT INTO webhook_events (provider, event_key, event_name, status, last_error) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("lemonsqueezy", "failed-1", "subscription_payment_failed", "processing", ""),
        )
    finish_webhook_event("lemonsqueezy", "failed-1", error="bad provider response")
    record_operational_event("smtp_failure", "transactional")

    snapshot = collect_operational_health()

    assert snapshot["status"] == "degraded"
    assert snapshot["checks"]["queued_jobs"]["status"] == "alert"
    assert snapshot["checks"]["queued_jobs"]["count"] == 1
    assert snapshot["checks"]["exhausted_retries"]["count"] == 1
    assert snapshot["checks"]["failed_webhooks"]["count"] == 1
    assert snapshot["checks"]["smtp_failures"]["count"] == 1


def test_operational_probe_requires_secret(client, monkeypatch):
    _clear_operational_state()
    import app as appmod
    monkeypatch.setattr(appmod, "OPERATIONS_SECRET", "monitor-secret")

    response = client.get("/health/operations")

    assert response.status_code == 404
    response = client.get(
        "/health/operations",
        headers={"X-Operations-Secret": "monitor-secret"},
    )
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "degraded"
    assert payload["checks"]["worker_heartbeat"]["status"] == "alert"
    assert "last_error" not in response.get_data(as_text=True)


def test_postgres_capacity_alert_uses_connection_utilization(monkeypatch):
    from contextlib import contextmanager
    from machreach_core import operations

    @contextmanager
    def fake_db():
        yield object()

    monkeypatch.setattr(operations, "_USE_PG", True)
    monkeypatch.setattr(operations, "get_db", fake_db)
    monkeypatch.setattr(
        operations,
        "_fetchone",
        lambda *_args, **_kwargs: {"updated_at": "fresh"},
    )
    monkeypatch.setattr(operations, "_async_job_is_stale", lambda *_args: False)
    monkeypatch.setattr(
        operations,
        "_count",
        lambda _db, sql, _params=(): 8 if "pg_stat_activity" in sql else 0,
    )
    monkeypatch.setattr(
        operations,
        "_fetchval",
        lambda _db, sql, _params=(): 10 if "max_connections" in sql else 0,
    )
    _configure_dependencies(monkeypatch)

    snapshot = operations.collect_operational_health()

    assert snapshot["status"] == "degraded"
    assert snapshot["checks"]["database_capacity"] == {
        "status": "alert",
        "used": 8,
        "maximum": 10,
        "utilization": 0.8,
    }


def test_operational_health_alerts_when_backup_is_missing(monkeypatch):
    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()

    snapshot = collect_operational_health()

    assert snapshot["status"] == "degraded"
    assert snapshot["checks"]["backup_freshness"]["status"] == "alert"
