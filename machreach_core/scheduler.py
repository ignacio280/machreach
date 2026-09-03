"""Run the background schedule inside the web service.

MachReach used to pay for a second Render service whose whole job was to hold
an APScheduler. For a product this size that service was almost always idle,
and Render has no free tier for background workers, so it was a fixed monthly
cost for a process that mostly slept. The same schedule now runs in a daemon
thread inside the gunicorn worker, and `worker.py` still runs standalone, so
splitting them again is one dashboard change away.

Two things make this safe rather than clever:

* **It starts after the fork, never at import.** `gunicorn --preload` imports
  the application in the arbiter and forks workers from that image. Threads do
  not survive a fork, so a scheduler started at import time would exist in the
  arbiter and be dead in every worker — running nothing, silently. The
  `post_fork` hook in gunicorn.conf.py is what calls this.

* **Only one process ever schedules.** A Postgres session advisory lock decides
  which. `--workers 1` is already pinned in render.yaml, so today there is only
  one candidate; the lock is what keeps a future `--workers 2` from sending
  every streak email twice. It is held on its own connection, outside the
  application pool, for the life of the process, and the database releases it
  when the process dies.

Jobs that touch the queue already claim their work transactionally, so the lock
is not what protects those. It protects the ones that cannot be claimed: the
nightly plan refresh, the streak emails, the cleanup crons.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Any

_log = logging.getLogger("machreach.scheduler")

# Arbitrary but fixed: the pair identifies this lock among any other advisory
# locks the database might hold. Changing it would let a second scheduler start
# alongside a running one.
_LOCK_NAMESPACE = 0x4D414348  # "MACH"
_LOCK_ID = 1

_state: dict[str, Any] = {"scheduler": None, "lock_conn": None}
_start_lock = threading.Lock()


def _acquire_singleton_lock():
    """Take the scheduler lock, or return None if another process holds it.

    The connection is deliberately not from the application pool: an advisory
    lock lives as long as its session, and parking a pooled connection for the
    life of the process would spend one of the twelve the web service has.
    """
    from machreach_core.db import _USE_PG

    if not _USE_PG:
        # SQLite is local development, where there is only ever one process.
        return None, True

    import psycopg2

    from machreach_core.config import DATABASE_URL

    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_NAMESPACE, _LOCK_ID))
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            conn.close()
            return None, False
        return conn, True
    except Exception:
        # A database that cannot be reached is not a reason to refuse to serve
        # requests. Without the lock this process simply does not schedule, and
        # the next restart tries again.
        _log.exception("[scheduler] could not take the singleton lock; not scheduling")
        return None, False


def start(force: bool = False) -> bool:
    """Start the schedule in this process. Returns whether it started.

    Idempotent: a second call while one is running is a no-op, which matters
    because gunicorn calls post_fork once per worker and a recycle calls it
    again in the replacement.
    """
    if os.getenv("MACHREACH_DISABLE_INPROCESS_SCHEDULER", "").strip() == "1" and not force:
        _log.info("[scheduler] disabled by MACHREACH_DISABLE_INPROCESS_SCHEDULER")
        return False

    with _start_lock:
        if _state["scheduler"] is not None:
            return False

        lock_conn, may_start = _acquire_singleton_lock()
        if not may_start:
            _log.info("[scheduler] another process holds the schedule; not starting here")
            return False

        from apscheduler.schedulers.background import BackgroundScheduler

        import worker

        # Four threads, not APScheduler's default ten: this pool shares half a
        # CPU with eight gunicorn threads, and the point of the whole change is
        # to fit both in one small service. Only one job runs on a tick anyway.
        scheduler = BackgroundScheduler(
            timezone=worker.SCHEDULER_TIMEZONE,
            job_defaults=worker.JOB_DEFAULTS,
            executors={"default": {"type": "threadpool", "max_workers": 4}},
        )
        worker.register_jobs(scheduler)

        # The startup set runs through the scheduler rather than inline. Inline
        # would block post_fork, and Render kills an instance that cannot answer
        # /health within five seconds — the exact failure --preload was added to
        # avoid.
        now = _now(worker.SCHEDULER_TIMEZONE)
        for index, job in enumerate(worker.STARTUP_JOBS):
            scheduler.add_job(job, "interval", seconds=86400, id=f"startup_{job.__name__}_{index}",
                              next_run_time=now)

        scheduler.start()
        _state["scheduler"] = scheduler
        _state["lock_conn"] = lock_conn
        atexit.register(shutdown)
        _log.info("[scheduler] running in-process (pid %s)", os.getpid())
        return True


def _now(timezone_name: str):
    """Now, as an aware datetime — APScheduler compares next_run_time against
    one. Built from the name rather than read off the scheduler, so this does
    not depend on how APScheduler happens to store its timezone.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(timezone_name))


def shutdown(wait: bool = False) -> None:
    """Stop the schedule and release the lock. Registered with atexit.

    `wait` is False by default: a gunicorn recycle should not be held up by a
    quiz generation that has 40 seconds left to run, and every job is safe to
    interrupt — the queue ones are claimed transactionally and the rest catch
    up on the next tick. Tests pass True so nothing logs after the run ends.
    """
    scheduler = _state.get("scheduler")
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=wait)
        except Exception:
            pass
        _state["scheduler"] = None
    conn = _state.get("lock_conn")
    if conn is not None:
        try:
            conn.close()  # releases the advisory lock
        except Exception:
            pass
        _state["lock_conn"] = None


def is_running() -> bool:
    return _state.get("scheduler") is not None
