"""
Student DB operations — extends MachReach's database with student-specific
tables for Canvas integration, course analysis, and study plans.
"""
from __future__ import annotations

import json
import json as _json
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from datetime import UTC
from zoneinfo import ZoneInfo

from machreach_core.db import (
    _exec,
    _fetchall,
    _fetchone,
    _fetchval,
    _insert_returning_id,
    _is_duplicate_column_error,
    _record_schema_version,
    _USE_PG,
    get_db,
)
from student.periods import get_user_timezone, local_now, user_date, utc_now

log = logging.getLogger(__name__)
STUDENT_SCHEMA_VERSION = 5


# ── Schema (appended to MachReach's init_db) ────────────────

STUDENT_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_courses (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_course_id INTEGER NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT DEFAULT '',
    term            TEXT DEFAULT '',
    analysis_json   TEXT DEFAULT '{}',
    last_synced     TIMESTAMP,
    canvas_active   BOOLEAN NOT NULL DEFAULT TRUE,
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
    grade           REAL,
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

CREATE TABLE IF NOT EXISTS student_focus_phases (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    phase_id        TEXT NOT NULL,
    mode            TEXT DEFAULT 'pomodoro',
    expected_minutes INTEGER DEFAULT 0,
    course_id       INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    exam_id         INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    started_at      TIMESTAMP DEFAULT NOW(),
    claimed_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, phase_id)
);
CREATE INDEX IF NOT EXISTS idx_focus_phases_client ON student_focus_phases(client_id, phase_id);

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

CREATE TABLE IF NOT EXISTS student_course_reviews (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    university      TEXT DEFAULT '',
    major           TEXT DEFAULT '',
    course_name     TEXT NOT NULL,
    course_code     TEXT DEFAULT '',
    difficulty_rating INTEGER NOT NULL,
    quality_rating    INTEGER NOT NULL,
    review_text     TEXT DEFAULT '',
    final_grade     REAL,
    total_hours     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_course_reviews_lookup ON student_course_reviews(university, major, course_name);

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
    timezone    TEXT DEFAULT 'America/Santiago',
    UNIQUE(client_id)
);
"""

STUDENT_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_courses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canvas_course_id INTEGER NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT DEFAULT '',
    term            TEXT DEFAULT '',
    analysis_json   TEXT DEFAULT '{}',
    last_synced     TEXT,
    canvas_active   INTEGER NOT NULL DEFAULT 1,
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
    grade           REAL,
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

CREATE TABLE IF NOT EXISTS student_focus_phases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    phase_id        TEXT NOT NULL,
    mode            TEXT DEFAULT 'pomodoro',
    expected_minutes INTEGER DEFAULT 0,
    course_id       INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    exam_id         INTEGER REFERENCES student_exams(id) ON DELETE SET NULL,
    started_at      TEXT DEFAULT (datetime('now', 'localtime')),
    claimed_at      TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(client_id, phase_id)
);
CREATE INDEX IF NOT EXISTS idx_focus_phases_client ON student_focus_phases(client_id, phase_id);

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

CREATE TABLE IF NOT EXISTS student_course_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    course_id       INTEGER REFERENCES student_courses(id) ON DELETE SET NULL,
    university      TEXT DEFAULT '',
    major           TEXT DEFAULT '',
    course_name     TEXT NOT NULL,
    course_code     TEXT DEFAULT '',
    difficulty_rating INTEGER NOT NULL,
    quality_rating    INTEGER NOT NULL,
    review_text     TEXT DEFAULT '',
    final_grade     REAL,
    total_hours     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(client_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_course_reviews_lookup ON student_course_reviews(university, major, course_name);

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
    timezone    TEXT DEFAULT 'America/Santiago',
    UNIQUE(client_id)
);
"""


# ── init ────────────────────────────────────────────────────

def init_student_db():
    """Create student tables. Called alongside MachReach's init_db()."""
    with get_db() as db:
        previous_schema_version = int(
            _fetchval(
                db,
                "SELECT version FROM schema_metadata WHERE component = %s",
                ("student",),
            )
            or 0
        )
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(STUDENT_PG_SCHEMA)
        else:
            db.executescript(STUDENT_SQLITE_SCHEMA)
    # Migrations
    _student_migrations()
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_email_prefs SET timezone = 'America/Santiago' "
            "WHERE timezone IS NULL OR timezone = '' OR timezone = 'America/Mexico_City'",
        )
    # Wallet (coins, freezes, banners)
    try:
        init_wallet_table()
    except Exception as e:
        log.exception("init_wallet_table failed: %s", e)
    # Real-money coin purchases; keeps Lemon Squeezy retries idempotent.
    try:
        init_coin_pack_orders_table()
    except Exception as e:
        log.exception("init_coin_pack_orders_table failed: %s", e)
    # Timed boosts (2x XP, 2x coins)
    try:
        init_boosts_table()
    except Exception as e:
        log.exception("init_boosts_table failed: %s", e)
    # Planner per-block completion marks
    try:
        init_planner_done_table()
    except Exception as e:
        log.exception("init_planner_done_table failed: %s", e)
    # Academic columns must exist before the course-catalog backfill reads
    # clients.university_id on a fresh install.
    try:
        from student.academic import init_academic_db
        init_academic_db()
    except Exception as e:
        log.exception("init_academic_db failed: %s", e)
    # Crowd-sourced per-university course catalog (autofill)
    try:
        init_course_catalog_table()
    except Exception as e:
        log.exception("init_course_catalog_table failed: %s", e)
    # Sweep every previously-added course into the catalog so autofill reflects
    # the whole user base per university, not just recently-added courses.
    try:
        _n = backfill_course_catalog()
        if _n:
            log.info("course catalog backfill added %s entr%s", _n, "y" if _n == 1 else "ies")
    except Exception as e:
        log.exception("backfill_course_catalog failed: %s", e)
    init_normalized_student_state(
        run_backfill=previous_schema_version < STUDENT_SCHEMA_VERSION
    )
    from student.ai_usage import init_schema as init_ai_usage_schema
    init_ai_usage_schema()
    # Removed market feature: new installs should not create or expose its tables.
    # Weekly/monthly leaderboard prize tables.
    from student.leaderboard_prizes import init_prize_tables
    init_prize_tables()
    # Never advertise a current student schema after a partial initializer.
    with get_db() as db:
        _exec(db, "SELECT client_id, semester_label, canvas_active FROM student_courses LIMIT 0")
        _exec(db, "SELECT client_id, exam_id FROM student_course_files LIMIT 0")
        _exec(db, "SELECT client_id, coins FROM student_wallet LIMIT 0")
        _exec(db, "SELECT coin_debt FROM student_wallet LIMIT 0")
        _exec(
            db,
            "SELECT period_kind, period_key, status, claim_token, claimed_at, completed_at "
            "FROM student_lb_payout_run LIMIT 0",
        )
        _exec(
            db,
            "SELECT period_kind, next_period_key FROM student_lb_payout_cursor LIMIT 0",
        )
        _exec(
            db,
            "SELECT event_key, client_id, status, attempts, claimed_at, "
            "next_attempt_at, sent_at "
            "FROM student_lb_email_outbox LIMIT 0",
        )
        _exec(
            db,
            "SELECT coins_reversed, refunded_amount, provider_total, refunded_at "
            "FROM student_coin_pack_orders LIMIT 0",
        )
        _exec(db, "SELECT id, university_id FROM clients LIMIT 0")
        _exec(db, "SELECT client_id, phase_id FROM student_focus_phases LIMIT 0")
        _exec(db, "SELECT client_id, course_name FROM student_course_reviews LIMIT 0")
        _exec(
            db,
            "SELECT client_id, tier, status, updated_at "
            "FROM student_subscription_state LIMIT 0",
        )
        _exec(
            db,
            "SELECT request_key, client_id, feature, status, reserved_at_utc "
            "FROM student_ai_usage LIMIT 0",
        )
        _exec(
            db,
            "SELECT university_id, canonical_course_id, cohort_version "
            "FROM student_course_outcomes LIMIT 0",
        )
    _record_schema_version("student", STUDENT_SCHEMA_VERSION)
    log.info("Student tables initialized.")


def _create_table_safe(pg_sql: str, sqlite_sql: str):
    """Create a table if it doesn't exist (safe for migrations)."""
    with get_db() as db:
        if _USE_PG:
            db.cursor().execute(pg_sql)
        else:
            db.execute(sqlite_sql)


