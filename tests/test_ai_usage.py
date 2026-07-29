import pytest
from concurrent.futures import ThreadPoolExecutor

from machreach_core.db import _exec, _fetchone, get_db
from student import ai_usage, subscription


def test_reservation_is_idempotent_and_blocks_second_free_generation(make_user):
    client_id = make_user("AI User", "ai-reserve@example.test")
    first = ai_usage.reserve(
        client_id,
        request_key="quiz:one",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    duplicate = ai_usage.reserve(
        client_id,
        request_key="quiz:one",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    assert duplicate["id"] == first["id"]
    with pytest.raises(ai_usage.AIQuotaExceeded):
        ai_usage.reserve(
            client_id,
            request_key="quiz:two",
            feature="quiz_worker",
            usage_kind="quiz_generated",
        )


def test_settle_is_atomic_and_idempotent(make_user):
    client_id = make_user("AI Settle", "ai-settle@example.test")
    ai_usage.reserve(
        client_id,
        request_key="flashcards:one",
        feature="flashcard_worker",
        usage_kind="flashcards_generated",
    )
    ai_usage.settle("flashcards:one")
    ai_usage.settle("flashcards:one")
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT status FROM student_ai_usage WHERE request_key=%s",
            ("flashcards:one",),
        )
        usage = _fetchone(
            db,
            "SELECT COUNT(*) AS n FROM student_xp WHERE client_id=%s "
            "AND action=%s AND detail=%s",
            (client_id, "flashcards_generated", "flashcards:one"),
        )
    assert row["status"] == "settled"
    assert usage["n"] == 1


def test_failed_reservation_can_retry_without_consuming_quota(make_user):
    client_id = make_user("AI Retry", "ai-retry@example.test")
    ai_usage.reserve(
        client_id,
        request_key="quiz:retry",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    ai_usage.fail("quiz:retry", "provider timeout")
    retried = ai_usage.reserve(
        client_id,
        request_key="quiz:retry",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    assert retried["status"] == "reserved"


def test_emergency_user_ceiling_fails_closed(make_user, monkeypatch):
    client_id = make_user("AI Ceiling", "ai-ceiling@example.test")
    monkeypatch.setenv("AI_USER_DAILY_MAX", "1")
    ai_usage.reserve(
        client_id,
        request_key="ceiling:first",
        feature="course_brain",
        usage_kind="ai_tool_generated",
    )
    with pytest.raises(ai_usage.AIQuotaExceeded):
        ai_usage.reserve(
            client_id,
            request_key="ceiling:second",
            feature="course_brain",
            usage_kind="ai_tool_generated",
        )


def test_emergency_token_ceiling_fails_before_provider_work(make_user, monkeypatch):
    client_id = make_user("AI Token Ceiling", "ai-token-ceiling@example.test")
    monkeypatch.setenv("AI_USER_DAILY_TOKEN_MAX", "100")

    with pytest.raises(ai_usage.AIQuotaExceeded):
        ai_usage.reserve(
            client_id,
            request_key="tokens:too-large",
            feature="course_brain",
            usage_kind="ai_tool_generated",
            estimated_input_tokens=90,
            estimated_output_tokens=20,
        )


def test_stale_reservation_recovery_releases_capacity(make_user):
    client_id = make_user("AI Recovery", "ai-recovery@example.test")
    ai_usage.reserve(
        client_id,
        request_key="stale:one",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_ai_usage SET reserved_at_utc=%s WHERE request_key=%s",
            ("2000-01-01T00:00:00+00:00", "stale:one"),
        )
    assert ai_usage.reconcile_stale_reservations() == 1
    retried = ai_usage.reserve(
        client_id,
        request_key="stale:one",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )
    assert retried["status"] == "reserved"


def test_concurrent_final_quota_reservation_has_one_winner(make_user):
    client_id = make_user("AI Concurrent", "ai-concurrent@example.test")

    def attempt(index):
        try:
            ai_usage.reserve(
                client_id,
                request_key=f"concurrent:{index}",
                feature="quiz_worker",
                usage_kind="quiz_generated",
            )
            return "reserved"
        except ai_usage.AIQuotaExceeded:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))

    assert sorted(outcomes) == ["blocked", "reserved"]


def test_plus_tools_share_the_provider_billing_window(make_user):
    client_id = make_user("AI Plus", "ai-plus@example.test")
    subscription.set_subscription_state(
        client_id,
        tier="plus",
        status="active",
        ls_sub_id="ai-plus-subscription",
        renews_at="2099-08-29T12:00:00Z",
        provider_event_at="2026-07-29T12:00:00Z",
    )

    ai_usage.reserve(
        client_id,
        request_key="plus:brain",
        feature="course_brain",
        usage_kind="ai_tool_generated",
        estimated_input_tokens=100,
        estimated_output_tokens=50,
    )
    ai_usage.settle("plus:brain")
    second = ai_usage.reserve(
        client_id,
        request_key="plus:quiz",
        feature="quiz_worker",
        usage_kind="quiz_generated",
    )

    assert second["tier"] == "plus"
    assert subscription.generation_usage(client_id)["used"] == 1
