"""Importing the app must not open anything a fork would inherit.

gunicorn runs with --preload: the application is imported once in the arbiter
and every worker is a fork of that image. It is what keeps a --max-requests
recycle from leaving the single worker slot empty for the seconds a fresh
import costs, which Render reads as a failed health check and a student reads
as a page that will not load.

Forking is only safe while import time stays free of live resources. A Postgres
pool created before the fork would be shared by every worker — same sockets,
interleaved traffic, and failures that look like database corruption rather
than a configuration mistake. So the two properties that make preloading safe
are pinned here rather than left as something a future import-time query can
quietly take away.

Run in a subprocess: this asserts what a *fresh* interpreter does at import,
and the test session has already imported the app with test settings.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Production settings, and a database that cannot be reached. Anything that
# touches the database at import fails loudly instead of passing silently.
PRODUCTION_ENV = {
    "RENDER": "1",
    "SECRET_KEY": "preload-test-secret",
    "ENCRYPTION_KEY": "preload-test-encryption",
    "OPERATIONS_SECRET": "preload-test-operations",
    "ADMIN_ACTION_SECRET": "preload-test-admin",
    "DATABASE_URL": "postgresql://nobody:nope@127.0.0.1:1/unreachable",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}

PROBE = """
import sys
sys.path.insert(0, %r)
import app
import machreach_core.db as db

assert app._RUN_STARTUP_MIGRATIONS is False, "migrations must not run at import in production"
assert getattr(db, "_POOL", None) is None, "the Postgres pool must not exist before the fork"
print("PRELOAD_SAFE")
""" % str(REPO_ROOT)


def _import_app_as_production():
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        env=PRODUCTION_ENV,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )


def test_importing_the_app_opens_no_database_connection_to_fork():
    """The import must survive a database that refuses every connection."""
    result = _import_app_as_production()

    assert "PRELOAD_SAFE" in result.stdout, (
        "importing the app reached the database or created its pool, which "
        f"--preload would fork into every worker.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr[-2000:]}"
    )
    assert result.returncode == 0


def test_the_preloaded_start_command_is_the_one_render_runs():
    """A start command without --preload puts the recycle gap back."""
    blueprint = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    start = next(
        line for line in blueprint.splitlines() if "startCommand: gunicorn" in line
    )

    assert "--preload" in start
    # One worker is only survivable because a replacement is a fork.
    assert "--workers 1" in start
