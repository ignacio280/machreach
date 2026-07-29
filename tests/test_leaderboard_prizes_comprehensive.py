from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor

import app as appmod
import pytest
from machreach_core.db import _exec, _fetchone, _fetchval, get_db
from student import db as sdb
from student import leaderboard_prizes as prizes


def test_prize_amounts_period_keys_and_windows():
    prizes.init_prize_tables()
    monday = date(2026, 7, 20)
    first = date(2026, 8, 1)

    assert prizes._coins_for("week", "global", 1) == 250
    assert prizes._coins_for("month", "major", 5) == 10
    assert prizes._coins_for("month", "bad", 1) == 0
    assert prizes._period_key("week", monday) == "2026-W29"
    assert prizes._period_key("month", first) == "2026-07"
    assert prizes._period_window("week", "2026-W29") == ("2026-07-13 00:00:00", "2026-07-20 00:00:00")
    assert prizes._period_window("month", "2026-12") == ("2026-12-01 00:00:00", "2027-01-01 00:00:00")
    assert prizes._xp_period_window("month", "2026-04", postgres=True) == (
        "2026-04-01T03:00:00+00:00", "2026-05-01T04:00:00+00:00"
    )


def test_award_winners_credits_wallet_and_records_prize(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Prize Winner")
    monkeypatch.setattr(prizes, "_top5_per_bucket", lambda scope, kind, key, db=None: (
        [{"client_id": client_id, "name": "Prize Winner", "scope_value": None, "rank": 1, "xp": 500}]
        if scope == "global" else []
    ))

    claim_token = prizes._claim_run("week", "2099-W02")
    assert claim_token
    summary = prizes._award_winners("week", "2099-W02", claim_token)

    assert summary["global"][0]["coins"] == 250
    assert sdb.get_wallet(client_id)["coins"] >= 250
    with get_db() as db:
        assert _fetchval(db, "SELECT COUNT(*) FROM student_lb_prize WHERE client_id = %s", (client_id,)) == 1


def test_top_five_queries_are_deterministic_for_every_partition(make_user):
    first_id = make_user("Partition First")
    second_id = make_user("Partition Second")
    with get_db() as db:
        university = _fetchone(
            db, "SELECT id FROM universities WHERE country_iso = %s ORDER BY id LIMIT 1",
            ("CL",),
        )
        major = _fetchone(
            db,
            "SELECT id FROM majors WHERE university_id = %s ORDER BY id LIMIT 1",
            (university["id"],),
        )
        for client_id in (first_id, second_id):
            _exec(
                db,
                "UPDATE clients SET country_iso = %s, university_id = %s, major_id = %s "
                "WHERE id = %s",
                ("CL", university["id"], major["id"], client_id),
            )
    first_xp = sdb.award_xp(first_id, "partition", 100, "partition test")
    second_xp = sdb.award_xp(second_id, "partition", 100, "partition test")
    start, _end = prizes._xp_period_window("week", "2099-W10")
    with get_db() as db:
        for xp_id in (first_xp, second_xp):
            _exec(
                db,
                "UPDATE student_xp SET occurred_at_utc = %s WHERE id = %s",
                (start, xp_id),
            )

    for scope in ("global", "country", "university", "major"):
        rows = prizes._top5_per_bucket(scope, "week", "2099-W10")
        assert [row["client_id"] for row in rows] == [first_id, second_id]
        assert [row["rank"] for row in rows] == [1, 2]
        if scope == "global":
            assert rows[0]["scope_value"] is None
        else:
            assert rows[0]["scope_value"] is not None
        rank = prizes._user_rank_in_scope(
            first_id, scope, "week", "2099-W10"
        )
        assert rank == {"rank": 1, "total_in_bucket": 2, "xp": 100}
    assert prizes._top5_per_bucket("invalid", "week", "2099-W10") == []
    assert prizes._user_rank_in_scope(
        first_id, "invalid", "week", "2099-W10"
    ) is None


def test_run_payouts_handles_due_period_and_records_completion(monkeypatch):
    expected = {"week": {"period_key": "2099-W03", "winners": 1}, "month": None}
    monkeypatch.setattr(prizes, "run_due_payouts", lambda: expected)

    result = prizes.run_payouts_if_due()

    assert result == expected


def test_pending_results_and_acknowledgement(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Pending Winner")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_payout_run "
            "(period_kind, period_key, status, claimed_at, completed_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("week", "2099-W04", "completed", "2099-01-01", "2099-01-01"),
        )
        _exec(db, "INSERT INTO student_lb_prize (client_id, period_kind, period_key, scope, rank, coins, xp_in_period) VALUES (%s,%s,%s,%s,%s,%s,%s)",
              (client_id, "week", "2099-W04", "global", 1, 250, 500))
    monkeypatch.setattr(prizes, "_user_rank_in_scope", lambda cid, scope, kind, key: {"rank": 1, "total_in_bucket": 10, "xp": 500} if scope == "global" else None)

    pending = prizes.get_pending_period_results(client_id)
    target = next(item for item in pending if item["period_key"] == "2099-W04")
    assert target["total_coins_won"] == 250
    assert target["scopes"]["global"]["rank"] == 1

    prizes.mark_period_seen(client_id, "week", "2099-W04")
    prizes.mark_period_seen(client_id, "week", "2099-W04")
    assert not any(item["period_key"] == "2099-W04" for item in prizes.get_pending_period_results(client_id))


def test_worker_catches_up_after_monday_and_awards_exactly_once(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Catch-up Winner")
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{
                "client_id": client_id,
                "name": "Catch-up Winner",
                "scope_value": None,
                "rank": 1,
                "xp": 500,
            }]
            if scope == "global" and kind == "week" else []
        ),
    )

    first = prizes.run_due_payouts(as_of=date(2099, 2, 3))  # Tuesday
    second = prizes.run_due_payouts(as_of=date(2099, 2, 3))

    assert first["week"] == {"period_key": "2099-W05", "winners": 1}
    assert second["week"] is None
    with get_db() as db:
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM student_lb_prize "
            "WHERE client_id = %s AND period_kind = 'week' AND period_key = '2099-W05'",
            (client_id,),
        ) == 1
        assert _fetchval(
            db,
            "SELECT coins FROM student_wallet WHERE client_id = %s",
            (client_id,),
        ) == 250


