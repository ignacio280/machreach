"""
Training tab — community quizzes, scoped per university course.

Any AI-generated quiz can be promoted to a shared `training_course`
(name + code, scoped to a `university_id`). Once promoted, every
student at that university sees the quiz in the Training tab and can
take it for XP. XP per attempt depends on the quiz's *difficulty*,
which is auto-assigned by heuristics on first publish and then
recalibrated as the community plays — so easy quizzes that everyone
aces lose value, hard quizzes that stump people gain it.

Tables
------
training_courses     — shared catalog of courses per university.
training_quizzes     — links a `student_quizzes` row into a course,
                       with difficulty + community stats.
training_attempts    — one row per user attempt.
training_ratings     — 1..10 rating per (quiz, user). Boosts XP.

Difficulty thresholds (avg score across attempts)
  ≥ 90%  → easy   → 15 XP
  50–90% → medium → 30 XP
  ≤ 50%  → hard   → 90 XP
"""
from __future__ import annotations

from datetime import datetime
import logging
import re

from outreach.db import get_db, _USE_PG, _exec, _fetchall, _fetchone, _insert_returning_id

log = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────

_TRAIN_PG = """
CREATE TABLE IF NOT EXISTS training_courses (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER NOT NULL,
    name          TEXT NOT NULL,
    code          TEXT,
    name_norm     TEXT,
    code_norm     TEXT,
    quiz_count    INTEGER DEFAULT 0,
    created_by    INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    created_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE(university_id, name_norm, code_norm)
);
CREATE INDEX IF NOT EXISTS idx_train_courses_uni ON training_courses(university_id);

CREATE TABLE IF NOT EXISTS training_quizzes (
    id                  SERIAL PRIMARY KEY,
    training_course_id  INTEGER NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
    quiz_id             INTEGER NOT NULL REFERENCES student_quizzes(id) ON DELETE CASCADE,
    uploaded_by         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    difficulty          TEXT NOT NULL DEFAULT 'medium',
    avg_score_pct       REAL,
    attempt_count       INTEGER DEFAULT 0,
    rating_sum          INTEGER DEFAULT 0,
    rating_count        INTEGER DEFAULT 0,
    is_official         INTEGER DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_train_quizzes_course ON training_quizzes(training_course_id);
CREATE INDEX IF NOT EXISTS idx_train_quizzes_quiz ON training_quizzes(quiz_id);

CREATE TABLE IF NOT EXISTS training_attempts (
    id                SERIAL PRIMARY KEY,
    training_quiz_id  INTEGER NOT NULL REFERENCES training_quizzes(id) ON DELETE CASCADE,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    score_pct         INTEGER NOT NULL,
    xp_awarded        INTEGER DEFAULT 0,
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_train_attempts_q ON training_attempts(training_quiz_id);
CREATE INDEX IF NOT EXISTS idx_train_attempts_c ON training_attempts(client_id, training_quiz_id);

CREATE TABLE IF NOT EXISTS training_ratings (
    training_quiz_id  INTEGER NOT NULL REFERENCES training_quizzes(id) ON DELETE CASCADE,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rating            INTEGER NOT NULL,
    created_at        TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (training_quiz_id, client_id)
);
"""

