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

CREATE TABLE IF NOT EXISTS student_date_overrides (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    override_date DATE NOT NULL,
    available_hours REAL DEFAULT 0,
    is_free_day BOOLEAN DEFAULT FALSE,
    note        TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, override_date)
);
CREATE INDEX IF NOT EXISTS idx_student_date_overrides_client ON student_date_overrides(client_id, override_date);

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
    topic       TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS student_date_overrides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    override_date TEXT NOT NULL,
    available_hours REAL DEFAULT 0,
    is_free_day INTEGER DEFAULT 0,
    note        TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, override_date)
);
CREATE INDEX IF NOT EXISTS idx_student_date_overrides_client ON student_date_overrides(client_id, override_date);

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
    topic       TEXT DEFAULT '',
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
    # Wallet (coins, freezes, banners)
    try:
        init_wallet_table()
    except Exception as e:
        log.exception("init_wallet_table failed: %s", e)
    # Timed boosts (2x XP, 2x coins)
    try:
        init_boosts_table()
    except Exception as e:
        log.exception("init_boosts_table failed: %s", e)
    # Quiz Duels v2 (file-upload + AI-generated, synchronous)
    try:
        init_quiz_duels_tables()
    except Exception as e:
        log.exception("init_quiz_duels_tables failed: %s", e)
    # Academic identity layer (countries, universities, majors, leagues).
    # Imported lazily so a bad seed file can't take down the whole app.
    try:
        from student.academic import init_academic_db
        init_academic_db()
    except Exception as e:
        log.exception("init_academic_db failed: %s", e)
    log.info("Student tables initialized.")


def _create_table_safe(pg_sql: str, sqlite_sql: str):
    """Create a table if it doesn't exist (safe for migrations)."""
    try:
        with get_db() as db:
            if _USE_PG:
                db.cursor().execute(pg_sql)
            else:
                db.execute(sqlite_sql)
    except Exception:
        pass


def _student_migrations():
    """Run safe column additions."""
    migrations = [
        ("student_course_files", "exam_id", "INTEGER DEFAULT NULL"),
        ("student_study_progress", "focus_minutes", "INTEGER DEFAULT 0"),
        ("student_study_progress", "pages_read", "INTEGER DEFAULT 0"),
        ("student_courses", "difficulty", "INTEGER DEFAULT 3"),
        ("student_email_prefs", "university", "TEXT DEFAULT ''"),
        ("student_email_prefs", "field_of_study", "TEXT DEFAULT ''"),
        ("student_email_prefs", "lang", "TEXT DEFAULT 'en'"),
        # SRS columns for spaced repetition
        ("student_flashcards", "easiness_factor", "REAL DEFAULT 2.5"),
        ("student_flashcards", "interval_days", "INTEGER DEFAULT 0"),
        ("student_flashcards", "repetitions", "INTEGER DEFAULT 0"),
        # Study Exchange — public notes
        ("student_notes", "is_public", "BOOLEAN DEFAULT FALSE"),
        ("student_notes", "likes", "INTEGER DEFAULT 0"),
        ("student_notes", "university", "TEXT DEFAULT ''"),
        ("student_notes", "author_name", "TEXT DEFAULT ''"),
        # GPA country preference
        ("student_email_prefs", "gpa_country", "TEXT DEFAULT 'us'"),
        # Quiz per-question topic tag (for analytics)
        ("student_quiz_questions", "topic", "TEXT DEFAULT ''"),
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
    # Study Exchange — note likes table
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_note_likes ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL, "
        "note_id INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, note_id))",
        "CREATE TABLE IF NOT EXISTS student_note_likes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL, "
        "note_id INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, note_id))",
    )
    # Personal leaderboard groups
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_lb_groups ("
        "id SERIAL PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "owner_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "invite_code TEXT NOT NULL UNIQUE, "
        "created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_lb_groups ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "owner_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "invite_code TEXT NOT NULL UNIQUE, "
        "created_at TIMESTAMP DEFAULT (datetime('now','localtime')))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_lb_members ("
        "id SERIAL PRIMARY KEY, "
        "group_id INTEGER NOT NULL REFERENCES student_lb_groups(id) ON DELETE CASCADE, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "joined_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(group_id, client_id))",
        "CREATE TABLE IF NOT EXISTS student_lb_members ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "group_id INTEGER NOT NULL REFERENCES student_lb_groups(id) ON DELETE CASCADE, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "joined_at TIMESTAMP DEFAULT (datetime('now','localtime')), "
        "UNIQUE(group_id, client_id))",
    )
    # Track unique users who forked/used a shared note (for XP)
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_note_forks ("
        "id SERIAL PRIMARY KEY, "
        "note_id INTEGER NOT NULL, "
        "forker_id INTEGER NOT NULL, "
        "author_id INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(note_id, forker_id))",
        "CREATE TABLE IF NOT EXISTS student_note_forks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "note_id INTEGER NOT NULL, "
        "forker_id INTEGER NOT NULL, "
        "author_id INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT (datetime('now','localtime')), "
        "UNIQUE(note_id, forker_id))",
    )
    # Streak freezes — 1 free auto-freeze per ISO week
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_streak_freezes ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "freeze_date DATE NOT NULL, "
        "iso_year INTEGER NOT NULL, "
        "iso_week INTEGER NOT NULL, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, iso_year, iso_week))",
        "CREATE TABLE IF NOT EXISTS student_streak_freezes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "freeze_date TEXT NOT NULL, "
        "iso_year INTEGER NOT NULL, "
        "iso_week INTEGER NOT NULL, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, iso_year, iso_week))",
    )
    # Daily quests — 3 randomized goals per day
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_daily_quests ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "quest_date DATE NOT NULL, "
        "quest_key TEXT NOT NULL, "
        "target INTEGER NOT NULL DEFAULT 1, "
        "progress INTEGER NOT NULL DEFAULT 0, "
        "xp_reward INTEGER NOT NULL DEFAULT 10, "
        "completed_at TIMESTAMP, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, quest_date, quest_key))",
        "CREATE TABLE IF NOT EXISTS student_daily_quests ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "quest_date TEXT NOT NULL, "
        "quest_key TEXT NOT NULL, "
        "target INTEGER NOT NULL DEFAULT 1, "
        "progress INTEGER NOT NULL DEFAULT 0, "
        "xp_reward INTEGER NOT NULL DEFAULT 10, "
        "completed_at TEXT, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, quest_date, quest_key))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_daily_quest_bundles ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "quest_date DATE NOT NULL, "
        "completed_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, quest_date))",
        "CREATE TABLE IF NOT EXISTS student_daily_quest_bundles ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "quest_date TEXT NOT NULL, "
        "completed_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, quest_date))",
    )
    # Friends — undirected after acceptance, but stored as one row per direction
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_friends ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "friend_client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, friend_client_id))",
        "CREATE TABLE IF NOT EXISTS student_friends ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "friend_client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, friend_client_id))",
    )
    # 7-day duels (head-to-head focus minutes)
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_duels ("
        "id SERIAL PRIMARY KEY, "
        "challenger_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "opponent_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "started_at TIMESTAMP DEFAULT NOW(), "
        "ends_at TIMESTAMP NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "winner_id INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "challenger_minutes INTEGER NOT NULL DEFAULT 0, "
        "opponent_minutes INTEGER NOT NULL DEFAULT 0, "
        "settled_at TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS student_duels ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "challenger_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "opponent_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "started_at TEXT DEFAULT (datetime('now','localtime')), "
        "ends_at TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "winner_id INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "challenger_minutes INTEGER NOT NULL DEFAULT 0, "
        "opponent_minutes INTEGER NOT NULL DEFAULT 0, "
        "settled_at TEXT)",
    )


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


def create_manual_course(client_id: int, name: str, code: str = "", term: str = "") -> int:
    """Create a course not tied to Canvas. Uses a negative synthetic
    canvas_course_id so the UNIQUE(client_id, canvas_course_id) index stays happy."""
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT MIN(canvas_course_id) AS min_id FROM student_courses "
            "WHERE client_id = %s AND canvas_course_id < 0",
            (client_id,),
        )
        next_id = -1
        if row and row.get("min_id") is not None:
            try:
                next_id = int(row["min_id"]) - 1
            except Exception:
                next_id = -1
        return _insert_returning_id(
            db,
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, next_id, name, code, term),
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def delete_course(client_id: int, course_db_id: int) -> bool:
    with get_db() as db:
        _exec(
            db,
            "DELETE FROM student_courses WHERE id = %s AND client_id = %s",
            (course_db_id, client_id),
        )
    return True


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

# Server-side ceilings to defend against orphaned timers, malicious clients,
# or stale localStorage state crediting absurd amounts of "study time".
FOCUS_MAX_MINUTES_PER_SESSION = 480   # 8h cap on a single save
FOCUS_MAX_MINUTES_PER_DAY     = 16 * 60  # 16h cap on a single calendar day

