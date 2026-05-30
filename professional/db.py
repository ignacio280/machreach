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

CREATE TABLE IF NOT EXISTS pro_bank_connections (
    id               SERIAL PRIMARY KEY,
    client_id        INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    provider         TEXT DEFAULT 'manual',
    institution_name TEXT DEFAULT '',
    account_name     TEXT DEFAULT '',
    account_type     TEXT DEFAULT 'checking',
    last_4           TEXT DEFAULT '',
    balance          REAL DEFAULT 0,
    currency         TEXT DEFAULT 'USD',
    status           TEXT DEFAULT 'active',
    connected_at     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_bank_client ON pro_bank_connections(client_id);

CREATE TABLE IF NOT EXISTS pro_transactions (
    id                 SERIAL PRIMARY KEY,
    client_id          INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    bank_connection_id INTEGER REFERENCES pro_bank_connections(id) ON DELETE SET NULL,
    amount             REAL NOT NULL,
    currency           TEXT DEFAULT 'USD',
    merchant           TEXT DEFAULT '',
    category           TEXT DEFAULT 'other',
    tx_date            TEXT,
    description        TEXT DEFAULT '',
    is_manual          BOOLEAN DEFAULT TRUE,
    created_at         TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_tx_client ON pro_transactions(client_id, tx_date DESC);

CREATE TABLE IF NOT EXISTS pro_budgets (
    id             SERIAL PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name           TEXT DEFAULT 'Monthly Budget',
    period         TEXT DEFAULT 'monthly',
    income         REAL DEFAULT 0,
    savings_goal   REAL DEFAULT 0,
    currency       TEXT DEFAULT 'USD',
    preferences    TEXT DEFAULT '',
    ai_plan        TEXT DEFAULT '',
    created_at     TIMESTAMP DEFAULT NOW(),
    updated_at     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pro_budget_client ON pro_budgets(client_id);

CREATE TABLE IF NOT EXISTS pro_relationship_notes (
    id                   SERIAL PRIMARY KEY,
    client_id            INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    contact_email        TEXT NOT NULL,
    contact_name         TEXT DEFAULT '',
    contact_role         TEXT DEFAULT '',
    company              TEXT DEFAULT '',
    notes                TEXT DEFAULT '',
    ai_summary           TEXT DEFAULT '',
    last_summary_at      TIMESTAMP,
    last_interaction_at  TEXT,
    reconnect_after_days INTEGER DEFAULT 90,
    pinned               BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMP DEFAULT NOW(),
    UNIQUE(client_id, contact_email)
);
CREATE INDEX IF NOT EXISTS idx_pro_rel_client ON pro_relationship_notes(client_id);
"""

PRO_SQLITE_SCHEMA = PRO_PG_SCHEMA.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT") \
    .replace("TIMESTAMP DEFAULT NOW()", "TEXT DEFAULT (datetime('now', 'localtime'))") \
    .replace("TIMESTAMP", "TEXT") \
    .replace("BOOLEAN DEFAULT TRUE", "INTEGER DEFAULT 1") \
    .replace("REAL DEFAULT 0", "REAL DEFAULT 0") \
    .replace("REAL DEFAULT 1", "REAL DEFAULT 1") \
    .replace("REAL NOT NULL", "REAL NOT NULL")






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




# ------------------------------------------------------------
# Tasks helpers (for notifications + email)
# ------------------------------------------------------------

def tasks_due_today(client_id: int) -> list[dict]:
    """Return unfinished tasks with due_date == today."""
    with get_db() as db:
        if _USE_PG:
            return _fetchall(db,
                "SELECT * FROM pro_tasks WHERE client_id = %s AND status != 'done' "
                "AND due_date::date = CURRENT_DATE ORDER BY id DESC",
                (client_id,))
        return _fetchall(db,
            "SELECT * FROM pro_tasks WHERE client_id = ? AND status != 'done' "
            "AND due_date = date('now', 'localtime') ORDER BY id DESC",
            (client_id,))


def tasks_overdue(client_id: int) -> list[dict]:
    with get_db() as db:
        if _USE_PG:
            return _fetchall(db,
                "SELECT * FROM pro_tasks WHERE client_id = %s AND status != 'done' "
                "AND due_date IS NOT NULL AND due_date::date < CURRENT_DATE ORDER BY due_date ASC",
                (client_id,))
        return _fetchall(db,
            "SELECT * FROM pro_tasks WHERE client_id = ? AND status != 'done' "
            "AND due_date IS NOT NULL AND due_date < date('now', 'localtime') ORDER BY due_date ASC",
            (client_id,))


# ------------------------------------------------------------
# Bank connections + transactions + budgets
# ------------------------------------------------------------

TRANSACTION_CATEGORIES = [
    "food_dining", "groceries", "transportation", "shopping", "entertainment",
    "subscriptions", "utilities", "rent_mortgage", "health", "travel",
    "education", "business", "income", "transfer", "other",
]


def list_bank_connections(client_id: int) -> list[dict]:
    with get_db() as db:
        return _fetchall(db,
            "SELECT * FROM pro_bank_connections WHERE client_id = %s ORDER BY id DESC",
            (client_id,))


def create_bank_connection(client_id: int, institution_name: str, account_name: str = "",
                           account_type: str = "checking", last_4: str = "",
                           balance: float = 0, currency: str = "USD",
                           provider: str = "manual") -> int:
    with get_db() as db:
        return _insert_returning_id(db,
            "INSERT INTO pro_bank_connections "
            "(client_id, provider, institution_name, account_name, account_type, last_4, balance, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, provider, institution_name, account_name, account_type, last_4, float(balance), currency))


def delete_bank_connection(conn_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_bank_connections WHERE id = %s AND client_id = %s",
              (conn_id, client_id))


def update_bank_balance(conn_id: int, client_id: int, balance: float) -> None:
    with get_db() as db:
        _exec(db, "UPDATE pro_bank_connections SET balance = %s WHERE id = %s AND client_id = %s",
              (float(balance), conn_id, client_id))


def update_bank_income_settings(conn_id: int, client_id: int, monthly_income: float, income_day: int) -> None:
    """Set per-card recurring monthly income amount + day of month to credit it."""
    income_day = max(1, min(28, int(income_day or 1)))  # 28 = safe last day all months
    with get_db() as db:
        _exec(db,
              "UPDATE pro_bank_connections SET monthly_income = %s, income_day = %s "
              "WHERE id = %s AND client_id = %s",
              (float(monthly_income or 0), income_day, conn_id, client_id))


def apply_recurring_income(client_id: int | None = None) -> int:
    """
    For every bank account with monthly_income > 0, credit the income on/after its
    income_day each month. Idempotent — uses last_income_date to avoid double-crediting.

    Returns number of income deposits created.
    """
    from datetime import date
    today = date.today()
    today_iso = today.strftime("%Y-%m-%d")
    period_key = today.strftime("%Y-%m")

    with get_db() as db:
        sql = "SELECT * FROM pro_bank_connections WHERE COALESCE(monthly_income, 0) > 0"
        params: tuple = ()
        if client_id is not None:
            sql += " AND client_id = %s"
            params = (client_id,)
        accounts = _fetchall(db, sql, params)

    created = 0
    for a in accounts:
        try:
            day = int(a.get("income_day") or 1)
        except Exception:
            day = 1
        if today.day < day:
            continue  # not yet this month

        last = (a.get("last_income_date") or "")[:7]  # YYYY-MM
        if last == period_key:
            continue  # already credited this month

        amount = float(a.get("monthly_income") or 0)
        if amount <= 0:
            continue

        merchant = "Recurring income · " + (a.get("institution_name") or "Account")
        create_transaction(
            client_id=int(a["client_id"]),
            amount=amount,
            merchant=merchant,
            category="income",
            tx_date=today_iso,
            bank_connection_id=int(a["id"]),
            description="Auto-credited monthly income",
            currency=(a.get("currency") or "USD"),
        )
        # Bump card balance + mark as credited this month
        new_balance = float(a.get("balance") or 0) + amount
        with get_db() as db:
            _exec(db,
                  "UPDATE pro_bank_connections SET balance = %s, last_income_date = %s "
                  "WHERE id = %s",
                  (new_balance, today_iso, int(a["id"])))
        created += 1
    return created


def list_transactions(client_id: int, days: int = 90, connection_id: int | None = None) -> list[dict]:
    with get_db() as db:
        if _USE_PG:
            sql = ("SELECT t.*, c.institution_name, c.last_4 FROM pro_transactions t "
                   "LEFT JOIN pro_bank_connections c ON c.id = t.bank_connection_id "
                   "WHERE t.client_id = %s AND (t.tx_date IS NULL OR t.tx_date::date >= CURRENT_DATE - (%s || ' days')::interval)")
            params: list = [client_id, days]
            if connection_id:
                sql += " AND t.bank_connection_id = %s"
                params.append(connection_id)
            sql += " ORDER BY t.tx_date DESC, t.id DESC"
            return _fetchall(db, sql, tuple(params))
        sql = ("SELECT t.*, c.institution_name, c.last_4 FROM pro_transactions t "
               "LEFT JOIN pro_bank_connections c ON c.id = t.bank_connection_id "
               "WHERE t.client_id = ? AND (t.tx_date IS NULL OR t.tx_date >= date('now', 'localtime', ?))")
        params = [client_id, f"-{days} days"]
        if connection_id:
            sql += " AND t.bank_connection_id = ?"
            params.append(connection_id)
        sql += " ORDER BY t.tx_date DESC, t.id DESC"
        return _fetchall(db, sql, tuple(params))


def create_transaction(client_id: int, amount: float, merchant: str = "", category: str = "other",
                       tx_date: str = "", description: str = "", currency: str = "USD",
                       bank_connection_id: int | None = None, is_manual: bool = True) -> int:
    with get_db() as db:
        return _insert_returning_id(db,
            "INSERT INTO pro_transactions "
            "(client_id, bank_connection_id, amount, currency, merchant, category, tx_date, description, is_manual) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, bank_connection_id, float(amount), currency, merchant, category,
             tx_date or None, description, bool(is_manual)))


def delete_transaction(tx_id: int, client_id: int) -> None:
    with get_db() as db:
        _exec(db, "DELETE FROM pro_transactions WHERE id = %s AND client_id = %s",
              (tx_id, client_id))


def spending_summary(client_id: int, days: int = 30) -> dict:
    with get_db() as db:
        if _USE_PG:
            total_spent = _fetchval(db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_transactions "
                "WHERE client_id = %s AND amount > 0 AND category != 'income' "
                "AND (tx_date IS NULL OR tx_date::date >= CURRENT_DATE - (%s || ' days')::interval)",
                (client_id, days))
            total_income = _fetchval(db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_transactions "
                "WHERE client_id = %s AND category = 'income' "
                "AND (tx_date IS NULL OR tx_date::date >= CURRENT_DATE - (%s || ' days')::interval)",
                (client_id, days))
            by_cat = _fetchall(db,
                "SELECT category, COALESCE(SUM(amount), 0) AS total FROM pro_transactions "
                "WHERE client_id = %s AND category != 'income' AND amount > 0 "
                "AND (tx_date IS NULL OR tx_date::date >= CURRENT_DATE - (%s || ' days')::interval) "
                "GROUP BY category ORDER BY total DESC",
                (client_id, days))
            by_merchant = _fetchall(db,
                "SELECT merchant, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count FROM pro_transactions "
                "WHERE client_id = %s AND amount > 0 AND category != 'income' AND merchant != '' "
                "AND (tx_date IS NULL OR tx_date::date >= CURRENT_DATE - (%s || ' days')::interval) "
                "GROUP BY merchant ORDER BY total DESC LIMIT 10",
                (client_id, days))
        else:
            total_spent = _fetchval(db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_transactions "
                "WHERE client_id = ? AND amount > 0 AND category != 'income' "
                "AND (tx_date IS NULL OR tx_date >= date('now', 'localtime', ?))",
                (client_id, f"-{days} days"))
            total_income = _fetchval(db,
                "SELECT COALESCE(SUM(amount), 0) FROM pro_transactions "
                "WHERE client_id = ? AND category = 'income' "
                "AND (tx_date IS NULL OR tx_date >= date('now', 'localtime', ?))",
                (client_id, f"-{days} days"))
            by_cat = _fetchall(db,
                "SELECT category, COALESCE(SUM(amount), 0) AS total FROM pro_transactions "
                "WHERE client_id = ? AND category != 'income' AND amount > 0 "
                "AND (tx_date IS NULL OR tx_date >= date('now', 'localtime', ?)) "
                "GROUP BY category ORDER BY total DESC",
                (client_id, f"-{days} days"))
            by_merchant = _fetchall(db,
                "SELECT merchant, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count FROM pro_transactions "
                "WHERE client_id = ? AND amount > 0 AND category != 'income' AND merchant != '' "
                "AND (tx_date IS NULL OR tx_date >= date('now', 'localtime', ?)) "
                "GROUP BY merchant ORDER BY total DESC LIMIT 10",
                (client_id, f"-{days} days"))
    return {
        "total_spent": float(total_spent or 0),
        "total_income": float(total_income or 0),
        "net": float(total_income or 0) - float(total_spent or 0),
        "by_category": by_cat or [],
        "top_merchants": by_merchant or [],
    }




# -- Budgets ---------------------------------------------

def get_budget(client_id: int) -> dict | None:
    with get_db() as db:
        return _fetchone(db,
            "SELECT * FROM pro_budgets WHERE client_id = %s ORDER BY id DESC LIMIT 1",
            (client_id,))


def upsert_budget(client_id: int, income: float, savings_goal: float,
                  preferences: str = "", ai_plan: str = "", currency: str = "USD") -> int:
    with get_db() as db:
        existing = _fetchone(db, "SELECT id FROM pro_budgets WHERE client_id = %s",
                             (client_id,))
        now_expr = "NOW()" if _USE_PG else "datetime('now','localtime')"
        if existing:
            _exec(db,
                  f"UPDATE pro_budgets SET income = %s, savings_goal = %s, preferences = %s, "
                  f"ai_plan = %s, currency = %s, updated_at = {now_expr} WHERE id = %s",
                  (float(income), float(savings_goal), preferences, ai_plan, currency, existing["id"]))
            return existing["id"]
        return _insert_returning_id(db,
            "INSERT INTO pro_budgets (client_id, income, savings_goal, preferences, ai_plan, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (client_id, float(income), float(savings_goal), preferences, ai_plan, currency))


# ------------------------------------------------------------
# Relationship Intelligence
# ------------------------------------------------------------

def list_relationship_contacts(client_id: int, limit: int = 100) -> list[dict]:
    """Aggregate distinct senders from mail_inbox + pro notes = contact map."""
    with get_db() as db:
        rows = _fetchall(db, """
            SELECT from_email, MAX(from_name) AS from_name, MAX(received_at) AS last_at,
                   COUNT(*) AS msg_count
            FROM mail_inbox
            WHERE client_id = %s AND from_email IS NOT NULL AND from_email != ''
            GROUP BY from_email ORDER BY MAX(received_at) DESC LIMIT %s
        """, (client_id, limit))
        notes = _fetchall(db,
            "SELECT * FROM pro_relationship_notes WHERE client_id = %s", (client_id,))
        notes_map = {n["contact_email"]: n for n in notes}
        out = []
        for r in rows:
            n = notes_map.get(r["from_email"])
            out.append({
                "email": r["from_email"],
                "name": (n["contact_name"] if n and n.get("contact_name") else (r.get("from_name") or "")),
                "role": (n or {}).get("contact_role", ""),
                "company": (n or {}).get("company", ""),
                "last_at": r["last_at"],
                "msg_count": r["msg_count"],
                "ai_summary": (n or {}).get("ai_summary", ""),
                "pinned": bool((n or {}).get("pinned")),
                "note_id": (n or {}).get("id"),
            })
        # Include pinned contacts not yet in inbox
        seen = {c["email"] for c in out}
        for n in notes:
            if n["contact_email"] not in seen and n.get("pinned"):
                out.append({
                    "email": n["contact_email"], "name": n.get("contact_name",""),
                    "role": n.get("contact_role",""), "company": n.get("company",""),
                    "last_at": None, "msg_count": 0,
                    "ai_summary": n.get("ai_summary",""), "pinned": True, "note_id": n["id"],
                })
        return out


def get_relationship_note(client_id: int, email: str) -> dict | None:
    with get_db() as db:
        return _fetchone(db,
            "SELECT * FROM pro_relationship_notes WHERE client_id = %s AND contact_email = %s",
            (client_id, email))


def upsert_relationship_note(client_id: int, email: str, **fields) -> int:
    allowed = {"contact_name", "contact_role", "company", "notes", "ai_summary",
               "last_summary_at", "last_interaction_at", "reconnect_after_days", "pinned"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    with get_db() as db:
        existing = _fetchone(db,
            "SELECT id FROM pro_relationship_notes WHERE client_id = %s AND contact_email = %s",
            (client_id, email))
        if existing:
            if clean:
                sets = ", ".join(f"{k} = %s" for k in clean)
                params = list(clean.values()) + [existing["id"]]
                _exec(db, f"UPDATE pro_relationship_notes SET {sets} WHERE id = %s",
                      tuple(params))
            return existing["id"]
        cols = ["client_id", "contact_email"] + list(clean.keys())
        vals = [client_id, email] + list(clean.values())
        placeholders = ", ".join(["%s"] * len(cols))
        return _insert_returning_id(db,
            f"INSERT INTO pro_relationship_notes ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
            tuple(vals))


