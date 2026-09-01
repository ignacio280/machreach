"""gunicorn reads this file from the working directory on its own.

It exists for one thing: the embedded background worker. With
EMBEDDED_WORKER=1 in the environment, every gunicorn worker process starts
the job scheduler from worker.py right after it is forked, and stops it
(giving a job in flight up to a minute) when the process exits. The web
service then does the background work too, and the separate worker
instance is not needed.

Nothing here runs in the arbiter: with --preload the app is imported once
there and forked, and a scheduler started before the fork would be shared
by every child. post_fork is the first moment that belongs to the child.
"""
from __future__ import annotations


def post_fork(server, worker):
    import worker as machreach_worker

    if not machreach_worker.embedded_worker_enabled():
        return
    worker.machreach_scheduler = machreach_worker.start_embedded()
    server.log.info("embedded worker started in worker pid %s", worker.pid)


def worker_exit(server, worker):
    scheduler = getattr(worker, "machreach_scheduler", None)
    if scheduler is None:
        return
    import worker as machreach_worker

    machreach_worker.stop_embedded(scheduler)
