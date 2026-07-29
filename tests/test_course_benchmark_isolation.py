from machreach_core.db import _exec, _fetchall, _fetchone, get_db
from student import db as sdb


def _outcome(make_user, university_id: int, semester: str, grade: float) -> int:
    client_id = make_user()
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET university_id = %s, current_semester = %s "
            "WHERE id = %s",
            (university_id, semester, client_id),
        )
    sdb.record_course_to_catalog(university_id, "MAT101", "Cálculo I")
    course_id = sdb.add_manual_course(client_id, "MAT101", "Cálculo I")
    assert sdb.record_course_outcome(client_id, course_id, grade)["ok"]
    return course_id


def test_benchmark_only_uses_same_university_canonical_course_and_cohort(make_user):
    with get_db() as db:
        universities = _fetchall(db, "SELECT id FROM universities ORDER BY id LIMIT 2")
    assert len(universities) == 2
    first_uni = int(universities[0]["id"])
    other_uni = int(universities[1]["id"])

    target = 0
    for _ in range(5):
        target = _outcome(make_user, first_uni, "2026-2", 6.0)
    for _ in range(5):
        _outcome(make_user, other_uni, "2026-2", 2.0)
    for _ in range(5):
        _outcome(make_user, first_uni, "2025-2", 2.0)

    benchmark = sdb.get_course_success_benchmark(target)

    assert benchmark["has_data"] is False  # no focus minutes were reported
    assert benchmark["total_reports"] == 5
    assert benchmark["passed_count"] == 5
    assert benchmark["avg_final_grade"] == 6.0
    assert benchmark["university_id"] == first_uni
    assert benchmark["cohort_version"] == "2026-2"
    assert benchmark["min_required"] == 5
    assert "at your university" in benchmark["methodology"]


def test_legacy_course_outcome_backfill_adds_university_canonical_and_cohort(make_user):
    with get_db() as db:
        university_id = int(
            _fetchone(db, "SELECT id FROM universities ORDER BY id LIMIT 1")["id"]
        )
    client_id = make_user()
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET university_id = %s, current_semester = %s "
            "WHERE id = %s",
            (university_id, "2026-2", client_id),
        )
    sdb.record_course_to_catalog(university_id, "MAT101", "Cálculo I")
    course_id = sdb.add_manual_course(client_id, "MAT101", "Cálculo I")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_course_outcomes "
            "(client_id, course_id, course_key, course_name, course_code, "
            "final_grade, passing_grade, passed, total_focus_minutes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                client_id,
                course_id,
                "mat101",
                "Cálculo I",
                "MAT101",
                5.5,
                3.95,
                True,
                120,
            ),
        )

    sdb._backfill_normalized_student_state()

    with get_db() as db:
        outcome = _fetchone(
            db,
            "SELECT university_id, canonical_course_id, cohort_version "
            "FROM student_course_outcomes WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        )
    assert outcome["university_id"] == university_id
    assert outcome["canonical_course_id"]
    assert outcome["cohort_version"] == "2026-2"


def test_outcome_post_does_not_leak_paid_benchmark(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Free Benchmark Reporter")
    course_id = sdb.create_manual_course(
        client_id, "Cálculo I", code="MAT101", term="2026-2"
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Free Benchmark Reporter"
        session["account_type"] = "student"
        session["session_version"] = 0

    response = client.post(
        f"/api/student/courses/{course_id}/outcome",
        json={"final_grade": 5.5, "passing_grade": 3.95},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "benchmark" not in payload
    assert "avg_hours" not in payload


def test_unknown_cohort_is_never_pooled(make_user):
    with get_db() as db:
        university_id = int(
            _fetchone(db, "SELECT id FROM universities ORDER BY id LIMIT 1")["id"]
        )
    client_id = make_user("Unknown Cohort")
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET university_id = %s, current_semester = '' "
            "WHERE id = %s",
            (university_id, client_id),
        )
    sdb.record_course_to_catalog(university_id, "MAT101", "Cálculo I")
    course_id = sdb.add_manual_course(client_id, "MAT101", "Cálculo I")
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_courses SET term = '', semester_label = '' WHERE id = %s",
            (course_id,),
        )

    assert sdb.record_course_outcome(client_id, course_id, 5.5)["ok"] is True
    benchmark = sdb.get_course_success_benchmark(course_id)

    assert benchmark["suppressed"] is True
    assert benchmark["reason"] == "cohort_required"