_TRAIN_SQLITE = """
CREATE TABLE IF NOT EXISTS training_courses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id INTEGER NOT NULL,
    name          TEXT NOT NULL,
    code          TEXT,
    name_norm     TEXT,
    code_norm     TEXT,
    quiz_count    INTEGER DEFAULT 0,
    created_by    INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(university_id, name_norm, code_norm)
);
CREATE INDEX IF NOT EXISTS idx_train_courses_uni ON training_courses(university_id);

CREATE TABLE IF NOT EXISTS training_quizzes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    training_course_id  INTEGER NOT NULL REFERENCES training_courses(id) ON DELETE CASCADE,
    quiz_id             INTEGER NOT NULL REFERENCES student_quizzes(id) ON DELETE CASCADE,
    uploaded_by         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    difficulty          TEXT NOT NULL DEFAULT 'medium',
    avg_score_pct       REAL,
    attempt_count       INTEGER DEFAULT 0,
    rating_sum          INTEGER DEFAULT 0,
    rating_count        INTEGER DEFAULT 0,
    is_official         INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_train_quizzes_course ON training_quizzes(training_course_id);
CREATE INDEX IF NOT EXISTS idx_train_quizzes_quiz ON training_quizzes(quiz_id);

CREATE TABLE IF NOT EXISTS training_attempts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    training_quiz_id  INTEGER NOT NULL REFERENCES training_quizzes(id) ON DELETE CASCADE,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    score_pct         INTEGER NOT NULL,
    xp_awarded        INTEGER DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_train_attempts_q ON training_attempts(training_quiz_id);
CREATE INDEX IF NOT EXISTS idx_train_attempts_c ON training_attempts(client_id, training_quiz_id);

CREATE TABLE IF NOT EXISTS training_ratings (
    training_quiz_id  INTEGER NOT NULL REFERENCES training_quizzes(id) ON DELETE CASCADE,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rating            INTEGER NOT NULL,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (training_quiz_id, client_id)
);
"""


def init_training_tables() -> None:
    with get_db() as db:
        if _USE_PG:
            cur = db.cursor()
            cur.execute(_TRAIN_PG)
        else:
            db.executescript(_TRAIN_SQLITE)


# ── XP table ────────────────────────────────────────────────────────────

# Base XP per attempt. Bonus from rating is +(avg_rating - 5) * 2, capped
# at +20, so a well-loved hard quiz can push to ~110 XP; a 1-rated easy
# one floors at ~5.
XP_BY_DIFFICULTY = {"easy": 15, "medium": 30, "hard": 90}


def _classify_difficulty(avg_score_pct: float | None) -> str:
    """Map community average score → difficulty label.
    None / no attempts means we keep whatever was already set."""
    if avg_score_pct is None:
        return "medium"
    if avg_score_pct >= 90:
        return "easy"
    if avg_score_pct <= 50:
        return "hard"
    return "medium"


def _initial_difficulty_from_questions(questions: list[dict]) -> str:
    """Lightweight heuristic to pick a starting difficulty before the
    community has touched the quiz. Looks at question/option/explanation
    length and basic complexity signals. Deterministic, no LLM cost."""
    if not questions:
        return "medium"
    n = len(questions)
    avg_q_len = sum(len((q.get("question") or "").split()) for q in questions) / n
    avg_opt_len = sum(
        sum(len((q.get(f"option_{k}") or "").split()) for k in "abcd") / 4
        for q in questions
    ) / n
    has_expl = sum(1 for q in questions if (q.get("explanation") or "").strip()) / n
    has_math = sum(1 for q in questions if "$" in (q.get("question") or "")) / n
    # Score 0..100. Long questions, long options, math, explanations = harder.
    score = (
        (avg_q_len / 30) * 35
        + (avg_opt_len / 12) * 25
        + has_expl * 15
        + has_math * 25
    )
    if score < 35:
        return "easy"
    if score > 65:
        return "hard"
    return "medium"


# ── Course catalog ──────────────────────────────────────────────────────

_NORM_RE = re.compile(r"[^\w]+", re.UNICODE)

def _norm(s: str | None) -> str:
    return _NORM_RE.sub(" ", (s or "").strip().lower()).strip()


def search_courses(university_id: int, query: str, limit: int = 30) -> list[dict]:
    """Find courses by free-text match against name OR code."""
    init_training_tables()
    q = (query or "").strip()
    with get_db() as db:
        if not q:
            rows = _fetchall(
                db,
                "SELECT id, name, code, quiz_count FROM training_courses "
                "WHERE university_id = %s "
                "ORDER BY quiz_count DESC, name ASC LIMIT %s",
                (university_id, limit),
            )
        else:
            like = f"%{q.lower()}%"
            rows = _fetchall(
                db,
                "SELECT id, name, code, quiz_count FROM training_courses "
                "WHERE university_id = %s "
                "AND (LOWER(name) LIKE %s OR LOWER(COALESCE(code, '')) LIKE %s) "
                "ORDER BY quiz_count DESC, name ASC LIMIT %s",
                (university_id, like, like, limit),
            )
    return [dict(r) for r in rows]


