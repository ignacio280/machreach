"""
Academic identity & hierarchical leaderboard module.

This module adds the competitive academic ecosystem on top of the existing
`student_xp` transaction table. It deliberately does NOT modify or reset any
existing XP — XP is read via SUM() from student_xp exactly as before.

Tables added
────────────
  countries                 ISO-3166 seed.
  universities              Top ~300 seeded + user-added (moderation flag).
  majors                    University-scoped or global. Normalized for dedupe.

Columns added to clients
────────────────────────
  country_iso               ISO-3166 alpha-2 of country of study.
  university_id             FK to universities.
  major_id                  FK to majors.
  academic_setup_complete   0/1 gate flag — forces onboarding modal until 1.
  academic_setup_at         Timestamp when user finished setup.
  xp_preserve_banner_seen   0/1 — has the user seen the "progress preserved" welcome once.
"""

from __future__ import annotations
import logging
import re
import unicodedata
from typing import Iterable, Optional

# Reuse the parent DB helpers — Postgres/SQLite agnostic.
from outreach.db import (
    get_db, _exec, _fetchone, _fetchall, _fetchval,
    _insert_returning_id, _USE_PG,
)
from student.academic_seeds import COUNTRIES, UNIVERSITIES, GLOBAL_MAJORS, GLOBAL_MAJORS_ES


log = logging.getLogger("student.academic")


# ═══════════════════════════════════════════════════════════════════════
#  Schema / migrations
# ═══════════════════════════════════════════════════════════════════════

def _create_table_safe(pg_sql: str, sqlite_sql: str) -> None:
    try:
        with get_db() as db:
            if _USE_PG:
                db.cursor().execute(pg_sql)
            else:
                db.execute(sqlite_sql)
    except Exception as e:
        log.warning("create_table_safe failed: %s", e)


def _add_column_safe(table: str, col: str, col_type: str) -> None:
    try:
        with get_db() as db:
            if _USE_PG:
                db.cursor().execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
            else:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except Exception:
        pass  # already exists


def init_academic_db() -> None:
    """Called from student.db.init_student_db(). Idempotent."""
    # Countries
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS countries (
            iso_code   VARCHAR(2) PRIMARY KEY,
            name       TEXT NOT NULL,
            flag_emoji TEXT DEFAULT '',
            region     TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS countries (
            iso_code   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            flag_emoji TEXT DEFAULT '',
            region     TEXT DEFAULT ''
        )""",
    )
    # Universities
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS universities (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            name_norm   TEXT NOT NULL,
            country_iso VARCHAR(2),
            slug        TEXT UNIQUE,
            short_name  TEXT DEFAULT '',
            status      TEXT DEFAULT 'approved',
            created_by  INTEGER,
            created_at  TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS universities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            name_norm   TEXT NOT NULL,
            country_iso TEXT,
            slug        TEXT UNIQUE,
            short_name  TEXT DEFAULT '',
            status      TEXT DEFAULT 'approved',
            created_by  INTEGER,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )""",
    )
    _safe_index("idx_univ_country", "universities", "country_iso")
    _safe_index("idx_univ_name_norm", "universities", "name_norm")

    # Majors
    _create_table_safe(
        """CREATE TABLE IF NOT EXISTS majors (
            id            SERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            name_norm     TEXT NOT NULL,
            university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
            slug          TEXT,
            status        TEXT DEFAULT 'approved',
            created_by    INTEGER,
            created_at    TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS majors (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            name_norm     TEXT NOT NULL,
            university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
            slug          TEXT,
            status        TEXT DEFAULT 'approved',
            created_by    INTEGER,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )""",
    )
    _safe_index("idx_major_univ", "majors", "university_id")
    _safe_index("idx_major_name_norm", "majors", "name_norm")

    # Column additions on clients
    for col, coltype in (
        ("country_iso",                "TEXT DEFAULT ''"),
        ("university_id",              "INTEGER"),
        ("major_id",                   "INTEGER"),
        ("academic_setup_complete",    "INTEGER DEFAULT 0"),
        ("academic_setup_at",          "TIMESTAMP"),
        ("xp_preserve_banner_seen",    "INTEGER DEFAULT 0"),
    ):
        _add_column_safe("clients", col, coltype)

    # Seed data
    _seed_countries()
    _seed_universities()
    _seed_majors()
    log.info("Academic identity tables initialized.")


def _safe_index(name: str, table: str, col: str) -> None:
    try:
        with get_db() as db:
            if _USE_PG:
                db.cursor().execute(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {table}({col})"
                )
            else:
                db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({col})")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
#  Seeding
# ═══════════════════════════════════════════════════════════════════════

