"""scripts/migrate_to_neon.py copies a database and refuses to lie about it.

The precondition and comparison logic runs everywhere. The end-to-end copy
needs a Postgres superuser (to create the two throwaway databases) and a
pg_dump at least as new as the server, so it runs in the Postgres CI job and
skips itself cleanly anywhere else.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

from machreach_core import db as odb

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migrate_to_neon.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_to_neon", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


# --- Logic that needs no database ------------------------------------------

def test_identical_or_missing_urls_are_refused():
    with pytest.raises(script.MigrationError, match="required"):
        script.check_preconditions("", "postgresql://x/y")
    with pytest.raises(script.MigrationError, match="same database"):
        script.check_preconditions("postgresql://a/b", "postgresql://a/b")


def test_a_neon_pooled_endpoint_is_refused_as_the_target():
    with pytest.raises(script.MigrationError, match="pooled"):
        script.check_preconditions(
            "postgresql://u:p@src.example/db",
            "postgresql://u:p@ep-abc-pooler.us-west-2.aws.neon.tech/machreach?sslmode=require",
        )


def test_a_target_with_tables_is_refused_unless_forced(monkeypatch):
    monkeypatch.setattr(script, "list_tables", lambda url: ["clients"])
    with pytest.raises(script.MigrationError, match="already has tables"):
        script.check_preconditions("postgresql://a/b", "postgresql://a/c")
    script.check_preconditions("postgresql://a/b", "postgresql://a/c", force=True)


def test_row_count_comparison_flags_every_difference_and_tolerates_the_dropped_retired_table():
    source = {"clients": 3, "async_jobs": 7, "student_boosts": 0, "webhook_events": 0}
    assert script.compare_counts(source, {"clients": 3, "async_jobs": 7, "student_boosts": None, "webhook_events": 0}) == []
    problems = script.compare_counts(source, {"clients": 2, "async_jobs": 7, "webhook_events": None})
    assert problems == [
        "clients: source=3 target=2",
        "webhook_events: source=0 target=None",
    ]
    # A retired table that still had rows must not vanish silently.
    assert script.compare_counts({"student_boosts": 1}, {"student_boosts": None}) == [
        "student_boosts: source=1 target=None"
    ]


def test_main_reports_a_precondition_failure_without_touching_pg_dump(monkeypatch, capsys):
    monkeypatch.setattr(script, "dump", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dump")))
    assert script.main(["--source", "postgresql://a/b", "--target", "postgresql://a/b"]) == 1
    assert "same database" in capsys.readouterr().err


# --- The real copy ----------------------------------------------------------

def _with_database(url: str, name: str) -> str:
    parts = urlparse(url)
    return urlunparse(parts._replace(path=f"/{name}"))


def _pg_dump_major() -> int:
    out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"(\d+)", out)
    return int(match.group(1)) if match else 0


@pytest.fixture()
def two_databases():
    if not odb._USE_PG:
        pytest.skip("needs Postgres")
    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        pytest.skip("needs pg_dump and pg_restore on PATH")
    import psycopg2

    admin = psycopg2.connect(odb.DATABASE_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SHOW server_version_num")
        server_major = int(cur.fetchone()[0]) // 10000
        if _pg_dump_major() < server_major:
            admin.close()
            pytest.skip(f"pg_dump {_pg_dump_major()} is older than server {server_major}")
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if not cur.fetchone()[0]:
            admin.close()
            pytest.skip("needs a superuser to create throwaway databases")
        for name in ("machreach_copy_source", "machreach_copy_target"):
            cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            cur.execute(f"CREATE DATABASE {name}")
    source = _with_database(odb.DATABASE_URL, "machreach_copy_source")
    target = _with_database(odb.DATABASE_URL, "machreach_copy_target")
    yield source, target
    with admin.cursor() as cur:
        for name in ("machreach_copy_source", "machreach_copy_target"):
            cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.close()


def test_the_script_copies_every_row_and_refuses_a_target_that_drifted(two_databases, tmp_path, capsys):
    import psycopg2

    source, target = two_databases
    env = dict(os.environ, DATABASE_URL=source)
    subprocess.run([sys.executable, str(REPO_ROOT / "migrate.py")], env=env, check=True, cwd=REPO_ROOT)
    with psycopg2.connect(source) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (name, email, password, account_type) VALUES (%s, %s, %s, 'student')",
            ("Copied Student", "copied@example.com", "x"),
        )
        cur.execute("SELECT COUNT(*) FROM clients")
        assert cur.fetchone()[0] == 1

    dump_file = tmp_path / "copy.dump"
    assert script.main(["--source", source, "--target", target, "--dump-file", str(dump_file)]) == 0
    out = capsys.readouterr().out
    assert "verified: every source table matches the target" in out
    assert dump_file.is_file()

    with psycopg2.connect(target) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM clients")
        assert cur.fetchall() == [("Copied Student",)]
        cur.execute("SELECT version FROM schema_metadata WHERE component = 'core'")
        assert cur.fetchone()[0] == odb.SCHEMA_VERSION

    # A second run must not restore over a populated target by accident.
    assert script.main(["--source", source, "--target", target, "--skip-dump", "--dump-file", str(dump_file)]) == 1
    assert "already has tables" in capsys.readouterr().err

    # Verification alone catches a target that drifted after the copy.
    with psycopg2.connect(target) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM clients")
    assert script.main(["--source", source, "--target", target, "--verify-only"]) == 2
    assert "clients: source=1 target=0" in capsys.readouterr().err
