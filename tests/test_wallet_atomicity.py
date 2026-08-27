"""Concurrent wallet purchases must never overspend or bypass caps."""

from concurrent.futures import ThreadPoolExecutor

from machreach_core.db import get_db, _exec
from student import db as sdb


def test_parallel_freeze_purchases_spend_one_available_balance(make_user):
    client_id = make_user("Atomic Wallet User")
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_wallet SET coins = %s, streak_freezes = 0 "
            "WHERE client_id = %s",
            (sdb.STREAK_FREEZE_PRICE, client_id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sdb.buy_streak_freeze(client_id), range(2)))

    assert sum(bool(result.get("ok")) for result in results) == 1
    wallet = sdb.get_wallet(client_id)
    assert wallet["coins"] == 0
    assert wallet["streak_freezes"] == 1


def test_parallel_banner_purchases_charge_and_unlock_once(make_user):
    client_id = make_user("Atomic Banner User")
    banner_key = "candy"
    price = sdb.BANNERS[banner_key]["price_coins"]
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_wallet SET coins = %s, unlocked_banners = %s "
            "WHERE client_id = %s",
            (price, '["default"]', client_id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sdb.buy_banner(client_id, banner_key), range(2)))

    assert sum(bool(result.get("ok")) for result in results) == 1
    wallet = sdb.get_wallet(client_id)
    assert wallet["coins"] == 0
    assert wallet["unlocked_banners"].count(banner_key) == 1


def test_parallel_flag_purchases_charge_and_unlock_once(make_user):
    client_id = make_user("Atomic Flag User")
    flag_key = "void_blue"
    price = sdb.FLAGS[flag_key]["price_coins"]
    sdb.award_xp(client_id, "test", sdb.FLAGS[flag_key]["xp_required"])
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET coins = %s WHERE client_id = %s", (price, client_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sdb.buy_flag(client_id, flag_key), range(2)))

    assert sum(bool(result.get("ok")) for result in results) == 1
    assert sdb.get_wallet(client_id)["coins"] == 0
    assert sdb.get_flag_state(client_id)["unlocked_flags"].count(flag_key) == 1


def test_parallel_bundle_purchases_charge_once(make_user):
    client_id = make_user("Atomic Bundle User")
    bundle_key = "ocean"
    price = sdb.bundle_price(bundle_key)
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET coins = %s WHERE client_id = %s", (price, client_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sdb.buy_bundle(client_id, bundle_key), range(2)))

    assert sum(bool(result.get("ok")) for result in results) == 1
    wallet = sdb.get_wallet(client_id)
    state = sdb.get_flag_state(client_id)
    assert wallet["coins"] == 0
    assert wallet["unlocked_banners"].count("ocean") == 1
    assert state["unlocked_flags"].count("void_blue") == 1


def test_parallel_coin_rewards_repay_refund_debt_once(make_user):
    client_id = make_user("Atomic Refund Debt")
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_wallet SET coins = 0, coin_debt = 200 WHERE client_id = %s",
            (client_id,),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: sdb.add_coins(client_id, 150, "parallel reward"), range(2)))

    wallet = sdb.get_wallet(client_id)
    assert wallet["coin_debt"] == 0
    assert wallet["coins"] == 100


def test_the_wallet_grant_never_opens_a_second_connection(make_user, monkeypatch):
    """One student's login hung forever: get_wallet's weekly freeze grant held
    the reward-state row uncommitted on connection A, then the plus-cap check
    called get_tier, which opened connection B and upserted the same row — a
    one-thread deadlock Postgres cannot detect, because half the cycle waits
    in Python, not in the database. (On SQLite the same shape degraded
    silently: connection B hit busy_timeout and get_tier fell back to "free",
    which is why no test ever caught it.) Everything must run on the caller's
    connection."""
    import student.subscription as ssub

    client_id = make_user("Deadlock Shape User")
    ssub.set_tier(client_id, "plus")  # only plus tier reaches the upsert

    # From here on, any pooled checkout inside the subscription module means
    # the deadlock shape is back.
    checkouts = []
    real_get_db = ssub.get_db

    def _recording_get_db():
        checkouts.append(True)
        return real_get_db()

    monkeypatch.setattr(ssub, "get_db", _recording_get_db)

    wallet = sdb.get_wallet(client_id)

    assert checkouts == [], (
        "get_tier opened its own connection inside get_wallet's transaction"
    )
    # And the grant still landed on the borrowed connection.
    assert wallet["streak_freezes"] >= 1
