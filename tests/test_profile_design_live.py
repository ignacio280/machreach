import base64
import json
import re
import time

from machreach_core.db import _exec, _fetchone, get_db, get_mail_preferences, update_mail_preferences
from student import academic, db as sdb


def _student(client, make_user, *, name="Profile Student"):
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


def test_profile_page_uses_exact_supplied_design_with_real_identity(client, make_user):
    _student(client, make_user, name="Valentina Perfil")

    response = client.get("/student/profile")
    body = response.get_data(as_text=True)
    data = _app_payload(body)

    assert response.status_code == 200
    assert "/static/machreach_app/perfil.bundle.min.js" in body
    assert "/static/machreach_app/app/profile.css" in body
    assert data["profile"]["name"] == "Valentina Perfil"
    assert data["profile"]["email"]
    assert data["profile"]["total_xp"] == 0
    assert data["profile"]["courses"] == []


def test_profile_update_persists_only_the_authenticated_students_profile(client, make_user):
    client_id = _student(client, make_user, name="Old Name")
    page = client.get("/student/profile")
    csrf = _app_payload(page.get_data(as_text=True))["csrf"]

    response = client.post(
        "/api/student/profile",
        json={"name": "Nombre Nuevo", "bio": "Estudio en bloques.", "avatar_color": "#8DACFF"},
        headers={"X-CSRFToken": csrf},
    )

    assert response.status_code == 200
    assert response.get_json()["profile"]["name"] == "Nombre Nuevo"
    with get_db() as db:
        row = _fetchone(db, "SELECT name, profile_bio FROM clients WHERE id = %s", (client_id,))
    assert row == {"name": "Nombre Nuevo", "profile_bio": "Estudio en bloques."}


def test_profile_update_rejects_invalid_identity_values(client, make_user):
    _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile").get_data(as_text=True))["csrf"]

    response = client.post(
        "/api/student/profile",
        json={"name": " ", "bio": "x" * 181, "avatar_color": "javascript:bad"},
        headers={"X-CSRFToken": csrf},
    )

    assert response.status_code == 400


def test_profile_preferences_merge_without_erasing_previous_choices(client, make_user):
    client_id = _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile").get_data(as_text=True))["csrf"]

    first = client.post(
        "/api/student/profile/preferences",
        json={"profile_public": False},
        headers={"X-CSRFToken": csrf},
    )
    second = client.post(
        "/api/student/profile/preferences",
        json={"allow_requests": False},
        headers={"X-CSRFToken": csrf},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    settings = json.loads(get_mail_preferences(client_id))
    assert settings["profile_public"] is False
    assert settings["allow_requests"] is False


def test_profile_preferences_reject_non_boolean_values(client, make_user):
    _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile").get_data(as_text=True))["csrf"]

    response = client.post(
        "/api/student/profile/preferences",
        json={"profile_public": "false"},
        headers={"X-CSRFToken": csrf},
    )

    assert response.status_code == 400


def test_profile_privacy_rank_and_presence_choices_are_enforced(client, make_user):
    target = make_user(name="Private Student")
    viewer = _student(client, make_user, name="Profile Viewer")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1, last_seen_at = %s WHERE id = %s", (int(time.time()), target))
        _exec(db, "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s)", (target, "test", 100, "privacy"))
    update_mail_preferences(target, json.dumps({
        "profile_public": False,
        "appear_in_rankings": False,
        "show_online": False,
    }))

    assert client.get(f"/api/academic/user/{target}").status_code == 404
    assert all(row["client_id"] != target for row in academic.leaderboard("global", viewer))
    sdb.add_friend(viewer, target)
    sdb.add_friend(target, viewer)
    friend = next(row for row in sdb.list_friends(viewer)["friends"] if row["id"] == target)
    assert friend["online"] is False


def test_profile_payload_uses_weighted_grade_and_selected_banner(client, make_user):
    client_id = _student(client, make_user)
    course_id = sdb.create_manual_course(client_id, "Cálculo", "MAT101")
    sdb.save_exams(client_id, course_id, [
        {"name": "Quiz", "weight_pct": 10},
        {"name": "Examen", "weight_pct": 60},
    ])
    with get_db() as db:
        exams = list(db.execute("SELECT id FROM student_exams WHERE course_id = ? ORDER BY id", (course_id,)))
        _exec(db, "UPDATE student_exams SET grade = %s WHERE id = %s", (7.0, exams[0]["id"]))
        _exec(db, "UPDATE student_exams SET grade = %s WHERE id = %s", (4.0, exams[1]["id"]))
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(db, "UPDATE student_wallet SET selected_banner = %s WHERE client_id = %s", ("cherry", client_id))

    body = client.get("/student/profile").get_data(as_text=True)
    profile = _app_payload(body)["profile"]

    assert profile["courses"][0]["grade"] == "4,4"
    assert profile["cover_anim_class"] == "bnr-anim-cherry"
    assert "@keyframes bnr-cherry" in body


def _other_student(make_user, *, name, xp=0):
    """A second student, set up far enough to appear on a profile page."""
    other = make_user(name=name)
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (other,))
        if xp:
            _exec(
                db,
                "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s)",
                (other, "test", xp, "public profile"),
            )
    return other


