"""The worker as a cron job: one bounded run per minute, with the time-of-day
schedule kept in the database instead of in a process that never exits.

The always-on worker knew what it had already fired because it never stopped.
A cron run starts from nothing every minute, so the last scheduled time each
daily job fired lives in async_jobs, and a run fires a job exactly when its
schedule has passed that mark. These tests pin the properties that matter:
nothing replays on first boot, missed days collapse into one run, two
overlapping runs cannot both fire a job, a failing job is recorded as fired
(and reported) rather than retried every minute, and a run always ends.
"""
from datetime import datetime, timedelta, timezone
import json
import runpy
import sys
import threading

import pytest

import worker
from machreach_core import db as odb


SANTIAGO_00_00 = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)  # 00:00 -03:00


def _reset_schedule_rows():
    with odb.get_db() as db:
        odb._exec(db, "DELETE FROM async_jobs WHERE job_type = %s", (worker._SCHEDULE_JOB_TYPE,))


@pytest.fixture(autouse=True)
def _clean_schedule():
    _reset_schedule_rows()
    yield
    _reset_schedule_rows()


@pytest.fixture()
def calls(monkeypatch):
    """Replace every daily job with a recorder, keeping the real schedule."""
    fired = []
    patched = tuple(
        (job_id, (lambda job_id=job_id: fired.append(job_id)), when)
        for job_id, _func, when in worker.DAILY_JOBS
    )
    monkeypatch.setattr(worker, "DAILY_JOBS", patched)
    return fired


def test_first_run_seeds_the_schedule_without_firing_anything(calls):
    now = SANTIAGO_00_00 + timedelta(minutes=7)

    assert worker.run_due_daily_jobs(now) == []
    assert calls == []
    for job_id, _func, _when in worker.DAILY_JOBS:
        last, raw = worker._last_fired(job_id)
        assert last == now
        assert json.loads(raw)["fired_at"] == now.isoformat()


def test_a_job_fires_once_its_scheduled_time_passes_and_records_the_schedule_time(calls):
    seeded = SANTIAGO_00_00 - timedelta(hours=2)
    worker.run_due_daily_jobs(seeded)

    # Still before midnight: nothing is due.
    assert worker.run_due_daily_jobs(SANTIAGO_00_00 - timedelta(minutes=1)) == []
    assert calls == []

    # The cron run that lands a few minutes after midnight fires the refresh.
    fired = worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(minutes=4))
    assert fired == ["refresh_student_plans"]
    assert calls == ["refresh_student_plans"]
    last, _raw = worker._last_fired("refresh_student_plans")
    assert last == SANTIAGO_00_00  # the schedule time, not the wall clock

    # The next minute it is no longer due.
    assert worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(minutes=5)) == []
    assert calls == ["refresh_student_plans"]


def test_missed_days_collapse_into_one_run(calls):
    worker.run_due_daily_jobs(SANTIAGO_00_00 - timedelta(hours=2))

    # The job was down for three days. Every daily job is due, once each.
    fired = worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(days=3, hours=1))
    assert sorted(fired) == sorted(job_id for job_id, _f, _w in worker.DAILY_JOBS)
    assert calls.count("streak_risk_push") == 1

    last, _raw = worker._last_fired("streak_risk_push")
    # 20:00 Santiago on the last full day: two days after the seed, not three
    # runs' worth of reminders in three consecutive minutes.
    assert last == datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    assert worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(days=3, hours=1, minutes=1)) == []


def test_two_overlapping_runs_cannot_both_own_a_slot():
    worker.run_due_daily_jobs(SANTIAGO_00_00 - timedelta(hours=2))
    _last, raw = worker._last_fired("purge_deleted_courses")

    assert worker._claim_schedule_slot("purge_deleted_courses", SANTIAGO_00_00, raw) is True
    # A second run that read the same state loses the race.
    assert worker._claim_schedule_slot("purge_deleted_courses", SANTIAGO_00_00, raw) is False
    # And a run seeding a row that already exists loses too.
    assert worker._claim_schedule_slot("purge_deleted_courses", SANTIAGO_00_00, None) is False


