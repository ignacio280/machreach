"""Copy the production database from Render Postgres to Neon.

Run it from a place that can reach both databases on port 5432 — the Render
shell of either service is the obvious one, because it already has
DATABASE_URL in its environment and outbound internet to Neon. Set the Neon
string as NEON_DATABASE_URL in the Render dashboard (a form field, not a
shell paste) and the whole command is short enough to type by hand::

    python scripts/migrate_to_neon.py --check      # read-only, look before you leap
    python scripts/migrate_to_neon.py --run
    python scripts/migrate_to_neon.py --verify     # counts on both sides, any time

Either Neon connection string works as the target: the pooled one is rewritten
to its direct endpoint, with a note, rather than rejected.

Why this and not pg_dump | psql
-------------------------------
pg_dump has to match the server's major version and is not installed on every
host you might run this from; psycopg2 already is, because the app depends on
it. More importantly the schema is not something to dump and hope: this app
builds it from code, idempotently, in migrate.py. So the target schema is
created by running the app's own migrations against Neon, which is by
definition the schema the app expects — and then only the *rows* are copied.
A dump/restore would instead reproduce whatever historical drift the Render
database has accumulated.

What it does, in order
----------------------
1. Connects to both, refuses to continue if they are the same database.
2. Runs migrate.py against Neon, in a subprocess with DATABASE_URL pointed
   there, so the schema exists and matches this commit.
3. Discovers the tables from the *source* at runtime. Nothing here hardcodes a
   table list: there are fifty-odd of them spread over seven modules and a
   stale list would silently skip whichever one was added last.
4. Orders them so a table is copied after everything it references, worked out
   from the real foreign keys on the target. That is what keeps the copy inside
   ordinary permissions — no disabling triggers, no superuser.
5. Copies each table with COPY, naming the columns explicitly, so a difference
   in column *order* between the two schemas cannot silently shift data into
   the wrong columns.
6. Sets every id sequence past the largest id that was copied. Skipping this is
   the classic way to end up live on a new database that hands the next signup
   an id that already exists.
7. Counts every table on both sides and prints the comparison.

Safety
------
The target has to be a blank database, checked *before* the schema is built —
migrate.py seeds the reference tables (countries, universities, majors), so
after it runs nothing is empty and the check would be meaningless. --force
lifts that for a half-finished run of this script. Either way the target's
tables are emptied before the copy, because the goal is a database identical
to the source rather than merged with it; that TRUNCATE is the only
destructive statement in this file and it can only ever reach the *target*.
The source is opened read-only, so a failed run costs you the target, never
production.

This copies data, it does not switch traffic. Nothing here touches Render's
environment: after a successful run the app is still talking to the old
database until DATABASE_URL is repointed by hand.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root.
sys.path.insert(0, str(REPO_ROOT))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

# Rows spill to disk past this, so a big table cannot balloon a 256MB dyno.
_SPOOL_BYTES = 64 * 1024 * 1024

# Written by the app itself and rebuilt by migrate.py on the target, so copying
# the source's row would fight whatever this commit's migrations just wrote.
_SKIP_TABLES = frozenset({"schema_metadata"})


class MigrationError(RuntimeError):
    """Anything that should stop the migration with a readable message."""


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _require_ssl(dsn: str) -> str:
    """Neon refuses plaintext, and a URL copied from a form often omits it.

    Only ever applied to the target. The source is left exactly as given
    because on Render DATABASE_URL is an *internal* hostname, and rewriting a
    connection string that already works is a good way to break the one half
    of this that must not break. A local host is left alone too, so the script
    is testable against a plain development cluster.
    """
    if "sslmode=" in dsn:
        return dsn
    if "@localhost" in dsn or "@127.0.0.1" in dsn:
        return dsn
    return dsn + ("&" if "?" in dsn else "?") + "sslmode=require"


def _unpool(dsn: str) -> tuple[str, str]:
    """Rewrite a pooled Neon target to its direct endpoint.

    Neon's console shows the pooled string first, so it is the one that gets
    copied, and the two differ by exactly six characters in the hostname. The
    pooled one is wrong for everything this script does — it builds the schema
    by running migrate.py, which takes a session-level advisory lock a
    transaction pooler cannot hold, and migrate.py refuses to start behind one.

    Refusing here as well would just mean a round trip to fix a typo nobody
    can see. The direct endpoint is the same database, reached the same way,
    so this corrects it and says so. Returns the URL and a note to print, or
    an empty note when there was nothing to change.
    """
    parts = urlsplit(dsn)
    host = parts.hostname or ""
    if "-pooler." not in host:
        return dsn, ""
    fixed_host = host.replace("-pooler.", ".", 1)
    # Split at the *last* '@' and rewrite only the right-hand side. Replacing
    # across the whole netloc could match inside a password, and rebuilding it
    # from parts.username/parts.password would mean re-encoding two values
    # urlsplit already percent-decoded. Neither risk is worth taking with the
    # one string that has to keep working.
    userinfo, _, hostpart = parts.netloc.rpartition("@")
    hostpart = hostpart.replace(host, fixed_host, 1)
    netloc = f"{userinfo}@{hostpart}" if userinfo else hostpart
    return urlunsplit(parts._replace(netloc=netloc)), (
        f"  NOTE: the target was the pooled endpoint ({host}).\n"
        f"        Using the direct one instead: {fixed_host}\n"
        "        Set DATABASE_URL to the direct endpoint too when you switch —\n"
        "        migrate.py refuses to run behind a pooler, by design."
    )


def _connect(dsn: str, *, readonly: bool = False):
    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    if readonly:
        # Belt and braces: the source is never written to by this script, and
        # now it cannot be written to by a mistake in it either.
        conn.set_session(readonly=True)
    else:
        # The TRUNCATE below needs ACCESS EXCLUSIVE. If something else is
        # holding the table — a leftover session from an interrupted run, the
        # app already pointed here — waiting forever looks identical to a
        # crash. Thirty seconds and a readable error is the better failure.
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '30s'")
        conn.commit()
    return conn


def _identity(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_database() AS db, current_user AS usr, "
            "version() AS version, "
            "inet_server_addr()::text AS host, "
            "pg_size_pretty(pg_database_size(current_database())) AS size"
        )
        return dict(cur.fetchone())


def _same_database(src, dst) -> bool:
    """Guard against both URLs pointing at the same place.

    Identity is the cluster *and* the database name together. system_identifier
    alone is per-cluster, so it would also refuse a perfectly good copy between
    two databases inside one cluster; the database name alone would refuse two
    unrelated servers that both happen to call it "machreach". Both matching is
    what actually means "the same rows", however differently the two URLs are
    spelled — a pooled and a direct Neon endpoint included.
    """
    def ident(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT system_identifier, current_database() AS db FROM pg_control_system()"
            )
            row = cur.fetchone() or {}
            return (row.get("system_identifier"), row.get("db"))
    try:
        a, b = ident(src), ident(dst)
    except Exception:
        return False
    return a[0] is not None and a == b


# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------

def _tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        return [r["table_name"] for r in cur.fetchall() if r["table_name"] not in _SKIP_TABLES]


def _columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "AND is_generated = 'NEVER' "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [r["column_name"] for r in cur.fetchall()]


def _fk_edges(conn) -> list[tuple[str, str]]:
    """(child, parent) pairs for foreign keys inside the public schema."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname AS child, p.relname AS parent "
            "FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_class p ON p.oid = k.confrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE k.contype = 'f' AND n.nspname = 'public'"
        )
        return [(r["child"], r["parent"]) for r in cur.fetchall()]


