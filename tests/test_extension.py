from student import db as sdb
from student import canvas as canvas_mod
from student.canvas import make_connect_token, verify_connect_token


def test_canvas_extension_status_rejects_invalid_token(client):
    response = client.post(
        "/api/student/canvas/extension-status",
        json={"token": "not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_token"


def test_canvas_extension_status_only_counts_canvas_courses(client, make_user):
    client_id = make_user("Canvas Status Student")
    token = make_connect_token(client_id)

    empty_response = client.post(
        "/api/student/canvas/extension-status",
        json={"token": token},
    )
    assert empty_response.status_code == 200
    assert empty_response.get_json() == {"ok": True, "synced": False}

    sdb.add_manual_course(client_id, "MAN101", "Manual Course")
    manual_response = client.post(
        "/api/student/canvas/extension-status",
        json={"token": token},
    )
    assert manual_response.get_json() == {"ok": True, "synced": False}

    sdb.upsert_course(client_id, 12345, "Canvas Course", "CAN101", "2026-1")
    canvas_response = client.post(
        "/api/student/canvas/extension-status",
        json={"token": token},
    )
    assert canvas_response.get_json() == {"ok": True, "synced": True}


def test_canvas_extension_import_survives_www_redirect(client, make_user):
    client_id = make_user("Canvas Import Student")
    token = make_connect_token(client_id)

    redirect = client.post(
        "/api/student/canvas/extension-import",
        base_url="https://www.machreach.com",
        json={
            "token": token,
            "courses": [{
                "id": 98765,
                "name": "Ruta Novata UC 2024",
                "course_code": "CUR-RUT-NOV-1_2024",
                "term": {"name": "2024-1"},
            }],
        },
    )

    assert redirect.status_code == 308
    assert redirect.headers["Location"] == "https://machreach.com/api/student/canvas/extension-import"

    response = client.post(
        "/api/student/canvas/extension-import",
        base_url="https://machreach.com",
        json={
            "token": token,
            "courses": [{
                "id": 98765,
                "name": "Ruta Novata UC 2024",
                "course_code": "CUR-RUT-NOV-1_2024",
                "term": {"name": "2024-1"},
            }],
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "saved": 1}
    assert any(
        int(course["canvas_course_id"]) == 98765
        for course in sdb.get_courses(client_id)
    )


def test_canvas_extension_token_expires(monkeypatch, make_user):
    client_id = make_user("Expired Canvas Token Student")
    token = make_connect_token(client_id)

    monkeypatch.setattr(canvas_mod, "_EXT_CONNECT_MAX_AGE_SECONDS", -1)

    assert verify_connect_token(token) is None