def get_or_create_course(university_id: int, name: str, code: str | None,
                         created_by: int) -> dict:
    """Idempotent: return the (university_id, name_norm, code_norm) row,
    creating it if missing."""
    init_training_tables()
    name = (name or "").strip()
    code = (code or "").strip() or None
    if len(name) < 2:
        raise ValueError("course name too short")
    name_norm = _norm(name)
    code_norm = _norm(code) if code else None
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT id, name, code, quiz_count FROM training_courses "
            "WHERE university_id = %s AND name_norm = %s "
            "  AND COALESCE(code_norm, '') = COALESCE(%s, '')",
            (university_id, name_norm, code_norm),
        )
        if row:
            return dict(row)
        new_id = _insert_returning_id(
            db,
            "INSERT INTO training_courses (university_id, name, code, name_norm, code_norm, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (university_id, name, code, name_norm, code_norm, created_by),
            "INSERT INTO training_courses (university_id, name, code, name_norm, code_norm, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )
        return {"id": new_id, "name": name, "code": code, "quiz_count": 0}


def get_course(course_id: int) -> dict | None:
    init_training_tables()
    with get_db() as db:
        r = _fetchone(
            db,
            "SELECT id, university_id, name, code, quiz_count FROM training_courses WHERE id = %s",
            (course_id,),
        )
    return dict(r) if r else None


def auto_create_courses_for_client(client_id: int) -> int:
    """After a Canvas sync, fold every student_courses row for this client
    into the shared training catalog. Idempotent: existing (name_norm, code_norm)
    matches are skipped by get_or_create_course. Returns the number of rows
    folded in (existing + newly created, best-effort count)."""
    init_training_tables()
    try:
        with get_db() as db:
            cli = _fetchone(
                db,
                "SELECT university_id FROM clients WHERE id = %s",
                (client_id,),
            )
            uni = (dict(cli).get("university_id") if cli else None)
            rows = _fetchall(
                db,
                "SELECT name, code FROM student_courses WHERE client_id = %s",
                (client_id,),
            )
    except Exception as e:
        log.warning("auto_create_courses_for_client lookup failed (client %s): %s", client_id, e)
        return 0

    if not uni:
        if rows:
            log.info(
                "auto_create_courses_for_client: client %s has %d Canvas courses "
                "but no university_id set — skipped. Prompt user to finish "
                "the academic profile.", client_id, len(rows),
            )
        return 0

    seen_ids: set[int] = set()
    folded = 0
    for r in rows or []:
        d = dict(r)
        name = (d.get("name") or "").strip()
        code = (d.get("code") or "").strip() or None
        if len(name) < 2:
            continue
        try:
            tc = get_or_create_course(uni, name, code, created_by=client_id)
            tcid = int(tc.get("id"))
            if tcid not in seen_ids:
                seen_ids.add(tcid)
                folded += 1
        except Exception as e:
            log.debug("skip course '%s' for client %s: %s", name, client_id, e)
            continue
    log.info(
        "auto_create_courses_for_client: client %s uni=%s — folded %d Canvas courses into training catalog.",
        client_id, uni, folded,
    )
    return folded


def backfill_courses_from_all_syncs() -> int:
    """One-shot: walk every client that has at least one student_courses row
    and make sure the shared training catalog contains it. Idempotent."""
    init_training_tables()
    try:
        with get_db() as db:
            rows = _fetchall(
                db,
                "SELECT DISTINCT client_id FROM student_courses",
            )
    except Exception as e:
        log.warning("backfill_courses_from_all_syncs list failed: %s", e)
        return 0
    total = 0
    clients_scanned = 0
    clients_missing_uni = 0
    for r in rows or []:
        cid = dict(r).get("client_id")
        if not cid:
            continue
        clients_scanned += 1
        folded = auto_create_courses_for_client(int(cid))
        total += folded
        if folded == 0:
            # auto_create_courses_for_client returns 0 only when no uni or
            # no valid course rows. Use it as a signal for visibility.
            try:
                with get_db() as db:
                    cli = _fetchone(
                        db,
                        "SELECT university_id FROM clients WHERE id = %s",
                        (int(cid),),
                    )
                    if not cli or not dict(cli).get("university_id"):
                        clients_missing_uni += 1
            except Exception:
                pass
    log.info(
        "backfill_courses_from_all_syncs: scanned=%d, folded=%d, clients_missing_university=%d",
        clients_scanned, total, clients_missing_uni,
    )
    return total


# ── Publish + list ──────────────────────────────────────────────────────

def publish_quiz(training_course_id: int, quiz_id: int, uploaded_by: int,
                 questions: list[dict], title: str,
                 is_official: bool = False) -> int:
    """Promote a `student_quizzes` row into a training course. Returns
    the new training_quizzes.id."""
    init_training_tables()
    diff = _initial_difficulty_from_questions(questions)
    with get_db() as db:
        new_id = _insert_returning_id(
            db,
            "INSERT INTO training_quizzes "
            "(training_course_id, quiz_id, uploaded_by, title, difficulty, is_official) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (training_course_id, quiz_id, uploaded_by, title, diff,
             1 if is_official else 0),
            "INSERT INTO training_quizzes "
            "(training_course_id, quiz_id, uploaded_by, title, difficulty, is_official) "
            "VALUES (?, ?, ?, ?, ?, ?)",
        )
        _exec(
            db,
            "UPDATE training_courses SET quiz_count = quiz_count + 1 WHERE id = %s",
            (training_course_id,),
        )
    return new_id