def init_normalized_student_state(*, run_backfill: bool = True) -> None:
    """Create normalized account-domain state and backfill legacy records."""
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_subscription_state (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            tier TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'inactive',
            ls_sub_id TEXT,
            since_at TIMESTAMPTZ,
            ends_at TIMESTAMPTZ,
            renews_at TIMESTAMPTZ,
            quota_period_start TIMESTAMPTZ,
            quota_reset_at TIMESTAMPTZ,
            past_due_since TIMESTAMPTZ,
            update_payment_method_url TEXT DEFAULT '',
            customer_portal_url TEXT DEFAULT '',
            provider_event_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS student_subscription_state (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            tier TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'inactive',
            ls_sub_id TEXT,
            since_at TEXT,
            ends_at TEXT,
            renews_at TEXT,
            quota_period_start TEXT,
            quota_reset_at TEXT,
            past_due_since TEXT,
            update_payment_method_url TEXT DEFAULT '',
            customer_portal_url TEXT DEFAULT '',
            provider_event_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    )
    try:
        with get_db() as db:
            _exec(
                db,
                "ALTER TABLE student_subscription_state ADD COLUMN provider_event_at "
                + ("TIMESTAMPTZ" if _USE_PG else "TEXT"),
            )
    except Exception as exc:
        if not _is_duplicate_column_error(exc):
            raise
    _create_table_safe(
        """CREATE INDEX IF NOT EXISTS idx_student_subscription_provider_lookup
           ON student_subscription_state(ls_sub_id)""",
        """CREATE INDEX IF NOT EXISTS idx_student_subscription_provider_lookup
           ON student_subscription_state(ls_sub_id)""",
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_subscription_state SET ls_sub_id = NULL "
            "WHERE TRIM(COALESCE(ls_sub_id, '')) = ''",
        )
        _exec(db, "DROP INDEX IF EXISTS uq_student_subscription_provider")
        _exec(
            db,
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_student_subscription_provider_nonblank "
            "ON student_subscription_state(ls_sub_id) "
            "WHERE NULLIF(TRIM(ls_sub_id), '') IS NOT NULL",
        )
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_promotional_entitlements (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            plus_until TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS student_promotional_entitlements (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            plus_until TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    )
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_reward_state (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            weekly_freeze_granted_at TIMESTAMPTZ,
            plus_insurance_period_key TEXT DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS student_reward_state (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            weekly_freeze_granted_at TEXT,
            plus_insurance_period_key TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    )
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_pending_referrals (
            referred_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            code TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS student_pending_referrals (
            referred_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    )

    for column, definition in (
        ("university_id", "INTEGER"),
        ("canonical_course_id", "INTEGER"),
        ("cohort_version", "TEXT DEFAULT ''"),
    ):
        try:
            with get_db() as db:
                _exec(
                    db,
                    f"ALTER TABLE student_course_outcomes ADD COLUMN {column} {definition}",
                )
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    for table, column, definition in (
        ("student_xp", "occurred_at_utc", "TIMESTAMPTZ"),
        ("student_study_progress", "recorded_at_utc", "TIMESTAMPTZ"),
        ("student_focus_phases", "started_at_utc", "TIMESTAMPTZ"),
        ("student_focus_phases", "claimed_at_utc", "TIMESTAMPTZ"),
    ):
        try:
            with get_db() as db:
                sqlite_definition = "TEXT" if not _USE_PG else definition
                _exec(
                    db,
                    f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_definition}",
                )
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    _create_table_safe(
        """CREATE INDEX IF NOT EXISTS idx_course_outcomes_benchmark
           ON student_course_outcomes(
               university_id, canonical_course_id, cohort_version, passed
           )""",
        """CREATE INDEX IF NOT EXISTS idx_course_outcomes_benchmark
           ON student_course_outcomes(
               university_id, canonical_course_id, cohort_version, passed
           )""",
    )
    if run_backfill:
        _backfill_normalized_student_state()


def _backfill_normalized_student_state() -> None:
    """Idempotently copy legacy JSON and course identity into normalized rows."""
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        # Timezone must be normalized before interpreting legacy naive wall times.
        legacy_clients = _fetchall(db, "SELECT id, mail_preferences FROM clients")
        for legacy_client in legacy_clients:
            try:
                legacy_prefs = _json.loads(
                    legacy_client.get("mail_preferences") or "{}"
                )
            except (TypeError, ValueError):
                continue
            timezone_name = (
                legacy_prefs.get("timezone")
                if isinstance(legacy_prefs, dict)
                else None
            )
            if timezone_name:
                from student.periods import normalize_timezone
                _exec(
                    db,
                    "INSERT INTO student_email_prefs (client_id, timezone) "
                    "VALUES (%s, %s) ON CONFLICT(client_id) DO NOTHING",
                    (
                        int(legacy_client["id"]),
                        normalize_timezone(timezone_name),
                    ),
                )
        if _USE_PG:
            _exec(
                db,
                "UPDATE student_xp SET occurred_at_utc = "
                "created_at AT TIME ZONE 'UTC' WHERE occurred_at_utc IS NULL",
            )
            _exec(
                db,
                "UPDATE student_study_progress SET recorded_at_utc = "
                "created_at AT TIME ZONE 'UTC' WHERE recorded_at_utc IS NULL",
            )
            _exec(
                db,
                "UPDATE student_focus_phases SET started_at_utc = "
                "started_at AT TIME ZONE 'UTC' WHERE started_at_utc IS NULL",
            )
            _exec(
                db,
                "UPDATE student_focus_phases SET claimed_at_utc = "
                "claimed_at AT TIME ZONE 'UTC' "
                "WHERE claimed_at_utc IS NULL AND claimed_at IS NOT NULL",
            )
        else:
            for table, source, target in (
                ("student_xp", "created_at", "occurred_at_utc"),
                ("student_study_progress", "created_at", "recorded_at_utc"),
                ("student_focus_phases", "started_at", "started_at_utc"),
                ("student_focus_phases", "claimed_at", "claimed_at_utc"),
            ):
                legacy_rows = _fetchall(
                    db,
                    f"SELECT id, client_id, {source} AS legacy_time FROM {table} "
                    f"WHERE {target} IS NULL AND {source} IS NOT NULL",
                )
                for legacy_row in legacy_rows:
                    converted = _legacy_user_wall_time_to_utc(
                        legacy_row.get("legacy_time"),
                        int(legacy_row["client_id"]),
                        db,
                    )
                    if converted:
                        _exec(
                            db,
                            f"UPDATE {table} SET {target} = %s WHERE id = %s",
                            (converted.isoformat(), legacy_row["id"]),
                        )
        rows = _fetchall(
            db,
            "SELECT id, mail_preferences FROM clients"
            + (" FOR UPDATE" if _USE_PG else ""),
        )
        for row in rows:
            try:
                prefs = _json.loads(row.get("mail_preferences") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(prefs, dict):
                continue
            client_id = int(row["id"])
            timezone_name = prefs.get("timezone")
            if timezone_name:
                from student.periods import normalize_timezone
                _exec(
                    db,
                    "INSERT INTO student_email_prefs (client_id, timezone) "
                    "VALUES (%s, %s) ON CONFLICT(client_id) DO NOTHING",
                    (client_id, normalize_timezone(timezone_name)),
                )
            subscription = prefs.get("subscription")
            if isinstance(subscription, dict):
                _upsert_subscription_backfill(db, client_id, subscription)
            plus_until = prefs.get("plus_until")
            if plus_until:
                if _USE_PG:
                    _exec(
                        db,
                        "INSERT INTO student_promotional_entitlements "
                        "(client_id, plus_until) VALUES (%s, %s) "
                        "ON CONFLICT(client_id) DO NOTHING",
                        (client_id, plus_until),
                    )
                else:
                    _exec(
                        db,
                        "INSERT OR IGNORE INTO student_promotional_entitlements "
                        "(client_id, plus_until) VALUES (%s, %s)",
                        (client_id, plus_until),
                    )
            pending_ref = str(prefs.get("pending_ref") or "").strip()
            if pending_ref:
                if _USE_PG:
                    _exec(
                        db,
                        "INSERT INTO student_pending_referrals (referred_id, code) "
                        "VALUES (%s, %s) ON CONFLICT(referred_id) DO NOTHING",
                        (client_id, pending_ref),
                    )
                else:
                    _exec(
                        db,
                        "INSERT OR IGNORE INTO student_pending_referrals "
                        "(referred_id, code) VALUES (%s, %s)",
                        (client_id, pending_ref),
                    )
            last_freeze = _legacy_user_wall_time_to_utc(
                prefs.get("last_free_freeze_grant"), client_id, db
            )
            insurance_period = str(
                prefs.get("plus_streak_insurance_month") or ""
            ).strip()
            if last_freeze or insurance_period:
                _exec(
                    db,
                    "INSERT INTO student_reward_state "
                    "(client_id, weekly_freeze_granted_at, "
                    "plus_insurance_period_key, updated_at) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT(client_id) DO UPDATE SET "
                    "weekly_freeze_granted_at=COALESCE("
                    "student_reward_state.weekly_freeze_granted_at, "
                    "excluded.weekly_freeze_granted_at), "
                    "plus_insurance_period_key=CASE WHEN COALESCE("
                    "student_reward_state.plus_insurance_period_key, '') = '' "
                    "THEN excluded.plus_insurance_period_key ELSE "
                    "student_reward_state.plus_insurance_period_key END, "
                    "updated_at=excluded.updated_at",
                    (
                        client_id,
                        last_freeze.isoformat() if last_freeze else None,
                        insurance_period,
                        utc_now().isoformat(),
                    ),
                )
            cleaned = {
                key: value
                for key, value in prefs.items()
                if key
                not in {
                    "subscription",
                    "plus_until",
                    "pending_ref",
                    "last_free_freeze_grant",
                    "plus_streak_insurance_month",
                    "timezone",
                }
            }
            if cleaned != prefs:
                _exec(
                    db,
                    "UPDATE clients SET mail_preferences = %s WHERE id = %s",
                    (_json.dumps(cleaned), client_id),
                )
        _exec(
            db,
            "UPDATE student_course_outcomes SET "
            "university_id = (SELECT c.university_id FROM clients c "
            "WHERE c.id = student_course_outcomes.client_id), "
            "canonical_course_id = (SELECT sc.catalog_id FROM student_courses sc "
            "WHERE sc.id = student_course_outcomes.course_id), "
            "cohort_version = COALESCE((SELECT NULLIF(sc.term, '') "
            "FROM student_courses sc WHERE sc.id = student_course_outcomes.course_id), "
            "(SELECT NULLIF(sc.semester_label, '') FROM student_courses sc "
            "WHERE sc.id = student_course_outcomes.course_id), 'unknown') "
            "WHERE university_id IS NULL OR canonical_course_id IS NULL "
            "OR cohort_version IS NULL OR cohort_version = ''",
        )
        missing = _fetchall(
            db,
            "SELECT o.id, o.course_id, o.client_id, sc.code, sc.name, "
            "c.university_id, sc.term, sc.semester_label "
            "FROM student_course_outcomes o "
            "JOIN student_courses sc ON sc.id = o.course_id "
            "JOIN clients c ON c.id = o.client_id "
            "WHERE o.canonical_course_id IS NULL AND c.university_id IS NOT NULL",
        )
        for outcome in missing:
            canonical = _fetchone(
                db,
                "SELECT id FROM student_course_catalog "
                "WHERE university_id = %s AND code_norm = %s "
                "AND name_norm = %s AND deleted_at IS NULL LIMIT 1",
                (
                    outcome["university_id"],
                    _catalog_norm(outcome.get("code") or ""),
                    _catalog_norm(outcome.get("name") or ""),
                ),
            )
            if not canonical:
                continue
            canonical_id = int(canonical["id"])
            _exec(
                db,
                "UPDATE student_courses SET catalog_id = %s "
                "WHERE id = %s AND catalog_id IS NULL",
                (canonical_id, outcome["course_id"]),
            )
            _exec(
                db,
                "UPDATE student_course_outcomes SET university_id = %s, "
                "canonical_course_id = %s, cohort_version = %s WHERE id = %s",
                (
                    outcome["university_id"],
                    canonical_id,
                    outcome.get("term")
                    or outcome.get("semester_label")
                    or "unknown",
                    outcome["id"],
                ),
            )


def _legacy_user_wall_time_to_utc(value, client_id: int, db) -> datetime | None:
    if not value:
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        if parsed.tzinfo is None:
            # SQLite legacy defaults used server-local wall time, not browser
            # or user-local time. The historical app/runtime timezone is Chile.
            parsed = parsed.replace(tzinfo=ZoneInfo("America/Santiago"))
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _upsert_subscription_backfill(db, client_id: int, subscription: dict) -> None:
    values = (
        client_id,
        subscription.get("tier") or "free",
        subscription.get("status") or "inactive",
        subscription.get("ls_sub_id"),
        subscription.get("since"),
        subscription.get("ends_at"),
        subscription.get("renews_at"),
        subscription.get("quota_period_start"),
        subscription.get("quota_reset_at"),
        subscription.get("past_due_since"),
        subscription.get("update_payment_method_url") or "",
        subscription.get("customer_portal_url") or "",
    )
    if _USE_PG:
        _exec(
            db,
            "INSERT INTO student_subscription_state "
            "(client_id, tier, status, ls_sub_id, since_at, ends_at, renews_at, "
            "quota_period_start, quota_reset_at, past_due_since, "
            "update_payment_method_url, customer_portal_url) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(client_id) DO NOTHING",
            values,
        )
    else:
        _exec(
            db,
            "INSERT OR IGNORE INTO student_subscription_state "
            "(client_id, tier, status, ls_sub_id, since_at, ends_at, renews_at, "
            "quota_period_start, quota_reset_at, past_due_since, "
            "update_payment_method_url, customer_portal_url) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            values,
        )


def _student_migrations():
    """Run safe column additions."""
    migrations = [
        ("student_course_files", "exam_id", "INTEGER DEFAULT NULL"),
        ("student_study_progress", "focus_minutes", "INTEGER DEFAULT 0"),
        ("student_study_progress", "pages_read", "INTEGER DEFAULT 0"),
        # Focus session → what are you studying for / which test (optional, per-session)
        ("student_study_progress", "course_id", "INTEGER DEFAULT NULL"),
        ("student_study_progress", "exam_id", "INTEGER DEFAULT NULL"),
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
        # Presence — unix epoch seconds of the last activity touch.
        ("clients", "last_seen_at", "BIGINT DEFAULT 0"),
        ("clients", "friend_discovery", "BOOLEAN DEFAULT TRUE"),
        # Grade-Sheet semester tracking. The user's current_semester drives
        # which slot incoming Canvas courses land in, and is also what the
        # /student/courses page shows in the picker. semester_label on each
        # course is the slot the course belongs to in the grade sheet.
        ("clients", "current_semester", "TEXT DEFAULT ''"),
        ("student_courses", "semester_label", "TEXT DEFAULT ''"),
        ("student_courses", "canvas_active", "BOOLEAN DEFAULT TRUE"),
        ("student_courses", "catalog_id", "INTEGER DEFAULT NULL"),
        ("student_exams", "grade", "REAL DEFAULT NULL"),
    ]
    for table, col, col_type in migrations:
        try:
            with get_db() as db:
                if _USE_PG:
                    db.cursor().execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                else:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_focus_phases ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "phase_id TEXT NOT NULL, "
        "mode TEXT DEFAULT 'pomodoro', "
        "expected_minutes INTEGER DEFAULT 0, "
        "course_id INTEGER REFERENCES student_courses(id) ON DELETE SET NULL, "
        "exam_id INTEGER REFERENCES student_exams(id) ON DELETE SET NULL, "
        "started_at TIMESTAMP DEFAULT NOW(), "
        "claimed_at TIMESTAMP, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, phase_id))",
        "CREATE TABLE IF NOT EXISTS student_focus_phases ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "phase_id TEXT NOT NULL, "
        "mode TEXT DEFAULT 'pomodoro', "
        "expected_minutes INTEGER DEFAULT 0, "
        "course_id INTEGER REFERENCES student_courses(id) ON DELETE SET NULL, "
        "exam_id INTEGER REFERENCES student_exams(id) ON DELETE SET NULL, "
        "started_at TEXT DEFAULT (datetime('now','localtime')), "
        "claimed_at TEXT, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, phase_id))",
    )
    _create_table_safe(
        "CREATE INDEX IF NOT EXISTS idx_focus_phases_client ON student_focus_phases(client_id, phase_id)",
        "CREATE INDEX IF NOT EXISTS idx_focus_phases_client ON student_focus_phases(client_id, phase_id)",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_course_reviews ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "course_id INTEGER REFERENCES student_courses(id) ON DELETE SET NULL, "
        "university TEXT DEFAULT '', major TEXT DEFAULT '', course_name TEXT NOT NULL, course_code TEXT DEFAULT '', "
        "difficulty_rating INTEGER NOT NULL, quality_rating INTEGER NOT NULL, review_text TEXT DEFAULT '', "
        "final_grade REAL, total_hours INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, course_id))",
        "CREATE TABLE IF NOT EXISTS student_course_reviews ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "course_id INTEGER REFERENCES student_courses(id) ON DELETE SET NULL, "
        "university TEXT DEFAULT '', major TEXT DEFAULT '', course_name TEXT NOT NULL, course_code TEXT DEFAULT '', "
        "difficulty_rating INTEGER NOT NULL, quality_rating INTEGER NOT NULL, review_text TEXT DEFAULT '', "
        "final_grade REAL, total_hours INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, course_id))",
    )
    with get_db() as db:
        if _USE_PG:
            db.cursor().execute("CREATE INDEX IF NOT EXISTS idx_course_reviews_lookup ON student_course_reviews(university, major, course_name)")
        else:
            db.execute("CREATE INDEX IF NOT EXISTS idx_course_reviews_lookup ON student_course_reviews(university, major, course_name)")
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
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_friend_blocks ("
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "blocked_client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "created_at TIMESTAMP DEFAULT NOW(), PRIMARY KEY(client_id, blocked_client_id))",
        "CREATE TABLE IF NOT EXISTS student_friend_blocks ("
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "blocked_client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY(client_id, blocked_client_id))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_friend_reports ("
        "id SERIAL PRIMARY KEY, reporter_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "reported_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "reason TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_friend_reports ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "reported_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "reason TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now','localtime')))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_friend_audit ("
        "id SERIAL PRIMARY KEY, actor_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "target_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "action TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_friend_audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "target_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "action TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now','localtime')))",
    )
    # Referral program: each user owns one code; each new user can be referred once.
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_referral_codes ("
        "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, "
        "code TEXT NOT NULL UNIQUE, "
        "created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_referral_codes ("
        "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, "
        "code TEXT NOT NULL UNIQUE, "
        "created_at TEXT DEFAULT (datetime('now','localtime')))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_referrals ("
        "id SERIAL PRIMARY KEY, "
        "code TEXT NOT NULL, "
        "referrer_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "referred_id INTEGER NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE, "
        "created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_referrals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "code TEXT NOT NULL, "
        "referrer_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "referred_id INTEGER NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE, "
        "created_at TEXT DEFAULT (datetime('now','localtime')))",
    )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_course_outcomes ("
        "id SERIAL PRIMARY KEY, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE, "
        "course_key TEXT NOT NULL, "
        "course_name TEXT DEFAULT '', "
        "course_code TEXT DEFAULT '', "
        "final_grade REAL, "
        "passing_grade REAL NOT NULL DEFAULT 3.95, "
        "passed BOOLEAN NOT NULL DEFAULT FALSE, "
        "total_focus_minutes INTEGER NOT NULL DEFAULT 0, "
        "reported_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(client_id, course_id))",
        "CREATE TABLE IF NOT EXISTS student_course_outcomes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "course_id INTEGER NOT NULL REFERENCES student_courses(id) ON DELETE CASCADE, "
        "course_key TEXT NOT NULL, "
        "course_name TEXT DEFAULT '', "
        "course_code TEXT DEFAULT '', "
        "final_grade REAL, "
        "passing_grade REAL NOT NULL DEFAULT 3.95, "
        "passed INTEGER NOT NULL DEFAULT 0, "
        "total_focus_minutes INTEGER NOT NULL DEFAULT 0, "
        "reported_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(client_id, course_id))",
    )
    for col, col_type in (("final_grade", "REAL"), ("passing_grade", "REAL DEFAULT 3.95")):
        try:
            with get_db() as db:
                if _USE_PG:
                    db.cursor().execute(f"ALTER TABLE student_course_outcomes ADD COLUMN {col} {col_type}")
                else:
                    db.execute(f"ALTER TABLE student_course_outcomes ADD COLUMN {col} {col_type}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise


# ── Courses ─────────────────────────────────────────────────

def upsert_course(client_id: int, canvas_course_id: int, name: str,
                  code: str = "", term: str = "") -> int:
    """Insert or update a Canvas course. NEW courses are auto-tagged with
    the user's current_semester so they land in the right Grade-Sheet slot;
    EXISTING courses keep whatever semester the user already set, so a fresh
    sync from Canvas never overwrites the slot they're in."""
    with get_db() as db:
        existing = _fetchone(
            db,
            "SELECT id FROM student_courses WHERE client_id = %s AND canvas_course_id = %s",
            (client_id, canvas_course_id),
        )
        if existing:
            _exec(db,
                  "UPDATE student_courses SET name = %s, code = %s, term = %s, "
                  "canvas_active = TRUE, last_synced = " + ("NOW()" if _USE_PG else "datetime('now','localtime')") + " WHERE id = %s",
                  (name, code, term, existing["id"]))
            return existing["id"]
        # New course → take semester from the client's current_semester so
        # mid-year syncs land courses in the next slot, not the current one.
        cli = _fetchone(
            db,
            "SELECT current_semester FROM clients WHERE id = %s",
            (client_id,),
        )
        sem = (dict(cli).get("current_semester") if cli else "") or ""
        return _insert_returning_id(
            db,
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term, semester_label, last_synced) "
            "VALUES (%s, %s, %s, %s, %s, %s, " + ("NOW()" if _USE_PG else "datetime('now','localtime')") + ") RETURNING id",
            (client_id, canvas_course_id, name, code, term, sem),
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term, semester_label, last_synced) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
        )


def _catalog_norm(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace — for catalog matching."""
    s = unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower().strip())


def _client_university_id(client_id: int) -> int | None:
    """The university the student picked during academic setup, or None."""
    try:
        with get_db() as db:
            r = _fetchone(db, "SELECT university_id FROM clients WHERE id = %s", (client_id,))
        uid = (dict(r).get("university_id") if r else None)
        return int(uid) if uid else None
    except Exception:
        return None


def add_manual_course(client_id: int, code: str, name: str) -> int:
    """Create a student-entered course (no Canvas). Manual courses use a
    negative synthetic canvas_course_id so they never collide with real Canvas
    ids and can be told apart later (canvas_course_id < 0 == manual)."""
    name = (name or "").strip()
    code = (code or "").strip()
    if not name:
        raise ValueError("Course name is required")
    with get_db() as db:
        # De-dupe only on normalized code plus the exact trimmed course name.
        existing = _fetchall(
            db,
            "SELECT id, name, code FROM student_courses WHERE client_id = %s",
            (client_id,),
        ) or []
        for r in existing:
            if (
                _catalog_norm(dict(r).get("code", "")) == _catalog_norm(code)
                and str(dict(r).get("name") or "").strip() == name
            ):
                return int(dict(r)["id"])
        # Pick a unique negative id below the current minimum for this client.
        min_id = _fetchval(
            db,
            "SELECT MIN(canvas_course_id) FROM student_courses WHERE client_id = %s",
            (client_id,),
        )
        manual_id = (min(int(min_id), 0) if min_id is not None else 0) - 1
        cli = _fetchone(db, "SELECT current_semester FROM clients WHERE id = %s", (client_id,))
        sem = (dict(cli).get("current_semester") if cli else "") or ""
        return _insert_returning_id(
            db,
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term, semester_label) "
            "VALUES (%s, %s, %s, %s, '', %s) RETURNING id",
            (client_id, manual_id, name, code, sem),
            "INSERT INTO student_courses (client_id, canvas_course_id, name, code, term, semester_label) "
            "VALUES (?, ?, ?, ?, '', ?)",
        )


def init_course_catalog_table() -> None:
    """Crowd-sourced per-university course catalog that powers autofill.
    Every course a student adds — manually OR via Canvas — is recorded here
    against their university, so the next student at the SAME university gets
    code/name suggestions. Strictly scoped per university id."""
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_course_catalog (
            id            SERIAL PRIMARY KEY,
            university_id INTEGER NOT NULL,
            code          TEXT DEFAULT '',
            code_norm     TEXT DEFAULT '',
            name          TEXT NOT NULL,
            name_norm     TEXT NOT NULL,
            uses          INTEGER DEFAULT 1,
            deleted_at    TIMESTAMP,
            deleted_by    INTEGER,
            recover_until TIMESTAMP,
            created_at    TIMESTAMP DEFAULT NOW(),
            UNIQUE(university_id, code_norm, name)
        )""",
        """CREATE TABLE IF NOT EXISTS student_course_catalog (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            code          TEXT DEFAULT '',
            code_norm     TEXT DEFAULT '',
            name          TEXT NOT NULL,
            name_norm     TEXT NOT NULL,
            uses          INTEGER DEFAULT 1,
            deleted_at    TEXT,
            deleted_by    INTEGER,
            recover_until TEXT,
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(university_id, code_norm, name)
        )""",
    )
    for column, spec in (
        ("deleted_at", "TIMESTAMP DEFAULT NULL"),
        ("deleted_by", "INTEGER DEFAULT NULL"),
        ("recover_until", "TIMESTAMP DEFAULT NULL"),
    ):
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE student_course_catalog ADD COLUMN {column} {spec}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    for idx, col in (
        ("idx_course_catalog_univ_name", "university_id, name_norm"),
        ("idx_course_catalog_univ_code", "university_id, code_norm"),
    ):
        try:
            with get_db() as db:
                stmt = f"CREATE INDEX IF NOT EXISTS {idx} ON student_course_catalog({col})"
                db.cursor().execute(stmt) if _USE_PG else db.execute(stmt)
        except Exception:
            pass
    if _USE_PG:
        with get_db() as db:
            _exec(
                db,
                "ALTER TABLE student_course_catalog DROP CONSTRAINT IF EXISTS "
                "student_course_catalog_university_id_code_norm_name_norm_key",
            )
            _exec(
                db,
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_course_catalog_exact_name "
                "ON student_course_catalog(university_id, code_norm, name)",
            )
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS admin_course_deletions ("
        "id SERIAL PRIMARY KEY, catalog_id INTEGER NOT NULL, admin_id INTEGER NOT NULL, "
        "course_name TEXT NOT NULL, course_code TEXT DEFAULT '', reason TEXT DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'recoverable', deleted_at TIMESTAMP DEFAULT NOW(), "
        "recover_until TIMESTAMP NOT NULL, recovered_at TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS admin_course_deletions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL, admin_id INTEGER NOT NULL, "
        "course_name TEXT NOT NULL, course_code TEXT DEFAULT '', reason TEXT DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'recoverable', "
        "deleted_at TEXT DEFAULT (datetime('now','localtime')), recover_until TEXT NOT NULL, "
        "recovered_at TEXT)",
    )


def record_course_to_catalog(university_id: int | None, code: str, name: str) -> None:
    """Add (or bump) a course in a university's autofill catalog. No-op without
    a university or a name. Safe to call on every course add."""
    if not university_id:
        return
    name = (name or "").strip()
    code = (code or "").strip()
    if not name:
        return
    name_norm = _catalog_norm(name)
    code_norm = _catalog_norm(code)
    try:
        with get_db() as db:
            if _USE_PG:
                _exec(
                    db,
                    "INSERT INTO student_course_catalog "
                    "(university_id, code, code_norm, name, name_norm) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (university_id, code_norm, name) "
                    "DO UPDATE SET uses = student_course_catalog.uses + 1",
                    (university_id, code, code_norm, name, name_norm),
                )
            else:
                try:
                    db.execute(
                        "INSERT INTO student_course_catalog "
                        "(university_id, code, code_norm, name, name_norm) "
                        "VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(university_id, code_norm, name) "
                        "DO UPDATE SET uses = uses + 1",
                        (university_id, code, code_norm, name, name_norm),
                    )
                except Exception:
                    # Older SQLite without UPSERT — emulate.
                    row = _fetchone(
                        db,
                        "SELECT id FROM student_course_catalog "
                        "WHERE university_id = ? AND code_norm = ? AND name = ?",
                        (university_id, code_norm, name),
                    )
                    if row:
                        db.execute(
                            "UPDATE student_course_catalog SET uses = uses + 1 WHERE id = ?",
                            (dict(row)["id"],),
                        )
                    else:
                        db.execute(
                            "INSERT INTO student_course_catalog "
                            "(university_id, code, code_norm, name, name_norm) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (university_id, code, code_norm, name, name_norm),
                        )
    except Exception as e:
        log.debug("record_course_to_catalog failed: %s", e)


def search_course_catalog(university_id: int | None, query: str, limit: int = 8) -> list[dict]:
    """Autofill suggestions for ONE university. Never leaks other schools'
    courses — university_id is always part of the WHERE clause."""
    if not university_id:
        return []
    qn = _catalog_norm(query)
    with get_db() as db:
        if not qn:
            rows = _fetchall(
                db,
                "SELECT id, code, name, uses FROM student_course_catalog "
                "WHERE university_id = %s AND deleted_at IS NULL "
                "ORDER BY uses DESC, name ASC LIMIT %s",
                (university_id, limit),
            ) or []
        else:
            like = f"%{qn}%"
            rows = _fetchall(
                db,
                "SELECT id, code, name, uses FROM student_course_catalog "
                "WHERE university_id = %s AND deleted_at IS NULL "
                "AND (name_norm LIKE %s OR code_norm LIKE %s) "
                "ORDER BY uses DESC, LENGTH(name) ASC, name ASC LIMIT %s",
                (university_id, like, like, limit),
            ) or []
    return [dict(r) for r in rows]


def find_catalog_course(university_id: int | None, code: str, name: str) -> dict | None:
    """Find an active canonical course by normalized code and exact name."""
    if not university_id or not (name or "").strip():
        return None
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT id, university_id, code, name, uses FROM student_course_catalog "
            "WHERE university_id = %s AND code_norm = %s AND name = %s "
            "AND deleted_at IS NULL LIMIT 1",
            (int(university_id), _catalog_norm(code), (name or "").strip()),
        )
    return dict(row) if row else None


def join_catalog_course(client_id: int, catalog_id: int) -> dict:
    """Create a student's membership in an existing ownerless course."""
    university_id = _client_university_id(client_id)
    with get_db() as db:
        catalog = _fetchone(
            db,
            "SELECT id, code, name FROM student_course_catalog "
            "WHERE id = %s AND university_id = %s AND deleted_at IS NULL",
            (catalog_id, university_id),
        )
        if not catalog:
            return {"ok": False, "error": "Course not found."}
        existing = _fetchone(
            db,
            "SELECT id FROM student_courses WHERE client_id = %s AND catalog_id = %s",
            (client_id, catalog_id),
        )
        if existing:
            return {"ok": True, "id": int(existing["id"]), "already_joined": True}
        min_id = _fetchval(
            db,
            "SELECT MIN(canvas_course_id) FROM student_courses WHERE client_id = %s",
            (client_id,),
        )
        manual_id = (min(int(min_id), 0) if min_id is not None else 0) - 1
        cli = _fetchone(db, "SELECT current_semester FROM clients WHERE id = %s", (client_id,))
        semester = (dict(cli).get("current_semester") if cli else "") or ""
        course_id = _insert_returning_id(
            db,
            "INSERT INTO student_courses "
            "(client_id, canvas_course_id, name, code, term, semester_label, catalog_id) "
            "VALUES (%s, %s, %s, %s, '', %s, %s) RETURNING id",
            (client_id, manual_id, catalog["name"], catalog["code"], semester, catalog_id),
            "INSERT INTO student_courses "
            "(client_id, canvas_course_id, name, code, term, semester_label, catalog_id) "
            "VALUES (?, ?, ?, ?, '', ?, ?)",
        )
        _exec(db, "UPDATE student_course_catalog SET uses = uses + 1 WHERE id = %s", (catalog_id,))
    return {"ok": True, "id": course_id, "joined_existing": True}


def attach_course_to_catalog(client_id: int, course_id: int, catalog_id: int) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_courses SET catalog_id = %s WHERE id = %s AND client_id = %s",
            (catalog_id, course_id, client_id),
        )


