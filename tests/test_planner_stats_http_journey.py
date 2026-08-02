"""Planner and study-stat routes preserve ownership and recover from AI failure."""

import base64
from datetime import date, timedelta
import json
import re

from machreach_core.db import _exec, get_db
from student import ai_usage, analyzer, db as sdb
from student import subscription


def _login(client, client_id: int, name: str = "Planner Student") -> None:
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


def _plus(client_id: int) -> None:
    subscription.set_subscription_state(
        client_id,
        tier="plus",
        status="active",
        ls_sub_id=f"sub-planner-{client_id}",
    )


def _app_payload(response) -> dict:
    body = response.get_data(as_text=True)
    encoded = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', body).group(1)
    return json.loads(base64.b64decode(encoded))


def test_live_design_focus_planner_and_grade_sheet_are_account_backed(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Live Design Student")
    course_id = sdb.create_manual_course(client_id, "Live Calculus", "MAT200")
    exam_id = sdb.upsert_exam(
        client_id,
        course_id,
        None,
        name="Live Midterm",
        exam_date=(date.today() + timedelta(days=30)).isoformat(),
        weight_pct=40,
        topics=["Limits"],
    )
    with get_db() as db:
        _exec(db, "UPDATE student_exams SET status = 'upcoming' WHERE id = %s", (exam_id,))
    sdb.save_focus_session(
        client_id, "pomodoro", 25, 0, "Live Calculus", course_id, exam_id
    )
    _login(client, client_id, "Live Design Student")
    _plus(client_id)

    courses = _app_payload(client.get("/student/courses"))
    assert courses["courses"]["items"][0]["name"] == "Live Calculus"
    assert "total_sessions" in courses["courses"]

    focus = _app_payload(client.get("/student/focus"))["focus"]
    assert "total_minutes" in focus["stats"]
    assert focus["next_exam"]["name"] == "Live Midterm"
    assert focus["benchmark"]["has_data"] is False

    settings = [
        {"day_of_week": day, "available_hours": 2, "is_free_day": False}
        for day in range(7)
    ]
    assert client.post(
        "/api/student/planner/availability", json={"settings": settings}
    ).status_code == 200
    generated = client.post("/api/student/planner/regenerate", json={})
    assert generated.status_code == 200
    plan = generated.get_json()["plan"]
    assert len(plan["days"]) == 7
    assert any(day["blocks"] for day in plan["days"])
    planner = _app_payload(client.get("/student/planner"))["plan"]
    assert planner["week_start"] == plan["week_start"]

    sheet = {
        "current": 0,
        "sems": [{"label": "I", "courses": [{
            "id": "c1", "name": "Live Calculus", "credits": 10, "evals": []
        }]}],
    }
    saved = client.post("/api/student/grades/sheet", json={"sheet": sheet})
    assert saved.status_code == 200
    assert sdb.get_grade_sheet(client_id) == sheet
    assert _app_payload(client.get("/student/gpa"))["grades"]["sheet"] == sheet


def test_grade_sheet_rejects_invalid_or_oversized_shapes(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Invalid Sheet Student")
    _login(client, client_id, "Invalid Sheet Student")
    assert client.post("/api/student/grades/sheet", json={"sheet": []}).status_code == 400
    assert client.post(
        "/api/student/grades/sheet",
        json={"sheet": {"sems": [{}]}},
    ).status_code == 400
    assert client.post(
        "/api/student/grades/sheet",
        json={"sheet": {"sems": [{"courses": [{}]}]}},
    ).status_code == 400


def test_stats_dashboard_semester_and_period_contracts(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Stats Journey")
    course_id = sdb.create_manual_course(client_id, "Statistics", "STA101")
    exam_id = sdb.upsert_exam(
        client_id,
        course_id,
        None,
        name="Final",
        exam_date="2099-12-01",
        weight_pct=50,
        topics=["Probability"],
    )
    sdb.save_focus_session(
        client_id,
        "pomodoro",
        25,
        2,
        "Statistics",
        course_id,
        exam_id,
    )
    _login(client, client_id, "Stats Journey")

    assert client.get("/api/student/stats/per_course").get_json()[
        "courses"
    ][0]["minutes"] == 25
    assert client.get(
        "/api/student/stats/course_detail?course_id=bad"
    ).status_code == 400
    assert client.get(
        "/api/student/stats/course_detail?course_id=999999"
    ).status_code == 404
    course_detail = client.get(
        f"/api/student/stats/course_detail?course_id={course_id}"
        "&week_offset=5"
    ).get_json()
    assert course_detail["course"]["id"] == course_id
    assert course_detail["week_offset"] == 0
    assert course_detail["exams"][0]["exam_id"] == exam_id

    assert client.get(
        "/api/student/stats/exam_detail?exam_id=bad"
    ).status_code == 400
    assert client.get(
        "/api/student/stats/exam_detail?exam_id=999999"
    ).status_code == 404
    exam_detail = client.get(
        f"/api/student/stats/exam_detail?exam_id={exam_id}"
        "&week_offset=bad"
    ).get_json()
    assert exam_detail["exam"]["course_id"] == course_id
    assert len(exam_detail["days"]) == 7

    dashboard = client.get("/api/student/dashboard").get_json()
    assert dashboard["canvas_connected"] is True
    assert dashboard["courses"][0]["id"] == course_id
    assert dashboard["upcoming_exams"][0]["days_until"] is not None

    assert client.get("/api/student/semester/current").get_json() == {
        "current": ""
    }
    assert client.post(
        "/api/student/semester/current", json={"label": ""}
    ).status_code == 400
    assert client.post(
        "/api/student/semester/current", json={"label": "2099-1"}
    ).get_json()["current"] == "2099-1"
    assert client.get("/api/student/courses/by-semester").status_code == 200

    assert client.post(
        "/api/student/period/ack", json={"period_kind": "bad"}
    ).status_code == 400
    assert client.get("/api/student/period/results").status_code == 200


def test_plus_planner_save_availability_and_completion(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Planner Persistence")
    _plus(client_id)
    _login(client, client_id, "Planner Persistence")

    assert client.post(
        "/api/student/planner/availability", json={"settings": "invalid"}
    ).status_code == 400
    availability = client.post(
        "/api/student/planner/availability",
        json={
            "settings": [
                {
                    "day_of_week": 1,
                    "available_hours": 2.5,
                    "is_free_day": False,
                },
                {
                    "day_of_week": 2,
                    "available_hours": 0,
                    "is_free_day": True,
                },
            ],
        },
    )
    assert availability.status_code == 200
    assert len(availability.get_json()["settings"]) == 2

    assert client.post(
        "/api/student/planner/save", json={"plan": "invalid"}
    ).status_code == 400
    saved = client.post(
        "/api/student/planner/save",
        json={
            "plan": {
                "daily_plan": [{
                    "date": date.today().isoformat(),
                    "sessions": [],
                }],
            },
            "preferences": "invalid-preferences",
        },
    ).get_json()
    assert saved["ok"] is True
    assert saved["plan_id"] > 0

    assert client.post(
        "/api/student/planner/block-done", json={"date": "bad"}
    ).status_code == 400
    done_payload = {
        "date": "2099-08-01",
        "exam_id": 0,
        "unit_index": 1,
        "title": "Trees",
        "phase": "review",
        "minutes": 25,
        "done": True,
    }
    assert client.post(
        "/api/student/planner/block-done", json=done_payload
    ).get_json() == {"ok": True}
    assert sdb.planner_done_blocks(client_id)[0]["title"] == "Trees"
    done_payload["done"] = False
    assert client.post(
        "/api/student/planner/block-done", json=done_payload
    ).get_json() == {"ok": True}
    assert sdb.planner_done_blocks(client_id) == []


def test_planner_material_fallback_and_block_tool_artifacts(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Planner AI")
    _plus(client_id)
    course_id = sdb.create_manual_course(client_id, "Algorithms", "ALG202")
    exam_id = sdb.upsert_exam(
        client_id,
        course_id,
        None,
        name="Algorithms Final",
        exam_date="2099-12-01",
        weight_pct=60,
        topics=["Trees"],
    )
    sdb.add_course_file(
        client_id,
        course_id,
        "algorithms.txt",
        "txt",
        (
            "AVL trees maintain logarithmic height using rotations. "
            "Breadth-first search explores a graph level by level. "
        )
        * 80,
        exam_id=exam_id,
    )
    _login(client, client_id, "Planner AI")

    assert client.post(
        "/api/student/planner/material-blocks",
        json={"course_id": "bad", "exam_id": "bad"},
    ).status_code == 400
    assert client.post(
        "/api/student/planner/material-blocks",
        json={"course_id": 999999, "exam_id": exam_id},
    ).status_code == 404
    assert client.post(
        "/api/student/planner/material-blocks",
        json={"course_id": course_id, "exam_id": 999999},
    ).status_code == 404

    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(
        ai_usage, "reserve", lambda *args, **kwargs: {"status": "reserved"}
    )
    monkeypatch.setattr(ai_usage, "settle", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_usage, "fail", lambda *args, **kwargs: None)
    fallback = client.post(
        "/api/student/planner/material-blocks",
        headers={"X-Idempotency-Key": "planner-material-fallback"},
        json={
            "course_id": course_id,
            "exam_id": exam_id,
            "target_units": "bad",
        },
    )
    assert fallback.status_code == 200
    material = fallback.get_json()
    assert material["ok"] is True
    assert material["units"]
    assert material["files"][0]["name"] == "algorithms.txt"
    assert "extracto" in material["units"][0]["page_hint"]

    assert client.post(
        "/api/student/planner/block-tool",
        json={"tool": "notes"},
    ).status_code == 400
    assert client.post(
        "/api/student/planner/block-tool",
        json={"tool": "quiz", "course_id": "bad", "exam_id": "bad"},
    ).status_code == 400
    assert client.post(
        "/api/student/planner/block-tool",
        json={"tool": "quiz", "course_id": 999999, "exam_id": exam_id},
    ).status_code == 404
    assert client.post(
        "/api/student/planner/block-tool",
        json={"tool": "quiz", "course_id": course_id, "exam_id": 999999},
    ).status_code == 404
    assert client.post(
        "/api/student/planner/block-tool",
        json={"tool": "quiz", "course_id": course_id, "exam_id": exam_id},
    ).status_code == 400

    monkeypatch.setattr(
        subscription, "can_generate_quiz_today", lambda cid: (True, "")
    )
    monkeypatch.setattr(
        subscription, "can_generate_flashcards_today", lambda cid: (True, "")
    )
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(subscription, "cap_cards", lambda cid, count: count)
    block_tool_view = flask_app.view_functions[
        "student_planner_block_tool_api"
    ].__wrapped__
    closure = dict(zip(block_tool_view.__code__.co_freevars, block_tool_view.__closure__))
    monkeypatch.setattr(
        closure["generate_quiz"],
        "cell_contents",
        lambda **kwargs: [{
            "topic": "Trees",
            "question": "Why rotate?",
            "option_a": "Balance",
            "option_b": "Sort",
            "option_c": "Hash",
            "option_d": "Delete",
            "correct": "a",
            "explanation": "Rotations restore balance.",
        }],
    )
    monkeypatch.setattr(
        closure["generate_flashcards"],
        "cell_contents",
        lambda **kwargs: [{"front": "AVL rotation", "back": "Restores balance"}],
    )
    from app import limiter as app_limiter

    app_limiter.reset()

    common = {
        "course_id": course_id,
        "exam_id": exam_id,
        "source_text": "AVL trees use rotations to maintain balance.",
        "block_title": "AVL Trees",
    }
    quiz = client.post(
        "/api/student/planner/block-tool",
        headers={"X-Idempotency-Key": "planner-quiz-artifact"},
        json={**common, "tool": "quiz"},
    ).get_json()
    assert quiz["ok"] is True
    assert quiz["type"] == "quiz"

    cards = client.post(
        "/api/student/planner/block-tool",
        headers={"X-Idempotency-Key": "planner-card-artifact"},
        json={**common, "tool": "flashcards"},
    ).get_json()
    assert cards["ok"] is True
    assert cards["type"] == "flashcards"