def save_focus_session(client_id: int, mode: str, minutes: int, pages: int,
                       course_name: str = "") -> int:
    """Persist a focus session.
    Returns the new row id, or 0 if the request was rejected for being garbage
    (negative, NaN, or so large that it must be a bug / abandoned timer).
    """
    # Sanitize inputs.
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 0
    try:
        pages = int(pages)
    except (TypeError, ValueError):
        pages = 0
    if minutes < 0:
        minutes = 0
    if pages < 0:
        pages = 0

    # Per-session cap (prevents a stopwatch left running for days from
    # crediting tens of hours in one shot).
    if minutes > FOCUS_MAX_MINUTES_PER_SESSION:
        minutes = FOCUS_MAX_MINUTES_PER_SESSION

    # Reject empty saves entirely (no minutes, no pages).
    if minutes <= 0 and pages <= 0:
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as db:
        # Per-day cap: clamp `minutes` so the calendar-day total never exceeds
        # FOCUS_MAX_MINUTES_PER_DAY. This is the second line of defence against
        # orphaned timers re-crediting after a long absence.
        already_today = _fetchval(
            db,
            "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s",
            (client_id, f"{today}%"),
        ) or 0
        room_left = max(0, FOCUS_MAX_MINUTES_PER_DAY - int(already_today))
        if minutes > room_left:
            minutes = room_left
        if minutes <= 0 and pages <= 0:
            return 0

        return _insert_returning_id(
            db,
            "INSERT INTO student_study_progress (client_id, plan_date, completed, notes, focus_minutes, pages_read) "
            "VALUES (%s, %s, 1, %s, %s, %s) RETURNING id",
            (client_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        from datetime import timedelta as _td
        # Build set of unique activity dates
        activity_dates = set()
        for r in rows:
            d_str = r["plan_date"][:10]
            try:
                activity_dates.add(datetime.strptime(d_str, "%Y-%m-%d").date())
            except ValueError:
                continue
        # Allow today to be missing without breaking — start counting from yesterday
        cur = today if today in activity_dates else (today - _td(days=1))
        while cur in activity_dates:
            streak += 1
            cur -= _td(days=1)
        return {
            "total_minutes": total_min,
            "total_hours": round(total_min / 60, 1),
            "total_pages": total_pages,
            "sessions": sessions,
            "streak_days": streak,
        }


def get_focus_stats_today(client_id: int) -> dict:
    """Focus stats restricted to TODAY (the user's local calendar day).

    Used by the Focus Mode page header so the numbers reflect what the
    student has put in *today*, not their lifetime totals.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    like_today = today_str + "%"
    with get_db() as db:
        total_min = _fetchval(
            db,
            "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s",
            (client_id, like_today),
        ) or 0
        total_pages = _fetchval(
            db,
            "SELECT COALESCE(SUM(pages_read),0) FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s",
            (client_id, like_today),
        ) or 0
        sessions = _fetchval(
            db,
            "SELECT COUNT(*) FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s AND focus_minutes > 0",
            (client_id, like_today),
        ) or 0
    # Reuse the lifetime call only for the streak number (cheap, single query already done)
    streak = get_focus_stats(client_id).get("streak_days", 0)
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
                  (client_id, s["day"], s.get("hours", 0), bool(s.get("free"))))


def get_schedule_settings(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db, "SELECT * FROM student_schedule_settings WHERE client_id = %s ORDER BY day_of_week",
            (client_id,),
        )
        return [dict(r) for r in rows]


# ── Date overrides (per-date availability — overrides weekly defaults) ──

def save_date_override(client_id: int, override_date: str, hours: float, is_free: bool, note: str = "") -> None:
    """Upsert a per-date availability override.
    override_date: ISO 'YYYY-MM-DD'.
    """
    with get_db() as db:
        _exec(db, "DELETE FROM student_date_overrides WHERE client_id = %s AND override_date = %s",
              (client_id, override_date))
        _exec(db,
              "INSERT INTO student_date_overrides (client_id, override_date, available_hours, is_free_day, note) "
              "VALUES (%s, %s, %s, %s, %s)",
              (client_id, override_date, hours, bool(is_free), note))


def delete_date_override(client_id: int, override_date: str) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM student_date_overrides WHERE client_id = %s AND override_date = %s",
              (client_id, override_date))


def get_date_overrides(client_id: int, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    with get_db() as db:
        if start_date and end_date:
            rows = _fetchall(
                db,
                "SELECT * FROM student_date_overrides WHERE client_id = %s AND override_date >= %s AND override_date <= %s ORDER BY override_date",
                (client_id, start_date, end_date),
            )
        else:
            rows = _fetchall(
                db, "SELECT * FROM student_date_overrides WHERE client_id = %s ORDER BY override_date",
                (client_id,),
            )
        out = []
        for r in rows:
            d = dict(r)
            # normalize override_date to ISO string
            od = d.get("override_date")
            if hasattr(od, "isoformat"):
                d["override_date"] = od.isoformat()[:10]
            elif isinstance(od, str):
                d["override_date"] = od[:10]
            out.append(d)
        return out


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
                  (bool(completed), now_str if completed else None, existing))
        else:
            _exec(db,
                  "INSERT INTO student_assignment_progress (client_id, plan_date, session_index, completed, completed_at) "
                  "VALUES (%s, %s, %s, %s, %s)",
                  (client_id, plan_date, session_index, bool(completed),
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


def get_due_flashcards(deck_id: int) -> list[dict]:
    """Get flashcards due for review (SRS). Returns due cards first, then new cards."""
    with get_db() as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return _fetchall(
            db,
            "SELECT * FROM student_flashcards WHERE deck_id = %s "
            "AND (next_review IS NULL OR next_review <= %s) "
            "ORDER BY CASE WHEN next_review IS NULL THEN 0 ELSE 1 END, next_review ASC, id",
            (deck_id, now),
        )


def count_due_flashcards(deck_id: int) -> int:
    """Count how many cards are due for review in a deck."""
    with get_db() as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return _fetchval(
            db,
            "SELECT COUNT(*) FROM student_flashcards WHERE deck_id = %s "
            "AND (next_review IS NULL OR next_review <= %s)",
            (deck_id, now),
        ) or 0


def update_flashcard_progress(card_id: int, correct: bool, quality: int = None):
    """Update flashcard with SM-2 spaced repetition algorithm.
    quality: 0-5 (0=complete blackout, 5=perfect recall)
    If quality is None, use old binary mode (correct=True→4, False→1).
    """
    if quality is None:
        quality = 4 if correct else 1

    with get_db() as db:
        # Fetch current SRS state
        row = _fetchone(db, "SELECT easiness_factor, interval_days, repetitions FROM student_flashcards WHERE id = %s", (card_id,))
        ef = float(row.get("easiness_factor") or 2.5) if row else 2.5
        interval = int(row.get("interval_days") or 0) if row else 0
        reps = int(row.get("repetitions") or 0) if row else 0

        # SM-2 algorithm
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        if ef < 1.3:
            ef = 1.3

        if quality < 3:
            # Reset on failure
            reps = 0
            interval = 0
        else:
            reps += 1
            if reps == 1:
                interval = 1
            elif reps == 2:
                interval = 6
            else:
                interval = int(interval * ef)

        from datetime import timedelta
        next_review = (datetime.now() + timedelta(days=max(interval, 0))).strftime("%Y-%m-%d %H:%M:%S")

        if correct:
            _exec(db,
                  "UPDATE student_flashcards SET times_seen = times_seen + 1, "
                  "times_correct = times_correct + 1, "
                  "easiness_factor = %s, interval_days = %s, repetitions = %s, next_review = %s "
                  "WHERE id = %s",
                  (round(ef, 2), interval, reps, next_review, card_id))
        else:
            _exec(db,
                  "UPDATE student_flashcards SET times_seen = times_seen + 1, "
                  "easiness_factor = %s, interval_days = %s, repetitions = %s, next_review = %s "
                  "WHERE id = %s",
                  (round(ef, 2), interval, reps, next_review, card_id))


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
                  "(quiz_id, question, option_a, option_b, option_c, option_d, correct, explanation, topic, sort_order) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                  (quiz_id, q["question"], q.get("option_a", ""), q.get("option_b", ""),
                   q.get("option_c", ""), q.get("option_d", ""), q["correct"],
                   q.get("explanation", ""), q.get("topic", ""), idx))
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
    # Apply timed XP multiplier (2x potion, etc.) only on positive awards.
    if xp and xp > 0:
        try:
            mult = get_active_boost(client_id, "xp")
            if mult and mult > 1.0:
                xp = int(round(xp * mult))
        except Exception:
            pass
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s) RETURNING id",
            (client_id, action, xp, detail),
            "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (?, ?, ?, ?)",
        )


def award_xp_with_rank_change(client_id: int, action: str, xp: int, detail: str = "") -> dict:
    """Award XP and return rank-change info for promotion notifications.

    Returns dict with keys:
        xp_awarded        — int
        total_xp          — new total
        promoted          — bool (rank index increased)
        rank_before       — study-rank dict before award
        rank_after        — study-rank dict after award
        tier_up           — bool (tier name changed, e.g. Apprentices -> Scholars)
        reached_elite     — bool (entered first elite rank)
    """
    before_total = get_total_xp(client_id)
    rank_before = get_study_rank(before_total)
    award_xp(client_id, action, xp, detail)
    after_total = before_total + xp
    rank_after = get_study_rank(after_total)
    promoted = rank_after["index"] > rank_before["index"]
    tier_up = promoted and rank_after["tier"] != rank_before["tier"]
    # Entered elite: before was in a divisioned rank, after has no division
    reached_elite = promoted and rank_after["division"] == "" and rank_before["division"] != ""
    return {
        "xp_awarded": xp,
        "total_xp": after_total,
        "promoted": promoted,
        "rank_before": rank_before,
        "rank_after": rank_after,
        "tier_up": tier_up,
        "reached_elite": reached_elite,
    }


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
    """Count consecutive days with XP activity ending today.

    A user gets ONE auto-applied streak freeze per ISO week: a single missed
    day inside that week does not break the streak. The freeze is recorded
    in `student_streak_freezes` the first time the gap is observed.
    """
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
    # Build set of activity dates
    activity = set()
    for r in rows:
        d = r["d"]
        if isinstance(d, str):
            d = _date.fromisoformat(d)
        activity.add(d)
    # Earliest activity date — we never apply freezes for days before this
    # (otherwise a brand-new account could rack up an infinite streak by
    # auto-consuming freezes on days that never had any activity).
    earliest_activity = min(activity) if activity else today
    # Pull existing freezes (last 120 days)
    existing_freezes = _get_recent_freeze_dates(client_id, today - timedelta(days=120))
    # Track freezes already used per ISO week
    weeks_used = {(d.isocalendar()[0], d.isocalendar()[1]) for d in existing_freezes}
    new_freezes: list = []  # tuples (date, year, week)
    streak = 0
    cur = today
    # Allow today to be missing without breaking — streak shows yesterday's count
    if cur not in activity and cur not in existing_freezes:
        cur = cur - timedelta(days=1)
    while True:
        # Hard floor: never count any day older than the user's earliest
        # activity \u2014 even if a freeze row exists for it (bogus rows from
        # prior buggy runs are ignored here).
        if cur < earliest_activity:
            break
        if cur in activity or cur in existing_freezes:
            streak += 1
            cur = cur - timedelta(days=1)
            continue
        # Try to consume the one auto-freeze allotted per ISO week
        iso = cur.isocalendar()
        wk = (iso[0], iso[1])
        if wk not in weeks_used:
            weeks_used.add(wk)
            new_freezes.append((cur, iso[0], iso[1]))
            streak += 1
            cur = cur - timedelta(days=1)
            continue
        # Try to auto-consume a wallet streak freeze (Duolingo-style).
        # Only consumes if the user has bought freezes in the shop.
        try:
            spent = _consume_wallet_freeze_for_date(client_id, cur)
        except Exception:
            spent = False
        if spent:
            existing_freezes.add(cur)
            new_freezes.append((cur, iso[0], iso[1]))
            weeks_used.add(wk)
            streak += 1
            cur = cur - timedelta(days=1)
            continue
        break
    # Persist any newly-applied freezes
    if new_freezes:
        _record_freezes(client_id, new_freezes)
    return streak


def _get_recent_freeze_dates(client_id: int, since) -> set:
    from datetime import date as _date
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT freeze_date FROM student_streak_freezes "
            "WHERE client_id = %s AND freeze_date >= %s",
            (client_id, since.isoformat() if not _USE_PG else since),
        ) or []
    out = set()
    for r in rows:
        d = r["freeze_date"]
        if isinstance(d, str):
            d = _date.fromisoformat(d[:10])
        out.add(d)
    return out


def _record_freezes(client_id: int, freezes: list) -> None:
    with get_db() as db:
        for d, y, w in freezes:
            try:
                _exec(
                    db,
                    "INSERT INTO student_streak_freezes "
                    "(client_id, freeze_date, iso_year, iso_week) "
                    "VALUES (%s, %s, %s, %s)",
                    (client_id, d.isoformat() if not _USE_PG else d, y, w),
                )
            except Exception:
                pass  # UNIQUE constraint — week already used


def _consume_wallet_freeze_for_date(client_id: int, target_date) -> bool:
    """Atomically decrement a wallet streak freeze if the user has any.
    Returns True if a freeze was consumed (i.e. the gap day should count).
    Used by get_streak_days for Duolingo-style auto-consumption."""
    with get_db() as db:
        _ensure_wallet(db, client_id)
        owned = _fetchval(
            db,
            "SELECT streak_freezes FROM student_wallet WHERE client_id = %s",
            (client_id,),
        ) or 0
        if int(owned) <= 0:
            return False
        _exec(
            db,
            "UPDATE student_wallet SET streak_freezes = streak_freezes - 1 "
            "WHERE client_id = %s AND streak_freezes > 0",
            (client_id,),
        )
    return True


def get_freeze_status(client_id: int) -> dict:
    """Return whether this ISO week's freeze has been used."""
    from datetime import date as _date
    iso = _date.today().isocalendar()
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT freeze_date FROM student_streak_freezes "
            "WHERE client_id = %s AND iso_year = %s AND iso_week = %s",
            (client_id, iso[0], iso[1]),
        )
    if row:
        d = row["freeze_date"]
        if isinstance(d, str):
            d = d[:10]
        else:
            d = d.isoformat()
        return {"available": False, "used_on": d}
    return {"available": True, "used_on": None}


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
    "xp_100":          {"emoji": "⭐", "name": "Rising Star",     "desc": "Earned 100 XP"},
    "xp_500":          {"emoji": "🌟", "name": "Shining Star",    "desc": "Earned 500 XP"},
    "xp_1000":         {"emoji": "💫", "name": "Superstar",       "desc": "Earned 1000 XP"},
    "focus_1h":        {"emoji": "⏱️", "name": "Focused",         "desc": "1 hour of total focus time"},
    "focus_10h":       {"emoji": "🧘", "name": "Deep Focus",      "desc": "10 hours of total focus time"},
    "focus_50h":       {"emoji": "🧠", "name": "Focus Master",    "desc": "50 hours of total focus time"},
    "page_100":        {"emoji": "📖", "name": "Page Turner",     "desc": "Read 100 pages"},
    "quiz_10":         {"emoji": "🎯", "name": "Quiz Pro",        "desc": "Completed 10 quizzes"},
    # New badges
    "flashcard_500":   {"emoji": "🗂️", "name": "Card Shark",     "desc": "Reviewed 500 flashcards"},
    "flashcard_1000":  {"emoji": "🎰", "name": "Flashcard Legend", "desc": "Reviewed 1,000 flashcards"},
    "note_taker_25":   {"emoji": "📚", "name": "Prolific Writer", "desc": "Created 25 notes"},
    "note_taker_50":   {"emoji": "✍️", "name": "Note Machine",   "desc": "Created 50 notes"},
    "quiz_25":         {"emoji": "🧪", "name": "Quiz Veteran",    "desc": "Completed 25 quizzes"},
    "quiz_50":         {"emoji": "🏅", "name": "Quiz Legend",     "desc": "Completed 50 quizzes"},
    "xp_2500":         {"emoji": "🚀", "name": "Rocket Student",  "desc": "Earned 2,500 XP"},
    "xp_5000":         {"emoji": "👑", "name": "XP King",         "desc": "Earned 5,000 XP"},
    "streak_14":       {"emoji": "🔱", "name": "Two-Week Warrior", "desc": "14-day study streak"},
    "streak_60":       {"emoji": "🏛️", "name": "Iron Will",      "desc": "60-day study streak"},
    "streak_100":      {"emoji": "💯", "name": "The 100 Club",    "desc": "100-day study streak"},
    "sharer":          {"emoji": "🤝", "name": "Sharer",          "desc": "Published a note to Study Exchange"},
    "popular_note":    {"emoji": "❤️", "name": "Popular Note",    "desc": "One of your shared notes got 5 likes"},
    "viral_note":      {"emoji": "🔥", "name": "Viral Note",      "desc": "One of your shared notes got 25 likes"},
    "helper_5":        {"emoji": "🙌", "name": "Helpful",         "desc": "5 students used your shared notes"},
    "helper_25":       {"emoji": "🌍", "name": "Community Hero",  "desc": "25 students used your shared notes"},
    "helper_100":      {"emoji": "🏆", "name": "Knowledge Legend", "desc": "100 students used your shared notes"},
    "first_course":    {"emoji": "📕", "name": "First Course",    "desc": "Added your first course"},
    "five_courses":    {"emoji": "📚", "name": "Course Collector", "desc": "Added 5 courses"},
    "focus_100h":      {"emoji": "⏳", "name": "Time Lord",       "desc": "100 hours of total focus time"},
    "page_500":        {"emoji": "📗", "name": "Bookworm",        "desc": "Read 500 pages"},
    "page_1000":       {"emoji": "📘", "name": "Library Regular", "desc": "Read 1,000 pages"},
    "perfect_week":    {"emoji": "🌟", "name": "Perfect Week",    "desc": "Earned XP every day for a week"},
    "early_bird":      {"emoji": "🌅", "name": "Early Bird",      "desc": "Studied before 7 AM"},
    "night_owl":       {"emoji": "🦉", "name": "Night Owl",       "desc": "Studied after 11 PM"},
    # ── Quiz / accuracy mastery ─────────────────────────────────────
    "quiz_100":        {"emoji": "🎓", "name": "Quiz Centurion",  "desc": "Completed 100 quizzes"},
    "quiz_500":        {"emoji": "🧬", "name": "Quiz Scientist",  "desc": "Completed 500 quizzes"},
    "quiz_perfect_5":  {"emoji": "🎯", "name": "Sharpshooter",    "desc": "5 perfect-score quizzes"},
    "quiz_perfect_25": {"emoji": "🏹", "name": "Bullseye",        "desc": "25 perfect-score quizzes"},
    "quiz_speed":      {"emoji": "⚡", "name": "Speedrunner",     "desc": "Aced a quiz in under 60 seconds"},
    "quiz_no_skip":    {"emoji": "✅", "name": "No Shortcuts",    "desc": "10 quizzes with no skipped questions"},
    "comeback_kid":    {"emoji": "💪", "name": "Comeback Kid",    "desc": "Failed a quiz, then aced the retake"},
    # ── Flashcard mastery ──────────────────────────────────────────
    "flashcard_5000":  {"emoji": "🃏", "name": "Card Master",     "desc": "Reviewed 5,000 flashcards"},
    "flashcard_streak":{"emoji": "🔁", "name": "Spaced Repetition","desc": "Reviewed flashcards 7 days in a row"},
    "deck_builder":    {"emoji": "🏗️", "name": "Deck Architect",  "desc": "Created 10 flashcard decks"},
    # ── Notes / writing ─────────────────────────────────────────────
    "note_taker_100":  {"emoji": "📓", "name": "Walking Library", "desc": "Created 100 notes"},
    "note_taker_250":  {"emoji": "📔", "name": "Encyclopedia",    "desc": "Created 250 notes"},
    "long_note":       {"emoji": "📜", "name": "Treatise",        "desc": "Wrote a 5,000-character note"},
    # ── Focus mastery ───────────────────────────────────────────────
    "focus_session_4h":{"emoji": "🪨", "name": "Marathon Mind",   "desc": "Completed a single 4-hour focus session"},
    "focus_500h":      {"emoji": "🌌", "name": "Eternal Focus",   "desc": "500 hours of total focus time"},
    "focus_1000h":     {"emoji": "♾️", "name": "Infinity Mind",   "desc": "1,000 hours of total focus time"},
    "no_phone_30":     {"emoji": "📵", "name": "Phone Down",      "desc": "30 sessions with no phone interruption"},
    "deep_work_week":  {"emoji": "🌊", "name": "Deep Work Week",  "desc": "20 hours of focus in a single week"},
    # ── Streak elite ────────────────────────────────────────────────
    "streak_180":      {"emoji": "🛡️", "name": "Half-Year Hero",  "desc": "180-day study streak"},
    "streak_365":      {"emoji": "🏆", "name": "Year of Discipline","desc": "365-day study streak"},
    "freeze_used":     {"emoji": "🧊", "name": "Saved by Ice",    "desc": "Used a streak freeze for the first time"},
    # ── XP elite ────────────────────────────────────────────────────
    "xp_10000":        {"emoji": "🌠", "name": "Cosmic Mind",     "desc": "Earned 10,000 XP"},
    "xp_25000":        {"emoji": "🌟", "name": "Galactic Mind",   "desc": "Earned 25,000 XP"},
    "xp_50000":        {"emoji": "🪐", "name": "Stellar Mind",    "desc": "Earned 50,000 XP"},
    "xp_100000":       {"emoji": "☄️", "name": "Supernova",       "desc": "Earned 100,000 XP"},
    # ── Time-of-day & calendar ──────────────────────────────────────
    "weekend_warrior": {"emoji": "🗡️", "name": "Weekend Warrior", "desc": "Studied both Saturday and Sunday"},
    "midnight_oil":    {"emoji": "🕯️", "name": "Midnight Oil",   "desc": "Studied past 2 AM"},
    "sunrise_session": {"emoji": "☀️", "name": "Sunrise Session", "desc": "Studied at exactly 6 AM"},
    "holiday_grinder": {"emoji": "🎄", "name": "Holiday Grinder", "desc": "Studied on a public holiday"},
    "birthday_study":  {"emoji": "🎂", "name": "Cake Later",      "desc": "Studied on your birthday"},
    "leap_day":        {"emoji": "🐸", "name": "Leap Year Scholar","desc": "Studied on Feb 29"},
    "new_years":       {"emoji": "🎆", "name": "New Year, New Me","desc": "Studied on January 1st"},
    # ── Duels & social combat ───────────────────────────────────────
    "duel_first":      {"emoji": "⚔️", "name": "First Blood",     "desc": "Won your first quiz duel"},
    "duel_streak_3":   {"emoji": "🔪", "name": "Win Streak",      "desc": "Won 3 quiz duels in a row"},
    "duel_streak_10":  {"emoji": "🗡️", "name": "Duelist",         "desc": "Won 10 quiz duels in a row"},
    "duel_25":         {"emoji": "🛡️", "name": "Veteran Duelist", "desc": "Won 25 quiz duels"},
    "duel_100":        {"emoji": "👑", "name": "Duel Champion",   "desc": "Won 100 quiz duels"},
    "duel_perfect":    {"emoji": "💥", "name": "Flawless Victory","desc": "Won a duel with a perfect score"},
    "duel_underdog":   {"emoji": "🐺", "name": "Underdog",        "desc": "Beat an opponent with 2x your XP"},
    # ── Exchange / community ────────────────────────────────────────
    "helper_500":      {"emoji": "🌐", "name": "Worldwide Mentor","desc": "500 students used your shared notes"},
    "viral_note_100":  {"emoji": "🚀", "name": "Trending",        "desc": "A shared note got 100 likes"},
    "exchange_5":      {"emoji": "📤", "name": "Generous",        "desc": "Shared 5 notes"},
    "exchange_25":     {"emoji": "🏛️", "name": "Library Founder", "desc": "Shared 25 notes"},
    "downloader_10":   {"emoji": "📥", "name": "Curious Mind",    "desc": "Used 10 shared notes from others"},
    # ── Course / academic milestones ────────────────────────────────
    "ten_courses":     {"emoji": "🎒", "name": "Course Hoarder",  "desc": "Added 10 courses"},
    "exam_passer":     {"emoji": "📋", "name": "Exam Ready",      "desc": "Passed 5 practice exams"},
    "exam_master":     {"emoji": "🎓", "name": "Exam Master",     "desc": "Passed 25 practice exams"},
    "syllabus_synced": {"emoji": "🔄", "name": "Synced Up",       "desc": "Synced Canvas for the first time"},
    # ── Wallet / economy ────────────────────────────────────────────
    "first_purchase":  {"emoji": "🛍️", "name": "First Splurge",   "desc": "Made your first shop purchase"},
    "big_spender":     {"emoji": "💰", "name": "Big Spender",     "desc": "Spent 1,000 coins in the shop"},
    "banner_collector":{"emoji": "🎨", "name": "Banner Collector","desc": "Unlocked 5 banners"},
    "flag_collector":  {"emoji": "🚩", "name": "Flag Collector",  "desc": "Unlocked 5 leaderboard flags"},
    "completionist":   {"emoji": "🏵️", "name": "Completionist",   "desc": "Unlocked every banner in the shop"},
    # ── Page & reading ──────────────────────────────────────────────
    "page_2500":       {"emoji": "📙", "name": "Speed Reader",    "desc": "Read 2,500 pages"},
    "page_5000":       {"emoji": "📕", "name": "Library Lord",    "desc": "Read 5,000 pages"},
    # ── Profile / cosmetics ─────────────────────────────────────────
    "first_banner":    {"emoji": "🖼️", "name": "Decorator",       "desc": "Equipped a custom profile banner"},
    "first_flag":      {"emoji": "🎌", "name": "Flying Colors",   "desc": "Equipped a leaderboard flag"},
    "identity":        {"emoji": "🪪", "name": "True Self",       "desc": "Equipped a leaderboard badge"},
    # ── Secret / quirky ─────────────────────────────────────────────
    "lucky_seven":     {"emoji": "🍀", "name": "Lucky Seven",     "desc": "Scored exactly 77% on a quiz"},
    "palindrome":      {"emoji": "🔄", "name": "Palindrome",      "desc": "Earned exactly 121 XP in a day"},
    "ghost":           {"emoji": "👻", "name": "Halloween Ghost", "desc": "Studied on October 31st"},
    "valentine":       {"emoji": "💌", "name": "Heartstudy",      "desc": "Studied on February 14th"},
    "pi_day":          {"emoji": "🥧", "name": "Pi Day",          "desc": "Studied on March 14th"},
    "april_fools":     {"emoji": "🃏", "name": "April Fool",      "desc": "Studied on April 1st"},
    "back_to_school":  {"emoji": "🍎", "name": "Back to School",  "desc": "Studied on September 1st"},
    "speed_demon":     {"emoji": "🏎️", "name": "Speed Demon",    "desc": "Earned 500 XP in a single day"},
    "marathoner":      {"emoji": "🏃", "name": "XP Marathoner",   "desc": "Earned 1,000 XP in a single day"},
    "comeback":        {"emoji": "🔥", "name": "Comeback Story",  "desc": "Returned after a 30-day break"},
    "loyal":           {"emoji": "💝", "name": "Loyal",           "desc": "Account older than 1 year"},
    "od_loyal":        {"emoji": "🗿", "name": "Old Guard",       "desc": "Account older than 2 years"},
    # ── PLUS-only badges ────────────────────────────────────────────
    "plus_member":     {"emoji": "💎", "name": "Plus Member",     "desc": "Active Plus subscription", "plus_only": True},
    "plus_supporter":  {"emoji": "🤍", "name": "Supporter",       "desc": "Subscribed to support development", "plus_only": True},
    "plus_founder":    {"emoji": "🏛️", "name": "Founder",         "desc": "Joined Plus during beta", "plus_only": True},
    "plus_vip":        {"emoji": "⭐", "name": "VIP",             "desc": "Exclusive Plus VIP badge", "plus_only": True},
    "plus_gold":       {"emoji": "🥇", "name": "Gold Tier",       "desc": "Exclusive Plus golden badge", "plus_only": True},
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


# ── Study Rank System ───────────────────────────────────────
# Tiered ladder inspired by competitive rank systems.
# Each main tier has 4 sub-divisions (IV=lowest, I=highest).
# Elite ranks at the top have no divisions and are rare globally.
STUDY_RANKS = [
    # (xp_floor, "Full rank name", "Short tier", "division or ''", "emoji/color")
    (0,      "Initiates IV",       "Initiates",    "IV", "#94A3B8"),
    (50,     "Initiates III",      "Initiates",    "III","#94A3B8"),
    (120,    "Initiates II",       "Initiates",    "II", "#94A3B8"),
    (200,    "Initiates I",        "Initiates",    "I",  "#94A3B8"),
    (300,    "Apprentices IV",     "Apprentices",  "IV", "#A3A380"),
    (450,    "Apprentices III",    "Apprentices",  "III","#A3A380"),
    (650,    "Apprentices II",     "Apprentices",  "II", "#A3A380"),
    (900,    "Apprentices I",      "Apprentices",  "I",  "#A3A380"),
    (1200,   "Scholars IV",        "Scholars",     "IV", "#10B981"),
    (1600,   "Scholars III",       "Scholars",     "III","#10B981"),
    (2100,   "Scholars II",        "Scholars",     "II", "#10B981"),
    (2700,   "Scholars I",         "Scholars",     "I",  "#10B981"),
    (3400,   "Researchers IV",     "Researchers",  "IV", "#3B82F6"),
    (4200,   "Researchers III",    "Researchers",  "III","#3B82F6"),
    (5100,   "Researchers II",     "Researchers",  "II", "#3B82F6"),
    (6100,   "Researchers I",      "Researchers",  "I",  "#3B82F6"),
    (7200,   "Academics IV",       "Academics",    "IV", "#8B5CF6"),
    (8500,   "Academics III",      "Academics",    "III","#8B5CF6"),
    (10000,  "Academics II",       "Academics",    "II", "#8B5CF6"),
    (11800,  "Academics I",        "Academics",    "I",  "#8B5CF6"),
    (13800,  "Masterminds IV",     "Masterminds",  "IV", "#EC4899"),
    (16200,  "Masterminds III",    "Masterminds",  "III","#EC4899"),
    (18800,  "Masterminds II",     "Masterminds",  "II", "#EC4899"),
    (21800,  "Masterminds I",      "Masterminds",  "I",  "#EC4899"),
    (25200,  "Grand Scholars IV",  "Grand Scholars","IV","#F59E0B"),
    (29000,  "Grand Scholars III", "Grand Scholars","III","#F59E0B"),
    (33200,  "Grand Scholars II",  "Grand Scholars","II", "#F59E0B"),
    (37800,  "Grand Scholars I",   "Grand Scholars","I",  "#F59E0B"),
    (43000,  "Legends IV",         "Legends",      "IV", "#EF4444"),
    (49000,  "Legends III",        "Legends",      "III","#EF4444"),
    (55800,  "Legends II",         "Legends",      "II", "#EF4444"),
    (63500,  "Legends I",          "Legends",      "I",  "#EF4444"),
    # Elite ranks — no divisions, extremely rare
    (72000,  "Arch Scholars",      "Arch Scholars","",   "#FBBF24"),
    (90000,  "High Sages",         "High Sages",   "",   "#E879F9"),
    (120000, "Oracles of Knowledge", "Oracles",    "",   "#22D3EE"),
]


def get_study_rank(xp: int) -> dict:
    """Return the study-rank dict for the given XP.

    Dict keys:
        full_name   — e.g. "Scholars II"
        tier        — e.g. "Scholars"  (used for promotion-tier detection)
        division    — e.g. "II"  ('' for elite ranks)
        color       — hex color for UI badge
        index       — 0-based index into STUDY_RANKS
        xp_floor    — XP at which this rank starts
        xp_ceil     — XP at which the next rank starts (None for max rank)
        progress_pct — 0-100 progress within the current rank
    """
    idx = 0
    for i in range(len(STUDY_RANKS) - 1, -1, -1):
        if xp >= STUDY_RANKS[i][0]:
            idx = i
            break
    floor, full, tier, div, color = STUDY_RANKS[idx]
    ceil_xp = STUDY_RANKS[idx + 1][0] if idx + 1 < len(STUDY_RANKS) else None
    if ceil_xp is not None and ceil_xp > floor:
        progress = int(((xp - floor) / (ceil_xp - floor)) * 100)
    else:
        progress = 100
    return {
        "full_name": full,
        "tier": tier,
        "division": div,
        "color": color,
        "index": idx,
        "xp_floor": floor,
        "xp_ceil": ceil_xp,
        "progress_pct": max(0, min(100, progress)),
    }


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
    seen = set()
    for r in rows:
        info = BADGE_DEFS.get(r["badge_key"], {})
        result.append({**r, **info})
        seen.add(r["badge_key"])
    # Ultimate tier: surface every defined badge so they can equip any of them.
    try:
        from . import subscription as _sub
        if _sub.get_tier(client_id) == "ultimate":
            for key, info in BADGE_DEFS.items():
                if key in seen:
                    continue
                result.append({"badge_key": key, "client_id": client_id, **info})
    except Exception:
        pass
    return result


# ── Email prefs ─────────────────────────────────────────────

def get_email_prefs(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db, "SELECT * FROM student_email_prefs WHERE client_id = %s", (client_id,),
        )


def upsert_email_prefs(client_id: int, daily_email: bool = True, email_hour: int = 7,
                        timezone: str = "America/Mexico_City",
                        university: str = "", field_of_study: str = "",
                        lang: str = "en"):
    with get_db() as db:
        existing = _fetchone(db, "SELECT id FROM student_email_prefs WHERE client_id = %s",
                             (client_id,))
        de = bool(daily_email)
        if existing:
            _exec(db,
                  "UPDATE student_email_prefs SET daily_email = %s, email_hour = %s, timezone = %s, "
                  "university = %s, field_of_study = %s, lang = %s "
                  "WHERE client_id = %s",
                  (de, email_hour, timezone, university, field_of_study, lang, client_id))
        else:
            _exec(db,
                  "INSERT INTO student_email_prefs (client_id, daily_email, email_hour, timezone, university, field_of_study, lang) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (client_id, de, email_hour, timezone, university, field_of_study, lang))


def set_gpa_country(client_id: int, country: str):
    with get_db() as db:
        existing = _fetchone(db, "SELECT id FROM student_email_prefs WHERE client_id = %s",
                             (client_id,))
        if existing:
            _exec(db,
                  "UPDATE student_email_prefs SET gpa_country = %s WHERE client_id = %s",
                  (country, client_id))
        else:
            _exec(db,
                  "INSERT INTO student_email_prefs (client_id, gpa_country) VALUES (%s, %s)",
                  (client_id, country))


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


# ── Leaderboard ─────────────────────────────────────────────

def get_leaderboard(limit: int = 50, university: str = "") -> list[dict]:
    """Get top students by XP. Optionally filter by university."""
    with get_db() as db:
        if university:
            return _fetchall(
                db,
                "SELECT c.id as client_id, c.name, "
                "COALESCE(ep.university, '') as university, "
                "COALESCE(ep.field_of_study, '') as field_of_study, "
                "COALESCE(SUM(x.xp), 0) as total_xp "
                "FROM clients c "
                "LEFT JOIN student_email_prefs ep ON ep.client_id = c.id "
                "LEFT JOIN student_xp x ON x.client_id = c.id "
                "WHERE c.account_type = 'student' AND LOWER(COALESCE(ep.university, '')) = LOWER(%s) "
                "GROUP BY c.id, c.name, ep.university, ep.field_of_study "
                "HAVING COALESCE(SUM(x.xp), 0) > 0 "
                "ORDER BY total_xp DESC LIMIT %s",
                (university, limit),
            )
        return _fetchall(
            db,
            "SELECT c.id as client_id, c.name, "
            "COALESCE(ep.university, '') as university, "
            "COALESCE(ep.field_of_study, '') as field_of_study, "
            "COALESCE(SUM(x.xp), 0) as total_xp "
            "FROM clients c "
            "LEFT JOIN student_email_prefs ep ON ep.client_id = c.id "
            "LEFT JOIN student_xp x ON x.client_id = c.id "
            "WHERE c.account_type = 'student' "
            "GROUP BY c.id, c.name, ep.university, ep.field_of_study "
            "HAVING COALESCE(SUM(x.xp), 0) > 0 "
            "ORDER BY total_xp DESC LIMIT %s",
            (limit,),
        )


def get_student_rank(client_id: int) -> int:
    """Get the rank of a specific student."""
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT client_id, SUM(xp) as total_xp FROM student_xp "
            "GROUP BY client_id ORDER BY total_xp DESC",
            (),
        )
    for i, r in enumerate(rows, 1):
        if r["client_id"] == client_id:
            return i
    return 0


# ── Study Exchange ──────────────────────────────────────────

def publish_note(note_id: int, client_id: int, author_name: str, university: str):
    """Make a note public for the Study Exchange."""
    with get_db() as db:
        _exec(db,
              "UPDATE student_notes SET is_public = TRUE, author_name = %s, university = %s "
              "WHERE id = %s AND client_id = %s",
              (author_name, university, note_id, client_id))


def unpublish_note(note_id: int, client_id: int):
    """Remove a note from the Study Exchange."""
    with get_db() as db:
        _exec(db,
              "UPDATE student_notes SET is_public = FALSE WHERE id = %s AND client_id = %s",
              (note_id, client_id))


def browse_public_notes(search: str = "", subject: str = "", university: str = "",
                        limit: int = 50, offset: int = 0) -> list[dict]:
    """Browse public notes in the Study Exchange."""
    with get_db() as db:
        conditions = ["n.is_public = TRUE"]
        params = []
        if search:
            conditions.append("LOWER(n.title) LIKE %s")
            params.append(f"%{search.lower()}%")
        if subject:
            conditions.append("LOWER(COALESCE(c.name, '')) LIKE %s")
            params.append(f"%{subject.lower()}%")
        if university:
            conditions.append("LOWER(COALESCE(n.university, '')) LIKE %s")
            params.append(f"%{university.lower()}%")
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        return _fetchall(
            db,
            f"SELECT n.id, n.title, n.source_type, n.created_at, n.likes, "
            f"n.author_name, n.university, COALESCE(c.name, '') as course_name, "
            f"LENGTH(n.content_html) as content_length "
            f"FROM student_notes n "
            f"LEFT JOIN student_courses c ON n.course_id = c.id "
            f"WHERE {where} "
            f"ORDER BY n.likes DESC, n.created_at DESC LIMIT %s OFFSET %s",
            tuple(params),
        )


def get_public_note(note_id: int) -> dict | None:
    """Get a public note for viewing (anyone can read)."""
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT n.*, COALESCE(c.name, '') as course_name "
            "FROM student_notes n "
            "LEFT JOIN student_courses c ON n.course_id = c.id "
            "WHERE n.id = %s AND n.is_public = TRUE",
            (note_id,),
        )