def soft_delete_catalog_course(
    admin_id: int,
    catalog_id: int,
    confirmation: str,
    reason: str = "",
    recovery_days: int = 30,
) -> dict:
    """Hide an ownerless course and its memberships during a recovery window."""
    now = datetime.now()
    recover_until = now + timedelta(days=max(1, int(recovery_days)))
    with get_db() as db:
        catalog = _fetchone(
            db,
            "SELECT id, university_id, code, code_norm, name FROM student_course_catalog "
            "WHERE id = %s AND deleted_at IS NULL",
            (catalog_id,),
        )
        if not catalog:
            return {"ok": False, "error": "Course not found."}
        if str(confirmation or "").strip() != str(catalog["name"]):
            return {"ok": False, "error": "Type the exact course name to confirm."}
        memberships = _fetchall(
            db,
            "SELECT sc.id, sc.code, sc.name, sc.catalog_id FROM student_courses sc "
            "JOIN clients c ON c.id = sc.client_id "
            "WHERE c.university_id = %s AND (sc.catalog_id = %s OR sc.catalog_id IS NULL)",
            (catalog["university_id"], catalog_id),
        )
        membership_ids = [
            int(row["id"])
            for row in memberships
            if int(row.get("catalog_id") or 0) == catalog_id or (
                _catalog_norm(row.get("code") or "") == str(catalog["code_norm"])
                and str(row.get("name") or "").strip() == str(catalog["name"])
            )
        ]
        for course_id in membership_ids:
            _exec(
                db,
                "UPDATE student_courses SET catalog_id = %s, canvas_active = %s WHERE id = %s",
                (catalog_id, False, course_id),
            )
        _exec(
            db,
            "UPDATE student_course_catalog SET deleted_at = %s, deleted_by = %s, "
            "recover_until = %s WHERE id = %s",
            (
                now.strftime("%Y-%m-%d %H:%M:%S"),
                admin_id,
                recover_until.strftime("%Y-%m-%d %H:%M:%S"),
                catalog_id,
            ),
        )
        deletion_id = _insert_returning_id(
            db,
            "INSERT INTO admin_course_deletions "
            "(catalog_id, admin_id, course_name, course_code, reason, recover_until) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                catalog_id,
                admin_id,
                catalog["name"],
                catalog["code"],
                str(reason or "")[:500],
                recover_until.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            "INSERT INTO admin_course_deletions "
            "(catalog_id, admin_id, course_name, course_code, reason, recover_until) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )
    return {
        "ok": True,
        "deletion_id": deletion_id,
        "memberships_hidden": len(membership_ids),
        "recover_until": recover_until.isoformat(),
    }


def recover_catalog_course(admin_id: int, deletion_id: int) -> dict:
    """Restore a soft-deleted course before its recovery window expires."""
    del admin_id  # actor is recorded by the application security audit log
    with get_db() as db:
        deletion = _fetchone(
            db,
            "SELECT id, catalog_id, recover_until, status FROM admin_course_deletions "
            "WHERE id = %s",
            (deletion_id,),
        )
        if not deletion or deletion.get("status") != "recoverable":
            return {"ok": False, "error": "Recovery record not found."}
        recover_until = _parse_focus_dt(deletion.get("recover_until"))
        if not recover_until or recover_until < datetime.now():
            return {"ok": False, "error": "The recovery window has expired."}
        catalog_id = int(deletion["catalog_id"])
        _exec(
            db,
            "UPDATE student_course_catalog SET deleted_at = NULL, deleted_by = NULL, "
            "recover_until = NULL WHERE id = %s",
            (catalog_id,),
        )
        _exec(
            db,
            "UPDATE student_courses SET canvas_active = %s WHERE catalog_id = %s",
            (True, catalog_id),
        )
        _exec(
            db,
            "UPDATE admin_course_deletions SET status = 'recovered', "
            "recovered_at = CURRENT_TIMESTAMP WHERE id = %s",
            (deletion_id,),
        )
    return {"ok": True, "catalog_id": catalog_id}


def purge_expired_course_deletions() -> int:
    """Permanently remove course-specific data after the recovery window."""
    def scrub_plan(value: Any, course_id: int, exam_ids: set[int]) -> Any:
        """Remove only blocks tied to a deleted membership from saved plans."""
        if isinstance(value, dict):
            try:
                linked_course = int(value.get("course_id") or 0) == course_id
            except (TypeError, ValueError):
                linked_course = False
            try:
                linked_exam = int(value.get("exam_id") or 0) in exam_ids
            except (TypeError, ValueError):
                linked_exam = False
            if linked_course or linked_exam:
                return None
            return {key: scrub_plan(item, course_id, exam_ids) for key, item in value.items()}
        if isinstance(value, list):
            cleaned = [scrub_plan(item, course_id, exam_ids) for item in value]
            return [item for item in cleaned if item is not None]
        return value

    purged = 0
    with get_db() as db:
        expired = _fetchall(
            db,
            "SELECT id, catalog_id FROM admin_course_deletions "
            "WHERE status = 'recoverable' AND recover_until < CURRENT_TIMESTAMP",
        )
        for deletion in expired:
            catalog_id = int(deletion["catalog_id"])
            memberships = _fetchall(
                db,
                "SELECT id, client_id FROM student_courses WHERE catalog_id = %s",
                (catalog_id,),
            )
            course_ids = [int(row["id"]) for row in memberships]
            for row in memberships:
                course_id = int(row["id"])
                client_id = int(row["client_id"])
                exam_rows = _fetchall(
                    db,
                    "SELECT id FROM student_exams WHERE course_id = %s AND client_id = %s",
                    (course_id, client_id),
                )
                exam_ids = {int(exam["id"]) for exam in exam_rows}
                saved_plans = _fetchall(
                    db,
                    "SELECT id, plan_json FROM student_study_plans WHERE client_id = %s",
                    (client_id,),
                )
                for saved in saved_plans:
                    try:
                        payload = saved.get("plan_json") or {}
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        cleaned = scrub_plan(payload, course_id, exam_ids)
                        _exec(
                            db,
                            "UPDATE student_study_plans SET plan_json = %s WHERE id = %s",
                            (json.dumps(cleaned or {}, ensure_ascii=False), int(saved["id"])),
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        log.warning("Could not scrub saved plan %s during course purge", saved.get("id"))
                for exam_id in exam_ids:
                    _exec(
                        db,
                        "DELETE FROM student_planner_done WHERE client_id = %s AND exam_id = %s",
                        (client_id, exam_id),
                    )
                _exec(
                    db,
                    "UPDATE student_study_progress SET course_id = NULL, exam_id = NULL "
                    "WHERE client_id = %s AND (course_id = %s OR exam_id IN "
                    "(SELECT id FROM student_exams WHERE course_id = %s))",
                    (client_id, course_id, course_id),
                )
                _exec(
                    db,
                    "UPDATE student_focus_phases SET course_id = NULL, exam_id = NULL "
                    "WHERE client_id = %s AND (course_id = %s OR exam_id IN "
                    "(SELECT id FROM student_exams WHERE course_id = %s))",
                    (client_id, course_id, course_id),
                )
                _exec(db, "DELETE FROM student_course_outcomes WHERE course_id = %s", (course_id,))
                _exec(db, "DELETE FROM student_course_reviews WHERE course_id = %s", (course_id,))
                _exec(db, "DELETE FROM student_courses WHERE id = %s", (course_id,))
            _exec(
                db,
                "UPDATE admin_course_deletions SET status = 'purged' WHERE id = %s",
                (int(deletion["id"]),),
            )
            _exec(db, "DELETE FROM student_course_catalog WHERE id = %s", (catalog_id,))
            purged += 1
            log.info("Purged canonical course %s and memberships %s", catalog_id, course_ids)
    return purged


def list_admin_course_catalog(limit: int = 200) -> dict:
    with get_db() as db:
        active = _fetchall(
            db,
            "SELECT id, university_id, code, name, uses FROM student_course_catalog "
            "WHERE deleted_at IS NULL ORDER BY uses DESC, name ASC LIMIT %s",
            (max(1, min(int(limit), 500)),),
        )
        recoverable = _fetchall(
            db,
            "SELECT id, catalog_id, course_code, course_name, reason, recover_until "
            "FROM admin_course_deletions WHERE status = 'recoverable' "
            "ORDER BY deleted_at DESC LIMIT %s",
            (max(1, min(int(limit), 500)),),
        )
    return {
        "active": [dict(row) for row in active],
        "recoverable": [dict(row) for row in recoverable],
    }


def backfill_course_catalog() -> int:
    """One-time, self-healing fill of the autofill catalog from EVERY course
    any student has ever added (manual or Canvas), grouped per university.

    The catalog is populated going forward on each course add, but courses
    added before that wiring existed were never recorded — so autofill only
    knew recent ones. This sweeps all existing student_courses (joined to each
    owner's university) into the catalog. Idempotent: it loads the catalog's
    existing keys once and only inserts the (university, code, name) combos
    that are missing, so re-running on every startup is cheap and never
    double-counts. `uses` is seeded with how many students added that course,
    so popularity ordering reflects the whole user base.
    """
    with get_db() as db:
        courses = _fetchall(
            db,
            "SELECT c.university_id AS university_id, sc.code AS code, sc.name AS name "
            "FROM student_courses sc JOIN clients c ON c.id = sc.client_id "
            "WHERE c.university_id IS NOT NULL",
        ) or []
        existing = _fetchall(
            db,
            "SELECT university_id, code_norm, name_norm FROM student_course_catalog",
        ) or []

    have = {
        (int(d["university_id"]), d.get("code_norm") or "", d.get("name_norm") or "")
        for d in (dict(r) for r in existing)
    }
    agg: dict[tuple, dict] = {}
    for row in courses:
        d = dict(row)
        uni = d.get("university_id")
        name = (d.get("name") or "").strip()
        if not uni or not name:
            continue
        code = (d.get("code") or "").strip()
        key = (int(uni), _catalog_norm(code), _catalog_norm(name))
        if key in have:
            continue  # already in the catalog — leave its uses untouched
        entry = agg.get(key)
        if entry is None:
            agg[key] = {"university_id": int(uni), "code": code, "name": name, "uses": 1}
        else:
            entry["uses"] += 1

    if not agg:
        return 0
    inserted = 0
    with get_db() as db:
        for (uni, code_norm, name_norm), v in agg.items():
            try:
                _exec(
                    db,
                    "INSERT INTO student_course_catalog "
                    "(university_id, code, code_norm, name, name_norm, uses) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (v["university_id"], v["code"], code_norm, v["name"], name_norm, v["uses"]),
                )
                inserted += 1
            except Exception:
                # Unique-constraint race (another writer added it): safe to skip.
                pass
    return inserted


def get_current_semester(client_id: int) -> str:
    with get_db() as db:
        r = _fetchone(db, "SELECT current_semester FROM clients WHERE id = %s", (client_id,))
    return ((dict(r).get("current_semester") if r else "") or "")


def set_current_semester(client_id: int, label: str) -> None:
    """Update the user's current semester. Any of their courses that don't
    yet have a semester get tagged with the OLD value (so the user's
    pre-existing list lands in whatever slot was active before they
    advanced)."""
    label = (label or "").strip()
    with get_db() as db:
        prev = _fetchone(db, "SELECT current_semester FROM clients WHERE id = %s", (client_id,))
        prev_label = ((dict(prev).get("current_semester") if prev else "") or "").strip()
        # Backfill: if the user is just now setting a semester for the first
        # time, tag all of their existing courses with whatever they pick.
        # On subsequent advances, only untagged courses inherit the OLD label.
        backfill_to = prev_label or label
        _exec(
            db,
            "UPDATE student_courses SET semester_label = %s "
            "WHERE client_id = %s AND (semester_label IS NULL OR semester_label = '')",
            (backfill_to, client_id),
        )
        _exec(db, "UPDATE clients SET current_semester = %s WHERE id = %s", (label, client_id))


def get_pending_course_outcomes(client_id: int, semester_label: str | None = None) -> list[dict]:
    """Courses in a semester that still need a final grade report."""
    sem = (semester_label if semester_label is not None else get_current_semester(client_id) or "").strip()
    if not sem:
        return []
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT c.id, c.name, c.code, COALESCE(c.semester_label,'') AS semester_label "
            "FROM student_courses c "
            "LEFT JOIN student_course_outcomes o ON o.client_id = c.client_id AND o.course_id = c.id "
            "WHERE c.client_id = %s AND COALESCE(c.semester_label,'') = %s "
            "AND (o.id IS NULL OR o.final_grade IS NULL) "
            "ORDER BY c.name ASC",
            (client_id, sem),
        ) or []
    return [dict(r) for r in rows]


def get_courses_by_semester(client_id: int) -> dict:
    """Return {semester_label: [{id, name, code, exams: [...]}]} for the
    Grade Sheet to overlay onto its localStorage data."""
    with get_db() as db:
        crows = _fetchall(
            db,
            "SELECT id, name, code, COALESCE(semester_label,'') AS semester_label "
            "FROM student_courses WHERE client_id = %s "
            "AND COALESCE(canvas_active, TRUE) ORDER BY id ASC",
            (client_id,),
        )
        erows = _fetchall(
            db,
            "SELECT course_id, name, exam_date, weight_pct, grade "
            "FROM student_exams WHERE client_id = %s",
            (client_id,),
        )
    exams_by_course: dict = {}
    for e in erows or []:
        d = dict(e)
        exams_by_course.setdefault(int(d["course_id"]), []).append({
            "name": d.get("name") or "",
            "exam_date": d.get("exam_date") or "",
            "weight_pct": int(d.get("weight_pct") or 0),
            "grade": d.get("grade"),
        })
    out: dict = {}
    for c in crows or []:
        d = dict(c)
        sem = (d.get("semester_label") or "") or "_unassigned"
        out.setdefault(sem, []).append({
            "id": int(d["id"]),
            "name": d.get("name") or "",
            "code": d.get("code") or "",
            "exams": exams_by_course.get(int(d["id"]), []),
        })
    return out


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


def leave_course(client_id: int, course_db_id: int) -> bool:
    """Remove one membership while preserving Focus and global progress."""
    with get_db() as db:
        owned = _fetchone(
            db,
            "SELECT id FROM student_courses WHERE id = %s AND client_id = %s",
            (course_db_id, client_id),
        )
        if not owned:
            return False
        _exec(
            db,
            "UPDATE student_study_progress SET course_id = NULL, exam_id = NULL "
            "WHERE client_id = %s AND (course_id = %s OR exam_id IN "
            "(SELECT id FROM student_exams WHERE course_id = %s AND client_id = %s))",
            (client_id, course_db_id, course_db_id, client_id),
        )
        _exec(
            db,
            "UPDATE student_focus_phases SET course_id = NULL, exam_id = NULL "
            "WHERE client_id = %s AND (course_id = %s OR exam_id IN "
            "(SELECT id FROM student_exams WHERE course_id = %s AND client_id = %s))",
            (client_id, course_db_id, course_db_id, client_id),
        )
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


def get_courses(client_id: int, include_archived: bool = False) -> list[dict]:
    active_filter = "" if include_archived else " AND COALESCE(canvas_active, TRUE)"
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT * FROM student_courses WHERE client_id = %s"
            + active_filter
            + " ORDER BY name",
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
                topics: list[str], grade: float | None = None) -> int:
    with get_db() as db:
        topics_json = json.dumps(topics, ensure_ascii=False)
        if exam_id:
            _exec(db,
                  "UPDATE student_exams SET name = %s, exam_date = %s, weight_pct = %s, "
                  "topics_json = %s, grade = %s WHERE id = %s AND client_id = %s",
                  (name, exam_date, weight_pct, topics_json, grade, exam_id, client_id))
            return exam_id
        return _insert_returning_id(
            db,
            "INSERT INTO student_exams (client_id, course_id, name, exam_date, weight_pct, topics_json, grade) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_db_id, name, exam_date, weight_pct, topics_json, grade),
            "INSERT INTO student_exams (client_id, course_id, name, exam_date, weight_pct, topics_json, grade) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
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


def init_planner_done_table():
    """Per-block completion marks for the weekly planner."""
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS student_planner_done (
            id          SERIAL PRIMARY KEY,
            client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            block_date  TEXT NOT NULL,
            exam_id     INTEGER DEFAULT 0,
            unit_index  INTEGER DEFAULT -1,
            title       TEXT DEFAULT '',
            phase       TEXT DEFAULT '',
            minutes     INTEGER DEFAULT 0,
            done_at     TIMESTAMP DEFAULT NOW(),
            UNIQUE(client_id, block_date, exam_id, unit_index, title)
        )""",
        """CREATE TABLE IF NOT EXISTS student_planner_done (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            block_date  TEXT NOT NULL,
            exam_id     INTEGER DEFAULT 0,
            unit_index  INTEGER DEFAULT -1,
            title       TEXT DEFAULT '',
            phase       TEXT DEFAULT '',
            minutes     INTEGER DEFAULT 0,
            done_at     TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(client_id, block_date, exam_id, unit_index, title)
        )""",
    )


def planner_mark_block(client_id: int, block_date: str, exam_id: int, unit_index: int,
                       title: str, phase: str, minutes: int, done: bool):
    """Mark/unmark one plan block as done. Identity = (date, exam, unit, title)."""
    exam_id = int(exam_id or 0)
    unit_index = -1 if unit_index is None else int(unit_index)
    title = (title or "")[:160]
    with get_db() as db:
        _exec(db,
              "DELETE FROM student_planner_done WHERE client_id = %s AND block_date = %s "
              "AND exam_id = %s AND unit_index = %s AND title = %s",
              (client_id, block_date, exam_id, unit_index, title))
        if done:
            _exec(db,
                  "INSERT INTO student_planner_done (client_id, block_date, exam_id, unit_index, title, phase, minutes) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (client_id, block_date, exam_id, unit_index, title, (phase or "")[:60], int(minutes or 0)))


def planner_done_blocks(client_id: int, since_date: str = "") -> list[dict]:
    with get_db() as db:
        if since_date:
            rows = _fetchall(db,
                             "SELECT * FROM student_planner_done WHERE client_id = %s AND block_date >= %s "
                             "ORDER BY block_date, id",
                             (client_id, since_date))
        else:
            rows = _fetchall(db,
                             "SELECT * FROM student_planner_done WHERE client_id = %s ORDER BY block_date, id",
                             (client_id,))
        return [dict(r) for r in rows]


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


def add_course_file(client_id: int, course_id: int, original_name: str, file_type: str,
                    extracted_text: str, exam_id: int | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO student_course_files (client_id, course_id, exam_id, original_name, file_type, extracted_text) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, course_id, exam_id, original_name, file_type, extracted_text),
            "INSERT INTO student_course_files (client_id, course_id, exam_id, original_name, file_type, extracted_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )


def delete_course_file(file_id: int, client_id: int):
    with get_db() as db:
        _exec(db, "DELETE FROM student_course_files WHERE id = %s AND client_id = %s",
              (file_id, client_id))


# ── Focus / Pomodoro tracking ────────────────────────────────

# Server-side ceilings to defend against orphaned timers, malicious clients,
# or stale localStorage state crediting absurd amounts of "study time".
FOCUS_MAX_MINUTES_PER_SESSION = 480   # 8h cap on a single save
FOCUS_MAX_MINUTES_PER_DAY     = 16 * 60  # 16h cap on a single calendar day
FOCUS_PHASE_CLAIM_GRACE_SECONDS = 20


def _parse_focus_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except Exception:
        pass
    raw = raw.replace("+00:00", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:26], fmt).replace(tzinfo=UTC)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except Exception:
        return None


def _focus_started_at(row: dict) -> datetime | None:
    """Use the canonical UTC timestamp, falling back only for legacy rows."""
    return _parse_focus_dt(row.get("started_at_utc")) or _parse_focus_dt(
        row.get("started_at")
    )


def _validate_focus_course_exam(db, client_id: int, course_id, exam_id):
    try:
        course_id = int(course_id or 0) or None
    except (TypeError, ValueError):
        course_id = None
    try:
        exam_id = int(exam_id or 0) or None
    except (TypeError, ValueError):
        exam_id = None

    if course_id:
        owns = _fetchval(
            db,
            "SELECT 1 FROM student_courses WHERE id = %s AND client_id = %s",
            (course_id, client_id),
        )
        if not owns:
            course_id = None

    if exam_id:
        if course_id:
            owns = _fetchval(
                db,
                "SELECT 1 FROM student_exams WHERE id = %s AND client_id = %s AND course_id = %s",
                (exam_id, client_id, course_id),
            )
        else:
            owns = _fetchval(
                db,
                "SELECT 1 FROM student_exams WHERE id = %s AND client_id = %s",
                (exam_id, client_id),
            )
        if not owns:
            exam_id = None

    return course_id, exam_id


def start_focus_phase(client_id: int, phase_id: str, mode: str = "pomodoro",
                      expected_minutes: int = 0, course_id: int | None = None,
                      exam_id: int | None = None) -> dict | None:
    """Register a work phase before the browser timer starts.

    Claim endpoints later require this row and enough elapsed wall-clock time.
    That prevents forged `localStorage.focus_pending_phases` entries from
    becoming XP or coins.
    """
    phase_id = (phase_id or "").strip()[:80]
    if not phase_id:
        return None
    mode = (mode or "pomodoro").strip()[:40] or "pomodoro"
    try:
        expected_minutes = int(expected_minutes or 0)
    except (TypeError, ValueError):
        expected_minutes = 0
    expected_minutes = max(0, min(FOCUS_MAX_MINUTES_PER_SESSION, expected_minutes))
    now_utc = utc_now()
    now = now_utc.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")

    with get_db() as db:
        course_id, exam_id = _validate_focus_course_exam(db, client_id, course_id, exam_id)
        existing = _fetchone(
            db,
            "SELECT * FROM student_focus_phases WHERE client_id = %s AND phase_id = %s",
            (client_id, phase_id),
        )
        if existing:
            return existing
        try:
            _exec(
                db,
                "INSERT INTO student_focus_phases "
                "(client_id, phase_id, mode, expected_minutes, course_id, exam_id, "
                "started_at, started_at_utc) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    client_id, phase_id, mode, expected_minutes, course_id,
                    exam_id, now, now_utc.isoformat(),
                ),
            )
        except Exception:
            pass
        return _fetchone(
            db,
            "SELECT * FROM student_focus_phases WHERE client_id = %s AND phase_id = %s",
            (client_id, phase_id),
        )


def validate_focus_phase_claim(client_id: int, phase_id: str, minutes: int,
                               course_id: int | None = None,
                               exam_id: int | None = None) -> tuple[bool, str, dict | None]:
    phase_id = (phase_id or "").strip()[:80]
    if not phase_id:
        return False, "missing-phase-id", None
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    minutes = max(0, min(FOCUS_MAX_MINUTES_PER_SESSION, minutes))
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT * FROM student_focus_phases WHERE client_id = %s AND phase_id = %s",
            (client_id, phase_id),
        )
        if not row:
            return False, "unverified-phase", None
        if row.get("claimed_at"):
            return False, "duplicate-phase", row

        registered_course = row.get("course_id")
        registered_exam = row.get("exam_id")
        try:
            requested_course = int(course_id or 0) or None
        except (TypeError, ValueError):
            requested_course = None
        try:
            requested_exam = int(exam_id or 0) or None
        except (TypeError, ValueError):
            requested_exam = None
        if registered_course and requested_course and int(registered_course) != requested_course:
            return False, "course-mismatch", row
        if registered_exam and requested_exam and int(registered_exam) != requested_exam:
            return False, "exam-mismatch", row

        try:
            expected = int(row.get("expected_minutes") or 0)
        except (TypeError, ValueError):
            expected = 0
        if expected > 0 and minutes > expected + 1:
            return False, "minutes-exceed-phase", row

        started_at = _focus_started_at(row)
        if not started_at:
            return False, "invalid-phase-start", row
        elapsed = (utc_now() - started_at).total_seconds()
        # NOTE: no wall-clock max-age here. Pending phases stay claimable no
        # matter how long they sit (e.g. paused overnight and claimed in the
        # morning). XP is only ever forfeited client-side when the 30-min
        # long-break claim window expires. Forged claims are still blocked by
        # the registration requirement + the "can't claim more than was
        # registered / than time actually elapsed" checks below.
        required_minutes = expected if expected > 0 else minutes
        required_seconds = max(0, (required_minutes * 60) - FOCUS_PHASE_CLAIM_GRACE_SECONDS)
        if minutes > 0 and elapsed < required_seconds:
            return False, "phase-too-early", row
        return True, "ok", row


def mark_focus_phase_claimed(client_id: int, phase_id: str) -> None:
    phase_id = (phase_id or "").strip()[:80]
    if not phase_id:
        return
    now = utc_now()
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_focus_phases SET claimed_at = %s, claimed_at_utc = %s "
            "WHERE client_id = %s AND phase_id = %s AND claimed_at IS NULL",
            (
                now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
                now.isoformat(),
                client_id,
                phase_id,
            ),
        )


def reconcile_canvas_courses(client_id: int, courses: list[dict]) -> dict:
    """Upsert one complete Canvas snapshot and archive missing Canvas courses."""
    normalized = []
    seen = set()
    for course in courses:
        canvas_id = int(course["canvas_course_id"])
        if canvas_id <= 0 or canvas_id in seen:
            continue
        seen.add(canvas_id)
        normalized.append({
            "canvas_course_id": canvas_id,
            "name": str(course.get("name") or f"Course {canvas_id}")[:240],
            "code": str(course.get("code") or "")[:120],
            "term": str(course.get("term") or "")[:160],
        })

    now = "NOW()" if _USE_PG else "datetime('now','localtime')"
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        client = _fetchone(
            db,
            "SELECT id, current_semester FROM clients WHERE id = %s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (client_id,),
        )
        if not client:
            raise ValueError("Client not found")
        semester = client.get("current_semester") or ""
        for course in normalized:
            if _USE_PG:
                _exec(db, f"""
                    INSERT INTO student_courses
                        (client_id, canvas_course_id, name, code, term, semester_label, canvas_active, last_synced)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, {now})
                    ON CONFLICT(client_id, canvas_course_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        code = EXCLUDED.code,
                        term = EXCLUDED.term,
                        canvas_active = TRUE,
                        last_synced = {now}
                """, (
                    client_id, course["canvas_course_id"], course["name"],
                    course["code"], course["term"], semester,
                ))
            else:
                _exec(db, f"""
                    INSERT INTO student_courses
                        (client_id, canvas_course_id, name, code, term, semester_label, canvas_active, last_synced)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, {now})
                    ON CONFLICT(client_id, canvas_course_id) DO UPDATE SET
                        name = excluded.name,
                        code = excluded.code,
                        term = excluded.term,
                        canvas_active = 1,
                        last_synced = {now}
                """, (
                    client_id, course["canvas_course_id"], course["name"],
                    course["code"], course["term"], semester,
                ))

        if seen:
            placeholders = ",".join(["%s"] * len(seen))
            archived = _exec(
                db,
                "UPDATE student_courses SET canvas_active = FALSE "
                f"WHERE client_id = %s AND canvas_course_id > 0 AND canvas_active = TRUE "
                f"AND canvas_course_id NOT IN ({placeholders})",
                (client_id, *sorted(seen)),
            ).rowcount
        else:
            archived = _exec(
                db,
                "UPDATE student_courses SET canvas_active = FALSE "
                "WHERE client_id = %s AND canvas_course_id > 0 AND canvas_active = TRUE",
                (client_id,),
            ).rowcount
    return {"saved": len(normalized), "archived": int(archived or 0)}


def claim_focus_phase_rewards(client_id: int, phase_id: str, minutes: int,
                              mode: str = "pomodoro", course_name: str = "",
                              course_id: int | None = None,
                              exam_id: int | None = None,
                              pages: int = 0) -> dict:
    """Atomically claim one verified phase and persist its core rewards.

    The phase row, study-progress row, XP marker, and wallet credit commit in one
    transaction. A per-client lock serializes different phases too, preserving
    the daily cap under concurrent requests.
    """
    phase_id = (phase_id or "").strip()[:80]
    if not phase_id:
        return {"ok": False, "saved": False, "reason": "missing-phase-id"}
    try:
        minutes = max(0, min(FOCUS_MAX_MINUTES_PER_SESSION, int(minutes or 0)))
        pages = max(0, int(pages or 0))
    except (TypeError, ValueError):
        return {"ok": False, "saved": False, "reason": "invalid-phase"}

    with get_db() as db:
        # PostgreSQL row locks and SQLite's immediate write transaction both
        # serialize all reward claims for one account.
        if _USE_PG:
            _exec(db, "SELECT id FROM clients WHERE id = %s FOR UPDATE", (client_id,))
        else:
            db.execute("BEGIN IMMEDIATE")

        row = _fetchone(
            db,
            "SELECT * FROM student_focus_phases WHERE client_id = %s AND phase_id = %s",
            (client_id, phase_id),
        )
        if not row:
            return {"ok": False, "saved": False, "reason": "unverified-phase"}
        if row.get("claimed_at"):
            return {"ok": True, "saved": False, "reason": "duplicate-phase"}

        registered_course = row.get("course_id")
        registered_exam = row.get("exam_id")
        try:
            requested_course = int(course_id or 0) or None
        except (TypeError, ValueError):
            requested_course = None
        try:
            requested_exam = int(exam_id or 0) or None
        except (TypeError, ValueError):
            requested_exam = None
        if registered_course and requested_course and int(registered_course) != requested_course:
            return {"ok": False, "saved": False, "reason": "course-mismatch"}
        if registered_exam and requested_exam and int(registered_exam) != requested_exam:
            return {"ok": False, "saved": False, "reason": "exam-mismatch"}

        expected = int(row.get("expected_minutes") or 0)
        if expected > 0 and minutes > expected + 1:
            return {"ok": False, "saved": False, "reason": "minutes-exceed-phase"}
        started_at = _focus_started_at(row)
        if not started_at:
            return {"ok": False, "saved": False, "reason": "invalid-phase-start"}
        required_minutes = expected if expected > 0 else minutes
        elapsed = (utc_now() - started_at).total_seconds()
        if minutes > 0 and elapsed < max(
            0, required_minutes * 60 - FOCUS_PHASE_CLAIM_GRACE_SECONDS
        ):
            return {"ok": False, "saved": False, "reason": "phase-too-early"}

        course_id, exam_id = _validate_focus_course_exam(
            db, client_id, requested_course or registered_course,
            requested_exam or registered_exam,
        )
        today = user_date(client_id, db=db).isoformat()
        already_today = int(_fetchval(
            db,
            "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress "
            "WHERE client_id = %s AND plan_date LIKE %s",
            (client_id, f"{today}%"),
        ) or 0)
        minutes = min(minutes, max(0, FOCUS_MAX_MINUTES_PER_DAY - already_today))
        if minutes <= 0 and pages <= 0:
            return {"ok": True, "saved": False, "reason": "daily-cap-or-empty"}

        claimed_instant = utc_now()
        claimed_at = claimed_instant.replace(tzinfo=None).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        claimed = _exec(
            db,
            "UPDATE student_focus_phases SET claimed_at = %s, claimed_at_utc = %s "
            "WHERE client_id = %s AND phase_id = %s AND claimed_at IS NULL",
            (claimed_at, claimed_instant.isoformat(), client_id, phase_id),
        )
        if not claimed.rowcount:
            return {"ok": True, "saved": False, "reason": "duplicate-phase"}

        session_id = _insert_returning_id(
            db,
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, pages_read, "
            "course_id, exam_id, recorded_at_utc) "
            "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                client_id,
                local_now(client_id, db=db).strftime("%Y-%m-%d %H:%M:%S.%f"),
                f"{mode}: {course_name}" if course_name else mode,
                minutes,
                pages,
                course_id,
                exam_id,
                claimed_instant.isoformat(),
            ),
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, pages_read, "
            "course_id, exam_id, recorded_at_utc) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
        )

        detail = f"{(mode or 'pomodoro').title()} {minutes}min"
        if course_name:
            detail += f" — {course_name}"
        detail += f" · phase:{phase_id}"

        base_xp = (minutes * 5) // 10
        xp_multiplier = float(_fetchval(
            db,
            "SELECT COALESCE(MAX(multiplier),1.0) FROM student_boosts "
            "WHERE client_id = %s AND kind = 'xp' AND expires_at > %s",
            (client_id, datetime.now()),
        ) or 1.0)
        xp_awarded = int(round(base_xp * max(1.0, xp_multiplier)))
        _exec(
            db,
            "INSERT INTO student_xp "
            "(client_id, action, xp, detail, occurred_at_utc) "
            "VALUES (%s, %s, %s, %s, %s)",
            (client_id, "focus_session", xp_awarded, detail, utc_now().isoformat()),
        )

        base_coins = minutes // 10
        coin_multiplier = float(_fetchval(
            db,
            "SELECT COALESCE(MAX(multiplier),1.0) FROM student_boosts "
            "WHERE client_id = %s AND kind = 'coin' AND expires_at > %s",
            (client_id, datetime.now()),
        ) or 1.0)
        coins_awarded = int(round(base_coins * max(1.0, coin_multiplier)))
        _ensure_wallet(db, client_id)
        if coins_awarded:
            _credit_wallet_with_debt(db, client_id, coins_awarded)

        _progress_focus_quests_in_transaction(db, client_id, minutes, pages)

        return {
            "ok": True,
            "saved": True,
            "reason": "ok",
            "session_id": session_id,
            "minutes_saved": minutes,
            "pages_saved": pages,
            "xp_awarded": xp_awarded,
            "coins_awarded": coins_awarded,
        }


def save_focus_session(client_id: int, mode: str, minutes: int, pages: int,
                       course_name: str = "", course_id: int | None = None,
                       exam_id: int | None = None) -> int:
    """Persist a focus session.
    Returns the new row id, or 0 if the request was rejected for being garbage
    (negative, NaN, or so large that it must be a bug / abandoned timer).

    course_id / exam_id are optional FKs — set when the session is tagged
    to a specific course (and, optionally, a specific exam within that course).
    Validated against ownership here so a malicious client can't tag another
    user's course/exam rows.
    """
    # Validate FK ownership up-front so garbage IDs don't poison stats later.
    if course_id:
        try:
            course_id = int(course_id)
        except (TypeError, ValueError):
            course_id = None
    if exam_id:
        try:
            exam_id = int(exam_id)
        except (TypeError, ValueError):
            exam_id = None
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

    today = user_date(client_id).isoformat()
    with get_db() as db:
        # Validate course ownership.
        if course_id:
            owns = _fetchval(
                db,
                "SELECT 1 FROM student_courses WHERE id = %s AND client_id = %s",
                (course_id, client_id),
            )
            if not owns:
                course_id = None
        # Validate exam ownership + that it belongs to the selected course.
        if exam_id:
            if course_id:
                owns = _fetchval(
                    db,
                    "SELECT 1 FROM student_exams WHERE id = %s AND client_id = %s AND course_id = %s",
                    (exam_id, client_id, course_id),
                )
            else:
                owns = _fetchval(
                    db,
                    "SELECT 1 FROM student_exams WHERE id = %s AND client_id = %s",
                    (exam_id, client_id),
                )
            if not owns:
                exam_id = None

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

        # Microsecond precision in plan_date: student_study_progress has a
        # UNIQUE(client_id, plan_date) constraint, and the batch claim inserts
        # several phases in the same second. Second precision collided on the
        # 2nd+ insert (only one session claimed, the rest errored out with
        # "Reintentar" and no XP). Microseconds keep every row distinct; all
        # date queries match on the YYYY-MM-DD prefix so they're unaffected.
        return _insert_returning_id(
            db,
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, pages_read, "
            "course_id, exam_id, recorded_at_utc) "
            "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, local_now(client_id, db=db).strftime("%Y-%m-%d %H:%M:%S.%f"),
             f"{mode}: {course_name}" if course_name else mode, minutes, pages,
             course_id, exam_id, utc_now().isoformat()),
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, pages_read, "
            "course_id, exam_id, recorded_at_utc) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
        )


def get_focus_session_entry(entry_id: int, client_id: int) -> dict | None:
    """Return one saved focus/progress row owned by a student."""
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT * FROM student_study_progress WHERE id = %s AND client_id = %s",
            (int(entry_id), int(client_id)),
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
    # Use the unified streak (XP-based, with weekly freeze) so every page
    # shows the same Racha number. A focus-only streak diverged from the
    # dashboard/analytics value and confused users.
    streak = get_streak_days(client_id)
    return {
        "total_minutes": total_min,
        "total_hours": round(total_min / 60, 1),
        "total_pages": total_pages,
        "sessions": sessions,
        "streak_days": streak,
    }


def get_focus_stats_today(client_id: int, local_date: str | None = None) -> dict:
    """Focus stats restricted to TODAY (the user's local calendar day).

    Used by the Focus Mode page header so the numbers reflect what the
    student has put in *today*, not their lifetime totals.

    Browser dates are ignored for accounting. The user's saved IANA timezone
    defines the day, preventing stale or forged client dates from moving usage
    across quota and reward boundaries.
    """
    del local_date
    today_str = user_date(client_id).isoformat()
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
    streak = get_streak_days(client_id)
    return {
        "total_minutes": total_min,
        "total_hours": round(total_min / 60, 1),
        "total_pages": total_pages,
        "sessions": sessions,
        "streak_days": streak,
    }


# ── Per-course / per-exam focus-minute aggregations ──────────
#
# These power the dashboard "time per course" chart and its drill-downs
# (course → week, course → exams, exam → week). Each row in
# student_study_progress now carries an optional course_id/exam_id, so the
# joins below are exact — no fragile notes-string parsing.

def get_time_per_course(client_id: int) -> list[dict]:
    """Total focus minutes per course (includes *only* sessions that were
    tagged to a specific course)."""
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT c.id AS course_id, c.name AS course_name, "
            "       COALESCE(SUM(sp.focus_minutes), 0) AS minutes, "
            "       COUNT(sp.id) AS sessions "
            "FROM student_courses c "
            "LEFT JOIN student_study_progress sp ON sp.course_id = c.id "
            "     AND sp.client_id = c.client_id "
            "     AND COALESCE(sp.focus_minutes, 0) > 0 "
            "WHERE c.client_id = %s "
            "GROUP BY c.id, c.name "
            "ORDER BY minutes DESC, c.name ASC",
            (client_id,),
        )
        return [dict(r) for r in rows]


def get_time_per_exam(client_id: int, course_db_id: int) -> list[dict]:
    """Total focus minutes per exam for a single course.
    Returns every exam in the course (minutes = 0 if never studied for)."""
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT e.id AS exam_id, e.name AS exam_name, e.exam_date, "
            "       COALESCE(SUM(sp.focus_minutes), 0) AS minutes, "
            "       COUNT(sp.id) AS sessions "
            "FROM student_exams e "
            "LEFT JOIN student_study_progress sp ON sp.exam_id = e.id "
            "     AND sp.client_id = e.client_id "
            "     AND COALESCE(sp.focus_minutes, 0) > 0 "
            "WHERE e.client_id = %s AND e.course_id = %s "
            "GROUP BY e.id, e.name, e.exam_date "
            "ORDER BY CASE WHEN e.exam_date IS NULL OR e.exam_date = '' THEN 1 ELSE 0 END, "
            "         e.exam_date ASC, e.name ASC",
            (client_id, course_db_id),
        )
        return [dict(r) for r in rows]


def _iso_week_anchor(week_offset: int, client_id: int | None = None) -> tuple:
    """Return (monday_date, sunday_date) for the ISO week offset from today.
    0 = this week, -1 = last week, etc."""
    from datetime import timedelta
    today = user_date(client_id) if client_id is not None else utc_now().date()
    monday = today - timedelta(days=today.weekday())
    anchor = monday + timedelta(weeks=week_offset)
    return anchor, anchor + timedelta(days=6)


def get_course_week(client_id: int, course_db_id: int, week_offset: int = 0) -> dict:
    """Focus minutes per day-of-week for a specific course in a given ISO week.

    Returns {
        "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD",
        "days": [{"date": "YYYY-MM-DD", "dow": "Mon", "minutes": int, "sessions": int}, ...]
    }
    """
    from datetime import timedelta
    monday, sunday = _iso_week_anchor(week_offset, client_id)
    # plan_date is stored as "YYYY-MM-DD HH:MM:SS" for focus rows, so range
    # compare the prefix substring.
    start_str = monday.strftime("%Y-%m-%d") + " 00:00:00"
    end_str = sunday.strftime("%Y-%m-%d") + " 23:59:59"
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT plan_date, COALESCE(focus_minutes, 0) AS mins "
            "FROM student_study_progress "
            "WHERE client_id = %s AND course_id = %s "
            "  AND plan_date >= %s AND plan_date <= %s "
            "  AND COALESCE(focus_minutes, 0) > 0",
            (client_id, course_db_id, start_str, end_str),
        )
    per_day_min = {(monday + timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(7)}
    per_day_sess = {k: 0 for k in per_day_min}
    for r in rows:
        d_str = (r["plan_date"] or "")[:10]
        if d_str in per_day_min:
            per_day_min[d_str] += int(r["mins"] or 0)
            per_day_sess[d_str] += 1
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        days.append({
            "date": ds,
            "dow": dow_names[i],
            "minutes": per_day_min[ds],
            "sessions": per_day_sess[ds],
        })
    return {
        "week_start": monday.strftime("%Y-%m-%d"),
        "week_end": sunday.strftime("%Y-%m-%d"),
        "days": days,
    }

def _course_benchmark_key(name: str = "", code: str = "") -> str:
    raw = (code or "").strip() or (name or "").strip()
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    return key[:80] or "course"


def get_course_outcome(client_id: int, course_id: int) -> dict | None:
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT final_grade, passing_grade, passed, total_focus_minutes, reported_at "
            "FROM student_course_outcomes WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        )
    if not row:
        return None
    d = dict(row)
    d["final_grade"] = float(d.get("final_grade") or 0)
    d["passing_grade"] = float(d.get("passing_grade") or 3.95)
    d["passed"] = bool(d.get("passed"))
    d["total_focus_minutes"] = int(d.get("total_focus_minutes") or 0)
    d["total_focus_hours"] = round(d["total_focus_minutes"] / 60, 1)
    return d


def search_course_reviews(query: str = "", university: str = "",
                          major: str = "", limit: int = 100) -> list[dict]:
    """Search anonymous course reviews using optional case-insensitive filters."""
    clauses = []
    params = []
    query = (query or "").strip()[:120]
    university = (university or "").strip()[:120]
    major = (major or "").strip()[:120]
    if query:
        like = f"%{query.lower()}%"
        clauses.append(
            "(LOWER(course_name) LIKE %s OR LOWER(course_code) LIKE %s "
            "OR LOWER(review_text) LIKE %s)"
        )
        params.extend([like, like, like])
    if university:
        clauses.append("LOWER(university) LIKE %s")
        params.append(f"%{university.lower()}%")
    if major:
        clauses.append("LOWER(major) LIKE %s")
        params.append(f"%{major.lower()}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit = max(1, min(int(limit or 100), 100))
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT id, university, major, course_name, course_code, "
            "difficulty_rating, quality_rating, review_text, final_grade, "
            "total_hours, created_at FROM student_course_reviews"
            + where
            + " ORDER BY created_at DESC, id DESC LIMIT %s",
            tuple(params + [limit]),
        )


def upsert_course_review(client_id: int, course_id: int, university: str,
                         major: str, course_name: str, course_code: str,
                         difficulty_rating: int, quality_rating: int,
                         review_text: str, final_grade, total_hours: int) -> int:
    """Create or replace one student's anonymous review for a completed course."""
    difficulty_rating = max(1, min(5, int(difficulty_rating)))
    quality_rating = max(1, min(5, int(quality_rating)))
    values = (
        client_id,
        course_id,
        (university or "").strip()[:160],
        (major or "").strip()[:160],
        (course_name or "Curso").strip()[:200],
        (course_code or "").strip()[:80],
        difficulty_rating,
        quality_rating,
        (review_text or "").strip()[:2000],
        final_grade,
        max(0, int(total_hours or 0)),
    )
    columns = (
        "client_id, course_id, university, major, course_name, course_code, "
        "difficulty_rating, quality_rating, review_text, final_grade, total_hours"
    )
    update = (
        "university=EXCLUDED.university, major=EXCLUDED.major, "
        "course_name=EXCLUDED.course_name, course_code=EXCLUDED.course_code, "
        "difficulty_rating=EXCLUDED.difficulty_rating, "
        "quality_rating=EXCLUDED.quality_rating, review_text=EXCLUDED.review_text, "
        "final_grade=EXCLUDED.final_grade, total_hours=EXCLUDED.total_hours"
    )
    with get_db() as db:
        if _USE_PG:
            row = _fetchone(
                db,
                f"INSERT INTO student_course_reviews ({columns}) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                f"ON CONFLICT (client_id, course_id) DO UPDATE SET {update} "
                "RETURNING id",
                values,
            )
            return int(row["id"])
        sqlite_update = update.replace("EXCLUDED.", "excluded.")
        _exec(
            db,
            f"INSERT INTO student_course_reviews ({columns}) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            f"ON CONFLICT(client_id, course_id) DO UPDATE SET {sqlite_update}",
            values,
        )
        return int(_fetchval(
            db,
            "SELECT id FROM student_course_reviews WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        ))


def record_course_outcome(client_id: int, course_id: int, final_grade: float, passing_grade: float = 3.95) -> dict:
    try:
        final_grade = round(float(final_grade), 2)
        passing_grade = round(float(passing_grade or 3.95), 2)
    except Exception:
        return {"ok": False, "error": "Ingresa una nota valida."}
    if not (1.0 <= final_grade <= 7.0) or not (1.0 <= passing_grade <= 7.0):
        return {"ok": False, "error": "La nota debe estar entre 1.0 y 7.0."}
    passed = final_grade >= passing_grade
    with get_db() as db:
        course = _fetchone(
            db,
            "SELECT sc.id, sc.name, sc.code, sc.catalog_id, sc.term, "
            "sc.semester_label, c.university_id "
            "FROM student_courses sc JOIN clients c ON c.id = sc.client_id "
            "WHERE sc.id = %s AND sc.client_id = %s",
            (course_id, client_id),
        )
        if not course:
            return {"ok": False, "error": "Course not found."}
        minutes = _fetchval(
            db,
            "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress "
            "WHERE client_id = %s AND course_id = %s",
            (client_id, course_id),
        ) or 0
        key = _course_benchmark_key(course.get("name") or "", course.get("code") or "")
        canonical_id = course.get("catalog_id")
        university_id = course.get("university_id")
        if not canonical_id and university_id:
            canonical = _fetchone(
                db,
                "SELECT id FROM student_course_catalog "
                "WHERE university_id = %s AND code_norm = %s "
                "AND name_norm = %s AND deleted_at IS NULL LIMIT 1",
                (
                    university_id,
                    _catalog_norm(course.get("code") or ""),
                    _catalog_norm(course.get("name") or ""),
                ),
            )
            canonical_id = canonical.get("id") if canonical else None
            if canonical_id:
                _exec(
                    db,
                    "UPDATE student_courses SET catalog_id = %s WHERE id = %s",
                    (canonical_id, course_id),
                )
        cohort_version = (
            course.get("term") or course.get("semester_label") or "unknown"
        )
        if _USE_PG:
            _exec(
                db,
                "INSERT INTO student_course_outcomes "
                "(client_id, course_id, course_key, course_name, course_code, "
                "university_id, canonical_course_id, cohort_version, final_grade, "
                "passing_grade, passed, total_focus_minutes, reported_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP) "
                "ON CONFLICT (client_id, course_id) DO UPDATE SET "
                "course_key=EXCLUDED.course_key, course_name=EXCLUDED.course_name, "
                "course_code=EXCLUDED.course_code, university_id=EXCLUDED.university_id, "
                "canonical_course_id=EXCLUDED.canonical_course_id, "
                "cohort_version=EXCLUDED.cohort_version, final_grade=EXCLUDED.final_grade, "
                "passing_grade=EXCLUDED.passing_grade, passed=EXCLUDED.passed, "
                "total_focus_minutes=EXCLUDED.total_focus_minutes, "
                "reported_at=CURRENT_TIMESTAMP",
                (
                    client_id, course_id, key, course.get("name") or "",
                    course.get("code") or "", university_id, canonical_id,
                    cohort_version, final_grade, passing_grade, bool(passed), int(minutes),
                ),
            )
        else:
            _exec(
                db,
                "INSERT INTO student_course_outcomes "
                "(client_id, course_id, course_key, course_name, course_code, "
                "university_id, canonical_course_id, cohort_version, final_grade, "
                "passing_grade, passed, total_focus_minutes, reported_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,datetime('now')) "
                "ON CONFLICT(client_id, course_id) DO UPDATE SET "
                "course_key=excluded.course_key, course_name=excluded.course_name, "
                "course_code=excluded.course_code, university_id=excluded.university_id, "
                "canonical_course_id=excluded.canonical_course_id, "
                "cohort_version=excluded.cohort_version, final_grade=excluded.final_grade, "
                "passing_grade=excluded.passing_grade, passed=excluded.passed, "
                "total_focus_minutes=excluded.total_focus_minutes, reported_at=datetime('now')",
                (
                    client_id, course_id, key, course.get("name") or "",
                    course.get("code") or "", university_id, canonical_id,
                    cohort_version, final_grade, passing_grade,
                    1 if passed else 0, int(minutes),
                ),
            )
    return {
        "ok": True,
        "final_grade": final_grade,
        "passing_grade": passing_grade,
        "passed": bool(passed),
        "total_focus_minutes": int(minutes),
    }


def get_course_success_benchmark(course_id: int) -> dict:
    with get_db() as db:
        course = _fetchone(
            db,
            "SELECT sc.name, sc.code, sc.catalog_id, sc.term, sc.semester_label, "
            "c.university_id FROM student_courses sc "
            "JOIN clients c ON c.id = sc.client_id WHERE sc.id = %s",
            (course_id,),
        )
        if not course:
            return {"has_data": False}
        key = _course_benchmark_key(course.get("name") or "", course.get("code") or "")
        university_id = course.get("university_id")
        canonical_id = course.get("catalog_id")
        cohort_version = (
            course.get("term") or course.get("semester_label") or "unknown"
        )
        if not university_id or not canonical_id:
            return {
                "has_data": False,
                "suppressed": True,
                "reason": "canonical_course_required",
                "min_required": 5,
                "methodology": (
                    "Benchmarks only combine the same canonical course, "
                    "university, and cohort."
                ),
            }
        if cohort_version == "unknown":
            return {
                "has_data": False,
                "suppressed": True,
                "reason": "cohort_required",
                "min_required": 5,
                "methodology": (
                    "A known course cohort is required before an anonymous "
                    "benchmark can be calculated."
                ),
            }
        row = _fetchone(
            db,
            "SELECT COUNT(*) AS total_reports, "
            "SUM(CASE WHEN passed_flag = 1 THEN 1 ELSE 0 END) AS passed_count, "
            "SUM(CASE WHEN passed_flag = 0 THEN 1 ELSE 0 END) AS failed_count, "
            "COALESCE(AVG(CASE WHEN passed_flag = 1 "
            "THEN total_focus_minutes END),0) AS avg_minutes, "
            "COALESCE(AVG(CASE WHEN passed_flag = 1 "
            "THEN final_grade END),0) AS avg_final_grade, "
            "COALESCE(AVG(final_grade),0) AS avg_all_final_grade, "
            "COALESCE(MIN(CASE WHEN passed_flag = 1 "
            "THEN total_focus_minutes END),0) AS min_minutes, "
            "COALESCE(MAX(CASE WHEN passed_flag = 1 "
            "THEN total_focus_minutes END),0) AS max_minutes "
            "FROM (SELECT client_id, "
            "MAX(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_flag, "
            "AVG(total_focus_minutes) AS total_focus_minutes, "
            "AVG(final_grade) AS final_grade "
            "FROM student_course_outcomes WHERE university_id = %s "
            "AND canonical_course_id = %s AND cohort_version = %s "
            "GROUP BY client_id) AS anonymous_reports",
            (university_id, canonical_id, cohort_version),
        ) or {}
    total_reports = int(row.get("total_reports") or 0)
    if total_reports < 5:
        return {
            "has_data": False,
            "suppressed": True,
            "min_required": 5,
            "total_reports": total_reports,
            "methodology": (
                "Only outcomes for this canonical course, at your university, "
                "in the same cohort are combined. Results remain hidden until "
                "at least 5 students have reported."
            ),
        }
    passed_count = int(row.get("passed_count") or 0)
    failed_count = int(row.get("failed_count") or 0)
    avg_minutes = int(round(float(row.get("avg_minutes") or 0)))
    return {
        "has_data": passed_count > 0 and avg_minutes > 0,
        "course_key": key,
        "university_id": int(university_id),
        "canonical_course_id": int(canonical_id),
        "cohort_version": cohort_version,
        "total_reports": total_reports,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "pass_rate": round((passed_count / total_reports) * 100, 1) if total_reports else 0,
        "avg_minutes": avg_minutes,
        "avg_hours": round(avg_minutes / 60, 1),
        "avg_final_grade": round(float(row.get("avg_final_grade") or 0), 2),
        "avg_all_final_grade": round(float(row.get("avg_all_final_grade") or 0), 2),
        "min_minutes": int(row.get("min_minutes") or 0),
        "max_minutes": int(row.get("max_minutes") or 0),
        "min_required": 5,
        "methodology": (
            "Anonymous aggregate of at least 5 reported outcomes for this "
            "canonical course, at your university, in the same cohort."
        ),
    }


def get_course_outcomes_admin(limit: int = 80) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT course_key, MAX(course_name) AS course_name, MAX(course_code) AS course_code, "
            "COUNT(*) AS reports, "
            "SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_reports, "
            "SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END) AS failed_reports, "
            "COALESCE(AVG(CASE WHEN passed THEN final_grade END),0) AS avg_pass_grade, "
            "COALESCE(AVG(CASE WHEN passed THEN total_focus_minutes END),0) AS avg_pass_minutes "
            "FROM student_course_outcomes GROUP BY course_key "
            "ORDER BY reports DESC, passed_reports DESC, course_name ASC LIMIT %s",
            (limit,),
        ) or []
    out = []
    for r in rows:
        d = dict(r)
        d["avg_pass_minutes"] = int(round(float(d.get("avg_pass_minutes") or 0)))
        d["avg_pass_hours"] = round(d["avg_pass_minutes"] / 60, 1) if d["avg_pass_minutes"] else 0
        d["avg_pass_grade"] = round(float(d.get("avg_pass_grade") or 0), 2)
        out.append(d)
    return out


def get_course_outcome_reports_admin(limit: int = 200) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT o.course_name, o.course_code, c.name AS user_name, c.email AS user_email, "
            "o.final_grade, o.passing_grade, o.passed, o.total_focus_minutes, o.reported_at "
            "FROM student_course_outcomes o "
            "JOIN clients c ON c.id = o.client_id "
            "ORDER BY o.reported_at DESC LIMIT %s",
            (limit,),
        ) or []
    out = []
    for r in rows:
        d = dict(r)
        d["result"] = "Aprobado" if d.get("passed") else "No aprobado"
        d["total_focus_hours"] = round(int(d.get("total_focus_minutes") or 0) / 60, 1)
        out.append(d)
    return out


def get_exam_week(client_id: int, exam_db_id: int, week_offset: int = 0) -> dict:
    """Focus minutes per day-of-week for a specific exam in a given ISO week."""
    from datetime import timedelta
    monday, sunday = _iso_week_anchor(week_offset, client_id)
    start_str = monday.strftime("%Y-%m-%d") + " 00:00:00"
    end_str = sunday.strftime("%Y-%m-%d") + " 23:59:59"
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT plan_date, COALESCE(focus_minutes, 0) AS mins "
            "FROM student_study_progress "
            "WHERE client_id = %s AND exam_id = %s "
            "  AND plan_date >= %s AND plan_date <= %s "
            "  AND COALESCE(focus_minutes, 0) > 0",
            (client_id, exam_db_id, start_str, end_str),
        )
    per_day_min = {(monday + timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(7)}
    per_day_sess = {k: 0 for k in per_day_min}
    for r in rows:
        d_str = (r["plan_date"] or "")[:10]
        if d_str in per_day_min:
            per_day_min[d_str] += int(r["mins"] or 0)
            per_day_sess[d_str] += 1
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        days.append({
            "date": ds,
            "dow": dow_names[i],
            "minutes": per_day_min[ds],
            "sessions": per_day_sess[ds],
        })
    return {
        "week_start": monday.strftime("%Y-%m-%d"),
        "week_end": sunday.strftime("%Y-%m-%d"),
        "days": days,
    }


# ── Schedule settings (per-day availability) ────────────────



def get_schedule_settings(client_id: int) -> list[dict]:
    with get_db() as db:
        rows = _fetchall(
            db, "SELECT * FROM student_schedule_settings WHERE client_id = %s ORDER BY day_of_week",
            (client_id,),
        )
        return [dict(r) for r in rows]


def save_schedule_settings(client_id: int, settings: list[dict]) -> None:
    """Replace the student's weekly availability defaults.

    day_of_week is 0=Monday .. 6=Sunday.
    """
    cleaned: list[tuple[int, float, bool]] = []
    for item in settings or []:
        try:
            day = int(item.get("day_of_week") or 0)
        except Exception:
            continue
        if day < 0 or day > 6:
            continue
        try:
            hours = float(item.get("available_hours") or 0)
        except Exception:
            hours = 0.0
        hours = max(0.0, min(16.0, hours))
        cleaned.append((day, hours, bool(item.get("is_free_day"))))
    with get_db() as db:
        for day, hours, is_free in cleaned:
            _exec(db, "DELETE FROM student_schedule_settings WHERE client_id = %s AND day_of_week = %s",
                  (client_id, day))
            _exec(db,
                  "INSERT INTO student_schedule_settings (client_id, day_of_week, available_hours, is_free_day) "
                  "VALUES (%s, %s, %s, %s)",
                  (client_id, day, hours, is_free))


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
            isoformat = getattr(od, "isoformat", None)
            if callable(isoformat):
                d["override_date"] = str(isoformat())[:10]
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


def export_student_data(client_id: int) -> dict:
    """Return every student-owned data category in a portable JSON shape."""
    direct_tables = {
        "courses": "student_courses",
        "exams": "student_exams",
        "study_plans": "student_study_plans",
        "study_progress": "student_study_progress",
        "focus_phases": "student_focus_phases",
        "course_files": "student_course_files",
        "course_reviews": "student_course_reviews",
        "schedule_settings": "student_schedule_settings",
        "date_overrides": "student_date_overrides",
        "assignment_progress": "student_assignment_progress",
        "notes": "student_notes",
        "xp": "student_xp",
        "badges": "student_badges",
        "email_preferences": "student_email_prefs",
        "streak_freezes": "student_streak_freezes",
        "daily_quests": "student_daily_quests",
        "daily_quest_bundles": "student_daily_quest_bundles",
        "referral_codes": "student_referral_codes",
        "course_outcomes": "student_course_outcomes",
        "planner_done": "student_planner_done",
        "boosts": "student_boosts",
        "coin_pack_orders": "student_coin_pack_orders",
        "leaderboard_prizes": "student_lb_prize",
        "leaderboard_periods_seen": "student_lb_period_seen",
        "leaderboard_email_outbox": "student_lb_email_outbox",
        "subscription_state": "student_subscription_state",
        "promotional_entitlements": "student_promotional_entitlements",
        "reward_state": "student_reward_state",
    }
    data: dict = {}
    with get_db() as db:
        data["pending_referral"] = dict(
            _fetchone(
                db,
                "SELECT code, created_at FROM student_pending_referrals "
                "WHERE referred_id = %s",
                (client_id,),
            )
            or {}
        )
        data["academic_profile"] = dict(_fetchone(
            db,
            "SELECT c.country_iso, c.university_id, u.name AS university_name, "
            "c.major_id, m.name AS major_name, c.current_semester, "
            "c.academic_setup_complete, c.profile_bio "
            "FROM clients c "
            "LEFT JOIN universities u ON u.id = c.university_id "
            "LEFT JOIN majors m ON m.id = c.major_id "
            "WHERE c.id = %s",
            (client_id,),
        ) or {})
        for key, table in direct_tables.items():
            try:
                data[key] = [dict(row) for row in _fetchall(
                    db, f"SELECT * FROM {table} WHERE client_id = %s ORDER BY 1", (client_id,)
                )]
            except Exception:
                data[key] = []

        decks = [dict(row) for row in _fetchall(
            db,
            "SELECT * FROM student_flashcard_decks WHERE client_id = %s ORDER BY id",
            (client_id,),
        )]
        for deck in decks:
            deck["cards"] = [dict(row) for row in _fetchall(
                db,
                "SELECT * FROM student_flashcards WHERE deck_id = %s ORDER BY id",
                (deck["id"],),
            )]
        data["flashcard_decks"] = decks

        quizzes = [dict(row) for row in _fetchall(
            db,
            "SELECT * FROM student_quizzes WHERE client_id = %s ORDER BY id",
            (client_id,),
        )]
        for quiz in quizzes:
            quiz["questions"] = [dict(row) for row in _fetchall(
                db,
                "SELECT * FROM student_quiz_questions WHERE quiz_id = %s ORDER BY sort_order, id",
                (quiz["id"],),
            )]
        data["quizzes"] = quizzes

        data["friends"] = [dict(row) for row in _fetchall(
            db,
            "SELECT * FROM student_friends WHERE client_id = %s OR friend_client_id = %s ORDER BY id",
            (client_id, client_id),
        )]
        data["referrals"] = [dict(row) for row in _fetchall(
            db,
            "SELECT * FROM student_referrals WHERE referrer_id = %s OR referred_id = %s ORDER BY id",
            (client_id, client_id),
        )]
        data["wallet"] = dict(_fetchone(
            db, "SELECT * FROM student_wallet WHERE client_id = %s", (client_id,)
        ) or {"coins": 0, "streak_freezes": 0})
    return data


# ── Cleanup ─────────────────────────────────────────────────



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


def update_flashcard_progress(card_id: int, client_id: int, correct: bool,
                              quality: int | None = None) -> bool:
    """Update flashcard with SM-2 spaced repetition algorithm.
    quality: 0-5 (0=complete blackout, 5=perfect recall)
    If quality is None, use old binary mode (correct=True→4, False→1).
    """
    if quality is None:
        quality = 4 if correct else 1

    with get_db() as db:
        # Fetch current SRS state
        row = _fetchone(
            db,
            "SELECT f.easiness_factor, f.interval_days, f.repetitions, f.deck_id "
            "FROM student_flashcards f "
            "JOIN student_flashcard_decks d ON d.id = f.deck_id "
            "WHERE f.id = %s AND d.client_id = %s",
            (card_id, client_id),
        )
        if not row:
            return False
        ef = float(row.get("easiness_factor") or 2.5)
        interval = int(row.get("interval_days") or 0)
        reps = int(row.get("repetitions") or 0)

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
                  "WHERE id = %s AND deck_id = %s",
                  (round(ef, 2), interval, reps, next_review, card_id, row["deck_id"]))
        else:
            _exec(db,
                  "UPDATE student_flashcards SET times_seen = times_seen + 1, "
                  "easiness_factor = %s, interval_days = %s, repetitions = %s, next_review = %s "
                  "WHERE id = %s AND deck_id = %s",
                  (round(ef, 2), interval, reps, next_review, card_id, row["deck_id"]))
        return True


