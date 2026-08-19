from machreach_core.db import (
    _exec,
    claim_async_jobs,
    enqueue_async_job,
    fail_async_job,
    get_async_job_status,
    get_db,
    list_async_jobs,
    record_worker_heartbeat,
    recover_stale_async_jobs,
    retry_async_job,
    set_async_job_status,
)


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
    run_id = claimed[0].pop("run_id", "")
    assert run_id
    assert claimed == [{
        "job_type": "test_queue",
        "job_key": "quiz-input",
        "attempts": 1,
        "max_attempts": 3,
        "input": {"source_text": "private notes", "count": 5},
    }]
    assert claim_async_jobs("test_queue", limit=1, progress="Running") == []

    status = get_async_job_status("test_queue", "quiz-input")
    assert status["status"] == "running"
    assert status["progress"] == "Running"


def test_failed_async_job_retries_until_max_attempts_then_errors():
    enqueue_async_job("retry_queue", "job-1", input_payload={"x": 1}, max_attempts=2)

    first = claim_async_jobs("retry_queue", limit=1)[0]
    assert first["attempts"] == 1

    retried = fail_async_job("retry_queue", "job-1", "temporary outage", retry=True)
    assert retried["status"] == "queued"
    assert "Retrying attempt 2 of 2" in retried["progress"]

    second = claim_async_jobs("retry_queue", limit=1)[0]
    assert second["attempts"] == 2

    failed = fail_async_job("retry_queue", "job-1", "still down", retry=True)
    assert failed["status"] == "error"
    assert failed["error"] == "still down"

    assert retry_async_job("retry_queue", "job-1") is True
    manual = claim_async_jobs("retry_queue", limit=1)[0]
    assert manual["attempts"] == 1


def test_worker_heartbeat_is_listed_separately():
    record_worker_heartbeat("test-worker")

    heartbeats = list_async_jobs(job_type="worker_heartbeat")

    assert heartbeats[0]["job_type"] == "worker_heartbeat"
    assert heartbeats[0]["job_key"] == "test-worker"
    assert heartbeats[0]["status"] == "running"


def test_interrupted_job_is_requeued_with_its_attempt_budget_preserved():
    enqueue_async_job("recoverable", "job-1", input_payload={"value": 1})
    first = claim_async_jobs("recoverable", limit=1)
    assert first[0]["attempts"] == 1
    with get_db() as db:
        _exec(
            db,
            "UPDATE async_jobs SET updated_at = %s WHERE job_type = %s AND job_key = %s",
            ("2000-01-01 00:00:00", "recoverable", "job-1"),
        )

    assert recover_stale_async_jobs(stale_after_seconds=60) >= 1
    second = claim_async_jobs("recoverable", limit=1)
    assert second[0]["attempts"] == 2


def test_each_enqueue_gets_its_own_run_id_but_a_retry_keeps_it():
    """The run id is what AI reservations key on.

    A new enqueue must look like new work; a retry of the same run must not,
    or the retry would reserve (and bill) a second generation.
    """
    enqueue_async_job("run_id_queue", "job-1", input_payload={"x": 1})
    first = claim_async_jobs("run_id_queue", limit=1)[0]

    fail_async_job("run_id_queue", "job-1", "temporary outage", retry=True)
    retried = claim_async_jobs("run_id_queue", limit=1)[0]
    assert retried["run_id"] == first["run_id"]
    assert retried["input"] == {"x": 1}

    set_async_job_status("run_id_queue", "job-1", "done", progress="Finished")
    enqueue_async_job("run_id_queue", "job-1", input_payload={"x": 2})
    second = claim_async_jobs("run_id_queue", limit=1)[0]
    assert second["run_id"] != first["run_id"]


def test_a_job_queued_before_run_ids_existed_is_backfilled_on_claim():
    enqueue_async_job("legacy_queue", "job-1", input_payload={"x": 1})
    with get_db() as db:
        _exec(
            db,
            "UPDATE async_jobs SET input_json = %s WHERE job_type = %s AND job_key = %s",
            ('{"x": 1}', "legacy_queue", "job-1"),
        )

    claimed = claim_async_jobs("legacy_queue", limit=1)[0]
    assert claimed["run_id"]
    assert claimed["input"] == {"x": 1}

    fail_async_job("legacy_queue", "job-1", "temporary outage", retry=True)
    assert claim_async_jobs("legacy_queue", limit=1)[0]["run_id"] == claimed["run_id"]