def toggle_note_like(client_id: int, note_id: int) -> bool:
    """Like/unlike a note. Returns True if liked, False if unliked."""
    with get_db() as db:
        existing = _fetchval(
            db, "SELECT id FROM student_note_likes WHERE client_id = %s AND note_id = %s",
            (client_id, note_id),
        )
        if existing:
            _exec(db, "DELETE FROM student_note_likes WHERE client_id = %s AND note_id = %s",
                  (client_id, note_id))
            _exec(db, "UPDATE student_notes SET likes = CASE WHEN likes > 0 THEN likes - 1 ELSE 0 END WHERE id = %s", (note_id,))
            return False
        else:
            _exec(db,
                  "INSERT INTO student_note_likes (client_id, note_id) VALUES (%s, %s)",
                  (client_id, note_id))
            _exec(db, "UPDATE student_notes SET likes = likes + 1 WHERE id = %s", (note_id,))
            return True


def has_liked_note(client_id: int, note_id: int) -> bool:
    """Check if a user has liked a note."""
    with get_db() as db:
        return bool(_fetchval(
            db, "SELECT id FROM student_note_likes WHERE client_id = %s AND note_id = %s",
            (client_id, note_id),
        ))


def fork_note(client_id: int, note_id: int) -> int | None:
    """Copy a public note to a user's private notes."""
    with get_db() as db:
        note = _fetchone(
            db, "SELECT * FROM student_notes WHERE id = %s AND is_public = TRUE",
            (note_id,),
        )
        if not note:
            return None
        return _insert_returning_id(
            db,
            "INSERT INTO student_notes (client_id, title, content_html, source_type) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (client_id, f"[Forked] {note['title']}", note["content_html"], "forked"),
            "INSERT INTO student_notes (client_id, title, content_html, source_type) "
            "VALUES (?, ?, ?, ?)",
        )


# ── Personal Leaderboards ──────────────────────────────────

def create_lb_group(owner_id: int, name: str) -> dict:
    """Create a personal leaderboard group with a random invite code."""
    import secrets
    code = secrets.token_urlsafe(8)
    with get_db() as db:
        gid = _insert_returning_id(
            db,
            "INSERT INTO student_lb_groups (name, owner_id, invite_code) VALUES (%s, %s, %s) RETURNING id",
            (name, owner_id, code),
            "INSERT INTO student_lb_groups (name, owner_id, invite_code) VALUES (?, ?, ?)",
        )
        # Owner auto-joins
        _exec(db, "INSERT INTO student_lb_members (group_id, client_id) VALUES (%s, %s)",
              (gid, owner_id))
    return {"id": gid, "invite_code": code}


def join_lb_group(client_id: int, invite_code: str) -> dict | None:
    """Join a personal leaderboard group via invite code. Returns group or None."""
    with get_db() as db:
        group = _fetchone(db, "SELECT * FROM student_lb_groups WHERE invite_code = %s", (invite_code,))
        if not group:
            return None
        try:
            _exec(db, "INSERT INTO student_lb_members (group_id, client_id) VALUES (%s, %s)",
                  (group["id"], client_id))
        except Exception:
            pass  # already a member
        return group


def leave_lb_group(client_id: int, group_id: int):
    """Leave a personal leaderboard group."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_lb_members WHERE group_id = %s AND client_id = %s",
              (group_id, client_id))


def delete_lb_group(group_id: int, owner_id: int):
    """Delete a leaderboard group (owner only)."""
    with get_db() as db:
        _exec(db, "DELETE FROM student_lb_groups WHERE id = %s AND owner_id = %s",
              (group_id, owner_id))


def get_my_lb_groups(client_id: int) -> list[dict]:
    """Get all leaderboard groups the user is a member of."""
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT g.*, (g.owner_id = %s) as is_owner, "
            "(SELECT COUNT(*) FROM student_lb_members WHERE group_id = g.id) as member_count "
            "FROM student_lb_groups g "
            "JOIN student_lb_members m ON m.group_id = g.id "
            "WHERE m.client_id = %s "
            "ORDER BY g.created_at DESC",
            (client_id, client_id),
        )


def get_lb_group_leaderboard(group_id: int) -> list[dict]:
    """Get leaderboard for a specific group.

    Personal leaderboards compare XP *gained since each member joined* — every
    member starts at 0 the moment they join so the group is a fair head-to-head
    comparison, independent of the global ranking.
    """
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT c.id as client_id, c.name, "
            "COALESCE(ep.university, '') as university, "
            "COALESCE(ep.field_of_study, '') as field_of_study, "
            "COALESCE(SUM(x.xp), 0) as total_xp, "
            "m.joined_at as joined_at "
            "FROM student_lb_members m "
            "JOIN clients c ON c.id = m.client_id "
            "LEFT JOIN student_email_prefs ep ON ep.client_id = c.id "
            "LEFT JOIN student_xp x ON x.client_id = c.id AND x.created_at >= m.joined_at "
            "WHERE m.group_id = %s "
            "GROUP BY c.id, c.name, ep.university, ep.field_of_study, m.joined_at "
            "ORDER BY total_xp DESC, c.name ASC",
            (group_id,),
        )


def get_lb_group(group_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM student_lb_groups WHERE id = %s", (group_id,))


def is_lb_member(client_id: int, group_id: int) -> bool:
    with get_db() as db:
        return bool(_fetchval(
            db, "SELECT id FROM student_lb_members WHERE group_id = %s AND client_id = %s",
            (group_id, client_id),
        ))


# ── Note fork tracking (XP for shared notes) ───────────────

def record_note_fork(note_id: int, forker_id: int, author_id: int) -> bool:
    """Record that a user forked/used a note. Returns True if new (first time)."""
    if forker_id == author_id:
        return False
    with get_db() as db:
        try:
            _exec(db,
                  "INSERT INTO student_note_forks (note_id, forker_id, author_id) VALUES (%s, %s, %s)",
                  (note_id, forker_id, author_id))
            return True
        except Exception:
            return False


def get_note_fork_count(author_id: int) -> int:
    """Count unique users who have forked any of this author's notes."""
    with get_db() as db:
        return _fetchval(
            db,
            "SELECT COUNT(DISTINCT forker_id) FROM student_note_forks WHERE author_id = %s",
            (author_id,),
        ) or 0

# -- Daily Quests --------------------------------------------

QUEST_POOL = [
    {"key": "focus_25",    "label": "Focus for 25 minutes",         "target": 25,  "xp": 15, "metric": "focus_minutes"},
    {"key": "focus_60",    "label": "Focus for 60 minutes",         "target": 60,  "xp": 25, "metric": "focus_minutes"},
    {"key": "flashcards_20","label": "Review 20 flashcards",        "target": 20,  "xp": 15, "metric": "flashcards_reviewed"},
    {"key": "quiz_1",      "label": "Complete 1 quiz",              "target": 1,   "xp": 20, "metric": "quizzes_completed"},
    {"key": "session_3",   "label": "Finish 3 study sessions",      "target": 3,   "xp": 20, "metric": "sessions_completed"},
    {"key": "pages_15",    "label": "Read 15 pages of material",    "target": 15,  "xp": 15, "metric": "pages_read"},
    {"key": "note_1",      "label": "Create 1 note",                "target": 1,   "xp": 10, "metric": "notes_created"},
    {"key": "exam_review_15","label": "15 min reviewing for an exam","target": 15, "xp": 15, "metric": "focus_minutes"},
]

QUEST_BUNDLE_BONUS_XP = 30  # awarded when all 3 daily quests complete


def get_or_create_daily_quests(client_id: int) -> list[dict]:
    """Return today's 3 quests, generating them on first call of the day."""
    import random
    from datetime import date as _date
    today = _date.today()
    today_s = today.isoformat()
    with get_db() as db:
        if _USE_PG:
            rows = _fetchall(
                db,
                "SELECT * FROM student_daily_quests WHERE client_id = %s AND quest_date = %s ORDER BY id",
                (client_id, today),
            )
        else:
            rows = _fetchall(
                db,
                "SELECT * FROM student_daily_quests WHERE client_id = %s AND quest_date = %s ORDER BY id",
                (client_id, today_s),
            )
    if rows:
        return [dict(r) for r in rows]
    # Pick 3 distinct quests deterministically per (client, day)
    rng = random.Random(f"{client_id}-{today_s}")
    picks = rng.sample(QUEST_POOL, 3)
    out = []
    with get_db() as db:
        for q in picks:
            try:
                _exec(
                    db,
                    "INSERT INTO student_daily_quests "
                    "(client_id, quest_date, quest_key, target, progress, xp_reward) "
                    "VALUES (%s, %s, %s, %s, 0, %s)",
                    (client_id, today if _USE_PG else today_s, q["key"], q["target"], q["xp"]),
                )
            except Exception:
                pass
        if _USE_PG:
            out = _fetchall(
                db,
                "SELECT * FROM student_daily_quests WHERE client_id = %s AND quest_date = %s ORDER BY id",
                (client_id, today),
            )
        else:
            out = _fetchall(
                db,
                "SELECT * FROM student_daily_quests WHERE client_id = %s AND quest_date = %s ORDER BY id",
                (client_id, today_s),
            )
    return [dict(r) for r in out]


def progress_quests_by_metric(client_id: int, metric: str, amount: int = 1) -> list[dict]:
    """Increment today's quests whose metric matches. Returns list of newly-completed quests."""
    if amount <= 0:
        return []
    quests = get_or_create_daily_quests(client_id)
    newly_completed: list[dict] = []
    matching_keys = {q["key"] for q in QUEST_POOL if q["metric"] == metric}
    from datetime import date as _date
    today = _date.today()
    today_s = today.isoformat()
    with get_db() as db:
        for row in quests:
            if row["quest_key"] not in matching_keys:
                continue
            if row.get("completed_at"):
                continue
            new_prog = min(int(row["target"]), int(row["progress"]) + amount)
            done_now = new_prog >= int(row["target"])
            if done_now:
                _exec(
                    db,
                    "UPDATE student_daily_quests SET progress = %s, completed_at = "
                    + ("NOW()" if _USE_PG else "datetime('now','localtime')")
                    + " WHERE id = %s",
                    (new_prog, row["id"]),
                )
                # Award XP for the quest itself
                _insert_returning_id(
                    db,
                    "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s) RETURNING id",
                    (client_id, "daily_quest", int(row["xp_reward"]), f"Quest: {row['quest_key']}"),
                    "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (?, ?, ?, ?)",
                )
                newly_completed.append(dict(row, progress=new_prog, completed_at="just now"))
            else:
                _exec(
                    db,
                    "UPDATE student_daily_quests SET progress = %s WHERE id = %s",
                    (new_prog, row["id"]),
                )
    # Bundle bonus when ALL 3 done
    if newly_completed:
        with get_db() as db:
            done_count = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_daily_quests "
                "WHERE client_id = %s AND quest_date = %s AND completed_at IS NOT NULL",
                (client_id, today if _USE_PG else today_s),
            ) or 0
            total = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_daily_quests "
                "WHERE client_id = %s AND quest_date = %s",
                (client_id, today if _USE_PG else today_s),
            ) or 0
            already = _fetchval(
                db,
                "SELECT id FROM student_daily_quest_bundles "
                "WHERE client_id = %s AND quest_date = %s",
                (client_id, today if _USE_PG else today_s),
            )
            if total >= 3 and done_count >= total and not already:
                try:
                    _exec(
                        db,
                        "INSERT INTO student_daily_quest_bundles (client_id, quest_date) VALUES (%s, %s)",
                        (client_id, today if _USE_PG else today_s),
                    )
                    _insert_returning_id(
                        db,
                        "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (%s, %s, %s, %s) RETURNING id",
                        (client_id, "quest_bundle", QUEST_BUNDLE_BONUS_XP, "All 3 daily quests complete"),
                        "INSERT INTO student_xp (client_id, action, xp, detail) VALUES (?, ?, ?, ?)",
                    )
                except Exception:
                    pass
    return newly_completed


# -- Friends -------------------------------------------------

def search_users(query: str, exclude_client_id: int | None = None, limit: int = 20) -> list[dict]:
    """Search clients by name or numeric ID. Returns id, name, email."""
    q = (query or "").strip()
    if not q:
        return []
    rows: list = []
    with get_db() as db:
        # Numeric ID lookup
        if q.lstrip("#").isdigit():
            cid = int(q.lstrip("#"))
            r = _fetchone(db, "SELECT id, name, email FROM clients WHERE id = %s", (cid,))
            if r:
                rows.append(r)
        # Name/email LIKE
        like = f"%{q}%"
        more = _fetchall(
            db,
            "SELECT id, name, email FROM clients "
            "WHERE (name ILIKE %s OR email ILIKE %s) "
            "ORDER BY id ASC LIMIT %s" if _USE_PG else
            "SELECT id, name, email FROM clients "
            "WHERE (name LIKE ? OR email LIKE ?) "
            "ORDER BY id ASC LIMIT ?",
            (like, like, limit),
        ) or []
        rows.extend(more)
    seen = set()
    out = []
    for r in rows:
        rid = r["id"]
        if rid in seen:
            continue
        if exclude_client_id and rid == exclude_client_id:
            continue
        seen.add(rid)
        out.append({"id": rid, "name": r.get("name") or "", "email": r.get("email") or ""})
        if len(out) >= limit:
            break
    return out


def add_friend(client_id: int, friend_id: int) -> str:
    """Send/accept a friend request. Returns 'requested', 'accepted', 'already', 'self'."""
    if client_id == friend_id:
        return "self"
    with get_db() as db:
        # If the other side already requested, accept both directions
        reverse = _fetchone(
            db,
            "SELECT id, status FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
            (friend_id, client_id),
        )
        existing = _fetchone(
            db,
            "SELECT id, status FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
            (client_id, friend_id),
        )
        if existing and existing["status"] == "accepted":
            return "already"
        if reverse:
            # Accept both directions
            _exec(db, "UPDATE student_friends SET status = 'accepted' WHERE id = %s", (reverse["id"],))
            if existing:
                _exec(db, "UPDATE student_friends SET status = 'accepted' WHERE id = %s", (existing["id"],))
            else:
                _exec(
                    db,
                    "INSERT INTO student_friends (client_id, friend_client_id, status) VALUES (%s, %s, 'accepted')",
                    (client_id, friend_id),
                )
            return "accepted"
        if existing:
            return "already"
        _exec(
            db,
            "INSERT INTO student_friends (client_id, friend_client_id, status) VALUES (%s, %s, 'pending')",
            (client_id, friend_id),
        )
        return "requested"


def remove_friend(client_id: int, friend_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
              (client_id, friend_id))
        _exec(db, "DELETE FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
              (friend_id, client_id))


