"""Configuration safety checks."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing

from machreach_core import config as cfg


def test_compatible_local_database_is_migrated_without_a_named_fallback(tmp_path):
    source = tmp_path / "previous-install.db"
    target = tmp_path / "machreach.db"
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY)")
        db.commit()

    cfg._migrate_compatible_local_database(target)

    assert target.exists()
    assert not source.exists()


def test_local_database_migration_includes_committed_wal_changes(tmp_path):
    source = tmp_path / "previous-install.db"
    target = tmp_path / "machreach.db"
    source_db = sqlite3.connect(source)
    try:
        source_db.execute("PRAGMA journal_mode=WAL")
        source_db.execute("PRAGMA wal_autocheckpoint=0")
        source_db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
        source_db.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY)")
        source_db.execute("INSERT INTO clients (id, name) VALUES (1, 'Preserved User')")
        source_db.commit()

        cfg._migrate_compatible_local_database(target)

        with closing(sqlite3.connect(target)) as migrated_db:
            row = migrated_db.execute("SELECT name FROM clients WHERE id = 1").fetchone()
        assert row == ("Preserved User",)
    finally:
        source_db.close()


def test_default_database_filename_is_machreach():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    # Keep an explicit empty value so load_dotenv() cannot rehydrate a local
    # developer override from the ignored .env file in the repository root.
    env["DATABASE_PATH"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from machreach_core.config import DATABASE_PATH; print(DATABASE_PATH.name)",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout.strip() == "machreach.db"


def test_encryption_key_is_required_in_render():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["RENDER"] = "1"
    env["SECRET_KEY"] = "prod-secret"
    env["ENCRYPTION_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import machreach_core.config"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "ENCRYPTION_KEY must be set in production" in result.stderr


def test_known_fallback_secrets_are_rejected_in_non_render_production():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env.pop("RENDER", None)
    env["FLASK_ENV"] = "production"
    env["SECRET_KEY"] = ""
    env["ENCRYPTION_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import machreach_core.config"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "SECRET_KEY must be set in production" in result.stderr
