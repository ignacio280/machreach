"""The AI planner reads real material, and its answer is never trusted as-is."""

import json
from datetime import date, timedelta

from machreach_core.db import _exec, get_db
from student import db as sdb, planner_ai, subscription


def _student(client, make_user, name="AI Planner"):
    client_id = make_user(name, f"{name.lower().replace(' ', '-')}@example.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    subscription.set_subscription_state(client_id, tier="plus", status="active", ls_sub_id=f"sub-ai-{client_id}")
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
    return client_id


def _exam_with_material(client_id, *, name, weight, days_out, text):
    course_id = sdb.create_manual_course(client_id, f"Curso {name}", "CUR100")
    exam_day = (date.today() + timedelta(days=days_out)).isoformat()
    sdb.upsert_exam(client_id, course_id, None, name=name, exam_date=exam_day,
                    weight_pct=weight, topics=[])
    exam_id = int(sdb.get_course_exams(course_id)[0]["id"])
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_course_files (client_id, course_id, exam_id, original_name, file_type, extracted_text) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (client_id, course_id, exam_id, f"{name}.pdf", "pdf", text),
        )
    return course_id, exam_id, exam_day


def test_context_carries_weight_date_and_uploaded_material(client, make_user):
    client_id = _student(client, make_user, "Context Student")
    _, exam_id, exam_day = _exam_with_material(
        client_id, name="Prueba 2", weight=40, days_out=6,
        text="Series de Taylor. Demostración del radio de convergencia. " * 40,
    )

    context = planner_ai.collect_exam_context(client_id, date.today())

    entry = next(row for row in context if row["exam_id"] == exam_id)
    assert entry["weight_pct"] == 40
    assert entry["days_left"] == 6
    assert entry["date"] == exam_day
    assert "radio de convergencia" in entry["material"][0]["text"]


def test_material_excerpts_stay_within_the_budget(client, make_user):
    client_id = _student(client, make_user, "Budget Student")
    _exam_with_material(client_id, name="Enorme", weight=10, days_out=10, text="x" * 500000)

    context = planner_ai.collect_exam_context(client_id, date.today())

    assert len(context[0]["material"][0]["text"]) <= planner_ai.CHARS_PER_FILE


def test_model_answer_is_validated_against_the_students_own_week(client, make_user, monkeypatch):
    client_id = _student(client, make_user, "Guarded Student")
    _, exam_id, _ = _exam_with_material(
        client_id, name="Control", weight=30, days_out=5, text="Termodinámica: ciclo de Carnot.",
    )
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    # 60 minutes on the first day of the week, nothing anywhere else.
    settings = {
        day: {"day_of_week": day, "available_hours": 1.0 if day == today.weekday() else 0.0,
              "is_free_day": day != today.weekday()}
        for day in range(7)
    }
    answer = {
        "difficulty": [
            {"exam_id": exam_id, "score": 9, "reason": "Denso"},
            {"exam_id": 987654, "score": 5, "reason": "No existe"},
        ],
        "days": [
            {"date": today.isoformat(), "blocks": [
                # Over the daily budget: gets clamped to what is left.
                {"exam_id": exam_id, "phase": "Practicar", "title": "Carnot", "minutes": 240,
                 "why": "Vale 30% y se rinde en 5 días."},
                # No budget left after the first block.
                {"exam_id": exam_id, "phase": "Repasar", "title": "Entropía", "minutes": 60},
                # Not this student's exam.
                {"exam_id": 987654, "phase": "Aprender", "title": "Inventado", "minutes": 30},
            ]},
            # A day the student marked free.
            {"date": (week_start + timedelta(days=(today.weekday() + 1) % 7)).isoformat(),
             "blocks": [{"exam_id": exam_id, "phase": "Repasar", "title": "Extra", "minutes": 60}]},
        ],
    }
    monkeypatch.setattr(planner_ai, "_call_model", lambda _prompt: answer)

    week = planner_ai.build_ai_week(client_id, today, week_start, settings)

    blocks = [b for day in week["days"] for b in day["blocks"]]
    assert len(blocks) == 1
    assert blocks[0]["minutes"] == 60          # clamped to the hour available
    assert blocks[0]["exam_id"] == exam_id     # hallucinated id dropped
    assert blocks[0]["why"]
    assert week["difficulty"][exam_id]["score"] == 5   # 9 clamped into 1..5
    assert 987654 not in week["difficulty"]
    for day in week["days"]:
        assert sum(b["minutes"] for b in day["blocks"]) <= max(0, int(day["hours"] * 60))


def test_regenerate_falls_back_to_rules_when_the_model_is_unavailable(
    client, flask_app, make_user, monkeypatch
):
    client_id = _student(client, make_user, "Fallback Student")
    _exam_with_material(client_id, name="Examen", weight=50, days_out=9, text="Material.")
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(planner_ai, "_call_model", lambda _prompt: None)
    client.post("/api/student/planner/availability", json={
        "settings": [{"day_of_week": day, "available_hours": 2, "is_free_day": False} for day in range(7)],
    })

    response = client.post("/api/student/planner/regenerate", json={})

    assert response.status_code == 200
    plan = response.get_json()["plan"]
    assert plan["source"] == "rules"
    assert len(plan["days"]) == 7


def test_regenerate_uses_the_model_when_it_answers(client, flask_app, make_user, monkeypatch):
    client_id = _student(client, make_user, "AI Plan Student")
    _, exam_id, _ = _exam_with_material(
        client_id, name="Prueba 1", weight=35, days_out=4, text="Álgebra lineal: diagonalización.",
    )
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    today = date.today()
    monkeypatch.setattr(planner_ai, "_call_model", lambda _prompt: {
        "difficulty": [{"exam_id": exam_id, "score": 4, "reason": "Demostraciones densas."}],
        "days": [{"date": today.isoformat(), "blocks": [{
            "exam_id": exam_id, "phase": "Practicar", "title": "Diagonalización: ejercicios",
            "topic": "Diagonalización", "minutes": 60,
            "why": "Vale 35%, faltan 4 días y el material es denso.",
        }]}],
    })
    client.post("/api/student/planner/availability", json={
        "settings": [{"day_of_week": day, "available_hours": 2, "is_free_day": False} for day in range(7)],
    })

    plan = client.post("/api/student/planner/regenerate", json={}).get_json()["plan"]

    assert plan["source"] == "ai"
    today_blocks = next(day["blocks"] for day in plan["days"] if day["date"] == today.isoformat())
    assert today_blocks[0]["title"] == "Diagonalización: ejercicios"
    assert today_blocks[0]["why"]
    assert today_blocks[0]["material_based"] is True
    exam_row = next(e for e in plan["exams"] if e["id"] == exam_id)
    assert exam_row["difficulty"] == 4
    assert exam_row["difficulty_reason"]
    # The saved plan must round-trip through json.dumps.
    assert json.loads(json.dumps(plan))["source"] == "ai"