def list_friends(client_id: int) -> dict:
    """Return {'friends': [...accepted...], 'incoming': [...pending TO me...], 'outgoing': [...pending FROM me...]}."""
    with get_db() as db:
        accepted = _fetchall(
            db,
            "SELECT c.id, c.name, c.email FROM student_friends sf "
            "JOIN clients c ON c.id = sf.friend_client_id "
            "WHERE sf.client_id = %s AND sf.status = 'accepted'",
            (client_id,),
        ) or []
        incoming = _fetchall(
            db,
            "SELECT c.id, c.name, c.email FROM student_friends sf "
            "JOIN clients c ON c.id = sf.client_id "
            "WHERE sf.friend_client_id = %s AND sf.status = 'pending'",
            (client_id,),
        ) or []
        outgoing = _fetchall(
            db,
            "SELECT c.id, c.name, c.email FROM student_friends sf "
            "JOIN clients c ON c.id = sf.friend_client_id "
            "WHERE sf.client_id = %s AND sf.status = 'pending'",
            (client_id,),
        ) or []
    return {
        "friends":  [{"id": r["id"], "name": r.get("name") or "", "email": r.get("email") or ""} for r in accepted],
        "incoming": [{"id": r["id"], "name": r.get("name") or "", "email": r.get("email") or ""} for r in incoming],
        "outgoing": [{"id": r["id"], "name": r.get("name") or "", "email": r.get("email") or ""} for r in outgoing],
    }


# -- 7-day Duels ---------------------------------------------

def start_duel(challenger_id: int, opponent_id: int) -> int:
    """Create a 7-day duel. Returns the duel id."""
    from datetime import datetime, timedelta
    ends = datetime.now() + timedelta(days=7)
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_duels (challenger_id, opponent_id, ends_at, status) "
            "VALUES (%s, %s, %s, 'active') RETURNING id",
            (challenger_id, opponent_id, ends),
            "INSERT INTO student_duels (challenger_id, opponent_id, ends_at, status) "
            "VALUES (?, ?, ?, 'active')",
        )


def get_active_duels(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT d.*, "
            "  cc.name AS challenger_name, "
            "  oc.name AS opponent_name "
            "FROM student_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE (d.challenger_id = %s OR d.opponent_id = %s) AND d.status = 'active' "
            "ORDER BY d.ends_at ASC",
            (client_id, client_id),
        ) or []
    return [dict(r) for r in rows]


def get_duel_history(client_id: int, limit: int = 50) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT d.*, "
            "  cc.name AS challenger_name, "
            "  oc.name AS opponent_name "
            "FROM student_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE (d.challenger_id = %s OR d.opponent_id = %s) AND d.status IN ('settled','tied') "
            "ORDER BY d.settled_at DESC LIMIT %s",
            (client_id, client_id, limit),
        ) or []
    return [dict(r) for r in rows]


def get_head_to_head(client_id: int, friend_id: int) -> dict:
    """Return {wins, losses, ties} between two users."""
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT winner_id, status FROM student_duels "
            "WHERE status IN ('settled','tied') "
            "AND ((challenger_id = %s AND opponent_id = %s) OR (challenger_id = %s AND opponent_id = %s))",
            (client_id, friend_id, friend_id, client_id),
        ) or []
    wins = losses = ties = 0
    for r in rows:
        if r.get("status") == "tied" or r.get("winner_id") is None:
            ties += 1
        elif r.get("winner_id") == client_id:
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "losses": losses, "ties": ties}


def settle_due_duels() -> int:
    """Find duels past their end-time, compute focus minutes for both sides,
    and mark as settled. Returns number of duels settled."""
    from datetime import datetime
    settled = 0
    with get_db() as db:
        if _USE_PG:
            rows = _fetchall(
                db,
                "SELECT * FROM student_duels WHERE status = 'active' AND ends_at <= NOW()",
            ) or []
        else:
            rows = _fetchall(
                db,
                "SELECT * FROM student_duels WHERE status = 'active' "
                "AND ends_at <= datetime('now','localtime')",
            ) or []
    for d in rows:
        c_min = _focus_minutes_between(d["challenger_id"], d["started_at"], d["ends_at"])
        o_min = _focus_minutes_between(d["opponent_id"], d["started_at"], d["ends_at"])
        if c_min > o_min:
            winner = d["challenger_id"]; status = "settled"
        elif o_min > c_min:
            winner = d["opponent_id"]; status = "settled"
        else:
            winner = None; status = "tied"
        with get_db() as db2:
            _exec(
                db2,
                "UPDATE student_duels SET challenger_minutes = %s, opponent_minutes = %s, "
                "winner_id = %s, status = %s, settled_at = "
                + ("NOW()" if _USE_PG else "datetime('now','localtime')")
                + " WHERE id = %s",
                (c_min, o_min, winner, status, d["id"]),
            )
        # Payout XP + coins to the marathon winner (or both on a tie).
        try:
            if status == "tied":
                add_coins(d["challenger_id"], MARATHON_TIE_COINS, "marathon_tie")
                add_coins(d["opponent_id"],   MARATHON_TIE_COINS, "marathon_tie")
                award_xp(d["challenger_id"], "marathon_tie", MARATHON_TIE_XP, f"marathon {d['id']}")
                award_xp(d["opponent_id"],   "marathon_tie", MARATHON_TIE_XP, f"marathon {d['id']}")
            elif winner:
                add_coins(winner, MARATHON_WIN_COINS, "marathon_win")
                award_xp(winner, "marathon_win", MARATHON_WIN_XP, f"marathon {d['id']}")
        except Exception:
            log.exception("marathon payout failed for duel %s", d["id"])
        settled += 1
    return settled


def _focus_minutes_between(client_id: int, start, end) -> int:
    """Sum focus_minutes from student_study_progress between two timestamps (using created_at)."""
    with get_db() as db:
        v = _fetchval(
            db,
            "SELECT COALESCE(SUM(focus_minutes), 0) FROM student_study_progress "
            "WHERE client_id = %s AND created_at >= %s AND created_at <= %s",
            (client_id, start, end),
        )
    try:
        return int(v or 0)
    except Exception:
        return 0


# -- Streak Risk Push (8pm cron) -----------------------------

def get_streak_risk_recipients(min_streak: int = 5) -> list[dict]:
    """Return active students whose streak >= min_streak AND have no XP today.
    Each item: {client_id, email, name, streak}."""
    from datetime import date as _date
    today = _date.today()
    out: list[dict] = []
    with get_db() as db:
        clients = _fetchall(
            db,
            "SELECT id, name, email FROM clients "
            "WHERE COALESCE(retired, FALSE) = FALSE AND email IS NOT NULL AND email <> ''" if _USE_PG else
            "SELECT id, name, email FROM clients "
            "WHERE COALESCE(retired, 0) = 0 AND email IS NOT NULL AND email <> ''",
        ) or []
    for c in clients:
        cid = c["id"]
        streak = get_streak_days(cid)
        if streak < min_streak:
            continue
        # Did they get XP today already?
        with get_db() as db2:
            today_act = _fetchval(
                db2,
                ("SELECT id FROM student_xp WHERE client_id = %s AND created_at::date = %s LIMIT 1"
                 if _USE_PG else
                 "SELECT id FROM student_xp WHERE client_id = %s AND date(created_at) = %s LIMIT 1"),
                (cid, today),
            )
        if today_act:
            continue
        out.append({
            "client_id": cid,
            "email":     c.get("email") or "",
            "name":      c.get("name") or "",
            "streak":    streak,
        })
    return out


# ── Wallet (coins, streak freezes, banners) ─────────────────

import json as _json

