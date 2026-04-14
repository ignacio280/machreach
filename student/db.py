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
        _exec(db, "DELETE FROM student_assignment_progress WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_schedule_settings WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_study_progress WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_study_plans WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_exams WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_course_files WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_courses WHERE client_id = %s", (client_id,))
        _exec(db, "DELETE FROM student_canvas_tokens WHERE client_id = %s", (client_id,))