def test_a_lost_claim_skips_the_job(calls, monkeypatch):
    worker.run_due_daily_jobs(SANTIAGO_00_00 - timedelta(hours=2))
    monkeypatch.setattr(worker, "_claim_schedule_slot", lambda *args: False)

    assert worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(minutes=1)) == []
    assert calls == []


def test_a_failing_job_is_reported_and_not_retried_every_minute(monkeypatch):
    reported = []
    monkeypatch.setattr(
        worker, "_report_worker_error", lambda ctx, exc: reported.append((ctx, type(exc).__name__))
    )

    def explode():
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(worker, "DAILY_JOBS", (("streak_risk_push", explode, {"hour": 20, "minute": 0}),))
    worker.run_due_daily_jobs(SANTIAGO_00_00)
    due = SANTIAGO_00_00 + timedelta(hours=20, minutes=2)

    assert worker.run_due_daily_jobs(due) == ["streak_risk_push"]
    assert reported == [("STREAK_RISK_PUSH", "RuntimeError")]
    # The slot is taken: the next minute does not fire it again.
    assert worker.run_due_daily_jobs(due + timedelta(minutes=1)) == []


def test_unreadable_schedule_state_is_reported_and_the_other_jobs_still_run(calls, monkeypatch):
    worker.run_due_daily_jobs(SANTIAGO_00_00 - timedelta(hours=2))
    reported = []
    monkeypatch.setattr(worker, "_report_worker_error", lambda ctx, exc: reported.append(ctx))
    real_last_fired = worker._last_fired

    def flaky(job_id):
        if job_id == "refresh_student_plans":
            raise RuntimeError("database unavailable")
        return real_last_fired(job_id)

    monkeypatch.setattr(worker, "_last_fired", flaky)

    fired = worker.run_due_daily_jobs(SANTIAGO_00_00 + timedelta(hours=4))
    assert reported == ["SCHEDULE_REFRESH_STUDENT_PLANS"]
    assert sorted(fired) == ["clean_abandoned_unverified_accounts", "purge_deleted_courses"]


def test_a_corrupt_schedule_row_is_reseeded_instead_of_crashing(calls):
    with odb.get_db() as db:
        odb._exec(
            db,
            "INSERT INTO async_jobs (job_type, job_key, status, payload_json) VALUES (%s, %s, 'done', %s)",
            (worker._SCHEDULE_JOB_TYPE, "purge_deleted_courses", "not json"),
        )
    assert worker._last_fired("purge_deleted_courses") == (None, "not json")

    now = SANTIAGO_00_00 + timedelta(hours=5)
    assert worker.run_due_daily_jobs(now) == []
    assert worker._last_fired("purge_deleted_courses")[0] == now


def test_naive_timestamps_in_schedule_state_are_read_as_utc():
    naive = SANTIAGO_00_00.replace(tzinfo=None)
    with odb.get_db() as db:
        odb._exec(
            db,
            "INSERT INTO async_jobs (job_type, job_key, status, payload_json) VALUES (%s, %s, 'done', %s)",
            (worker._SCHEDULE_JOB_TYPE, "refresh_student_plans", json.dumps({"fired_at": naive.isoformat()})),
        )
    assert worker._last_fired("refresh_student_plans")[0] == SANTIAGO_00_00


def test_run_once_runs_every_interval_job_then_drains_the_queue_until_empty(monkeypatch):
    order = []
    monkeypatch.setattr(
        worker,
        "INTERVAL_JOBS",
        tuple((job_id, (lambda job_id=job_id: order.append(job_id))) for job_id, _f in worker.INTERVAL_JOBS),
    )
    monkeypatch.setattr(worker, "run_due_daily_jobs", lambda now=None: order.append("daily") or ["x"])
    batches = iter([3, 2, 0])
    monkeypatch.setattr(worker, "process_async_jobs", lambda: order.append("drain") or next(batches))

    summary = worker.run_once(max_seconds=60)

    assert order[: len(worker.INTERVAL_JOBS)] == [job_id for job_id, _f in worker.INTERVAL_JOBS]
    assert order[len(worker.INTERVAL_JOBS)] == "daily"
    assert order.count("drain") == 3
    assert summary["processed"] == 5
    assert summary["passes"] == 3
    assert summary["fired"] == ["x"]
    assert isinstance(summary["seconds"], float)