# Banner catalog: key -> { name, price_coins, xp_required, css, plus_only? }
BANNERS = {
    "default":    {"name": "Default",            "price_coins": 0,    "xp_required": 0,     "css": "linear-gradient(135deg,#475569,#1e293b)"},
    "ocean":      {"name": "Ocean Wave",         "price_coins": 50,   "xp_required": 100,   "css": "linear-gradient(135deg,#06b6d4,#3b82f6)"},
    "sunset":     {"name": "Sunset",             "price_coins": 50,   "xp_required": 100,   "css": "linear-gradient(135deg,#f97316,#ec4899)"},
    "forest":     {"name": "Forest",             "price_coins": 75,   "xp_required": 250,   "css": "linear-gradient(135deg,#10b981,#065f46)"},
    "lavender":   {"name": "Lavender Dream",     "price_coins": 100,  "xp_required": 500,   "css": "linear-gradient(135deg,#a78bfa,#7c3aed)"},
    "gold":       {"name": "Gold Rush",          "price_coins": 200,  "xp_required": 1000,  "css": "linear-gradient(135deg,#facc15,#b45309)"},
    "galaxy":     {"name": "Galaxy",             "price_coins": 300,  "xp_required": 2500,  "css": "linear-gradient(135deg,#1e1b4b,#7c3aed,#ec4899)"},
    "champion":   {"name": "Champion (Elite)",   "price_coins": 500,  "xp_required": 5000,  "css": "linear-gradient(135deg,#f43f5e,#facc15,#10b981)"},
    # ── Generative banners ────────────────────────────────────────────
    "aurora":     {"name": "Aurora Borealis",    "price_coins": 250,  "xp_required": 750,
                   "animated": True, "anim_class": "bnr-anim-aurora-borealis",
                   "css": "radial-gradient(ellipse 120% 80% at 30% 0%, rgba(34,197,94,.55), transparent 60%), radial-gradient(ellipse 120% 80% at 70% 100%, rgba(56,189,248,.55), transparent 60%), radial-gradient(ellipse 80% 100% at 50% 50%, rgba(168,85,247,.45), transparent 70%), #0a0f1f"},
    "iridescent": {"name": "Iridescent Pearl",   "price_coins": 350,  "xp_required": 1500,
                   "css": "conic-gradient(from 220deg at 50% 50%, #f0abfc, #67e8f9, #fde68a, #86efac, #c4b5fd, #f0abfc)"},
    "matrix":     {"name": "Matrix Rain (PLUS)", "price_coins": 800,  "xp_required": 0, "plus_only": True,
                   "animated": True, "anim_class": "bnr-anim-matrix",
                   "css": ("url('data:image/svg+xml;utf8,"
                           "<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2284%22 height=%22480%22>"
                           "<style>.g{font:bold 18px ui-monospace,Menlo,monospace;fill:%2322c55e}"
                           ".h{font:bold 18px ui-monospace,Menlo,monospace;fill:%23dcfce7}</style>"
                           "<text class=%22h%22 x=%228%22 y=%2218%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%2248%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%2278%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22108%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22138%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22168%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22198%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22228%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22258%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22288%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22318%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22348%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22378%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22408%22>1</text>"
                           "<text class=%22g%22 x=%228%22 y=%22438%22>0</text>"
                           "<text class=%22g%22 x=%228%22 y=%22468%22>1</text>"
                           "<text class=%22h%22 x=%2236%22 y=%2230%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%2260%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%2290%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22120%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22150%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22180%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22210%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22240%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22270%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22300%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22330%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22360%22>0</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22390%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22420%22>1</text>"
                           "<text class=%22g%22 x=%2236%22 y=%22450%22>0</text>"
                           "<text class=%22h%22 x=%2264%22 y=%2212%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%2242%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%2272%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22102%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22132%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22162%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22192%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22222%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22252%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22282%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22312%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22342%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22372%22>0</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22402%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22432%22>1</text>"
                           "<text class=%22g%22 x=%2264%22 y=%22462%22>0</text>"
                           "</svg>'), "
                           "linear-gradient(180deg, #022c22 0%, #000 100%)")},
    "blueprint":  {"name": "Blueprint Grid",     "price_coins": 250,  "xp_required": 750,
                   "css": "repeating-linear-gradient(0deg, rgba(255,255,255,.08) 0 1px, transparent 1px 24px), repeating-linear-gradient(90deg, rgba(255,255,255,.08) 0 1px, transparent 1px 24px), linear-gradient(135deg, #0c4a6e, #1e3a8a)"},
    "hologram":   {"name": "Hologram",           "price_coins": 600,  "xp_required": 6000,
                   "css": "conic-gradient(from 0deg at 50% 50%, rgba(236,72,153,.7), rgba(99,102,241,.7), rgba(34,211,238,.7), rgba(132,204,22,.7), rgba(236,72,153,.7)), repeating-linear-gradient(45deg, rgba(255,255,255,.06) 0 2px, transparent 2px 8px)"},
    "abyss":      {"name": "Deep Abyss",         "price_coins": 450,  "xp_required": 3500,
                   "animated": True, "anim_class": "bnr-anim-abyss",
                   "css": "radial-gradient(circle at 20% 30%, #312e81 0%, transparent 45%), radial-gradient(circle at 80% 70%, #155e75 0%, transparent 45%), radial-gradient(circle at 50% 50%, #020617 60%, #000 100%)"},
    "phoenix":    {"name": "Phoenix",            "price_coins": 750,  "xp_required": 8000,
                   "css": "radial-gradient(circle at 50% 100%, #fde047 0%, #f97316 25%, #dc2626 55%, #1e1b4b 100%)"},
    # ── Coin-only banners (no XP gate, accessible to new players) ──
    "candy":      {"name": "Candy Pop",          "price_coins": 80,   "xp_required": 0,
                   "css": "linear-gradient(135deg,#fbcfe8,#f9a8d4,#a5b4fc)"},
    "mint":       {"name": "Fresh Mint",         "price_coins": 80,   "xp_required": 0,
                   "css": "linear-gradient(135deg,#a7f3d0,#6ee7b7,#34d399)"},
    "obsidian":   {"name": "Obsidian",           "price_coins": 150,  "xp_required": 200,
                   "css": "linear-gradient(135deg,#0f172a,#1e293b,#334155)"},
    "rosegold":   {"name": "Rose Gold",          "price_coins": 200,  "xp_required": 400,
                   "css": "linear-gradient(135deg,#fda4af,#f59e0b,#fde68a)"},
    "sapphire":   {"name": "Sapphire",           "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#1e3a8a,#3b82f6,#93c5fd)"},
    "emerald":    {"name": "Emerald",            "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#064e3b,#059669,#a7f3d0)"},
    "ruby":       {"name": "Ruby",               "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#7f1d1d,#dc2626,#fecaca)"},
    "amethyst":   {"name": "Amethyst",           "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#4c1d95,#7c3aed,#c4b5fd)"},
    "topaz":      {"name": "Topaz",              "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#92400e,#f59e0b,#fde68a)"},
    "pearl":      {"name": "Pearl",              "price_coins": 220,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#f1f5f9,#cbd5e1,#94a3b8)"},
    # ── Themed / illustrative banners ─────────────────────────────────
    "cherry":     {"name": "Cherry Blossom",     "price_coins": 280,  "xp_required": 800,
                   "animated": True, "anim_class": "bnr-anim-cherry",
                   "css": "radial-gradient(circle at 20% 30%, #fbcfe8 0%, transparent 30%), radial-gradient(circle at 80% 60%, #f9a8d4 0%, transparent 30%), radial-gradient(2px 3px at 15% 20%, #ec4899 50%, transparent 55%), radial-gradient(3px 2px at 75% 70%, #db2777 50%, transparent 55%), radial-gradient(2px 2px at 45% 40%, #f472b6 50%, transparent 55%), radial-gradient(3px 3px at 90% 25%, #be185d 50%, transparent 55%), linear-gradient(135deg,#fff1f2,#fbcfe8)"},
    "tropical":   {"name": "Tropical Sunset",    "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#0c4a6e 0%,#1e3a8a 30%,#7c3aed 55%,#ec4899 80%,#f59e0b 100%)"},
    "tundra":     {"name": "Frozen Tundra",      "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#1e3a8a,#2563eb,#bae6fd,#f0f9ff)"},
    "desert":     {"name": "Sahara Dusk",        "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#fde68a 0%,#f59e0b 30%,#b45309 60%,#7c2d12 100%)"},
    "savanna":    {"name": "Savanna Sky",        "price_coins": 320,  "xp_required": 1200,
                   "css": "linear-gradient(180deg,#fbbf24 0%,#f97316 40%,#7c2d12 100%)"},
    "deepsea":    {"name": "Deep Sea",           "price_coins": 320,  "xp_required": 1200,
                   "animated": True, "anim_class": "bnr-anim-deepsea",
                   "css": "radial-gradient(ellipse 60% 40% at 30% 80%, rgba(6,182,212,.55) 0%, transparent 60%), radial-gradient(ellipse 50% 30% at 70% 20%, rgba(14,165,233,.5) 0%, transparent 60%), radial-gradient(circle at 50% 50%, rgba(125,211,252,.18) 0%, transparent 50%), linear-gradient(180deg,#0c4a6e,#082f49,#020617)"},
    "noir":       {"name": "Noir",               "price_coins": 350,  "xp_required": 1500,
                   "css": "repeating-linear-gradient(135deg, #18181b 0 24px, #27272a 24px 48px)"},
    "neon_grid":  {"name": "Neon Grid",          "price_coins": 420,  "xp_required": 2500,
                   "animated": True, "anim_class": "bnr-anim-neongrid",
                   "css": "repeating-linear-gradient(0deg, rgba(236,72,153,.22) 0 1px, transparent 1px 28px), repeating-linear-gradient(90deg, rgba(34,211,238,.22) 0 1px, transparent 1px 28px), radial-gradient(ellipse at center, rgba(168,85,247,.35) 0%, transparent 60%), linear-gradient(135deg,#0f172a,#581c87)"},
    "starfield":  {"name": "Starfield",          "price_coins": 420,  "xp_required": 2500,
                   "animated": True, "anim_class": "bnr-anim-starfield",
                   "css": "radial-gradient(2px 2px at 20% 30%, #fff 50%, transparent 50%), radial-gradient(2px 2px at 80% 70%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 50% 50%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 30% 70%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 70% 20%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 10% 60%, #fff 50%, transparent 50%), radial-gradient(2px 2px at 90% 40%, #fff 50%, transparent 50%), linear-gradient(135deg,#000,#1e1b4b)"},
    "carbon":     {"name": "Carbon Fiber",       "price_coins": 320,  "xp_required": 1200,
                   "css": "repeating-linear-gradient(45deg, #1f2937 0 6px, #111827 6px 12px), repeating-linear-gradient(-45deg, rgba(255,255,255,.04) 0 6px, transparent 6px 12px)"},
    "stained":    {"name": "Stained Glass",      "price_coins": 480,  "xp_required": 3000,
                   "css": "conic-gradient(from 45deg at 30% 30%, #ef4444, #f59e0b, #84cc16, #06b6d4, #8b5cf6, #ec4899, #ef4444)"},
    # ── PLUS-only banners (premium cosmetics) ─────────────────────────
    "plus_velvet":   {"name": "Velvet (PLUS)",   "price_coins": 600,  "xp_required": 0,    "plus_only": True,
                      "animated": True, "anim_class": "bnr-anim-velvet",
                      "css": "radial-gradient(ellipse at 30% 30%, rgba(168,85,247,.6), transparent 60%), radial-gradient(ellipse at 70% 70%, rgba(236,72,153,.6), transparent 60%), linear-gradient(135deg,#3b0764,#831843)"},
    "plus_chrome":   {"name": "Chrome (PLUS)",   "price_coins": 600,  "xp_required": 0,    "plus_only": True,
                      "css": "linear-gradient(135deg,#f8fafc 0%,#cbd5e1 25%,#475569 55%,#cbd5e1 80%,#f8fafc 100%)"},
    "plus_aurora2":  {"name": "Polar Aurora (PLUS)", "price_coins": 800, "xp_required": 0, "plus_only": True,
                      "animated": True, "anim_class": "bnr-anim-polar",
                      "css": "radial-gradient(ellipse 120% 80% at 50% 0%, rgba(56,189,248,.7), transparent 70%), radial-gradient(ellipse 100% 70% at 50% 100%, rgba(168,85,247,.7), transparent 70%), radial-gradient(ellipse 60% 80% at 30% 50%, rgba(34,197,94,.6), transparent 70%), #020617"},
    "plus_prism":    {"name": "Prism (PLUS)",    "price_coins": 800,  "xp_required": 0,    "plus_only": True,
                      "css": "conic-gradient(from 0deg at 50% 50%, #ef4444, #f59e0b, #fde047, #84cc16, #06b6d4, #6366f1, #a855f7, #ef4444)"},
    "plus_cosmic":   {"name": "Cosmic Drift (PLUS)", "price_coins": 1000, "xp_required": 0, "plus_only": True,
                      "animated": True, "anim_class": "bnr-anim-cosmic",
                      "css": "radial-gradient(2px 2px at 25% 35%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 75% 75%, #fff 50%, transparent 50%), radial-gradient(2px 2px at 60% 20%, #fff 50%, transparent 50%), radial-gradient(circle at 30% 50%, rgba(124,58,237,.55) 0%, transparent 45%), radial-gradient(circle at 80% 30%, rgba(236,72,153,.45) 0%, transparent 40%), linear-gradient(135deg,#0c0a1f,#1e1b4b,#0c0a1f)"},
    "plus_quartz":   {"name": "Quartz (PLUS)",   "price_coins": 700,  "xp_required": 0,    "plus_only": True,
                      "css": "conic-gradient(from 90deg at 50% 50%, #fef3c7, #ddd6fe, #bae6fd, #fbcfe8, #fef3c7)"},
    "plus_void":     {"name": "Void (PLUS)",     "price_coins": 700,  "xp_required": 0,    "plus_only": True,
                      "animated": True, "anim_class": "bnr-anim-void",
                      "css": "radial-gradient(circle at 50% 50%, transparent 0%, #000 65%), radial-gradient(circle at 30% 30%, rgba(109,40,217,.7) 0%, transparent 30%), radial-gradient(circle at 70% 70%, rgba(219,39,119,.7) 0%, transparent 30%), radial-gradient(circle at 20% 80%, rgba(56,189,248,.4) 0%, transparent 25%), #000"},
    "plus_gold":     {"name": "24K Gold (PLUS)", "price_coins": 900,  "xp_required": 0,    "plus_only": True,
                      "css": "linear-gradient(135deg,#fde68a 0%,#fbbf24 25%,#b45309 50%,#fbbf24 75%,#fde68a 100%)"},
    "plus_galaxy2":  {"name": "Andromeda (PLUS)", "price_coins": 1200, "xp_required": 0,   "plus_only": True,
                      "animated": True, "anim_class": "bnr-anim-andromeda",
                      "css": "radial-gradient(2px 2px at 15% 25%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 65% 75%, #fff 50%, transparent 50%), radial-gradient(2px 2px at 85% 15%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 40% 60%, #fff 50%, transparent 50%), radial-gradient(circle at 30% 60%, rgba(168,85,247,.55) 0%, transparent 30%), radial-gradient(circle at 70% 40%, rgba(56,189,248,.55) 0%, transparent 30%), radial-gradient(ellipse at center, #1e1b4b 0%, #000 100%)"},
    # ── Animated PLUS-only banners ──────────────────────────────────
    # Each entry sets `animated: True` and `anim_class` so the renderer can
    # apply the matching CSS class (defined in BANNER_ANIM_CSS below) which
    # provides the @keyframes + background-size for smooth motion.
    "plus_anim_aurora":  {"name": "Drifting Aurora (PLUS)", "price_coins": 1500, "xp_required": 0,
                          "plus_only": True, "animated": True, "anim_class": "bnr-anim-aurora",
                          "css": "linear-gradient(120deg,#022c22,#16a34a 25%,#38bdf8 55%,#a855f7 80%,#022c22)"},
    "plus_anim_magma":   {"name": "Flowing Magma (PLUS)",   "price_coins": 1500, "xp_required": 0,
                          "plus_only": True, "animated": True, "anim_class": "bnr-anim-magma",
                          "css": "radial-gradient(circle at 30% 70%, #fde047 0%, transparent 28%), radial-gradient(circle at 70% 30%, #ef4444 0%, transparent 30%), linear-gradient(135deg,#7c2d12,#1c0a04)"},
    "plus_anim_neon":    {"name": "Neon Drift (PLUS)",      "price_coins": 1500, "xp_required": 0,
                          "plus_only": True, "animated": True, "anim_class": "bnr-anim-neon",
                          "css": "linear-gradient(120deg,#ec4899,#8b5cf6 30%,#06b6d4 60%,#ec4899)"},
    "plus_anim_sunset":  {"name": "Endless Sunset (PLUS)",  "price_coins": 1500, "xp_required": 0,
                          "plus_only": True, "animated": True, "anim_class": "bnr-anim-sunset",
                          "css": "linear-gradient(120deg,#1e1b4b,#7c3aed 30%,#ec4899 55%,#f59e0b 80%,#1e1b4b)"},
}

# CSS injected once per page that uses banners. Provides the @keyframes for
# every animated banner in BANNERS (matched by anim_class). Background-size
# is set generously so the gradient has room to drift.
BANNER_ANIM_CSS = """
@keyframes bnr-pan-x { 0% { background-position: 0% 50%; } 100% { background-position: 200% 50%; } }
@keyframes bnr-pan-diag { 0% { background-position: 0% 0%; } 50% { background-position: 100% 100%; } 100% { background-position: 0% 0%; } }
@keyframes bnr-pan-slow { 0% { background-position: 0% 50%; } 100% { background-position: 100% 50%; } }
@keyframes bnr-pan-vert { 0% { background-position: 50% 0%; } 100% { background-position: 50% 200%; } }
@keyframes bnr-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
@keyframes bnr-glow-pulse {
  0%,100% { filter: brightness(1) saturate(1); }
  50%     { filter: brightness(1.25) saturate(1.4); }
}
@keyframes bnr-twinkle {
  0%,100% { opacity: .85; transform: scale(1); }
  50%     { opacity: 1;   transform: scale(1.04); }
}
@keyframes bnr-grid-scan { 0% { background-position: 0 0, 0 0, 50% 50%, 0 0; } 100% { background-position: 28px 28px, 28px 28px, 50% 50%, 0 0; } }
@keyframes bnr-void-pulse {
  0%,100% { background-position: 50% 50%, 30% 30%, 70% 70%, 20% 80%, 0 0; filter: brightness(1); }
  50%     { background-position: 50% 50%, 35% 25%, 65% 75%, 25% 85%, 0 0; filter: brightness(1.3); }
}
@keyframes bnr-andromeda-drift {
  0%   { background-position: 0% 0%, 0% 0%, 0% 0%, 0% 0%, 0% 0%, 0% 0%, 0% 0%; transform: scale(1) rotate(0deg); }
  50%  { transform: scale(1.04) rotate(.6deg); }
  100% { background-position: 100% 100%, 100% 100%, 100% 100%, 100% 100%, 100% 100%, 100% 100%, 100% 100%; transform: scale(1) rotate(0deg); }
}

.bnr-anim-host { overflow:hidden; position:relative; }

/* Aurora-family pan banners (Plus animated set) */
.bnr-anim-aurora,.bnr-anim-magma,.bnr-anim-neon,.bnr-anim-sunset { background-size: 300% 300% !important; }
.bnr-anim-aurora { animation: bnr-pan-x 18s linear infinite; }
.bnr-anim-magma  { animation: bnr-pan-diag 14s ease-in-out infinite; }
.bnr-anim-neon   { animation: bnr-pan-x 10s linear infinite; }
.bnr-anim-sunset { animation: bnr-pan-x 24s linear infinite; }

/* Static-art banners that now drift / pulse */
.bnr-anim-deepsea   { background-size: 200% 200% !important; animation: bnr-pan-diag 22s ease-in-out infinite, bnr-glow-pulse 6s ease-in-out infinite; }
.bnr-anim-starfield { background-size: 200% 200% !important; animation: bnr-pan-vert 60s linear infinite, bnr-twinkle 3.5s ease-in-out infinite; }
.bnr-anim-neongrid  { background-size: 28px 28px, 28px 28px, 200% 200%, 100% 100% !important; animation: bnr-grid-scan 4s linear infinite, bnr-glow-pulse 5s ease-in-out infinite; }
.bnr-anim-void      { background-size: 200% 200%, 200% 200%, 200% 200%, 200% 200%, 100% 100% !important; animation: bnr-void-pulse 8s ease-in-out infinite; }
.bnr-anim-cosmic    { background-size: 200% 200% !important; animation: bnr-pan-diag 30s ease-in-out infinite, bnr-twinkle 4s ease-in-out infinite; }
.bnr-anim-andromeda { background-size: 220% 220%, 220% 220%, 220% 220%, 220% 220%, 220% 220%, 220% 220%, 220% 220%; transform-origin: center; animation: bnr-andromeda-drift 28s ease-in-out infinite, bnr-twinkle 5s ease-in-out infinite; }

/* Matrix Rain — actual falling 1s and 0s (SVG layer scrolls down). The SVG
   tile is 84px x 480px (3 staggered columns) and is set as the first
   background-image via the banner's css value. We just animate Y. */
@keyframes bnr-matrix-rain {
  0%   { background-position: 0 0,    0 0; }
  100% { background-position: 0 480px, 0 0; }
}
.bnr-anim-matrix {
  background-size: 84px 480px, 100% 100% !important;
  background-repeat: repeat, no-repeat !important;
  animation: bnr-matrix-rain 4s linear infinite;
  filter: drop-shadow(0 0 6px rgba(74,222,128,.45));
}

/* Aurora Borealis — luminous green/cyan ribbons swimming */
@keyframes bnr-aurora-borealis {
  0%,100% { background-position: 30% 0%, 70% 100%, 50% 50%, 0 0; filter: hue-rotate(0deg) brightness(1); }
  50%     { background-position: 60% 10%, 40% 90%, 55% 45%, 0 0; filter: hue-rotate(25deg) brightness(1.2); }
}
.bnr-anim-aurora-borealis {
  background-size: 220% 220%, 220% 220%, 220% 220%, 100% 100% !important;
  animation: bnr-aurora-borealis 14s ease-in-out infinite;
}

/* Deep Abyss — slow swirling pressure with breathing glow */
@keyframes bnr-abyss {
  0%,100% { background-position: 20% 30%, 80% 70%, 50% 50%; filter: brightness(.85); }
  50%     { background-position: 30% 35%, 70% 65%, 55% 50%; filter: brightness(1.15); }
}
.bnr-anim-abyss {
  background-size: 180% 180%, 180% 180%, 180% 180% !important;
  animation: bnr-abyss 11s ease-in-out infinite;
}

/* Cherry Blossom — petals drifting diagonally */
@keyframes bnr-cherry {
  0%   { background-position: 20% 30%, 80% 60%, 0 -40px, 0 -40px, 0 -40px, 0 -40px, 0 0; }
  100% { background-position: 25% 32%, 75% 58%, 40px 320px, -40px 320px, 30px 320px, -30px 320px, 0 0; }
}
.bnr-anim-cherry {
  background-size: 100% 100%, 100% 100%, 200px 220px, 220px 240px, 180px 200px, 240px 260px, 100% 100% !important;
  animation: bnr-cherry 9s linear infinite;
}

/* Velvet (PLUS) — soft purple/pink blooms breathing */
@keyframes bnr-velvet {
  0%,100% { background-position: 30% 30%, 70% 70%, 0 0; filter: saturate(1) brightness(1); }
  50%     { background-position: 35% 28%, 65% 72%, 0 0; filter: saturate(1.25) brightness(1.15); }
}
.bnr-anim-velvet {
  background-size: 200% 200%, 200% 200%, 100% 100% !important;
  animation: bnr-velvet 10s ease-in-out infinite;
}

/* Polar Aurora (PLUS) — northern lights drifting both ways */
@keyframes bnr-polar {
  0%,100% { background-position: 50% 0%,  50% 100%, 30% 50%, 0 0; filter: hue-rotate(0deg)   brightness(1); }
  33%     { background-position: 35% 5%,  65% 95%,  40% 45%, 0 0; filter: hue-rotate(-15deg) brightness(1.18); }
  66%     { background-position: 65% 8%,  35% 92%,  20% 55%, 0 0; filter: hue-rotate(20deg)  brightness(1.1); }
}
.bnr-anim-polar {
  background-size: 240% 240%, 240% 240%, 200% 200%, 100% 100% !important;
  animation: bnr-polar 16s ease-in-out infinite;
}
"""

# Leaderboard flag catalog. Rendered as a horizontal CSS background on the
# leaderboard row, mask-faded from full opacity on the LEFT to transparent on
# the RIGHT. Same structure as banners (re-uses cfg["css"]).
FLAGS = {
    "none":       {"name": "No flag",            "price_coins": 0,    "xp_required": 0,
                   "css": "transparent"},
    "sunrise":    {"name": "Sunrise Streak",     "price_coins": 75,   "xp_required": 200,
                   "css": "linear-gradient(90deg, #f97316 0%, #fde047 100%)"},
    "tide":       {"name": "Tidal Pull",         "price_coins": 75,   "xp_required": 200,
                   "css": "linear-gradient(90deg, #0ea5e9 0%, #6366f1 100%)"},
    "neon":       {"name": "Neon Pulse",         "price_coins": 150,  "xp_required": 600,
                   "css": "linear-gradient(90deg, #ec4899 0%, #8b5cf6 50%, #06b6d4 100%)"},
    "racing":     {"name": "Racing Stripes",     "price_coins": 200,  "xp_required": 1000,
                   "css": "repeating-linear-gradient(45deg, #ef4444 0 14px, #fafafa 14px 28px)"},
    "racing_anim":{"name": "Racing Stripes (Anim)", "price_coins": 350,  "xp_required": 1500,
                   "animated": True, "anim_class": "flag-anim-racing",
                   "css": "repeating-linear-gradient(45deg, #ef4444 0 14px, #fafafa 14px 28px)"},
    "verdant":    {"name": "Verdant Banner",     "price_coins": 200,  "xp_required": 1000,
                   "css": "linear-gradient(90deg, #064e3b 0%, #10b981 60%, #a7f3d0 100%)"},
    "imperial":   {"name": "Imperial Gold",      "price_coins": 350,  "xp_required": 2500,
                   "css": "linear-gradient(90deg, #78350f 0%, #f59e0b 50%, #fde68a 100%)"},
    "void":       {"name": "Void Walker",        "price_coins": 350,  "xp_required": 2500,
                   "css": "linear-gradient(90deg, #000 0%, #6d28d9 60%, #c084fc 100%)"},
    "diamond":    {"name": "Diamond Tier",       "price_coins": 600,  "xp_required": 5000,
                   "css": "linear-gradient(90deg, #1e3a8a 0%, #38bdf8 40%, #e0f2fe 100%)"},
    "crimson":    {"name": "Crimson Crown",      "price_coins": 600,  "xp_required": 5000,
                   "css": "linear-gradient(90deg, #450a0a 0%, #dc2626 50%, #fecaca 100%)"},
    "rainbow":    {"name": "Rainbow Arc",        "price_coins": 800,  "xp_required": 10000,
                   "css": "linear-gradient(90deg, #ef4444, #f97316, #facc15, #22c55e, #06b6d4, #6366f1, #a855f7)"},
    # ── New flags (more cheap, mid-tier and high-tier) ──────────────
    "ember":      {"name": "Ember",              "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(90deg, #7f1d1d 0%, #ef4444 60%, #fbbf24 100%)"},
    "ocean_calm": {"name": "Ocean Calm",         "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(90deg, #0c4a6e 0%, #0ea5e9 60%, #bae6fd 100%)"},
    "forest_run": {"name": "Forest Run",         "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(90deg, #064e3b 0%, #16a34a 60%, #bbf7d0 100%)"},
    "mocha":      {"name": "Mocha",              "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(90deg, #292524 0%, #78350f 50%, #fed7aa 100%)"},
    "lilac":      {"name": "Lilac",              "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(90deg, #581c87 0%, #a855f7 60%, #e9d5ff 100%)"},
    "checker":    {"name": "Checker Flag",       "price_coins": 250,  "xp_required": 1500,
                   "css": "repeating-conic-gradient(#0f172a 0% 25%, #f8fafc 25% 50%) 0 0 / 24px 24px"},
    "checker_anim":{"name": "Checker Flag (Anim)","price_coins": 400, "xp_required": 1800,
                   "animated": True, "anim_class": "flag-anim-checker",
                   "css": "repeating-conic-gradient(#0f172a 0% 25%, #f8fafc 25% 50%) 0 0 / 24px 24px"},
    "stripes_3":  {"name": "Tricolor",           "price_coins": 180,  "xp_required": 800,
                   "css": "linear-gradient(90deg, #ef4444 0% 33%, #fafafa 33% 66%, #2563eb 66% 100%)"},
    "stripes_5":  {"name": "Pentaband",          "price_coins": 220,  "xp_required": 1200,
                   "css": "linear-gradient(90deg, #ef4444 0% 20%, #f59e0b 20% 40%, #facc15 40% 60%, #22c55e 60% 80%, #3b82f6 80% 100%)"},
    "midnight":   {"name": "Midnight Streak",    "price_coins": 280,  "xp_required": 1500,
                   "css": "linear-gradient(90deg, #020617 0%, #1e1b4b 50%, #4c1d95 100%)"},
    "ice":        {"name": "Ice Wave",           "price_coins": 280,  "xp_required": 1500,
                   "css": "linear-gradient(90deg, #082f49 0%, #38bdf8 50%, #f0f9ff 100%)"},
    "smoke":      {"name": "Smoke",              "price_coins": 200,  "xp_required": 1000,
                   "css": "linear-gradient(90deg, #18181b 0%, #52525b 50%, #d4d4d8 100%)"},
    "candy_pop":  {"name": "Candy Pop",          "price_coins": 150,  "xp_required": 500,
                   "css": "linear-gradient(90deg, #f472b6 0%, #fcd34d 50%, #60a5fa 100%)"},
    "candy_anim": {"name": "Candy Pop (Anim)",   "price_coins": 280,  "xp_required": 900,
                   "animated": True, "anim_class": "flag-anim-candy",
                   "css": "linear-gradient(90deg, #f472b6, #fcd34d, #60a5fa, #a78bfa, #34d399, #f472b6)"},
    "matrix_bar": {"name": "Matrix Trail",       "price_coins": 320,  "xp_required": 2000,
                   "animated": True, "anim_class": "flag-anim-matrix-h",
                   "css": "repeating-linear-gradient(90deg, rgba(134,239,172,.95) 0 5px, rgba(34,197,94,.55) 5px 12px, rgba(16,185,129,.22) 12px 22px, transparent 22px 48px), repeating-linear-gradient(180deg, transparent 0 9px, rgba(0,0,0,.5) 9px 11px), linear-gradient(90deg, #022c22 0%, #16a34a 50%, #86efac 100%)"},
    "matrix_rain":{"name": "Matrix Rain (PLUS)", "price_coins": 800,  "xp_required": 0, "plus_only": True,
                   "animated": True, "anim_class": "flag-anim-matrix",
                   "css": ("url('data:image/svg+xml;utf8,"
                           "<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2284%22 height=%22120%22>"
                           "<style>.g{font:bold 14px ui-monospace,Menlo,monospace;fill:%2322c55e}"
                           ".h{font:bold 14px ui-monospace,Menlo,monospace;fill:%23dcfce7}</style>"
                           "<text class=%22h%22 x=%226%22 y=%2214%22>1</text>"
                           "<text class=%22g%22 x=%226%22 y=%2238%22>0</text>"
                           "<text class=%22g%22 x=%226%22 y=%2262%22>1</text>"
                           "<text class=%22g%22 x=%226%22 y=%2286%22>1</text>"
                           "<text class=%22g%22 x=%226%22 y=%22110%22>0</text>"
                           "<text class=%22h%22 x=%2234%22 y=%2226%22>0</text>"
                           "<text class=%22g%22 x=%2234%22 y=%2250%22>1</text>"
                           "<text class=%22g%22 x=%2234%22 y=%2274%22>0</text>"
                           "<text class=%22g%22 x=%2234%22 y=%2298%22>1</text>"
                           "<text class=%22h%22 x=%2262%22 y=%228%22>1</text>"
                           "<text class=%22g%22 x=%2262%22 y=%2232%22>1</text>"
                           "<text class=%22g%22 x=%2262%22 y=%2256%22>0</text>"
                           "<text class=%22g%22 x=%2262%22 y=%2280%22>1</text>"
                           "<text class=%22g%22 x=%2262%22 y=%22104%22>0</text>"
                           "</svg>'), "
                           "linear-gradient(180deg, #022c22 0%, #000 100%)")},
    "bloodmoon":  {"name": "Blood Moon",         "price_coins": 450,  "xp_required": 3500,
                   "css": "linear-gradient(90deg, #000 0%, #7f1d1d 50%, #f87171 100%)"},
    "honey":      {"name": "Honeycomb",          "price_coins": 320,  "xp_required": 2000,
                   "css": "linear-gradient(90deg, #78350f 0%, #f59e0b 50%, #fde68a 100%)"},
    "galaxy_bar": {"name": "Galactic Trail",     "price_coins": 700,  "xp_required": 7000,
                   "css": "linear-gradient(90deg, #000 0%, #4c1d95 30%, #db2777 60%, #f59e0b 100%)"},
    "phoenix_bar":{"name": "Phoenix Trail",      "price_coins": 800,  "xp_required": 9000,
                   "css": "linear-gradient(90deg, #1e1b4b 0%, #dc2626 30%, #f97316 60%, #fde047 100%)"},
    # ── PLUS-only flags ─────────────────────────────────────────────
    "plus_chrome":   {"name": "Chrome Streak (PLUS)", "price_coins": 700, "xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #f8fafc 0%, #94a3b8 30%, #1e293b 60%, #94a3b8 80%, #f8fafc 100%)"},
    "plus_holo":     {"name": "Holographic (PLUS)",   "price_coins": 800, "xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #fbcfe8, #67e8f9, #fde68a, #86efac, #c4b5fd, #fbcfe8)"},
    "plus_void":     {"name": "Void Bar (PLUS)",      "price_coins": 700, "xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #000 0%, #1e1b4b 40%, #6d28d9 70%, #c084fc 100%)"},
    "plus_gold":     {"name": "24K Gold Bar (PLUS)",  "price_coins": 900, "xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #fde68a 0%, #fbbf24 25%, #b45309 50%, #fbbf24 75%, #fde68a 100%)"},
    "plus_aurora":   {"name": "Aurora Bar (PLUS)",    "price_coins": 800, "xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #022c22 0%, #16a34a 25%, #38bdf8 60%, #a855f7 100%)"},
    "plus_prism":    {"name": "Prism Bar (PLUS)",     "price_coins": 1000,"xp_required": 0, "plus_only": True,
                      "css": "linear-gradient(90deg, #ef4444, #f59e0b, #fde047, #84cc16, #06b6d4, #6366f1, #a855f7)"},
    # ── More cheap / mid-tier ─────────────────────────────────────
    "rose_gold":   {"name": "Rose Gold",         "price_coins": 90,   "xp_required": 250,
                    "css": "linear-gradient(90deg, #7f1d1d 0%, #fb7185 50%, #fed7aa 100%)"},
    "tropic":      {"name": "Tropic",            "price_coins": 90,   "xp_required": 250,
                    "css": "linear-gradient(90deg, #047857 0%, #10b981 30%, #fbbf24 70%, #ef4444 100%)"},
    "bubblegum":   {"name": "Bubblegum",         "price_coins": 90,   "xp_required": 250,
                    "css": "linear-gradient(90deg, #ec4899 0%, #f472b6 50%, #fbcfe8 100%)"},
    "midnight":    {"name": "Midnight",          "price_coins": 120,  "xp_required": 400,
                    "css": "linear-gradient(90deg, #020617 0%, #1e3a8a 60%, #38bdf8 100%)"},
    "sandstorm":   {"name": "Sandstorm",         "price_coins": 120,  "xp_required": 400,
                    "css": "linear-gradient(90deg, #44403c 0%, #b45309 40%, #fde68a 100%)"},
    "candy":       {"name": "Candy",             "price_coins": 120,  "xp_required": 400,
                    "css": "repeating-linear-gradient(90deg, #ec4899 0 18px, #fbcfe8 18px 36px)"},
    "mint_chip":   {"name": "Mint Chip",         "price_coins": 150,  "xp_required": 600,
                    "css": "linear-gradient(90deg, #064e3b 0%, #34d399 40%, #ecfeff 100%)"},
    "blueprint":   {"name": "Blueprint",         "price_coins": 200,  "xp_required": 1000,
                    "css": "repeating-linear-gradient(90deg, rgba(255,255,255,.1) 0 1px, transparent 1px 18px), linear-gradient(90deg, #0c4a6e 0%, #1e3a8a 100%)"},
    "vapor":       {"name": "Vaporwave",         "price_coins": 250,  "xp_required": 1500,
                    "css": "linear-gradient(90deg, #ec4899 0%, #a855f7 35%, #06b6d4 70%, #fde047 100%)"},
    "obsidian":    {"name": "Obsidian",          "price_coins": 280,  "xp_required": 1800,
                    "css": "linear-gradient(90deg, #000 0%, #1f2937 40%, #6b7280 80%, #d1d5db 100%)"},
    "infrared":    {"name": "Infrared",          "price_coins": 300,  "xp_required": 2000,
                    "css": "linear-gradient(90deg, #450a0a 0%, #7f1d1d 30%, #f97316 60%, #facc15 100%)"},
    "ultraviolet": {"name": "Ultraviolet",       "price_coins": 300,  "xp_required": 2000,
                    "css": "linear-gradient(90deg, #1e1b4b 0%, #4c1d95 30%, #a855f7 65%, #f0abfc 100%)"},
    # ── Animated / dynamic flags ──────────────────────────────────
    "flow_aurora": {"name": "Flow Aurora",       "price_coins": 500,  "xp_required": 3000,
                    "animated": True, "anim_class": "flag-anim-pan",
                    "css": "linear-gradient(90deg, #022c22 0%, #16a34a 25%, #38bdf8 50%, #a855f7 75%, #022c22 100%)"},
    "flow_sunset": {"name": "Flow Sunset",       "price_coins": 500,  "xp_required": 3000,
                    "animated": True, "anim_class": "flag-anim-pan",
                    "css": "linear-gradient(90deg, #1e1b4b 0%, #7c3aed 30%, #ec4899 55%, #f59e0b 80%, #1e1b4b 100%)"},
    "flow_neon":   {"name": "Flow Neon",         "price_coins": 500,  "xp_required": 3000,
                    "animated": True, "anim_class": "flag-anim-fast",
                    "css": "linear-gradient(90deg, #ec4899 0%, #8b5cf6 25%, #06b6d4 50%, #8b5cf6 75%, #ec4899 100%)"},
    "flow_lava":   {"name": "Flow Lava",         "price_coins": 600,  "xp_required": 4000,
                    "animated": True, "anim_class": "flag-anim-shimmer",
                    "css": "linear-gradient(90deg, #7c2d12 0%, #ef4444 30%, #fde047 55%, #ef4444 80%, #7c2d12 100%)"},
    "flow_glacier":{"name": "Flow Glacier",      "price_coins": 600,  "xp_required": 4000,
                    "animated": True, "anim_class": "flag-anim-shimmer",
                    "css": "linear-gradient(90deg, #0c4a6e 0%, #38bdf8 35%, #f0f9ff 55%, #38bdf8 75%, #0c4a6e 100%)"},
    "flow_rainbow":{"name": "Flow Rainbow",      "price_coins": 900,  "xp_required": 9000,
                    "animated": True, "anim_class": "flag-anim-fast",
                    "css": "linear-gradient(90deg, #ef4444, #f97316, #facc15, #22c55e, #06b6d4, #6366f1, #a855f7, #ef4444)"},
    "plus_anim_holo": {"name": "Holo Sweep (PLUS)",  "price_coins": 1200, "xp_required": 0, "plus_only": True,
                       "animated": True, "anim_class": "flag-anim-shimmer",
                       "css": "linear-gradient(90deg, #fbcfe8, #67e8f9, #fde68a, #86efac, #c4b5fd, #fbcfe8)"},
    "plus_anim_chrome":{"name": "Liquid Chrome (PLUS)","price_coins": 1200, "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #f8fafc 0%, #cbd5e1 25%, #475569 50%, #cbd5e1 75%, #f8fafc 100%)"},
    "plus_anim_galaxy":{"name": "Drifting Galaxy (PLUS)","price_coins": 1500,"xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-pan",
                        "css": "linear-gradient(90deg, #000 0%, #1e1b4b 25%, #7c3aed 50%, #ec4899 75%, #000 100%)"},
}

# CSS injected once per page that renders flags. Provides @keyframes + helpers
# for animated flags (matched by anim_class).
FLAG_ANIM_CSS = """
@keyframes flg-pan { 0% { background-position: 0% 50%; } 100% { background-position: 200% 50%; } }
@keyframes flg-shimmer { 0% { background-position: -100% 50%; } 100% { background-position: 200% 50%; } }
.flag-anim-pan,.flag-anim-fast,.flag-anim-shimmer { background-size: 300% 100% !important; }
.flag-anim-pan     { animation: flg-pan 14s linear infinite; }
.flag-anim-fast    { animation: flg-pan 6s linear infinite; }
.flag-anim-shimmer { animation: flg-shimmer 5s ease-in-out infinite; }

/* Matrix rain flag — actual falling 1s and 0s (SVG layer scrolls down) */
@keyframes flg-matrix-rain {
  0%   { background-position: 0 0,    0 0; }
  100% { background-position: 0 120px, 0 0; }
}
.flag-anim-matrix {
  background-size: 84px 120px, 100% 100% !important;
  background-repeat: repeat, no-repeat !important;
  animation: flg-matrix-rain 2.4s linear infinite;
  filter: drop-shadow(0 0 4px rgba(74,222,128,.5));
}

/* Matrix Trail — same idea but rolling to the RIGHT */
@keyframes flg-matrix-rain-h {
  0%   { background-position: 0 0,        0 0, 0 0; }
  100% { background-position: 480px 0,    0 0, 0 0; }
}
.flag-anim-matrix-h {
  background-size: 48px 100%, 100% 11px, 100% 100% !important;
  animation: flg-matrix-rain-h 2.6s linear infinite;
  filter: drop-shadow(0 0 4px rgba(74,222,128,.5));
}

/* Racing Stripes (Anim) — diagonal stripes flying right */
@keyframes flg-racing { 0% { background-position: 0 0; } 100% { background-position: 56px 56px; } }
.flag-anim-racing {
  background-size: 28px 28px !important;
  animation: flg-racing .7s linear infinite;
}

/* Checker Flag (Anim) — checker rolling sideways */
@keyframes flg-checker { 0% { background-position: 0 0; } 100% { background-position: 48px 0; } }
.flag-anim-checker {
  animation: flg-checker .9s linear infinite;
}

/* Candy Pop (Anim) — pastel rainbow shimmer */
@keyframes flg-candy { 0% { background-position: 0% 50%; } 100% { background-position: 300% 50%; } }
.flag-anim-candy {
  background-size: 300% 100% !important;
  animation: flg-candy 6s linear infinite;
}
"""


# ── Focus-timer themes ─────────────────────────────────────────────────
# Visual-only backgrounds for the focus session screen. All free + selectable.
FOCUS_THEMES = {
    "default":   {"name": "Default",       "css": "linear-gradient(135deg,#0f172a,#1e293b)"},
    "rain":      {"name": "Rain Window",   "css": "radial-gradient(circle at 30% 20%, rgba(56,189,248,.25), transparent 40%), radial-gradient(circle at 70% 60%, rgba(99,102,241,.25), transparent 45%), linear-gradient(180deg,#0c1a2e,#020617)"},
    "fireplace": {"name": "Fireplace",     "css": "radial-gradient(circle at 50% 90%, #fbbf24 0%, #ef4444 18%, transparent 45%), radial-gradient(circle at 50% 100%, #fde047 0%, transparent 25%), linear-gradient(180deg,#1c0a04,#000)"},
    "library":   {"name": "Library",       "css": "repeating-linear-gradient(0deg, rgba(180,83,9,.12) 0 32px, transparent 32px 64px), linear-gradient(135deg,#3b2412,#1c1006)"},
    "cafe":      {"name": "Café",          "css": "radial-gradient(circle at 30% 30%, rgba(251,191,36,.15), transparent 40%), linear-gradient(135deg,#3f2a14,#1c120a)"},
    "forest":    {"name": "Misty Forest",  "css": "radial-gradient(circle at 50% 100%, rgba(34,197,94,.25), transparent 45%), linear-gradient(180deg,#022c22,#000)"},
    "ocean":     {"name": "Calm Ocean",    "css": "linear-gradient(180deg,#0c4a6e 0%,#082f49 50%,#020617 100%)"},
    "sunset":    {"name": "Lo-fi Sunset",  "css": "linear-gradient(180deg,#1e1b4b 0%,#7c3aed 35%,#ec4899 70%,#f59e0b 100%)"},
    "snow":      {"name": "Quiet Snow",    "css": "radial-gradient(2px 2px at 25% 30%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 75% 60%, #fff 50%, transparent 50%), radial-gradient(2px 2px at 50% 80%, #fff 50%, transparent 50%), linear-gradient(180deg,#1e293b,#020617)"},
    "stars":     {"name": "Starfield",     "css": "radial-gradient(2px 2px at 20% 30%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 80% 70%, #fff 50%, transparent 50%), radial-gradient(1px 1px at 50% 50%, #fff 50%, transparent 50%), linear-gradient(135deg,#000,#1e1b4b)"},
}

# ── Streak flame visuals ─────────────────────────────────────────────
# The character / span used next to the streak number. Stored as plain text;
# leaderboard/profile/dashboard read the user's selected_streak_flame.
STREAK_FLAMES = {
    "fire":      {"name": "Classic Fire",  "icon": "🔥"},
    "blue":      {"name": "Blue Flame",    "icon": "💙",  "css": "filter:hue-rotate(160deg) saturate(1.4);"},
    "green":     {"name": "Green Flame",   "icon": "🟢",  "css": "filter:hue-rotate(75deg) saturate(1.4);"},
    "lightning": {"name": "Lightning",     "icon": "⚡"},
    "snowflake": {"name": "Ironic Snow",   "icon": "❄️"},
    "comet":     {"name": "Comet",         "icon": "☄️"},
    "star":      {"name": "Shooting Star", "icon": "🌟"},
    "rocket":    {"name": "Rocket",        "icon": "🚀"},
    "diamond":   {"name": "Diamond",       "icon": "💎"},
}

# ── Quiz card themes ─────────────────────────────────────────────────
# Background applied to the quiz answering page. Visual only.
QUIZ_THEMES = {
    "default":   {"name": "Default",     "css": "var(--card)"},
    "paper":     {"name": "Paper",       "css": "linear-gradient(135deg,#fefce8,#fef3c7)", "text": "#1c1917"},
    "holo":      {"name": "Hologram",    "css": "conic-gradient(from 220deg at 50% 50%, #f0abfc, #67e8f9, #fde68a, #86efac, #c4b5fd, #f0abfc)", "text": "#000"},
    "dark":      {"name": "Pure Dark",   "css": "linear-gradient(135deg,#000,#0a0a0a)"},
    "blueprint": {"name": "Blueprint",   "css": "repeating-linear-gradient(0deg, rgba(255,255,255,.06) 0 1px, transparent 1px 22px), repeating-linear-gradient(90deg, rgba(255,255,255,.06) 0 1px, transparent 1px 22px), linear-gradient(135deg,#0c4a6e,#1e3a8a)"},
    "carbon":    {"name": "Carbon",      "css": "repeating-linear-gradient(45deg, #1f2937 0 6px, #111827 6px 12px)"},
    "mint":      {"name": "Mint",        "css": "linear-gradient(135deg,#a7f3d0,#6ee7b7)", "text": "#053b22"},
    "rose":      {"name": "Rose",        "css": "linear-gradient(135deg,#fda4af,#fbcfe8)", "text": "#5d0c1d"},
}

# ── Focus timer rings ────────────────────────────────────────────────
# CSS gradient applied to the conic timer ring. Stored as conic-gradient.
TIMER_RINGS = {
    "default":   {"name": "Default",   "css": "linear-gradient(135deg,#7c3aed,#3b82f6)"},
    "rainbow":   {"name": "Rainbow",   "css": "conic-gradient(from 0deg, #ef4444, #f59e0b, #fde047, #84cc16, #06b6d4, #6366f1, #a855f7, #ef4444)"},
    "fire":      {"name": "Fire",      "css": "conic-gradient(from 0deg, #fde047, #f97316, #dc2626, #fde047)"},
    "ice":       {"name": "Ice",       "css": "conic-gradient(from 0deg, #f0f9ff, #38bdf8, #0c4a6e, #f0f9ff)"},
    "neon":      {"name": "Neon",      "css": "conic-gradient(from 0deg, #ec4899, #8b5cf6, #06b6d4, #ec4899)"},
    "gold":      {"name": "Gold",      "css": "conic-gradient(from 0deg, #fde68a, #fbbf24, #b45309, #fde68a)"},
    "emerald":   {"name": "Emerald",   "css": "conic-gradient(from 0deg, #a7f3d0, #10b981, #064e3b, #a7f3d0)"},
    "amethyst":  {"name": "Amethyst",  "css": "conic-gradient(from 0deg, #e9d5ff, #a855f7, #4c1d95, #e9d5ff)"},
}

# ── Identity bundles ─────────────────────────────────────────────────
# Each bundle grants its banner + flag at a 25% discount vs buying separately.
# Price is computed dynamically from the included items.
BUNDLES = {
    "ocean": {
        "name": "Ocean Pack",
        "desc": "Ocean Wave banner + Tidal Pull flag — sail in style.",
        "banner": "ocean",
        "flag": "tide",
    },
    "space": {
        "name": "Space Pack",
        "desc": "Galaxy banner + Galactic Trail flag — interstellar drip.",
        "banner": "galaxy",
        "flag": "galaxy_bar",
    },
    "forest": {
        "name": "Forest Pack",
        "desc": "Forest banner + Verdant flag — deep-green focus.",
        "banner": "forest",
        "flag": "verdant",
    },
    "sunset": {
        "name": "Sunset Pack",
        "desc": "Sunset banner + Sunrise Streak flag — warm vibes.",
        "banner": "sunset",
        "flag": "sunrise",
    },
    "matrix": {
        "name": "Matrix Pack",
        "desc": "Matrix Rain banner + Matrix Trail flag — hacker mode.",
        "banner": "matrix",
        "flag": "matrix_bar",
    },
    "phoenix": {
        "name": "Phoenix Pack",
        "desc": "Phoenix banner + Phoenix Trail flag — rise from the ashes.",
        "banner": "phoenix",
        "flag": "phoenix_bar",
    },
    "iridescent": {
        "name": "Iridescent Pack",
        "desc": "Iridescent Pearl banner + Holographic flag (PLUS).",
        "banner": "iridescent",
        "flag": "plus_holo",
        "plus_only": True,
    },
}


def bundle_price(bundle_key: str) -> int:
    """Return the discounted price of a bundle (25% off the sum of items)."""
    cfg = BUNDLES.get(bundle_key)
    if not cfg:
        return 0
    bnr = BANNERS.get(cfg.get("banner", "")) or {}
    flg = FLAGS.get(cfg.get("flag", "")) or {}
    full = int(bnr.get("price_coins") or 0) + int(flg.get("price_coins") or 0)
    return max(1, int(round(full * 0.75)))


STREAK_FREEZE_PRICE = 10
STREAK_FREEZE_BUNDLE_QTY = 3
STREAK_FREEZE_BUNDLE_PRICE = 25  # vs. 30 if bought one-by-one (saves 5)

# ── Coin microtransactions ──────────────────────────────────────────
# Real-money packs that credit virtual coins. Prices are in USD; the actual
# payment processor (Lemon Squeezy one-time orders) is wired in routes.py.
COIN_PACKS = {
    "small":  {"name": "Pocket Pack",    "coins": 250,   "bonus": 0,    "price_usd": 0.99,  "tag": ""},
    "medium": {"name": "Stash Pack",     "coins": 800,   "bonus": 50,   "price_usd": 2.99,  "tag": "+6%"},
    "large":  {"name": "Vault Pack",     "coins": 2000,  "bonus": 300,  "price_usd": 6.99,  "tag": "+15%"},
    "mega":   {"name": "Treasury Pack",  "coins": 5500,  "bonus": 1000, "price_usd": 14.99, "tag": "+18% \u2022 BEST VALUE"},
    "ultra":  {"name": "Whale Pack",     "coins": 15000, "bonus": 4000, "price_usd": 34.99, "tag": "+27%"},
}

# Timed boosts. Each entry: (label, multiplier, hours, price_coins)
BOOSTS = {
}

def init_boosts_table() -> None:
    """Create the boosts table if missing."""
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_boosts ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL, "
        "multiplier REAL NOT NULL DEFAULT 2.0, "
        "expires_at TIMESTAMP NOT NULL, "
        "created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_boosts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL, "
        "multiplier REAL NOT NULL DEFAULT 2.0, "
        "expires_at TIMESTAMP NOT NULL, "
        "created_at TIMESTAMP DEFAULT (datetime('now','localtime')))",
    )


