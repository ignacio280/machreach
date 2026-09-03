"""A doorbell for the background schedule.

The queue used to be drained by polling every five seconds. That is the right
shape when the database is a server you rent by the month and it is running
anyway. It is the wrong shape when you rent the database by the second: a query
every five seconds means the compute never idles, never suspends, and bills for
every hour of the month whether or not a student is studying.

Since the schedule moved into the web process (machreach_core/scheduler.py),
the request that enqueues a job and the thread that runs it are in the same
process, so the queue does not need polling at all. The request rings this
bell; the drain loop is waiting on it and starts immediately. A student's quiz
now starts sooner than it used to, and a database with nobody studying is asked
nothing at all.

The periodic sweep stays, slowed right down, because a bell only rings for work
enqueued by a process that is still alive. Work orphaned by a crash is found by
the sweep, and by the startup pass that runs whenever the process comes back.
"""
from __future__ import annotations

import threading

_bell = threading.Event()


def ring() -> None:
    """Signal that there is work to pick up. Safe from any thread."""
    _bell.set()


def wait(timeout: float) -> bool:
    """Block until someone rings, or `timeout` seconds pass.

    Returns True when woken by a ring. The caller drains the queue either way:
    a timeout is the periodic sweep, a ring is a fresh job.
    """
    rang = _bell.wait(timeout)
    _bell.clear()
    return rang


def pending() -> bool:
    """Whether a ring is waiting to be consumed. For tests."""
    return _bell.is_set()


def reset() -> None:
    """Drop any pending ring. For tests."""
    _bell.clear()
