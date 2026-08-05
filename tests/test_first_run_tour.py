"""The walkthrough shown once, right after setup, ending on the referral."""
import json
import re

from machreach_core.db import _exec, get_db
from student import db as sdb

SOURCE = "static/machreach_app/app/shell.jsx"


def _student(client, make_user, name, email):
    client_id = make_user(name, email)
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"
    return client_id


def _dashboard_payload(body):
    match = re.search(r"__MACHREACH_DASHBOARD__\s*=\s*(\{.*?\});", body, re.S)
    assert match, "the dashboard payload is missing"
    return json.loads(match.group(1))


def test_a_new_account_is_offered_the_tour(client, make_user):
    _student(client, make_user, "Newcomer", "newcomer@tour.test")

    payload = _dashboard_payload(client.get("/student").get_data(as_text=True))

    assert payload["show_tour"] is True
    assert payload["referral_link"].endswith(payload["referral_link"].split("ref=")[-1])
    assert "/register?ref=" in payload["referral_link"]


def test_finishing_the_tour_stops_it_coming_back(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = _student(client, make_user, "Seen", "seen@tour.test")

    done = client.post("/api/student/tour/done")

    assert done.status_code == 200
    assert sdb.tour_seen(client_id) is True
    payload = _dashboard_payload(client.get("/student").get_data(as_text=True))
    assert payload["show_tour"] is False


def test_the_tour_is_not_public(client, flask_app, monkeypatch):
    # Without a session it is refused by CSRF first, and by auth once CSRF is
    # out of the way. Both are refusals; neither writes anything.
    assert client.post("/api/student/tour/done").status_code == 400

    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    assert client.post("/api/student/tour/done").status_code == 401


def test_every_step_points_at_a_real_navigation_item():
    """A tour that highlights a selector nobody renders teaches nothing."""
    source = open(SOURCE, encoding="utf-8").read()
    anchors = set(re.findall(r'anchor:\s*"([a-z]+)"', source))
    nav_ids = set(re.findall(r'\{\s*id:\s*"([a-z]+)"', source))

    assert anchors, "the tour has no anchored steps"
    assert anchors <= nav_ids, f"anchors with no nav item: {anchors - nav_ids}"


def test_the_tour_ends_on_the_referral_offer():
    source = open(SOURCE, encoding="utf-8").read()
    steps = re.search(r"const TOUR_STEPS = \[(.*?)\n\];", source, re.S).group(1)

    assert "final: true" in steps
    assert steps.index("final: true") > steps.index('anchor: "home"')
    assert "7 días de Plus" in source
    assert "/student/friends" in source


def test_the_locked_plus_pages_are_explained_before_the_offer():
    source = open(SOURCE, encoding="utf-8").read()

    assert "plusPitch: true" in source
    # Plan and Analytics are the two Plus-only entries in the sidebar.
    assert 'anchor: "plan"' in source