def get_active_boost(client_id: int, kind: str) -> float:
    """Return the highest active multiplier for `kind` ('xp' or 'coin'), or 1.0.
    Re-entrant safe: never raises (returns 1.0 on any failure)."""
    try:
        with get_db() as db:
            row = _fetchone(
                db,
                "SELECT MAX(multiplier) AS m FROM student_boosts "
                "WHERE client_id = %s AND kind = %s AND expires_at > %s",
                (client_id, kind, datetime.now()),
            )
            m = float((row or {}).get("m") or 1.0)
            return m if m >= 1.0 else 1.0
    except Exception:
        return 1.0


def get_active_boosts(client_id: int) -> list:
    """Return list of active boosts with kind, multiplier, expires_at (ISO)."""
    try:
        with get_db() as db:
            rows = _fetchall(
                db,
                "SELECT kind, multiplier, expires_at FROM student_boosts "
                "WHERE client_id = %s AND expires_at > %s ORDER BY expires_at DESC",
                (client_id, datetime.now()),
            ) or []
        out = []
        for r in rows:
            exp = r.get("expires_at")
            if hasattr(exp, "isoformat"):
                exp = exp.isoformat()
            out.append({"kind": r.get("kind"), "multiplier": float(r.get("multiplier") or 1.0), "expires_at": exp})
        return out
    except Exception:
        return []


