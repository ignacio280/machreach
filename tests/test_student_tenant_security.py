"""Tenant-isolation regressions for student-owned study content."""

from student import db as sdb
from machreach_core.db import get_db, _exec


def _login(client, client_id, name="Tenant User"):
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = name
        sess["account_type"] = "student"
        sess["session_version"] = 0


def test_flashcard_progress_rejects_a_card_owned_by_another_student(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Card Owner")
    attacker_id = make_user("Card Attacker")
    deck_id = sdb.create_flashcard_deck(owner_id, "Private deck")
    card_id = sdb.add_flashcard(deck_id, "private question", "private answer")

    _login(client, attacker_id, "Card Attacker")
    response = client.post(
        "/api/student/flashcards/progress",
        json={"card_id": card_id, "quality": 5},
    )

    assert response.status_code == 404
    card = sdb.get_flashcards(deck_id)[0]
    assert card["times_seen"] == 0
    assert card["times_correct"] == 0


def test_flashcard_deck_delete_does_not_delete_another_students_cards(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Deck Owner")
    attacker_id = make_user("Deck Attacker")
    deck_id = sdb.create_flashcard_deck(owner_id, "Owner-only deck")
    card_id = sdb.add_flashcard(deck_id, "owner question", "owner answer")

    _login(client, attacker_id, "Deck Attacker")
    response = client.delete(f"/api/student/flashcards/decks/{deck_id}")

    assert response.status_code == 404
    assert sdb.get_flashcard_deck(deck_id, owner_id) is not None
    assert [card["id"] for card in sdb.get_flashcards(deck_id)] == [card_id]


def test_quiz_submission_rejects_a_quiz_owned_by_another_student(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Quiz Owner")
    attacker_id = make_user("Quiz Attacker")
    quiz_id = sdb.create_quiz(owner_id, "Private quiz")
    sdb.add_quiz_questions(
        quiz_id,
        [
            {
                "question": "Private question?",
                "option_a": "yes",
                "option_b": "no",
                "correct": "a",
            }
        ],
    )
    question_id = sdb.get_quiz_questions(quiz_id)[0]["id"]

    _login(client, attacker_id, "Quiz Attacker")
    response = client.post(
        f"/api/student/quizzes/{quiz_id}/score",
        json={"answers": [{"q_id": question_id, "selected": "a"}]},
    )

    assert response.status_code == 404
    assert sdb.get_quiz(quiz_id, owner_id)["attempts"] == 0


def test_quiz_score_is_calculated_from_owned_server_questions(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Quiz Scorer")
    quiz_id = sdb.create_quiz(owner_id, "Server-scored quiz")
    sdb.add_quiz_questions(
        quiz_id,
        [
            {"question": "Q1", "correct": "a"},
            {"question": "Q2", "correct": "b"},
        ],
    )
    questions = sdb.get_quiz_questions(quiz_id)

    _login(client, owner_id, "Quiz Scorer")
    response = client.post(
        f"/api/student/quizzes/{quiz_id}/score",
        json={
            "score": 1000,
            "answers": [
                {"q_id": questions[0]["id"], "selected": "a"},
                {"q_id": questions[1]["id"], "selected": "a"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["score"] == 50
    quiz = sdb.get_quiz(quiz_id, owner_id)
    assert quiz["attempts"] == 1
    assert quiz["best_score"] == 50


def test_quiz_delete_does_not_delete_another_students_questions(
    client, make_user, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    owner_id = make_user("Quiz Delete Owner")
    attacker_id = make_user("Quiz Delete Attacker")
    quiz_id = sdb.create_quiz(owner_id, "Owner-only quiz")
    sdb.add_quiz_questions(quiz_id, [{"question": "Q1", "correct": "a"}])
    question_id = sdb.get_quiz_questions(quiz_id)[0]["id"]

    _login(client, attacker_id, "Quiz Delete Attacker")
    response = client.delete(f"/api/student/quizzes/{quiz_id}")

    assert response.status_code == 404
    assert sdb.get_quiz(quiz_id, owner_id) is not None
    assert [q["id"] for q in sdb.get_quiz_questions(quiz_id)] == [question_id]
