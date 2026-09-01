"""Copy one Postgres database into another, empty one, and prove it.

    python scripts/copy_database.py --source "$OLD_URL" --target "$NEW_URL"

Written for the move off Render's Postgres: to Neon, or to the Postgres on
the VPS stack in deploy/. The target is any Postgres reachable from where
this runs.

What it does, in order, stopping at the first failure:

1. Refuses obvious mistakes: identical URLs, a target that already holds
   tables (unless --force), a Neon pooled endpoint as the target (the app's
   advisory lock and session settings need a direct connection).
2. pg_dump the source in custom format (--no-owner --no-privileges: Neon has
   different roles). The file is kept, so a retry can pass --dump-file and
   --skip-dump instead of dumping again.
3. pg_restore into the target.
4. Runs migrate.py against the target, exactly as a deploy would.
5. Counts the rows of every source table on both sides and prints them.
   Any difference is a non-zero exit; the cutover must not proceed on it.

Needs pg_dump/pg_restore of a major version at least as new as the source
server (Render's Postgres version is in its dashboard; Neon runs 16 or 17).
Run it from a laptop with both installed, not from a Render shell: the dump
should never sit on a production instance's disk.

The runbooks, including the cutover order and the rollback, are
docs/VPS_DEPLOY.md (own server) and docs/NEON_MIGRATION.md (Render + Neon).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Tables whose row counts legitimately differ after a migrate.py run: the
# worker heartbeat and schedule rows are rewritten by whatever touches the
# target, and a retired empty table is dropped.
_COUNT_EXEMPT = {"student_boosts"}


class MigrationError(RuntimeError):
    """A precondition or verification failed; the cutover must not proceed."""


def _database_name(url: str) -> str:
    return urlparse(url).path.lstrip("/") or "(unnamed)"


def check_preconditions(source: str, target: str, *, force: bool = False) -> None:
    if not source or not target:
        raise MigrationError("both --source and --target are required")
    if source == target:
        raise MigrationError("source and target are the same database")
    host = (urlparse(target).hostname or "").lower()
    if "-pooler." in host:
        raise MigrationError(
            "target is a Neon pooled endpoint; use the direct connection string "
            "(the hostname without '-pooler')"
        )
    if not force and list_tables(target):
        raise MigrationError(
            f"target database {_database_name(target)!r} already has tables; "
            "restore into an empty database, or pass --force to restore over it"
        )


def _connect(url: str):
    import psycopg2

    return psycopg2.connect(url)


def list_tables(url: str) -> list[str]:
    with _connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
            return [row[0] for row in cur.fetchall()]


def row_counts(url: str, tables: list[str]) -> dict[str, int | None]:
    """Exact counts per table; None for a table the database does not have."""
    present = set(list_tables(url))
    counts: dict[str, int | None] = {}
    with _connect(url) as conn:
        with conn.cursor() as cur:
            for table in tables:
                if table not in present:
                    counts[table] = None
                    continue
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[table] = int(cur.fetchone()[0])
    return counts


def compare_counts(source: dict[str, int | None], target: dict[str, int | None]) -> list[str]:
    """Return one line per table that does not match; empty means verified."""
    problems = []
    for table, expected in source.items():
        actual = target.get(table)
        if actual == expected:
            continue
        if table in _COUNT_EXEMPT and not expected and actual is None:
            continue  # migrate.py drops the retired table when it is empty
        problems.append(f"{table}: source={expected} target={actual}")
    return problems


def _run(cmd: list[str], *, env: dict | None = None) -> None:
    shown = " ".join(part if not part.startswith("postgres") else "<url>" for part in cmd)
    print(f"[migrate] $ {shown}", flush=True)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise MigrationError(f"{cmd[0]} exited with {result.returncode}")


def dump(source: str, dump_file: Path) -> None:
    if not shutil.which("pg_dump"):
        raise MigrationError("pg_dump is required and was not found on PATH")
    dump_file.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "pg_dump", "--format", "custom", "--no-owner", "--no-privileges",
        "--file", str(dump_file), source,
    ])


def restore(target: str, dump_file: Path) -> None:
    if not shutil.which("pg_restore"):
        raise MigrationError("pg_restore is required and was not found on PATH")
    if not dump_file.is_file():
        raise MigrationError(f"dump file does not exist: {dump_file}")
    _run([
        "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
        "--dbname", target, str(dump_file),
    ])


def run_migrations(target: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = target
    _run([sys.executable, str(REPO_ROOT / "migrate.py")], env=env)


def verify(source: str, target: str) -> list[str]:
    tables = list_tables(source)
    if not tables:
        raise MigrationError("source database has no tables; wrong URL?")
    expected = row_counts(source, tables)
    actual = row_counts(target, tables)
    width = max(len(t) for t in tables)
    print(f"\n{'table'.ljust(width)}  {'source':>10}  {'target':>10}")
    for table in tables:
        print(f"{table.ljust(width)}  {str(expected[table]):>10}  {str(actual[table]):>10}")
    return compare_counts(expected, actual)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default=os.environ.get("SOURCE_DATABASE_URL", ""))
    parser.add_argument("--target", default=os.environ.get("TARGET_DATABASE_URL", ""))
    parser.add_argument("--dump-file", type=Path, default=None,
                        help="where to write (or, with --skip-dump, read) the pg_dump file")
    parser.add_argument("--skip-dump", action="store_true", help="reuse an existing --dump-file")
    parser.add_argument("--verify-only", action="store_true",
                        help="only compare row counts between source and target")
    parser.add_argument("--force", action="store_true",
                        help="restore into a target that already has tables")
    args = parser.parse_args(argv)

    started = time.monotonic()
    try:
        if args.verify_only:
            problems = verify(args.source, args.target)
        else:
            check_preconditions(args.source, args.target, force=args.force)
            dump_file = args.dump_file or Path(
                f"machreach-{time.strftime('%Y%m%d-%H%M%S')}.dump"
            )
            if not args.skip_dump:
                dump(args.source, dump_file)
            restore(args.target, dump_file)
            run_migrations(args.target)
            problems = verify(args.source, args.target)
    except MigrationError as exc:
        print(f"\n[migrate] FAILED: {exc}", file=sys.stderr)
        return 1

    minutes = (time.monotonic() - started) / 60
    if problems:
        print("\n[migrate] row counts differ; do not cut over:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 2
    print(f"\n[migrate] verified: every source table matches the target ({minutes:.1f} min).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
