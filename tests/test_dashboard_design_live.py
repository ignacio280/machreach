import json
import re
from datetime import date
from pathlib import Path

from machreach_core.db import _exec, get_db


def _student(client, make_user, *, name="Dashboard Student", lang="es"):
    client_id = make_user(name=name)
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = name
        session["account_type"] = "student"
        session["session_version"] = 0
        session["lang"] = lang
    return client_id


def _payload(markup):
    match = re.search(r"window\.__MACHREACH_DASHBOARD__=(\{.*?\});</script>", markup, re.S)
    assert match, "the live dashboard payload is missing"
    return json.loads(match.group(1))


def test_live_dashboard_uses_supplied_design_shell_and_real_identity(client, make_user):
    _student(client, make_user, name="Valentina Real")

    response = client.get("/student")
    body = response.get_data(as_text=True)
    data = _payload(body)

    assert response.status_code == 200
    assert '<div id="root"></div>' in body
    assert "/static/machreach_app/dashboard.bundle.min.js" in body
    assert "/static/machreach_app/app/dash.css" in body
    assert 'class="mr-app-shell app top-shell"' not in body
    assert data["live"] is True
    assert data["avatar"] == "VR"
    assert "Valentina" in data["mission"]["headline"]
    assert "Sofía" not in body


def test_dashboard_payload_contains_every_supplied_dashboard_section(client, make_user):
    _student(client, make_user)
    data = _payload(client.get("/student").get_data(as_text=True))

    assert set(("mission", "stats", "plan", "courses", "exams", "league", "level", "streak_data", "friends")) <= data.keys()
    assert len(data["stats"]) == 4
    assert len(data["streak_data"]["heat"]) == 35
    assert data["plan"]["toggle_url"] == "/api/student/assignments/toggle"
    assert data["plan"]["csrf"]


def test_dashboard_payload_is_english_when_student_language_is_english(client, make_user):
    client_id = _student(client, make_user, lang="en")
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_study_progress "
            "(client_id, plan_date, focus_minutes, pages_read, notes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (client_id, "2026-07-29", 90, 0, "English weekday contract"),
        )
    from student import db as sdb
    original_user_date = sdb.user_date
    sdb.user_date = lambda _client_id: date(2026, 7, 31)
    try:
        data = _payload(client.get("/student").get_data(as_text=True))
    finally:
        sdb.user_date = original_user_date

    assert data["title"] == "Home"
    assert data["courses"]["title"] == "My courses"
    assert data["streak_data"]["days_label"] == "days in a row"
    assert data["stats"][1]["sub"] == "Wed"


def test_dashboard_weekly_league_keeps_exact_rank_and_rank_centred_window(
    client, make_user, monkeypatch
):
    client_id = _student(client, make_user)
    from student import academic as academic

    rows = [
        {
            "rank": rank,
            "client_id": client_id if rank == 53 else 10000 + rank,
            "name": "Dashboard Student" if rank == 53 else f"Student {rank}",
            "xp": 1000 - rank,
        }
        for rank in range(1, 56)
    ]
    monkeypatch.setattr(academic, "my_rank", lambda *a, **k: {"rank": 53, "total": 80, "xp": 947})
    monkeypatch.setattr(academic, "leaderboard", lambda *a, **k: rows)
    monkeypatch.setattr(academic, "league_for_xp", lambda xp: {"name": "Cobre"})

    data = _payload(client.get("/student").get_data(as_text=True))

    assert data["league"]["rank"] == "53º"
    assert data["league"]["rank_copy"] == "de 80"
    assert data["league"]["title"] == "Cobre"
    assert [row["r"] for row in data["league"]["board"]] == [51, 52, 53, 54, 55]
    assert data["league"]["board"][2]["xp"] == "947"


def test_dashboard_friend_cards_use_real_friend_xp(client, make_user, monkeypatch):
    _student(client, make_user)
    from student import db as sdb

    monkeypatch.setattr(sdb, "list_friends", lambda cid: {
        "friends": [{"id": 99, "name": "Amiga Real", "online": True}],
        "incoming": [],
        "outgoing": [],
    })
    monkeypatch.setattr(sdb, "get_friend_leaderboard", lambda cid: [
        {"client_id": 99, "name": "Amiga Real", "total_xp": 1234},
    ])

    data = _payload(client.get("/student").get_data(as_text=True))
    assert data["friends"]["items"][0]["xp"] == "1.234"


def test_plan_toggle_source_rolls_back_and_reports_failed_writes():
    source = (Path(__file__).parents[1] / "static/machreach_app/app/dash-left.jsx").read_text(encoding="utf-8")
    assert "if (!response.ok) throw" in source
    assert "setDone((d) => ({ ...d, [i]: !next }))" in source
    assert 'role="alert"' in source


def test_dashboard_plan_csrf_token_authorizes_real_toggle_endpoint(client, make_user):
    _student(client, make_user)
    data = _payload(client.get("/student").get_data(as_text=True))

    response = client.post(
        data["plan"]["toggle_url"],
        json={"date": data["plan"]["date"], "session_index": 0, "completed": True},
        headers={"X-CSRFToken": data["plan"]["csrf"]},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_non_dashboard_pages_do_not_load_dashboard_design_styles(client, make_user):
    _student(client, make_user)
    body = client.get("/student/courses").get_data(as_text=True)
    assert "/static/machreach_layout/layout-base.css" in body
    assert "/static/machreach_layout/design.css" not in body
