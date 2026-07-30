from contextlib import contextmanager

import student.db as sdb


def test_streak_risk_recipients_treats_retired_as_integer_on_postgres(monkeypatch):
    queries: list[str] = []

    @contextmanager
    def fake_get_db():
        yield object()

    def postgres_fetchall(_db, sql, _params=()):
        queries.append(sql)
        if "COALESCE(retired, FALSE)" in sql:
            raise RuntimeError(
                "COALESCE types integer and boolean cannot be matched"
            )
        return []

    monkeypatch.setattr(sdb, "_USE_PG", True)
    monkeypatch.setattr(sdb, "get_db", fake_get_db)
    monkeypatch.setattr(sdb, "_fetchall", postgres_fetchall)

    assert sdb.get_streak_risk_recipients(min_streak=5) == []
    assert queries
    assert "COALESCE(retired, 0) = 0" in queries[0]
