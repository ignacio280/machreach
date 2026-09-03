"""Gunicorn configuration.

Gunicorn loads this file automatically when it is in the working directory, so
render.yaml's startCommand does not name it.

Its only job is the post_fork hook. `--preload` imports the application in the
arbiter and forks each worker from that image, and threads do not survive a
fork: a scheduler started at import time would live in the arbiter and be dead
in every worker, running nothing and reporting nothing. post_fork runs inside
the worker process, after the fork, which is the one place a background thread
can be started safely.

See machreach_core/scheduler.py for why the schedule runs here at all.
"""


def post_fork(server, worker):
    try:
        from machreach_core import scheduler

        if scheduler.start():
            server.log.info("[gunicorn] background schedule started in worker %s", worker.pid)
        else:
            server.log.info("[gunicorn] background schedule not started in worker %s", worker.pid)
    except Exception:
        # A scheduler that will not start must not stop the worker from serving
        # requests. The jobs are all catch-up-safe, and /health/operations
        # reports a stale worker heartbeat, which is the alert for exactly this.
        server.log.exception("[gunicorn] background schedule failed to start")


def worker_exit(server, worker):
    try:
        from machreach_core import scheduler

        scheduler.shutdown()
    except Exception:
        pass