def _copy_order(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Parents before children, so foreign keys hold during the copy.

    A self-reference is ignored — a row pointing at its own table is satisfied
    within the one COPY. A genuine cycle between two tables cannot be ordered,
    so the remainder is appended in name order and the FK failure, if there is
    one, is left to surface loudly rather than be papered over.
    """
    known = set(tables)
    pending: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in edges:
        if child in known and parent in known and child != parent:
            pending[child].add(parent)

    ordered: list[str] = []
    done: set[str] = set()
    while True:
        ready = sorted(t for t in tables if t not in done and not (pending[t] - done))
        if not ready:
            break
        ordered.extend(ready)
        done.update(ready)
    ordered.extend(sorted(t for t in tables if t not in done))
    return ordered


def _counts(conn, tables: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f'SELECT count(*) AS n FROM "{table}"')
            out[table] = int(cur.fetchone()["n"])
    return out


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------

def _copy_table(src, dst, table: str, columns: list[str]) -> int:
    """Stream one table across. Returns the number of rows written."""
    collist = ", ".join(f'"{c}"' for c in columns)
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES, mode="w+b") as buf:
        with src.cursor() as cur:
            cur.copy_expert(f'COPY "{table}" ({collist}) TO STDOUT', buf)
        buf.seek(0)
        with dst.cursor() as cur:
            cur.copy_expert(f'COPY "{table}" ({collist}) FROM STDIN', buf)
            return cur.rowcount


def _reset_sequences(dst) -> int:
    """Move every owned sequence past the largest value that was copied.

    Without this the target's sequences are still at 1 while the rows go up to
    whatever production reached, and the first insert after the switch collides
    on a primary key.
    """
    moved = 0
    with dst.cursor() as cur:
        cur.execute(
            "SELECT s.relname AS seq, t.relname AS tbl, a.attname AS col "
            "FROM pg_class s "
            "JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass "
            "JOIN pg_class t ON t.oid = d.refobjid "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
            "JOIN pg_namespace n ON n.oid = s.relnamespace "
            "WHERE s.relkind = 'S' AND n.nspname = 'public'"
        )
        owned = [dict(r) for r in cur.fetchall()]
        for row in owned:
            cur.execute(
                f'SELECT setval(%s, COALESCE((SELECT MAX("{row["col"]}") FROM "{row["tbl"]}"), 0) + 1, false)',
                (f'public."{row["seq"]}"',),
            )
            moved += 1
    return moved


def _build_target_schema(target_dsn: str, dst) -> None:
    """Run the app's own migrations against Neon, in a clean subprocess.

    In-process would not work: machreach_core.db reads DATABASE_URL at import
    time and binds its connection pool to it, so the schema would be built on
    whichever database was configured when this script started — the source.

    The rollback is not tidiness, it is the whole reason this takes ``dst``.
    Every SELECT above left this connection inside an open transaction holding
    an ACCESS SHARE lock on each table it read. migrate.py's ALTER TABLE wants
    ACCESS EXCLUSIVE, so it would queue behind locks held by *this* process,
    which is sitting here waiting for it: a deadlock that no timeout breaks
    because neither side is deadlocked in a way Postgres can detect. It hangs
    until someone kills it.
    """
    dst.rollback()
    env = dict(os.environ)
    env["DATABASE_URL"] = target_dsn
    env.pop("DATABASE_PATH", None)
    print("  running migrate.py against the target...", flush=True)
    result = subprocess.run(
        [sys.executable, "migrate.py"], cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True,
    )
    for line in (result.stdout or "").splitlines():
        print(f"    {line}", flush=True)
    if result.returncode != 0:
        raise MigrationError(
            "migrate.py failed against the target:\n" + (result.stderr or "").strip()
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _describe(label: str, conn) -> None:
    info = _identity(conn)
    version = str(info["version"]).split(" on ")[0]
    print(f"{label}:")
    print(f"  database: {info['db']}   user: {info['usr']}")
    print(f"  server:   {version}")
    print(f"  size:     {info['size']}")


def cmd_check(source_dsn: str, target_dsn: str) -> int:
    with _connect(source_dsn, readonly=True) as src:
        _describe("SOURCE (Render)", src)
        tables = _tables(src)
        src_counts = _counts(src, tables)
    total = sum(src_counts.values())
    print(f"  tables:   {len(tables)}   rows: {total:,}")

    print()
    try:
        with _connect(target_dsn) as dst:
            _describe("TARGET (Neon)", dst)
            dst_tables = _tables(dst)
            if dst_tables:
                dst_total = sum(_counts(dst, dst_tables).values())
                print(f"  tables:   {len(dst_tables)}   rows: {dst_total:,}")
                if dst_total:
                    print("  NOTE: the target already holds rows; --run needs --force.")
            else:
                print("  tables:   none yet (--run will build the schema)")
    except psycopg2.Error as exc:
        print(f"TARGET (Neon): cannot connect — {str(exc).strip()}")
        return 1

    print("\nNon-empty tables in the source:")
    for table in sorted(tables, key=lambda t: (-src_counts[t], t)):
        if src_counts[table]:
            print(f"  {src_counts[table]:>9,}  {table}")
    return 0


def cmd_verify(source_dsn: str, target_dsn: str) -> int:
    with _connect(source_dsn, readonly=True) as src:
        tables = _tables(src)
        src_counts = _counts(src, tables)
    with _connect(target_dsn) as dst:
        dst_tables = set(_tables(dst))
        dst_counts = _counts(dst, [t for t in tables if t in dst_tables])

    bad = []
    for table in tables:
        got = dst_counts.get(table)
        if got != src_counts[table]:
            bad.append((table, src_counts[table], got))

    print(f"{len(tables)} tables, {sum(src_counts.values()):,} source rows.")
    if not bad:
        print("Every table matches.")
        return 0
    print(f"\n{len(bad)} table(s) DO NOT match:")
    for table, want, got in bad:
        shown = "missing table" if got is None else f"{got:,}"
        print(f"  {table}: source {want:,} -> target {shown}")
    return 1


def cmd_run(source_dsn: str, target_dsn: str, force: bool) -> int:
    src = _connect(source_dsn, readonly=True)
    dst = _connect(target_dsn)
    try:
        if _same_database(src, dst):
            raise MigrationError(
                "the source and the target are the same database — check the two URLs"
            )
        _describe("SOURCE (Render)", src)
        print()
        _describe("TARGET (Neon)", dst)

        # Asked before the schema is built, because migrate.py seeds the
        # reference tables — countries, universities, majors — and afterwards
        # the target is never empty. Checking then would demand --force on
        # every first run, which is exactly how --force stops meaning anything.
        # Rows that are here *now* are rows this script did not put here.
        prior = _counts(dst, _tables(dst))
        occupied = {t: n for t, n in prior.items() if n}
        if occupied and not force:
            listed = ", ".join(f"{t} ({n:,})" for t, n in sorted(occupied.items()))
            raise MigrationError(
                f"the target is not a blank database — it already holds rows in "
                f"{len(occupied)} table(s): {listed}\n"
                "If that is a half-finished run of this script, --force will empty "
                "those tables and refill them. If it is somebody's data, use a "
                "different Neon database."
            )

        print("\nBuilding the schema on the target")
        _build_target_schema(target_dsn, dst)

        tables = _tables(src)
        missing = [t for t in tables if t not in set(_tables(dst))]
        if missing:
            raise MigrationError(
                "the target is missing tables the source has, so migrate.py did not "
                "build the schema this data needs: " + ", ".join(sorted(missing))
            )

        order = _copy_order(tables, _fk_edges(dst))
        src_counts = _counts(src, tables)

        # Always, not only under --force: migrate.py has just seeded the
        # reference tables, and the source's own copy of those rows is the one
        # that must win. Left in place they would collide with the incoming ids
        # — and the point is a target identical to the source, not merged with it.
        print(f"\nEmptying the {len(order)} target tables before the copy")
        with dst.cursor() as cur:
            # One statement so mutual foreign keys cannot block it.
            quoted = ", ".join(f'"{t}"' for t in order)
            cur.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")

        print(f"\nCopying {len(order)} tables")
        copied = 0
        for i, table in enumerate(order, 1):
            src_cols = _columns(src, table)
            dst_cols = set(_columns(dst, table))
            gone = [c for c in src_cols if c not in dst_cols]
            if gone:
                raise MigrationError(
                    f"{table}: the target has no column(s) {', '.join(gone)} — the "
                    "target schema is older than the data being copied"
                )
            want = src_counts[table]
            if not want:
                continue
            wrote = _copy_table(src, dst, table, src_cols)
            copied += wrote
            flag = "" if wrote == want else f"  !! expected {want:,}"
            print(f"  [{i:>2}/{len(order)}] {table:<38} {wrote:>9,}{flag}", flush=True)

        print("\nResetting sequences")
        print(f"  {_reset_sequences(dst)} sequence(s) moved past the copied ids")

        dst.commit()
        print(f"\nCommitted. {copied:,} rows copied.")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()

    print("\nVerifying")
    rc = cmd_verify(source_dsn, target_dsn)
    if rc == 0:
        print(
            "\nThe data is on Neon. Nothing is switched over yet: the app is still\n"
            "reading the old database until DATABASE_URL is repointed in Render."
        )
    return rc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy the production database from Render Postgres to Neon.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="read-only: connect to both and report what is there")
    mode.add_argument("--run", action="store_true", help="do the copy")
    mode.add_argument("--verify", action="store_true",
                      help="compare row counts between the two databases")
    parser.add_argument("--source", default=os.getenv("DATABASE_URL", ""),
                        help="source URL (default: $DATABASE_URL)")
    parser.add_argument("--target", default=os.getenv("NEON_DATABASE_URL", ""),
                        help="target URL (default: $NEON_DATABASE_URL)")
    parser.add_argument("--force", action="store_true",
                        help="with --run: TRUNCATE non-empty target tables first")
    args = parser.parse_args(argv)

    if not args.source:
        parser.error("no source: set DATABASE_URL or pass --source")
    if not args.target:
        parser.error("no target: set NEON_DATABASE_URL or pass --target")
    # Normalized once, here, so every path below shares one target string.
    args.target, note = _unpool(_require_ssl(args.target))
    if note:
        print(note + "\n")

    try:
        if args.check:
            return cmd_check(args.source, args.target)
        if args.verify:
            return cmd_verify(args.source, args.target)
        return cmd_run(args.source, args.target, args.force)
    except MigrationError as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    except psycopg2.Error as exc:
        print(f"\nDatabase error: {str(exc).strip()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
