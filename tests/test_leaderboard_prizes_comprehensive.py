from datetime import date

import app as appmod
from machreach_core.db import _exec, _fetchval, get_db
from student import db as sdb
from student import leaderboard_prizes as prizes


def test_prize_amounts_period_keys_windows_and_due_state():
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
    assert prizes._is_due("week", date(2026, 7, 21)) is False
    assert prizes._is_due("month", date(2026, 7, 2)) is False


def test_mark_run_is_idempotent_and_blocks_repeat():
    prizes.init_prize_tables()
    key = "2099-W01"
    prizes._mark_run("week", key)
    prizes._mark_run("week", key)
    with get_db() as db:
        count = _fetchval(db, "SELECT COUNT(*) FROM student_lb_payout_run WHERE period_kind = %s AND period_key = %s", ("week", key))
    assert count == 1


def test_award_winners_credits_wallet_and_records_prize(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Prize Winner")
    monkeypatch.setattr(prizes, "_top5_per_bucket", lambda scope, kind, key: (
        [{"client_id": client_id, "name": "Prize Winner", "scope_value": None, "rank": 1, "xp": 500}]
        if scope == "global" else []
    ))

    summary = prizes._award_winners("week", "2099-W02")

    assert summary["global"][0]["coins"] == 250
    assert sdb.get_wallet(client_id)["coins"] >= 250
    with get_db() as db:
        assert _fetchval(db, "SELECT COUNT(*) FROM student_lb_prize WHERE client_id = %s", (client_id,)) == 1


def test_winner_and_admin_emails_are_aggregated(monkeypatch, make_user):
    client_id = make_user("Email Winner")
    sent = []
    from machreach_core import config

    monkeypatch.setattr(config, "LEADERBOARD_WINNERS_RECIPIENT", "ops@example.test")
    monkeypatch.setattr(appmod, "_send_system_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    summary = {
        "global": [{"client_id": client_id, "name": "Email Winner", "scope_value": None, "rank": 1, "xp": 100, "coins": 500}],
        "country": [{"client_id": client_id, "name": "Email Winner", "scope_value": "CL", "rank": 2, "xp": 100, "coins": 200}],
        "university": [], "major": [],
    }

    prizes._email_winners("month", "2099-01", summary)
    prizes._email_admin("month", "2099-01", summary)

    assert len(sent) == 2
    assert "700 coins" in sent[0][1]
    assert "GLOBAL" in sent[1][2] and "COUNTRY" in sent[1][2]


def test_run_payouts_handles_due_period_and_records_completion(monkeypatch):
    monkeypatch.setattr(prizes, "_is_due", lambda kind, today=None: kind == "week")
    monkeypatch.setattr(prizes, "_period_key", lambda kind, today=None: "2099-W03")
    monkeypatch.setattr(prizes, "_award_winners", lambda kind, key: {
        "global": [{"client_id": 1}], "country": [], "university": [], "major": []
    })
    monkeypatch.setattr(prizes, "_email_admin", lambda *args: None)
    monkeypatch.setattr(prizes, "_email_winners", lambda *args: None)
    marked = []
    monkeypatch.setattr(prizes, "_mark_run", lambda kind, key: marked.append((kind, key)))

    result = prizes.run_payouts_if_due()

    assert result["week"] == {"period_key": "2099-W03", "winners": 1}
    assert result["month"] is None
    assert marked == [("week", "2099-W03")]


def test_pending_results_and_acknowledgement(monkeypatch, make_user):
    prizes.init_prize_tables()
    client_id = make_user("Pending Winner")
    prizes._mark_run("week", "2099-W04")
    with get_db() as db:
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