def _seed_countries() -> None:
    """Insert ISO-3166 country list if not already present."""
    with get_db() as db:
        existing = _fetchval(db, "SELECT COUNT(*) FROM countries", ()) or 0
        if existing >= len(COUNTRIES):
            return
        for iso, name, flag, region in COUNTRIES:
            try:
                _exec(
                    db,
                    "INSERT INTO countries (iso_code, name, flag_emoji, region) "
                    "VALUES (%s, %s, %s, %s) "
                    + ("ON CONFLICT (iso_code) DO NOTHING" if _USE_PG else ""),
                    (iso, name, flag, region),
                )
            except Exception:
                # SQLite: use OR IGNORE
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO countries (iso_code, name, flag_emoji, region) "
                        "VALUES (?, ?, ?, ?)",
                        (iso, name, flag, region),
                    )
                except Exception:
                    pass


def _seed_universities() -> None:
    """Insert the top-universities seed list if not already present."""
    with get_db() as db:
        existing = _fetchval(db, "SELECT COUNT(*) FROM universities", ()) or 0
        if existing >= len(UNIVERSITIES) * 0.9:
            return
        for name, country_iso, short in UNIVERSITIES:
            slug = _slugify(name)
            norm = normalize_name(name)
            try:
                _exec(
                    db,
                    "INSERT INTO universities (name, name_norm, country_iso, slug, short_name, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'approved') "
                    + ("ON CONFLICT (slug) DO NOTHING" if _USE_PG else ""),
                    (name, norm, country_iso, slug, short or ""),
                )
            except Exception:
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO universities "
                        "(name, name_norm, country_iso, slug, short_name, status) "
                        "VALUES (?, ?, ?, ?, ?, 'approved')",
                        (name, norm, country_iso, slug, short or ""),
                    )
                except Exception:
                    pass


def _seed_majors() -> None:
    """Insert the global majors list (university_id NULL = available everywhere).

    English entries use the alias-collapsed normalized form so that students
    can find them by short codes like "CS" or "econ". Spanish entries are
    stored with their LITERAL normalized form (no alias mapping) so that
    Spanish-typing students can find them by partial matches like "psicol".
    """
    entries = (
        [(name, normalize_major(name)) for name in GLOBAL_MAJORS]
        + [(name, normalize_name(name)) for name in GLOBAL_MAJORS_ES]
    )
    with get_db() as db:
        existing = _fetchval(
            db,
            "SELECT COUNT(*) FROM majors WHERE university_id IS NULL",
            (),
        ) or 0
        if existing >= len(entries) * 0.9:
            return
        for name, norm in entries:
            slug = _slugify(name) + "-global"
            try:
                # Skip if a global major with this normalized name already exists
                dup = _fetchval(
                    db,
                    "SELECT id FROM majors WHERE university_id IS NULL AND name_norm = %s",
                    (norm,),
                )
                if dup:
                    continue
                _exec(
                    db,
                    "INSERT INTO majors (name, name_norm, university_id, slug, status) "
                    "VALUES (%s, %s, NULL, %s, 'approved')",
                    (name, norm, slug),
                )
            except Exception as e:
                log.warning("seed major %r failed: %s", name, e)


# ═══════════════════════════════════════════════════════════════════════
#  Normalization & slug helpers
# ═══════════════════════════════════════════════════════════════════════

