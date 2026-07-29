"""HTTP contracts for verified, idempotent focus-session claims."""

from datetime import UTC, datetime, timedelta

from machreach_core.db import _exec, get_db
from student import db as sdb


def _login(client, client_id: int, name: str = "Focus Route Student") -> None:
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


def _backdate_phase(client_id: int, phase_id: str, minutes: int = 25) -> None:
    instant = datetime.now(UTC) - timedelta(minutes=minutes)
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_focus_phases SET started_at = %s, started_at_utc = %s "
            "WHERE client_id = %s AND phase_id = %s",
            (instant.replace(tzinfo=None), instant, client_id, phase_id),
        )


def test_focus_phase_and_save_routes_reject_untrusted_claims(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    assert client.post("/api/student/focus/phase/start", json={}).status_code == 401
    assert client.post("/api/student/focus/save", json={}).status_code == 401
    assert client.post("/api/student/focus/claim", json={}).status_code == 401
    assert client.post("/api/student/focus/claim-adjust", json={}).status_code == 401

    client_id = make_user("Focus Route Student")
    _login(client, client_id)
    disabled_adjustment = client.post(
        "/api/student/focus/claim-adjust",
        json={"claim_id": "legacy", "total_minutes": 100},
    )
    assert disabled_adjustment.status_code == 410

    missing_phase = client.post(
        "/api/student/focus/phase/start",
        json={"phase_id": "***", "course_id": "bad", "exam_id": "bad"},
    )
    assert missing_phase.status_code == 400
    assert missing_phase.get_json()["error"] == "Missing phase id"

    missing_course = client.post(
        "/api/student/focus/phase/start",
        json={"phase_id": "phase-no-course", "expected_minutes": "bad"},
    )
    assert missing_course.status_code == 400
    assert "Pick a course" in missing_course.get_json()["error"]

    course_id = sdb.create_manual_course(client_id, "Focus Integrity", code="FOC101")
    started = client.post(
        "/api/student/focus/phase/start",
        json={
            "phaseId": "phase:verified",
            "expected_minutes": 999,
            "courseId": course_id,
            "examId": "bad",
        },
    )
    assert started.status_code == 200
    assert started.get_json()["expected_minutes"] == 480

    no_course = client.post(
        "/api/student/focus/save",
        json={"minutes": 20, "phase_id": "phase:verified"},
    )
    assert no_course.status_code == 400

    invalid_minutes = client.post(
        "/api/student/focus/save",
        json={
            "minutes": -1,
            "pages": "bad",
            "course_id": course_id,
            "phase_id": "phase:verified",
        },
    )
    assert invalid_minutes.status_code == 400
    assert invalid_minutes.get_json()["error"] == "Invalid minutes"

    empty = client.post(
        "/api/student/focus/save",
        json={"minutes": "bad", "pages": -5, "course_id": course_id},
    )
    assert empty.get_json() == {"ok": True, "saved": False, "reason": "empty"}

    unverified = client.post(
        "/api/student/focus/save",
        json={"minutes": 20, "course_id": course_id},
    )
    assert unverified.status_code == 400
    assert unverified.get_json()["reason"] == "missing-phase-id"


def test_focus_save_is_atomic_and_duplicate_safe(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Atomic Focus Route")
    course_id = sdb.create_manual_course(client_id, "Atomic Focus", code="ATM101")
    _login(client, client_id, "Atomic Focus Route")

    phase_id = "atomic-route-phase"
    assert client.post(
        "/api/student/focus/phase/start",
        json={
            "phase_id": phase_id,
            "expected_minutes": 20,
            "course_id": course_id,
        },
    ).status_code == 200
    _backdate_phase(client_id, phase_id)

    saved = client.post(
        "/api/student/focus/save?local_date=2099-01-01",
        json={
            "minutes": 20,
            "pages": 3,
            "course_id": course_id,
            "phase_id": phase_id,
        },
    )
    payload = saved.get_json()
    assert saved.status_code == 200
    assert payload["saved"] is True
    assert payload["minutes_saved"] == 20
    assert payload["xp_awarded"] == 10
    assert payload["coins_awarded"] == 2

    duplicate = client.post(
        "/api/student/focus/save",
        json={
            "minutes": 20,
            "course_name": "Atomic Focus",
            "phase_id": phase_id,
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["reason"] == "duplicate-phase"
    assert sdb.get_focus_stats(client_id)["total_minutes"] == 20

    pages_only = client.post(
        "/api/student/focus/save",
        json={"minutes": 0, "pages": 5, "course_name": "Atomic Focus"},
    )
    assert pages_only.status_code == 200
    assert pages_only.get_json()["pages_saved"] == 5


def test_focus_batch_claim_classifies_skips_duplicates_and_retryable_failures(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Batch Focus Route")
    course_id = sdb.create_manual_course(client_id, "Batch Focus", code="BAT101")
    _login(client, client_id, "Batch Focus Route")

    for phase_id in ("batch-ok", "batch-fail"):
        sdb.start_focus_phase(
            client_id,
            phase_id,
            expected_minutes=20,
            course_id=course_id,
        )
        _backdate_phase(client_id, phase_id)

    real_claim = sdb.claim_focus_phase_rewards

    def controlled_claim(client_id_arg, phase_id, **kwargs):
        if phase_id == "batch-fail":
            raise RuntimeError("simulated transient database failure")
        return real_claim(client_id_arg, phase_id, **kwargs)

    monkeypatch.setattr(sdb, "claim_focus_phase_rewards", controlled_claim)
    phases = [
        "not-an-object",
        {"minutes": 0},
        {"minutes": 20, "phase_id": "missing-course"},
        {"minutes": 20, "course_id": course_id},
        {
            "minutes": 20,
            "phase_id": "batch-ok",
            "course_id": course_id,
        },
        {
            "minutes": 20,
            "phase_id": "batch-fail",
            "course_name": "Batch Focus",
        },
    ]
    response = client.post(
        "/api/student/focus/claim",
        json={
            "phases": phases,
            "fallback_exam_id": "bad",
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["saved_count"] == 1
    assert payload["skipped_count"] == 4
    assert payload["failed_count"] == 1
    assert payload["minutes_saved"] == 20
    assert payload["retryable_phases"] == [phases[-1]]

    duplicate = client.post(
        "/api/student/focus/claim",
        json={
            "phases": [{
                "minutes": 20,
                "phase_id": "batch-ok",
                "course_name": "Batch Focus",
            }],
        },
    )
    assert duplicate.get_json()["duplicate_count"] == 1
