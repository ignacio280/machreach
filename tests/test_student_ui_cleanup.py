import base64
import json
import re

from machreach_core.db import _exec, get_db
from student import db as sdb

PHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


def _student(client, make_user, *, name="UI Student"):
    client_id = make_user(name=name)
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"
    return client_id


def _app_payload(markup):
    match = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', markup)
    assert match, "the live app payload is missing"
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def test_achievements_page_is_gone(client, make_user):
    _student(client, make_user)

    response = client.get("/student/achievements")

    assert response.status_code == 302
    assert "/student/achievements" not in client.get("/student").get_data(as_text=True)


def test_focus_on_a_phone_serves_the_notice_instead_of_the_timer(client, make_user):
    _student(client, make_user)

    phone = client.get("/student/focus", headers={"User-Agent": PHONE_UA}).get_data(as_text=True)
    desktop = client.get("/student/focus").get_data(as_text=True)

    assert "focus-mobile-locked" in phone
    assert "focus.bundle.min.js" not in phone
    assert "focus.bundle.min.js" in desktop


def test_topbar_payload_carries_streak_freezes(client, make_user):
    client_id = _student(client, make_user)
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET streak_freezes = %s WHERE client_id = %s", (4, client_id))

    payload = _app_payload(client.get("/student/courses").get_data(as_text=True))

    assert payload["freezes"] == 4


def test_grade_sheet_picks_up_courses_added_after_it_was_saved(client, make_user):
    client_id = _student(client, make_user)
    sdb.create_manual_course(client_id, "Álgebra", "MAT100")
    page = _app_payload(client.get("/student/gpa").get_data(as_text=True))
    first = page["grades"]["sheet"]
    current = first["current"]
    assert [c["name"] for c in first["sems"][current]["courses"]] == ["Álgebra"]

    saved = client.post("/api/student/grades/sheet", json={"sheet": first},
                        headers={"X-CSRFToken": page["csrf"]})
    assert saved.status_code == 200
    sdb.create_manual_course(client_id, "Física", "FIS100")

    second = _app_payload(client.get("/student/gpa").get_data(as_text=True))["grades"]["sheet"]

    assert [c["name"] for c in second["sems"][current]["courses"]] == ["Álgebra", "Física"]


def test_grade_sheet_does_not_duplicate_a_course_the_student_typed_by_hand(client, make_user):
    client_id = _student(client, make_user)
    sdb.create_manual_course(client_id, "Química", "QIM100")
    sheet = {
        "current": 0,
        "sems": [{"label": "I", "courses": [
            {"id": "manual-1", "name": "química", "credits": 10, "evals": []},
        ]}],
    }
    csrf = _app_payload(client.get("/student/gpa").get_data(as_text=True))["csrf"]
    stored = client.post("/api/student/grades/sheet", json={"sheet": sheet},
                         headers={"X-CSRFToken": csrf})
    assert stored.status_code == 200

    saved = _app_payload(client.get("/student/gpa").get_data(as_text=True))["grades"]["sheet"]

    assert len(saved["sems"][0]["courses"]) == 1


def test_settings_page_serves_the_react_shell_with_profile_data(client, make_user):
    _student(client, make_user, name="Ajustes Student")

    body = client.get("/student/settings").get_data(as_text=True)
    data = _app_payload(body)

    assert "/static/machreach_app/ajustes.bundle.min.js" in body
    assert data["profile"]["name"] == "Ajustes Student"
    assert "preferences" in data["profile"]


def test_edit_profile_gets_the_identity_payload_for_its_preview(client, make_user):
    _student(client, make_user, name="Preview Student")

    data = _app_payload(client.get("/student/profile/edit").get_data(as_text=True))

    # The editor renders the public hero, so it needs both payloads.
    assert data["profile"]["handle"]
    assert data["profile_edit"]["selected_banner"] == "default"


def test_shop_payload_marks_animated_cosmetics(client, make_user):
    client_id = _student(client, make_user)
    sdb.get_wallet(client_id)

    shop = _app_payload(client.get("/student/shop").get_data(as_text=True))["shop"]

    animated = {item["k"]: item["anim"] for item in shop["banners"] + shop["flags"] if item["anim"]}
    assert "cherry" in animated
    assert animated["cherry"] == sdb.BANNERS["cherry"]["anim_class"]
