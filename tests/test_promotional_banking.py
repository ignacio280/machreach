"""Free weeks must never burn underneath a plan the student paid for.

An invited student holds promotional Plus (`plus_until`). The moment they also
pay, that promotional time is banked instead of ticking away, and it is handed
back the instant paid access stops. The bug these pin: the shop keyed its cards
off the effective tier, so a student with free weeks saw Plus as "Plan actual"
and had no way to ever buy it.
"""
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from student import subscription as ssub


@pytest.fixture()
def sub_id() -> str:
    """A provider subscription id is unique table-wide; give each test its own."""
    return "sub_" + uuid.uuid4().hex[:12]


def _days_left(client_id) -> int:
    return ssub.promotional_summary(client_id)["days_left"]


def _sign_in(client, client_id) -> None:
    """A session that reaches the shop instead of the setup wizard."""
    from machreach_core.db import _exec, get_db

    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET email_verified = 1, academic_setup_complete = 1 "
            "WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = "Test User"
        sess["account_type"] = "student"
        sess["session_version"] = 0
        sess["lang"] = "es"


def test_free_weeks_do_not_burn_while_a_paid_plan_runs(make_user, sub_id):
    cid = make_user()
    ssub.grant_plus_days(cid, 14)
    assert _days_left(cid) == 14

    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)

    summary = ssub.promotional_summary(cid)
    assert summary["banked"] is True          # parked, not running
    assert summary["days_left"] == 14         # and nothing was lost
    assert summary["plus_until"] is None
    assert ssub.get_tier(cid) == "plus"       # paid is what grants access now


def test_free_weeks_resume_when_the_paid_plan_ends(make_user, sub_id):
    cid = make_user()
    ssub.grant_plus_days(cid, 14)
    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)
    assert ssub.promotional_summary(cid)["banked"] is True

    ssub.set_subscription_state(cid, tier="free", status="expired", ls_sub_id=sub_id)

    summary = ssub.promotional_summary(cid)
    assert summary["banked"] is False
    assert summary["days_left"] == 14
    # Still Plus — the free weeks are carrying them now, from this instant.
    assert ssub.get_tier(cid) == "plus"
    resumed = datetime.fromisoformat(summary["plus_until"])
    if resumed.tzinfo is None:
        resumed = resumed.replace(tzinfo=timezone.utc)
    assert resumed > datetime.now(timezone.utc) + timedelta(days=13)


def test_a_reward_earned_while_paying_is_banked_not_spent(make_user, sub_id):
    """An invite that lands mid-month must not evaporate under the plan."""
    cid = make_user()
    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)

    ssub.grant_plus_days(cid, 7)

    summary = ssub.promotional_summary(cid)
    assert summary["banked"] is True
    assert summary["days_left"] == 7

    ssub.set_subscription_state(cid, tier="free", status="expired", ls_sub_id=sub_id)
    assert _days_left(cid) == 7


def test_banked_weeks_survive_a_full_paid_cycle_and_stack(make_user, sub_id):
    cid = make_user()
    ssub.grant_plus_days(cid, 7)
    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)
    ssub.grant_plus_days(cid, 7)               # second invite pays out mid-plan

    assert ssub.promotional_summary(cid)["days_left"] == 14

    ssub.set_subscription_state(cid, tier="free", status="expired", ls_sub_id=sub_id)
    assert _days_left(cid) == 14


def test_promotional_plus_alone_never_looks_like_a_paid_plan(make_user):
    """What the shop keys its buttons off. Free weeks grant access but must
    leave the paid tier at free, or the Plus card disables itself."""
    cid = make_user()
    ssub.grant_plus_days(cid, 14)

    assert ssub.get_tier(cid) == "plus"                        # they do have Plus
    state = ssub.get_subscription_state(cid)
    assert ssub._paid_only_tier(state) == "free"               # but they pay for nothing


def _shop_payload(client) -> dict:
    """The live shop is a React page fed by a base64 payload; read that."""
    import base64
    import json
    import re

    page = client.get("/student/shop").get_data(as_text=True)
    for match in re.finditer(r'atob\("([A-Za-z0-9+/=]{40,})"\)', page):
        try:
            data = json.loads(base64.b64decode(match.group(1)))
        except Exception:
            continue
        if isinstance(data, dict) and "shop" in data:
            return data
    raise AssertionError("shop payload not found in the rendered page")


def test_the_shop_lets_a_student_holding_free_weeks_buy_plus(client, make_user):
    """The reported bug: free weeks read as Plus, so the buy button vanished."""
    cid = make_user()
    ssub.grant_plus_days(cid, 14)
    _sign_in(client, cid)

    data = _shop_payload(client)

    assert data["plus"] is True                  # they do have Plus access
    assert data["shop"]["paid"] is False         # but they pay for nothing,
    assert data["shop"]["promo_days"] == 14      # so the card must offer the plan
    assert data["shop"]["promo_banked"] is False


def test_a_paying_student_is_not_offered_the_plan_again(client, make_user, sub_id):
    cid = make_user()
    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)
    _sign_in(client, cid)

    data = _shop_payload(client)

    assert data["plus"] is True
    assert data["shop"]["paid"] is True


def test_a_paying_student_sees_their_free_weeks_waiting(client, make_user, sub_id):
    cid = make_user()
    ssub.grant_plus_days(cid, 14)
    ssub.set_subscription_state(cid, tier="plus", status="active", ls_sub_id=sub_id)
    _sign_in(client, cid)

    data = _shop_payload(client)

    assert data["shop"]["paid"] is True
    assert data["shop"]["promo_banked"] is True
    assert data["shop"]["promo_days"] == 14
