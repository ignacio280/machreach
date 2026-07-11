"""Canvas imports are complete snapshots, not append-only partial updates."""

from outreach.db import _exec, _fetchall, get_db
from student import db as sdb
from student.canvas import make_connect_token


def test_canvas_import_archives_courses_missing_from_next_snapshot(client, make_user):
    client_id = make_user("Canvas Student", "canvas-reconcile@example.test")
    token = make_connect_token(client_id)

    first = client.post(
        "/api/student/canvas/extension-import",
        json={
            "token": token,
            "courses": [
                {"id": 101, "name": "Mathematics", "course_code": "MAT101"},
                {"id": 202, "name": "History", "course_code": "HIS202"},
            ],
        },
    )
    assert first.status_code == 200
    assert first.get_json()["saved"] == 2

    second = client.post(
        "/api/student/canvas/extension-import",
        json={
            "token": token,
            "courses": [
                {"id": 202, "name": "Modern History", "course_code": "HIS202"},
            ],
        },
    )

    assert second.status_code == 200
    assert second.get_json()["archived"] == 1
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT canvas_course_id, name, canvas_active FROM student_courses "
            "WHERE client_id = %s ORDER BY canvas_course_id",
            (client_id,),
        )
    assert rows == [
        {"canvas_course_id": 101, "name": "Mathematics", "canvas_active": 0},
        {"canvas_course_id": 202, "name": "Modern History", "canvas_active": 1},
    ]
    assert [course["canvas_course_id"] for course in sdb.get_courses(client_id)] == [202]


def test_empty_canvas_snapshot_archives_all_canvas_courses(client, make_user):
    client_id = make_user("Empty Canvas", "canvas-empty@example.test")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_courses (client_id, canvas_course_id, name) VALUES (%s, %s, %s)",
            (client_id, 303, "Old course"),
        )

    response = client.post(
        "/api/student/canvas/extension-import",
        json={"token": make_connect_token(client_id), "courses": []},
    )

    assert response.status_code == 200
    assert response.get_json()["archived"] == 1
