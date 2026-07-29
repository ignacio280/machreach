"""End-to-end contracts for student-owned courses, exams, and study sources."""

from io import BytesIO

from machreach_core.db import _exec, get_db


def _login(client, client_id: int, name: str = "Course Journey") -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0


def test_course_exam_source_and_progress_http_lifecycle(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Course Journey")
    _login(client, client_id)

    assert client.get("/api/student/courses").get_json() == {"courses": []}
    assert client.post(
        "/api/student/courses/manual", json={"name": ""}
    ).status_code == 400
    assert client.post(
        "/api/student/courses/manual",
        json={"name": "x" * 161, "code": "LONG"},
    ).status_code == 400

    created = client.post(
        "/api/student/courses/manual",
        json={"name": "Data Structures", "code": "CS201"},
    )
    assert created.status_code == 200
    course_id = created.get_json()["id"]

    course = client.get(f"/api/student/courses/{course_id}")
    assert course.status_code == 200
    assert course.get_json()["name"] == "Data Structures"
    assert client.get("/api/student/courses/99999999").status_code == 404

    updated = client.put(
        f"/api/student/courses/{course_id}",
        json={
            "name": "Advanced Data Structures",
            "code": "CS-201",
            "grading": {"midterm": 40},
            "weekly_schedule": [{"week": 1, "topic": "Trees"}],
            "study_tips": ["Practice implementations"],
        },
    )
    assert updated.get_json() == {"ok": True}
    assert client.get(f"/api/student/courses/{course_id}").get_json()[
        "name"
    ] == "Advanced Data Structures"

    assert client.post(
        f"/api/student/courses/{course_id}/exams", json={"name": ""}
    ).status_code == 400
    exam_response = client.post(
        f"/api/student/courses/{course_id}/exams",
        json={
            "name": "Midterm",
            "exam_date": "2099-08-10",
            "weight_pct": 150,
            "topics": ["Trees", "Graphs"],
            "grade": "8.0",
        },
    )
    assert exam_response.status_code == 200
    exam_id = exam_response.get_json()["id"]

    exams = client.get(f"/api/student/courses/{course_id}/exams").get_json()[
        "exams"
    ]
    assert exams[0]["topics"] == ["Trees", "Graphs"]
    assert client.get("/api/student/exams?upcoming=false").status_code == 200
    assert client.get("/api/student/exams?upcoming=true").status_code == 200

    assert client.put(
        f"/api/student/exams/{exam_id}",
        json={"name": "", "course_id": course_id},
    ).status_code == 400
    assert client.put(
        f"/api/student/exams/{exam_id}",
        json={
            "name": "Midterm Updated",
            "course_id": course_id,
            "exam_date": "2099-08-11",
            "weight_pct": 40,
            "topics": ["Graphs"],
            "grade": "not-a-grade",
        },
    ).get_json() == {"ok": True}

    assert client.get(
        f"/api/student/courses/{course_id}/files?exam_id=bad"
    ).status_code == 400
    assert client.get(
        f"/api/student/courses/{course_id}/files?exam_id=999999"
    ).status_code == 400
    assert client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"exam_id": "bad", "source_text": "x" * 30},
    ).status_code == 400
    assert client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"exam_id": "999999", "source_text": "x" * 30},
    ).status_code == 400
    assert client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"source_text": "too short"},
    ).status_code == 400
    assert client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"file": (BytesIO(b"unsupported"), "material.exe")},
        content_type="multipart/form-data",
    ).status_code == 400

    source_response = client.post(
        f"/api/student/courses/{course_id}/sources",
        data={
            "exam_id": str(exam_id),
            "title": "Tree Notes",
            "source_text": (
                "Balanced search trees maintain logarithmic height through rotations."
            ),
        },
    )
    assert source_response.status_code == 200
    source_id = source_response.get_json()["id"]
    assert source_response.get_json()["char_count"] > 20

    sources = client.get(
        f"/api/student/courses/{course_id}/files?exam_id={exam_id}"
    ).get_json()["files"]
    assert sources[0]["id"] == source_id
    preview = client.get(f"/api/student/files/{source_id}").get_json()
    assert preview["name"] == "Tree Notes"
    assert "Balanced search trees" in preview["text"]
    assert client.get("/api/student/files/99999999").status_code == 404

    benchmark = client.get(
        f"/api/student/courses/{course_id}/benchmark"
    ).get_json()
    assert benchmark["has_data"] is False
    assert "same canonical course" in benchmark["methodology"]
    outcome = client.post(
        f"/api/student/courses/{course_id}/outcome",
        json={"final_grade": 6.1, "passing_grade": 4.0},
    )
    assert outcome.status_code in (200, 400)

    assert client.post(
        "/api/student/progress/complete",
        json={"date": "2099-08-01", "notes": "Completed review"},
    ).status_code == 200
    assert client.get("/api/student/stats").status_code == 200

    assert client.delete(f"/api/student/files/{source_id}").get_json() == {
        "ok": True
    }
    assert client.delete(f"/api/student/files/{source_id}").status_code == 404
    assert client.delete(f"/api/student/exams/{exam_id}").get_json() == {
        "ok": True
    }
    assert client.post(
        f"/api/student/courses/{course_id}/upload"
    ).status_code == 410
    assert client.delete(f"/api/student/courses/{course_id}").get_json() == {
        "ok": True
    }
    assert client.delete(f"/api/student/courses/{course_id}").status_code == 404


def test_course_http_resources_are_tenant_isolated(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Course Owner")
    attacker_id = make_user("Course Attacker")
    _login(client, owner_id, "Course Owner")
    course_id = client.post(
        "/api/student/courses/manual",
        json={"name": "Private Algorithms", "code": "ALG401"},
    ).get_json()["id"]
    exam_id = client.post(
        f"/api/student/courses/{course_id}/exams",
        json={"name": "Private Final", "weight_pct": 50},
    ).get_json()["id"]
    source_id = client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"source_text": "Private material " * 4},
    ).get_json()["id"]

    _login(client, attacker_id, "Course Attacker")
    for path in (
        f"/api/student/courses/{course_id}",
        f"/api/student/courses/{course_id}/exams",
        f"/api/student/courses/{course_id}/files",
        f"/api/student/courses/{course_id}/benchmark",
    ):
        assert client.get(path).status_code == 404
    assert client.put(
        f"/api/student/courses/{course_id}", json={"name": "Stolen"}
    ).status_code == 404
    assert client.post(
        f"/api/student/courses/{course_id}/sources",
        data={"source_text": "attacker material " * 3},
    ).status_code == 404
    assert client.post(
        f"/api/student/courses/{course_id}/outcome",
        json={"final_grade": 7},
    ).status_code == 404
    assert client.get(f"/api/student/files/{source_id}").status_code == 404
    assert client.delete(f"/api/student/files/{source_id}").status_code == 404
    assert client.delete(f"/api/student/courses/{course_id}").status_code == 404

    # Deleting an exam is tenant-scoped even though the compatibility endpoint
    # intentionally returns a stable success shape.
    assert client.delete(f"/api/student/exams/{exam_id}").get_json() == {
        "ok": True
    }
    _login(client, owner_id, "Course Owner")
    assert len(
        client.get(f"/api/student/courses/{course_id}/exams").get_json()["exams"]
    ) == 1
