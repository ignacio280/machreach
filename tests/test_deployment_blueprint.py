"""The Render Blueprint is the production topology; pin the shape it must keep.

Production Postgres lives on Neon, so the Blueprint must not declare a Render
database (a sync would provision and bill one), and the background worker is a
cron job running one bounded pass per minute rather than an always-on
instance. These are the two changes that took the bill down; a well-meaning
edit that adds either back would quietly put it up again.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _blueprint() -> str:
    return (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")


def _service_block(text: str, name: str) -> str:
    start = text.index(f"name: {name}\n")
    rest = text[start:]
    nxt = rest.find("\n  - type:")
    return rest if nxt < 0 else rest[:nxt]


def test_no_render_database_is_declared():
    lines = [line.rstrip() for line in _blueprint().splitlines()]
    assert "databases:" not in lines
    assert "fromDatabase:" not in _blueprint()


def test_database_url_is_set_by_hand_on_both_services():
    text = _blueprint()
    for name in ("machreach", "machreach-worker"):
        block = _service_block(text, name)
        assert "- key: DATABASE_URL\n        sync: false" in block, name


def test_the_worker_is_a_cron_job_running_one_pass_per_minute():
    text = _blueprint()
    assert "  - type: cron\n    name: machreach-worker\n" in text
    assert "type: worker" not in text
    block = _service_block(text, "machreach-worker")
    assert 'schedule: "* * * * *"' in block
    assert "startCommand: python worker.py --once" in block
    assert "preDeployCommand" not in block
    assert "WORKER_RUN_MAX_SECONDS" in block


def test_the_web_service_keeps_migrations_and_the_cron_heartbeat_window():
    block = _service_block(_blueprint(), "machreach")
    assert "preDeployCommand: python migrate.py" in block
    assert "- key: WORKER_HEARTBEAT_STALE_SECONDS\n        value: \"180\"" in block
