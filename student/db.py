"""
Student DB operations — extends MachReach's database with student-specific
tables for Canvas integration, course analysis, and study plans.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from outreach.db import (
    _exec,
    _fetchall,
    _fetchone,
    _fetchval,
    _insert_returning_id,
    _USE_PG,
    get_db,
)

log = logging.getLogger(__name__)


# ── Schema (appended to MachReach's init_db) ────────────────

STUDENT_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_canvas_tokens (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_url  TEXT NOT NULL,
    token       TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id)
);

CREATE TABLE IF NOT EXISTS student_courses (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_course_id INTEGER NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT DEFAULT '',
    term            TEXT DEFAULT '',
    analysis_json   TEXT DEFAULT '{}',
    last_synced     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, canvas_course_id)
);

CREATE INDEX IF NOT EXISTS idx_student_courses_client ON student_courses(client_id);

CREATE TABLE IF NOT EXISTS student_exams (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    exam_date       TEXT,
    weight_pct      INTEGER DEFAULT 0,
    topics_json     TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'upcoming',
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_exams_client ON student_exams(client_id);
CREATE INDEX IF NOT EXISTS idx_student_exams_date ON student_exams(exam_date);

CREATE TABLE IF NOT EXISTS student_study_plans (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_json       TEXT NOT NULL DEFAULT '{}',
    preferences_json TEXT DEFAULT '{}',
    generated_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_plans_client ON student_study_plans(client_id);

CREATE TABLE IF NOT EXISTS student_study_progress (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_date       TEXT NOT NULL,
    completed       INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, plan_date)
);

CREATE TABLE IF NOT EXISTS student_course_files (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    original_name   TEXT NOT NULL,
    file_type       TEXT DEFAULT '',
    extracted_text  TEXT DEFAULT '',
    uploaded_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_student_files_course ON student_course_files(course_id);

CREATE TABLE IF NOT EXISTS student_schedule_settings (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL,  -- 0=Monday .. 6=Sunday
    available_hours REAL DEFAULT 0,
    is_free_day BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS student_assignment_progress (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_date       TEXT NOT NULL,
    session_index   INTEGER NOT NULL,  -- index within that day's sessions
    completed       BOOLEAN DEFAULT FALSE,
    completed_at    TIMESTAMP,
    UNIQUE(client_id, plan_date, session_index)
);

CREATE INDEX IF NOT EXISTS idx_student_assignment_client ON student_assignment_progress(client_id, plan_date);

CREATE TABLE IF NOT EXISTS student_flashcard_decks (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    exam_id     INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    source_type TEXT DEFAULT 'ai',
    card_count  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flashcard_decks_client ON student_flashcard_decks(client_id);

CREATE TABLE IF NOT EXISTS student_flashcards (
    id          SERIAL PRIMARY KEY,
    deck_id     INTEGER NOT NULL REFERENCES student_flashcard_decks(id) ON DELETE CASCADE,
    front       TEXT NOT NULL,
    back        TEXT NOT NULL,
    difficulty  INTEGER DEFAULT 0,
    times_seen  INTEGER DEFAULT 0,
    times_correct INTEGER DEFAULT 0,
    next_review TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flashcards_deck ON student_flashcards(deck_id);

CREATE TABLE IF NOT EXISTS student_quizzes (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    exam_id     INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    difficulty  TEXT DEFAULT 'medium',
    question_count INTEGER DEFAULT 0,
    best_score  INTEGER DEFAULT 0,
    attempts    INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quizzes_client ON student_quizzes(client_id);

CREATE TABLE IF NOT EXISTS student_quiz_questions (
    id          SERIAL PRIMARY KEY,
    quiz_id     INTEGER NOT NULL REFERENCES student_quizzes(id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    option_a    TEXT NOT NULL,
    option_b    TEXT NOT NULL,
    option_c    TEXT NOT NULL,
    option_d    TEXT NOT NULL,
    correct     TEXT NOT NULL,
    explanation TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quiz_questions ON student_quiz_questions(quiz_id);

CREATE TABLE IF NOT EXISTS student_notes (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    content_html TEXT NOT NULL DEFAULT '',
    source_type TEXT DEFAULT 'ai',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notes_client ON student_notes(client_id);

CREATE TABLE IF NOT EXISTS student_chat_messages (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    content     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_client ON student_chat_messages(client_id, course_id);

CREATE TABLE IF NOT EXISTS student_youtube_imports (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    youtube_url TEXT NOT NULL,
    video_title TEXT DEFAULT '',
    transcript  TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    note_id     INTEGER REFERENCES student_notes(id) ON DELETE SET NULL,
    deck_id     INTEGER REFERENCES student_flashcard_decks(id) ON DELETE SET NULL,
    quiz_id     INTEGER REFERENCES student_quizzes(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_yt_client ON student_youtube_imports(client_id);

CREATE TABLE IF NOT EXISTS student_xp (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    action      TEXT NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    detail      TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_xp_client ON student_xp(client_id);

CREATE TABLE IF NOT EXISTS student_badges (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    badge_key   TEXT NOT NULL,
    earned_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, badge_key)
);
CREATE INDEX IF NOT EXISTS idx_badges_client ON student_badges(client_id);

CREATE TABLE IF NOT EXISTS student_email_prefs (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    daily_email BOOLEAN DEFAULT TRUE,
    email_hour  INTEGER DEFAULT 7,
    timezone    TEXT DEFAULT 'America/Mexico_City',
    UNIQUE(client_id)
);
"""

STUDENT_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_canvas_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_url  TEXT NOT NULL,
    token       TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id)
);

CREATE TABLE IF NOT EXISTS student_courses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_course_id INTEGER NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT DEFAULT '',
    term            TEXT DEFAULT '',
    analysis_json   TEXT DEFAULT '{}',
    last_synced     TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, canvas_course_id)
);