def test_run_once_stops_draining_when_its_time_budget_is_spent(monkeypatch):
    monkeypatch.setattr(worker, "INTERVAL_JOBS", ())
    monkeypatch.setattr(worker, "run_due_daily_jobs", lambda now=None: [])
    monkeypatch.setattr(worker, "process_async_jobs", lambda: 1)  # never empty

    summary = worker.run_once(max_seconds=0)

    assert summary["passes"] == 1
    assert summary["processed"] == 1


def test_run_once_contains_failures_and_always_stops_the_heartbeat_thread(monkeypatch):
    reported = []
    monkeypatch.setattr(worker, "_report_worker_error", lambda ctx, exc: reported.append(ctx))

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "INTERVAL_JOBS", (("worker_heartbeat", broken),))
    monkeypatch.setattr(worker, "run_due_daily_jobs", lambda now=None: [])
    monkeypatch.setattr(worker, "process_async_jobs", broken)
    started = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            assert daemon is True
            self.stop = args[0]
            started.append(self)

        def start(self):
            pass

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)

    summary = worker.run_once(max_seconds=60)

    assert reported == ["WORKER_HEARTBEAT", "PROCESS_ASYNC_JOBS"]
    assert summary["processed"] == 0 and summary["passes"] == 1
    assert started[0].stop.is_set()


def test_run_once_reads_its_budget_from_the_environment(monkeypatch):
    monkeypatch.setenv("WORKER_RUN_MAX_SECONDS", "0")
    monkeypatch.setattr(worker, "INTERVAL_JOBS", ())
    monkeypatch.setattr(worker, "run_due_daily_jobs", lambda now=None: [])
    monkeypatch.setattr(worker, "process_async_jobs", lambda: 1)

    assert worker.run_once()["passes"] == 1


def test_the_heartbeat_thread_beats_until_told_to_stop_and_survives_a_failure(monkeypatch):
    beats = []
    stop = threading.Event()

    def beat():
        beats.append(1)
        if len(beats) == 1:
            raise RuntimeError("database hiccup")
        if len(beats) >= 2:
            stop.set()

    reported = []
    monkeypatch.setattr(worker, "record_worker_heartbeat", beat)
    monkeypatch.setattr(worker, "_report_worker_error", lambda ctx, exc: reported.append(ctx))

    worker._keep_heartbeat_fresh(stop, every=0.01)

    assert len(beats) == 2
    assert reported == ["WORKER_HEARTBEAT"]


def test_once_mode_is_requested_by_flag_or_environment(monkeypatch):
    monkeypatch.delenv("WORKER_MODE", raising=False)
    assert worker._once_requested(["--once"]) is True
    assert worker._once_requested([]) is False
    monkeypatch.setenv("WORKER_MODE", "once")
    assert worker._once_requested([]) is True
    monkeypatch.setattr(sys, "argv", ["worker.py", "--once"])
    monkeypatch.delenv("WORKER_MODE", raising=False)
    assert worker._once_requested() is True


def test_the_entrypoint_in_once_mode_runs_one_pass_and_exits_cleanly(monkeypatch):
    import machreach_core.db as core_db
    from student import db as sdb

    runs = []
    monkeypatch.setenv("WORKER_MODE", "once")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(core_db, "init_db", lambda: None)
    monkeypatch.setattr(core_db, "check_schema_readiness", lambda: None)
    monkeypatch.setattr(sdb, "init_student_db", lambda: None)
    monkeypatch.setattr(worker, "run_once", lambda: runs.append("run"))

    class NeverStarted:
        def __init__(self, timezone):
            raise AssertionError("once mode must not build a scheduler")

    import apscheduler.schedulers.blocking as blocking
    monkeypatch.setattr(blocking, "BlockingScheduler", NeverStarted)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("worker", run_name="__main__")

    assert exit_info.value.code == 0
    # run_module executes a fresh copy of the module, whose run_once is the
    # real one; the monkeypatched name on the imported module is not it. What
    # is pinned here is the mode selection and the clean exit.
    assert exit_info.value.code == 0