def delete_flashcard_deck(deck_id: int, client_id: int) -> bool:
    with get_db() as db:
        owned = _fetchone(
            db,
            "SELECT id FROM student_flashcard_decks WHERE id = %s AND client_id = %s",
            (deck_id, client_id),
        )
        if not owned:
            return False
        _exec(db, "DELETE FROM student_flashcards WHERE deck_id = %s", (deck_id,))
        _exec(db, "DELETE FROM student_flashcard_decks WHERE id = %s AND client_id = %s",
              (deck_id, client_id))
        return True


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


def get_quiz_stats_by_exam(client_id: int) -> dict[int, dict]:
    """Return quiz attempt stats grouped by exam.

    Missing exams are intentionally absent. The planner should only penalize
    poor quiz performance when the student has actually attempted quizzes.
    """
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT exam_id, COUNT(*) AS quiz_count, "
            "COALESCE(SUM(attempts), 0) AS attempts, "
            "COALESCE(MAX(best_score), 0) AS best_score, "
            "COALESCE(AVG(CASE WHEN attempts > 0 THEN best_score ELSE NULL END), 0) AS avg_score "
            "FROM student_quizzes "
            "WHERE client_id = %s AND exam_id IS NOT NULL "
            "GROUP BY exam_id",
            (client_id,),
        )
    out: dict[int, dict] = {}
    for r in rows or []:
        d = dict(r)
        try:
            exam_id = int(d.get("exam_id") or 0)
        except Exception:
            continue
        if not exam_id:
            continue
        out[exam_id] = {
            "quiz_count": int(d.get("quiz_count") or 0),
            "attempts": int(d.get("attempts") or 0),
            "best_score": int(d.get("best_score") or 0),
            "avg_score": float(d.get("avg_score") or 0),
        }
    return out


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


