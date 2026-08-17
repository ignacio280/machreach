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


def _seeded_academic_pair():
    """A real (university, carrera) pair from the seed catalogue, since
    academic setup refuses anything not approved."""
    from machreach_core.db import _fetchone

    with get_db() as db:
        university = _fetchone(
            db,
            "SELECT id, short_name FROM universities WHERE name = %s",
            ("Universidad de Chile",),
        )
        major = _fetchone(
            db, "SELECT id, name FROM majors WHERE university_id IS NULL ORDER BY id LIMIT 1")
    return university, major


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


def test_each_row_carries_the_university_and_carrera_the_student_picked(make_user):
    from student import academic

    studied = make_user("Studied", "studied@growth.test")
    fresh = make_user("No Setup", "nosetup@growth.test")
    university, major = _seeded_academic_pair()
    academic.save_academic_profile(studied, "CL", int(university["id"]), int(major["id"]))

    rows = growth.user_rows()

    assert _row_for(rows, studied)["university"] == university["short_name"]
    assert _row_for(rows, studied)["major"] == major["name"]
    # Someone who never finished academic setup gets empty strings, not None.
    assert _row_for(rows, fresh)["university"] == ""
    assert _row_for(rows, fresh)["major"] == ""


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


def test_the_page_and_the_csv_show_the_university_and_carrera(client, make_user):
    from student import academic

    _admin_session(client, make_user, "Acad Owner", "acad-owner@growth.test")
    student_id = make_user("Acad Student", "acad-student@growth.test")
    university, major = _seeded_academic_pair()
    academic.save_academic_profile(student_id, "CL", int(university["id"]), int(major["id"]))

    body = client.get("/admin/growth").get_data(as_text=True)
    csv_body = client.get("/admin/growth.csv").get_data(as_text=True)
    searched = client.get(
        "/admin/growth", query_string={"q": university["short_name"]}).get_data(as_text=True)

    assert "<th>Universidad</th><th>Carrera</th>" in body
    assert university["short_name"] in body
    assert major["name"] in body
    assert "Universidad,Carrera" in csv_body.splitlines()[0]
    # The search box reaches the new columns too.
    assert "acad-student@growth.test" in searched
    assert "acad-owner@growth.test" not in searched


def _admin_session(client, make_user, name, email, *, reauth=True):
    from datetime import datetime as _dt
    from machreach_core import admin_security

    admin_id = make_user(name, email)
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (admin_id,))
    admin_security.enroll(admin_id, "JBSWY3DPEHPK3PXP")
    now = _dt.now(timezone.utc).timestamp()
    with client.session_transaction() as session:
        session["client_id"] = admin_id
        session["account_type"] = "student"
        session["session_version"] = 0
        session["admin_mfa_verified_at"] = now
        if reauth:
            session["admin_reauthenticated_at"] = now
    return admin_id


def test_deleting_a_user_needs_a_recent_reauthentication(client, make_user):
    _admin_session(client, make_user, "Del Admin A", "del-a@growth.test", reauth=False)
    victim = make_user("Victim A", "victim-a@growth.test")

    response = client.get(f"/admin/growth/delete/{victim}")

    assert response.status_code == 302
    assert "/admin/reauth" in response.headers["Location"]


def test_deletion_requires_the_exact_email_typed_back(client, flask_app, make_user, monkeypatch):
    from machreach_core.db import get_client

    _admin_session(client, make_user, "Del Admin B", "del-b@growth.test")
    victim = make_user("Victim B", "victim-b@growth.test")
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    response = client.post(f"/admin/growth/delete/{victim}",
                           data={"confirm_email": "wrong@growth.test"})

    assert response.status_code == 200
    assert get_client(victim) is not None


def test_a_confirmed_deletion_removes_the_account_and_its_rows(client, flask_app, make_user, monkeypatch):
    from machreach_core.db import _fetchone, get_client

    _admin_session(client, make_user, "Del Admin C", "del-c@growth.test")
    victim = make_user("Victim C", "victim-c@growth.test")
    sdb.create_manual_course(victim, "Cálculo", "MAT100")
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    response = client.post(f"/admin/growth/delete/{victim}",
                           data={"confirm_email": "victim-c@growth.test"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/growth")
    assert get_client(victim) is None
    with get_db() as db:
        assert _fetchone(db, "SELECT 1 FROM student_courses WHERE client_id = %s", (victim,)) is None


def test_administrators_and_your_own_account_cannot_be_deleted(client, flask_app, make_user, monkeypatch):
    from machreach_core.db import get_client

    admin_id = _admin_session(client, make_user, "Del Admin D", "del-d@growth.test")
    other_admin = make_user("Other Admin", "other-admin@growth.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET is_admin = 1 WHERE id = %s", (other_admin,))
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    mine = client.post(f"/admin/growth/delete/{admin_id}",
                       data={"confirm_email": "del-d@growth.test"})
    theirs = client.post(f"/admin/growth/delete/{other_admin}",
                         data={"confirm_email": "other-admin@growth.test"})

    assert mine.status_code == 200
    assert theirs.status_code == 200
    assert get_client(admin_id) is not None
    assert get_client(other_admin) is not None


def test_the_table_offers_delete_only_where_it_is_allowed(client, make_user):
    admin_id = _admin_session(client, make_user, "Del Admin E", "del-e@growth.test")
    victim = make_user("Victim E", "victim-e@growth.test")

    body = client.get("/admin/growth").get_data(as_text=True)

    assert f'/admin/growth/delete/{victim}' in body
    assert f'/admin/growth/delete/{admin_id}' not in body


def test_deleting_a_paying_account_stops_when_billing_cannot_be_cancelled(
    client, flask_app, make_user, monkeypatch
):
    """A cancelled-but-still-billed subscription is worse than a stale row."""
    from machreach_core.db import get_client
    import app as appmod

    _admin_session(client, make_user, "Del Admin F", "del-f@growth.test")
    victim = make_user("Victim F", "victim-f@growth.test")
    with get_db() as db:
        _exec(db, "INSERT INTO student_subscription_state (client_id, tier, status, ls_sub_id) "
                  "VALUES (%s, 'plus', 'active', %s)", (victim, "ls-123"))
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(appmod, "_cancel_ls_subs", lambda ids, client_id: False)

    response = client.post(f"/admin/growth/delete/{victim}",
                           data={"confirm_email": "victim-f@growth.test"})

    assert response.status_code == 200
    assert get_client(victim) is not None