def buy_boost(client_id: int, boost_key: str) -> dict:
    """Purchase a timed boost. Stacks duration if same kind already active
    (extends expiry by `hours`)."""
    cfg = BOOSTS.get(boost_key)
    if not cfg:
        return {"ok": False, "error": "Unknown boost."}
    cost = int(cfg["price_coins"])
    hours = int(cfg["hours"])
    mult = float(cfg["mult"])
    kind = cfg["kind"]
    from datetime import timedelta as _td
    with get_db() as db:
        _ensure_wallet(db, client_id)
        coins = int(_fetchval(db, "SELECT coins FROM student_wallet WHERE client_id = %s",
                              (client_id,)) or 0)
        if coins < cost:
            return {"ok": False, "error": "Not enough coins."}
        # If a same-kind boost is active, extend it; otherwise create new.
        existing = _fetchone(
            db,
            "SELECT id, expires_at FROM student_boosts "
            "WHERE client_id = %s AND kind = %s AND expires_at > %s "
            "ORDER BY expires_at DESC LIMIT 1",
            (client_id, kind, datetime.now()),
        )
        if existing:
            cur_exp = existing["expires_at"]
            if isinstance(cur_exp, str):
                try:
                    cur_exp = datetime.fromisoformat(cur_exp)
                except Exception:
                    cur_exp = datetime.now()
            new_exp = cur_exp + _td(hours=hours)
            _exec(db, "UPDATE student_boosts SET expires_at = %s, multiplier = %s WHERE id = %s",
                  (new_exp, mult, existing["id"]))
        else:
            new_exp = datetime.now() + _td(hours=hours)
            _exec(
                db,
                "INSERT INTO student_boosts (client_id, kind, multiplier, expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (client_id, kind, mult, new_exp),
            )
        _exec(db, "UPDATE student_wallet SET coins = coins - %s WHERE client_id = %s",
              (cost, client_id))
        new_coins = int(_fetchval(db, "SELECT coins FROM student_wallet WHERE client_id = %s",
                                  (client_id,)) or 0)
    return {"ok": True, "coins": new_coins, "boost": {"kind": kind, "multiplier": mult,
            "expires_at": new_exp.isoformat() if hasattr(new_exp, "isoformat") else str(new_exp)}}


def init_wallet_table() -> None:
    """Create the wallet table if missing (called from init_student_db)."""
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_wallet ("
        "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, "
        "coins INTEGER DEFAULT 0, "
        "streak_freezes INTEGER DEFAULT 0, "
        "selected_banner TEXT DEFAULT 'default', "
        "unlocked_banners TEXT DEFAULT '[\"default\"]', "
        "updated_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_wallet ("
        "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, "
        "coins INTEGER DEFAULT 0, "
        "streak_freezes INTEGER DEFAULT 0, "
        "selected_banner TEXT DEFAULT 'default', "
        "unlocked_banners TEXT DEFAULT '[\"default\"]', "
        "updated_at TIMESTAMP DEFAULT (datetime('now','localtime')))",
    )


def _ensure_wallet(db, client_id: int) -> None:
    row = _fetchone(db, "SELECT client_id FROM student_wallet WHERE client_id = %s", (client_id,))
    if not row:
        _exec(
            db,
            "INSERT INTO student_wallet (client_id, coins, streak_freezes, selected_banner, unlocked_banners) "
            "VALUES (%s, 0, 0, 'default', %s)",
            (client_id, '["default"]'),
        )


def _grant_weekly_free_freeze(db, client_id: int) -> None:
    """Once every 7 days, grant the user 1 free streak freeze (capped at 3 owned).
    Tracked via clients.mail_preferences JSON key `last_free_freeze_grant`.
    """
    try:
        from datetime import datetime, timedelta
        row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (client_id,))
        raw = (row or {}).get("mail_preferences") or ""
        try:
            prefs = _json.loads(raw) if raw else {}
            if not isinstance(prefs, dict):
                prefs = {}
        except Exception:
            prefs = {}
        last = prefs.get("last_free_freeze_grant")
        now = datetime.utcnow()
        due = True
        if last:
            try:
                due = (now - datetime.fromisoformat(last)) >= timedelta(days=7)
            except Exception:
                due = True
        if not due:
            return
        # Grant if under cap
        cur = _fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,)) or 0
        if int(cur) < 3:
            _exec(db, "UPDATE student_wallet SET streak_freezes = streak_freezes + 1 WHERE client_id = %s",
                  (client_id,))
        # Always update the timestamp so the cycle advances even if at cap.
        prefs["last_free_freeze_grant"] = now.isoformat()
        _exec(db, "UPDATE clients SET mail_preferences = %s WHERE id = %s",
              (_json.dumps(prefs), client_id))
    except Exception as e:
        log.exception("weekly free freeze grant failed: %s", e)


def get_wallet(client_id: int) -> dict:
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _grant_weekly_free_freeze(db, client_id)
        row = _fetchone(db, "SELECT * FROM student_wallet WHERE client_id = %s", (client_id,))
    try:
        unlocked = _json.loads(row.get("unlocked_banners") or '["default"]')
        if not isinstance(unlocked, list):
            unlocked = ["default"]
    except Exception:
        unlocked = ["default"]
    if "default" not in unlocked:
        unlocked.insert(0, "default")
    # Ultimate tier auto-unlocks every banner (incl. PLUS-only).
    try:
        from . import subscription as _sub
        if _sub.get_tier(client_id) == "ultimate":
            for k in BANNERS.keys():
                if k not in unlocked:
                    unlocked.append(k)
    except Exception:
        pass
    return {
        "coins":            int(row.get("coins") or 0),
        "streak_freezes":   int(row.get("streak_freezes") or 0),
        "selected_banner":  row.get("selected_banner") or "default",
        "unlocked_banners": unlocked,
    }


def add_coins(client_id: int, amount: int, _reason: str = "") -> int:
    """Add (or subtract) coins. Returns new balance.
    Positive amounts get multiplied by an active 2x-coin boost if any."""
    if amount == 0:
        return get_wallet(client_id)["coins"]
    if amount > 0:
        try:
            mult = get_active_boost(client_id, "coin")
            if mult and mult > 1.0:
                amount = int(round(amount * mult))
        except Exception:
            pass
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(db, "UPDATE student_wallet SET coins = coins + %s WHERE client_id = %s",
              (int(amount), client_id))
        return _fetchval(db, "SELECT coins FROM student_wallet WHERE client_id = %s", (client_id,)) or 0


def credit_coin_pack(client_id: int, pack_key: str) -> dict:
    """Credit coins for a real-money pack purchase. Bypasses 2x-coin boost
    (real-money purchases shouldn't double-stack with timed boosts)."""
    cfg = COIN_PACKS.get(pack_key)
    if not cfg:
        return {"ok": False, "error": "Unknown coin pack."}
    total = int(cfg["coins"]) + int(cfg.get("bonus") or 0)
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(db, "UPDATE student_wallet SET coins = coins + %s WHERE client_id = %s",
              (total, client_id))
        new_balance = _fetchval(db, "SELECT coins FROM student_wallet WHERE client_id = %s", (client_id,)) or 0
    return {"ok": True, "credited": total, "coins": new_balance, "pack": pack_key}


def buy_streak_freeze(client_id: int, qty: int = 1, bundle: bool = False) -> dict:
    """Spend coins to buy `qty` streak freezes (max 3 owned at once).
    If `bundle=True` and qty==STREAK_FREEZE_BUNDLE_QTY, use the discounted
    bundle price."""
    qty = max(1, min(3, int(qty)))
    if bundle and qty == STREAK_FREEZE_BUNDLE_QTY:
        cost = STREAK_FREEZE_BUNDLE_PRICE
    else:
        cost = STREAK_FREEZE_PRICE * qty
    with get_db() as db:
        _ensure_wallet(db, client_id)
        row = _fetchone(db, "SELECT coins, streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,))
        coins = int(row.get("coins") or 0)
        owned = int(row.get("streak_freezes") or 0)
        if owned + qty > 3:
            return {"ok": False, "error": "Max 3 freezes at a time."}
        if coins < cost:
            return {"ok": False, "error": "Not enough coins."}
        _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, streak_freezes = streak_freezes + %s WHERE client_id = %s",
            (cost, qty, client_id),
        )
        new = _fetchone(db, "SELECT coins, streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,))
    return {"ok": True, "coins": int(new.get("coins") or 0), "streak_freezes": int(new.get("streak_freezes") or 0)}


def _is_plus_user(client_id: int) -> bool:
    """True if user can access PLUS-only cosmetics."""
    try:
        from student.subscription import get_tier  # type: ignore
        return get_tier(client_id) in ("plus", "ultimate")
    except Exception:
        return False


def buy_banner(client_id: int, banner_key: str) -> dict:
    """Buy a banner. Requires both XP threshold and coins."""
    cfg = BANNERS.get(banner_key)
    if not cfg:
        return {"ok": False, "error": "Unknown banner."}
    if cfg.get("plus_only") and not _is_plus_user(client_id):
        return {"ok": False, "error": "Plus subscription required."}
    total_xp = get_total_xp(client_id)
    if total_xp < cfg["xp_required"]:
        return {"ok": False, "error": f"Need {cfg['xp_required']} XP to unlock this banner."}
    wallet = get_wallet(client_id)
    if banner_key in wallet["unlocked_banners"]:
        return {"ok": False, "error": "Already owned."}
    if wallet["coins"] < cfg["price_coins"]:
        return {"ok": False, "error": "Not enough coins."}
    new_unlocked = wallet["unlocked_banners"] + [banner_key]
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, unlocked_banners = %s WHERE client_id = %s",
            (cfg["price_coins"], _json.dumps(new_unlocked), client_id),
        )
    return {"ok": True, "coins": wallet["coins"] - cfg["price_coins"], "unlocked_banners": new_unlocked}


def set_selected_banner(client_id: int, banner_key: str) -> dict:
    wallet = get_wallet(client_id)
    if banner_key not in wallet["unlocked_banners"]:
        return {"ok": False, "error": "Banner not unlocked."}
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(db, "UPDATE student_wallet SET selected_banner = %s WHERE client_id = %s",
              (banner_key, client_id))
    return {"ok": True, "selected_banner": banner_key}


# ── Leaderboard flags ─────────────────────────────────────────────
# Flags are stored inside clients.mail_preferences JSON to avoid a schema
# migration. Keys: "selected_flag" (str), "unlocked_flags" (list).

def _load_flag_prefs(db, client_id: int) -> dict:
    row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (client_id,))
    raw = (row or {}).get("mail_preferences") or ""
    try:
        prefs = _json.loads(raw) if raw else {}
        if not isinstance(prefs, dict):
            prefs = {}
    except Exception:
        prefs = {}
    return prefs


def _save_flag_prefs(db, client_id: int, prefs: dict) -> None:
    _exec(db, "UPDATE clients SET mail_preferences = %s WHERE id = %s",
          (_json.dumps(prefs), client_id))


def get_flag_state(client_id: int) -> dict:
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
    unlocked = prefs.get("unlocked_flags") or ["none"]
    if not isinstance(unlocked, list):
        unlocked = ["none"]
    if "none" not in unlocked:
        unlocked = ["none"] + unlocked
    sel = prefs.get("selected_flag") or "none"
    if sel not in FLAGS:
        sel = "none"
    # Ultimate tier auto-unlocks every flag (incl. PLUS-only).
    try:
        from . import subscription as _sub
        if _sub.get_tier(client_id) == "ultimate":
            for k in FLAGS.keys():
                if k not in unlocked:
                    unlocked.append(k)
    except Exception:
        pass
    return {"selected_flag": sel, "unlocked_flags": unlocked}


def get_flags_for_clients(client_ids: list[int]) -> dict[int, dict]:
    """Return {client_id: {css, anim_class}} for each client in the list.
    Used by the leaderboard to render flags behind each row."""
    if not client_ids:
        return {}
    out: dict[int, dict] = {}
    placeholders = ",".join(["%s"] * len(client_ids))
    with get_db() as db:
        rows = _fetchall(
            db,
            f"SELECT id, mail_preferences FROM clients WHERE id IN ({placeholders})",
            tuple(client_ids),
        )
    for r in rows:
        try:
            raw = r.get("mail_preferences") or ""
            prefs = _json.loads(raw) if raw else {}
            sel = (prefs.get("selected_flag") or "none") if isinstance(prefs, dict) else "none"
        except Exception:
            sel = "none"
        cfg = FLAGS.get(sel) or FLAGS["none"]
        if sel != "none":
            out[int(r["id"])] = {
                "css": cfg["css"],
                "anim_class": (cfg.get("anim_class") or "") if cfg.get("animated") else "",
            }
    return out


def buy_flag(client_id: int, flag_key: str) -> dict:
    cfg = FLAGS.get(flag_key)
    if not cfg:
        return {"ok": False, "error": "Unknown flag."}
    if flag_key == "none":
        return {"ok": False, "error": "Already owned."}
    if cfg.get("plus_only") and not _is_plus_user(client_id):
        return {"ok": False, "error": "Plus subscription required."}
    total_xp = get_total_xp(client_id)
    if total_xp < cfg["xp_required"]:
        return {"ok": False, "error": f"Need {cfg['xp_required']} XP to unlock this flag."}
    state = get_flag_state(client_id)
    if flag_key in state["unlocked_flags"]:
        return {"ok": False, "error": "Already owned."}
    wallet = get_wallet(client_id)
    if wallet["coins"] < cfg["price_coins"]:
        return {"ok": False, "error": "Not enough coins."}
    new_unlocked = list(state["unlocked_flags"]) + [flag_key]
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(db, "UPDATE student_wallet SET coins = coins - %s WHERE client_id = %s",
              (cfg["price_coins"], client_id))
        prefs = _load_flag_prefs(db, client_id)
        prefs["unlocked_flags"] = new_unlocked
        _save_flag_prefs(db, client_id, prefs)
    return {"ok": True, "coins": wallet["coins"] - cfg["price_coins"], "unlocked_flags": new_unlocked}


def set_selected_flag(client_id: int, flag_key: str) -> dict:
    if flag_key not in FLAGS:
        return {"ok": False, "error": "Unknown flag."}
    state = get_flag_state(client_id)
    if flag_key not in state["unlocked_flags"] and flag_key != "none":
        return {"ok": False, "error": "Flag not unlocked."}
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
        prefs["selected_flag"] = flag_key
        _save_flag_prefs(db, client_id, prefs)
    return {"ok": True, "selected_flag": flag_key}


# ── Equipped leaderboard badge ────────────────────────────────────
# A single badge the user has earned can be shown next to their name on the
# leaderboard. Stored inside clients.mail_preferences JSON as 'equipped_badge'.

def _badge_slot_keys(prefs: dict) -> tuple[str, str]:
    """Return (left_key, right_key) honoring legacy single-slot 'equipped_badge'."""
    if not isinstance(prefs, dict):
        return "", ""
    left = prefs.get("equipped_badge_left") or ""
    right = prefs.get("equipped_badge_right") or ""
    legacy = prefs.get("equipped_badge") or ""
    # Legacy single-slot entries fall back to the right side.
    if not right and legacy:
        right = legacy
    if not isinstance(left, str): left = ""
    if not isinstance(right, str): right = ""
    if left and left not in BADGE_DEFS: left = ""
    if right and right not in BADGE_DEFS: right = ""
    return left, right


def get_equipped_badge(client_id: int, side: str = "right") -> str:
    """Return the badge key for the requested side ('left' or 'right')."""
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
    left, right = _badge_slot_keys(prefs)
    return left if str(side).lower() == "left" else right


def get_equipped_badges(client_id: int) -> dict:
    """Return both equipped slots."""
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
    left, right = _badge_slot_keys(prefs)
    return {"left": left, "right": right}


def set_equipped_badge(client_id: int, badge_key: str, side: str = "right") -> dict:
    """Equip a badge to one slot ('left' or 'right'). Empty string clears that slot.
    The same badge_key may be equipped to both sides simultaneously.
    User must already own the badge."""
    side = "left" if str(side).lower() == "left" else "right"
    if badge_key:
        if badge_key not in BADGE_DEFS:
            return {"ok": False, "error": "Unknown badge."}
        owned = {b["badge_key"] for b in get_badges(client_id)}
        if badge_key not in owned:
            return {"ok": False, "error": "Badge not earned."}
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
        # Migrate legacy single slot into right on first write
        if "equipped_badge" in prefs and "equipped_badge_right" not in prefs:
            prefs["equipped_badge_right"] = prefs.get("equipped_badge") or ""
        if side == "left":
            prefs["equipped_badge_left"] = badge_key or ""
        else:
            prefs["equipped_badge_right"] = badge_key or ""
            # Keep legacy field in sync for any old reader
            prefs["equipped_badge"] = badge_key or ""
        _save_flag_prefs(db, client_id, prefs)
    left, right = _badge_slot_keys(prefs)
    return {"ok": True, "equipped_badge_left": left, "equipped_badge_right": right}


def get_equipped_badges_for_clients(client_ids: list[int]) -> dict[int, dict]:
    """Bulk lookup for the leaderboard.
    Returns {client_id: {left:{emoji,name,key}|None, right:{emoji,name,key}|None}}."""
    if not client_ids:
        return {}
    out: dict[int, dict] = {}
    placeholders = ",".join(["%s"] * len(client_ids))
    with get_db() as db:
        rows = _fetchall(
            db,
            f"SELECT id, mail_preferences FROM clients WHERE id IN ({placeholders})",
            tuple(client_ids),
        )
    for r in rows:
        try:
            raw = r.get("mail_preferences") or ""
            prefs = _json.loads(raw) if raw else {}
        except Exception:
            prefs = {}
        left_key, right_key = _badge_slot_keys(prefs)
        entry = {"left": None, "right": None}
        if left_key:
            info = BADGE_DEFS[left_key]
            entry["left"] = {"emoji": info.get("emoji", ""), "name": info.get("name", ""), "key": left_key}
        if right_key:
            info = BADGE_DEFS[right_key]
            entry["right"] = {"emoji": info.get("emoji", ""), "name": info.get("name", ""), "key": right_key}
        if entry["left"] or entry["right"]:
            out[int(r["id"])] = entry
    return out


# ── Generic cosmetic selectors (focus theme, streak flame, quiz theme, timer ring) ──
_COSMETIC_KINDS = {
    "focus_theme":  ("FOCUS_THEMES",  "selected_focus_theme"),
    "streak_flame": ("STREAK_FLAMES", "selected_streak_flame"),
    "quiz_theme":   ("QUIZ_THEMES",   "selected_quiz_theme"),
    "timer_ring":   ("TIMER_RINGS",   "selected_timer_ring"),
}


def get_cosmetic_state(client_id: int) -> dict:
    """Return all selected cosmetic keys for the user (with defaults filled in)."""
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
    out = {}
    for kind, (catalog_name, pref_key) in _COSMETIC_KINDS.items():
        catalog = globals().get(catalog_name) or {}
        sel = prefs.get(pref_key) if isinstance(prefs, dict) else None
        if not sel or sel not in catalog:
            sel = "default" if "default" in catalog else (next(iter(catalog), "") if catalog else "")
        out[kind] = sel
    return out


def set_cosmetic(client_id: int, kind: str, key: str) -> dict:
    """Select a cosmetic. All catalog entries are free + always available."""
    if kind not in _COSMETIC_KINDS:
        return {"ok": False, "error": "Unknown cosmetic kind."}
    catalog_name, pref_key = _COSMETIC_KINDS[kind]
    catalog = globals().get(catalog_name) or {}
    if key not in catalog:
        return {"ok": False, "error": "Unknown option."}
    with get_db() as db:
        prefs = _load_flag_prefs(db, client_id)
        prefs[pref_key] = key
        _save_flag_prefs(db, client_id, prefs)
    return {"ok": True, "kind": kind, "key": key}


def buy_bundle(client_id: int, bundle_key: str) -> dict:
    """Atomic 25%-off purchase that grants the bundle's banner + flag.
    Already-owned items are skipped on the unlock side but the bundle price
    stays the same (the discount IS the value)."""
    cfg = BUNDLES.get(bundle_key)
    if not cfg:
        return {"ok": False, "error": "Unknown bundle."}
    if cfg.get("plus_only") and not _is_plus_user(client_id):
        return {"ok": False, "error": "Plus subscription required."}
    bnr_key = cfg.get("banner") or ""
    flg_key = cfg.get("flag") or ""
    if bnr_key not in BANNERS or flg_key not in FLAGS:
        return {"ok": False, "error": "Bundle misconfigured."}
    price = bundle_price(bundle_key)
    wallet = get_wallet(client_id)
    if wallet["coins"] < price:
        return {"ok": False, "error": f"Need {price} coins (have {wallet['coins']})."}
    flag_state = get_flag_state(client_id)
    new_banners = list(wallet["unlocked_banners"])
    if bnr_key not in new_banners:
        new_banners.append(bnr_key)
    new_flags = list(flag_state["unlocked_flags"])
    if flg_key not in new_flags:
        new_flags.append(flg_key)
    with get_db() as db:
        _ensure_wallet(db, client_id)
        _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, unlocked_banners = %s WHERE client_id = %s",
            (price, _json.dumps(new_banners), client_id),
        )
        prefs = _load_flag_prefs(db, client_id)
        prefs["unlocked_flags"] = new_flags
        _save_flag_prefs(db, client_id, prefs)
    return {
        "ok": True, "bundle": bundle_key, "spent": price,
        "coins": wallet["coins"] - price,
        "banner": bnr_key, "flag": flg_key,
        "banner_name": BANNERS[bnr_key].get("name", bnr_key),
        "flag_name": FLAGS[flg_key].get("name", flg_key),
        "unlocked_banners": new_banners, "unlocked_flags": new_flags,
    }