def update_quiz_score(quiz_id: int, client_id: int, score: int) -> bool:
    with get_db() as db:
        cur = _exec(
            db,
            "UPDATE student_quizzes SET attempts = attempts + 1, "
            "best_score = CASE WHEN %s > best_score THEN %s ELSE best_score END "
            "WHERE id = %s AND client_id = %s",
            (score, score, quiz_id, client_id),
        )
        return bool(cur.rowcount)


def delete_quiz(quiz_id: int, client_id: int) -> bool:
    with get_db() as db:
        owned = _fetchone(
            db,
            "SELECT id FROM student_quizzes WHERE id = %s AND client_id = %s",
            (quiz_id, client_id),
        )
        if not owned:
            return False
        _exec(db, "DELETE FROM student_quiz_questions WHERE quiz_id = %s", (quiz_id,))
        _exec(db, "DELETE FROM student_quizzes WHERE id = %s AND client_id = %s",
              (quiz_id, client_id))
        return True


# ── Notes ───────────────────────────────────────────────────



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








# ── Chat messages ───────────────────────────────────────────







# ── YouTube imports ─────────────────────────────────────────







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
            "INSERT INTO student_xp "
            "(client_id, action, xp, detail, occurred_at_utc) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, action, xp, detail, utc_now().isoformat()),
            "INSERT INTO student_xp "
            "(client_id, action, xp, detail, occurred_at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
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
    """Count consecutive days with XP activity ending today.

    A user gets ONE auto-applied streak freeze per ISO week: a single missed
    day inside that week does not break the streak. The freeze is recorded
    in `student_streak_freezes` the first time the gap is observed.
    """
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT occurred_at_utc, created_at FROM student_xp "
            "WHERE client_id = %s ORDER BY created_at DESC LIMIT 5000",
            (client_id,),
        )
    if not rows:
        return 0
    today = user_date(client_id)
    timezone_name = get_user_timezone(client_id)
    timezone_info = ZoneInfo(timezone_name)
    # Build set of activity dates
    activity = set()
    for r in rows:
        instant = _parse_focus_dt(r.get("occurred_at_utc") or r.get("created_at"))
        if instant:
            activity.add(instant.astimezone(timezone_info).date())
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
    earn_badge(client_id, "freeze_used")   # "Saved by Ice"
    return True


def get_freeze_status(client_id: int) -> dict:
    """Return whether this ISO week's freeze has been used."""
    iso = user_date(client_id).isocalendar()
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

BADGE_DEFS: dict[str, dict[str, Any]] = {
    "first_login":     {"emoji": "🎉", "name": "Welcome!",        "desc": "Logged in for the first time"},
    "first_quiz":      {"emoji": "📝", "name": "Quiz Rookie",     "desc": "Completed your first quiz"},
    "quiz_master":     {"emoji": "🏆", "name": "Quiz Master",     "desc": "Scored 100% on a quiz"},
    "flashcard_fan":   {"emoji": "🃏", "name": "Flashcard Fan",   "desc": "Reviewed 100 flashcards"},
    "streak_3":        {"emoji": "🔥", "name": "On Fire!",        "desc": "3-day study streak"},
    "streak_7":        {"emoji": "⚡", "name": "Unstoppable",      "desc": "7-day study streak"},
    "streak_30":       {"emoji": "💎", "name": "Diamond Student",  "desc": "30-day study streak"},
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
    "quiz_25":         {"emoji": "🧪", "name": "Quiz Veteran",    "desc": "Completed 25 quizzes"},
    "quiz_50":         {"emoji": "🏅", "name": "Quiz Legend",     "desc": "Completed 50 quizzes"},
    "xp_2500":         {"emoji": "🚀", "name": "Rocket Student",  "desc": "Earned 2,500 XP"},
    "xp_5000":         {"emoji": "👑", "name": "XP King",         "desc": "Earned 5,000 XP"},
    "streak_14":       {"emoji": "🔱", "name": "Two-Week Warrior", "desc": "14-day study streak"},
    "streak_60":       {"emoji": "🏛️", "name": "Iron Will",      "desc": "60-day study streak"},
    "streak_100":      {"emoji": "💯", "name": "The 100 Club",    "desc": "100-day study streak"},
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
    # ── Flashcard mastery ──────────────────────────────────────────
    "deck_builder":    {"emoji": "🏗️", "name": "Deck Architect",  "desc": "Created 10 flashcard decks"},
    # ── Focus mastery ───────────────────────────────────────────────
    "focus_session_4h":{"emoji": "🪨", "name": "Marathon Mind",   "desc": "Completed a single 4-hour focus session"},
    "focus_500h":      {"emoji": "🌌", "name": "Eternal Focus",   "desc": "500 hours of total focus time"},
    "focus_1000h":     {"emoji": "♾️", "name": "Infinity Mind",   "desc": "1,000 hours of total focus time"},
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
    "leap_day":        {"emoji": "🐸", "name": "Leap Year Scholar","desc": "Studied on Feb 29"},
    "new_years":       {"emoji": "🎆", "name": "New Year, New Me","desc": "Studied on January 1st"},
    # ── Legacy social milestones ────────────────────────────────────
    "duel_first":      {"emoji": "⚔️", "name": "Social Spark",    "desc": "Earned a legacy social milestone"},
    "duel_streak_3":   {"emoji": "🔪", "name": "Social Streak",   "desc": "Earned a legacy social milestone"},
    "duel_streak_10":  {"emoji": "🗡️", "name": "Social Run",      "desc": "Earned a legacy social milestone"},
    "duel_25":         {"emoji": "🛡️", "name": "Social Veteran",  "desc": "Earned a legacy social milestone"},
    "duel_100":        {"emoji": "👑", "name": "Social Champion", "desc": "Earned a legacy social milestone"},
    "duel_underdog":   {"emoji": "🐺", "name": "Social Upset",    "desc": "Earned a legacy social milestone"},
    # ── Weekly / monthly payouts ────────────────────────────────────
    # ── Course / academic milestones ────────────────────────────────
    "ten_courses":     {"emoji": "🎒", "name": "Course Hoarder",  "desc": "Added 10 courses"},
    "syllabus_synced": {"emoji": "🔄", "name": "Synced Up",       "desc": "Synced Canvas for the first time"},
    # ── Wallet / economy ────────────────────────────────────────────
    "banner_collector":{"emoji": "🎨", "name": "Banner Collector","desc": "Unlocked 5 banners"},
    "flag_collector":  {"emoji": "🚩", "name": "Flag Collector",  "desc": "Unlocked 5 leaderboard flags"},
    # ── Page & reading ──────────────────────────────────────────────
    "page_2500":       {"emoji": "📙", "name": "Speed Reader",    "desc": "Read 2,500 pages"},
    "page_5000":       {"emoji": "📕", "name": "Library Lord",    "desc": "Read 5,000 pages"},
    # ── Profile / cosmetics ─────────────────────────────────────────
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
    "loyal":           {"emoji": "💝", "name": "Loyal",           "desc": "Account older than 1 year"},
    "od_loyal":        {"emoji": "🗿", "name": "Old Guard",       "desc": "Account older than 2 years"},
    # ── PLUS-only badges ────────────────────────────────────────────
    "plus_member":     {"emoji": "💎", "name": "Plus Member",     "desc": "Active Plus subscription", "plus_only": True},
}

LEVEL_THRESHOLDS = [
    (0, "Iniciado"),
    (100, "Aprendiz"),
    (300, "Estudioso"),
    (600, "Investigador"),
    (1000, "Académico"),
    (2000, "Mente maestra"),
    (5000, "Gran estudioso"),
]


