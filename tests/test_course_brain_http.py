"""End-to-end contracts for the Plus Course Brain study assistant."""

import json
import uuid
from types import SimpleNamespace

from machreach_core.db import _exec, get_db
from student import ai_usage, analyzer, db as sdb, subscription


def _login(client, client_id: int) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Course Brain"
        session["account_type"] = "student"
        session["session_version"] = 0


def _ai_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=f"```json\n{json.dumps(payload)}\n```"
                )
            )
        ]
    )


def _plus_course_with_context(client_id: int) -> int:
    course_id = sdb.create_manual_course(
        client_id, "Algorithms", "CS301", "2026-2"
    )
    exam_id = sdb.upsert_exam(
        client_id,
        course_id,
        None,
        "Final",
        "2099-12-10",
        45,
        ["Graph traversal", "Dynamic programming"],
    )
    sdb.add_course_file(
        client_id,
        course_id,
        "syllabus.txt",
        "text/plain",
        "Breadth-first search explores a graph level by level.",
        exam_id,
    )
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_notes "
            "(client_id, course_id, title, content_html, source_type) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                client_id,
                course_id,
                "Graph notes",
                "<p>Use a <strong>queue</strong> for BFS.</p>",
                "manual",
            ),
        )
    quiz_id = sdb.create_quiz(
        client_id, "Graphs checkpoint", "hard", course_id=course_id
    )
    sdb.add_quiz_questions(
        quiz_id,
        [
            {
                "question": "Which structure powers BFS?",
                "option_a": "Queue",
                "option_b": "Stack",
                "option_c": "Heap",
                "option_d": "Set",
                "correct": "a",
                "explanation": "A queue preserves frontier order.",
                "topic": "Graphs",
            }
        ],
    )
    deck_id = sdb.create_flashcard_deck(
        client_id, "Graph recall", course_id=course_id
    )
    sdb.add_flashcard(deck_id, "BFS structure?", "Queue")
    assert sdb.save_focus_session(
        client_id, "pomodoro", 25, 0, "Algorithms", course_id, exam_id
    )
    state = subscription.set_subscription_state(
        client_id,
        tier="plus",
        status="active",
        ls_sub_id=f"course-brain-{uuid.uuid4().hex}",
        payment_succeeded=True,
    )
    assert state["ok"] is True
    return course_id


