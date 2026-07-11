"""The retired Outreach product must not reappear in the Uni runtime."""

import importlib.util
from pathlib import Path

from outreach import db as odb
from outreach import lemonsqueezy as ls
import worker


def test_worker_has_no_legacy_campaign_or_mail_jobs():
    source = Path(worker.__file__).read_text(encoding="utf-8")

    assert "campaign_send" not in source
    assert "sync_mail_hub" not in source
    assert "send_scheduled" not in source
    assert "send_batch" not in source


def test_legacy_product_routes_are_not_registered(flask_app):
    rules = {rule.rule for rule in flask_app.url_map.iter_rules()}

    assert "/campaigns" not in rules
    assert "/contacts" not in rules
    assert "/mail" not in rules
    assert "/inbox" not in rules


def _table_names() -> set[str]:
    with odb.get_db() as db:
        if odb._USE_PG:
            rows = odb._fetchall(
                db,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'",
            )
        else:
            rows = odb._fetchall(
                db,
                "SELECT name AS table_name FROM sqlite_master WHERE type = 'table'",
            )
    return {row["table_name"] for row in rows}


def test_fresh_schema_has_no_legacy_product_tables():
    assert not set(odb._RETIRED_PRODUCT_TABLES) & _table_names()


def test_removed_product_modules_are_not_importable():
    assert importlib.util.find_spec("outreach.mail_hub") is None
    assert importlib.util.find_spec("outreach.reply_checker") is None
    assert importlib.util.find_spec("outreach.sender") is None
    assert importlib.util.find_spec("outreach.ai") is None
    assert importlib.util.find_spec("professional.routes") is None


def test_migration_archives_subscription_ids_before_dropping_legacy_tables():
    with odb.get_db() as db:
        odb._exec(
            db,
            "CREATE TABLE subscriptions ("
            "id INTEGER PRIMARY KEY, client_id INTEGER, stripe_subscription_id TEXT)",
        )
        odb._exec(
            db,
            "INSERT INTO subscriptions (id, client_id, stripe_subscription_id) "
            "VALUES (%s, %s, %s)",
            (1, 99, "legacy-subscription-to-cancel"),
        )

    odb._archive_and_drop_retired_product_tables()

    assert "subscriptions" not in _table_names()
    with odb.get_db() as db:
        row = odb._fetchone(
            db,
            "SELECT status, attempts FROM retired_billing_cancellations "
            "WHERE subscription_id = %s",
            ("legacy-subscription-to-cancel",),
        )
    assert row == {"status": "pending", "attempts": 0}


def test_worker_cancels_archived_provider_subscription(monkeypatch):
    with odb.get_db() as db:
        odb._exec(
            db,
            "INSERT INTO retired_billing_cancellations "
            "(subscription_id, status, attempts) VALUES (%s, 'pending', 0) "
            "ON CONFLICT(subscription_id) DO UPDATE SET status = 'pending', attempts = 0",
            ("legacy-worker-cancel",),
        )
    calls = []
    monkeypatch.setattr(ls, "cancel_subscription", lambda sid: calls.append(sid) or True)

    worker.cancel_retired_product_subscriptions()

    assert "legacy-worker-cancel" in calls
    with odb.get_db() as db:
        row = odb._fetchone(
            db,
            "SELECT status, attempts FROM retired_billing_cancellations "
            "WHERE subscription_id = %s",
            ("legacy-worker-cancel",),
        )
    assert row == {"status": "cancelled", "attempts": 1}
