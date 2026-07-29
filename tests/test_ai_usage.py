import pytest

from machreach_core.db import _fetchone, get_db
from student import ai_usage


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
