"""
Professional / Business DB — tables for the Pro Toolkit:
tasks, time tracker, invoices, expenses, goals/OKRs.
"""
from __future__ import annotations

import logging
from outreach.db import _exec, _fetchall, _fetchone, _fetchval, _insert_returning_id, _USE_PG, get_db

log = logging.getLogger(__name__)


PRO_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS pro_tasks (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    priority     TEXT DEFAULT 'medium',
    status       TEXT DEFAULT 'todo',
    due_date     TEXT,
    project_tag  TEXT DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pro_tasks_client ON pro_tasks(client_id);

CREATE TABLE IF NOT EXISTS pro_time_entries (
    id               SERIAL PRIMARY KEY,
    client_id        INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project          TEXT DEFAULT '',
    description      TEXT DEFAULT '',
    started_at       TIMESTAMP NOT NULL,
    ended_at         TIMESTAMP,
    duration_seconds INTEGER DEFAULT 0,
    billable         BOOLEAN DEFAULT TRUE,
    hourly_rate      REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pro_time_client ON pro_time_entries(client_id);

CREATE TABLE IF NOT EXISTS pro_invoices (
    id             SERIAL PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    invoice_number TEXT NOT NULL,
    bill_to_name   TEXT DEFAULT '',
    bill_to_email  TEXT DEFAULT '',
    bill_to_addr   TEXT DEFAULT '',
    issue_date     TEXT,
    due_date       TEXT,
    notes          TEXT DEFAULT '',
    status         TEXT DEFAULT 'draft',
    tax_rate       REAL DEFAULT 0,
    currency       TEXT DEFAULT 'USD',
    created_at     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_invoices_client ON pro_invoices(client_id);

CREATE TABLE IF NOT EXISTS pro_invoice_items (
    id          SERIAL PRIMARY KEY,
    invoice_id  INTEGER NOT NULL REFERENCES pro_invoices(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    quantity    REAL DEFAULT 1,
    unit_price  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pro_items_invoice ON pro_invoice_items(invoice_id);

CREATE TABLE IF NOT EXISTS pro_expenses (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    amount       REAL NOT NULL,
    currency     TEXT DEFAULT 'USD',
    category     TEXT DEFAULT 'other',
    description  TEXT DEFAULT '',
    expense_date TEXT,
    vendor       TEXT DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_expenses_client ON pro_expenses(client_id);

CREATE TABLE IF NOT EXISTS pro_goals (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    quarter     INTEGER DEFAULT 1,
    year        INTEGER DEFAULT 2026,
    status      TEXT DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_goals_client ON pro_goals(client_id);

CREATE TABLE IF NOT EXISTS pro_key_results (
    id       SERIAL PRIMARY KEY,
    goal_id  INTEGER NOT NULL REFERENCES pro_goals(id) ON DELETE CASCADE,
    title    TEXT NOT NULL,
    target   REAL DEFAULT 100,
    current  REAL DEFAULT 0,
    unit     TEXT DEFAULT '%'
);
CREATE INDEX IF NOT EXISTS idx_pro_kr_goal ON pro_key_results(goal_id);
"""

PRO_SQLITE_SCHEMA = PRO_PG_SCHEMA.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT") \
    .replace("TIMESTAMP DEFAULT NOW()", "TEXT DEFAULT (datetime('now', 'localtime'))") \
    .replace("TIMESTAMP", "TEXT") \
    .replace("BOOLEAN DEFAULT TRUE", "INTEGER DEFAULT 1") \
    .replace("REAL DEFAULT 0", "REAL DEFAULT 0") \
    .replace("REAL DEFAULT 1", "REAL DEFAULT 1") \
    .replace("REAL NOT NULL", "REAL NOT NULL")


def init_professional_db():
    """Create pro toolkit tables."""
    with get_db() as db:
        if _USE_PG:
            db.cursor().execute(PRO_PG_SCHEMA)
        else:
            db.executescript(PRO_SQLITE_SCHEMA)
    log.info("Professional toolkit tables initialized.")


# ── Tasks ──────────────────────────────────────────────────

def list_tasks(client_id: int, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM pro_tasks WHERE client_id = %s"
    params: list = [client_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, " \
           "COALESCE(due_date, '9999-12-31'), id DESC"
    with get_db() as db:
        return _fetchall(db, sql, tuple(params))


def create_task(client_id: int, title: str, description: str = "", priority: str = "medium",
                due_date: str = "", project_tag: str = "") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO pro_tasks (client_id, title, description, priority, due_date, project_tag) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, title, description, priority, due_date or None, project_tag),
        )


def update_task(task_id: int, client_id: int, **fields) -> None:
    allowed = {"title", "description", "priority", "status", "due_date", "project_tag", "completed_at"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = %s")
            params.append(v)
    if not sets:
        return
    params += [task_id, client_id]
    with get_db() as db:
        _exec(db, f"UPDATE pro_tasks SET {', '.join(sets)} WHERE id = %s AND client_id = %s", tuple(params))


def delete_task(task_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_tasks WHERE id = %s AND client_id = %s", (task_id, client_id))


# ── Time tracker ──────────────────────────────────────────

def get_running_timer(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(
            db,
            "SELECT * FROM pro_time_entries WHERE client_id = %s AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (client_id,),
        )


def start_timer(client_id: int, project: str, description: str = "",
                billable: bool = True, hourly_rate: float = 0) -> int:
    # Stop any existing running timer first
    running = get_running_timer(client_id)
    if running:
        stop_timer(client_id, running["id"])
    with get_db() as db:
        now_expr = "NOW()" if _USE_PG else "datetime('now', 'localtime')"
        return _insert_returning_id(
            db,
            f"INSERT INTO pro_time_entries (client_id, project, description, started_at, billable, hourly_rate) "
            f"VALUES (%s, %s, %s, {now_expr}, %s, %s) RETURNING id",
            (client_id, project, description, 1 if billable else 0, hourly_rate),
        )


def stop_timer(client_id: int, entry_id: int) -> dict | None:
    with get_db() as db:
        entry = _fetchone(db, "SELECT * FROM pro_time_entries WHERE id = %s AND client_id = %s",
                          (entry_id, client_id))
        if not entry or entry.get("ended_at"):
            return entry
        if _USE_PG:
            _exec(db,
                  "UPDATE pro_time_entries SET ended_at = NOW(), "
                  "duration_seconds = EXTRACT(EPOCH FROM NOW() - started_at)::int "
                  "WHERE id = %s AND client_id = %s",
                  (entry_id, client_id))
        else:
            _exec(db,
                  "UPDATE pro_time_entries SET ended_at = datetime('now', 'localtime'), "
                  "duration_seconds = CAST((julianday('now', 'localtime') - julianday(started_at)) * 86400 AS INTEGER) "
                  "WHERE id = ? AND client_id = ?",
                  (entry_id, client_id))
        return _fetchone(db, "SELECT * FROM pro_time_entries WHERE id = %s", (entry_id,))


def list_time_entries(client_id: int, days: int = 30) -> list[dict]:
    with get_db() as db:
        if _USE_PG:
            return _fetchall(
                db,
                "SELECT * FROM pro_time_entries WHERE client_id = %s "
                "AND started_at >= NOW() - (%s || ' days')::interval "
                "ORDER BY started_at DESC",
                (client_id, days),
            )
        return _fetchall(
            db,
            "SELECT * FROM pro_time_entries WHERE client_id = ? "
            "AND started_at >= datetime('now', 'localtime', ? ) "
            "ORDER BY started_at DESC",
            (client_id, f"-{days} days"),
        )


def delete_time_entry(entry_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_time_entries WHERE id = %s AND client_id = %s",
              (entry_id, client_id))


def time_summary(client_id: int, days: int = 7) -> dict:
    """Return {'total_seconds': int, 'billable_seconds': int, 'by_project': [{project, seconds}]}."""
    with get_db() as db:
        if _USE_PG:
            totals = _fetchone(
                db,
                "SELECT COALESCE(SUM(duration_seconds), 0) AS total, "
                "COALESCE(SUM(CASE WHEN billable THEN duration_seconds ELSE 0 END), 0) AS billable "
                "FROM pro_time_entries WHERE client_id = %s "
                "AND started_at >= NOW() - (%s || ' days')::interval AND ended_at IS NOT NULL",
                (client_id, days),
            )
            by_proj = _fetchall(
                db,
                "SELECT COALESCE(NULLIF(project, ''), '(No project)') AS project, "
                "SUM(duration_seconds) AS seconds "
                "FROM pro_time_entries WHERE client_id = %s "
                "AND started_at >= NOW() - (%s || ' days')::interval AND ended_at IS NOT NULL "
                "GROUP BY project ORDER BY seconds DESC",
                (client_id, days),
            )
        else:
            totals = _fetchone(
                db,
                "SELECT COALESCE(SUM(duration_seconds), 0) AS total, "
                "COALESCE(SUM(CASE WHEN billable = 1 THEN duration_seconds ELSE 0 END), 0) AS billable "
                "FROM pro_time_entries WHERE client_id = ? "
                "AND started_at >= datetime('now', 'localtime', ?) AND ended_at IS NOT NULL",
                (client_id, f"-{days} days"),
            )
            by_proj = _fetchall(
                db,
                "SELECT COALESCE(NULLIF(project, ''), '(No project)') AS project, "
                "SUM(duration_seconds) AS seconds "
                "FROM pro_time_entries WHERE client_id = ? "
                "AND started_at >= datetime('now', 'localtime', ?) AND ended_at IS NOT NULL "
                "GROUP BY project ORDER BY seconds DESC",
                (client_id, f"-{days} days"),
            )
    return {
        "total_seconds": int((totals or {}).get("total") or 0),
        "billable_seconds": int((totals or {}).get("billable") or 0),
        "by_project": by_proj or [],
    }


# ── Invoices ───────────────────────────────────────────────

def list_invoices(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(
            db,
            "SELECT i.*, "
            "(SELECT COALESCE(SUM(quantity * unit_price), 0) FROM pro_invoice_items WHERE invoice_id = i.id) AS subtotal "
            "FROM pro_invoices i WHERE client_id = %s ORDER BY issue_date DESC NULLS LAST, id DESC"
            if _USE_PG else
            "SELECT i.*, "
            "(SELECT COALESCE(SUM(quantity * unit_price), 0) FROM pro_invoice_items WHERE invoice_id = i.id) AS subtotal "
            "FROM pro_invoices i WHERE client_id = ? ORDER BY COALESCE(issue_date, '9999-12-31') DESC, id DESC",
            (client_id,),
        )


def get_invoice(invoice_id: int, client_id: int) -> dict | None:
    with get_db() as db:
        inv = _fetchone(db, "SELECT * FROM pro_invoices WHERE id = %s AND client_id = %s",
                        (invoice_id, client_id))
        if not inv:
            return None
        inv["items"] = _fetchall(db,
                                 "SELECT * FROM pro_invoice_items WHERE invoice_id = %s ORDER BY id",
                                 (invoice_id,))
        return inv


def create_invoice(client_id: int, data: dict) -> int:
    with get_db() as db:
        iid = _insert_returning_id(
            db,
            "INSERT INTO pro_invoices (client_id, invoice_number, bill_to_name, bill_to_email, "
            "bill_to_addr, issue_date, due_date, notes, status, tax_rate, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                client_id,
                data.get("invoice_number", ""),
                data.get("bill_to_name", ""),
                data.get("bill_to_email", ""),
                data.get("bill_to_addr", ""),
                data.get("issue_date") or None,
                data.get("due_date") or None,
                data.get("notes", ""),
                data.get("status", "draft"),
                float(data.get("tax_rate") or 0),
                data.get("currency", "USD"),
            ),
        )
        for it in data.get("items", []):
            if not (it.get("description") or "").strip():
                continue
            _exec(db,
                  "INSERT INTO pro_invoice_items (invoice_id, description, quantity, unit_price) "
                  "VALUES (%s, %s, %s, %s)",
                  (iid, it["description"], float(it.get("quantity") or 1), float(it.get("unit_price") or 0)))
        return iid


def update_invoice(invoice_id: int, client_id: int, data: dict) -> None:
    with get_db() as db:
        exists = _fetchone(db, "SELECT id FROM pro_invoices WHERE id = %s AND client_id = %s",
                           (invoice_id, client_id))
        if not exists:
            return
        _exec(db,
              "UPDATE pro_invoices SET invoice_number=%s, bill_to_name=%s, bill_to_email=%s, "
              "bill_to_addr=%s, issue_date=%s, due_date=%s, notes=%s, status=%s, tax_rate=%s, currency=%s "
              "WHERE id=%s",
              (
                  data.get("invoice_number", ""),
                  data.get("bill_to_name", ""),
                  data.get("bill_to_email", ""),
                  data.get("bill_to_addr", ""),
                  data.get("issue_date") or None,
                  data.get("due_date") or None,
                  data.get("notes", ""),
                  data.get("status", "draft"),
                  float(data.get("tax_rate") or 0),
                  data.get("currency", "USD"),
                  invoice_id,
              ))
        _exec(db, "DELETE FROM pro_invoice_items WHERE invoice_id = %s", (invoice_id,))
        for it in data.get("items", []):
            if not (it.get("description") or "").strip():
                continue
            _exec(db,
                  "INSERT INTO pro_invoice_items (invoice_id, description, quantity, unit_price) "
                  "VALUES (%s, %s, %s, %s)",
                  (invoice_id, it["description"], float(it.get("quantity") or 1),
                   float(it.get("unit_price") or 0)))


def delete_invoice(invoice_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_invoices WHERE id = %s AND client_id = %s",
              (invoice_id, client_id))


def next_invoice_number(client_id: int) -> str:
    with get_db() as db:
        n = _fetchval(db, "SELECT COUNT(*) FROM pro_invoices WHERE client_id = %s",
                      (client_id,)) or 0
    return f"INV-{int(n) + 1:04d}"


# ── Expenses ──────────────────────────────────────────────

EXPENSE_CATEGORIES = [
    "software", "travel", "meals", "office", "marketing",
    "contractors", "education", "hardware", "taxes", "other",
]


def list_expenses(client_id: int, days: int = 90) -> list[dict]:
    with get_db() as db:
        if _USE_PG:
            return _fetchall(
                db,
                "SELECT * FROM pro_expenses WHERE client_id = %s "
                "AND (expense_date IS NULL OR expense_date::date >= CURRENT_DATE - (%s || ' days')::interval) "
                "ORDER BY expense_date DESC NULLS LAST, id DESC",
                (client_id, days),
            )
        return _fetchall(
            db,
            "SELECT * FROM pro_expenses WHERE client_id = ? "
            "AND (expense_date IS NULL OR expense_date >= date('now', 'localtime', ?)) "
            "ORDER BY COALESCE(expense_date, '0000') DESC, id DESC",
            (client_id, f"-{days} days"),
        )


def create_expense(client_id: int, amount: float, category: str, description: str,
                   expense_date: str = "", vendor: str = "", currency: str = "USD") -> int:
    with get_db() as db:
        return _insert_returning_id(
            db,
            "INSERT INTO pro_expenses (client_id, amount, currency, category, description, expense_date, vendor) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, float(amount), currency, category, description, expense_date or None, vendor),
        )


def delete_expense(expense_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_expenses WHERE id = %s AND client_id = %s",
              (expense_id, client_id))


def expense_summary(client_id: int, days: int = 30) -> dict:
    with get_db() as db:
        if _USE_PG:
            total = _fetchval(
                db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_expenses WHERE client_id = %s "
                "AND (expense_date IS NULL OR expense_date::date >= CURRENT_DATE - (%s || ' days')::interval)",
                (client_id, days),
            )
            by_cat = _fetchall(
                db,
                "SELECT category, COALESCE(SUM(amount), 0) AS total "
                "FROM pro_expenses WHERE client_id = %s "
                "AND (expense_date IS NULL OR expense_date::date >= CURRENT_DATE - (%s || ' days')::interval) "
                "GROUP BY category ORDER BY total DESC",
                (client_id, days),
            )
        else:
            total = _fetchval(
                db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_expenses WHERE client_id = ? "
                "AND (expense_date IS NULL OR expense_date >= date('now', 'localtime', ?))",
                (client_id, f"-{days} days"),
            )
            by_cat = _fetchall(
                db,
                "SELECT category, COALESCE(SUM(amount), 0) AS total "
                "FROM pro_expenses WHERE client_id = ? "
                "AND (expense_date IS NULL OR expense_date >= date('now', 'localtime', ?)) "
                "GROUP BY category ORDER BY total DESC",
                (client_id, f"-{days} days"),
            )
    return {"total": float(total or 0), "by_category": by_cat or []}


# ── Goals / OKRs ──────────────────────────────────────────

def list_goals(client_id: int) -> list[dict]:
    with get_db() as db:
        goals = _fetchall(db,
                          "SELECT * FROM pro_goals WHERE client_id = %s "
                          "ORDER BY year DESC, quarter DESC, id DESC",
                          (client_id,))
        for g in goals:
            g["key_results"] = _fetchall(
                db, "SELECT * FROM pro_key_results WHERE goal_id = %s ORDER BY id",
                (g["id"],))
            if g["key_results"]:
                pct = sum(min(100, (kr["current"] or 0) / (kr["target"] or 1) * 100)
                          for kr in g["key_results"]) / len(g["key_results"])
            else:
                pct = 0
            g["progress_pct"] = round(pct, 1)
        return goals


def create_goal(client_id: int, title: str, description: str, quarter: int, year: int,
                key_results: list[dict]) -> int:
    with get_db() as db:
        gid = _insert_returning_id(
            db,
            "INSERT INTO pro_goals (client_id, title, description, quarter, year) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (client_id, title, description, int(quarter), int(year)),
        )
        for kr in key_results:
            if not (kr.get("title") or "").strip():
                continue
            _exec(db,
                  "INSERT INTO pro_key_results (goal_id, title, target, current, unit) "
                  "VALUES (%s, %s, %s, %s, %s)",
                  (gid, kr["title"], float(kr.get("target") or 100),
                   float(kr.get("current") or 0), kr.get("unit") or "%"))
        return gid


def update_key_result(kr_id: int, client_id: int, current: float) -> None:
    with get_db() as db:
        # Verify ownership via join
        owned = _fetchone(
            db,
            "SELECT kr.id FROM pro_key_results kr "
            "JOIN pro_goals g ON kr.goal_id = g.id "
            "WHERE kr.id = %s AND g.client_id = %s",
            (kr_id, client_id),
        )
        if not owned:
            return
        _exec(db, "UPDATE pro_key_results SET current = %s WHERE id = %s",
              (float(current), kr_id))


def delete_goal(goal_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_goals WHERE id = %s AND client_id = %s",
              (goal_id, client_id))


def update_goal_status(goal_id: int, client_id: int, status: str) -> None:
    with get_db() as db:
        _exec(db, "UPDATE pro_goals SET status = %s WHERE id = %s AND client_id = %s",
              (status, goal_id, client_id))
