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
        _exec(db, "DELETE FROM student_referral_reward_audit")
        _exec(db, "DELETE FROM student_ai_usage")
        _exec(db, "DELETE FROM operational_events")
        _exec(db, "DELETE FROM webhook_events")
        _exec(db, "DELETE FROM async_jobs")


def _configure_dependencies(monkeypatch):
    from machreach_core import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_API_KEY", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_STORE_ID", "configured")
    monkeypatch.setattr(config, "LEMON_SQUEEZY_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(config, "LS_PRODUCT_STUDENT_PLUS", "configured")
    monkeypatch.setattr(config, "LS_VARIANT_STUDENT_PLUS", "configured")
    monkeypatch.setattr(config, "SYSTEM_SMTP_USER", "configured")
    monkeypatch.setattr(config, "SYSTEM_SMTP_PASSWORD", "configured")
    monkeypatch.setattr(config, "SENTRY_DSN", "configured")
    monkeypatch.setattr(config, "LEADERBOARD_WINNERS_RECIPIENT", "configured")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://configured")


def test_operational_health_is_sanitized_and_healthy_with_fresh_worker(monkeypatch):
    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    snapshot = collect_operational_health()

    assert snapshot["status"] == "ok"
    assert snapshot["checks"]["worker_heartbeat"]["status"] == "ok"
    assert snapshot["checks"]["queued_jobs"]["count"] == 0
    assert snapshot["checks"]["failed_webhooks"]["count"] == 0
    assert "error" not in str(snapshot).lower()
    assert snapshot["checks"]["sentry"]["status"] == "ok"


def test_operational_health_reports_missing_dependencies(monkeypatch):
    _clear_operational_state()
    record_worker_heartbeat()
    from machreach_core import config

    for name in (
        "OPENAI_API_KEY",
        "LEMON_SQUEEZY_API_KEY",
        "LEMON_SQUEEZY_STORE_ID",
        "LEMON_SQUEEZY_WEBHOOK_SECRET",
        "LS_PRODUCT_STUDENT_PLUS",
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


def test_exhausted_verification_job_stops_alerting_once_nothing_is_left_to_do(
    monkeypatch, make_user
):
    """Mirror of the 2026-08-21 uptime incident: a verification email burned
    its retries during a mail outage, the person got in some other way, and the
    dead job held /health/operations degraded on every probe after that."""
    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    from machreach_core.db import (
        claim_async_jobs,
        mark_email_verified,
        reconcile_orphaned_async_jobs,
    )

    provisioned_id = make_user("Provisioned By Operator")
    deleted_id = 99887763  # account removed after the email never arrived
    for key in (provisioned_id, deleted_id):
        enqueue_async_job("verification_email", str(key), max_attempts=1)
    claim_async_jobs("verification_email", limit=10)
    for key in (provisioned_id, deleted_id):
        fail_async_job("verification_email", str(key), "smtp down")

    degraded = collect_operational_health()
    assert degraded["status"] == "degraded"
    assert degraded["checks"]["exhausted_retries"]["count"] == 2

    mark_email_verified(provisioned_id)  # what scripts/provision_account.py does
    assert reconcile_orphaned_async_jobs() == 1  # the deleted account's row

    recovered = collect_operational_health()
    assert recovered["checks"]["exhausted_retries"]["count"] == 0
    assert recovered["status"] == "ok"


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


def test_abandoning_a_terminal_webhook_clears_the_alert(monkeypatch):
    """Events that failed before the deleted-account fix cannot be retried by
    the provider any more, so the operator script is the only way out."""
    from machreach_core.db import _exec, _fetchone, get_db
    from scripts.resolve_failed_webhooks import main as resolve

    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO webhook_events (provider, event_key, event_name, status, last_error) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("lemonsqueezy", "id:dead-1", "subscription_expired", "failed",
             "ForeignKeyViolation"),
        )
    assert collect_operational_health()["checks"]["failed_webhooks"]["status"] == "alert"

    assert resolve(["--error", "ForeignKeyViolation", "--apply"]) == 0

    assert collect_operational_health()["checks"]["failed_webhooks"]["count"] == 0
    with get_db() as db:
        row = _fetchone(
            db, "SELECT status FROM webhook_events WHERE event_key = %s", ("id:dead-1",)
        )
    assert row["status"] == "abandoned"


def test_deploy_settles_order_events_the_handler_could_never_accept(monkeypatch):
    """The predeploy migration clears this class on its own: before the handler
    read first_order_item, no Plus `order_created` could pass the identity
    check, so every such row is the bug rather than a real rejection — and the
    operator has no shell handy when the monitor starts paging."""
    from machreach_core.db import _exec, _fetchone, get_db, settle_unmatchable_order_events

    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO webhook_events (provider, event_key, event_name, status, last_error) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("lemonsqueezy", "id:order-1", "order_created", "failed",
             "unapproved-subscription-variant"),
        )
    assert collect_operational_health()["checks"]["failed_webhooks"]["status"] == "alert"

    assert settle_unmatchable_order_events() == 1

    assert collect_operational_health()["checks"]["failed_webhooks"]["count"] == 0
    with get_db() as db:
        row = _fetchone(
            db, "SELECT status FROM webhook_events WHERE event_key = %s", ("id:order-1",)
        )
    assert row["status"] == "abandoned"
    # Idempotent: a second deploy finds nothing left to settle.
    assert settle_unmatchable_order_events() == 0


