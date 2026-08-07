import pytest
from datetime import UTC, datetime, timedelta

from machreach_core.db import _exec, _fetchall, _fetchone, get_db
from student.periods import FrozenClock, use_clock, user_date


@pytest.fixture()
def academic_student(client, make_user, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Academic Student")
    with get_db() as db:
        _exec(db, "UPDATE clients SET email_verified = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Academic Student"
        session["account_type"] = "student"
        session["session_version"] = 0
    return client_id


@pytest.mark.parametrize("path", [
    "/api/academic/countries", "/api/academic/universities?country=CL",
    "/api/academic/majors?q=engineering", "/api/academic/profile",
    "/api/academic/leaderboard", "/api/academic/ranks", "/api/academic/analytics",
])
def test_academic_routes_require_authentication(client, path):
    assert client.get(path).status_code == 401


def test_academic_catalog_profile_and_leaderboards(client, academic_student):
    countries = client.get("/api/academic/countries").get_json()["countries"]
    assert countries and {c["iso_code"] for c in countries} == {"CL"}

    assert client.get("/api/academic/universities").status_code == 400
    universities = client.get("/api/academic/universities?country=CL&q=").get_json()["universities"]
    assert universities
    university_id = universities[0]["id"]

    assert client.get("/api/academic/majors").get_json() == {"majors": []}
    majors = client.get(f"/api/academic/majors?university_id={university_id}").get_json()["majors"]
    assert majors
    major_id = majors[0]["id"]

    saved = client.post("/api/academic/profile", json={
        "country_iso": "cl", "university_id": university_id, "major_id": major_id,
    })
    assert saved.status_code == 200
    profile = client.get("/api/academic/profile").get_json()
    assert profile["needs_setup"] is False
    assert profile["university"]["id"] == university_id
    assert profile["major"]["id"] == major_id

    assert client.post("/api/academic/starter-tutorial/seen").get_json()["ok"] is True
    # The "your XP is intact" welcome banner is gone, and so is its endpoint.
    assert client.post("/api/academic/banner/seen").status_code == 404
    assert client.post("/api/academic/universities", json={}).status_code == 403
    assert client.post("/api/academic/majors", json={}).status_code == 403

    public = client.get(f"/api/academic/user/{academic_student}")
    assert public.status_code == 200
    assert "email" not in public.get_json()
    assert client.get("/api/academic/user/99999999").status_code == 404

    assert client.get("/api/academic/leaderboard?scope=invalid").status_code == 400
    board = client.get("/api/academic/leaderboard?scope=country&period=week").get_json()
    assert board["scope"] == "country"
    assert board["period"] == "week"
    ranks = client.get("/api/academic/ranks?period=month").get_json()
    assert ranks["period"] == "month"
    assert "league" in ranks


@pytest.mark.parametrize("payload", [
    {}, {"country_iso": "CL", "university_id": "bad", "major_id": 1},
    {"country_iso": "", "university_id": 1, "major_id": 1},
])
def test_academic_profile_rejects_invalid_payloads(client, academic_student, payload):
    assert client.post("/api/academic/profile", json=payload).status_code == 400


def test_academic_profile_rejects_cross_country_university(client, academic_student):
    university = client.get(
        "/api/academic/universities?country=CL&q="
    ).get_json()["universities"][0]
    major = client.get(
        f"/api/academic/majors?university_id={university['id']}"
    ).get_json()["majors"][0]

    response = client.post(
        "/api/academic/profile",
        json={
            "country_iso": "US",
            "university_id": university["id"],
            "major_id": major["id"],
        },
    )

    assert response.status_code == 400


def test_university_changes_are_audited_and_rate_limited(
    client, academic_student
):
    with get_db() as db:
        universities = _fetchall(
            db,
            "SELECT id FROM universities WHERE country_iso = %s "
            "AND status = %s ORDER BY id LIMIT 3",
            ("CL", "approved"),
        )
        selections = []
        for university in universities:
            major = _fetchone(
                db,
                "SELECT id FROM majors WHERE university_id = %s "
                "AND status = %s ORDER BY id LIMIT 1",
                (university["id"], "approved"),
            )
            assert major
            selections.append((university["id"], major["id"]))

    def save(selection):
        return client.post(
            "/api/academic/profile",
            json={
                "country_iso": "CL",
                "university_id": selection[0],
                "major_id": selection[1],
            },
        )

    with use_clock(FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))):
        assert save(selections[0]).status_code == 200
        assert save(selections[1]).status_code == 200
    with use_clock(FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))):
        blocked = save(selections[2])
    assert blocked.status_code == 400
    assert "30 days" in blocked.get_json()["error"]

    with use_clock(FrozenClock(datetime(2026, 2, 1, tzinfo=UTC))):
        assert save(selections[2]).status_code == 200

    with get_db() as db:
        rows = _fetchall(
            db,
            "SELECT old_university_id, new_university_id, changed_at_utc "
            "FROM student_academic_profile_audit WHERE client_id = %s ORDER BY id",
            (academic_student,),
        )
    assert len(rows) == 3
    assert rows[1]["old_university_id"] == selections[0][0]
    assert rows[1]["new_university_id"] == selections[1][0]


