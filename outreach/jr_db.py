"""MachReach JR — v2 normalized persistence on the university stack.

Replaces the JR prototype's one-JSON-blob-per-school model (which let any
student read everyone's plaintext passwords and did last-write-wins). JR
accounts are ordinary `clients` rows (so they reuse university auth, sessions,
CSRF, email verification, password reset). This module adds the JR-specific,
per-school, per-entity tables on top.

Tenancy anchor: `jr_members` links a `clients.id` to a `jr_schools.id` with a
role. Every read/write is scoped through a member, so a user only ever touches
their own school's data (and, by role, only their slice of it). There is no
endpoint that returns a whole school's blob.

Reuses outreach.db's engine-agnostic helpers (_USE_PG / get_db / _exec /
_fetchone / _fetchall / _fetchval / _insert_returning_id) — same Postgres
(prod) / SQLite (local dev) fallback as the rest of the app.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

import bcrypt

from outreach.db import (
    _USE_PG,
    get_db,
    _exec,
    _fetchone,
    _fetchall,
    _fetchval,
    _insert_returning_id,
    _now_expr,
    create_client,
    get_client_by_email,
)

JR_ROLES = ("admin", "teacher", "student", "parent")

# How long an emailed invite link stays valid.
INVITE_TTL_HOURS = 72


def hash_password(pw: str) -> str:
    """bcrypt hash matching the university app's _hash_pw (cost 12), so a
    JR-provisioned account's password verifies through the shared /login."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Two dialects because PG uses SERIAL/TIMESTAMP/NOW() and SQLite uses
