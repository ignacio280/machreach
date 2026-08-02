import base64
import json
import re

from machreach_core.db import _exec, _fetchone, get_db, get_mail_preferences


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
    settings = json.loads(get_mail_preferences(client_id))["profile_settings"]
    assert settings["profile_public"] is False
    assert settings["allow_requests"] is False