def test_academic_analytics_returns_sanitized_zero_state_and_clamps_future_week(client, academic_student):
    current = client.get("/api/academic/analytics?week_offset=bad").get_json()
    future = client.get("/api/academic/analytics?week_offset=5").get_json()

    assert current["totals"]["minutes"] == 0
    assert len(current["last_14_days"]) == 14
    assert len(current["dow_dist"]) == 7
    assert future["week_offset"] == 0


def test_academic_analytics_aggregates_real_sessions_and_skips_bad_dates(
    client, academic_student
):
    # The endpoint buckets every day with user_date(), i.e. the account's own
    # timezone — America/Santiago for this CL student. Seeding from
    # datetime.now() instead compared that against the runner's clock, which
    # agree on a machine in Chile and disagree for the hours between Santiago
    # midnight and UTC midnight. That is why the two-day streak below read as
    # one day only on CI. Freeze both to the same instant: a Wednesday noon in
    # Santiago, so "yesterday" stays inside the same ISO week.
    with use_clock(FrozenClock(datetime(2026, 3, 11, 15, 0, tzinfo=UTC))):
        today = user_date(academic_student)
        yesterday = today - timedelta(days=1)
        with get_db() as db:
            for plan_date, minutes, pages, notes in (
                (f"{today.isoformat()} 09:00:00", 40, 3, "course: Algorithms"),
                (today.isoformat(), 20, 2, "course: Algorithms"),
                (yesterday.isoformat(), 30, 1, "course: Databases"),
                ("not-a-date", 999, 9, "course: Ignored"),
            ):
                _exec(
                    db,
                    "INSERT INTO student_study_progress "
                    "(client_id, plan_date, focus_minutes, pages_read, notes) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (academic_student, plan_date, minutes, pages, notes),
                )
            _exec(
                db,
                "INSERT INTO student_xp (client_id, action, xp, detail) "
                "VALUES (%s, %s, %s, %s)",
                (academic_student, "analytics-contract", 55, "covered"),
            )

        current = client.get("/api/academic/analytics?week_offset=0").get_json()
        previous = client.get(
            "/api/academic/analytics?week_offset=-1"
        ).get_json()
        older = client.get("/api/academic/analytics?week_offset=-3").get_json()

    assert current["totals"]["minutes"] == 1089
    assert current["totals"]["pages"] == 15
    assert current["totals"]["sessions"] == 4
    assert current["totals"]["xp"] >= 55
    assert current["totals"]["streak"] == 2
    assert current["week_label"] == "this week"
    assert current["best_dow"]["minutes"] > 0
    assert current["top_courses"][0] == {
        "course": "Algorithms",
        "minutes": 60,
    }
    assert previous["week_label"] == "last week"
    assert " – " in older["week_label"] or " â€“ " in older["week_label"]


def test_public_academic_profile_hides_focus_from_strangers_and_blocked_users(
    client, academic_student, make_user
):
    stranger_id = make_user("Profile Stranger")
    private = client.get(f"/api/academic/user/{stranger_id}")
    assert private.status_code == 200
    assert private.get_json()["focus_private"] is True
    assert private.get_json()["total_minutes"] is None

    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_friend_blocks "
            "(client_id, blocked_client_id) VALUES (%s, %s)",
            (academic_student, stranger_id),
        )
    assert client.get(
        f"/api/academic/user/{stranger_id}"
    ).status_code == 404
