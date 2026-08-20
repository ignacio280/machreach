"""HTTP contracts for plans, flashcards, quizzes, and safe extraction."""

import json
from io import BytesIO
from types import SimpleNamespace

from machreach_core.db import _exec, get_db
from pdf_fixtures import STUDY_MATERIAL, scanned_pdf, text_pdf
from student import ai_usage, analyzer, db as sdb, subscription


def _login(client, client_id: int, name: str = "Learning Tools") -> None:
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


def _question(text: str, correct: str = "a") -> dict:
    return {
        "topic": "Trees",
        "question": text,
        "option_a": "Correct",
        "option_b": "Wrong B",
        "option_c": "Wrong C",
        "option_d": "Wrong D",
        "correct": correct,
        "explanation": "A is correct because of the invariant.",
    }


def test_planning_and_assignment_http_contracts(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Planning Tools")
    course_id = sdb.create_manual_course(client_id, "Planning Course", "PLN101")
    _login(client, client_id, "Planning Tools")

    assert client.post(
        "/api/student/date-overrides", json={"date": "bad"}
    ).status_code == 400
    assert client.post(
        "/api/student/date-overrides",
        json={"date": "2026-02-30", "hours": 4},
    ).status_code == 400
    assert client.post(
        "/api/student/date-overrides",
        json={
            "date": "2099-08-01",
            "hours": 30,
            "free": False,
            "note": "Limited availability",
        },
    ).get_json() == {"ok": True}
    overrides = client.get(
        "/api/student/date-overrides?start=2099-01-01&end=2099-12-31"
    ).get_json()["overrides"]
    assert overrides[0]["available_hours"] == 24
    assert client.delete(
        "/api/student/date-overrides/2099-08-01"
    ).get_json() == {"ok": True}

    difficulty = client.put(
        f"/api/student/courses/{course_id}/difficulty",
        json={"difficulty": 99},
    ).get_json()
    assert difficulty == {"ok": True, "difficulty": 5}
    assert client.put(
        "/api/student/courses/999999/difficulty", json={"difficulty": 3}
    ).status_code == 404

    assert client.post(
        "/api/student/assignments/toggle", json={"date": "2099-08-01"}
    ).status_code == 400
    assert client.post(
        "/api/student/assignments/toggle",
        json={"date": "2099-08-01", "session_index": 0, "completed": True},
    ).get_json() == {"ok": True}
    progress = client.get(
        "/api/student/assignments/progress?date=2099-08-01"
    ).get_json()
    assert progress["date"] == "2099-08-01"
    assert client.get("/api/student/assignments/incomplete").status_code == 200

    assert client.post(
        "/api/student/manual-plan", json={"date": "bad", "sessions": {}}
    ).status_code == 400
    plan = client.post(
        "/api/student/manual-plan",
        json={
            "date": "2099-08-01",
            "sessions": [
                {"topic": ""},
                {
                    "start_time": "09:00",
                    "course": "Planning Course",
                    "topic": "Graph traversal",
                    "hours": 20,
                    "type": "review",
                    "priority": "high",
                },
            ],
        },
    ).get_json()
    assert plan["sessions"][0]["hours"] == 8
    assert client.get("/api/student/manual-plan").get_json()[
        "daily_plan"
    ][0]["date"] == "2099-08-01"


def test_flashcard_crud_progress_and_generation_queue(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Flashcard Tools")
    course_id = sdb.create_manual_course(client_id, "Flashcards", "FC101")
    deck_id = sdb.create_flashcard_deck(
        client_id, "Tree Deck", course_id=course_id
    )
    card_id = sdb.add_flashcard(deck_id, "What is AVL?", "A balanced BST")
    _login(client, client_id, "Flashcard Tools")

    decks = client.get(
        f"/api/student/flashcards/decks?course_id={course_id}"
    ).get_json()["decks"]
    assert decks[0]["id"] == deck_id
    deck = client.get(f"/api/student/flashcards/decks/{deck_id}").get_json()
    assert deck["cards"][0]["id"] == card_id
    assert client.get("/api/student/flashcards/decks/999999").status_code == 404

    assert client.put(
        f"/api/student/flashcards/{card_id}",
        json={"deck_id": deck_id, "front": "", "back": "answer"},
    ).status_code == 400
    assert client.put(
        f"/api/student/flashcards/{card_id}",
        json={"deck_id": deck_id, "front": "AVL property?", "back": "Balance <= 1"},
    ).get_json() == {"ok": True}
    added = client.post(
        "/api/student/flashcards/add",
        json={"deck_id": deck_id, "front": "Red-black root?", "back": "Black"},
    )
    added_id = added.get_json()["card_id"]
    assert client.post(
        "/api/student/flashcards/add",
        json={"deck_id": 999999, "front": "Q", "back": "A"},
    ).status_code == 404

    assert client.post(
        "/api/student/flashcards/progress",
        json={"card_id": card_id, "quality": 9},
    ).get_json() == {"ok": True}
    assert client.post(
        "/api/student/flashcards/progress",
        json={"card_id": 999999, "correct": True},
    ).status_code == 404
    assert client.delete(
        f"/api/student/flashcards/{added_id}"
    ).status_code == 400
    assert client.delete(
        f"/api/student/flashcards/{added_id}?deck_id={deck_id}"
    ).get_json() == {"ok": True}

    monkeypatch.setattr(
        subscription, "can_generate_flashcards_today", lambda cid: (True, "")
    )
    too_large = client.post(
        "/api/student/flashcards/generate",
        json={"source_text": "x" * 250001},
    )
    assert too_large.status_code == 413
    queued = client.post(
        "/api/student/flashcards/generate",
        json={"source_text": "Tree material", "title": "Generated Tree Cards"},
    )
    assert queued.status_code == 202
    assert queued.get_json()["queued"] is True
    assert client.get(
        "/api/student/flashcards/generate/status"
    ).get_json()["status"] in {"queued", "running"}
    duplicate = client.post(
        "/api/student/flashcards/generate",
        json={"source_text": "Retry"},
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["already_running"] is True

    assert client.delete(f"/api/student/flashcards/decks/{deck_id}").get_json() == {
        "ok": True
    }
    assert client.delete(
        f"/api/student/flashcards/decks/{deck_id}"
    ).status_code == 404


def test_quiz_scoring_analysis_queue_and_deletion(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Quiz Tools")
    course_id = sdb.create_manual_course(client_id, "Quiz Course", "QZ101")
    quiz_id = sdb.create_quiz(
        client_id, "Tree Quiz", "hard", course_id=course_id
    )
    sdb.add_quiz_questions(
        quiz_id,
        [_question("Question 1"), _question("Question 2")],
    )
    _login(client, client_id, "Quiz Tools")

    quizzes = client.get(
        f"/api/student/quizzes?course_id={course_id}"
    ).get_json()["quizzes"]
    assert quizzes[0]["id"] == quiz_id
    quiz = client.get(f"/api/student/quizzes/{quiz_id}").get_json()
    questions = quiz["questions"]
    assert questions[0]["explanation"] == ""
    assert client.get("/api/student/quizzes/999999").status_code == 404

    assert client.post(
        f"/api/student/quizzes/{quiz_id}/score", json={"answers": []}
    ).status_code == 400
    answers = [
        {"q_id": "bad", "selected": "a"},
        {"q_id": questions[0]["id"], "selected": "a"},
        {"q_id": questions[0]["id"], "selected": "b"},
        {"q_id": questions[1]["id"], "selected": "a"},
    ]
    score = client.post(
        f"/api/student/quizzes/{quiz_id}/score", json={"answers": answers}
    ).get_json()
    assert score["score"] == 100
    assert score["correct"] == 2

    ai_payload = {
        "headline": "Strong tree fundamentals.",
        "verdict": "solid",
        "strengths": [{"topic": "Trees", "detail": "Both answers were correct."}],
        "weaknesses": [{"topic": "None", "detail": "", "fix": ""}],
        "mistake_patterns": [],
        "time_insight": "Steady pace.",
        "next_actions": ["Try harder trees."],
        "study_plan_30min": [{"minutes": 30, "task": "Practice"}],
        "encouragement": "Keep the precision.",
    }
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(ai_payload))
            )
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        ),
    )
    monkeypatch.setattr(ai_usage, "reserve", lambda *args, **kwargs: {"status": "reserved"})
    monkeypatch.setattr(ai_usage, "settle", lambda *args, **kwargs: None)
    analysis = client.post(
        f"/api/student/quizzes/{quiz_id}/analyze",
        json={
            "answers": [
                {"q_id": questions[0]["id"], "selected": "a", "time": 12.5},
                {"q_id": questions[1]["id"], "selected": "b", "time": 4},
                {"q_id": 999999, "selected": "a", "time": 1},
            ],
        },
    )
    assert analysis.status_code == 200
    report = analysis.get_json()
    assert report["score"] == 50
    assert report["plus_report"]["unlocked"] is False
    assert report["ai"]["weaknesses"] == []
    assert report["pacing"]["fastest"] == 4

    assert client.post(
        f"/api/student/quizzes/{quiz_id}/analyze", json={"answers": []}
    ).status_code == 400
    monkeypatch.setattr(
        subscription, "can_generate_quiz_today", lambda cid: (True, "")
    )
    assert client.post("/api/student/quizzes/generate", json={}).status_code == 410
    queued = client.post(
        "/api/student/quizzes/generate-async",
        json={"course_id": course_id},
    ).get_json()
    assert queued["queued"] is True
    in_flight = client.post(
        "/api/student/quizzes/generate-async",
        json={"course_id": course_id},
    )
    assert in_flight.status_code == 409
    assert in_flight.get_json()["already_running"] is True
    assert client.get(
        "/api/student/quizzes/generate/status"
    ).get_json()["status"] in {"queued", "running"}

    assert client.delete(f"/api/student/quizzes/{quiz_id}").get_json() == {
        "ok": True
    }
    assert client.delete(f"/api/student/quizzes/{quiz_id}").status_code == 404


