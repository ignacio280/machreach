"""Owner dashboard: who signed up, who they referred, who pays."""
from datetime import datetime, timedelta, timezone

from machreach_core.db import _exec, get_db
from student import db as sdb
from student import growth


def _pay(client_id: int, *, status: str = "active", ends_at=None, since=None):
    with get_db() as db:
        _exec(db, "DELETE FROM student_subscription_state WHERE client_id = %s", (client_id,))
        _exec(
            db,
            "INSERT INTO student_subscription_state (client_id, tier, status, ends_at, since_at) "
            "VALUES (%s, 'plus', %s, %s, %s)",
            (client_id, status, ends_at, since or datetime.now(timezone.utc).isoformat()),
        )


def _row_for(rows, client_id):
    return next(r for r in rows if r["id"] == client_id)


def test_each_user_row_counts_their_referrals_and_which_ones_pay(make_user):
    owner = make_user("Inviter", "inviter@growth.test")
    payer = make_user("Paying Friend", "payer@growth.test")
    freeloader = make_user("Free Friend", "free@growth.test")
    code = sdb.get_or_create_referral_code(owner)
    assert sdb.redeem_referral(code, payer) == owner
    assert sdb.redeem_referral(code, freeloader) == owner
    _pay(payer)

    rows = growth.user_rows()
    mine = _row_for(rows, owner)

    assert mine["email"] == "inviter@growth.test"
    assert mine["referral_code"] == code
    assert mine["referred"] == 2
    assert mine["referred_paying"] == 1
    assert _row_for(rows, payer)["paying"] is True
    assert _row_for(rows, freeloader)["paying"] is False


def test_referral_weeks_are_not_counted_as_paying(make_user):
    """A free week from a referral makes the app behave like Plus, but it is
    not revenue and must not inflate the paying number."""
    from student import subscription as ssub

    client_id = make_user("Gifted", "gifted@growth.test")
    ssub.grant_plus_days(client_id, 7)

    row = _row_for(growth.user_rows(), client_id)

    assert row["paying"] is False
    assert row["promo_plus"] is True
    assert ssub.get_tier(client_id) == "plus"


def test_a_cancelled_subscription_counts_until_the_term_ends(make_user):
    still_paid = make_user("Cancelled Soon", "cancelling@growth.test")
    lapsed = make_user("Cancelled Already", "lapsed@growth.test")
    future = (datetime.now(timezone.utc) + timedelta(days=9)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    _pay(still_paid, status="cancelled", ends_at=future)
    _pay(lapsed, status="cancelled", ends_at=past)

    rows = growth.user_rows()

    assert _row_for(rows, still_paid)["paying"] is True
    assert _row_for(rows, lapsed)["paying"] is False


def test_summary_reports_the_referral_channel_conversion(make_user):
    owner = make_user("Channel Owner", "channel@growth.test")
    joiner = make_user("Channel Joiner", "joiner@growth.test")
    sdb.redeem_referral(sdb.get_or_create_referral_code(owner), joiner)
    _pay(joiner)

    stats = growth.summary()

    assert stats["users"] >= 2
    assert stats["signups_from_referrals"] >= 1
    assert stats["referred_paying"] >= 1
    assert stats["referred_conversion_pct"] > 0


def test_signups_by_day_covers_every_day_without_gaps():
    days = growth.signups_by_day(14)

    assert len(days) == 14
    assert days[0]["day"] < days[-1]["day"]
    assert days[-1]["day"] == datetime.now(timezone.utc).date().isoformat()


def test_the_dashboard_is_admin_only(client, make_user):
    client_id = make_user("Nosy Student", "nosy@growth.test")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["account_type"] = "student"
        session["session_version"] = 0

    page = client.get("/admin/growth")
    export = client.get("/admin/growth.csv")

    assert page.status_code == 302
    assert "/admin" not in page.headers["Location"]
    assert export.status_code == 302


def test_an_admin_without_mfa_is_sent_to_set_it_up(client, make_user):
    client_id = make_user("Admin No MFA", "admin-nomfa@growth.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["account_type"] = "student"
        session["session_version"] = 0

    response = client.get("/admin/growth")

    assert response.status_code == 302
    assert "/admin/mfa" in response.headers["Location"]


def test_the_admin_page_lists_users_with_their_referral_numbers(client, make_user):
    from datetime import datetime as _dt
    from machreach_core import admin_security

    admin_id = make_user("Owner", "owner@growth.test")
    invited = make_user("Invited Payer", "invited@growth.test")
    sdb.redeem_referral(sdb.get_or_create_referral_code(admin_id), invited)
    _pay(invited)
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (admin_id,))
    admin_security.enroll(admin_id, "JBSWY3DPEHPK3PXP")
    with client.session_transaction() as session:
        session["client_id"] = admin_id
        session["account_type"] = "student"
        session["session_version"] = 0
        session["admin_mfa_verified_at"] = _dt.now(timezone.utc).timestamp()

    body = client.get("/admin/growth").get_data(as_text=True)
    csv_body = client.get("/admin/growth.csv").get_data(as_text=True)

    assert "owner@growth.test" in body
    assert "invited@growth.test" in body
    assert "Referidos que pagan" in body
    assert "invited@growth.test" in csv_body
    assert csv_body.splitlines()[0].startswith("ID,Nombre,Correo")


def test_the_page_can_be_filtered_to_paying_users(client, make_user):
    from datetime import datetime as _dt
    from machreach_core import admin_security

    admin_id = make_user("Filter Owner", "filter-owner@growth.test")
    payer = make_user("Filter Payer", "filter-payer@growth.test")
    _pay(payer)
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (admin_id,))
    admin_security.enroll(admin_id, "JBSWY3DPEHPK3PXP")
    with client.session_transaction() as session:
        session["client_id"] = admin_id
        session["account_type"] = "student"
        session["session_version"] = 0
        session["admin_mfa_verified_at"] = _dt.now(timezone.utc).timestamp()

    body = client.get("/admin/growth?only=paying").get_data(as_text=True)

    assert "filter-payer@growth.test" in body
    assert "filter-owner@growth.test" not in body