CREATE INDEX IF NOT EXISTS idx_student_courses_client ON student_courses(client_id);

CREATE TABLE IF NOT EXISTS student_exams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    exam_date       TEXT,
    weight_pct      INTEGER DEFAULT 0,
    topics_json     TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'upcoming',
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_student_exams_client ON student_exams(client_id);
CREATE INDEX IF NOT EXISTS idx_student_exams_date ON student_exams(exam_date);

CREATE TABLE IF NOT EXISTS student_study_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_json       TEXT NOT NULL DEFAULT '{}',
    preferences_json TEXT DEFAULT '{}',
    generated_at    TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_student_plans_client ON student_study_plans(client_id);

CREATE TABLE IF NOT EXISTS student_study_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_date       TEXT NOT NULL,
    completed       INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, plan_date)
);

CREATE TABLE IF NOT EXISTS student_course_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE,
    original_name   TEXT NOT NULL,
    file_type       TEXT DEFAULT '',
    extracted_text  TEXT DEFAULT '',
    uploaded_at     TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_student_files_course ON student_course_files(course_id);

CREATE TABLE IF NOT EXISTS student_schedule_settings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL,
    available_hours REAL DEFAULT 0,
    is_free_day INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS student_assignment_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_date       TEXT NOT NULL,
    session_index   INTEGER NOT NULL,
    completed       INTEGER DEFAULT 0,
    completed_at    TEXT,
    UNIQUE(client_id, plan_date, session_index)
);

CREATE INDEX IF NOT EXISTS idx_student_assignment_client ON student_assignment_progress(client_id, plan_date);

CREATE TABLE IF NOT EXISTS student_flashcard_decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    exam_id     INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    source_type TEXT DEFAULT 'ai',
    card_count  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_flashcard_decks_client ON student_flashcard_decks(client_id);

CREATE TABLE IF NOT EXISTS student_flashcards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES student_flashcard_decks(id) ON DELETE CASCADE,
    front       TEXT NOT NULL,
    back        TEXT NOT NULL,
    difficulty  INTEGER DEFAULT 0,
    times_seen  INTEGER DEFAULT 0,
    times_correct INTEGER DEFAULT 0,
    next_review TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_flashcards_deck ON student_flashcards(deck_id);

CREATE TABLE IF NOT EXISTS student_quizzes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    exam_id     INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    difficulty  TEXT DEFAULT 'medium',
    question_count INTEGER DEFAULT 0,
    best_score  INTEGER DEFAULT 0,
    attempts    INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_quizzes_client ON student_quizzes(client_id);

CREATE TABLE IF NOT EXISTS student_quiz_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id     INTEGER NOT NULL REFERENCES student_quizzes(id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    option_a    TEXT NOT NULL,
    option_b    TEXT NOT NULL,
    option_c    TEXT NOT NULL,
    option_d    TEXT NOT NULL,
    correct     TEXT NOT NULL,
    explanation TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quiz_questions ON student_quiz_questions(quiz_id);

CREATE TABLE IF NOT EXISTS student_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    content_html TEXT NOT NULL DEFAULT '',
    source_type TEXT DEFAULT 'ai',
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_notes_client ON student_notes(client_id);

CREATE TABLE IF NOT EXISTS student_chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    content     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_chat_client ON student_chat_messages(client_id, course_id);

CREATE TABLE IF NOT EXISTS student_youtube_imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    youtube_url TEXT NOT NULL,
    video_title TEXT DEFAULT '',
    transcript  TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    note_id     INTEGER REFERENCES student_notes(id) ON DELETE SET NULL,
    deck_id     INTEGER REFERENCES student_flashcard_decks(id) ON DELETE SET NULL,
    quiz_id     INTEGER REFERENCES student_quizzes(id) ON DELETE SET NULL,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_yt_client ON student_youtube_imports(client_id);

CREATE TABLE IF NOT EXISTS student_xp (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    action      TEXT NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    detail      TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_xp_client ON student_xp(client_id);

CREATE TABLE IF NOT EXISTS student_badges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    badge_key   TEXT NOT NULL,
    earned_at   TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, badge_key)
);
CREATE INDEX IF NOT EXISTS idx_badges_client ON student_badges(client_id);

CREATE TABLE IF NOT EXISTS student_email_prefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    daily_email INTEGER DEFAULT 1,
    email_hour  INTEGER DEFAULT 7,
    timezone    TEXT DEFAULT 'America/Mexico_City',
    UNIQUE(client_id)
);
"""


# ── init ────────────────────────────────────────────────────

def init_student_db():
    """Create student tables. Called alongside MachReach's init_db()."""
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(STUDENT_PG_SCHEMA)
        else:
            db.executescript(STUDENT_SQLITE_SCHEMA)
    # Migrations
    _student_migrations()
    log.info("Student tables initialized.")


def _student_migrations():
    """Run safe column additions."""
    migrations = [
        ("student_course_files", "exam_id", "INTEGER DEFAULT NULL"),
        ("student_study_progress", "focus_minutes", "INTEGER DEFAULT 0"),
        ("student_study_progress", "pages_read", "INTEGER DEFAULT 0"),
        ("student_courses", "difficulty", "INTEGER DEFAULT 3"),
    ]
    for table, col, col_type in migrations:
        try:
            with get_db() as db:
                if _USE_PG:
                    db.cursor().execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                else:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # column already exists


# ── Canvas tokens ───────────────────────────────────────────

