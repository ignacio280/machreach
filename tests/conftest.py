"""Test setup: isolate everything in a throwaway SQLite DB and configure a
non-production environment BEFORE importing the app (config reads env at import).
"""
import os
import sys
import uuid
import tempfile

import pytest

# ── Configure an isolated env before importing the app ──────────────────────
_TEST_DATABASE_URL = os.environ.get("MACHREACH_TEST_DATABASE_URL", "").strip()
if _TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
else:
    os.environ.pop("DATABASE_URL", None)   # force the SQLite engine locally
os.environ.pop("RENDER", None)         # not production: cookies work over http, no ProxyFix
_TMPDIR = tempfile.mkdtemp(prefix="machreach_test_")
os.environ["DATABASE_PATH"] = os.path.join(_TMPDIR, "test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key")
os.environ["LEMON_SQUEEZY_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["LEMON_SQUEEZY_STORE_ID"] = "test-store"
os.environ["LS_PRODUCT_STUDENT_PLUS"] = "test-plus-product"
os.environ["LS_VARIANT_STUDENT_PLUS"] = "test-plus-variant"
os.environ["LS_PRODUCT_COIN_SMALL"] = "test-coin-small-product"
os.environ["LS_VARIANT_COIN_SMALL"] = "test-coin-small-variant"

# Repo root on the path so `import app` works regardless of pytest's CWD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod                                   # noqa: E402
from machreach_core.db import create_client   # noqa: E402

WEBHOOK_SECRET = os.environ["LEMON_SQUEEZY_WEBHOOK_SECRET"]


@pytest.fixture(scope="session")
def flask_app():
    return appmod.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def make_user():
    """Factory creating throwaway student accounts (unique email each call)."""
    def _make(name="Test User", email=None):
        email = email or f"test_{uuid.uuid4().hex[:12]}@example.com"
        return create_client(name, email, "x", "", "student")
    return _make
