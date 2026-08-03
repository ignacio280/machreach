import base64
import io
import json
import re

from machreach_core.db import _exec, get_db
from student import db as sdb

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _student(client, make_user, *, name="Edit Student"):
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


def _upload(client, csrf, data=PNG_1PX, filename="avatar.png"):
    return client.post(
        "/api/student/profile/picture",
        data={"photo": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf},
    )


def test_edit_page_serves_the_supplied_design_with_unlocked_cosmetics(client, make_user):
    client_id = _student(client, make_user, name="Editora Perfil")
    sdb.get_wallet(client_id)

    response = client.get("/student/profile/edit")
    body = response.get_data(as_text=True)
    data = _app_payload(body)

    assert response.status_code == 200
    assert "/static/machreach_app/perfil-editar.bundle.min.js" in body
    assert "/static/machreach_app/app/profedit.css" in body
    edit = data["profile_edit"]
    assert edit["name"] == "Editora Perfil"
    assert edit["selected_banner"] == "default"
    assert [b["key"] for b in edit["banners"]] == ["default"]
    assert [f["key"] for f in edit["flags"]] == ["none"]


def test_edit_page_never_offers_a_cosmetic_the_student_has_not_unlocked(client, make_user):
    client_id = _student(client, make_user)
    sdb.get_wallet(client_id)
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_wallet SET unlocked_banners = %s WHERE client_id = %s",
            (json.dumps(["default", "ocean"]), client_id),
        )

    edit = _app_payload(client.get("/student/profile/edit").get_data(as_text=True))["profile_edit"]

    keys = {b["key"] for b in edit["banners"]}
    assert keys == {"default", "ocean"}
    assert "galaxy" not in keys


def test_profile_picture_round_trips_through_upload_serve_and_delete(client, make_user):
    client_id = _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile/edit").get_data(as_text=True))["csrf"]

    upload = _upload(client, csrf)
    assert upload.status_code == 200
    assert upload.get_json()["ok"] is True

    served = client.get(f"/student/profile/picture/{client_id}")
    assert served.status_code == 200
    assert served.headers["Content-Type"] == "image/png"
    assert served.data == PNG_1PX

    # The avatar URL is versioned so a replacement busts the browser cache.
    payload = _app_payload(client.get("/student/profile").get_data(as_text=True))
    assert payload["profile"]["picture_url"].startswith(f"/student/profile/picture/{client_id}?v=")
    assert payload["avatar_url"] == payload["profile"]["picture_url"]

    removed = client.delete("/api/student/profile/picture", headers={"X-CSRFToken": csrf})
    assert removed.status_code == 200
    assert client.get(f"/student/profile/picture/{client_id}").status_code == 404


def test_profile_picture_upload_rejects_non_images_and_oversized_files(client, make_user):
    _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile/edit").get_data(as_text=True))["csrf"]

    # A script that merely claims to be a PNG: the magic bytes decide.
    disguised = _upload(client, csrf, data=b"<?php echo 1; ?>", filename="evil.png")
    oversized = _upload(client, csrf, data=PNG_1PX + b"\0" * (sdb.PROFILE_PICTURE_MAX_BYTES + 1))

    assert disguised.status_code == 400
    assert oversized.status_code == 400


def test_profile_picture_is_not_served_to_anonymous_visitors(client, make_user):
    client_id = _student(client, make_user)
    csrf = _app_payload(client.get("/student/profile/edit").get_data(as_text=True))["csrf"]
    assert _upload(client, csrf).status_code == 200

    with client.session_transaction() as session:
        session.clear()

    assert client.get(f"/student/profile/picture/{client_id}").status_code == 401
    # Uploading without a session never reaches the handler — CSRF stops it first.
    assert client.post("/api/student/profile/picture").status_code == 400
