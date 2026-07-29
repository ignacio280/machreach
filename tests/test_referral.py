"""Money path: referral codes, redemption guards, and the free-week reward."""
from student import db as sdb
from student import subscription as ssub
from machreach_core.db import _fetchone, get_db


def test_code_is_stable_and_unique(make_user):
    a, b = make_user(), make_user()
    code_a = sdb.get_or_create_referral_code(a)
    assert code_a and sdb.get_or_create_referral_code(a) == code_a   # stable
    assert sdb.get_or_create_referral_code(b) != code_a              # unique per user


def test_lookup_owner(make_user):
    owner = make_user()
    code = sdb.get_or_create_referral_code(owner)
    assert sdb.lookup_referral_owner(code) == owner
    assert sdb.lookup_referral_owner(code.lower()) == owner          # case-insensitive
    assert sdb.lookup_referral_owner("NOPE123") is None


def test_redeem_grants_referrer_and_is_idempotent(make_user):
    owner = make_user()
    newbie = make_user()
    code = sdb.get_or_create_referral_code(owner)

    rewarded = sdb.redeem_referral(code, newbie)
    assert rewarded == owner
    assert sdb.referral_count(owner) == 1

    # Same user can't redeem again (UNIQUE on referred_id).
    assert sdb.redeem_referral(code, newbie) is None
    assert sdb.referral_count(owner) == 1


def test_self_referral_blocked(make_user):
    owner = make_user()
    code = sdb.get_or_create_referral_code(owner)
    assert sdb.redeem_referral(code, owner) is None
    assert sdb.referral_count(owner) == 0


def test_unknown_code_rejected(make_user):
    newbie = make_user()
    assert sdb.redeem_referral("DOESNT9", newbie) is None


def test_full_reward_flow_grants_a_week_of_plus(make_user):
    owner = make_user()
    newbie = make_user()
    assert ssub.get_tier(owner) == "free"

    code = sdb.get_or_create_referral_code(owner)
    rewarded = sdb.redeem_referral(code, newbie)
    assert rewarded == owner
    ssub.grant_plus_days(rewarded, 7)            # mirrors the signup route

    assert ssub.get_tier(owner) == "plus"
    assert ssub.has_plus_access(owner) is True


def test_multiple_referrals_stack_weeks(make_user):
    owner = make_user()
    code = sdb.get_or_create_referral_code(owner)
    for _ in range(3):
        cid = make_user()
        assert sdb.redeem_referral(code, cid) == owner
        ssub.grant_plus_days(owner, 7)
    assert sdb.referral_count(owner) == 3
    assert ssub.get_tier(owner) == "plus"


def test_failed_referral_reward_keeps_pending_marker_for_retry(
    make_user, monkeypatch
):
    owner = make_user("Retry Referrer")
    referred = make_user("Retry Referral")
    code = sdb.get_or_create_referral_code(owner)
    sdb.set_pending_referral(referred, code)
    monkeypatch.setattr(
        ssub,
        "_extend_promotional_entitlement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )

    try:
        ssub.redeem_pending_referral_reward(referred)
    except RuntimeError:
        pass

    with get_db() as db:
        pending = _fetchone(
            db,
            "SELECT code FROM student_pending_referrals WHERE referred_id = %s",
            (referred,),
        )
        redemption = _fetchone(
            db,
            "SELECT referred_id FROM student_referrals WHERE referred_id = %s",
            (referred,),
        )
    assert pending == {"code": code}
    assert redemption is None


def test_signup_route_rewards_inviter_after_verify(make_user, client, monkeypatch):
    """End-to-end wiring: a friend who registers with the code and then verifies
    their email grants the inviter a free week of Plus. The reward must fire at
    verification (genuine sign-up), not at registration. Regression guard for the
    bug where the signup route captured ?ref= but never redeemed it."""
    import app as appmod
    import re
    from machreach_core.db import get_client_by_email

    # Exercise the route logic without the CSRF token the real form injects.
    monkeypatch.setitem(appmod.app.config, "WTF_CSRF_ENABLED", False)

    owner = make_user()
    code = sdb.get_or_create_referral_code(owner)
    assert ssub.get_tier(owner) == "free"

    sent = []

    def _capture_email(_to, _subject, body):
        sent.append(body)
        return True

    # Pretend the verification email sent so the account isn't rolled back.
    monkeypatch.setattr(appmod, "_send_system_email", _capture_email)

    email = f"friend_{owner}@example.com"
    resp = client.post("/register", data={
        "name": "Friend", "email": email,
        "password": "secret12345", "password2": "secret12345", "ref": code,
    })
    assert resp.status_code in (301, 302)

    newbie = get_client_by_email(email)
    assert newbie and not newbie.get("email_verified")

    # Reward must NOT be granted before the email is verified.
    assert sdb.referral_count(owner) == 0
    assert ssub.get_tier(owner) == "free"

    match = re.search(r"/verify-email/([A-Za-z0-9_-]+)", sent[-1])
    assert match
    client.get(f"/verify-email/{match.group(1)}")

    # Now the inviter gets their free week.
    assert sdb.referral_count(owner) == 1
    assert ssub.get_tier(owner) == "plus"


def test_referral_rolling_cap_withholds_excess_reward(make_user, monkeypatch):
    monkeypatch.setenv("REFERRAL_REWARD_30D_MAX", "1")
    owner = make_user("Referral Cap Owner")
    code = sdb.get_or_create_referral_code(owner)
    first = make_user("Referral First")
    second = make_user("Referral Second")
    sdb.set_pending_referral(first, code)
    sdb.set_pending_referral(second, code)

    assert ssub.redeem_pending_referral_reward(first) is not None
    first_expiry = ssub.plus_grant_until(owner)
    assert ssub.redeem_pending_referral_reward(second) is None

    assert ssub.plus_grant_until(owner) == first_expiry
    with get_db() as db:
        withheld = _fetchone(
            db,
            "SELECT status FROM student_referral_reward_audit WHERE referred_id=%s",
            (second,),
        )
    assert withheld["status"] == "withheld"


def test_referral_reward_can_be_revoked_once(make_user):
    owner = make_user("Referral Revoke Owner")
    referred = make_user("Referral Revoke Friend")
    code = sdb.get_or_create_referral_code(owner)
    sdb.set_pending_referral(referred, code)
    assert ssub.redeem_pending_referral_reward(referred) is not None
    before = ssub._parse_iso(ssub.plus_grant_until(owner))

    assert ssub.revoke_referral_reward(referred) is True
    assert ssub.revoke_referral_reward(referred) is False
    after = ssub._parse_iso(ssub.plus_grant_until(owner))
    assert before is not None
    assert after is None or after < before