# AUTOINCREMENT/TEXT. Mirrors the split already in outreach/db.py.

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS jr_schools (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    plan           TEXT NOT NULL DEFAULT 'pilot',
    seat_cap       INTEGER NOT NULL DEFAULT 0,
    contract_start TEXT DEFAULT '',
    contract_end   TEXT DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jr_members (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    grade       TEXT DEFAULT '',
    section     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (client_id, school_id)
);
CREATE INDEX IF NOT EXISTS idx_jr_members_school ON jr_members(school_id);
CREATE INDEX IF NOT EXISTS idx_jr_members_client ON jr_members(client_id);

CREATE TABLE IF NOT EXISTS jr_invites (
    id          SERIAL PRIMARY KEY,
    token       TEXT NOT NULL UNIQUE,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    email       TEXT NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_jr_invites_client ON jr_invites(client_id);

CREATE TABLE IF NOT EXISTS jr_subjects (
    id          SERIAL PRIMARY KEY,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS jr_classes (
    id                SERIAL PRIMARY KEY,
    school_id         INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    subject           TEXT NOT NULL,
    grade             TEXT NOT NULL,
    section           TEXT DEFAULT '',
    name              TEXT DEFAULT '',
    teacher_member_id INTEGER REFERENCES jr_members(id) ON DELETE SET NULL,
    UNIQUE (school_id, subject, grade, section)
);
CREATE INDEX IF NOT EXISTS idx_jr_classes_school ON jr_classes(school_id);
CREATE INDEX IF NOT EXISTS idx_jr_classes_teacher ON jr_classes(teacher_member_id);

CREATE TABLE IF NOT EXISTS jr_exams (
    id          SERIAL PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES jr_classes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    exam_date   TEXT DEFAULT '',
    pdf_name    TEXT DEFAULT '',
    pdf_data    TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_jr_exams_class ON jr_exams(class_id);

CREATE TABLE IF NOT EXISTS jr_parent_links (
    id                SERIAL PRIMARY KEY,
    parent_member_id  INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    student_member_id INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    UNIQUE (parent_member_id, student_member_id)
);
CREATE INDEX IF NOT EXISTS idx_jr_parent_links_parent ON jr_parent_links(parent_member_id);
CREATE INDEX IF NOT EXISTS idx_jr_parent_links_student ON jr_parent_links(student_member_id);

CREATE TABLE IF NOT EXISTS jr_student_stats (
    member_id   INTEGER PRIMARY KEY REFERENCES jr_members(id) ON DELETE CASCADE,
    xp          INTEGER NOT NULL DEFAULT 0,
    focus_min   INTEGER NOT NULL DEFAULT 0,
    streak      INTEGER NOT NULL DEFAULT 0,
    study_hours TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS jr_exam_done (
    member_id   INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    exam_id     INTEGER NOT NULL REFERENCES jr_exams(id) ON DELETE CASCADE,
    PRIMARY KEY (member_id, exam_id)
);

CREATE TABLE IF NOT EXISTS jr_plan_blocks (
    id          SERIAL PRIMARY KEY,
    member_id   INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    block_date  TEXT DEFAULT '',
    subject     TEXT DEFAULT '',
    topic       TEXT DEFAULT '',
    minutes     INTEGER NOT NULL DEFAULT 0,
    exam_id     INTEGER REFERENCES jr_exams(id) ON DELETE CASCADE,
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jr_plan_blocks_member ON jr_plan_blocks(member_id);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jr_schools (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    plan           TEXT NOT NULL DEFAULT 'pilot',
    seat_cap       INTEGER NOT NULL DEFAULT 0,
    contract_start TEXT DEFAULT '',
    contract_end   TEXT DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS jr_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    grade       TEXT DEFAULT '',
    section     TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE (client_id, school_id)
);
CREATE INDEX IF NOT EXISTS idx_jr_members_school ON jr_members(school_id);
CREATE INDEX IF NOT EXISTS idx_jr_members_client ON jr_members(client_id);

CREATE TABLE IF NOT EXISTS jr_invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    email       TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_jr_invites_client ON jr_invites(client_id);

CREATE TABLE IF NOT EXISTS jr_subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id   INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS jr_classes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id         INTEGER NOT NULL REFERENCES jr_schools(id) ON DELETE CASCADE,
    subject           TEXT NOT NULL,
    grade             TEXT NOT NULL,
    section           TEXT DEFAULT '',
    name              TEXT DEFAULT '',
    teacher_member_id INTEGER REFERENCES jr_members(id) ON DELETE SET NULL,
    UNIQUE (school_id, subject, grade, section)
);
CREATE INDEX IF NOT EXISTS idx_jr_classes_school ON jr_classes(school_id);
CREATE INDEX IF NOT EXISTS idx_jr_classes_teacher ON jr_classes(teacher_member_id);

CREATE TABLE IF NOT EXISTS jr_exams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id    INTEGER NOT NULL REFERENCES jr_classes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    exam_date   TEXT DEFAULT '',
    pdf_name    TEXT DEFAULT '',
    pdf_data    TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_jr_exams_class ON jr_exams(class_id);

CREATE TABLE IF NOT EXISTS jr_parent_links (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_member_id  INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    student_member_id INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    UNIQUE (parent_member_id, student_member_id)
);
CREATE INDEX IF NOT EXISTS idx_jr_parent_links_parent ON jr_parent_links(parent_member_id);
CREATE INDEX IF NOT EXISTS idx_jr_parent_links_student ON jr_parent_links(student_member_id);

CREATE TABLE IF NOT EXISTS jr_student_stats (
    member_id   INTEGER PRIMARY KEY REFERENCES jr_members(id) ON DELETE CASCADE,
    xp          INTEGER NOT NULL DEFAULT 0,
    focus_min   INTEGER NOT NULL DEFAULT 0,
    streak      INTEGER NOT NULL DEFAULT 0,
    study_hours TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS jr_exam_done (
    member_id   INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    exam_id     INTEGER NOT NULL REFERENCES jr_exams(id) ON DELETE CASCADE,
    PRIMARY KEY (member_id, exam_id)
);

CREATE TABLE IF NOT EXISTS jr_plan_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER NOT NULL REFERENCES jr_members(id) ON DELETE CASCADE,
    block_date  TEXT DEFAULT '',
    subject     TEXT DEFAULT '',
    topic       TEXT DEFAULT '',
    minutes     INTEGER NOT NULL DEFAULT 0,
    exam_id     INTEGER REFERENCES jr_exams(id) ON DELETE CASCADE,
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jr_plan_blocks_member ON jr_plan_blocks(member_id);
"""


def jr_init_db():
    """Create JR tables if absent. Idempotent; safe to call at every startup."""
    with get_db() as db:
        if _USE_PG:
            db.cursor().execute(_PG_SCHEMA)
        else:
            db.executescript(_SQLITE_SCHEMA)
    print("JR database initialized.")


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------

def create_school(name: str, plan: str = "pilot", seat_cap: int = 0,
                  contract_start: str = "", contract_end: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO jr_schools (name, plan, seat_cap, contract_start, contract_end) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, plan, seat_cap, contract_start, contract_end),
        )


def get_school(school_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM jr_schools WHERE id = %s", (school_id,))


def update_school(school_id: int, **fields) -> bool:
    allowed = {"name", "plan", "seat_cap", "contract_start", "contract_end", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    sets = ", ".join(f"{k} = %s" for k in updates)
    vals = list(updates.values()) + [school_id]
    with get_db() as db:
        _exec(db, f"UPDATE jr_schools SET {sets} WHERE id = %s", vals)
        return True


def school_seat_count(school_id: int) -> int:
    """Members currently consuming a seat (everyone except parents)."""
    with get_db() as db:
        return _fetchval(
            db,
            "SELECT COUNT(*) FROM jr_members WHERE school_id = %s AND role != 'parent'",
            (school_id,),
        ) or 0


# ---------------------------------------------------------------------------
# Members  (the tenancy anchor)
# ---------------------------------------------------------------------------

def add_member(client_id: int, school_id: int, role: str,
               grade: str = "", section: str = "") -> int:
    if role not in JR_ROLES:
        raise ValueError(f"invalid JR role: {role}")
    with get_db() as db:
        member_id = _insert_returning_id(
            db,
            "INSERT INTO jr_members (client_id, school_id, role, grade, section) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, school_id, role, grade, section),
        )
        if role == "student":
            _exec(db, "INSERT INTO jr_student_stats (member_id) VALUES (%s)", (member_id,))
        return member_id


def get_member(member_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM jr_members WHERE id = %s", (member_id,))


def get_member_for_client(client_id: int) -> dict | None:
    """Resolve the JR membership for a logged-in university client.

    Joins the client's name/email so the API has the display identity without a
    second query. A client belongs to at most one school (UNIQUE constraint).
    """
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT m.*, c.name AS name, c.email AS email, c.email_verified AS email_verified "
            "FROM jr_members m JOIN clients c ON c.id = m.client_id "
            "WHERE m.client_id = %s",
            (client_id,),
        )


def get_member_in_school(member_id: int, school_id: int) -> dict | None:
    """Fetch a member only if it belongs to `school_id` — cross-school guard."""
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT m.*, c.name AS name, c.email AS email, c.email_verified AS email_verified "
            "FROM jr_members m JOIN clients c ON c.id = m.client_id "
            "WHERE m.id = %s AND m.school_id = %s",
            (member_id, school_id),
        )


def list_members(school_id: int, role: str | None = None) -> list[dict]:
    with get_db() as db:
        sql = ("SELECT m.*, c.name AS name, c.email AS email, c.email_verified AS email_verified "
               "FROM jr_members m JOIN clients c ON c.id = m.client_id "
               "WHERE m.school_id = %s")
        params: list = [school_id]
        if role:
            sql += " AND m.role = %s"
            params.append(role)
        sql += " ORDER BY c.name"
        return _fetchall(db, sql, params)


def remove_member(member_id: int, school_id: int) -> bool:
    """Delete a member (scoped to its school). Cascades to stats/done/plan/links."""
    with get_db() as db:
        cur = _exec(db,
                    "DELETE FROM jr_members WHERE id = %s AND school_id = %s",
                    (member_id, school_id))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Invites  (provision real accounts — Workstream C)
# ---------------------------------------------------------------------------
# A JR member is an ordinary `clients` row. When an admin adds someone who has
# no account yet, we create that row UNVERIFIED (email_verified=0, random
# password) so the shared university /login refuses it, then email a one-time
# invite link. Accepting the invite sets the member's own password and flips
# email_verified=1 — the only path that clears the login gate for these rows.

def create_invite(client_id: int, school_id: int, role: str, email: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=INVITE_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        # One live invite per account: drop any earlier unused ones.
        _exec(db, "DELETE FROM jr_invites WHERE client_id = %s AND used = 0", (client_id,))
        _exec(db,
            "INSERT INTO jr_invites (token, client_id, school_id, role, email, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (token, client_id, school_id, role, email, expires))
    return token


def get_valid_invite(token: str) -> dict | None:
    with get_db() as db:
        now = _now_expr()
        return _fetchone(db,
            f"SELECT * FROM jr_invites WHERE token = %s AND used = 0 AND expires_at > {now}",
            (token,))


def provision_member(school_id: int, email: str, name: str, role: str,
                     grade: str = "", section: str = "") -> tuple[int, str]:
    """Create an UNVERIFIED account + JR membership for a brand-new email, and
    mint an invite token. Caller emails the link. Returns (member_id, token).

    Caller must have already checked the email isn't an existing client.
    """
    if role not in JR_ROLES:
        raise ValueError(f"invalid JR role: {role}")
    # Random unusable password — replaced when the invite is accepted. The
    # account can't be logged into before then because email_verified stays 0.
    client_id = create_client(name or email, email, hash_password(secrets.token_urlsafe(24)),
                              account_type="student")
    member_id = add_member(client_id, school_id, role, grade=grade, section=section)
    token = create_invite(client_id, school_id, role, email)
    return member_id, token


def accept_invite(token: str, password: str) -> dict | None:
    """Redeem an invite: set the member's password, clear the email_verified
    gate, and burn the token. Returns {client_id, school_id, role} or None if
    the token is invalid/expired/used."""
    inv = get_valid_invite(token)
    if not inv:
        return None
    with get_db() as db:
        _exec(db, "UPDATE clients SET password = %s, email_verified = 1 WHERE id = %s",
              (hash_password(password), inv["client_id"]))
        _exec(db, "UPDATE jr_invites SET used = 1 WHERE token = %s", (token,))
    return {"client_id": inv["client_id"], "school_id": inv["school_id"], "role": inv["role"]}


# ---------------------------------------------------------------------------
# Subjects (per-school catalog)
# ---------------------------------------------------------------------------

def list_subjects(school_id: int) -> list[str]:
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT name FROM jr_subjects WHERE school_id = %s ORDER BY name",
            (school_id,))
        return [r["name"] for r in rows]


def set_subjects(school_id: int, names: list[str]):
    """Replace the school's subject catalog."""
    with get_db() as db:
        _exec(db, "DELETE FROM jr_subjects WHERE school_id = %s", (school_id,))
        for n in names:
            n = (n or "").strip()
            if not n:
                continue
            try:
                _exec(db, "INSERT INTO jr_subjects (school_id, name) VALUES (%s, %s)",
                      (school_id, n))
            except Exception:
                if _USE_PG:
                    db.rollback()


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

def add_class(school_id: int, subject: str, grade: str, section: str = "",
              name: str = "", teacher_member_id: int | None = None) -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO jr_classes (school_id, subject, grade, section, name, teacher_member_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (school_id, subject, grade, section, name, teacher_member_id),
        )


def get_class(class_id: int, school_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db,
            "SELECT * FROM jr_classes WHERE id = %s AND school_id = %s",
            (class_id, school_id))


def list_classes(school_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM jr_classes WHERE school_id = %s ORDER BY grade, section, subject",
            (school_id,))


def list_classes_for_teacher(teacher_member_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM jr_classes WHERE teacher_member_id = %s ORDER BY grade, section, subject",
            (teacher_member_id,))


def list_classes_for_student(school_id: int, grade: str, section: str) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM jr_classes WHERE school_id = %s AND grade = %s AND section = %s "
            "ORDER BY subject",
            (school_id, grade, section))


def assign_teacher(class_id: int, school_id: int, teacher_member_id: int | None) -> bool:
    with get_db() as db:
        cur = _exec(db,
            "UPDATE jr_classes SET teacher_member_id = %s WHERE id = %s AND school_id = %s",
            (teacher_member_id, class_id, school_id))
        return cur.rowcount > 0


def remove_class(class_id: int, school_id: int) -> bool:
    with get_db() as db:
        cur = _exec(db, "DELETE FROM jr_classes WHERE id = %s AND school_id = %s",
                    (class_id, school_id))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------

def add_exam(class_id: int, name: str, exam_date: str = "",
             pdf_name: str = "", pdf_data: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO jr_exams (class_id, name, exam_date, pdf_name, pdf_data) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (class_id, name, exam_date, pdf_name, pdf_data),
        )


def list_exams(class_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT id, class_id, name, exam_date, pdf_name, "
            "(pdf_data != '') AS has_pdf "
            "FROM jr_exams WHERE class_id = %s ORDER BY exam_date",
            (class_id,))


def get_exam(exam_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db, "SELECT * FROM jr_exams WHERE id = %s", (exam_id,))


def exam_belongs_to_school(exam_id: int, school_id: int) -> bool:
    with get_db() as db:
        row = _fetchone(db,
            "SELECT 1 AS ok FROM jr_exams e JOIN jr_classes c ON c.id = e.class_id "
            "WHERE e.id = %s AND c.school_id = %s",
            (exam_id, school_id))
        return row is not None


def remove_exam(exam_id: int) -> bool:
    with get_db() as db:
        cur = _exec(db, "DELETE FROM jr_exams WHERE id = %s", (exam_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Parent <-> student links
# ---------------------------------------------------------------------------

def link_parent(parent_member_id: int, student_member_id: int) -> bool:
    with get_db() as db:
        try:
            _exec(db,
                "INSERT INTO jr_parent_links (parent_member_id, student_member_id) "
                "VALUES (%s, %s)",
                (parent_member_id, student_member_id))
            return True
        except Exception:
            if _USE_PG:
                db.rollback()
            return False


def unlink_parent(parent_member_id: int, student_member_id: int) -> bool:
    with get_db() as db:
        cur = _exec(db,
            "DELETE FROM jr_parent_links "
            "WHERE parent_member_id = %s AND student_member_id = %s",
            (parent_member_id, student_member_id))
        return cur.rowcount > 0


def children_of(parent_member_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT m.*, c.name AS name, c.email AS email, c.email_verified AS email_verified "
            "FROM jr_parent_links l "
            "JOIN jr_members m ON m.id = l.student_member_id "
            "JOIN clients c ON c.id = m.client_id "
            "WHERE l.parent_member_id = %s ORDER BY c.name",
            (parent_member_id,))


def is_parent_of(parent_member_id: int, student_member_id: int) -> bool:
    with get_db() as db:
        row = _fetchone(db,
            "SELECT 1 AS ok FROM jr_parent_links "
            "WHERE parent_member_id = %s AND student_member_id = %s",
            (parent_member_id, student_member_id))
        return row is not None


# ---------------------------------------------------------------------------
# Student stats
# ---------------------------------------------------------------------------

def get_stats(member_id: int) -> dict:
    with get_db() as db:
        row = _fetchone(db, "SELECT * FROM jr_student_stats WHERE member_id = %s",
                        (member_id,))
        if row:
            return row
        _exec(db, "INSERT INTO jr_student_stats (member_id) VALUES (%s)", (member_id,))
        return {"member_id": member_id, "xp": 0, "focus_min": 0, "streak": 0,
                "study_hours": "{}"}


def update_stats(member_id: int, **fields) -> bool:
    allowed = {"xp", "focus_min", "streak", "study_hours"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    sets = ", ".join(f"{k} = %s" for k in updates)
    vals = list(updates.values()) + [member_id]
    with get_db() as db:
        _exec(db, f"UPDATE jr_student_stats SET {sets} WHERE member_id = %s", vals)
        return True


def add_xp(member_id: int, amount: int) -> int:
    with get_db() as db:
        _exec(db, "UPDATE jr_student_stats SET xp = xp + %s WHERE member_id = %s",
              (amount, member_id))
        return _fetchval(db, "SELECT xp FROM jr_student_stats WHERE member_id = %s",
                         (member_id,)) or 0


# ---------------------------------------------------------------------------
# Exam-done checklist
# ---------------------------------------------------------------------------

def list_done_exam_ids(member_id: int) -> list[int]:
    with get_db() as db:
        rows = _fetchall(db,
            "SELECT exam_id FROM jr_exam_done WHERE member_id = %s", (member_id,))
        return [r["exam_id"] for r in rows]


def set_exam_done(member_id: int, exam_id: int, done: bool) -> bool:
    with get_db() as db:
        if done:
            try:
                if _USE_PG:
                    _exec(db,
                        "INSERT INTO jr_exam_done (member_id, exam_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (member_id, exam_id))
                else:
                    _exec(db,
                        "INSERT OR IGNORE INTO jr_exam_done (member_id, exam_id) VALUES (%s, %s)",
                        (member_id, exam_id))
            except Exception:
                if _USE_PG:
                    db.rollback()
                return False
        else:
            _exec(db, "DELETE FROM jr_exam_done WHERE member_id = %s AND exam_id = %s",
                  (member_id, exam_id))
        return True


# ---------------------------------------------------------------------------
# Study plan blocks
# ---------------------------------------------------------------------------

def get_plan(member_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM jr_plan_blocks WHERE member_id = %s ORDER BY block_date",
            (member_id,))


def replace_plan(member_id: int, blocks: list[dict]):
    """Overwrite a student's plan with a freshly generated set of blocks.

    Each block: {block_date, subject, topic, minutes, exam_id, done}.
    """
    with get_db() as db:
        _exec(db, "DELETE FROM jr_plan_blocks WHERE member_id = %s", (member_id,))
        for b in blocks:
            _exec(db,
                "INSERT INTO jr_plan_blocks (member_id, block_date, subject, topic, minutes, exam_id, done) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (member_id, b.get("block_date", ""), b.get("subject", ""),
                 b.get("topic", ""), int(b.get("minutes", 0) or 0),
                 b.get("exam_id"), 1 if b.get("done") else 0))


def set_block_done(block_id: int, member_id: int, done: bool) -> bool:
    """Toggle a plan block's done flag — scoped to its owner member."""
    with get_db() as db:
        cur = _exec(db,
            "UPDATE jr_plan_blocks SET done = %s WHERE id = %s AND member_id = %s",
            (1 if done else 0, block_id, member_id))
        return cur.rowcount > 0


if __name__ == "__main__":
    jr_init_db()