def test_course_brain_builds_grounded_context_for_every_mode(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Course Brain Plus")
    course_id = _plus_course_with_context(client_id)
    _login(client, client_id)

    prompts: list[str] = []
    settled: list[str] = []
    reservations: list[dict] = []

    def complete(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return _ai_response({"title": "Grounded result", "ok": True})

    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=complete)
            )
        ),
    )
    monkeypatch.setattr(
        ai_usage,
        "reserve",
        lambda *args, **kwargs: reservations.append(kwargs)
        or {"status": "reserved"},
    )
    monkeypatch.setattr(
        ai_usage, "settle", lambda key: settled.append(key)
    )

    for mode in ("brain", "exam", "studio"):
        response = client.post(
            f"/api/student/courses/{course_id}/ai-studio",
            json={"mode": mode},
            headers={"X-Idempotency-Key": f"mode-{mode}"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["mode"] == mode
        assert payload["generated_at"].endswith("Z")
        assert payload["result"]["title"] == "Grounded result"
        assert payload["context_counts"] == {
            "sources": 2,
            "exams": 1,
            "quizzes": 1,
            "flashcard_decks": 1,
        }

    assert len(prompts) == 3
    assert "Course Brain" in prompts[0]
    assert "seven_day_plan" in prompts[1]
    assert "NotebookLM-style" in prompts[2]
    for prompt in prompts:
        assert "Algorithms" in prompt
        assert "syllabus.txt" in prompt
        assert "Graph notes" in prompt
        assert "Which structure powers BFS?" in prompt
        assert "BFS structure?" in prompt
    assert [r["feature"] for r in reservations] == [
        "course_brain_brain",
        "course_brain_exam",
        "course_brain_studio",
    ]
    assert settled == [
        f"course-brain:{client_id}:mode-brain",
        f"course-brain:{client_id}:mode-exam",
        f"course-brain:{client_id}:mode-studio",
    ]


def test_course_brain_access_validation_and_failure_accounting(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    free_id = make_user("Course Brain Free")
    free_course = sdb.create_manual_course(free_id, "Free Course", "FREE1")
    _login(client, free_id)
    upgrade = client.post(
        f"/api/student/courses/{free_course}/ai-studio", json={"mode": "brain"}
    )
    assert upgrade.status_code == 402
    assert upgrade.get_json()["upgrade_required"] is True

    plus_id = make_user("Course Brain Failures")
    course_id = _plus_course_with_context(plus_id)
    _login(client, plus_id)
    assert client.post(
        f"/api/student/courses/{course_id}/ai-studio",
        json={"mode": "unsupported"},
    ).status_code == 400
    assert client.post(
        "/api/student/courses/999999/ai-studio", json={"mode": "brain"}
    ).status_code == 404

    monkeypatch.setattr(
        ai_usage, "reserve", lambda *args, **kwargs: {"status": "reserved"}
    )
    monkeypatch.setattr(ai_usage, "settle", lambda *args, **kwargs: None)
    failed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ai_usage, "fail", lambda key, reason: failed.append((key, reason))
    )

    invalid = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="this is not JSON")
            )
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: invalid)
            )
        ),
    )
    response = client.post(
        f"/api/student/courses/{course_id}/ai-studio",
        json={"mode": "brain"},
        headers={"X-Idempotency-Key": "invalid-json"},
    )
    assert response.status_code == 500
    assert "respuesta invalida" in response.get_json()["error"]
    assert failed == [
        (
            f"course-brain:{plus_id}:invalid-json",
            "invalid_json_response",
        )
    ]

    def provider_failure(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=provider_failure)
            )
        ),
    )
    response = client.post(
        f"/api/student/courses/{course_id}/ai-studio",
        json={"mode": "exam"},
        headers={"X-Idempotency-Key": "provider-failure"},
    )
    assert response.status_code == 500
    assert response.get_json()["error"] == (
        "No se pudo generar el estudio del curso. Intenta de nuevo."
    )
    assert failed[-1] == (
        f"course-brain:{plus_id}:provider-failure",
        "provider unavailable",
    )
    assert failed == [
        (
            f"course-brain:{plus_id}:invalid-json",
            "invalid_json_response",
        ),
        (
            f"course-brain:{plus_id}:provider-failure",
            "provider unavailable",
        ),
    ]


def test_course_brain_degrades_safely_when_optional_context_stores_fail(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Course Brain Sparse")
    course_id = _plus_course_with_context(client_id)
    _login(client, client_id)

    def unavailable(*args, **kwargs):
        raise RuntimeError("optional store unavailable")

    for name in (
        "get_course_files",
        "get_notes",
        "get_course_exams",
        "get_quizzes",
        "get_flashcard_decks",
        "get_time_per_course",
    ):
        monkeypatch.setattr(sdb, name, unavailable)
    monkeypatch.setattr(
        ai_usage, "reserve", lambda *args, **kwargs: {"status": "reserved"}
    )
    monkeypatch.setattr(ai_usage, "settle", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _ai_response(
                        {"title": "Sparse but safe"}
                    )
                )
            )
        ),
    )

    response = client.post(
        f"/api/student/courses/{course_id}/ai-studio",
        json={"mode": "brain"},
        headers={"X-Idempotency-Key": "sparse"},
    )
    assert response.status_code == 200
    assert response.get_json()["context_counts"] == {
        "sources": 0,
        "exams": 0,
        "quizzes": 0,
        "flashcard_decks": 0,
    }
