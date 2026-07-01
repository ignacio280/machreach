from student import db as sdb
from student.canvas import make_connect_token


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