def test_wallet_and_prize_roll_back_together_then_retry(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Crash Winner")
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{"client_id": client_id, "name": "Crash Winner", "scope_value": None,
              "rank": 1, "xp": 500}]
            if scope == "global" and kind == "week" else []
        ),
    )
    original_credit = sdb._credit_wallet_with_debt
    should_crash = True

    def crash_after_credit(db, winner_id, amount):
        nonlocal should_crash
        result = original_credit(db, winner_id, amount)
        if should_crash:
            should_crash = False
            raise RuntimeError("simulated process failure")
        return result

    monkeypatch.setattr(sdb, "_credit_wallet_with_debt", crash_after_credit)
    failed = prizes.run_due_payouts(as_of=date(2099, 2, 10))
    assert failed["week"] is None
    with get_db() as db:
        assert _fetchval(
            db, "SELECT COUNT(*) FROM student_lb_prize WHERE client_id = %s",
            (client_id,),
        ) == 0
        assert _fetchval(
            db, "SELECT status FROM student_lb_payout_run "
            "WHERE period_kind = 'week' AND period_key = '2099-W06'",
        ) == "failed"

    retried = prizes.run_due_payouts(as_of=date(2099, 2, 10))
    assert retried["week"]["winners"] == 1
    with get_db() as db:
        assert _fetchval(
            db, "SELECT coins FROM student_wallet WHERE client_id = %s",
            (client_id,),
        ) == 250


def test_concurrent_workers_create_one_prize_and_one_credit(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Concurrent Winner")
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{"client_id": client_id, "name": "Concurrent Winner", "scope_value": None,
              "rank": 1, "xp": 500}]
            if scope == "global" and kind == "week" else []
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _index: prizes.run_due_payouts(as_of=date(2099, 2, 17)),
            range(2),
        ))

    assert sum(result["week"] is not None for result in results) == 1
    with get_db() as db:
        assert _fetchval(
            db, "SELECT COUNT(*) FROM student_lb_prize "
            "WHERE client_id = %s AND period_key = '2099-W07'",
            (client_id,),
        ) == 1
        assert _fetchval(
            db, "SELECT coins FROM student_wallet WHERE client_id = %s",
            (client_id,),
        ) == 250


def test_outbox_is_deduplicated_and_delivered_once(monkeypatch, make_user):
    prizes.init_prize_tables()
    with get_db() as db:
        _exec(db, "UPDATE student_lb_email_outbox SET status = 'sent'")
    client_id = make_user("Outbox Winner")
    from machreach_core import config

    monkeypatch.setattr(config, "LEADERBOARD_WINNERS_RECIPIENT", "ops@example.test")
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{"client_id": client_id, "name": "Outbox Winner", "scope_value": None,
              "rank": 1, "xp": 500}]
            if scope == "global" and kind == "week" else []
        ),
    )
    prizes.run_due_payouts(as_of=date(2099, 2, 24))
    prizes.run_due_payouts(as_of=date(2099, 2, 24))
    delivered = []
    monkeypatch.setattr(
        appmod,
        "_send_system_email",
        lambda to, subject, body, **_kwargs: delivered.append((to, subject, body)) or True,
    )

    assert prizes.dispatch_payout_notifications(limit=10) == {"sent": 2, "failed": 0}
    assert prizes.dispatch_payout_notifications(limit=10) == {"sent": 0, "failed": 0}
    assert len(delivered) == 2
    with get_db() as db:
        assert _fetchval(
            db, "SELECT COUNT(*) FROM student_lb_email_outbox "
            "WHERE period_kind = 'week' AND period_key = '2099-W08'",
        ) == 2