def test_another_students_profile_uses_the_live_design_not_the_legacy_markup(client, make_user):
    """/student/profile/<id> used to render its own dark inline page, because
    the design table in _s_render can only key on exact paths and this one
    carries an id. Same design as everywhere else now, over public facts only."""
    _student(client, make_user, name="Profile Viewer")
    other = _other_student(make_user, name="Rival Student", xp=4200)

    response = client.get(f"/student/profile/{other}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/static/machreach_app/perfil-publico.bundle.min.js" in body
    assert "/static/machreach_app/app/profile.css" in body
    # The legacy page's markup and its private-to-you bundle are both gone.
    assert "mr-prof" not in body
    assert "/static/machreach_app/perfil.bundle.min.js" not in body

    profile = _app_payload(body)["public_profile"]
    assert profile["name"] == "Rival Student"
    assert profile["xp"] == 4200
    assert profile["level_number"] > 1
    # Nothing the public-profile gate withholds may ride along in the payload.
    assert {"email", "bio", "joined_at", "created_at", "courses", "heat", "preferences"}.isdisjoint(profile)


def test_another_students_focus_totals_stay_friend_gated_on_the_page(client, make_user):
    viewer = _student(client, make_user, name="Focus Viewer")
    other = _other_student(make_user, name="Focus Target", xp=200)
    sdb.save_focus_session(other, "pomodoro", 45, 0)

    stranger = _app_payload(client.get(f"/student/profile/{other}").get_data(as_text=True))["public_profile"]
    assert stranger["focus_private"] is True
    assert stranger["total_minutes"] is None

    sdb.add_friend(viewer, other)
    sdb.add_friend(other, viewer)

    friend = _app_payload(client.get(f"/student/profile/{other}").get_data(as_text=True))["public_profile"]
    assert friend["focus_private"] is False
    assert friend["total_minutes"] == 45


def test_a_profile_you_may_not_see_answers_the_same_way_as_one_that_is_missing(client, make_user):
    """Missing, blocked and switched-off must be indistinguishable, or the
    difference tells a blocked viewer they were blocked."""
    viewer = _student(client, make_user, name="Blocked Viewer")
    hidden = _other_student(make_user, name="Hidden Student", xp=100)
    update_mail_preferences(hidden, json.dumps({"profile_public": False}))
    blocked = _other_student(make_user, name="Blocking Student", xp=100)
    sdb.block_student(blocked, viewer)

    missing_response = client.get("/student/profile/99887764")
    hidden_response = client.get(f"/student/profile/{hidden}")
    blocked_response = client.get(f"/student/profile/{blocked}")

    for response in (missing_response, hidden_response, blocked_response):
        assert response.status_code == 404
        body = response.get_data(as_text=True)
        assert "Perfil no encontrado" in body
        assert "public_profile" not in body

    # Byte-identical once the per-response CSP nonce is normalised — the nonce
    # is minted fresh for every response and says nothing about the student.
    def _stable(response):
        return re.sub(r'nonce="[^"]+"', 'nonce=""', response.get_data(as_text=True))

    assert _stable(hidden_response) == _stable(blocked_response) == _stable(missing_response)


def test_your_own_id_lands_on_your_own_profile_rather_than_the_public_view(client, make_user):
    """The public view strips out your email, courses and edit button, so
    /student/profile/<your id> has to hand you the real thing."""
    client_id = _student(client, make_user, name="Self Viewer")

    redirected = client.get(f"/student/profile/{client_id}")

    assert redirected.status_code == 302
    assert redirected.headers["Location"].endswith("/student/profile")
    own = client.get("/student/profile")
    assert "/static/machreach_app/perfil.bundle.min.js" in own.get_data(as_text=True)
