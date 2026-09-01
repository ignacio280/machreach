"""The background worker inside the web process.

With EMBEDDED_WORKER=1, gunicorn's post-fork hook starts the job scheduler
in each worker process and stops it on exit. What these pin: the hook does
nothing unless asked (a second worker service must stay possible), it starts
after the fork and never at import (test_gunicorn_preload_is_safe covers the
import side), the scheduler carries every job the standalone worker has, the
startup pass runs off the hook's thread, and shutdown gives a job in flight
a bounded wait instead of killing it.
"""
import importlib.util
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import worker

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location("gunicorn_conf", REPO_ROOT / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append((func.__name__, trigger, kwargs))


def test_the_embedded_switch_reads_the_environment(monkeypatch):
    for value, expected in (("1", True), ("true", True), ("on", True), ("0", False), ("", False)):
        monkeypatch.setenv("EMBEDDED_WORKER", value)
        assert worker.embedded_worker_enabled() is expected
    monkeypatch.delenv("EMBEDDED_WORKER")
    assert worker.embedded_worker_enabled() is False


def test_every_runtime_registers_the_same_jobs_with_database_backed_daily_schedule():
    scheduler = RecordingScheduler()
    worker.register_jobs(scheduler)
    ids = {kwargs["id"] for _f, _t, kwargs in scheduler.jobs}
    assert ids == {
        "process_async_jobs", "worker_heartbeat", "recover_worker_state",
        "retired_billing_cancellations", "expire_billing_grace_periods",
        "leaderboard_payouts", "daily_schedule",
    }
    daily = next(j for j in scheduler.jobs if j[2]["id"] == "daily_schedule")
    assert daily[0] == "run_due_daily_jobs" and daily[1] == "interval"
    assert all(trigger == "interval" for _f, trigger, _k in scheduler.jobs)


def test_the_startup_pass_runs_every_interval_job_and_the_queue_and_contains_failures(monkeypatch):
    order, reported = [], []

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(worker, "INTERVAL_JOBS", (("a", lambda: order.append("a")), ("b", boom)))
    monkeypatch.setattr(worker, "process_async_jobs", lambda: order.append("queue") or 0)
    monkeypatch.setattr(worker, "_report_worker_error", lambda ctx, exc: reported.append(ctx))
    worker._startup_pass()
    assert order == ["a", "queue"] and reported == ["B"]

    monkeypatch.setattr(worker, "process_async_jobs", boom)
    worker._startup_pass()
    assert reported == ["B", "B", "PROCESS_ASYNC_JOBS"]


def test_start_embedded_runs_the_startup_pass_off_the_calling_thread_then_stops_cleanly(monkeypatch):
    ran = threading.Event()
    seen = {}

    def startup():
        seen["thread"] = threading.current_thread().name
        ran.set()

    monkeypatch.setattr(worker, "_startup_pass", startup)
    monkeypatch.setattr(worker, "register_jobs", lambda scheduler: None)

    started = time.monotonic()
    scheduler = worker.start_embedded()
    assert time.monotonic() - started < 2
    assert ran.wait(5), "the startup pass never ran"
    assert seen["thread"] != threading.current_thread().name
    assert scheduler.running

    assert worker.stop_embedded(scheduler, wait_seconds=10) is True
    assert not scheduler.running


def test_start_embedded_carries_the_full_schedule_on_a_bounded_thread_pool(monkeypatch):
    monkeypatch.setattr(worker, "_startup_pass", lambda: None)
    scheduler = worker.start_embedded()
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert {"process_async_jobs", "daily_schedule", "worker_heartbeat"} <= ids
        executor = scheduler._executors["default"]
        assert executor._pool._max_workers == worker.EMBEDDED_WORKER_THREADS
    finally:
        worker.stop_embedded(scheduler, wait_seconds=10)


def test_stop_embedded_waits_a_bounded_time_for_a_job_in_flight():
    class SlowScheduler:
        def shutdown(self, wait=True):
            time.sleep(0.5)

    assert worker.stop_embedded(SlowScheduler(), wait_seconds=0.05) is False
    assert worker.stop_embedded(SlowScheduler(), wait_seconds=5) is True


def test_gunicorn_hooks_start_only_when_asked_and_stop_what_they_started(monkeypatch):
    conf = _load_gunicorn_conf()
    server = SimpleNamespace(log=logging.getLogger("test-gunicorn"))
    gunicorn_worker = SimpleNamespace(pid=4242)
    started, stopped = [], []
    monkeypatch.setattr(worker, "start_embedded", lambda: started.append("s") or "the-scheduler")
    monkeypatch.setattr(worker, "stop_embedded", lambda scheduler: stopped.append(scheduler))

    monkeypatch.delenv("EMBEDDED_WORKER", raising=False)
    conf.post_fork(server, gunicorn_worker)
    conf.worker_exit(server, gunicorn_worker)
    assert started == [] and stopped == []

    monkeypatch.setenv("EMBEDDED_WORKER", "1")
    conf.post_fork(server, gunicorn_worker)
    assert started == ["s"] and gunicorn_worker.machreach_scheduler == "the-scheduler"
    conf.worker_exit(server, gunicorn_worker)
    assert stopped == ["the-scheduler"]


def test_the_blueprint_is_one_web_service_with_the_embedded_worker():
    blueprint = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "type: worker" not in blueprint
    assert "databases:" not in blueprint.splitlines()
    assert "fromDatabase" not in blueprint
    assert "- key: EMBEDDED_WORKER\n        value: \"1\"" in blueprint
    assert "- key: DATABASE_URL\n        sync: false" in blueprint
    # The hook file gunicorn picks up by name must exist next to the app.
    assert (REPO_ROOT / "gunicorn.conf.py").is_file()