def test_extract_file_validation_and_text_success(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Extraction Tools")
    _login(client, client_id, "Extraction Tools")

    assert client.post("/api/student/extract-file").status_code == 400
    assert client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(b""), "")},
        content_type="multipart/form-data",
    ).status_code == 400
    assert client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(b"binary"), "unsafe.exe")},
        content_type="multipart/form-data",
    ).status_code == 400
    assert client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(b"short"), "short.txt")},
        content_type="multipart/form-data",
    ).status_code == 400

    extracted = client.post(
        "/api/student/extract-file",
        data={
            "file": (
                BytesIO(b"Readable study material for safe extraction."),
                "notes.txt",
            )
        },
        content_type="multipart/form-data",
    )
    assert extracted.status_code == 200
    assert extracted.get_json()["title"] == "notes"

    # A phone scan of handwritten notes: real file, real pages, no text layer.
    # This used to return 200 with a page marker per page, and the quiz built
    # from it was about nothing the student had uploaded.
    scanned = client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(scanned_pdf(pages=6)), "apuntes.pdf")},
        content_type="multipart/form-data",
    )
    assert scanned.status_code == 400
    assert scanned.get_json()["no_text_layer"] is True

    readable_pdf = client.post(
        "/api/student/extract-file",
        data={
            "file": (
                BytesIO(text_pdf([STUDY_MATERIAL[:70], STUDY_MATERIAL[70:140]])),
                "apuntes.pdf",
            )
        },
        content_type="multipart/form-data",
    )
    assert readable_pdf.status_code == 200
    assert "Carnot cycle" in readable_pdf.get_json()["text"]