# ── Study Rank System ───────────────────────────────────────
# Tiered ladder inspired by competitive rank systems.
# Each main tier has 4 sub-divisions (IV=lowest, I=highest).
# Elite ranks at the top have no divisions and are rare globally.
STUDY_RANKS = [
    # (xp_floor, "Full rank name", "Short tier", "division or ''", "emoji/color")
    (0,      "Iniciados IV",       "Iniciados",       "IV", "#94A3B8"),
    (50,     "Iniciados III",      "Iniciados",       "III","#94A3B8"),
    (120,    "Iniciados II",       "Iniciados",       "II", "#94A3B8"),
    (200,    "Iniciados I",        "Iniciados",       "I",  "#94A3B8"),
    (300,    "Aprendices IV",      "Aprendices",      "IV", "#A3A380"),
    (450,    "Aprendices III",     "Aprendices",      "III","#A3A380"),
    (650,    "Aprendices II",      "Aprendices",      "II", "#A3A380"),
    (900,    "Aprendices I",       "Aprendices",      "I",  "#A3A380"),
    (1200,   "Estudiosos IV",      "Estudiosos",      "IV", "#10B981"),
    (1600,   "Estudiosos III",     "Estudiosos",      "III","#10B981"),
    (2100,   "Estudiosos II",      "Estudiosos",      "II", "#10B981"),
    (2700,   "Estudiosos I",       "Estudiosos",      "I",  "#10B981"),
    (3400,   "Investigadores IV",  "Investigadores",  "IV", "#3B82F6"),
    (4200,   "Investigadores III", "Investigadores",  "III","#3B82F6"),
    (5100,   "Investigadores II",  "Investigadores",  "II", "#3B82F6"),
    (6100,   "Investigadores I",   "Investigadores",  "I",  "#3B82F6"),
    (7200,   "Académicos IV",      "Académicos",      "IV", "#8B5CF6"),
    (8500,   "Académicos III",     "Académicos",      "III","#8B5CF6"),
    (10000,  "Académicos II",      "Académicos",      "II", "#8B5CF6"),
    (11800,  "Académicos I",       "Académicos",      "I",  "#8B5CF6"),
    (13800,  "Mentes maestras IV", "Mentes maestras", "IV", "#EC4899"),
    (16200,  "Mentes maestras III","Mentes maestras", "III","#EC4899"),
    (18800,  "Mentes maestras II", "Mentes maestras", "II", "#EC4899"),
    (21800,  "Mentes maestras I",  "Mentes maestras", "I",  "#EC4899"),
    (25200,  "Grandes estudiosos IV",  "Grandes estudiosos","IV","#F59E0B"),
    (29000,  "Grandes estudiosos III", "Grandes estudiosos","III","#F59E0B"),
    (33200,  "Grandes estudiosos II",  "Grandes estudiosos","II", "#F59E0B"),
    (37800,  "Grandes estudiosos I",   "Grandes estudiosos","I",  "#F59E0B"),
    (43000,  "Leyendas IV",        "Leyendas",        "IV", "#EF4444"),
    (49000,  "Leyendas III",       "Leyendas",        "III","#EF4444"),
    (55800,  "Leyendas II",        "Leyendas",        "II", "#EF4444"),
    (63500,  "Leyendas I",         "Leyendas",        "I",  "#EF4444"),
    # Elite ranks — no divisions, extremely rare
    (72000,  "Archisabios",        "Archisabios",       "",   "#FBBF24"),
    (90000,  "Grandes sabios",     "Grandes sabios",    "",   "#E879F9"),
    (120000, "Oráculos del conocimiento", "Oráculos", "",   "#22D3EE"),
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
    try:
        from machreach_core.i18n import translate_rank_name

        full = translate_rank_name(full)
        tier = translate_rank_name(tier)
    except Exception:
        pass
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
            try:
                from machreach_core.i18n import translate_rank_name

                return translate_rank_name(LEVEL_THRESHOLDS[i][1]), floor, ceil
            except Exception:
                return LEVEL_THRESHOLDS[i][1], floor, ceil
    try:
        from machreach_core.i18n import translate_rank_name

        return translate_rank_name("Iniciado"), 0, 100
    except Exception:
        return "Iniciado", 0, 100


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


# Threshold ladders: (metric_key, [(minimum, badge_key), ...]). Centralizing
# them here means every event site can just call evaluate_badges() and any
# newly-qualified milestone is awarded (earn_badge is idempotent).
_BADGE_LADDERS = {
    "focus_hours": [(1, "focus_1h"), (10, "focus_10h"), (50, "focus_50h"),
                    (100, "focus_100h"), (500, "focus_500h"), (1000, "focus_1000h")],
    "pages": [(100, "page_100"), (500, "page_500"), (1000, "page_1000"),
              (2500, "page_2500"), (5000, "page_5000")],
    "streak": [(3, "streak_3"), (7, "streak_7"), (14, "streak_14"), (30, "streak_30"),
               (60, "streak_60"), (100, "streak_100"), (180, "streak_180"), (365, "streak_365")],
    "xp": [(100, "xp_100"), (500, "xp_500"), (1000, "xp_1000"), (2500, "xp_2500"),
           (5000, "xp_5000"), (10000, "xp_10000"), (25000, "xp_25000"),
           (50000, "xp_50000"), (100000, "xp_100000")],
    "quizzes": [(1, "first_quiz"), (10, "quiz_10"), (25, "quiz_25"), (50, "quiz_50"),
                (100, "quiz_100"), (500, "quiz_500")],
    "perfect_quizzes": [(1, "quiz_master"), (5, "quiz_perfect_5"), (25, "quiz_perfect_25")],
    "courses": [(1, "first_course"), (5, "five_courses"), (10, "ten_courses")],
    "decks": [(10, "deck_builder")],
}


def evaluate_badges(client_id: int, stats: dict | None = None) -> list[str]:
    """Award every threshold/milestone badge the user now qualifies for.

    Reads cheap totals and runs them through _BADGE_LADDERS. Safe to call after
    any relevant event (focus save, quiz, course add) — idempotent, so
    already-earned badges are skipped. Returns the keys newly earned.
    """
    earned: list[str] = []
    try:
        st = stats or get_focus_stats(client_id)
    except Exception:
        st = {}
    try:
        hrs = float(str(st.get("total_hours") or 0).replace("h", "").replace(",", "") or 0)
    except Exception:
        hrs = 0.0
    metrics = {
        "focus_hours": hrs,
        "pages": int(st.get("total_pages") or 0),
        "streak": int(st.get("streak_days") or 0),
    }
    try:
        with get_db() as db:
            metrics["xp"] = int(_fetchval(db, "SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE client_id = %s", (client_id,)) or 0)
            metrics["quizzes"] = int(_fetchval(db, "SELECT COUNT(*) FROM student_quizzes WHERE client_id = %s AND attempts > 0", (client_id,)) or 0)
            metrics["perfect_quizzes"] = int(_fetchval(db, "SELECT COUNT(*) FROM student_quizzes WHERE client_id = %s AND best_score >= 100", (client_id,)) or 0)
            metrics["courses"] = int(_fetchval(db, "SELECT COUNT(*) FROM student_courses WHERE client_id = %s", (client_id,)) or 0)
            metrics["decks"] = int(_fetchval(db, "SELECT COUNT(*) FROM student_flashcard_decks WHERE client_id = %s", (client_id,)) or 0)
    except Exception:
        pass
    for metric, ladder in _BADGE_LADDERS.items():
        value = metrics.get(metric, 0)
        for minimum, key in ladder:
            if value >= minimum and earn_badge(client_id, key):
                earned.append(key)
    # Account-age loyalty badges.
    try:
        from datetime import datetime
        from machreach_core.db import get_client
        c = get_client(client_id) or {}
        created = c.get("created_at")
        if created:
            cdt = created if hasattr(created, "year") else datetime.fromisoformat(str(created)[:19])
            age_days = (datetime.now() - cdt).days
            if age_days >= 365 and earn_badge(client_id, "loyal"):
                earned.append("loyal")
            if age_days >= 730 and earn_badge(client_id, "od_loyal"):
                earned.append("od_loyal")
    except Exception:
        pass
    return earned


def evaluate_event_badges(client_id: int, session_minutes: int = 0) -> list[str]:
    """Award time-of-day, calendar, single-session and daily-XP badges.

    Call right after a study event with the minutes credited this session.
    """
    from datetime import datetime
    earned: list[str] = []

    def _try(key):
        if earn_badge(client_id, key):
            earned.append(key)

    now = datetime.now()
    h = now.hour

    if session_minutes >= 240:
        _try("focus_session_4h")           # single 4h+ session
    if session_minutes > 0:
        if h < 7:
            _try("early_bird")
        if h == 6:
            _try("sunrise_session")
        if h >= 23:
            _try("night_owl")
        if 2 <= h < 5:
            _try("midnight_oil")           # studying in the small hours
        if now.weekday() >= 5:
            _try("weekend_warrior")        # studied on a weekend
        # Calendar dates
        cal = {(1, 1): "new_years", (2, 14): "valentine", (3, 14): "pi_day",
               (4, 1): "april_fools", (9, 1): "back_to_school", (10, 31): "ghost",
               (2, 29): "leap_day"}
        key = cal.get((now.month, now.day))
        if key:
            _try(key)

    # Daily-XP milestones
    try:
        with get_db() as db:
            if _USE_PG:
                today_xp = int(_fetchval(db, "SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE client_id = %s AND created_at::date = CURRENT_DATE", (client_id,)) or 0)
            else:
                today_xp = int(_fetchval(db, "SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE client_id = %s AND date(created_at) = date('now','localtime')", (client_id,)) or 0)
    except Exception:
        today_xp = 0
    if today_xp >= 500:
        _try("speed_demon")
    if today_xp >= 1000:
        _try("marathoner")
    if today_xp == 121:
        _try("palindrome")

    # 20h of focus within the last 7 days
    try:
        with get_db() as db:
            if _USE_PG:
                week_min = int(_fetchval(db, "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE client_id = %s AND plan_date::date >= CURRENT_DATE - 7", (client_id,)) or 0)
            else:
                week_min = int(_fetchval(db, "SELECT COALESCE(SUM(focus_minutes),0) FROM student_study_progress WHERE client_id = %s AND date(plan_date) >= date('now','localtime','-7 days')", (client_id,)) or 0)
        if week_min >= 1200:
            _try("deep_work_week")
    except Exception:
        pass
    return earned


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


def set_user_timezone(client_id: int, timezone_name: str) -> str:
    """Persist the canonical IANA timezone without touching other settings."""
    from student.periods import normalize_timezone

    timezone_name = normalize_timezone(timezone_name)
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_email_prefs (client_id, timezone) "
            "VALUES (%s, %s) ON CONFLICT(client_id) DO UPDATE "
            "SET timezone=excluded.timezone",
            (client_id, timezone_name),
        )
    return timezone_name


def upsert_email_prefs(client_id: int, daily_email: bool = True, email_hour: int = 7,
                        timezone: str = "America/Santiago",
                        university: str = "", field_of_study: str = "",
                        lang: str = "en"):
    from student.periods import normalize_timezone

    timezone = normalize_timezone(timezone)
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




# ── Weak topics ─────────────────────────────────────────────





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















# ── Personal Leaderboards ──────────────────────────────────

# ── Referral program ───────────────────────────────────────

def set_pending_referral(referred_id: int, code: str) -> None:
    code = (code or "").strip().upper()[:16]
    if not code:
        return
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_pending_referrals (referred_id, code) "
            "VALUES (%s, %s) ON CONFLICT(referred_id) DO UPDATE "
            "SET code=excluded.code",
            (referred_id, code),
        )


def pop_pending_referral(referred_id: int) -> str | None:
    """Atomically consume a pending referral after email verification."""
    with get_db() as db:
        if not _USE_PG:
            db.execute("BEGIN IMMEDIATE")
        row = _fetchone(
            db,
            "SELECT code FROM student_pending_referrals WHERE referred_id = %s"
            + (" FOR UPDATE" if _USE_PG else ""),
            (referred_id,),
        )
        if not row:
            return None
        _exec(
            db,
            "DELETE FROM student_pending_referrals WHERE referred_id = %s",
            (referred_id,),
        )
        return str(row.get("code") or "") or None


def get_or_create_referral_code(client_id: int) -> str:
    """Return this user's stable referral code, creating one on first use."""
    import secrets
    with get_db() as db:
        row = _fetchone(db, "SELECT code FROM student_referral_codes WHERE client_id = %s", (client_id,))
        if row and row.get("code"):
            return row["code"]
        # Generate a short, unambiguous, uppercase code; retry on collision.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
        for _ in range(8):
            code = "".join(secrets.choice(alphabet) for _ in range(7))
            try:
                _exec(db,
                      "INSERT INTO student_referral_codes (client_id, code) VALUES (%s, %s)",
                      (client_id, code))
                return code
            except Exception:
                # Either this client raced and already has one, or code clashed.
                existing = _fetchone(db, "SELECT code FROM student_referral_codes WHERE client_id = %s", (client_id,))
                if existing and existing.get("code"):
                    return existing["code"]
                continue
    return ""


def lookup_referral_owner(code: str) -> int | None:
    """Return the client_id that owns a referral code, or None."""
    code = (code or "").strip().upper()
    if not code:
        return None
    with get_db() as db:
        row = _fetchone(db, "SELECT client_id FROM student_referral_codes WHERE code = %s", (code,))
    return int(row["client_id"]) if row and row.get("client_id") is not None else None


def record_referral(code: str, referrer_id: int, referred_id: int) -> bool:
    """Record that `referred_id` joined via `referrer_id`'s code.

    Returns True only if this is a brand-new redemption (referred_id not seen
    before), so callers grant the reward exactly once.
    """
    with get_db() as db:
        try:
            _exec(db,
                  "INSERT INTO student_referrals (code, referrer_id, referred_id) VALUES (%s, %s, %s)",
                  ((code or "").strip().upper(), referrer_id, referred_id))
            return True
        except Exception:
            return False  # referred_id already redeemed (UNIQUE), or bad ids


def referral_count(client_id: int) -> int:
    """How many users joined with this user's code."""
    with get_db() as db:
        return int(_fetchval(db, "SELECT COUNT(*) FROM student_referrals WHERE referrer_id = %s",
                             (client_id,)) or 0)


def redeem_referral(code: str, referred_id: int) -> int | None:
    """Validate and record a referral for a newly-created user.

    Returns the referrer's client_id (to reward) on a fresh, valid redemption,
    or None if the code is unknown, it's a self-referral, or this user already
    redeemed one. Keeps the guard logic in one testable place.
    """
    owner = lookup_referral_owner(code)
    if not owner or owner == referred_id:
        return None
    if record_referral(code, owner, referred_id):
        return owner
    return None


def has_generated_ai(client_id: int) -> bool:
    """True if the user has ever generated an AI quiz or flashcard set
    (used by the onboarding checklist to detect activation)."""
    with get_db() as db:
        return bool(_fetchval(
            db,
            "SELECT 1 FROM student_xp WHERE client_id = %s "
            "AND action IN ('quiz_generated', 'flashcards_generated') LIMIT 1",
            (client_id,)))


def get_friend_leaderboard(client_id: int) -> list[dict]:
    """Return the current user plus accepted friends ranked by all-time XP."""
    with get_db() as db:
        ids = [client_id] + [
            int(r["friend_client_id"]) for r in _fetchall(
                db,
                "SELECT friend_client_id FROM student_friends "
                "WHERE client_id = %s AND status = 'accepted'",
                (client_id,),
            )
        ]
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        rows = _fetchall(
            db,
            f"SELECT c.id as client_id, COALESCE(c.name, c.email, 'Student') as name, "
            f"COALESCE(SUM(x.xp), 0) as total_xp "
            f"FROM clients c "
            f"LEFT JOIN student_xp x ON x.client_id = c.id "
            f"WHERE c.id IN ({placeholders}) "
            f"GROUP BY c.id, c.name, c.email "
            f"ORDER BY total_xp DESC, name ASC",
            tuple(ids),
        )
        return rows


# ── Note fork tracking (XP for shared notes) ───────────────




# -- Daily Quests --------------------------------------------

QUEST_POOL: list[dict[str, Any]] = [
    {"key": "focus_25",    "label": "Enfócate 25 minutos",            "target": 25, "xp": 10, "metric": "focus_minutes"},
    {"key": "focus_60",    "label": "Enfócate 60 minutos",            "target": 60, "xp": 25, "metric": "focus_minutes"},
    {"key": "session_3",   "label": "Completa 3 sesiones de estudio", "target": 3,  "xp": 20, "metric": "sessions_completed"},
    {"key": "exam_review_15", "label": "15 min repasando para una prueba", "target": 15, "xp": 8, "metric": "focus_minutes"},
]

QUEST_BUNDLE_BONUS_XP = 15
_QUEST_XP_BY_KEY = {q["key"]: int(q.get("xp") or 0) for q in QUEST_POOL}


def _progress_focus_quests_in_transaction(db, client_id: int, minutes: int, pages: int) -> None:
    """Apply focus-session quest progress using the caller's transaction.

    Focus claims are idempotent at the phase row. Keeping the related quest
    writes here prevents a committed phase claim from losing quest credit if
    the request process stops before route-level follow-up work can run.
    """
    import random
    today = user_date(client_id, db=db)
    quest_date = today if _USE_PG else today.isoformat()
    rows = _fetchall(
        db,
        "SELECT * FROM student_daily_quests "
        "WHERE client_id = %s AND quest_date = %s ORDER BY id",
        (client_id, quest_date),
    )
    if not rows:
        picks = random.Random(f"{client_id}-{today.isoformat()}").sample(QUEST_POOL, 3)
        for quest in picks:
            _exec(
                db,
                "INSERT INTO student_daily_quests "
                "(client_id, quest_date, quest_key, target, progress, xp_reward) "
                "VALUES (%s, %s, %s, %s, 0, %s)",
                (
                    client_id,
                    quest_date,
                    quest["key"],
                    quest["target"],
                    quest["xp"],
                ),
            )
        rows = _fetchall(
            db,
            "SELECT * FROM student_daily_quests "
            "WHERE client_id = %s AND quest_date = %s ORDER BY id",
            (client_id, quest_date),
        )

    increments = {
        "focus_minutes": max(0, int(minutes or 0)),
        "sessions_completed": 1 if int(minutes or 0) > 0 else 0,
        "pages_read": max(0, int(pages or 0)),
    }
    newly_completed = False
    now = "CURRENT_TIMESTAMP" if _USE_PG else "datetime('now')"
    for row in rows:
        metric = next(
            (quest["metric"] for quest in QUEST_POOL if quest["key"] == row["quest_key"]),
            "",
        )
        amount = increments.get(metric, 0)
        if amount <= 0 or row.get("completed_at"):
            continue
        target = int(row["target"])
        progress = min(target, int(row.get("progress") or 0) + amount)
        if progress >= target:
            _exec(
                db,
                f"UPDATE student_daily_quests SET progress = %s, completed_at = {now} "
                "WHERE id = %s AND completed_at IS NULL",
                (progress, row["id"]),
            )
            reward = int(
                row.get("xp_reward")
                or _QUEST_XP_BY_KEY.get(row["quest_key"], 0)
                or 0
            )
            if reward:
                _exec(
                    db,
                    "INSERT INTO student_xp "
                    "(client_id, action, xp, detail, occurred_at_utc) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        client_id,
                        "daily_quest",
                        reward,
                        f"Quest completed: {row['quest_key']}",
                        utc_now().isoformat(),
                    ),
                )
            newly_completed = True
        else:
            _exec(
                db,
                "UPDATE student_daily_quests SET progress = %s WHERE id = %s",
                (progress, row["id"]),
            )

    if not newly_completed:
        return
    done_count = int(
        _fetchval(
            db,
            "SELECT COUNT(*) FROM student_daily_quests "
            "WHERE client_id = %s AND quest_date = %s AND completed_at IS NOT NULL",
            (client_id, quest_date),
        )
        or 0
    )
    total = int(
        _fetchval(
            db,
            "SELECT COUNT(*) FROM student_daily_quests "
            "WHERE client_id = %s AND quest_date = %s",
            (client_id, quest_date),
        )
        or 0
    )
    if total < 3 or done_count < total:
        return
    inserted = _exec(
        db,
        "INSERT INTO student_daily_quest_bundles (client_id, quest_date) "
        "SELECT %s, %s WHERE NOT EXISTS ("
        "SELECT 1 FROM student_daily_quest_bundles "
        "WHERE client_id = %s AND quest_date = %s)",
        (client_id, quest_date, client_id, quest_date),
    )
    if inserted.rowcount and QUEST_BUNDLE_BONUS_XP > 0:
        _exec(
            db,
            "INSERT INTO student_xp "
            "(client_id, action, xp, detail, occurred_at_utc) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                client_id,
                "daily_quest_bundle",
                QUEST_BUNDLE_BONUS_XP,
                "Completed all daily missions",
                utc_now().isoformat(),
            ),
        )


def _sync_daily_quest_rewards(client_id: int, quest_date) -> None:
    """Keep already-created daily quests aligned with the current reward table."""
    try:
        with get_db() as db:
            for key, xp in _QUEST_XP_BY_KEY.items():
                _exec(
                    db,
                    "UPDATE student_daily_quests SET xp_reward = %s "
                    "WHERE client_id = %s AND quest_date = %s AND quest_key = %s "
                    "AND (xp_reward IS NULL OR xp_reward <> %s)",
                    (xp, client_id, quest_date, key, xp),
                )
    except Exception:
        pass


def get_or_create_daily_quests(client_id: int) -> list[dict]:
    """Return today's 3 quests, generating them on first call of the day."""
    import random
    today = user_date(client_id)
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
        _sync_daily_quest_rewards(client_id, today if _USE_PG else today_s)
        fixed = []
        for r in rows:
            d = dict(r)
            d["xp_reward"] = _QUEST_XP_BY_KEY.get(d.get("quest_key"), int(d.get("xp_reward") or 0))
            fixed.append(d)
        return fixed
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
    today = user_date(client_id)
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
                    + ("CURRENT_TIMESTAMP" if _USE_PG else "datetime('now')")
                    + " WHERE id = %s",
                    (new_prog, row["id"]),
                )
                reward = int(row.get("xp_reward") or _QUEST_XP_BY_KEY.get(row["quest_key"], 0) or 0)
                if reward > 0:
                    try:
                        award_xp(client_id, "daily_quest", reward, f"Quest completed: {row['quest_key']}")
                    except Exception:
                        pass
                newly_completed.append(dict(row, progress=new_prog, completed_at="just now", xp_reward=reward))
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
                    if QUEST_BUNDLE_BONUS_XP > 0:
                        award_xp(client_id, "daily_quest_bundle", QUEST_BUNDLE_BONUS_XP, "Completed all daily missions")
                except Exception:
                    pass
    return newly_completed


# -- Friends -------------------------------------------------

def search_users(query: str, exclude_client_id: int | None = None, limit: int = 20) -> list[dict]:
    """Search students by name, numeric ID, or exact email without exposing email."""
    q = (query or "").strip()
    if not q:
        return []
    rows: list = []
    with get_db() as db:
        # Numeric ID lookup
        if q.lstrip("#").isdigit():
            cid = int(q.lstrip("#"))
            r = _fetchone(
                db,
                "SELECT id, name FROM clients WHERE id = %s AND account_type = 'student' "
                "AND COALESCE(friend_discovery, TRUE) = TRUE" if _USE_PG else
                "SELECT id, name FROM clients WHERE id = ? AND account_type = 'student' "
                "AND COALESCE(friend_discovery, 1) = 1",
                (cid,),
            )
            if r:
                rows.append(r)
        if "@" in q:
            more = _fetchall(
                db,
                "SELECT id, name FROM clients WHERE account_type = 'student' "
                "AND COALESCE(friend_discovery, TRUE) = TRUE "
                "AND LOWER(email) = LOWER(%s) ORDER BY id ASC LIMIT %s" if _USE_PG else
                "SELECT id, name FROM clients WHERE account_type = 'student' "
                "AND COALESCE(friend_discovery, 1) = 1 "
                "AND LOWER(email) = LOWER(?) ORDER BY id ASC LIMIT ?",
                (q, limit),
            ) or []
        elif len(q) >= 3:
            like = f"%{q}%"
            more = _fetchall(
                db,
                "SELECT id, name FROM clients WHERE account_type = 'student' "
                "AND COALESCE(friend_discovery, TRUE) = TRUE AND name ILIKE %s "
                "ORDER BY id ASC LIMIT %s" if _USE_PG else
                "SELECT id, name FROM clients WHERE account_type = 'student' "
                "AND COALESCE(friend_discovery, 1) = 1 AND name LIKE ? "
                "ORDER BY id ASC LIMIT ?",
                (like, limit),
            ) or []
        else:
            more = []
        rows.extend(more)
    seen = set()
    out = []
    for r in rows:
        rid = r["id"]
        if rid in seen:
            continue
        if exclude_client_id and rid == exclude_client_id:
            continue
        if exclude_client_id and users_blocked(exclude_client_id, int(rid)):
            continue
        seen.add(rid)
        out.append({"id": rid, "name": r.get("name") or ""})
        if len(out) >= limit:
            break
    return out


def users_blocked(client_id: int, other_id: int) -> bool:
    with get_db() as db:
        return bool(_fetchone(
            db,
            "SELECT 1 FROM student_friend_blocks WHERE "
            "(client_id = %s AND blocked_client_id = %s) OR "
            "(client_id = %s AND blocked_client_id = %s) LIMIT 1",
            (client_id, other_id, other_id, client_id),
        ))


def are_friends(client_id: int, other_id: int) -> bool:
    if client_id == other_id:
        return True
    with get_db() as db:
        return bool(_fetchone(
            db,
            "SELECT 1 FROM student_friends WHERE client_id = %s "
            "AND friend_client_id = %s AND status = 'accepted'",
            (client_id, other_id),
        ))


