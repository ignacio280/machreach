"""The background schedule now runs inside the web service.

What these pin is the part that fails silently: a schedule that loses a job, or
two processes that both decide to run it. Neither shows up in a request, and
the first symptom of either is a student not getting a plan, or getting the same
streak email twice.
"""
import logging

import pytest

import worker
from machreach_core import scheduler as sched


class FakeScheduler:
    """Records what was registered, without starting anything."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.jobs = []
        self.started = False
        self.timezone = "UTC"

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.started = False


# The schedule as it ran in the dedicated worker service. If a change here is
# deliberate, change this list in the same commit and say why.
EXPECTED_JOB_IDS = {
    "process_async_jobs",
    "worker_heartbeat",
    "recover_worker_state",
    "retired_billing_cancellations",
    "refresh_student_plans",
    "streak_risk_push",
    "clean_abandoned_unverified_accounts",
    "expire_billing_grace_periods",
    "leaderboard_payouts",
    "purge_deleted_courses",
}


def test_register_jobs_covers_the_whole_schedule():
    fake = FakeScheduler()
    worker.register_jobs(fake)
    assert {job["id"] for job in fake.jobs} == EXPECTED_JOB_IDS


def test_the_queue_drain_and_payouts_cannot_pile_up():
    """Both poll faster than they can take, so overlap has to be refused."""
    fake = FakeScheduler()
    worker.register_jobs(fake)
    by_id = {job["id"]: job for job in fake.jobs}
    assert by_id["process_async_jobs"]["max_instances"] == 1
    assert by_id["leaderboard_payouts"]["max_instances"] == 1
    assert by_id["leaderboard_payouts"]["coalesce"] is True


def test_a_job_that_misfires_on_a_busy_process_still_runs():
    """Sharing a CPU with eight request threads, APScheduler's one-second
    default would drop the nightly jobs rather than run them late."""
    assert worker.JOB_DEFAULTS["misfire_grace_time"] >= 600
    assert worker.JOB_DEFAULTS["coalesce"] is True


def test_startup_set_is_the_recovery_path():
    names = [job.__name__ for job in worker.STARTUP_JOBS]
    assert names == [
        "heartbeat",
        "recover_worker_state",
        "cancel_retired_product_subscriptions",
        "process_leaderboard_payouts",
        "process_async_jobs",
    ]


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    sched.shutdown(wait=True)
    yield
    sched.shutdown(wait=True)


def test_start_registers_the_schedule_and_runs_the_startup_set(monkeypatch):
    created = {}

    def fake_background_scheduler(**kwargs):
        created["scheduler"] = FakeScheduler(**kwargs)
        return created["scheduler"]

    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler", fake_background_scheduler
    )
    monkeypatch.setattr(sched, "_acquire_singleton_lock", lambda: (None, True))

    assert sched.start(force=True) is True
    fake = created["scheduler"]
    assert fake.started is True
    assert EXPECTED_JOB_IDS <= {job["id"] for job in fake.jobs}
    # Every startup job is queued to run immediately rather than inline: the
    # fork hook must not block the worker from answering /health.
    startup = [job for job in fake.jobs if job["id"].startswith("startup_")]
    assert len(startup) == len(worker.STARTUP_JOBS)
    assert all(job.get("next_run_time") is not None for job in startup)


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler",
        lambda **kwargs: FakeScheduler(**kwargs),
    )
    monkeypatch.setattr(sched, "_acquire_singleton_lock", lambda: (None, True))

    assert sched.start(force=True) is True
    # gunicorn calls post_fork again for the worker that replaces a recycled
    # one; in a process that already schedules, that must change nothing.
    assert sched.start(force=True) is False
    assert sched.is_running() is True


def test_a_second_process_does_not_schedule(monkeypatch):
    """`--workers 1` is pinned today, but a future `--workers 2` must not send
    every streak email twice."""
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler",
        lambda **kwargs: FakeScheduler(**kwargs),
    )
    monkeypatch.setattr(sched, "_acquire_singleton_lock", lambda: (None, False))

    assert sched.start(force=True) is False
    assert sched.is_running() is False


def test_an_unreachable_database_does_not_schedule_and_does_not_raise(monkeypatch, caplog):
    """Serving requests matters more than scheduling; the stale heartbeat on
    /health/operations is the alert for a process that never started one."""
    import psycopg2

    monkeypatch.setattr("machreach_core.db._USE_PG", True, raising=False)

    def refuse(*args, **kwargs):
        raise psycopg2.OperationalError("could not connect")

    monkeypatch.setattr(psycopg2, "connect", refuse)

    with caplog.at_level(logging.ERROR):
        assert sched.start(force=True) is False
    assert sched.is_running() is False


def test_the_environment_can_turn_it_off(monkeypatch):
    monkeypatch.setenv("MACHREACH_DISABLE_INPROCESS_SCHEDULER", "1")
    monkeypatch.setattr(sched, "_acquire_singleton_lock", lambda: (None, True))
    assert sched.start() is False
    assert sched.is_running() is False


def test_gunicorn_hook_swallows_a_failing_scheduler(monkeypatch):
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "machreach_gunicorn_conf", pathlib.Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    )
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sched, "start", explode)

    class FakeLog:
        def __init__(self):
            self.exceptions = 0

        def info(self, *args, **kwargs):
            pass

        def exception(self, *args, **kwargs):
            self.exceptions += 1

    class FakeServer:
        def __init__(self):
            self.log = FakeLog()

    class FakeWorker:
        pid = 123

    server = FakeServer()
    conf.post_fork(server, FakeWorker())  # must not raise
    assert server.log.exceptions == 1


def test_it_really_runs_a_job_in_this_process(monkeypatch):
    """No fakes: a real BackgroundScheduler, a real thread, a real execution.

    Everything above checks wiring. This checks the thing the whole change is
    for — that a job actually fires inside the web process — because a
    scheduler that registers ten jobs and runs none looks identical from the
    outside.

    It registers one probe instead of the real schedule on purpose. Running the
    real one here would drain the job queue and start leaderboard payouts
    against the shared test database, and the tests that own those would then
    fail somewhere else entirely.
    """
    import threading

    fired = threading.Event()

    def probe():
        fired.set()

    monkeypatch.setattr(worker, "STARTUP_JOBS", (probe,))
    monkeypatch.setattr(worker, "register_jobs", lambda scheduler: scheduler)
    monkeypatch.setattr(sched, "_acquire_singleton_lock", lambda: (None, True))

    assert sched.start(force=True) is True
    try:
        assert fired.wait(timeout=15), "the scheduler started but never ran a job"
    finally:
        sched.shutdown(wait=True)

    assert sched.is_running() is False
