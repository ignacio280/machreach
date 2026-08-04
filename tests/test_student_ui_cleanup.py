import base64
import json
import re

from machreach_core.db import _exec, _fetchone, get_db
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


def test_restore_purchase_is_gone(client, make_user):
    """Self-service "restore" granted Plus from an email match, so it is gone;
    entitlement now comes only from the signed provider webhook."""
    _student(client, make_user)

    response = client.post("/api/student/subscription/reconcile")
    shop = client.get("/student/shop").get_data(as_text=True)

    assert response.status_code == 404
    assert "Restaurar compra" not in shop
    assert "reconcile" not in shop


def test_leaderboard_rows_carry_avatar_colour_and_scope_labels(client, make_user):
    """País shows where someone studies, Universidad shows what — unless they
    switched it off in Privacidad."""
    from machreach_core.db import update_mail_preferences
    from student import academic

    client_id = _student(client, make_user, name="Board Student")
    with get_db() as db:
        _exec(db, "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s)",
              (client_id, "test", 300, "seed"))
        major = _fetchone(db, "SELECT id FROM majors LIMIT 1", ())
        univ = _fetchone(db, "SELECT id FROM universities LIMIT 1", ())
        _exec(db, "UPDATE clients SET country_iso = 'CL', university_id = %s, major_id = %s WHERE id = %s",
              (univ["id"], major["id"], client_id))
    update_mail_preferences(client_id, json.dumps({
        "profile_avatar": "#8DACFF", "show_university": True, "show_major": True,
    }))

    # Other tests seed students too, so find this one rather than trusting rank.
    def _mine(rows):
        return next(row for row in rows if int(row["client_id"]) == client_id)

    mine_country = _mine(academic.leaderboard("country", client_id))
    mine_university = _mine(academic.leaderboard("university", client_id))

    assert mine_country["avatar_color"] == "#8DACFF"
    assert mine_country["university"]
    assert mine_university["major"]

    update_mail_preferences(client_id, json.dumps({
        "profile_avatar": "#8DACFF", "show_university": False, "show_major": False,
    }))
    hidden = _mine(academic.leaderboard("country", client_id))
    assert "university" not in hidden
    assert "major" not in hidden


def test_setup_page_serves_the_authored_design(client, make_user):
    """Onboarding is the design's standalone page: its own top bar, no app
    chrome, and the doodles the design shipped are gone."""
    client_id = make_user(name="Setup Student")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 0, university_id = NULL, "
                  "major_id = NULL, country_iso = '' WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Setup Student"
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = "es"

    body = client.get("/student/setup").get_data(as_text=True)

    assert "/static/machreach_app/setup.bundle.min.js" in body
    assert "/static/machreach_app/app/setup.css" in body
    assert "/static/machreach_app/app/pubtop.css" in body
    assert "ss-wrap" not in body           # the legacy onboarding markup is gone
    source = open("static/machreach_app/app/setup.jsx", encoding="utf-8").read()
    assert "doodle" not in source


def test_setup_redirects_once_the_profile_is_complete(client, make_user):
    client_id = _student(client, make_user, name="Done Setup")
    from student import academic

    # The save validates that the university really is in the chosen country.
    with get_db() as db:
        univ = _fetchone(db, "SELECT id, country_iso FROM universities WHERE country_iso <> '' LIMIT 1", ())
        major = _fetchone(db, "SELECT id FROM majors LIMIT 1", ())
    academic.save_academic_profile(
        client_id=client_id, country_iso=univ["country_iso"],
        university_id=int(univ["id"]), major_id=int(major["id"]),
    )

    response = client.get("/student/setup")

    assert response.status_code == 302
    assert "/student" in response.headers["Location"]


def test_topbar_carries_the_chosen_avatar_colour(client, make_user):
    """The bubble used to be hardcoded peach no matter what the student picked
    in the profile editor."""
    from machreach_core.db import update_mail_preferences

    client_id = _student(client, make_user, name="Colour Student")
    update_mail_preferences(client_id, json.dumps({"profile_avatar": "#5DE3B0"}))

    courses = _app_payload(client.get("/student/courses").get_data(as_text=True))
    # The dashboard inlines its payload as plain JSON rather than base64.
    dashboard = client.get("/student").get_data(as_text=True)

    assert courses["avatar_color"] == "#5DE3B0"
    assert '"avatar_color": "#5DE3B0"' in dashboard
    source = open("static/machreach_app/app/shell.jsx", encoding="utf-8").read()
    assert "SHELL_DATA.avatar_color" in source


def test_the_courses_page_has_an_empty_state(client, make_user):
    _student(client, make_user, name="Empty Student")
    source = open("static/machreach_app/app/courses.jsx", encoding="utf-8").read()

    assert "Está vacío…" in source
    assert "¡Comienza agregando tu primer ramo!" in source
    assert "cgrid-empty" in open("static/machreach_app/app/courses.css", encoding="utf-8").read()


def test_focus_guard_chip_distinguishes_offline_from_desactivado():
    """Activo has to mean sites are being blocked right now — not merely that
    the extension is installed."""
    source = open("static/machreach_app/app/focus.jsx", encoding="utf-8").read()

    assert "Offline" in source and "Desactivado" in source
    assert "function focusGuardBlocking" in source
    # It mirrors the extension's own rule, so the two can never disagree.
    assert 'state.phase !== "break"' in source
    assert "Inactivo" not in source


def test_the_status_pills_lost_their_dots():
    for name in ("dash-left.jsx", "focus.jsx"):
        source = open(f"static/machreach_app/app/{name}", encoding="utf-8").read()
        assert 'className="dot"' not in source


def test_auth_flow_pages_serve_the_authored_designs(client):
    forgot = client.get("/forgot-password").get_data(as_text=True)
    verify = client.get("/verify-email-pending?email=a@b.com").get_data(as_text=True)

    assert "/static/machreach_app/recuperar.bundle.min.js" in forgot
    assert "/static/machreach_app/verificar.bundle.min.js" in verify
    for body in (forgot, verify):
        assert "/static/machreach_app/app/authflow.css" in body
        assert "/static/machreach_layout/layout-base.css" not in body
    # The design's floating doodles are not shipped.
    assert "AF_DOODLES" not in open("static/machreach_app/app/authflow.jsx", encoding="utf-8").read()


def test_a_dead_reset_link_lands_on_the_expired_view(client):
    response = client.get("/reset-password/not-a-real-token")

    assert response.status_code == 302
    assert "expired=1" in response.headers["Location"]