def set_friend_discovery(client_id: int, enabled: bool) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET friend_discovery = %s WHERE id = %s",
            (bool(enabled), client_id),
        )


def get_friend_discovery(client_id: int) -> bool:
    with get_db() as db:
        value = _fetchval(db, "SELECT friend_discovery FROM clients WHERE id = %s", (client_id,))
    return bool(value if value is not None else True)


def add_friend(client_id: int, friend_id: int) -> str:
    """Send/accept a friend request. Returns 'requested', 'accepted', 'already', 'self'."""
    if client_id == friend_id:
        return "self"
    with get_db() as db:
        target = _fetchone(
            db,
            "SELECT id FROM clients WHERE id = %s AND account_type = 'student'",
            (friend_id,),
        )
        if not target:
            return "not_found"
        if _fetchone(
            db,
            "SELECT 1 FROM student_friend_blocks WHERE "
            "(client_id = %s AND blocked_client_id = %s) OR "
            "(client_id = %s AND blocked_client_id = %s) LIMIT 1",
            (client_id, friend_id, friend_id, client_id),
        ):
            return "blocked"
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
            _exec(
                db,
                "INSERT INTO student_friend_audit (actor_id, target_id, action) VALUES (%s, %s, 'accepted')",
                (client_id, friend_id),
            )
            return "accepted"
        if existing:
            return "already"
        cooldown_sql = (
            "created_at > NOW() - INTERVAL '24 hours'"
            if _USE_PG else
            "created_at > datetime('now','localtime','-24 hours')"
        )
        if _fetchone(
            db,
            "SELECT 1 FROM student_friend_audit WHERE actor_id = %s AND target_id = %s "
            "AND action = 'requested' AND " + cooldown_sql + " LIMIT 1",
            (client_id, friend_id),
        ):
            return "cooldown"
        _exec(
            db,
            "INSERT INTO student_friends (client_id, friend_client_id, status) VALUES (%s, %s, 'pending')",
            (client_id, friend_id),
        )
        _exec(
            db,
            "INSERT INTO student_friend_audit (actor_id, target_id, action) VALUES (%s, %s, 'requested')",
            (client_id, friend_id),
        )
        return "requested"


def remove_friend(client_id: int, friend_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
              (client_id, friend_id))
        _exec(db, "DELETE FROM student_friends WHERE client_id = %s AND friend_client_id = %s",
              (friend_id, client_id))
        _exec(
            db,
            "INSERT INTO student_friend_audit (actor_id, target_id, action) VALUES (%s, %s, 'removed')",
            (client_id, friend_id),
        )


def block_student(client_id: int, other_id: int) -> str:
    if client_id == other_id:
        return "self"
    with get_db() as db:
        _exec(
            db,
            "DELETE FROM student_friends WHERE "
            "(client_id = %s AND friend_client_id = %s) OR "
            "(client_id = %s AND friend_client_id = %s)",
            (client_id, other_id, other_id, client_id),
        )
        if _USE_PG:
            _exec(
                db,
                "INSERT INTO student_friend_blocks (client_id, blocked_client_id) "
                "VALUES (%s, %s) ON CONFLICT (client_id, blocked_client_id) DO NOTHING",
                (client_id, other_id),
            )
        else:
            _exec(
                db,
                "INSERT OR IGNORE INTO student_friend_blocks (client_id, blocked_client_id) VALUES (%s, %s)",
                (client_id, other_id),
            )
        _exec(
            db,
            "INSERT INTO student_friend_audit (actor_id, target_id, action) VALUES (%s, %s, 'blocked')",
            (client_id, other_id),
        )
    return "blocked"


def unblock_student(client_id: int, other_id: int) -> bool:
    """Remove only the block created by this student; the other side's block remains."""
    with get_db() as db:
        result = _exec(
            db,
            "DELETE FROM student_friend_blocks WHERE client_id = %s AND blocked_client_id = %s",
            (client_id, other_id),
        )
        if result.rowcount:
            _exec(
                db,
                "INSERT INTO student_friend_audit (actor_id, target_id, action) "
                "VALUES (%s, %s, 'unblocked')",
                (client_id, other_id),
            )
    return bool(result.rowcount)


def report_student(client_id: int, other_id: int, reason: str) -> bool:
    reason = str(reason or "").strip()[:500]
    if client_id == other_id or not reason:
        return False
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_friend_reports (reporter_id, reported_id, reason) VALUES (%s, %s, %s)",
            (client_id, other_id, reason),
        )
        _exec(
            db,
            "INSERT INTO student_friend_audit (actor_id, target_id, action) VALUES (%s, %s, 'reported')",
            (client_id, other_id),
        )
    return True


def list_friends(client_id: int) -> dict:
    """Return {'friends': [...accepted...], 'incoming': [...pending TO me...], 'outgoing': [...pending FROM me...]}.

    Each friend row also includes `last_seen_at` (epoch seconds) and `online`
    (bool — true when last_seen_at is within ONLINE_WINDOW_SEC).
    """
    with get_db() as db:
        accepted = _fetchall(
            db,
            "SELECT c.id, c.name, c.last_seen_at FROM student_friends sf "
            "JOIN clients c ON c.id = sf.friend_client_id "
            "WHERE sf.client_id = %s AND sf.status = 'accepted'",
            (client_id,),
        ) or []
        incoming = _fetchall(
            db,
            "SELECT c.id, c.name, c.last_seen_at FROM student_friends sf "
            "JOIN clients c ON c.id = sf.client_id "
            "WHERE sf.friend_client_id = %s AND sf.status = 'pending'",
            (client_id,),
        ) or []
        outgoing = _fetchall(
            db,
            "SELECT c.id, c.name, c.last_seen_at FROM student_friends sf "
            "JOIN clients c ON c.id = sf.friend_client_id "
            "WHERE sf.client_id = %s AND sf.status = 'pending'",
            (client_id,),
        ) or []

    def _row(r):
        ls = int(r.get("last_seen_at") or 0)
        return {
            "id": r["id"],
            "name": r.get("name") or "",
            "last_seen_at": ls,
            "online": is_online(ls),
        }

    return {
        "friends":  [_row(r) for r in accepted],
        "incoming": [_row(r) for r in incoming],
        "outgoing": [_row(r) for r in outgoing],
    }


# -- Presence (online status) --------------------------------

ONLINE_WINDOW_SEC = 90  # users with activity in last 90s are "online"


def touch_presence(client_id: int) -> None:
    """Mark a user as online RIGHT NOW. Cheap single-row update."""
    import time as _time
    try:
        with get_db() as db:
            _exec(
                db,
                "UPDATE clients SET last_seen_at = %s WHERE id = %s",
                (int(_time.time()), client_id),
            )
    except Exception as e:
        log.debug("touch_presence failed: %s", e)


def is_online(last_seen_at: int | None) -> bool:
    if not last_seen_at:
        return False
    import time as _time
    try:
        return (int(_time.time()) - int(last_seen_at)) < ONLINE_WINDOW_SEC
    except Exception:
        return False


def is_user_online(client_id: int) -> bool:
    try:
        with get_db() as db:
            ls = _fetchval(
                db, "SELECT last_seen_at FROM clients WHERE id = %s", (client_id,),
            )
        return is_online(int(ls or 0))
    except Exception:
        return False


# -- Streak Risk Push (8pm cron) -----------------------------

