"""Returning what the streak walk took, with an audit trail."""
from machreach_core.db import _exec, _fetchone, _fetchval, get_db
from student import db as sdb
from scripts.refund_streak_freezes import main


def _wallet(client_id):
    return sdb.get_wallet(client_id)


def test_a_dry_run_reports_and_writes_nothing(make_user, capsys):
    cid = make_user("Refund Dry", "dry@refund.test")
    sdb.get_wallet(cid)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET coins = 5, streak_freezes = 0 WHERE client_id = %s", (cid,))

    assert main(["dry@refund.test", "--freezes", "3", "--coins", "25"]) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out
    assert _wallet(cid)["coins"] == 5
    assert _wallet(cid)["streak_freezes"] == 0


def test_applying_restores_freezes_and_coins_and_leaves_an_audit_trail(make_user):
    cid = make_user("Refund Real", "real@refund.test")
    sdb.get_wallet(cid)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET coins = 5, streak_freezes = 0, freezes_since = '' "
                  "WHERE client_id = %s", (cid,))

    assert main(["real@refund.test", "--freezes", "2", "--coins", "25", "--apply"]) == 0

    wallet = _wallet(cid)
    assert wallet["streak_freezes"] == 2
    assert wallet["coins"] == 30
    # Restored freezes protect from today forward, never backwards.
    with get_db() as db:
        since = _fetchone(db, "SELECT freezes_since FROM student_wallet WHERE client_id = %s", (cid,))
        assert str(since["freezes_since"] or "")[:10] == sdb.user_date(cid).isoformat()
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM operational_events WHERE event_type = %s",
            ("streak_freeze_refund",),
        ) >= 1


def test_a_request_over_the_cap_is_clamped_not_silently_dropped(make_user, capsys):
    cid = make_user("Refund Cap", "cap@refund.test")
    sdb.get_wallet(cid)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET streak_freezes = 2 WHERE client_id = %s", (cid,))

    assert main(["cap@refund.test", "--freezes", "9", "--apply"]) == 0

    out = capsys.readouterr().out
    assert "may hold" in out
    # Free cap is 3, so only the one remaining slot is filled.
    assert _wallet(cid)["streak_freezes"] == sdb.FREE_STREAK_FREEZE_CAP


def test_it_refuses_unknown_accounts_and_negative_amounts(capsys):
    import pytest

    assert main(["nobody@refund.test", "--freezes", "1", "--apply"]) == 1
    assert "No account" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["someone@refund.test", "--coins", "-5", "--apply"])