def list_quizzes_for_course(training_course_id: int) -> list[dict]:
    init_training_tables()
    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT tq.id, tq.title, tq.difficulty, tq.avg_score_pct, tq.attempt_count, "
            "       tq.rating_sum, tq.rating_count, tq.is_official, tq.uploaded_by, "
            "       tq.quiz_id, sq.question_count, c.name AS uploader_name "
            "FROM training_quizzes tq "
            "JOIN student_quizzes sq ON sq.id = tq.quiz_id "
            "LEFT JOIN clients c ON c.id = tq.uploaded_by "
            "WHERE tq.training_course_id = %s "
            "ORDER BY tq.is_official DESC, tq.attempt_count DESC, tq.id DESC",
            (training_course_id,),
        )
    out = []
    for r in rows:
        d = dict(r)
        rc = int(d.get("rating_count") or 0)
        d["avg_rating"] = round((d.get("rating_sum") or 0) / rc, 2) if rc else None
        out.append(d)
    return out


def get_training_quiz(training_quiz_id: int) -> dict | None:
    init_training_tables()
    with get_db() as db:
        r = _fetchone(
            db,
            "SELECT tq.*, sq.question_count, c.name AS uploader_name, "
            "       tc.name AS course_name, tc.university_id "
            "FROM training_quizzes tq "
            "JOIN student_quizzes sq ON sq.id = tq.quiz_id "
            "JOIN training_courses tc ON tc.id = tq.training_course_id "
            "LEFT JOIN clients c ON c.id = tq.uploaded_by "
            "WHERE tq.id = %s",
            (training_quiz_id,),
        )
    if not r:
        return None
    d = dict(r)
    rc = int(d.get("rating_count") or 0)
    d["avg_rating"] = round((d.get("rating_sum") or 0) / rc, 2) if rc else None
    return d


# ── Attempts (recalibrate difficulty + award XP) ────────────────────────