def get_streak_risk_recipients(min_streak: int = 5) -> list[dict]:
    """Return active students whose streak >= min_streak AND have no XP today.
    Each item: {client_id, email, name, streak}."""
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
        from student.periods import day_window_utc
        start, end = day_window_utc(cid)
        with get_db() as db2:
            today_act = _fetchval(
                db2,
                "SELECT id FROM student_xp WHERE client_id = %s "
                "AND occurred_at_utc >= %s AND occurred_at_utc < %s LIMIT 1",
                (cid, start.isoformat(), end.isoformat()),
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

# Banner catalog: key -> { name, price_coins, xp_required, css, plus_only? }
BANNERS: dict[str, dict[str, Any]] = {
    # ── Defaults + simple solid color sweeps ───────────────────────────
    "default":    {"name": "Default",            "price_coins": 0,    "xp_required": 0,
                   "css": "linear-gradient(135deg,#475569,#1e293b)"},
    "ocean":      {"name": "Ocean Wave",         "price_coins": 50,   "xp_required": 100,
                   "css": "linear-gradient(135deg,#06b6d4,#3b82f6)"},
    "sunset":     {"name": "Sunset",             "price_coins": 50,   "xp_required": 100,
                   "css": "linear-gradient(135deg,#f97316,#ec4899)"},
    "forest":     {"name": "Forest",             "price_coins": 75,   "xp_required": 250,
                   "css": "linear-gradient(135deg,#10b981,#065f46)"},
    "lavender":   {"name": "Lavender Dream",     "price_coins": 100,  "xp_required": 500,
                   "css": "linear-gradient(135deg,#a78bfa,#7c3aed)"},
    "gold":       {"name": "Gold Rush",          "price_coins": 200,  "xp_required": 1000,
                   "css": "linear-gradient(135deg,#facc15,#b45309)"},
    "galaxy":     {"name": "Galaxy",             "price_coins": 300,  "xp_required": 2500,
                   "css": "linear-gradient(135deg,#1e1b4b,#7c3aed,#ec4899)"},
    "neon_rift":  {"name": "Neon Rift",          "price_coins": 450,  "xp_required": 1500,
                   "css": "url('/static/cosmetics/neon-rift-banner.png') center / cover no-repeat"},
    "champion":   {"name": "Champion (Elite)",   "price_coins": 500,  "xp_required": 5000,
                   "css": "linear-gradient(135deg,#f43f5e,#facc15,#10b981)"},
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
    "tropical":   {"name": "Tropical Sunset",    "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#0c4a6e 0%,#1e3a8a 30%,#7c3aed 55%,#ec4899 80%,#f59e0b 100%)"},
    "tundra":     {"name": "Frozen Tundra",      "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#1e3a8a,#2563eb,#bae6fd,#f0f9ff)"},
    "desert":     {"name": "Sahara Dusk",        "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#fde68a 0%,#f59e0b 30%,#b45309 60%,#7c2d12 100%)"},
    "savanna":    {"name": "Savanna Sky",        "price_coins": 320,  "xp_required": 1200,
                   "css": "linear-gradient(180deg,#fbbf24 0%,#f97316 40%,#7c2d12 100%)"},
    "peach":      {"name": "Peach Fizz",         "price_coins": 40,   "xp_required": 50,
                   "css": "linear-gradient(135deg,#fed7aa,#fb923c,#fda4af)"},
    "lagoon":     {"name": "Lagoon",             "price_coins": 40,   "xp_required": 50,
                   "css": "linear-gradient(135deg,#67e8f9,#22d3ee,#0891b2)"},
    "lemon":      {"name": "Lemonade",           "price_coins": 40,   "xp_required": 50,
                   "css": "linear-gradient(135deg,#fef9c3,#facc15,#84cc16)"},
    "berry":      {"name": "Berry Crush",        "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(135deg,#7e22ce,#db2777,#fda4af)"},
    "graphite":   {"name": "Graphite",           "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(135deg,#1f2937,#374151,#9ca3af)"},
    "sky":        {"name": "Abrir Cielo",        "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(180deg,#0ea5e9 0%,#bae6fd 60%,#f0f9ff 100%)"},
    "moss":       {"name": "Moss Stone",         "price_coins": 60,   "xp_required": 100,
                   "css": "linear-gradient(135deg,#1c1917,#3f6212,#84cc16)"},
    "vineyard":   {"name": "Vineyard",           "price_coins": 320,  "xp_required": 1200,
                   "css": "linear-gradient(180deg,#fde047 0%,#84cc16 30%,#15803d 70%,#052e16 100%)"},
    # ── Patterned / themed (kept by name) ─────────────────────────────
    "argyle":     {"name": "Argyle",             "price_coins": 260,  "xp_required": 1000,
                   "css": "repeating-linear-gradient(45deg, rgba(255,255,255,.06) 0 14px, transparent 14px 28px), repeating-linear-gradient(-45deg, rgba(255,255,255,.06) 0 14px, transparent 14px 28px), linear-gradient(135deg,#7c2d12,#facc15)"},
    "lofi_dawn":  {"name": "Lofi Dawn",          "price_coins": 280,  "xp_required": 800,
                   "css": "linear-gradient(180deg,#fb7185 0%,#fda4af 30%,#fbcfe8 55%,#a78bfa 80%,#6366f1 100%)"},
    "cherry":     {"name": "Cherry Blossom (PLUS)", "price_coins": 280, "xp_required": 0, "plus_only": True,
                   "animated": True, "anim_class": "bnr-anim-cherry",
                   "css": "radial-gradient(circle at 20% 30%, #fbcfe8 0%, transparent 30%), radial-gradient(circle at 80% 60%, #f9a8d4 0%, transparent 30%), radial-gradient(2px 3px at 15% 20%, #ec4899 50%, transparent 55%), radial-gradient(3px 2px at 75% 70%, #db2777 50%, transparent 55%), radial-gradient(2px 2px at 45% 40%, #f472b6 50%, transparent 55%), radial-gradient(3px 3px at 90% 25%, #be185d 50%, transparent 55%), linear-gradient(135deg,#fff1f2,#fbcfe8)"},
    # ── Matrix Rain (kept) ────────────────────────────────────────────
    "matrix":     {"name": "Matrix Rain (PLUS)", "price_coins": 800,  "xp_required": 0, "plus_only": True,
                   "animated": True, "anim_class": "bnr-anim-matrix",
                   "css": ("url('data:image/svg+xml;utf8,"
                           "<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2284%22 height=%22480%22>"
                           "<style>.g{font:bold 18px Nunito,sans-serif;fill:%2322c55e}"
                           ".h{font:bold 18px Nunito,sans-serif;fill:%23dcfce7}</style>"
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
    # ── PLUS-only solid sweeps ────────────────────────────────────────
    "plus_chrome":   {"name": "Chrome (PLUS)",   "price_coins": 600,  "xp_required": 0,    "plus_only": True,
                      "css": "linear-gradient(135deg,#f8fafc 0%,#cbd5e1 25%,#475569 55%,#cbd5e1 80%,#f8fafc 100%)"},
    "plus_gold":     {"name": "24K Gold (PLUS)", "price_coins": 900,  "xp_required": 0,    "plus_only": True,
                      "css": "linear-gradient(135deg,#fde68a 0%,#fbbf24 25%,#b45309 50%,#fbbf24 75%,#fde68a 100%)"},
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

/* Monsoon — diagonal rain streaks scrolling */
@keyframes bnr-monsoon { 0% { background-position: 0 0, 0 0; } 100% { background-position: -200px 600px, 0 0; } }
.bnr-anim-monsoon {
  background-size: 100% 100%, 100% 100% !important;
  animation: bnr-monsoon 1.4s linear infinite;
}

/* Molten Core — heat shimmer + slow drift */
@keyframes bnr-molten {
  0%,100% { background-position: 30% 70%, 70% 30%, 0 0; filter: brightness(1) hue-rotate(0deg); }
  50%     { background-position: 35% 65%, 65% 35%, 0 0; filter: brightness(1.2) hue-rotate(8deg); }
}
.bnr-anim-molten {
  background-size: 200% 200%, 200% 200%, 100% 100% !important;
  animation: bnr-molten 7s ease-in-out infinite;
}

/* Glacial — cold breath drifting */
@keyframes bnr-glacial {
  0%,100% { background-position: 30% 30%, 70% 70%, 0 0; filter: brightness(1); }
  50%     { background-position: 35% 28%, 65% 72%, 0 0; filter: brightness(1.12); }
}
.bnr-anim-glacial {
  background-size: 220% 220%, 220% 220%, 100% 100% !important;
  animation: bnr-glacial 12s ease-in-out infinite;
}

/* Supernova — pulsing radial burst */
@keyframes bnr-supernova {
  0%,100% { background-size: 200% 200% !important; filter: brightness(1); }
  50%     { background-size: 240% 240% !important; filter: brightness(1.35); }
}
.bnr-anim-supernova {
  background-size: 200% 200% !important;
  background-position: center center;
  animation: bnr-supernova 6s ease-in-out infinite;
}

/* Zen Garden — gentle horizontal drift */
@keyframes bnr-zen {
  0%,100% { background-position: 30% 50%, 70% 50%, 0 0; }
  50%     { background-position: 33% 50%, 67% 50%, 0 0; }
}
.bnr-anim-zen {
  background-size: 200% 200%, 200% 200%, 100% 100% !important;
  animation: bnr-zen 18s ease-in-out infinite;
}
"""

# Leaderboard flag catalog. Rendered as a horizontal CSS background on the
# leaderboard row, mask-faded from full opacity on the LEFT to transparent on
# the RIGHT. Same structure as banners (re-uses cfg["css"]).
FLAGS: dict[str, dict[str, Any]] = {
    "none":            {"name": "Sin bandera",                  "price_coins": 0,    "xp_required": 0,
                        "css": "transparent"},
    # ── Void Walker family (original + color variants) ─────────────────
    "void":            {"name": "Void Walker",                  "price_coins": 350,  "xp_required": 2500,
                        "css": "linear-gradient(90deg, #000 0%, #6d28d9 60%, #c084fc 100%)"},
    "void_red":  {"name": "Void Walker — Crimson", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #7f1d1d 60%, #fca5a5 100%)"},
    "void_blue":  {"name": "Void Walker — Tide", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #1e3a8a 60%, #93c5fd 100%)"},
    "void_green":  {"name": "Void Walker — Forest", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #14532d 60%, #86efac 100%)"},
    "void_orange":  {"name": "Void Walker — Ember", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #9a3412 60%, #fdba74 100%)"},
    "void_pink":  {"name": "Void Walker — Bloom", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #9d174d 60%, #f9a8d4 100%)"},
    "void_cyan":  {"name": "Void Walker — Glacier", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #155e75 60%, #67e8f9 100%)"},
    "void_yellow":  {"name": "Void Walker — Solar", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #854d0e 60%, #fde047 100%)"},
    "void_emerald":  {"name": "Void Walker — Emerald", "price_coins": 350, "xp_required": 1500,
                  "css": "linear-gradient(90deg, #000 0%, #065f46 60%, #6ee7b7 100%)"},
    # ── Stripes / patterns ────────────────────────────────────────────
    "racing":          {"name": "Racing Stripes",               "price_coins": 200,  "xp_required": 1000,
                        "css": "repeating-linear-gradient(90deg, #ef4444 0 14px, #fafafa 14px 28px)"},
    "racing_anim":     {"name": "Racing Stripes Anim (PLUS)",   "price_coins": 350,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-racing",
                        "css": "repeating-linear-gradient(90deg, #ef4444 0 14px, #fafafa 14px 28px)"},
    "checker":         {"name": "Checker Flag",                 "price_coins": 250,  "xp_required": 1500,
                        "css": "repeating-conic-gradient(#0f172a 0% 25%, #f8fafc 25% 50%) 0 0 / 24px 24px"},
    "checker_anim":    {"name": "Checker Flag (Anim)",          "price_coins": 400,  "xp_required": 1800,
                        "animated": True, "anim_class": "flag-anim-checker",
                        "css": "repeating-conic-gradient(#0f172a 0% 25%, #f8fafc 25% 50%) 0 0 / 24px 24px"},
    "stripes_3":       {"name": "Tricolor",                     "price_coins": 180,  "xp_required": 800,
                        "css": "linear-gradient(90deg, #ef4444 0% 33%, #fafafa 33% 66%, #2563eb 66% 100%)"},
    "stripes_5":       {"name": "Pentaband",                    "price_coins": 220,  "xp_required": 1200,
                        "css": "linear-gradient(90deg, #ef4444 0% 20%, #f59e0b 20% 40%, #facc15 40% 60%, #22c55e 60% 80%, #3b82f6 80% 100%)"},
    "candy":           {"name": "Candy",                        "price_coins": 120,  "xp_required": 400,
                        "css": "repeating-linear-gradient(90deg, #ec4899 0 18px, #fbcfe8 18px 36px)"},
    "argyle_bar":      {"name": "Argyle Bar",                   "price_coins": 250,  "xp_required": 1200,
                        "css": "repeating-linear-gradient(45deg, rgba(255,255,255,.06) 0 12px, transparent 12px 24px), repeating-linear-gradient(-45deg, rgba(255,255,255,.06) 0 12px, transparent 12px 24px), linear-gradient(90deg, #7c2d12, #facc15)"},
    # ── Animated / themed ────────────────────────────────────────────
    "heartbeat":       {"name": "Heartbeat (PLUS)",             "price_coins": 260,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flg-heartbeat",
                        "css": "linear-gradient(90deg, #450a0a 0%, #7f1d1d 25%, #ef4444 55%, #fecaca 85%, #450a0a 100%)"},
    "vortex_spiral":   {"name": "Vortex Spiral",                "price_coins": 300,  "xp_required": 1100,
                        "animated": True, "anim_class": "flag-anim-pan",
                        "css": "radial-gradient(circle at 50% 50%, rgba(251,146,60,.5), rgba(124,58,237,.4) 30%, transparent 60%), linear-gradient(90deg, #1e1b4b 0%, #4c1d95 50%, #1e1b4b 100%)"},
    "polar_lights":    {"name": "Polar Lights",                 "price_coins": 320,  "xp_required": 1300,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #052e16 0%, #16a34a 25%, #22d3ee 50%, #a855f7 75%, #052e16 100%)"},
    "toxic_haze":      {"name": "Toxic Haze (PLUS)",            "price_coins": 340,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-pulse",
                        "css": "radial-gradient(ellipse at 30% 50%, rgba(163,230,53,.6), transparent 55%), linear-gradient(90deg, #14532d 0%, #4d7c0f 50%, #65a30d 100%)"},
    "black_hole":      {"name": "Black Hole",                   "price_coins": 480,  "xp_required": 2200,
                        "css": "radial-gradient(circle at 50% 50%, #000 0 12%, rgba(251,191,36,.4) 14%, rgba(168,85,247,.3) 22%, transparent 45%), linear-gradient(90deg, #020617 0%, #1e1b4b 50%, #000 100%)"},
    "neon_rift":       {"name": "Neon Rift",                    "price_coins": 450,  "xp_required": 1500,
                        "css": "url('/static/cosmetics/neon-rift-flag.png') center / cover no-repeat"},
    "flow_lava":       {"name": "Flow Lava (PLUS)",             "price_coins": 600,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #7c2d12 0%, #ef4444 30%, #fde047 55%, #ef4444 80%, #7c2d12 100%)"},
    "flow_glacier":    {"name": "Flow Glacier (PLUS)",          "price_coins": 600,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #0c4a6e 0%, #38bdf8 35%, #f0f9ff 55%, #38bdf8 75%, #0c4a6e 100%)"},
    "flow_rainbow":    {"name": "Flow Rainbow (PLUS)",          "price_coins": 900,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-fast",
                        "css": "linear-gradient(90deg, #ef4444, #f97316, #facc15, #22c55e, #06b6d4, #6366f1, #a855f7, #ef4444)"},
    "plus_anim_holo":  {"name": "Holo Sweep (PLUS)",            "price_coins": 1200, "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #fbcfe8, #67e8f9, #fde68a, #86efac, #c4b5fd, #fbcfe8)"},
    "plus_anim_solar": {"name": "Solar Flare (PLUS)",           "price_coins": 1500, "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-shimmer",
                        "css": "linear-gradient(90deg, #1c0a04 0%, #dc2626 20%, #fde047 50%, #f97316 80%, #1c0a04 100%)"},
    "plus_prism":      {"name": "Prism Bar (PLUS)",             "price_coins": 1000, "xp_required": 0, "plus_only": True,
                        "css": "linear-gradient(90deg, #ef4444, #f59e0b, #fde047, #84cc16, #06b6d4, #6366f1, #a855f7)"},
    "matrix_rain":     {"name": "Matrix Rain (PLUS)",           "price_coins": 800,  "xp_required": 0, "plus_only": True,
                        "animated": True, "anim_class": "flag-anim-matrix",
                        "css": ("url('data:image/svg+xml;utf8,"
                                "<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2284%22 height=%22120%22>"
                                "<style>.g{font:bold 14px Nunito,sans-serif;fill:%2322c55e}"
                                ".h{font:bold 14px Nunito,sans-serif;fill:%23dcfce7}</style>"
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

/* Racing Stripes (Anim) — vertical stripes scrolling rightward */
@keyframes flg-racing { 0% { background-position: 0 0; } 100% { background-position: 56px 0; } }
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

/* Neon Pulse (Anim) — pulsing saturation */
@keyframes flg-pulse {
  0%,100% { filter: brightness(.9) saturate(1); }
  50%     { filter: brightness(1.3) saturate(1.6); }
}
.flag-anim-pulse { animation: flg-pulse 1.8s ease-in-out infinite; }

/* Scanline — bright line sweeping rightward */
@keyframes flg-scan { 0% { background-position: -200px 0, 0 0; } 100% { background-position: 200px 0, 0 0; } }
.flag-anim-scan {
  background-size: 100% 100%, 100% 100% !important;
  animation: flg-scan 2.4s linear infinite;
}

/* Heartbeat — double-thump pulse (systole/diastole rhythm) */
@keyframes flg-heartbeat {
  0%,  100% { filter: brightness(.92) saturate(1);    transform: scale(1); }
  14%       { filter: brightness(1.35) saturate(1.5); transform: scale(1.03); }
  28%       { filter: brightness(1)    saturate(1.1); transform: scale(1); }
  42%       { filter: brightness(1.25) saturate(1.4); transform: scale(1.02); }
  56%       { filter: brightness(.96)  saturate(1);   transform: scale(1); }
}
.flg-heartbeat { animation: flg-heartbeat 1.2s ease-in-out infinite; transform-origin: center; }
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
BUNDLES: dict[str, dict[str, Any]] = {
    "ocean": {
        "name": "Ocean Pack",
        "desc": "Ocean Wave banner + Void Walker Tide flag — sail in style.",
        "banner": "ocean",
        "flag": "void_blue",
    },
    "forest": {
        "name": "Forest Pack",
        "desc": "Forest banner + Void Walker Forest flag — deep-green focus.",
        "banner": "forest",
        "flag": "void_green",
    },
    "sunset": {
        "name": "Sunset Pack",
        "desc": "Sunset banner + Void Walker Ember flag — warm vibes.",
        "banner": "sunset",
        "flag": "void_orange",
    },
    "matrix": {
        "name": "Matrix Pack",
        "desc": "Matrix Rain banner + Matrix Rain flag — hacker mode (PLUS).",
        "banner": "matrix",
        "flag": "matrix_rain",
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
FREE_STREAK_FREEZE_CAP = 3
PAID_STREAK_FREEZE_CAP = 5


def _streak_freeze_cap(client_id: int) -> int:
    try:
        from student.subscription import get_tier
        if get_tier(client_id) == "plus":
            return PAID_STREAK_FREEZE_CAP
    except Exception:
        pass
    return FREE_STREAK_FREEZE_CAP

# ── Coin microtransactions ──────────────────────────────────────────
# Real-money packs that credit virtual coins. Prices are in USD; the actual
# payment processor (Lemon Squeezy one-time orders) is wired in routes.py.
COIN_PACKS: dict[str, dict[str, Any]] = {
    "small":  {"name": "Pocket Pack",    "coins": 250,   "bonus": 0,    "price_clp": 990,   "tag": ""},
    "medium": {"name": "Stash Pack",     "coins": 800,   "bonus": 50,   "price_clp": 2990,  "tag": "+6%"},
    "large":  {"name": "Vault Pack",     "coins": 2000,  "bonus": 300,  "price_clp": 6990,  "tag": "+15%"},
    "mega":   {"name": "Treasury Pack",  "coins": 5500,  "bonus": 1000, "price_clp": 14990, "tag": "+18% \u2022 BEST VALUE"},
    "ultra":  {"name": "Whale Pack",     "coins": 15000, "bonus": 4000, "price_clp": 34990, "tag": "+27%"},
}

# Timed boosts. Each entry: (label, multiplier, hours, price_coins)
BOOSTS: dict[str, dict[str, Any]] = {
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
        "coin_debt INTEGER NOT NULL DEFAULT 0, "
        "streak_freezes INTEGER DEFAULT 0, "
        "selected_banner TEXT DEFAULT 'default', "
        "unlocked_banners TEXT DEFAULT '[\"default\"]', "
        "updated_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS student_wallet ("
        "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, "
        "coins INTEGER DEFAULT 0, "
        "coin_debt INTEGER NOT NULL DEFAULT 0, "
        "streak_freezes INTEGER DEFAULT 0, "
        "selected_banner TEXT DEFAULT 'default', "
        "unlocked_banners TEXT DEFAULT '[\"default\"]', "
        "updated_at TIMESTAMP DEFAULT (datetime('now','localtime')))",
    )
    for column, definition in (("coin_debt", "INTEGER NOT NULL DEFAULT 0"),):
        try:
            with get_db() as db:
                _exec(db, f"ALTER TABLE student_wallet ADD COLUMN {column} {definition}")
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise


def init_coin_pack_orders_table() -> None:
    """Create the idempotency ledger for real-money coin purchases."""
    _create_table_safe(
        "CREATE TABLE IF NOT EXISTS student_coin_pack_orders ("
        "id SERIAL PRIMARY KEY, "
        "provider TEXT NOT NULL DEFAULT 'lemonsqueezy', "
        "order_id TEXT NOT NULL, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "pack_key TEXT NOT NULL, "
        "coins_credited INTEGER NOT NULL DEFAULT 0, "
        "coins_reversed INTEGER NOT NULL DEFAULT 0, "
        "refunded_amount INTEGER NOT NULL DEFAULT 0, "
        "provider_total INTEGER NOT NULL DEFAULT 0, "
        "refunded_at TIMESTAMP, "
        "created_at TIMESTAMP DEFAULT NOW(), "
        "UNIQUE(provider, order_id))",
        "CREATE TABLE IF NOT EXISTS student_coin_pack_orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "provider TEXT NOT NULL DEFAULT 'lemonsqueezy', "
        "order_id TEXT NOT NULL, "
        "client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, "
        "pack_key TEXT NOT NULL, "
        "coins_credited INTEGER NOT NULL DEFAULT 0, "
        "coins_reversed INTEGER NOT NULL DEFAULT 0, "
        "refunded_amount INTEGER NOT NULL DEFAULT 0, "
        "provider_total INTEGER NOT NULL DEFAULT 0, "
        "refunded_at TEXT, "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "UNIQUE(provider, order_id))",
    )
    for column, definition in (
        ("coins_reversed", "INTEGER NOT NULL DEFAULT 0"),
        ("refunded_amount", "INTEGER NOT NULL DEFAULT 0"),
        ("provider_total", "INTEGER NOT NULL DEFAULT 0"),
        ("refunded_at", "TIMESTAMP DEFAULT NULL"),
    ):
        try:
            with get_db() as db:
                _exec(
                    db,
                    f"ALTER TABLE student_coin_pack_orders ADD COLUMN {column} {definition}",
                )
        except Exception as exc:
            if not _is_duplicate_column_error(exc):
                raise
    _create_table_safe(
        "CREATE INDEX IF NOT EXISTS idx_coin_pack_orders_client ON student_coin_pack_orders(client_id)",
        "CREATE INDEX IF NOT EXISTS idx_coin_pack_orders_client ON student_coin_pack_orders(client_id)",
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


def _lock_wallet(db, client_id: int) -> dict:
    """Return a wallet row while serializing purchases for this account."""
    if not _USE_PG:
        db.execute("BEGIN IMMEDIATE")
    _ensure_wallet(db, client_id)
    suffix = " FOR UPDATE" if _USE_PG else ""
    return _fetchone(
        db,
        "SELECT * FROM student_wallet WHERE client_id = %s" + suffix,
        (client_id,),
    )


def _grant_weekly_free_freeze(db, client_id: int) -> None:
    """Atomically claim one weekly freeze grant in normalized reward state."""
    try:
        now = utc_now()
        cutoff = now - timedelta(days=7)
        _exec(
            db,
            "INSERT INTO student_reward_state (client_id) VALUES (%s) "
            "ON CONFLICT(client_id) DO NOTHING",
            (client_id,),
        )
        claimed = _exec(
            db,
            "UPDATE student_reward_state SET weekly_freeze_granted_at = %s, "
            "updated_at = %s WHERE client_id = %s AND "
            "(weekly_freeze_granted_at IS NULL OR weekly_freeze_granted_at <= %s)",
            (now.isoformat(), now.isoformat(), client_id, cutoff.isoformat()),
        )
        if not claimed.rowcount:
            return
        # Grant if under cap. Paid plans can hold a few extra freezes through
        # Streak Insurance+.
        cur = _fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,)) or 0
        if int(cur) < _streak_freeze_cap(client_id):
            _exec(db, "UPDATE student_wallet SET streak_freezes = streak_freezes + 1 WHERE client_id = %s",
                  (client_id,))
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
    return {
        "coins":            int(row.get("coins") or 0),
        "coin_debt":        int(row.get("coin_debt") or 0),
        "streak_freezes":   int(row.get("streak_freezes") or 0),
        "selected_banner":  row.get("selected_banner") or "default",
        "unlocked_banners": unlocked,
    }


def _credit_wallet_with_debt(db, client_id: int, amount: int) -> int:
    """Apply a coin change, using positive credits to repay refund debt first."""
    amount = int(amount)
    if amount > 0:
        greatest = "GREATEST" if _USE_PG else "MAX"
        _exec(
            db,
            f"UPDATE student_wallet SET "
            f"coins = coins + {greatest}(%s - coin_debt, 0), "
            f"coin_debt = {greatest}(coin_debt - %s, 0) "
            "WHERE client_id = %s",
            (amount, amount, client_id),
        )
    elif amount < 0:
        _exec(
            db,
            "UPDATE student_wallet SET coins = coins + %s WHERE client_id = %s",
            (amount, client_id),
        )
    return int(
        _fetchval(
            db, "SELECT coins FROM student_wallet WHERE client_id = %s", (client_id,)
        )
        or 0
    )


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
        return _credit_wallet_with_debt(db, client_id, int(amount))


def credit_coin_pack(client_id: int, pack_key: str, order_id: str | None = None) -> dict:
    """Credit coins for a real-money pack purchase. Bypasses 2x-coin boost
    (real-money purchases shouldn't double-stack with timed boosts).

    When `order_id` is supplied, the LS order is first written to a unique
    ledger row inside the same DB transaction. Duplicate webhook retries then
    no-op instead of crediting coins again.
    """
    cfg = COIN_PACKS.get(pack_key)
    if not cfg:
        return {"ok": False, "error": "Unknown coin pack."}
    total = int(cfg["coins"]) + int(cfg.get("bonus") or 0)
    order_id = str(order_id or "").strip()
    with get_db() as db:
        _ensure_wallet(db, client_id)
        if order_id:
            if _USE_PG:
                cur = _exec(
                    db,
                    "INSERT INTO student_coin_pack_orders "
                    "(provider, order_id, client_id, pack_key, coins_credited) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (provider, order_id) DO NOTHING",
                    ("lemonsqueezy", order_id, client_id, pack_key, total),
                )
            else:
                cur = _exec(
                    db,
                    "INSERT OR IGNORE INTO student_coin_pack_orders "
                    "(provider, order_id, client_id, pack_key, coins_credited) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("lemonsqueezy", order_id, client_id, pack_key, total),
                )
            if getattr(cur, "rowcount", 0) == 0:
                new_balance = _fetchval(db, "SELECT coins FROM student_wallet WHERE client_id = %s", (client_id,)) or 0
                return {"ok": True, "credited": 0, "coins": new_balance, "pack": pack_key, "duplicate": True}
        new_balance = _credit_wallet_with_debt(db, client_id, total)
    return {"ok": True, "credited": total, "coins": new_balance, "pack": pack_key, "duplicate": False}


def refund_coin_pack(
    client_id: int,
    order_id: str,
    *,
    refunded_amount: int = 0,
    provider_total: int = 0,
    fully_refunded: bool = False,
) -> dict:
    """Reverse a cumulative provider refund without allowing negative wallets.

    Partial refunds reverse the same proportion of the pack. Any shortfall from
    already-spent coins is stored as debt and consumed by future coin credits.
    """
    order_id = str(order_id or "").strip()
    if not order_id:
        return {"ok": False, "error": "Missing order id."}
    refunded_amount = max(0, int(refunded_amount or 0))
    provider_total = max(0, int(provider_total or 0))
    with get_db() as db:
        wallet = _lock_wallet(db, client_id)
        suffix = " FOR UPDATE" if _USE_PG else ""
        order = _fetchone(
            db,
            "SELECT * FROM student_coin_pack_orders "
            "WHERE provider = %s AND order_id = %s" + suffix,
            ("lemonsqueezy", order_id),
        )
        if not order or int(order.get("client_id") or 0) != int(client_id):
            return {"ok": False, "error": "Coin order not found."}
        credited = int(order.get("coins_credited") or 0)
        already_reversed = int(order.get("coins_reversed") or 0)
        if fully_refunded:
            calculated_target = credited
        elif provider_total > 0:
            calculated_target = min(
                credited, (credited * min(refunded_amount, provider_total)) // provider_total
            )
        else:
            calculated_target = already_reversed
        target_reversed = max(already_reversed, calculated_target)
        reverse_now = max(0, target_reversed - already_reversed)
        if reverse_now:
            balance = int(wallet.get("coins") or 0)
            from_balance = min(balance, reverse_now)
            debt_added = reverse_now - from_balance
            _exec(
                db,
                "UPDATE student_wallet SET coins = coins - %s, coin_debt = coin_debt + %s "
                "WHERE client_id = %s",
                (from_balance, debt_added, client_id),
            )
        _exec(
            db,
            "UPDATE student_coin_pack_orders SET coins_reversed = %s, "
            "refunded_amount = %s, provider_total = %s, "
            "refunded_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE refunded_at END "
            "WHERE provider = %s AND order_id = %s",
            (
                target_reversed,
                max(int(order.get("refunded_amount") or 0), refunded_amount),
                max(int(order.get("provider_total") or 0), provider_total),
                bool(fully_refunded),
                "lemonsqueezy",
                order_id,
            ),
        )
        updated = _fetchone(
            db,
            "SELECT coins, coin_debt FROM student_wallet WHERE client_id = %s",
            (client_id,),
        )
    return {
        "ok": True,
        "reversed": reverse_now,
        "coins": int(updated.get("coins") or 0),
        "coin_debt": int(updated.get("coin_debt") or 0),
        "duplicate": reverse_now == 0,
    }


def buy_streak_freeze(client_id: int, qty: int = 1, bundle: bool = False) -> dict:
    """Spend coins to buy `qty` streak freezes.
    If `bundle=True` and qty==STREAK_FREEZE_BUNDLE_QTY, use the discounted
    bundle price."""
    qty = max(1, min(3, int(qty)))
    if bundle and qty == STREAK_FREEZE_BUNDLE_QTY:
        cost = STREAK_FREEZE_BUNDLE_PRICE
    else:
        cost = STREAK_FREEZE_PRICE * qty
    cap = _streak_freeze_cap(client_id)
    with get_db() as db:
        _ensure_wallet(db, client_id)
        changed = _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, "
            "streak_freezes = streak_freezes + %s "
            "WHERE client_id = %s AND coins >= %s AND streak_freezes + %s <= %s",
            (cost, qty, client_id, cost, qty, cap),
        )
        new = _fetchone(db, "SELECT coins, streak_freezes FROM student_wallet WHERE client_id = %s", (client_id,))
        if not changed.rowcount:
            if int(new.get("streak_freezes") or 0) + qty > cap:
                return {"ok": False, "error": f"Puedes guardar máximo {cap} congeladores de racha a la vez."}
            return {"ok": False, "error": "Not enough coins."}
    return {"ok": True, "coins": int(new.get("coins") or 0), "streak_freezes": int(new.get("streak_freezes") or 0)}


def _is_plus_user(client_id: int) -> bool:
    """True if user can access PLUS-only cosmetics."""
    try:
        from student.subscription import get_tier  # type: ignore
        return get_tier(client_id) == "plus"
    except Exception:
        return False


def buy_banner(client_id: int, banner_key: str) -> dict:
    """Buy a banner. Requires both XP threshold and coins."""
    cfg = BANNERS.get(banner_key)
    if not cfg:
        return {"ok": False, "error": "Unknown banner."}
    if cfg.get("plus_only") and not _is_plus_user(client_id):
        return {"ok": False, "error": "Plus subscription required."}
    with get_db() as db:
        row = _lock_wallet(db, client_id)
        total_xp = int(_fetchval(
            db,
            "SELECT COALESCE(SUM(xp), 0) FROM student_xp WHERE client_id = %s",
            (client_id,),
        ) or 0)
        if total_xp < cfg["xp_required"]:
            return {"ok": False, "error": f"Need {cfg['xp_required']} XP to unlock this banner."}
        try:
            unlocked = _json.loads(row.get("unlocked_banners") or '["default"]')
        except Exception:
            unlocked = ["default"]
        if banner_key in unlocked:
            return {"ok": False, "error": "Already owned."}
        coins = int(row.get("coins") or 0)
        if coins < cfg["price_coins"]:
            return {"ok": False, "error": "Not enough coins."}
        new_unlocked = list(unlocked) + [banner_key]
        _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, unlocked_banners = %s "
            "WHERE client_id = %s AND coins >= %s",
            (cfg["price_coins"], _json.dumps(new_unlocked), client_id,
             cfg["price_coins"]),
        )
    # "Banner Collector": 5+ banners unlocked (excludes the free default).
    if len([b for b in new_unlocked if b != "default"]) >= 5:
        earn_badge(client_id, "banner_collector")
    return {"ok": True, "coins": coins - cfg["price_coins"], "unlocked_banners": new_unlocked}


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
    with get_db() as db:
        wallet = _lock_wallet(db, client_id)
        total_xp = int(_fetchval(
            db,
            "SELECT COALESCE(SUM(xp), 0) FROM student_xp WHERE client_id = %s",
            (client_id,),
        ) or 0)
        if total_xp < cfg["xp_required"]:
            return {"ok": False, "error": f"Need {cfg['xp_required']} XP to unlock this flag."}
        prefs = _load_flag_prefs(db, client_id)
        unlocked = prefs.get("unlocked_flags") or ["none"]
        if not isinstance(unlocked, list):
            unlocked = ["none"]
        if flag_key in unlocked:
            return {"ok": False, "error": "Already owned."}
        coins = int(wallet.get("coins") or 0)
        if coins < cfg["price_coins"]:
            return {"ok": False, "error": "Not enough coins."}
        new_unlocked = list(unlocked) + [flag_key]
        changed = _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s "
            "WHERE client_id = %s AND coins >= %s",
            (cfg["price_coins"], client_id, cfg["price_coins"]),
        )
        if not changed.rowcount:
            return {"ok": False, "error": "Not enough coins."}
        prefs["unlocked_flags"] = new_unlocked
        _save_flag_prefs(db, client_id, prefs)
    # "Flag Collector": 5+ flags unlocked (excludes the free "none").
    if len([f for f in new_unlocked if f != "none"]) >= 5:
        earn_badge(client_id, "flag_collector")
    return {"ok": True, "coins": coins - cfg["price_coins"], "unlocked_flags": new_unlocked}


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
    if not isinstance(left, str):
        left = ""
    if not isinstance(right, str):
        right = ""
    if left and left not in BADGE_DEFS:
        left = ""
    if right and right not in BADGE_DEFS:
        right = ""
    return left, right




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
    if badge_key:
        earn_badge(client_id, "identity")   # "True Self": equipped a leaderboard badge
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
        entry: dict[str, dict[str, Any] | None] = {"left": None, "right": None}
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
    with get_db() as db:
        wallet = _lock_wallet(db, client_id)
        try:
            current_banners = _json.loads(wallet.get("unlocked_banners") or '["default"]')
        except Exception:
            current_banners = ["default"]
        prefs = _load_flag_prefs(db, client_id)
        current_flags = prefs.get("unlocked_flags") or ["none"]
        if not isinstance(current_flags, list):
            current_flags = ["none"]
        if bnr_key in current_banners and flg_key in current_flags:
            return {"ok": False, "error": "Already owned."}
        coins = int(wallet.get("coins") or 0)
        if coins < price:
            return {"ok": False, "error": f"Need {price} coins (have {coins})."}
        new_banners = list(current_banners)
        if bnr_key not in new_banners:
            new_banners.append(bnr_key)
        new_flags = list(current_flags)
        if flg_key not in new_flags:
            new_flags.append(flg_key)
        changed = _exec(
            db,
            "UPDATE student_wallet SET coins = coins - %s, unlocked_banners = %s "
            "WHERE client_id = %s AND coins >= %s",
            (price, _json.dumps(new_banners), client_id, price),
        )
        if not changed.rowcount:
            return {"ok": False, "error": "Not enough coins."}
        prefs["unlocked_flags"] = new_flags
        _save_flag_prefs(db, client_id, prefs)
    return {
        "ok": True, "bundle": bundle_key, "spent": price,
        "coins": coins - price,
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
    yesterday_day = user_date(client_id) - _td(days=1)
    yesterday = yesterday_day.isoformat()
    recorded_at = (
        datetime.combine(yesterday_day, datetime.min.time())
        .replace(hour=12, tzinfo=ZoneInfo(get_user_timezone(client_id)))
        .astimezone(UTC)
    )
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
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, "
            "pages_read, recorded_at_utc) "
            "VALUES (%s, %s, 1, %s, %s, %s, %s) RETURNING id",
            (
                client_id,
                yesterday + " 12:00:00",
                "[streak freeze]",
                1,
                0,
                recorded_at.isoformat(),
            ),
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, completed, notes, focus_minutes, "
            "pages_read, recorded_at_utc) VALUES (?, ?, 1, ?, ?, ?, ?)",
        )
        _exec(db, "UPDATE student_wallet SET streak_freezes = streak_freezes - 1 WHERE client_id = %s",
              (client_id,))
        new_owned = _fetchval(db, "SELECT streak_freezes FROM student_wallet WHERE client_id = %s",
                              (client_id,)) or 0
    earn_badge(client_id, "freeze_used")   # "Saved by Ice"
    return {"ok": True, "streak_freezes": new_owned}


# ─────────────────────────────────────────────────────────────
# Removed market feature tables can remain in old databases, but the app no longer creates, reads, or exposes them.
