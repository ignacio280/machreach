import json
from types import SimpleNamespace

import pytest

from student import analyzer


def _ai_with(content, finish_reason="stop"):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    completions = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(choices=[choice]))
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _question(text="Question one", correct="a"):
    return {
        "topic": "Concept", "question": text,
        "option_a": "A", "option_b": "B", "option_c": "C", "option_d": "D",
        "correct": correct, "explanation": "The rule makes option A correct.",
    }


def test_analyze_material_empty_valid_and_invalid(monkeypatch):
    assert analyzer.analyze_course_material("Math") == analyzer._empty_result("Math")
    payload = {"course_name": "wrong", "exams": [], "weekly_schedule": [], "grading": {}, "key_dates": [], "study_tips": []}
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with("```json\n" + json.dumps(payload) + "\n```"))
    result = analyzer.analyze_course_material(
        "Math", syllabus_html="<p>Limits</p>",
        file_texts=[{"filename": "syllabus.pdf", "text": "Exam topics"}],
        assignments=[{"name": "Midterm", "due_at": "2026-08-01", "points_possible": 100, "description": "<b>Limits</b>"}],
    )
    assert result["course_name"] == "Math"

    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with("not-json"))
    assert analyzer.analyze_course_material("Math", syllabus_html="x") == analyzer._empty_result("Math")

    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: (_ for _ in ()).throw(
                        RuntimeError("provider unavailable")
                    )
                )
            )
        ),
    )
    assert analyzer.analyze_course_material(
        "Math", syllabus_html="material"
    ) == analyzer._empty_result("Math")


def test_study_plan_builds_all_context_and_applies_hard_overrides(monkeypatch):
    raw_plan = {
        "daily_plan": [
            {"date": "2026-08-01", "total_hours": 3, "sessions": [{"course": "Math", "hours": 3}]},
            {"date": "2026-08-03", "total_hours": 2, "sessions": [{"course": "Math", "hours": 2}]},
        ],
        "upcoming_exams": [], "recommendations": [],
    }
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with(json.dumps(raw_plan)))
    result = analyzer.generate_study_plan(
        [{"course_name": "Math", "exams": [{"name": "Final", "date": "2026-08-02", "weight_pct": 50,
          "topics": ["Limits"], "material_files": ["m.pdf"], "materials": "TOTAL PAGES: 10"}]}],
        preferences={"hours_per_day": 2, "exam_prep_days": 5, "weak_subjects": ["Math"]},
        schedule_settings=[{"day": 0, "hours": 2, "free": False}, {"day": 8, "hours": 0, "free": True}],
        course_difficulties={"Math": 5},
        incomplete_assignments=[{"date": "2026-07-16", "course": "Math", "topic": "Limits", "hours": 1, "priority": "high"}],
        date_overrides=[{"date": "2026-08-01", "hours": 1, "note": "work"}],
    )
    assert result["daily_plan"][0]["total_hours"] == 1
    assert result["daily_plan"][0]["sessions"][0]["hours"] == 1
    assert result["daily_plan"][1]["sessions"] == []


def test_post_processing_free_day_invalid_dates_and_passthrough():
    assert analyzer._post_process_plan([], [], []) == []
    plan = {"daily_plan": [
        {"date": "bad", "sessions": [{"course": "Math", "hours": 1}]},
        {"date": "2026-08-01", "total_hours": 2, "sessions": [{"course": "Math", "hours": 2}]},
    ]}
    out = analyzer._post_process_plan(plan, [], [{"date": "2026-08-01", "free": True}])
    assert out["daily_plan"][0]["date"] == "bad"
    assert out["daily_plan"][1]["sessions"] == []


def test_flashcards_and_quiz_batch_parse_provider_results(monkeypatch):
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with('```json\n[{"front":"Q","back":"A"},{"front":"","back":"bad"}]\n```'))
    assert analyzer.generate_flashcards("Math", ["Limits"], "source", 2) == [{"front": "Q", "back": "A"}]

    wrapped = json.dumps({"questions": [_question(), {"question": "bad"}]})
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with(wrapped))
    batch = analyzer._generate_quiz_batch("Math", "Limits", "hard", "hard", "source", 1, 0, 2, ["old"])
    assert len(batch) == 1
    assert batch[0]["topic"] == "Concept"