def use_streak_freeze(client_id: int) -> dict:
    """Consume one freeze and log a sentinel focus row for yesterday so the
    streak chain doesn't break. No-op if no freezes available or yesterday
    already has activity."""
    from datetime import timedelta as _td
    yesterday = (datetime.now().date() - _td(days=1)).strftime("%Y-%m-%d")
    with get_db() as db:
        _ensure_wallet(db, client_id)
        owned = _fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s",
                          (client_id,)) or 0
        if owned <= 0:
            return {"ok": False, "error": "No freezes."}
        existing = _fetchval(
            db,
            "SELECT id FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s AND focus_minutes > 0 LIMIT 1",
            (client_id, yesterday + "%"),
        )
        if existing:
            return {"ok": False, "error": "Yesterday already has activity."}
        # Insert a 1-minute sentinel session so the streak query sees yesterday.
        _insert_returning_id(
            db,
            "INSERT INTO student_study_progress (client_id, plan_date, completed, notes, focus_minutes, pages_read) "
            "VALUES (%s, %s, 1, %s, %s, %s) RETURNING id",
            (client_id, yesterday + " 12:00:00", "[streak freeze]", 1, 0),
            "INSERT INTO student_study_progress (client_id, plan_date, completed, notes, focus_minutes, pages_read) "
            "VALUES (?, ?, 1, ?, ?, ?)",
        )
        _exec(db, "UPDATE student_wallet SET streak_freezes = streak_freezes - 1 WHERE client_id = %s",
              (client_id,))
        new_owned = _fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s",
                              (client_id,)) or 0
    return {"ok": True, "streak_freezes": new_owned}


# ── Quiz Duels v2 (file-upload + AI-generated, synchronous) ─

# Lifecycle: pending -> ready -> playing -> settled
#                            \-> declined / expired / forfeit

QUIZ_DUEL_INVITE_TTL_MIN  = 10   # opponent must accept within 10 min
QUIZ_DUEL_PLAY_TTL_MIN    = 15   # match must be finished within 15 min of start
QUIZ_DUEL_QUESTION_COUNT  = 10
QUIZ_DUEL_WIN_COINS       = 50
QUIZ_DUEL_WIN_XP          = 5
QUIZ_DUEL_TIE_COINS       = 20
QUIZ_DUEL_TIE_XP          = 2
QUIZ_DUEL_DAILY_PAY_CAP   = 3    # rewards capped at N matches per opponent per day

# ── Study Marathon (7-day asynchronous duel) rewards ──
# Slightly higher than a quiz duel (50 coins / 5 XP) but not by much,
# because marathons run for a whole week and would otherwise dominate.
MARATHON_WIN_COINS  = 70
MARATHON_WIN_XP     = 8
MARATHON_TIE_COINS  = 25
MARATHON_TIE_XP     = 3


def init_quiz_duels_tables() -> None:
    """Create v2 quiz-duel tables. Called from init_student_db."""
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_quiz_duels ("
        "id SERIAL PRIMARY KEY, "
        "challenger_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "opponent_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "topic TEXT DEFAULT '', "
        "file_name TEXT DEFAULT '', "
        "questions_json TEXT NOT NULL DEFAULT '[]', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "challenger_score INTEGER NOT NULL DEFAULT 0, "
        "opponent_score INTEGER NOT NULL DEFAULT 0, "
        "challenger_time_ms INTEGER NOT NULL DEFAULT 0, "
        "opponent_time_ms INTEGER NOT NULL DEFAULT 0, "
        "challenger_done BOOLEAN NOT NULL DEFAULT FALSE, "
        "opponent_done BOOLEAN NOT NULL DEFAULT FALSE, "
        "winner_id INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "forfeit_by INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "accepted_at TIMESTAMP, "
        "settled_at TIMESTAMP, "
        "expires_at TIMESTAMP NOT NULL)",
        "CREATE TABLE IF NOT EXISTS student_quiz_duels ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "challenger_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "opponent_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "topic TEXT DEFAULT '', "
        "file_name TEXT DEFAULT '', "
        "questions_json TEXT NOT NULL DEFAULT '[]', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "challenger_score INTEGER NOT NULL DEFAULT 0, "
        "opponent_score INTEGER NOT NULL DEFAULT 0, "
        "challenger_time_ms INTEGER NOT NULL DEFAULT 0, "
        "opponent_time_ms INTEGER NOT NULL DEFAULT 0, "
        "challenger_done INTEGER NOT NULL DEFAULT 0, "
        "opponent_done INTEGER NOT NULL DEFAULT 0, "
        "winner_id INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "forfeit_by INTEGER REFERENCES clients(id) ON DELETE SET NULL, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "accepted_at TEXT, "
        "settled_at TEXT, "
        "expires_at TEXT NOT NULL)",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_quiz_duel_answers ("
        "id SERIAL PRIMARY KEY, "
        "duel_id INTEGER NOT NULL REFERENCES student_quiz_duels(id) ON DELETE CASCADE, "
        "client_id INTEGER NOT NULL, "
        "question_idx INTEGER NOT NULL, "
        "answer TEXT DEFAULT '', "
        "is_correct BOOLEAN NOT NULL DEFAULT FALSE, "
        "time_ms INTEGER NOT NULL DEFAULT 0, "
        "submitted_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(duel_id, client_id, question_idx))",
        "CREATE TABLE IF NOT EXISTS student_quiz_duel_answers ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "duel_id INTEGER NOT NULL REFERENCES student_quiz_duels(id) ON DELETE CASCADE, "
        "client_id INTEGER NOT NULL, "
        "question_idx INTEGER NOT NULL, "
        "answer TEXT DEFAULT '', "
        "is_correct INTEGER NOT NULL DEFAULT 0, "
        "time_ms INTEGER NOT NULL DEFAULT 0, "
        "submitted_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(duel_id, client_id, question_idx))",
    )


def _qd_row(duel_id: int) -> dict | None:
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT d.*, "
            "  cc.name AS challenger_name, "
            "  oc.name AS opponent_name "
            "FROM student_quiz_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE d.id = %s",
            (duel_id,),
        )
    return dict(row) if row else None


def create_quiz_duel(
    challenger_id: int,
    opponent_id: int,
    questions: list[dict],
    topic: str = "",
    file_name: str = "",
) -> int:
    """Persist a new pending quiz-duel with the AI-generated questions."""
    from datetime import datetime, timedelta
    if not questions:
        raise ValueError("No questions to store.")
    expires = datetime.now() + timedelta(minutes=QUIZ_DUEL_INVITE_TTL_MIN)
    payload = json.dumps(questions, ensure_ascii=False)
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_quiz_duels "
            "(challenger_id, opponent_id, topic, file_name, questions_json, status, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, 'pending', %s) RETURNING id",
            (challenger_id, opponent_id, topic[:200], file_name[:200], payload, expires),
            "INSERT INTO student_quiz_duels "
            "(challenger_id, opponent_id, topic, file_name, questions_json, status, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        )


def get_quiz_duel(duel_id: int, viewer_id: int | None = None) -> dict | None:
    """Return the full duel row + parsed questions. If viewer_id is given,
    redact the `correct` and `explanation` fields while the duel is still in
    progress to avoid leaking answers via the network tab."""
    d = _qd_row(duel_id)
    if not d:
        return None
    try:
        qs = json.loads(d.get("questions_json") or "[]")
    except Exception:
        qs = []
    settled = d.get("status") in ("settled", "tied", "forfeit", "expired", "declined")
    if viewer_id is not None and not settled:
        qs = [
            {k: v for k, v in q.items() if k not in ("correct", "explanation")}
            for q in qs
        ]
    d["questions"] = qs
    d.pop("questions_json", None)
    return d


def list_pending_quiz_duels_for(client_id: int) -> list[dict]:
    """Invites awaiting this user's accept/decline."""
    expire_pending_quiz_duels()
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT d.*, cc.name AS challenger_name, oc.name AS opponent_name "
            "FROM student_quiz_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE d.opponent_id = %s AND d.status = 'pending' "
            "ORDER BY d.created_at DESC",
            (client_id,),
        ) or []
    out = []
    for r in rows:
        d = dict(r)
        d.pop("questions_json", None)
        out.append(d)
    return out


def list_active_quiz_duels_for(client_id: int) -> list[dict]:
    """Duels the user is currently playing or waiting on."""
    expire_pending_quiz_duels()
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT d.*, cc.name AS challenger_name, oc.name AS opponent_name "
            "FROM student_quiz_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE (d.challenger_id = %s OR d.opponent_id = %s) "
            "AND d.status IN ('pending','ready','playing') "
            "ORDER BY d.created_at DESC",
            (client_id, client_id),
        ) or []
    out = []
    for r in rows:
        d = dict(r)
        d.pop("questions_json", None)
        out.append(d)
    return out


def expire_pending_quiz_duels() -> int:
    """Mark any pending invites whose TTL passed as 'expired'."""
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(
                "UPDATE student_quiz_duels SET status = 'expired', settled_at = NOW() "
                "WHERE status = 'pending' AND expires_at <= NOW()"
            )
            return cur.rowcount or 0
        else:
            cur = db.execute(
                "UPDATE student_quiz_duels SET status = 'expired', "
                "settled_at = datetime('now','localtime') "
                "WHERE status = 'pending' AND expires_at <= datetime('now','localtime')"
            )
            return cur.rowcount or 0


def accept_quiz_duel(duel_id: int, opponent_id: int) -> dict:
    """Opponent accepts -> match goes 'ready' (then 'playing' on first answer).
    Also extends expires_at to the play TTL."""
    from datetime import datetime, timedelta
    d = _qd_row(duel_id)
    if not d:
        return {"ok": False, "error": "Duel not found."}
    if d["opponent_id"] != opponent_id:
        return {"ok": False, "error": "Not your invite."}
    if d["status"] != "pending":
        return {"ok": False, "error": f"Duel is {d['status']}."}
    new_exp = datetime.now() + timedelta(minutes=QUIZ_DUEL_PLAY_TTL_MIN)
    with get_db() as db:
        if _USE_PG:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = 'ready', accepted_at = NOW(), "
                "expires_at = %s WHERE id = %s",
                (new_exp, duel_id),
            )
        else:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = 'ready', "
                "accepted_at = datetime('now','localtime'), expires_at = ? WHERE id = ?",
                (new_exp, duel_id),
            )
    return {"ok": True}


def decline_quiz_duel(duel_id: int, opponent_id: int) -> dict:
    d = _qd_row(duel_id)
    if not d:
        return {"ok": False, "error": "Duel not found."}
    if d["opponent_id"] != opponent_id:
        return {"ok": False, "error": "Not your invite."}
    if d["status"] != "pending":
        return {"ok": False, "error": f"Duel is {d['status']}."}
    with get_db() as db:
        if _USE_PG:
            _exec(db, "UPDATE student_quiz_duels SET status = 'declined', settled_at = NOW() WHERE id = %s", (duel_id,))
        else:
            _exec(db, "UPDATE student_quiz_duels SET status = 'declined', settled_at = datetime('now','localtime') WHERE id = ?", (duel_id,))
    return {"ok": True}


def submit_duel_answer(
    duel_id: int,
    client_id: int,
    question_idx: int,
    answer: str,
    time_ms: int,
) -> dict:
    """Record one answer. Once a player has answered all questions, mark them
    done; once both done (or a forfeit happens), settle the duel."""
    d = _qd_row(duel_id)
    if not d:
        return {"ok": False, "error": "Duel not found."}
    if client_id not in (d["challenger_id"], d["opponent_id"]):
        return {"ok": False, "error": "Not your duel."}
    if d["status"] not in ("ready", "playing"):
        return {"ok": False, "error": f"Duel is {d['status']}."}
    try:
        qs = json.loads(d.get("questions_json") or "[]")
    except Exception:
        qs = []
    if question_idx < 0 or question_idx >= len(qs):
        return {"ok": False, "error": "Bad question index."}
    correct_letter = (qs[question_idx].get("correct") or "").strip().lower()
    is_correct = bool(answer) and answer.strip().lower() == correct_letter
    is_chal = (client_id == d["challenger_id"])
    time_ms = max(0, min(int(time_ms or 0), 10 * 60 * 1000))

    with get_db() as db:
        # Insert (idempotent — UNIQUE on (duel,client,q_idx))
        try:
            _exec(
                db,
                "INSERT INTO student_quiz_duel_answers "
                "(duel_id, client_id, question_idx, answer, is_correct, time_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (duel_id, client_id, question_idx, (answer or "")[:8], is_correct, time_ms),
            )
        except Exception:
            # Already submitted — treat as no-op for that question
            return {"ok": True, "duplicate": True}

        # Recount score + total time for this player
        if is_chal:
            score = _fetchval(
                db,
                "SELECT COALESCE(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END), 0) "
                "FROM student_quiz_duel_answers WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            total_ms = _fetchval(
                db,
                "SELECT COALESCE(SUM(time_ms), 0) FROM student_quiz_duel_answers "
                "WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            count = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_quiz_duel_answers "
                "WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            done_flag = 1 if count >= len(qs) else 0
            if _USE_PG:
                _exec(
                    db,
                    "UPDATE student_quiz_duels SET status = 'playing', "
                    "challenger_score = %s, challenger_time_ms = %s, "
                    "challenger_done = %s WHERE id = %s",
                    (int(score), int(total_ms), bool(done_flag), duel_id),
                )
            else:
                _exec(
                    db,
                    "UPDATE student_quiz_duels SET status = 'playing', "
                    "challenger_score = ?, challenger_time_ms = ?, "
                    "challenger_done = ? WHERE id = ?",
                    (int(score), int(total_ms), int(done_flag), duel_id),
                )
        else:
            score = _fetchval(
                db,
                "SELECT COALESCE(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END), 0) "
                "FROM student_quiz_duel_answers WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            total_ms = _fetchval(
                db,
                "SELECT COALESCE(SUM(time_ms), 0) FROM student_quiz_duel_answers "
                "WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            count = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_quiz_duel_answers "
                "WHERE duel_id = %s AND client_id = %s",
                (duel_id, client_id),
            ) or 0
            done_flag = 1 if count >= len(qs) else 0
            if _USE_PG:
                _exec(
                    db,
                    "UPDATE student_quiz_duels SET status = 'playing', "
                    "opponent_score = %s, opponent_time_ms = %s, "
                    "opponent_done = %s WHERE id = %s",
                    (int(score), int(total_ms), bool(done_flag), duel_id),
                )
            else:
                _exec(
                    db,
                    "UPDATE student_quiz_duels SET status = 'playing', "
                    "opponent_score = ?, opponent_time_ms = ?, "
                    "opponent_done = ? WHERE id = ?",
                    (int(score), int(total_ms), int(done_flag), duel_id),
                )
    settle_quiz_duel_if_done(duel_id)
    return {"ok": True, "is_correct": is_correct}


def forfeit_quiz_duel(duel_id: int, loser_id: int, reason: str = "") -> dict:
    """Instant loss — used by tab-switch anti-cheat."""
    d = _qd_row(duel_id)
    if not d:
        return {"ok": False, "error": "Duel not found."}
    if loser_id not in (d["challenger_id"], d["opponent_id"]):
        return {"ok": False, "error": "Not your duel."}
    if d["status"] in ("settled", "tied", "forfeit", "declined", "expired"):
        return {"ok": True, "already": True}
    winner = d["opponent_id"] if loser_id == d["challenger_id"] else d["challenger_id"]
    with get_db() as db:
        if _USE_PG:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = 'forfeit', winner_id = %s, "
                "forfeit_by = %s, settled_at = NOW() WHERE id = %s",
                (winner, loser_id, duel_id),
            )
        else:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = 'forfeit', winner_id = ?, "
                "forfeit_by = ?, settled_at = datetime('now','localtime') WHERE id = ?",
                (winner, loser_id, duel_id),
            )
    _payout_quiz_duel(duel_id, winner, tied=False, reason=reason or "forfeit")
    return {"ok": True, "winner_id": winner}


def settle_quiz_duel_if_done(duel_id: int) -> dict:
    """If both players are done, decide the winner and pay out."""
    d = _qd_row(duel_id)
    if not d:
        return {"ok": False, "error": "Duel not found."}
    if d["status"] in ("settled", "tied", "forfeit", "declined", "expired"):
        return {"ok": True, "already": True}
    if not (d.get("challenger_done") and d.get("opponent_done")):
        return {"ok": True, "waiting": True}
    cs = int(d.get("challenger_score") or 0)
    os_ = int(d.get("opponent_score") or 0)
    ct = int(d.get("challenger_time_ms") or 0)
    ot = int(d.get("opponent_time_ms") or 0)
    if cs > os_:
        winner = d["challenger_id"]; status = "settled"; tied = False
    elif os_ > cs:
        winner = d["opponent_id"]; status = "settled"; tied = False
    else:
        # Tiebreaker: faster total time wins
        if ct < ot and ct > 0:
            winner = d["challenger_id"]; status = "settled"; tied = False
        elif ot < ct and ot > 0:
            winner = d["opponent_id"]; status = "settled"; tied = False
        else:
            winner = None; status = "tied"; tied = True
    with get_db() as db:
        if _USE_PG:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = %s, winner_id = %s, settled_at = NOW() WHERE id = %s",
                (status, winner, duel_id),
            )
        else:
            _exec(
                db,
                "UPDATE student_quiz_duels SET status = ?, winner_id = ?, "
                "settled_at = datetime('now','localtime') WHERE id = ?",
                (status, winner, duel_id),
            )
    _payout_quiz_duel(duel_id, winner, tied=tied)
    return {"ok": True, "winner_id": winner, "tied": tied}


def _count_paid_quiz_duels_today(winner_id: int, opponent_id: int) -> int:
    """How many already-paid quiz-duel wins/ties this user has racked up
    against this specific opponent today (for anti-farm cap)."""
    with get_db() as db:
        if _USE_PG:
            v = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_quiz_duels "
                "WHERE settled_at::date = CURRENT_DATE "
                "AND status IN ('settled','tied','forfeit') "
                "AND ((challenger_id = %s AND opponent_id = %s) "
                "  OR (challenger_id = %s AND opponent_id = %s))",
                (winner_id, opponent_id, opponent_id, winner_id),
            )
        else:
            v = _fetchval(
                db,
                "SELECT COUNT(*) FROM student_quiz_duels "
                "WHERE substr(settled_at,1,10) = date('now','localtime') "
                "AND status IN ('settled','tied','forfeit') "
                "AND ((challenger_id = ? AND opponent_id = ?) "
                "  OR (challenger_id = ? AND opponent_id = ?))",
                (winner_id, opponent_id, opponent_id, winner_id),
            )
    try:
        return int(v or 0)
    except Exception:
        return 0


def _payout_quiz_duel(duel_id: int, winner_id: int | None, tied: bool, reason: str = "") -> None:
    """Apply XP + coin rewards with the per-friend per-day cap."""
    d = _qd_row(duel_id)
    if not d:
        return
    a, b = d["challenger_id"], d["opponent_id"]
    paid_a = _count_paid_quiz_duels_today(a, b)
    # The current match itself has already been counted (settled_at was set
    # before payout), so cap is "<= cap" inclusive.
    over_cap = paid_a > QUIZ_DUEL_DAILY_PAY_CAP
    if over_cap:
        log.info("Quiz-duel %s skipped payout (anti-farm cap, %s vs %s)", duel_id, a, b)
        return
    if tied:
        try:
            add_coins(a, QUIZ_DUEL_TIE_COINS, "quiz_duel_tie")
            add_coins(b, QUIZ_DUEL_TIE_COINS, "quiz_duel_tie")
            award_xp(a, "quiz_duel_tie", QUIZ_DUEL_TIE_XP, f"duel {duel_id}")
            award_xp(b, "quiz_duel_tie", QUIZ_DUEL_TIE_XP, f"duel {duel_id}")
        except Exception as e:
            log.exception("tie payout failed: %s", e)
    elif winner_id:
        try:
            add_coins(winner_id, QUIZ_DUEL_WIN_COINS, f"quiz_duel_win {reason}".strip())
            award_xp(winner_id, "quiz_duel_win", QUIZ_DUEL_WIN_XP, f"duel {duel_id}")
        except Exception as e:
            log.exception("win payout failed: %s", e)


def get_quiz_duel_history(client_id: int, limit: int = 20) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT d.*, cc.name AS challenger_name, oc.name AS opponent_name "
            "FROM student_quiz_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE (d.challenger_id = %s OR d.opponent_id = %s) "
            "AND d.status IN ('settled','tied','forfeit','declined','expired') "
            "ORDER BY d.settled_at DESC NULLS LAST LIMIT %s"
            if _USE_PG else
            "SELECT d.*, cc.name AS challenger_name, oc.name AS opponent_name "
            "FROM student_quiz_duels d "
            "JOIN clients cc ON cc.id = d.challenger_id "
            "JOIN clients oc ON oc.id = d.opponent_id "
            "WHERE (d.challenger_id = ? OR d.opponent_id = ?) "
            "AND d.status IN ('settled','tied','forfeit','declined','expired') "
            "ORDER BY d.settled_at DESC LIMIT ?",
            (client_id, client_id, limit),
        ) or []
    out = []
    for r in rows:
        d = dict(r)
        d.pop("questions_json", None)
        out.append(d)
    return out
