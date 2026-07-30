from machreach_core.db import get_db, _exec
from student import academic as ac
from student import db as sdb


def _setup_ranked_student(client_id: int, *, country: str = "CL") -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1, country_iso = %s WHERE id = %s",
            (country, client_id),
        )


def test_period_leaderboard_uses_lifetime_xp_for_league(make_user):
    client_id = make_user("Lifetime League Student")
    _setup_ranked_student(client_id)

    old_xp_id = sdb.award_xp(client_id, "old_grind", 1300, "lifetime rank seed")
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_xp SET created_at = %s, occurred_at_utc = %s "
            "WHERE id = %s",
            ("2000-01-01 00:00:00", "2000-01-01T00:00:00+00:00", old_xp_id),
        )
    sdb.award_xp(client_id, "week_grind", 10, "current week")

    rows = ac.leaderboard("country", client_id, period="week")
    me = next(row for row in rows if row["client_id"] == client_id)

    assert me["xp"] == 10
    assert me["lifetime_xp"] == 1310
    assert me["league_key"] == "researcher"
    assert me["league_name"] == "Researcher"


def test_academic_ranks_api_reports_lifetime_league(client, make_user):
    client_id = make_user("Ranks API Lifetime Student")
    _setup_ranked_student(client_id)
    old_xp_id = sdb.award_xp(client_id, "old_grind", 1300, "lifetime rank seed")
    with get_db() as db:
        _exec(
            db,
            "UPDATE student_xp SET created_at = %s, occurred_at_utc = %s "
            "WHERE id = %s",
            ("2000-01-01 00:00:00", "2000-01-01T00:00:00+00:00", old_xp_id),
        )
    sdb.award_xp(client_id, "week_grind", 10, "current week")

    with client.session_transaction() as sess:
        sess["client_id"] = client_id
        sess["client_name"] = "Ranks API Lifetime Student"
        sess["account_type"] = "student"
        sess["session_version"] = 0

    response = client.get("/api/academic/ranks?period=week")

    assert response.status_code == 200
    body = response.get_json()
    assert body["league"]["key"] == "researcher"


def test_zero_xp_students_are_unranked_for_that_period(make_user):
    zero_xp_id = make_user("Zero XP Student")
    earning_id = make_user("Earning Student")
    _setup_ranked_student(zero_xp_id)
    _setup_ranked_student(earning_id)
    sdb.award_xp(earning_id, "week_grind", 25, "current period")

    rows = ac.leaderboard("country", zero_xp_id, period="week")
    ranked_ids = {row["client_id"] for row in rows}

    assert earning_id in ranked_ids
    assert zero_xp_id not in ranked_ids
    assert ac.my_rank("country", zero_xp_id, period="week") is None
    earning_rank = ac.my_rank("country", earning_id, period="week")
    assert earning_rank["rank"] == 1
    assert earning_rank["total"] == len(rows)
    assert earning_rank["xp"] == 25
    assert sdb.get_friend_leaderboard(zero_xp_id) == []
