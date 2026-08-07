"""Wallet mutations remain authenticated, atomic, and ownership-aware."""

from machreach_core.db import _exec, get_db
from student import db as sdb


def _login(client, client_id: int, name: str = "Wallet Student") -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0


def test_wallet_routes_require_login(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    paths = (
        "buy-freeze",
        "buy-freeze-bundle",
        "buy-banner",
        "buy-coin-pack",
        "set-banner",
        "buy-flag",
        "set-flag",
        "set-badge",
        "set-cosmetic",
        "buy-bundle",
        "use-freeze",
    )
    for path in paths:
        assert client.post(f"/api/student/wallet/{path}", json={}).status_code == 401


def test_wallet_purchase_and_equipment_lifecycle(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Wallet Lifecycle")
    _login(client, client_id, "Wallet Lifecycle")

    # Weekly grant initializes the wallet with one free freeze. Coins and XP are
    # credited through the same domain services used by focus/reward flows.
    assert sdb.get_wallet(client_id)["streak_freezes"] == 1
    assert sdb.add_coins(client_id, 5000, "test reward") == 5000
    sdb.award_xp(client_id, "test", 5000, "wallet eligibility")

    assert client.post(
        "/api/student/wallet/buy-banner", json={"banner_key": "unknown"}
    ).get_json()["ok"] is False
    banner = client.post(
        "/api/student/wallet/buy-banner", json={"banner_key": "ocean"}
    ).get_json()
    assert banner["ok"] is True
    assert client.post(
        "/api/student/wallet/buy-banner", json={"banner_key": "ocean"}
    ).get_json()["error"] == "Already owned."
    assert client.post(
        "/api/student/wallet/set-banner", json={"banner_key": "ocean"}
    ).get_json() == {"ok": True, "selected_banner": "ocean"}
    assert client.post(
        "/api/student/wallet/set-banner", json={"banner_key": "gold"}
    ).get_json()["ok"] is False

    assert client.post(
        "/api/student/wallet/buy-flag", json={"flag_key": "none"}
    ).get_json()["ok"] is False
    flag = client.post(
        "/api/student/wallet/buy-flag", json={"flag_key": "void_blue"}
    ).get_json()
    assert flag["ok"] is True
    assert client.post(
        "/api/student/wallet/set-flag", json={"flag_key": "void_blue"}
    ).get_json() == {"ok": True, "selected_flag": "void_blue"}
    assert client.post(
        "/api/student/wallet/set-flag", json={"flag_key": "unknown"}
    ).get_json()["ok"] is False

    sdb.earn_badge(client_id, "first_course")
    badge = client.post(
        "/api/student/wallet/set-badge",
        json={"badge_key": "first_course", "side": "left"},
    ).get_json()
    assert badge["ok"] is True
    assert badge["equipped_badge_left"] == "first_course"
    assert client.post(
        "/api/student/wallet/set-badge",
        json={"badge_key": "quiz_50", "side": "right"},
    ).get_json()["ok"] is False

    cosmetic = client.post(
        "/api/student/wallet/set-cosmetic",
        json={"kind": "focus_theme", "key": "default"},
    ).get_json()
    assert cosmetic == {
        "ok": True,
        "kind": "focus_theme",
        "key": "default",
    }
    assert client.post(
        "/api/student/wallet/set-cosmetic",
        json={"kind": "unknown", "key": "default"},
    ).get_json()["ok"] is False

    bundle = client.post(
        "/api/student/wallet/buy-bundle", json={"bundle_key": "forest"}
    ).get_json()
    assert bundle["ok"] is True
    assert bundle["banner"] == "forest"
    assert bundle["flag"] == "void_green"
    assert client.post(
        "/api/student/wallet/buy-bundle", json={"bundle_key": "forest"}
    ).get_json()["error"] == "Already owned."

    freeze = client.post("/api/student/wallet/buy-freeze").get_json()
    assert freeze["ok"] is True
    assert client.post("/api/student/wallet/use-freeze").get_json()["ok"] is True
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_wallet SET streak_freezes = 0 WHERE client_id = %s",
            (client_id,),
        )
    bundle_freezes = client.post(
        "/api/student/wallet/buy-freeze-bundle"
    ).get_json()
    assert bundle_freezes["ok"] is True
    capped = client.post(
        "/api/student/wallet/buy-freeze-bundle"
    ).get_json()
    assert capped["ok"] is False

    assert client.post(
        "/api/student/wallet/buy-coin-pack", json={"pack_key": "unknown"}
    ).status_code == 400
    # Provider credentials are deliberately absent in the test environment;
    # checkout must fail closed without crediting the wallet.
    from machreach_core import lemonsqueezy

    monkeypatch.setattr(
        lemonsqueezy, "is_configured", lambda test_mode=False: False
    )
    before = sdb.get_wallet(client_id)["coins"]
    assert client.post(
        "/api/student/wallet/buy-coin-pack", json={"pack_key": "small"}
    ).status_code == 503
    assert sdb.get_wallet(client_id)["coins"] == before


def test_wallet_insufficient_funds_and_catalog_validation(make_user):
    client_id = make_user("Empty Wallet")
    assert sdb.bundle_price("unknown") == 0
    assert sdb.buy_streak_freeze(client_id, 1)["ok"] is False
    assert sdb.buy_banner(client_id, "unknown")["ok"] is False
    assert sdb.buy_flag(client_id, "unknown")["ok"] is False
    assert sdb.buy_bundle(client_id, "unknown")["ok"] is False
    assert sdb.set_selected_flag(client_id, "unknown")["ok"] is False
    assert sdb.set_cosmetic(client_id, "unknown", "default")["ok"] is False
    assert sdb.credit_coin_pack(client_id, "unknown", "order-unknown")[
        "ok"
    ] is False

    # Idempotent order ledger: provider retry cannot double-credit.
    first = sdb.credit_coin_pack(client_id, "small", "order-123")
    second = sdb.credit_coin_pack(client_id, "small", "order-123")
    assert first["ok"] is True
    assert second["ok"] is True
    assert second["duplicate"] is True
    assert sdb.get_wallet(client_id)["coins"] == 250