def save_canvas_token(client_id: int, canvas_url: str, token: str) -> int:
    with get_db() as db:
        # Upsert
        existing = _fetchval(
            db, "SELECT id FROM student_canvas_tokens WHERE client_id = %s",
            (client_id,),
        )
        if existing:
            _exec(db,
                  "UPDATE student_canvas_tokens SET canvas_url = %s, token = %s WHERE client_id = %s",
                  (canvas_url, token, client_id))
            return existing
        return _insert_returning_id(
            db,
            "INSERT INTO student_canvas_tokens (client_id, canvas_url, token) VALUES (%s, %s, %s) RETURNING id",
            (client_id, canvas_url, token),
            "INSERT INTO student_canvas_tokens (client_id, canvas_url, token) VALUES (?, ?, ?)",
        )


def get_canvas_token(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db, "SELECT * FROM student_canvas_tokens WHERE client_id = %s",
            (client_id,),
        )


def delete_canvas_token(client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_canvas_tokens WHERE client_id = %s", (client_id,))


# ── Courses ─────────────────────────────────────────────────

def upsert_course(client_id: int, canvas_course_id: int, name: str,
                  code: str = "", term: str = "") -> int:
    with get_db() as db:
        existing = _fetchone(
            db,
            "SELECT id FROM student_courses WHERE client_id = %s AND canvas_course_id = %s",
            (client_id, canvas_course_id),
        )
        if existing:
            _exec(db,
                  "UPDATE student_courses SET name = %s, code = %s, term = %s WHERE id = %s",
                  (name, code, term, existing["id"]))
            return existing["id"]
        return _insert_returning_id(
            db,
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, canvas_course_id, name, code, term),
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def update_course_analysis(course_db_id: int, analysis: dict):
    with get_db() as db:
        _exec(db,
              "UPDATE student_courses SET analysis_json = %s, last_synced = NOW() WHERE id = %s"
              if _USE_PG else
              "UPDATE student_courses SET analysis_json = ?, last_synced = datetime('now','localtime') WHERE id = ?",
              (json.dumps(analysis, ensure_ascii=False), course_db_id))


def get_courses(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db, "SELECT * FROM student_courses WHERE client_id = %s ORDER BY name",
            (client_id,),
        )


def get_course(course_db_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM student_courses WHERE id = %s", (course_db_id,))


def update_course_info(course_db_id: int, client_id: int, name: str, code: str,
                       grading: dict, weekly_schedule: list, study_tips: list):
    """Update editable fields of a course's analysis_json."""
    with get_db() as db:
        row = _fetchone(db, "SELECT analysis_json FROM student_courses WHERE id = %s AND client_id = %s",
                        (course_db_id, client_id))
        if not row:
            return
        analysis = json.loads(row["analysis_json"]) if isinstance(row["analysis_json"], str) else (row["analysis_json"] or {})
        analysis["grading"] = grading
        analysis["weekly_schedule"] = weekly_schedule
        analysis["study_tips"] = study_tips
        _exec(db, "UPDATE student_courses SET name = %s, code = %s, analysis_json = %s WHERE id = %s AND client_id = %s",
              (name, code, json.dumps(analysis, ensure_ascii=False), course_db_id, client_id))


# ── Exams ───────────────────────────────────────────────────

def save_exams(client_id: int, course_db_id: int, exams: list[dict]):
    """Replace all exams for a course with fresh analysis data."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_exams WHERE course_id = %s", (course_db_id,))
        for ex in exams:
            _exec(db,
                  "INSERT INTO student_exams (client_id, course_id, name, exam_date, weight_pct, topics_json) "
                  "VALUES (%s, %s, %s, %s, %s, %s)",
                  (client_id, course_db_id, ex.get("name", "Exam"),
                   ex.get("date"), ex.get("weight_pct", 0),
                   json.dumps(ex.get("topics", []), ensure_ascii=False)))


def get_exams(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT e.*, c.name as course_name FROM student_exams e "
            "JOIN student_courses c ON e.course_id = c.id "
            "WHERE e.client_id = %s ORDER BY e.exam_date",
            (client_id,),
        )


def get_upcoming_exams(client_id: int) -> list[dict]:
    with get_db() as db:
        today = datetime.now().strftime("%Y-%m-%d")
        return _fetchall(
            db,
            "SELECT e.*, c.name as course_name FROM student_exams e "
            "JOIN student_courses c ON e.course_id = c.id "
            "WHERE e.client_id = %s AND e.exam_date >= %s AND e.status = 'upcoming' "
            "ORDER BY e.exam_date",
            (client_id, today),
        )


def get_course_exams(course_db_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db, "SELECT * FROM student_exams WHERE course_id = %s ORDER BY exam_date",
            (course_db_id,),
        )


def upsert_exam(client_id: int, course_db_id: int, exam_id: int | None,
                name: str, exam_date: str | None, weight_pct: int,
                topics: list[str]) -> int:
    with get_db() as db:
        topics_json = json.dumps(topics, ensure_ascii=False)
        if exam_id:
            _exec(db,
                  "UPDATE student_exams SET name = %s, exam_date = %s, weight_pct = %s, "
                  "topics_json = %s WHERE id = %s AND client_id = %s",
                  (name, exam_date, weight_pct, topics_json, exam_id, client_id))
            return exam_id
        return _insert_returning_id(
            db,
            "INSERT INTO student_exams (client_id, course_id, name, exam_date, weight_pct, topics_json) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_db_id, name, exam_date, weight_pct, topics_json),
            "INSERT INTO student_exams (client_id, course_id, name, exam_date, weight_pct, topics_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )


def delete_exam(exam_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_exams WHERE id = %s AND client_id = %s",
              (exam_id, client_id))


# ── Study plans ─────────────────────────────────────────────

def save_study_plan(client_id: int, plan: dict, preferences: dict | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_study_plans (client_id, plan_json, preferences_json) "
            "VALUES (%s, %s, %s) RETURNING id",
            (client_id, json.dumps(plan, ensure_ascii=False),
             json.dumps(preferences or {}, ensure_ascii=False)),
            "INSERT INTO student_study_plans (client_id, plan_json, preferences_json) "
            "VALUES (?, ?, ?)",
        )


def get_latest_plan(client_id: int) -> dict | None:
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT * FROM student_study_plans WHERE client_id = %s ORDER BY generated_at DESC LIMIT 1",
            (client_id,),
        )
        if row:
            row = dict(row)
            row["plan_json"] = json.loads(row["plan_json"]) if isinstance(row["plan_json"], str) else row["plan_json"]
            row["preferences_json"] = json.loads(row["preferences_json"]) if isinstance(row["preferences_json"], str) else row["preferences_json"]
        return row


# ── Progress tracking ───────────────────────────────────────

def mark_day_complete(client_id: int, plan_date: str, notes: str = ""):
    with get_db() as db:
        existing = _fetchval(
            db,
            "SELECT id FROM student_study_progress WHERE client_id = %s AND plan_date = %s",
            (client_id, plan_date),
        )
        if existing:
            _exec(db,
                  "UPDATE student_study_progress SET completed = 1, notes = %s WHERE id = %s",
                  (notes, existing))
        else:
            _exec(db,
                  "INSERT INTO student_study_progress (client_id, plan_date, completed, notes) "
                  "VALUES (%s, %s, 1, %s)",
                  (client_id, plan_date, notes))


def get_progress(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT * FROM student_study_progress WHERE client_id = %s ORDER BY plan_date",
            (client_id,),
        )


def get_study_stats(client_id: int) -> dict:
    with get_db() as db:
        total = _fetchval(
            db, "SELECT COUNT(*) FROM student_study_progress WHERE client_id = %s",
            (client_id,),
        ) or 0
        done = _fetchval(
            db, "SELECT COUNT(*) FROM student_study_progress WHERE client_id = %s AND completed = 1",
            (client_id,),
        ) or 0
        exams = _fetchval(
            db,
            "SELECT COUNT(*) FROM student_exams WHERE client_id = %s AND status = 'upcoming'",
            (client_id,),
        ) or 0
        return {
            "days_tracked": total,
            "days_completed": done,
            "completion_pct": round(done / max(total, 1) * 100, 1),
            "upcoming_exams": exams,
        }


# ── Course files (manual uploads) ───────────────────────────

def save_course_file(client_id: int, course_id: int, original_name: str,
                     file_type: str, extracted_text: str, exam_id: int | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_course_files (client_id, course_id, original_name, file_type, extracted_text, exam_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_id, original_name, file_type, extracted_text, exam_id),
            "INSERT INTO student_course_files (client_id, course_id, original_name, file_type, extracted_text, exam_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )


def get_course_files(client_id: int, course_id: int, exam_id: int | None = None) -> list[dict]:
    with get_db() as db:
        if exam_id is not None:
            return _fetchall(
                db,
                "SELECT * FROM student_course_files WHERE client_id = %s AND course_id = %s AND exam_id = %s ORDER BY uploaded_at DESC",
                (client_id, course_id, exam_id),
            )
        return _fetchall(
            db,
            "SELECT * FROM student_course_files WHERE client_id = %s AND course_id = %s ORDER BY uploaded_at DESC",
            (client_id, course_id),
        )


def delete_course_file(file_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_course_files WHERE id = %s AND client_id = %s",
              (file_id, client_id))


def delete_course(course_id: int, client_id: int):
    """Delete a course and all its related data (exams, files)."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_course_files WHERE course_id = %s AND client_id = %s",
              (course_id, client_id))
        _exec(db, "DELETE FROM student_exams WHERE course_id = %s AND client_id = %s",
              (course_id, client_id))
        _exec(db, "DELETE FROM student_courses WHERE id = %s AND client_id = %s",
              (course_id, client_id))


# ── Focus / Pomodoro tracking ────────────────────────────────

def save_focus_session(client_id: int, mode: str, minutes: int, pages: int,
                       course_name: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_study_progress (client_id, plan_date, completed, notes, focus_minutes, pages_read) "
            "VALUES (%s, %s, 1, %s, %s, %s) RETURNING id",
            (client_id, datetime.now().strftime("%Y-%m-%d"),
             f"{mode}: {course_name}" if course_name else mode, minutes, pages),
            "INSERT INTO student_study_progress (client_id, plan_date, completed, notes, focus_minutes, pages_read) "
            "VALUES (?, ?, 1, ?, ?, ?)",
        )


def get_focus_stats(client_id: int) -> dict:
    with get_db() as db:
        total_min = _fetchval(
            db, "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE client_id = %s",
            (client_id,),
        ) or 0
        total_pages = _fetchval(
            db, "SELECT COALESCE(SUM(pages_read),0) FROM student_study_progress WHERE client_id = %s",
            (client_id,),
        ) or 0
        sessions = _fetchval(
            db, "SELECT COUNT(*) FROM student_study_progress WHERE client_id = %s AND focus_minutes > 0",
            (client_id,),
        ) or 0
        # Streak: consecutive days with focus sessions
        rows = _fetchall(
            db, "SELECT DISTINCT plan_date FROM student_study_progress "
                "WHERE client_id = %s AND focus_minutes > 0 ORDER BY plan_date DESC",
            (client_id,),
        )
        streak = 0
        today = datetime.now().date()
        for r in rows:
            d = datetime.strptime(r["plan_date"], "%Y-%m-%d").date()
            expected = today - __import__('datetime').timedelta(days=streak)
            if d == expected:
                streak += 1
            else:
                break
        return {
            "total_minutes": total_min,
            "total_hours": round(total_min / 60, 1),
            "total_pages": total_pages,
            "sessions": sessions,
            "streak_days": streak,
        }


# ── Schedule settings (per-day availability) ────────────────

def save_schedule_settings(client_id: int, settings: list[dict]):
    """Save weekly schedule settings.
    settings: [{"day": 0, "hours": 4.0, "free": False}, ...]  (day 0=Mon..6=Sun)
    """
    with get_db() as db:
        _exec(db, "DELETE FROM student_schedule_settings WHERE client_id = %s", (client_id,))
        for s in settings:
            _exec(db,
                  "INSERT INTO student_schedule_settings (client_id, day_of_week, available_hours, is_free_day) "
                  "VALUES (%s, %s, %s, %s)",
                  (client_id, s["day"], s.get("hours", 0), 1 if s.get("free") else 0))


def get_schedule_settings(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db, "SELECT * FROM student_schedule_settings WHERE client_id = %s ORDER BY day_of_week",
            (client_id,),
        )
        return [dict(r) for r in rows]


# ── Course difficulty ────────────────────────────────────────

def set_course_difficulty(client_id: int, course_db_id: int, difficulty: int):
    """Set difficulty 1-5 for a course."""
    difficulty = max(1, min(5, difficulty))
    with get_db() as db:
        _exec(db, "UPDATE student_courses SET difficulty = %s WHERE id = %s AND client_id = %s",
              (difficulty, course_db_id, client_id))


def get_course_difficulty(client_id: int, course_db_id: int) -> int:
    with get_db() as db:
        val = _fetchval(
            db, "SELECT difficulty FROM student_courses WHERE id = %s AND client_id = %s",
            (course_db_id, client_id),
        )
        return val if val is not None else 3


# ── Assignment-level progress ────────────────────────────────

def toggle_assignment_complete(client_id: int, plan_date: str, session_index: int,
                               completed: bool):
    """Mark a specific session/assignment within a day as complete or incomplete."""
    with get_db() as db:
        existing = _fetchval(
            db,
            "SELECT id FROM student_assignment_progress WHERE client_id = %s AND plan_date = %s AND session_index = %s",
            (client_id, plan_date, session_index),
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if existing:
            _exec(db,
                  "UPDATE student_assignment_progress SET completed = %s, completed_at = %s WHERE id = %s",
                  (1 if completed else 0, now_str if completed else None, existing))
        else:
            _exec(db,
                  "INSERT INTO student_assignment_progress (client_id, plan_date, session_index, completed, completed_at) "
                  "VALUES (%s, %s, %s, %s, %s)",
                  (client_id, plan_date, session_index, 1 if completed else 0,
                   now_str if completed else None))


def get_assignment_progress(client_id: int, plan_date: str) -> list[dict]:
    """Get completion status for all assignments on a given date."""
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT * FROM student_assignment_progress WHERE client_id = %s AND plan_date = %s ORDER BY session_index",
            (client_id, plan_date),
        )
        return [dict(r) for r in rows]


def get_incomplete_assignments(client_id: int, before_date: str) -> list[dict]:
    """Get all incomplete assignments before a given date (for rollover)."""
    with get_db() as db:
        plan_row = _fetchone(
            db,
            "SELECT plan_json FROM student_study_plans WHERE client_id = %s ORDER BY generated_at DESC LIMIT 1",
            (client_id,),
        )
        if not plan_row:
            return []
        plan = json.loads(plan_row["plan_json"]) if isinstance(plan_row["plan_json"], str) else plan_row["plan_json"]
        daily_plan = plan.get("daily_plan", [])

        incomplete = []
        for day in daily_plan:
            d = day.get("date", "")
            if d >= before_date:
                continue
            sessions = day.get("sessions", [])
            progress = _fetchall(
                db,
                "SELECT session_index, completed FROM student_assignment_progress "
                "WHERE client_id = %s AND plan_date = %s",
                (client_id, d),
            )
            completed_indices = {r["session_index"] for r in progress if r["completed"]}
            for idx, s in enumerate(sessions):
                if idx not in completed_indices:
                    incomplete.append({
                        "date": d,
                        "session_index": idx,
                        "course": s.get("course", ""),
                        "topic": s.get("topic", ""),
                        "hours": s.get("hours", 0),
                        "type": s.get("type", "study"),
                        "priority": s.get("priority", "medium"),
                    })
        return incomplete


def get_all_student_client_ids() -> list[int]:
    """Return all client IDs that have at least one study plan."""
    with get_db() as db:
        rows = _fetchall(db, "SELECT DISTINCT client_id FROM student_study_plans", ())
        return [r["client_id"] for r in rows]


# ── Cleanup ─────────────────────────────────────────────────

def delete_student_data(client_id: int):
    """Remove all student data for a client (account deletion)."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_quiz_questions WHERE quiz_id IN (SELECT id FROM student_quizzes WHERE client_id = %s)", (client_id,))
        _exec(db, "DELETE FROM student_quizzes WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_flashcards WHERE deck_id IN (SELECT id FROM student_flashcard_decks WHERE client_id = %s)", (client_id,))
        _exec(db, "DELETE FROM student_flashcard_decks WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_notes WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_assignment_progress WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_schedule_settings WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_study_progress WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_study_plans WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_exams WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_course_files WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_courses WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_canvas_tokens WHERE client_id = %s", (client_id,))


# ── Flashcard decks & cards ─────────────────────────────────

def create_flashcard_deck(client_id: int, title: str, course_id: int | None = None,
                          exam_id: int | None = None, source_type: str = "ai") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_flashcard_decks (client_id, course_id, exam_id, title, source_type) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_id, exam_id, title, source_type),
            "INSERT INTO student_flashcard_decks (client_id, course_id, exam_id, title, source_type) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def add_flashcards(deck_id: int, cards: list[dict]):
    """Insert a batch of flashcards into a deck."""
    with get_db() as db:
        for c in cards:
            _exec(db,
                  "INSERT INTO student_flashcards (deck_id, front, back) VALUES (%s, %s, %s)",
                  (deck_id, c["front"], c["back"]))
        _exec(db, "UPDATE student_flashcard_decks SET card_count = %s WHERE id = %s",
              (len(cards), deck_id))


def get_flashcard_decks(client_id: int, course_id: int | None = None) -> list[dict]:
    with get_db() as db:
        if course_id:
            return _fetchall(
                db,
                "SELECT d.*, c.name as course_name FROM student_flashcard_decks d "
                "LEFT JOIN student_courses c ON d.course_id = c.id "
                "WHERE d.client_id = %s AND d.course_id = %s ORDER BY d.created_at DESC",
                (client_id, course_id),
            )
        return _fetchall(
            db,
            "SELECT d.*, c.name as course_name FROM student_flashcard_decks d "
            "LEFT JOIN student_courses c ON d.course_id = c.id "
            "WHERE d.client_id = %s ORDER BY d.created_at DESC",
            (client_id,),
        )


def get_flashcard_deck(deck_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT d.*, c.name as course_name FROM student_flashcard_decks d "
            "LEFT JOIN student_courses c ON d.course_id = c.id "
            "WHERE d.id = %s AND d.client_id = %s",
            (deck_id, client_id),
        )


def get_flashcards(deck_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db, "SELECT * FROM student_flashcards WHERE deck_id = %s ORDER BY id",
            (deck_id,),
        )


def update_flashcard_progress(card_id: int, correct: bool):
    with get_db() as db:
        if correct:
            _exec(db,
                  "UPDATE student_flashcards SET times_seen = times_seen + 1, "
                  "times_correct = times_correct + 1 WHERE id = %s", (card_id,))
        else:
            _exec(db,
                  "UPDATE student_flashcards SET times_seen = times_seen + 1 WHERE id = %s",
                  (card_id,))


def delete_flashcard_deck(deck_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_flashcards WHERE deck_id = %s", (deck_id,))
        _exec(db, "DELETE FROM student_flashcard_decks WHERE id = %s AND client_id = %s",
              (deck_id, client_id))


def update_flashcard(card_id: int, deck_id: int, front: str, back: str):
    """Update a single flashcard's front/back text."""
    with get_db() as db:
        _exec(db,
              "UPDATE student_flashcards SET front = %s, back = %s WHERE id = %s AND deck_id = %s",
              (front, back, card_id, deck_id))


def add_flashcard(deck_id: int, front: str, back: str) -> int:
    """Add a single flashcard to a deck and bump card_count."""
    with get_db() as db:
        card_id = _insert_returning_id(
            db,
            "INSERT INTO student_flashcards (deck_id, front, back) VALUES (%s, %s, %s) RETURNING id",
            (deck_id, front, back),
            "INSERT INTO student_flashcards (deck_id, front, back) VALUES (?, ?, ?)",
        )
        _exec(db,
              "UPDATE student_flashcard_decks SET card_count = "
              "(SELECT COUNT(*) FROM student_flashcards WHERE deck_id = %s) WHERE id = %s",
              (deck_id, deck_id))
        return card_id


def delete_flashcard(card_id: int, deck_id: int):
    """Delete a single flashcard and update deck count."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_flashcards WHERE id = %s AND deck_id = %s",
              (card_id, deck_id))
        _exec(db,
              "UPDATE student_flashcard_decks SET card_count = "
              "(SELECT COUNT(*) FROM student_flashcards WHERE deck_id = %s) WHERE id = %s",
              (deck_id, deck_id))


# ── Quizzes ─────────────────────────────────────────────────

def create_quiz(client_id: int, title: str, difficulty: str = "medium",
                course_id: int | None = None, exam_id: int | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_quizzes (client_id, course_id, exam_id, title, difficulty) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_id, exam_id, title, difficulty),
            "INSERT INTO student_quizzes (client_id, course_id, exam_id, title, difficulty) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def add_quiz_questions(quiz_id: int, questions: list[dict]):
    """Insert a batch of quiz questions."""
    with get_db() as db:
        for idx, q in enumerate(questions):
            _exec(db,
                  "INSERT INTO student_quiz_questions "
                  "(quiz_id, question, option_a, option_b, option_c, option_d, correct, explanation, sort_order) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                  (quiz_id, q["question"], q.get("option_a", ""), q.get("option_b", ""),
                   q.get("option_c", ""), q.get("option_d", ""), q["correct"],
                   q.get("explanation", ""), idx))
        _exec(db, "UPDATE student_quizzes SET question_count = %s WHERE id = %s",
              (len(questions), quiz_id))


def get_quizzes(client_id: int, course_id: int | None = None) -> list[dict]:
    with get_db() as db:
        if course_id:
            return _fetchall(
                db,
                "SELECT q.*, c.name as course_name FROM student_quizzes q "
                "LEFT JOIN student_courses c ON q.course_id = c.id "
                "WHERE q.client_id = %s AND q.course_id = %s ORDER BY q.created_at DESC",
                (client_id, course_id),
            )
        return _fetchall(
            db,
            "SELECT q.*, c.name as course_name FROM student_quizzes q "
            "LEFT JOIN student_courses c ON q.course_id = c.id "
            "WHERE q.client_id = %s ORDER BY q.created_at DESC",
            (client_id,),
        )


def get_quiz(quiz_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT q.*, c.name as course_name FROM student_quizzes q "
            "LEFT JOIN student_courses c ON q.course_id = c.id "
            "WHERE q.id = %s AND q.client_id = %s",
            (quiz_id, client_id),
        )


def get_quiz_questions(quiz_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db, "SELECT * FROM student_quiz_questions WHERE quiz_id = %s ORDER BY sort_order",
            (quiz_id,),
        )


def update_quiz_score(quiz_id: int, score: int):
    with get_db() as db:
        _exec(db,
              "UPDATE student_quizzes SET attempts = attempts + 1, "
              "best_score = CASE WHEN %s > best_score THEN %s ELSE best_score END "
              "WHERE id = %s",
              (score, score, quiz_id))


def delete_quiz(quiz_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_quiz_questions WHERE quiz_id = %s", (quiz_id,))
        _exec(db, "DELETE FROM student_quizzes WHERE id = %s AND client_id = %s",
              (quiz_id, client_id))


# ── Notes ───────────────────────────────────────────────────

def create_note(client_id: int, title: str, content_html: str,
                course_id: int | None = None, source_type: str = "ai") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_notes (client_id, course_id, title, content_html, source_type) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_id, title, content_html, source_type),
            "INSERT INTO student_notes (client_id, course_id, title, content_html, source_type) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def get_notes(client_id: int, course_id: int | None = None) -> list[dict]:
    with get_db() as db:
        if course_id:
            return _fetchall(
                db,
                "SELECT n.*, c.name as course_name FROM student_notes n "
                "LEFT JOIN student_courses c ON n.course_id = c.id "
                "WHERE n.client_id = %s AND n.course_id = %s ORDER BY n.created_at DESC",
                (client_id, course_id),
            )
        return _fetchall(
            db,
            "SELECT n.*, c.name as course_name FROM student_notes n "
            "LEFT JOIN student_courses c ON n.course_id = c.id "
            "WHERE n.client_id = %s ORDER BY n.created_at DESC",
            (client_id,),
        )


def get_note(note_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db, "SELECT * FROM student_notes WHERE id = %s AND client_id = %s",
            (note_id, client_id),
        )


def update_note(note_id: int, client_id: int, content_html: str):
    with get_db() as db:
        _exec(db,
              "UPDATE student_notes SET content_html = %s, updated_at = %s WHERE id = %s AND client_id = %s"
              if _USE_PG else
              "UPDATE student_notes SET content_html = ?, updated_at = datetime('now','localtime') WHERE id = ? AND client_id = ?",
              (content_html, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note_id, client_id)
              if _USE_PG else (content_html, note_id, client_id))


def delete_note(note_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_notes WHERE id = %s AND client_id = %s",
              (note_id, client_id))


# ── Chat messages ───────────────────────────────────────────

def add_chat_message(client_id: int, role: str, content: str, course_id: int | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_chat_messages (client_id, course_id, role, content) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (client_id, course_id, role, content),
            "INSERT INTO student_chat_messages (client_id, course_id, role, content) "
            "VALUES (?, ?, ?, ?)",
        )


def get_chat_history(client_id: int, course_id: int | None = None, limit: int = 50) -> list[dict]:
    with get_db() as db:
        if course_id:
            return _fetchall(
                db,
                "SELECT * FROM student_chat_messages WHERE client_id = %s AND course_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (client_id, course_id, limit),
            )
        return _fetchall(
            db,
            "SELECT * FROM student_chat_messages WHERE client_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (client_id, limit),
        )


def clear_chat_history(client_id: int, course_id: int | None = None):
    with get_db() as db:
        if course_id:
            _exec(db, "DELETE FROM student_chat_messages WHERE client_id = %s AND course_id = %s",
                  (client_id, course_id))
        else:
            _exec(db, "DELETE FROM student_chat_messages WHERE client_id = %s", (client_id,))


# ── YouTube imports ─────────────────────────────────────────

def create_youtube_import(client_id: int, youtube_url: str, video_title: str = "",
                          transcript: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_youtube_imports (client_id, youtube_url, video_title, transcript) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (client_id, youtube_url, video_title, transcript),
            "INSERT INTO student_youtube_imports (client_id, youtube_url, video_title, transcript) "
            "VALUES (?, ?, ?, ?)",
        )


def update_youtube_import(import_id: int, **kwargs):
    with get_db() as db:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("status", "transcript", "video_title", "note_id", "deck_id", "quiz_id"):
                sets.append(f"{k} = %s")
                vals.append(v)
        if not sets:
            return
        vals.append(import_id)
        _exec(db, f"UPDATE student_youtube_imports SET {', '.join(sets)} WHERE id = %s",
              tuple(vals))


def get_youtube_imports(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT * FROM student_youtube_imports WHERE client_id = %s ORDER BY created_at DESC",
            (client_id,),
        )


# ── XP / Gamification ──────────────────────────────────────

def award_xp(client_id: int, action: str, xp: int, detail: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s) RETURNING id",
            (client_id, action, xp, detail),
            "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (?, ?, ?, ?)",
        )


def get_total_xp(client_id: int) -> int:
    with get_db() as db:
        return _fetchval(
            db,
            "SELECT COALESCE(SUM(xp), 0) FROM student_xp WHERE client_id = %s",
            (client_id,),
        ) or 0


def get_xp_history(client_id: int, limit: int = 30) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT * FROM student_xp WHERE client_id = %s ORDER BY created_at DESC LIMIT %s",
            (client_id, limit),
        )


def get_streak_days(client_id: int) -> int:
    """Count consecutive days with XP activity ending today."""
    with get_db() as db:
        if _USE_PG:
            rows = _fetchall(
                db,
                "SELECT DISTINCT created_at::date AS d FROM student_xp "
                "WHERE client_id = %s ORDER BY d DESC LIMIT 90",
                (client_id,),
            )
        else:
            rows = _fetchall(
                db,
                "SELECT DISTINCT date(created_at) AS d FROM student_xp "
                "WHERE client_id = %s ORDER BY d DESC LIMIT 90",
                (client_id,),
            )
    if not rows:
        return 0
    from datetime import date as _date, timedelta
    today = _date.today()
    streak = 0
    for r in rows:
        d = r["d"]
        if isinstance(d, str):
            d = _date.fromisoformat(d)
        expected = today - timedelta(days=streak)
        if d == expected:
            streak += 1
        else:
            break
    return streak


# ── Badges ──────────────────────────────────────────────────

BADGE_DEFS = {
    "first_login":     {"emoji": "🎉", "name": "Welcome!",        "desc": "Logged in for the first time"},
    "first_quiz":      {"emoji": "📝", "name": "Quiz Rookie",     "desc": "Completed your first quiz"},
    "quiz_master":     {"emoji": "🏆", "name": "Quiz Master",     "desc": "Scored 100% on a quiz"},
    "flashcard_fan":   {"emoji": "🃏", "name": "Flashcard Fan",   "desc": "Reviewed 100 flashcards"},
    "streak_3":        {"emoji": "🔥", "name": "On Fire!",        "desc": "3-day study streak"},
    "streak_7":        {"emoji": "⚡", "name": "Unstoppable",      "desc": "7-day study streak"},
    "streak_30":       {"emoji": "💎", "name": "Diamond Student",  "desc": "30-day study streak"},
    "note_taker":      {"emoji": "📒", "name": "Note Taker",      "desc": "Created 10 notes"},
    "youtube_scholar": {"emoji": "🎬", "name": "YouTube Scholar", "desc": "Imported 5 YouTube videos"},
    "xp_100":          {"emoji": "⭐", "name": "Rising Star",     "desc": "Earned 100 XP"},
    "xp_500":          {"emoji": "🌟", "name": "Shining Star",    "desc": "Earned 500 XP"},
    "xp_1000":         {"emoji": "💫", "name": "Superstar",       "desc": "Earned 1000 XP"},
    "chat_curious":    {"emoji": "💬", "name": "Curious Mind",    "desc": "Asked 10 questions to AI tutor"},
}

LEVEL_THRESHOLDS = [
    (0, "Freshman"),
    (100, "Sophomore"),
    (300, "Junior"),
    (600, "Senior"),
    (1000, "Scholar"),
    (2000, "Master"),
    (5000, "Professor"),
]


def get_level(xp: int) -> tuple[str, int, int]:
    """Return (level_name, current_level_xp_floor, next_level_xp_floor)."""
    for i in range(len(LEVEL_THRESHOLDS) - 1, -1, -1):
        if xp >= LEVEL_THRESHOLDS[i][0]:
            floor = LEVEL_THRESHOLDS[i][0]
            ceil = LEVEL_THRESHOLDS[i + 1][0] if i + 1 < len(LEVEL_THRESHOLDS) else floor + 1000
            return LEVEL_THRESHOLDS[i][1], floor, ceil
    return "Freshman", 0, 100


def earn_badge(client_id: int, badge_key: str) -> bool:
    """Try to award a badge. Returns True if newly earned, False if already had."""
    if badge_key not in BADGE_DEFS:
        return False
    with get_db() as db:
        try:
            _exec(db, "INSERT INTO student_badges (client_id, badge_key) VALUES (%s, %s)",
                  (client_id, badge_key))
            return True
        except Exception:
            return False


def get_badges(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db, "SELECT * FROM student_badges WHERE client_id = %s ORDER BY earned_at",
            (client_id,),
        )
    result = []
    for r in rows:
        info = BADGE_DEFS.get(r["badge_key"], {})
        result.append({**r, **info})
    return result


# ── Email prefs ─────────────────────────────────────────────

def get_email_prefs(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db, "SELECT * FROM student_email_prefs WHERE client_id = %s", (client_id,),
        )


def upsert_email_prefs(client_id: int, daily_email: bool = True, email_hour: int = 7,
                        timezone: str = "America/Mexico_City"):
    with get_db() as db:
        existing = _fetchone(db, "SELECT id FROM student_email_prefs WHERE client_id = %s",
                             (client_id,))
        de = 1 if daily_email else 0
        if existing:
            _exec(db,
                  "UPDATE student_email_prefs SET daily_email = %s, email_hour = %s, timezone = %s "
                  "WHERE client_id = %s",
                  (de, email_hour, timezone, client_id))
        else:
            _exec(db,
                  "INSERT INTO student_email_prefs (client_id, daily_email, email_hour, timezone) "
                  "VALUES (%s, %s, %s, %s)",
                  (client_id, de, email_hour, timezone))


# ── Weak topics ─────────────────────────────────────────────

def get_flashcard_accuracy(client_id: int) -> list[dict]:
    """Get per-deck accuracy rates for flashcards."""
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT d.id, d.title, d.course_id, c.name as course_name, "
            "SUM(f.times_seen) as total_seen, SUM(f.times_correct) as total_correct, "
            "CASE WHEN SUM(f.times_seen) > 0 THEN "
            "ROUND(100.0 * SUM(f.times_correct) / SUM(f.times_seen)) ELSE 0 END as accuracy "
            "FROM student_flashcard_decks d "
            "JOIN student_flashcards f ON f.deck_id = d.id "
            "LEFT JOIN student_courses c ON d.course_id = c.id "
            "WHERE d.client_id = %s AND f.times_seen > 0 "
            "GROUP BY d.id, d.title, d.course_id, c.name "
            "ORDER BY accuracy ASC",
            (client_id,),
        )


def get_quiz_scores(client_id: int) -> list[dict]:
    """Get quiz scores for weak topic detection."""
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT q.id, q.title, q.course_id, c.name as course_name, "
            "q.best_score, q.attempts, q.question_count "
            "FROM student_quizzes q "
            "LEFT JOIN student_courses c ON q.course_id = c.id "
            "WHERE q.client_id = %s AND q.attempts > 0 "
            "ORDER BY q.best_score ASC",
            (client_id,),
        )
