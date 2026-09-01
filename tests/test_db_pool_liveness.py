"""A pooled connection the server closed must never reach a request.

Neon suspends an idle compute after five minutes and closes every connection
it held. The pool in machreach_core.db would hand that dead socket to the next
get_db() and the request would fail with "SSL connection has been closed
unexpectedly" rather than run. So a connection that sat idle past
DB_IDLE_PING_SECONDS is pinged on checkout and replaced when the server is
gone, while a recently used one is handed over untouched.

Postgres only: the SQLite engine opens a fresh file handle per get_db().
"""
import time

import pytest

from machreach_core import db as odb

pytestmark = pytest.mark.skipif(not odb._USE_PG, reason="Postgres connection pool only")


def _mark_idle(conn, seconds: float) -> None:
    odb._LAST_RETURNED[id(conn)] = time.monotonic() - seconds


def _terminate_backend(pid: int) -> None:
    """Kill a server backend from outside the pool, like a Neon suspend does.

    Through the pool it would be handed the very connection under test and
    terminate itself.
    """
    admin = odb.psycopg2.connect(odb.DATABASE_URL)
    try:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
    finally:
        admin.close()


def test_a_connection_the_server_closed_is_replaced_before_use():
    with odb.get_db() as db:
        pid = odb._fetchval(db, "SELECT pg_backend_pid()")
        stale = db
    # The pool still holds `stale` and believes it is open; the server does not.
    _terminate_backend(pid)
    assert not stale.closed
    _mark_idle(stale, odb._IDLE_PING_SECONDS + 1)

    with odb.get_db() as db:
        assert odb._fetchval(db, "SELECT 1") == 1
        assert odb._fetchval(db, "SELECT pg_backend_pid()") != pid
    assert id(stale) not in odb._LAST_RETURNED


def test_a_recently_used_connection_is_not_pinged(monkeypatch):
    pings = []
    monkeypatch.setattr(odb, "_connection_is_live", lambda conn: pings.append(conn) or True)
    with odb.get_db() as db:
        odb._fetchval(db, "SELECT 1")
    with odb.get_db() as db:
        odb._fetchval(db, "SELECT 1")
    assert pings == []


def test_an_idle_connection_is_pinged_and_kept_when_it_answers(monkeypatch):
    pings = []
    real = odb._connection_is_live
    monkeypatch.setattr(odb, "_connection_is_live", lambda conn: pings.append(conn) or real(conn))
    with odb.get_db() as db:
        odb._fetchval(db, "SELECT 1")
        held = db
    _mark_idle(held, odb._IDLE_PING_SECONDS + 1)

    with odb.get_db() as db:
        assert odb._fetchval(db, "SELECT 1") == 1
        # The pinged connection is not left inside a transaction the ping opened.
        assert db.get_transaction_status() in (0, 2)  # idle or in the SELECT's txn only
    assert pings == [held]


def test_the_ping_reports_a_dead_socket_as_not_live():
    with odb.get_db() as db:
        pid = odb._fetchval(db, "SELECT pg_backend_pid()")
        held = db
    _terminate_backend(pid)
    assert odb._connection_is_live(held) is False
    # Put the pool back in a clean state for the tests that follow.
    _mark_idle(held, odb._IDLE_PING_SECONDS + 1)
    with odb.get_db() as db:
        assert odb._fetchval(db, "SELECT 1") == 1


def test_a_broken_connection_is_forgotten_on_checkin():
    with odb.get_db() as db:
        pid = odb._fetchval(db, "SELECT pg_backend_pid()")
        held = db
    _terminate_backend(pid)
    _mark_idle(held, 0)  # looks fresh, so no ping: the failure surfaces mid-request
    with pytest.raises(odb.psycopg2.OperationalError):
        with odb.get_db() as db:
            odb._fetchval(db, "SELECT 1")
    assert id(held) not in odb._LAST_RETURNED
    with odb.get_db() as db:
        assert odb._fetchval(db, "SELECT 1") == 1
