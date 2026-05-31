"""Money path: referral codes, redemption guards, and the free-week reward."""
from student import db as sdb
from student import subscription as ssub


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
    assert ssub.has_unlimited_ai(owner) is True


def test_multiple_referrals_stack_weeks(make_user):
    owner = make_user()
    code = sdb.get_or_create_referral_code(owner)
    for _ in range(3):
        cid = make_user()
        assert sdb.redeem_referral(code, cid) == owner
        ssub.grant_plus_days(owner, 7)
    assert sdb.referral_count(owner) == 3
    assert ssub.get_tier(owner) == "plus"
