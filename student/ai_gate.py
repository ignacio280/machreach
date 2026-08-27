"""Cap how many web threads may wait on a model at once.

The web service is one gunicorn worker with eight threads, and three endpoints
call a model inline — the planner's material analysis, its block tools, and
quiz analysis — each holding its thread for up to forty seconds. Eight of
those at once and no thread is left to answer anything, including the /health
probe Render kills the instance over.

Five slots leaves three threads always free for ordinary pages and the probe.
The sixth caller gets a 429 and a message to retry, which is a much smaller
failure than the whole instance dying mid-request for everyone.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

_MAX_SLOTS = max(1, int(os.getenv("INLINE_AI_MAX_CONCURRENCY", "5")))
_slots = threading.BoundedSemaphore(_MAX_SLOTS)


class InlineAIBusy(RuntimeError):
    """Every inline-AI slot is taken; the caller should retry shortly."""


@contextmanager
def inline_ai_slot():
    if not _slots.acquire(blocking=False):
        raise InlineAIBusy()
    try:
        yield
    finally:
        _slots.release()