def test_quiz_generation_deduplicates_tops_up_and_balances(monkeypatch):
    calls = []
    def fake_batch(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [_question("One", "a"), _question("One", "a")]
        return [_question("Two", "b"), _question("Three", "c")]
    monkeypatch.setattr(analyzer, "_generate_quiz_batch", fake_batch)
    monkeypatch.setattr(analyzer.random, "shuffle", lambda items: None)
    result = analyzer.generate_quiz("Math", source_text="source", difficulty="hard", count="3")
    assert {q["question"] for q in result} == {"One", "Two", "Three"}
    assert len(calls) == 2


def test_helpers_format_split_remap_and_invalid_provider(monkeypatch):
    assert "Description: Read" in analyzer._format_assignments([
        {"name": "A", "due_at": None, "points_possible": 5, "description": "<p>Read</p>"}
    ])
    assert analyzer._remap_letters_in_text("option B is tempting", {"b": "d"}) == "option D is tempting"
    assert analyzer._split_into_chunks("") == []
    chunks = analyzer._split_into_chunks("Chapter 1\n" + "a" * 20 + "\nChapter 2\n" + "b" * 20, max_chars=12, hard_cap_chunks=2)
    assert 1 <= len(chunks) <= 2
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with("bad"))
    assert analyzer.generate_flashcards("Math", source_text="source") == []


def test_study_plan_provider_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with("not-json"))
    with pytest.raises(RuntimeError, match="Plan generation failed"):
        analyzer.generate_study_plan([])


def test_truncated_study_plan_is_repaired_when_json_can_be_closed(monkeypatch):
    raw = '{"daily_plan":[{"date":"2026-08-01","sessions":[]}'
    monkeypatch.setattr(analyzer, "_ai", lambda: _ai_with(raw, finish_reason="length"))

    result = analyzer.generate_study_plan([])

    assert result["daily_plan"][0]["date"] == "2026-08-01"


def test_post_processing_ignores_invalid_catalog_rows_and_caps_sessions():
    plan = {
        "daily_plan": [{
            "date": "2026-08-01",
            "total_hours": 3,
            "sessions": [
                {"course": "Math", "hours": 1.5},
                {"course": "Math", "hours": 1.5},
            ],
        }],
    }
    courses = [
        {"exams": []},
        {"course_name": "Invalid", "exams": [{"date": ""}, {"date": "bad"}]},
        {"course_name": "Math", "exams": [{"date": "2026-08-10"}]},
    ]
    overrides = [
        {"date": ""},
        {"date": "2026-08-01", "hours": 2, "note": "limited"},
    ]

    result = analyzer._post_process_plan(plan, courses, overrides)

    assert result["daily_plan"][0]["total_hours"] == 2
    assert result["daily_plan"][0]["sessions"][1]["hours"] == 0.5


def test_long_material_generation_chunks_and_deduplicates(monkeypatch):
    long_source = (
        "Chapter 1\n" + "a" * 60010 + "\nChapter 2\n" + "b" * 60010
    )
    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: _ai_with(
            json.dumps([
                {"front": "Shared", "back": "Answer"},
                {"front": "Shared", "back": "Duplicate"},
            ])
        ),
    )
    cards = analyzer.generate_flashcards("Math", source_text=long_source, count=8)
    assert cards == [{"front": "Shared", "back": "Answer"}]

    calls = []

    def batch(**kwargs):
        calls.append(kwargs["context"][:30])
        return [_question(f"Question {len(calls)}")]

    monkeypatch.setattr(analyzer, "_generate_quiz_batch", batch)
    quiz = analyzer.generate_quiz(
        "Math", source_text=long_source, difficulty="hard", count=6
    )
    assert len(quiz) >= 2
    assert len(calls) >= 2


def test_quiz_batch_provider_fallback_and_malformed_items(monkeypatch):
    calls = 0
    raw = (
        'prefix [{"question":"valid","option_a":"A","option_b":"B",'
        '"option_c":"C","option_d":"D","correct":"a"},'
        '"not-a-dict",{"question":""},'
        '{"question":"bad answer","correct":"z"},'
        '{"question":"missing options","correct":"a"}] suffix'
    )

    def create(**kwargs):
        nonlocal calls
        calls += 1
        if "response_format" in kwargs:
            raise RuntimeError("json mode unsupported")
        return _ai_with(raw).chat.completions.create()

    monkeypatch.setattr(
        analyzer,
        "_ai",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    batch = analyzer._generate_quiz_batch(
        "Math", "Limits", "hard", "hard", "source", 5, 0, 1, []
    )
    assert calls == 2
    assert [question["question"] for question in batch] == ["valid"]


def test_quiz_generation_handles_invalid_count_and_provider_failures(monkeypatch):
    calls = 0

    def failing_batch(**kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analyzer, "_generate_quiz_batch", failing_batch)

    assert analyzer.generate_quiz("Math", count="bad") == []
    assert calls >= 1