def test_worker_drains_multiple_missed_periods_in_order(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Backlog Winner")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_payout_cursor (period_kind, next_period_key) "
            "VALUES ('week', '2299-W01') "
            "ON CONFLICT(period_kind) DO UPDATE SET next_period_key = '2299-W01'",
        )
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{"client_id": client_id, "name": "Backlog Winner", "scope_value": None,
              "rank": 1, "xp": 500}]
            if scope == "global" and kind == "week" and key == "2299-W03" else []
        ),
    )
    after_w03 = date.fromisoformat(
        prizes._period_window("week", "2299-W03")[1][:10]
    ) + timedelta(days=1)

    result = prizes.run_due_payouts(as_of=after_w03)

    assert result["week"] == {"period_key": "2299-W03", "winners": 1}
    with get_db() as db:
        completed = _fetchval(
            db,
            "SELECT COUNT(*) FROM student_lb_payout_run "
            "WHERE period_kind = 'week' AND period_key IN ('2299-W01','2299-W02','2299-W03') "
            "AND status = 'completed'",
        )
        cursor = _fetchval(
            db,
            "SELECT next_period_key FROM student_lb_payout_cursor WHERE period_kind = 'week'",
        )
    assert completed == 3
    assert cursor == "2299-W04"


def test_only_one_worker_can_reclaim_a_stale_run():
    prizes.init_prize_tables()
    old_claimed_at = prizes._utcnow() - timedelta(hours=1)
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_payout_run "
            "(period_kind, period_key, status, claim_token, claimed_at, completed_at) "
            "VALUES ('week', '2399-W01', 'processing', 'dead-worker', %s, NULL)",
            (old_claimed_at,),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(
            lambda _index: prizes._claim_run("week", "2399-W01"),
            range(2),
        ))

    assert sum(token is not None for token in claims) == 1


def test_failed_email_does_not_starve_later_outbox_rows(monkeypatch):
    prizes.init_prize_tables()
    with get_db() as db:
        _exec(db, "UPDATE student_lb_email_outbox SET status = 'sent'")
        for suffix, recipient in (("bad", "bad@example.test"), ("good", "good@example.test")):
            _exec(
                db,
                "INSERT INTO student_lb_email_outbox "
                "(event_key, period_kind, period_key, recipient, subject, body) "
                "VALUES (%s, 'week', '2499-W01', %s, 'subject', 'body')",
                (f"lb:week:2499-W01:{suffix}", recipient),
            )
    delivered = []
    monkeypatch.setattr(
        appmod,
        "_send_system_email",
        lambda to, subject, body, **_kwargs: (
            False if to == "bad@example.test"
            else delivered.append(to) or True
        ),
    )

    result = prizes.dispatch_payout_notifications(limit=10)

    assert result == {"sent": 1, "failed": 1}
    assert delivered == ["good@example.test"]
    with get_db() as db:
        assert _fetchval(
            db,
            "SELECT status FROM student_lb_email_outbox "
            "WHERE event_key = 'lb:week:2499-W01:bad'",
        ) == "pending"
    prizes.cleanup_payout_outbox()


def test_reclaimed_worker_fences_old_owner_before_money_writes(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Fenced Winner")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_lb_payout_run "
            "(period_kind, period_key, status, claim_token, claimed_at, completed_at) "
            "VALUES ('week', '2399-W02', 'processing', 'old-owner', %s, NULL)",
            (prizes._utcnow() - timedelta(hours=1),),
        )
    new_owner = prizes._claim_run("week", "2399-W02")
    assert new_owner
    monkeypatch.setattr(
        prizes,
        "_top5_per_bucket",
        lambda scope, kind, key, db=None: (
            [{"client_id": client_id, "name": "Fenced Winner", "scope_value": None,
              "rank": 1, "xp": 500}]
            if scope == "global" else []
        ),
    )

    with pytest.raises(RuntimeError, match="ownership was lost"):
        prizes._award_winners("week", "2399-W02", "old-owner")

    with get_db() as db:
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM student_lb_prize "
            "WHERE client_id = %s AND period_key = '2399-W02'",
            (client_id,),
        ) == 0