def test_deploy_leaves_a_genuinely_rejected_subscription_variant_alone(monkeypatch):
    """Same error string, different event: a subscription rejected for a variant
    nobody approved means someone may have paid for something the product does
    not know about. That one keeps paging."""
    from machreach_core.db import _exec, _fetchone, get_db, settle_unmatchable_order_events

    _clear_operational_state()
    _configure_dependencies(monkeypatch)
    record_worker_heartbeat()
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO webhook_events (provider, event_key, event_name, status, last_error) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("lemonsqueezy", "id:sub-rogue", "subscription_created", "failed",
             "unapproved-subscription-variant"),
        )

    assert settle_unmatchable_order_events() == 0

    assert collect_operational_health()["checks"]["failed_webhooks"]["status"] == "alert"
    with get_db() as db:
        row = _fetchone(
            db, "SELECT status FROM webhook_events WHERE event_key = %s", ("id:sub-rogue",)
        )
    assert row["status"] == "failed"


def test_resolve_script_refuses_to_close_every_failed_event(monkeypatch):
    from machreach_core.db import _exec, get_db
    from scripts.resolve_failed_webhooks import main as resolve

    _clear_operational_state()
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO webhook_events (provider, event_key, event_name, status, last_error) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("lemonsqueezy", "id:dead-2", "subscription_payment_success", "failed", "Timeout"),
        )

    assert resolve(["--apply"]) == 1
    assert collect_operational_health()["checks"]["failed_webhooks"]["status"] == "alert"


def test_liveness_probe_makes_one_database_round_trip(client, monkeypatch):
    """Render kills whatever cannot answer this in five seconds.

    The probe used to verify two schema versions and inspect five tables, so a
    slow moment on the database read as a dead instance. Anything added back to
    this path is paid on every probe, of every instance, forever.
    """
    import machreach_core.db as core_db

    queries = []
    real_fetchval = core_db._fetchval

    def counting_fetchval(db, sql, *args, **kwargs):
        queries.append(sql)
        return real_fetchval(db, sql, *args, **kwargs)

    monkeypatch.setattr(core_db, "_fetchval", counting_fetchval)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "healthy"}
    assert queries == ["SELECT 1"]


def test_operational_probe_reports_resident_memory(monkeypatch):
    """The service is restarted for exceeding its memory limit.

    Nothing inside the app could see it: the first news was an email from
    Render, after the restart. This is where the number lives now.
    """
    import builtins

    from machreach_core import operations

    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/status":
            import io as _io

            return _io.StringIO("VmHWM:\t  512000 kB\nVmRSS:\t  259584 kB\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    assert operations._process_memory() == {"status": "ok", "rss_mb": 254}

    def no_proc(path, *args, **kwargs):
        if path == "/proc/self/status":
            raise OSError("not linux")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_proc)

    assert operations._process_memory() == {"status": "not_applicable"}