_ACCENT_RE = re.compile(r"[\u0300-\u036f]")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _strip_accents(s: str) -> str:
    return _ACCENT_RE.sub("", unicodedata.normalize("NFKD", s or ""))


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse non-alphanumerics."""
    s = _strip_accents(name or "").lower()
    s = s.replace("&", " and ")
    return _NONALNUM_RE.sub(" ", s).strip()


def _slugify(name: str) -> str:
    s = _strip_accents(name or "").lower()
    return _NONALNUM_RE.sub("-", s).strip("-")[:100]


# Common aliases so "Comp Sci" maps to "Computer Science"
_MAJOR_ALIASES: dict[str, str] = {
    "comp sci": "computer science",
    "cs": "computer science",
    "ee": "electrical engineering",
    "mech e": "mechanical engineering",
    "civil e": "civil engineering",
    "bio e": "biomedical engineering",
    "chem e": "chemical engineering",
    "ind e": "industrial engineering",
    "econ": "economics",
    "bus admin": "business administration",
    "ba": "business administration",
    "mba": "business administration",
    "poli sci": "political science",
    "ir": "international relations",
    "med": "medicine",
    "pre med": "medicine",
    "pre-med": "medicine",
    "eng": "engineering",
    "math": "mathematics",
    "stats": "statistics",
    "psych": "psychology",
    "phil": "philosophy",
    "arch": "architecture",
    "ing comercial": "business administration",
    "ingenieria comercial": "business administration",
    "ing civil": "civil engineering",
    "ingenieria civil": "civil engineering",
    "ing informatica": "computer science",
    "ingenieria informatica": "computer science",
    "medicina": "medicine",
    "derecho": "law",
    "psicologia": "psychology",
    "economia": "economics",
    "arquitectura": "architecture",
}


def normalize_major(name: str) -> str:
    norm = normalize_name(name)
    return _MAJOR_ALIASES.get(norm, norm)


# ═══════════════════════════════════════════════════════════════════════
#  Queries — countries, universities, majors
# ═══════════════════════════════════════════════════════════════════════

def list_countries() -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db, "SELECT iso_code, name, flag_emoji, region FROM countries ORDER BY name", ()
        )


def search_universities(country_iso: str, query: str, limit: int = 20) -> list[dict]:
    """Fuzzy match: normalize input, try prefix and contains."""
    q_norm = normalize_name(query)
    if not q_norm:
        # Show the first N for this country if no query
        with get_db() as db:
            return _fetchall(
                db,
                "SELECT id, name, short_name, country_iso, status FROM universities "
                "WHERE country_iso = %s ORDER BY name LIMIT %s",
                (country_iso, limit),
            )
    like = f"%{q_norm}%"
    prefix = f"{q_norm}%"
    with get_db() as db:
        # Prefix matches first (highest relevance)
        rows = _fetchall(
            db,
            "SELECT id, name, short_name, country_iso, status FROM universities "
            "WHERE country_iso = %s AND name_norm LIKE %s "
            "ORDER BY LENGTH(name), name LIMIT %s",
            (country_iso, prefix, limit),
        )
        seen_ids = {r["id"] for r in rows}
        if len(rows) < limit:
            extra = _fetchall(
                db,
                "SELECT id, name, short_name, country_iso, status FROM universities "
                "WHERE country_iso = %s AND name_norm LIKE %s "
                "ORDER BY LENGTH(name), name LIMIT %s",
                (country_iso, like, limit),
            )
            for r in extra:
                if r["id"] not in seen_ids:
                    rows.append(r)
                    if len(rows) >= limit:
                        break
    return rows


def create_university(name: str, country_iso: str, created_by: int) -> int:
    """User-added universities start as 'pending' for moderation."""
    norm = normalize_name(name)
    slug = _slugify(name) + f"-u{created_by}"
    with get_db() as db:
        # Dedupe: if the normalized form already exists for this country, reuse it
        existing = _fetchval(
            db,
            "SELECT id FROM universities WHERE country_iso = %s AND name_norm = %s LIMIT 1",
            (country_iso, norm),
        )
        if existing:
            return int(existing)
        return _insert_returning_id(
            db,
            "INSERT INTO universities (name, name_norm, country_iso, slug, status, created_by) "
            "VALUES (%s, %s, %s, %s, 'pending', %s) RETURNING id",
            (name.strip(), norm, country_iso, slug, created_by),
            "INSERT INTO universities (name, name_norm, country_iso, slug, status, created_by) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
        )


def get_university(univ_id: int) -> Optional[dict]:
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT id, name, short_name, country_iso, status FROM universities WHERE id = %s",
            (univ_id,),
        )


def search_majors(query: str, university_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    """Match against both global majors and majors scoped to this university.

    Searches both the alias-collapsed and literal normalized forms so that
    students can find majors by abbreviation ("CS"), English name, or
    Spanish/local name.
    """
    q_aliased = normalize_major(query)
    q_literal = normalize_name(query)
    forms = [q_aliased]
    if q_literal and q_literal != q_aliased:
        forms.append(q_literal)
    forms = [f for f in forms if f]
    if not forms:
        return []
    seen_ids: set[int] = set()
    rows: list[dict] = []
    with get_db() as db:
        for q in forms:
            like = f"%{q}%"
            if university_id is not None:
                hits = _fetchall(
                    db,
                    "SELECT id, name, university_id FROM majors "
                    "WHERE (university_id IS NULL OR university_id = %s) "
                    "AND name_norm LIKE %s "
                    "ORDER BY LENGTH(name), name LIMIT %s",
                    (university_id, like, limit),
                )
            else:
                hits = _fetchall(
                    db,
                    "SELECT id, name, university_id FROM majors "
                    "WHERE name_norm LIKE %s ORDER BY LENGTH(name), name LIMIT %s",
                    (like, limit),
                )
            for r in hits:
                if r["id"] in seen_ids:
                    continue
                seen_ids.add(r["id"])
                rows.append(r)
                if len(rows) >= limit:
                    return rows
    return rows


def create_major(name: str, university_id: Optional[int], created_by: int) -> int:
    """Majors are deduped by normalized name within scope (global or univ)."""
    norm = normalize_major(name)
    slug = _slugify(name)
    with get_db() as db:
        # Check global duplicate first
        existing = _fetchval(
            db,
            "SELECT id FROM majors WHERE name_norm = %s AND "
            "(university_id IS NULL OR university_id = %s) LIMIT 1",
            (norm, university_id or -1),
        )
        if existing:
            return int(existing)
        return _insert_returning_id(
            db,
            "INSERT INTO majors (name, name_norm, university_id, slug, status, created_by) "
            "VALUES (%s, %s, %s, %s, 'approved', %s) RETURNING id",
            (name.strip().title(), norm, university_id, slug, created_by),
            "INSERT INTO majors (name, name_norm, university_id, slug, status, created_by) "
            "VALUES (?, ?, ?, ?, 'approved', ?)",
        )


def get_major(major_id: int) -> Optional[dict]:
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT id, name, university_id FROM majors WHERE id = %s",
            (major_id,),
        )


# ═══════════════════════════════════════════════════════════════════════
#  Profile / setup state
# ═══════════════════════════════════════════════════════════════════════

def get_academic_profile(client_id: int) -> dict:
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT country_iso, university_id, major_id, "
            "academic_setup_complete, academic_setup_at, xp_preserve_banner_seen "
            "FROM clients WHERE id = %s",
            (client_id,),
        )
    return row or {}


def save_academic_profile(
    client_id: int,
    country_iso: str,
    university_id: int,
    major_id: int,
) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET country_iso = %s, university_id = %s, major_id = %s, "
            "academic_setup_complete = 1, academic_setup_at = "
            + ("NOW()" if _USE_PG else "datetime('now','localtime')")
            + " WHERE id = %s",
            (country_iso, university_id, major_id, client_id),
        )


def mark_welcome_banner_seen(client_id: int) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET xp_preserve_banner_seen = 1 WHERE id = %s",
            (client_id,),
        )


def needs_setup(client_id: int) -> bool:
    prof = get_academic_profile(client_id)
    return not bool(prof.get("academic_setup_complete"))


# ═══════════════════════════════════════════════════════════════════════
#  League system
# ═══════════════════════════════════════════════════════════════════════
# Pure XP-threshold leagues. No promotions/demotions in Phase 1 — XP only
# moves up, so users monotonically climb. Weekly promotion/demotion reset
# lives in Phase 2 (with the season system).

LEAGUES: list[dict] = [
    {"key": "initiate",    "name": "Initiate",         "min_xp": 0,      "color": "#94A3B8", "glow": "#64748B"},
    {"key": "scholar",     "name": "Scholar",          "min_xp": 300,    "color": "#22C55E", "glow": "#10B981"},
    {"key": "researcher",  "name": "Researcher",       "min_xp": 1200,   "color": "#06B6D4", "glow": "#0891B2"},
    {"key": "academic",    "name": "Academic",         "min_xp": 3500,   "color": "#3B82F6", "glow": "#2563EB"},
    {"key": "mastermind",  "name": "Mastermind",       "min_xp": 8000,   "color": "#8B5CF6", "glow": "#7C3AED"},
    {"key": "grand",       "name": "Grand Scholar",    "min_xp": 18000,  "color": "#EC4899", "glow": "#DB2777"},
    {"key": "legend",      "name": "Legend",           "min_xp": 40000,  "color": "#F59E0B", "glow": "#D97706"},
]


def league_for_xp(xp: int) -> dict:
    """Return the league dict the given XP total falls into."""
    chosen = LEAGUES[0]
    for L in LEAGUES:
        if xp >= L["min_xp"]:
            chosen = L
    # Include next threshold for progress bar
    idx = LEAGUES.index(chosen)
    next_lg = LEAGUES[idx + 1] if idx + 1 < len(LEAGUES) else None
    result = dict(chosen)
    result["index"] = idx
    result["next_name"] = next_lg["name"] if next_lg else None
    result["next_min_xp"] = next_lg["min_xp"] if next_lg else None
    if next_lg:
        span = next_lg["min_xp"] - chosen["min_xp"]
        pct = 0 if span <= 0 else min(100, int((xp - chosen["min_xp"]) / span * 100))
        result["progress_pct"] = pct
    else:
        result["progress_pct"] = 100
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Leaderboard queries
# ═══════════════════════════════════════════════════════════════════════
# Every leaderboard is computed live by SUMming student_xp and joining
# clients with the relevant scope column. Cheap at current scale and
# avoids any duplicate XP storage.

def _xp_select(where_extra: str = "", params: Iterable = ()) -> tuple[str, tuple]:
    base = (
        "SELECT c.id AS client_id, c.name AS display_name, "
        "       c.country_iso, c.university_id, c.major_id, "
        "       COALESCE(SUM(x.xp), 0) AS total_xp "
        "FROM clients c "
        "LEFT JOIN student_xp x ON x.client_id = c.id "
        "WHERE c.account_type = 'student' "
    )
    q = base + where_extra + " GROUP BY c.id, c.name, c.country_iso, c.university_id, c.major_id "
    return q, tuple(params)


def leaderboard(scope: str, client_id: int, limit: int = 100) -> list[dict]:
    """
    scope ∈ {"global", "country", "university", "major"}.
    Returned rows are sorted by total_xp desc and include rank + is_you flag.
    """
    with get_db() as db:
        # Fetch the requester's scoping values
        me = _fetchone(
            db,
            "SELECT country_iso, university_id, major_id FROM clients WHERE id = %s",
            (client_id,),
        ) or {}

    extra = ""
    params: list = []
    if scope == "country":
        if not me.get("country_iso"):
            return []
        extra = " AND c.country_iso = %s "
        params.append(me["country_iso"])
    elif scope == "university":
        if not me.get("university_id"):
            return []
        extra = " AND c.university_id = %s "
        params.append(me["university_id"])
    elif scope == "major":
        if not me.get("major_id"):
            return []
        extra = " AND c.major_id = %s "
        params.append(me["major_id"])
    # global => no extra filter

    q, p = _xp_select(extra, params)
    q += " ORDER BY total_xp DESC, c.id ASC LIMIT %s "
    p = p + (limit,)

    with get_db() as db:
        rows = _fetchall(db, q, p)

    out: list[dict] = []
    for idx, r in enumerate(rows, start=1):
        xp = int(r.get("total_xp") or 0)
        lg = league_for_xp(xp)
        out.append({
            "rank": idx,
            "client_id": r["client_id"],
            "name": r.get("display_name") or "Student",
            "xp": xp,
            "league_key": lg["key"],
            "league_name": lg["name"],
            "league_color": lg["color"],
            "is_you": r["client_id"] == client_id,
        })
    return out


def my_rank(scope: str, client_id: int) -> Optional[dict]:
    """
    Efficient 'where am I in this leaderboard?' lookup without materializing
    the full list. Returns {rank, total} or None if the scope isn't set.
    """
    with get_db() as db:
        me = _fetchone(
            db,
            "SELECT country_iso, university_id, major_id FROM clients WHERE id = %s",
            (client_id,),
        ) or {}
        my_xp = _fetchval(
            db,
            "SELECT COALESCE(SUM(xp),0) FROM student_xp WHERE client_id = %s",
            (client_id,),
        ) or 0

    where_extra = ""
    params: list = []
    if scope == "country":
        if not me.get("country_iso"):
            return None
        where_extra = " AND c.country_iso = %s "
        params.append(me["country_iso"])
    elif scope == "university":
        if not me.get("university_id"):
            return None
        where_extra = " AND c.university_id = %s "
        params.append(me["university_id"])
    elif scope == "major":
        if not me.get("major_id"):
            return None
        where_extra = " AND c.major_id = %s "
        params.append(me["major_id"])

    q = (
        "SELECT COUNT(*) FROM ("
        "  SELECT c.id, COALESCE(SUM(x.xp),0) AS total_xp "
        "  FROM clients c LEFT JOIN student_xp x ON x.client_id = c.id "
        "  WHERE c.account_type = 'student' " + where_extra +
        "  GROUP BY c.id "
        "  HAVING COALESCE(SUM(x.xp),0) > %s "
        ") AS ahead"
    )
    total_q = (
        "SELECT COUNT(DISTINCT c.id) FROM clients c "
        "WHERE c.account_type = 'student' " + where_extra
    )
    with get_db() as db:
        ahead = _fetchval(db, q, tuple(params) + (int(my_xp),)) or 0
        total = _fetchval(db, total_q, tuple(params)) or 0
    return {"rank": int(ahead) + 1, "total": int(total), "xp": int(my_xp)}


def ranks_summary(client_id: int) -> dict:
    """Compact payload for the dashboard ranking strip."""
    return {
        "global":     my_rank("global", client_id),
        "country":    my_rank("country", client_id),
        "university": my_rank("university", client_id),
        "major":      my_rank("major", client_id),
    }
