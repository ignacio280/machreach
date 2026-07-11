"""Portable account export must include the student product's owned data."""

from outreach.db import get_db, _exec, _fetchone
from student import db as sdb


def test_student_export_contains_core_study_and_gamification_data(client, make_user):
    client_id = make_user("Export Student")
    with get_db() as db:
        university = _fetchone(db, "SELECT id FROM universities ORDER BY id LIMIT 1")
        major = _fetchone(db, "SELECT id FROM majors ORDER BY id LIMIT 1")
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1, country_iso = 'CL', "
            "university_id = %s, major_id = %s WHERE id = %s",
            (university["id"], major["id"], client_id),
        )
    course_id = sdb.create_manual_course(client_id, "Export Course", code="EXP101")
    deck_id = sdb.create_flashcard_deck(client_id, "Export Deck", course_id=course_id)
    sdb.add_flashcard(deck_id, "front", "back")
    quiz_id = sdb.create_quiz(client_id, "Export Quiz", course_id=course_id)
    sdb.add_quiz_questions(quiz_id, [{"question": "Q?", "correct": "a"}])
    sdb.save_focus_session(client_id, "pomodoro", 25, 0, "Export Course", course_id)
    sdb.award_xp(client_id, "test_export", 12, "export marker")
    sdb.add_coins(client_id, 7, "export marker")

    with client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = "Export Student"
        sess["account_type"] = "student"
        sess["session_version"] = 0
    response = client.get("/api/export-my-data")

    assert response.status_code == 200
    payload = response.get_json()
    student = payload["student"]
    assert student["academic_profile"]["country_iso"] == "CL"
    assert student["courses"][0]["name"] == "Export Course"
    assert student["flashcard_decks"][0]["cards"][0]["front"] == "front"
    assert student["quizzes"][0]["questions"][0]["question"] == "Q?"
    assert student["study_progress"][0]["focus_minutes"] == 25
    assert any(row["action"] == "test_export" for row in student["xp"])
    assert student["wallet"]["coins"] == 7
