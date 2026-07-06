from outreach.db import _exec, claim_async_jobs, enqueue_async_job, get_async_job_status, get_db, set_async_job_status


def test_async_job_status_defaults_to_idle():
    assert get_async_job_status("test_job", "missing") == {"status": "idle"}


def test_async_job_status_round_trips_and_updates_payload():
    set_async_job_status(
        "test_job",
        "quiz-1",
        "running",
        progress="Working",
        payload={"sent": 1, "total": 3},
    )

    assert get_async_job_status("test_job", "quiz-1") == {
        "status": "running",
        "progress": "Working",
        "sent": 1,
        "total": 3,
    }

    set_async_job_status(
        "test_job",
        "quiz-1",
        "error",
        progress="Failed",
        payload={"sent": 1, "total": 3},
        error="Provider timed out",
    )

    assert get_async_job_status("test_job", "quiz-1") == {
        "status": "error",
        "progress": "Failed",
        "sent": 1,
        "total": 3,
        "error": "Provider timed out",
    }


def test_async_job_status_marks_stale_running_jobs_retryable():
    set_async_job_status("test_job", "stale-quiz", "running", progress="Working")
    with get_db() as db:
        _exec(db, """
            UPDATE async_jobs
            SET updated_at = datetime('now', 'localtime', '-2 hours')
            WHERE job_type = %s AND job_key = %s
        """, ("test_job", "stale-quiz"))

    assert get_async_job_status("test_job", "stale-quiz", stale_after_seconds=60) == {
        "status": "error",
        "progress": "Background job was interrupted. Please try again.",
        "error": "Background job interrupted before it finished.",
    }


def test_enqueue_async_job_hides_input_from_status_and_claims_once():
    queued = enqueue_async_job(
        "test_queue",
        "quiz-input",
        input_payload={"source_text": "private notes", "count": 5},
        progress="Queued",
        visible_payload={"public": True},
    )

    assert queued == {
        "status": "queued",
        "progress": "Queued",
        "public": True,
    }

    assert "source_text" not in get_async_job_status("test_queue", "quiz-input")

    claimed = claim_async_jobs("test_queue", limit=1, progress="Running")
    assert claimed == [{
        "job_type": "test_queue",
        "job_key": "quiz-input",
        "input": {"source_text": "private notes", "count": 5},
    }]
    assert claim_async_jobs("test_queue", limit=1, progress="Running") == []

    status = get_async_job_status("test_queue", "quiz-input")
    assert status["status"] == "running"
    assert status["progress"] == "Running"
