"""What has to hold before DATABASE_URL may point at serverless Postgres.

Neon differs from Render's Postgres in two ways this codebase actually cares
about, and both fail quietly rather than loudly, which is why they are pinned
here instead of being left to the deploy to discover:

  * the connection string Neon offers first is a **transaction pooler**, and
    the migration lock is a session lock — through a pooler it protects
    nothing and the two services can migrate concurrently;
  * an idle compute is suspended, so the first connection after a quiet spell
    can be refused while the wake is still in progress, and a single retry is
    the difference between a served request and a 500.
"""
import pytest

import migrate
from machreach_core import db as coredb

_ON_POSTGRES = coredb._USE_PG
_needs_pg = pytest.mark.skipif(
    not _ON_POSTGRES,
    reason="the pool, and so _checkout, only exists on the Postgres engine",
)


# ---------------------------------------------------------------------------
# The pooled endpoint must not be used for migrations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech",
    "ep-x-pooler.eu-central-1.aws.neon.tech",
])
def test_migrations_refuse_a_transaction_pooler(monkeypatch, host):
    """Neon shows the pooled string first, so this is the URL people paste."""
    monkeypatch.setattr(
        migrate, "DATABASE_URL", f"postgresql://u:p@{host}/machreach?sslmode=require"
    )
    with pytest.raises(SystemExit) as excinfo:
        migrate._refuse_transaction_pooler()
    message = str(excinfo.value)
    # The error has to say what to do, not just that something is wrong.
    assert "-pooler" in message or "pooler" in message
    assert "direct endpoint" in message


@pytest.mark.parametrize("url", [
    "postgresql://u:p@ep-cool-darkness-123456.us-east-2.aws.neon.tech/machreach?sslmode=require",
    "postgresql://u:p@dpg-abc123-a.oregon-postgres.render.com/machreach",
    "postgresql://u:p@localhost:5432/machreach",
    "",  # SQLite locally: there is no URL to inspect
])
def test_migrations_accept_a_direct_endpoint(monkeypatch, url):
    monkeypatch.setattr(migrate, "DATABASE_URL", url)
    migrate._refuse_transaction_pooler()  # must not raise


def test_the_guard_runs_before_anything_touches_the_database(monkeypatch):
    """A refusal after init_db() had started would be a refusal too late."""
    order = []

    def _guard():
        order.append("guard")

    def _open_db():
        order.append("get_db")
        raise RuntimeError("far enough — the guard already had its turn")

    monkeypatch.setattr(migrate, "_refuse_transaction_pooler", _guard)
    monkeypatch.setattr(migrate, "get_db", _open_db)

    with pytest.raises(RuntimeError):
        migrate.migrate()
    assert order == ["guard", "get_db"]


# ---------------------------------------------------------------------------
# A suspended compute waking up must not surface as an error
# ---------------------------------------------------------------------------

class _FakePool:
    """Stands in for the psycopg2 pool; _checkout only needs these two calls."""

    def __init__(self, script):
        # Each entry is either an exception to raise or a connection to hand back.
        self.script = list(script)
        self.returned = []

    def getconn(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


class _Conn:
    def __init__(self, closed=0):
        self.closed = closed


@_needs_pg
def test_a_cold_start_is_retried_rather_than_raised(monkeypatch):
    """Neon refuses the first connect while the compute is still waking."""
    import psycopg2

    monkeypatch.setattr(coredb.time, "sleep", lambda _s: None)
    live = _Conn()
    pool = _FakePool([
        psycopg2.OperationalError("connection refused: compute is starting"),
        live,
    ])

    assert coredb._checkout(pool) is live


@_needs_pg
def test_a_database_that_is_really_down_still_fails(monkeypatch):
    """The retry is a bounded transient absorber, not an infinite wait."""
    import psycopg2

    slept = []
    monkeypatch.setattr(coredb.time, "sleep", slept.append)
    boom = psycopg2.OperationalError("could not connect to server")
    pool = _FakePool([boom] * coredb._CONNECT_ATTEMPTS)

    with pytest.raises(psycopg2.OperationalError):
        coredb._checkout(pool)
    # One sleep per failed attempt, and the waits grow rather than hammering.
    assert len(slept) == coredb._CONNECT_ATTEMPTS
    assert slept == sorted(slept)
    # Well inside the five seconds Render allows /health before killing us.
    assert sum(slept) < 5


@_needs_pg
def test_a_connection_the_server_already_closed_is_discarded():
    """Keepalives can hand back a dead socket; it must not reach a caller."""
    dead, live = _Conn(closed=1), _Conn()
    pool = _FakePool([dead, live])

    assert coredb._checkout(pool) is live
    assert pool.returned == [(dead, True)]


@_needs_pg
def test_checkout_never_raises_none_when_every_connection_was_dead():
    """The give-up path had no exception to re-raise if nothing ever threw."""
    import psycopg2

    pool = _FakePool([_Conn(closed=1) for _ in range(coredb._CONNECT_ATTEMPTS)])
    with pytest.raises(psycopg2.OperationalError):
        coredb._checkout(pool)


@_needs_pg
def test_the_pool_bounds_how_long_a_connect_may_hang():
    """An unbounded connect turns one slow database into a dead service."""
    assert coredb._CONNECT_TIMEOUT > 0
    assert coredb._CONNECT_TIMEOUT <= 30


# ---------------------------------------------------------------------------
# The migration script corrects a pooled target instead of arguing about it
# ---------------------------------------------------------------------------

def _unpool(dsn):
    """Import lazily: scripts/ is not a package on the default path."""
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "migrate_to_neon.py"
    spec = importlib.util.spec_from_file_location("_mig2neon", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._unpool(dsn)


def test_a_pooled_target_is_rewritten_to_the_direct_endpoint():
    """The two strings differ by six characters nobody notices when pasting."""
    fixed, note = _unpool(
        "postgresql://u:pw@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech"
        "/machreach?sslmode=require"
    )
    assert fixed == (
        "postgresql://u:pw@ep-cool-darkness-123456.us-east-2.aws.neon.tech"
        "/machreach?sslmode=require"
    )
    # Silently fixing it would leave them pasting the same wrong string into
    # DATABASE_URL at the switch, where migrate.py refuses outright.
    assert "pooled endpoint" in note
    assert "DATABASE_URL" in note


def test_the_credentials_survive_the_rewrite():
    """A password containing the host's own characters must not be mangled."""
    fixed, _ = _unpool(
        "postgresql://neondb_owner:np_x-pooler.abc@ep-a-pooler.eu-central-1.aws.neon.tech/db"
    )
    assert fixed.startswith("postgresql://neondb_owner:np_x-pooler.abc@")
    assert "@ep-a.eu-central-1.aws.neon.tech/db" in fixed


def test_a_direct_target_is_left_exactly_as_it_was():
    dsn = "postgresql://u:pw@ep-cool-darkness-123456.us-east-2.aws.neon.tech/machreach"
    fixed, note = _unpool(dsn)
    assert fixed == dsn
    assert note == ""


def test_a_password_that_looks_like_the_host_is_not_rewritten():
    """The pathological case the userinfo split exists for."""
    host = "ep-a-pooler.eu-central-1.aws.neon.tech"
    fixed, _ = _unpool(f"postgresql://owner:{host}@{host}/db?sslmode=require")
    # The password keeps its '-pooler'; only the host loses it.
    assert fixed == (
        f"postgresql://owner:{host}@ep-a.eu-central-1.aws.neon.tech/db?sslmode=require"
    )