def record_attempt(training_quiz_id: int, client_id: int, score_pct: int) -> dict:
    """Persist an attempt, recalibrate quiz difficulty, award XP. Returns
    {xp_awarded, new_difficulty, avg_rating_bonus}."""
    init_training_tables()
    score_pct = max(0, min(100, int(score_pct)))

    # Pull current quiz state
    with get_db() as db:
        q = _fetchone(
            db,
            "SELECT id, difficulty, avg_score_pct, attempt_count, "
            "       rating_sum, rating_count "
            "FROM training_quizzes WHERE id = %s",
            (training_quiz_id,),
        )
    if not q:
        raise ValueError("quiz not found")

    # New running average
    n = int(q.get("attempt_count") or 0)
    prev_avg = q.get("avg_score_pct")
    if prev_avg is None:
        new_avg = float(score_pct)
    else:
        new_avg = (float(prev_avg) * n + score_pct) / (n + 1)
    new_n = n + 1
    new_diff = _classify_difficulty(new_avg)

    # XP base from new (post-attempt) difficulty
    base_xp = XP_BY_DIFFICULTY.get(new_diff, 30)
    rc = int(q.get("rating_count") or 0)
    avg_rating = (int(q.get("rating_sum") or 0) / rc) if rc else 5.0
    rating_bonus = max(-10, min(20, int(round((avg_rating - 5.0) * 2))))
    xp = max(1, base_xp + rating_bonus)

    # Persist
    with get_db() as db:
        _exec(
            db,
            "UPDATE training_quizzes SET avg_score_pct = %s, "
            "attempt_count = %s, difficulty = %s WHERE id = %s",
            (new_avg, new_n, new_diff, training_quiz_id),
        )
        _exec(
            db,
            "INSERT INTO training_attempts (training_quiz_id, client_id, score_pct, xp_awarded) "
            "VALUES (%s, %s, %s, %s)",
            (training_quiz_id, client_id, score_pct, xp),
        )

    # Award XP via the regular student XP pipeline
    try:
        from . import db as sdb
        sdb.award_xp(client_id, "training_quiz",
                     xp, f"Training quiz #{training_quiz_id} ({new_diff}, {score_pct}%)")
    except Exception as e:
        log.warning("award_xp failed for training quiz %s: %s", training_quiz_id, e)

    return {
        "xp_awarded": xp,
        "new_difficulty": new_diff,
        "avg_score_pct": round(new_avg, 1),
        "rating_bonus": rating_bonus,
        "attempt_count": new_n,
    }


# ── Ratings ─────────────────────────────────────────────────────────────

def record_rating(training_quiz_id: int, client_id: int, rating: int) -> dict:
    """Insert or update a 1..10 rating. Updates the cached sum/count on
    the quiz so list queries don't need an aggregate."""
    init_training_tables()
    rating = max(1, min(10, int(rating)))
    with get_db() as db:
        prior = _fetchone(
            db,
            "SELECT rating FROM training_ratings WHERE training_quiz_id = %s AND client_id = %s",
            (training_quiz_id, client_id),
        )
        if prior:
            prev = int(prior["rating"])
            _exec(
                db,
                "UPDATE training_ratings SET rating = %s, created_at = %s "
                "WHERE training_quiz_id = %s AND client_id = %s",
                (rating, datetime.now().isoformat(timespec="seconds"),
                 training_quiz_id, client_id),
            )
            _exec(
                db,
                "UPDATE training_quizzes SET rating_sum = rating_sum + %s "
                "WHERE id = %s",
                (rating - prev, training_quiz_id),
            )
        else:
            _exec(
                db,
                "INSERT INTO training_ratings (training_quiz_id, client_id, rating) "
                "VALUES (%s, %s, %s)",
                (training_quiz_id, client_id, rating),
            )
            _exec(
                db,
                "UPDATE training_quizzes SET rating_sum = rating_sum + %s, "
                "rating_count = rating_count + 1 WHERE id = %s",
                (rating, training_quiz_id),
            )
        row = _fetchone(
            db,
            "SELECT rating_sum, rating_count FROM training_quizzes WHERE id = %s",
            (training_quiz_id,),
        )
    rs = int((row or {}).get("rating_sum") or 0)
    rc = int((row or {}).get("rating_count") or 0)
    return {"avg_rating": round(rs / rc, 2) if rc else None, "rating_count": rc}
