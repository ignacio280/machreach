from __future__ import annotations

from machreach_core.db import _exec, get_db
from student import db as sdb


def _login(client, client_id: int, name: str) -> None:
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET academic_setup_complete = 1, email_verified = 1 WHERE id = %s",
            (client_id,),
        )
    with client.session_transaction() as session:
        session.update({
            "client_id": client_id,
            "client_name": name,
            "account_type": "student",
            "session_version": 0,
        })


def test_friend_search_never_displays_email_and_respects_name_discovery(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    viewer = make_user("Search Viewer")
    target_email = "private-target@example.test"
    target = make_user("Private Target", target_email)
    _login(client, viewer, "Search Viewer")

    visible = client.get("/api/student/friends/search?q=Private%20Target").get_json()["results"]
    assert any(row["id"] == target for row in visible)
    assert all("email" not in row for row in visible)

    sdb.set_friend_discovery(target, False)
    hidden = client.get("/api/student/friends/search?q=Private%20Target").get_json()["results"]
    exact = client.get(f"/api/student/friends/search?q={target_email}").get_json()["results"]
    numeric = client.get(f"/api/student/friends/search?q={target}").get_json()["results"]

    assert all(row["id"] != target for row in hidden)
    assert all(row["id"] != target for row in exact)
    assert all(row["id"] != target for row in numeric)
    assert all("email" not in row for row in exact)


def test_focus_totals_are_private_until_friendship_is_accepted(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    viewer = make_user("Profile Viewer")
    target = make_user("Focus Target")
    sdb.save_focus_session(target, "pomodoro", 30, 0)
    _login(client, viewer, "Profile Viewer")

    private = client.get(f"/api/academic/user/{target}").get_json()
    assert private["focus_private"] is True
    assert private["total_minutes"] is None
    assert private["sessions"] is None
    assert {"bio", "country", "joined_at", "banner", "email"}.isdisjoint(private)

    assert client.post("/api/student/friends/add", json={"friend_id": target}).get_json()["status"] == "requested"
    _login(client, target, "Focus Target")
    assert client.post("/api/student/friends/add", json={"friend_id": viewer}).get_json()["status"] == "accepted"
    _login(client, viewer, "Profile Viewer")

    shared = client.get(f"/api/academic/user/{target}").get_json()
    assert shared["focus_private"] is False
    assert shared["total_minutes"] == 30


def test_blocking_removes_friendship_and_hides_profiles(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    first = make_user("Block First")
    second = make_user("Block Second")
    sdb.add_friend(first, second)
    sdb.add_friend(second, first)
    _login(client, first, "Block First")

    blocked = client.post("/api/student/friends/block", json={"friend_id": second})

    assert blocked.status_code == 200
    assert blocked.get_json()["status"] == "blocked"
    assert sdb.are_friends(first, second) is False
    assert client.get(f"/api/academic/user/{second}").status_code == 404

    unblocked = client.post("/api/student/friends/unblock", json={"friend_id": second})
    assert unblocked.status_code == 200
    assert unblocked.get_json()["ok"] is True
    assert client.get(f"/api/academic/user/{second}").status_code == 200


def test_students_cannot_self_remove_from_general_rankings(client, flask_app, make_user, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Always Ranked")
    _login(client, client_id, "Always Ranked")

    assert client.post("/api/student/retire").status_code == 404
    assert client.post("/api/student/unretire").status_code == 404
    with get_db() as db:
        _exec(db, "UPDATE clients SET retired = 0 WHERE id = %s", (client_id,))


def test_friend_api_rejects_unauthenticated_and_malformed_requests(
    client, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    assert client.get("/api/student/friends/search?q=x").status_code == 401
    assert client.get("/api/student/friends/list").status_code == 401
    assert client.post("/api/student/presence/heartbeat").status_code == 401
    for path in ("add", "block", "unblock", "report", "remove"):
        assert client.post(
            f"/api/student/friends/{path}", json={"friend_id": "bad"}
        ).status_code == 401
    assert client.get("/api/student/friends/discovery").status_code == 401


def test_friend_presence_discovery_report_and_remove_routes(
    client, flask_app, make_user, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    first = make_user("Friend Controls")
    second = make_user("Report Target")
    _login(client, first, "Friend Controls")

    assert client.get("/api/student/friends/list").status_code == 200
    assert client.post("/api/student/presence/heartbeat").get_json() == {"ok": True}
    assert client.post(
        "/api/student/friends/add", json={"friend_id": "bad"}
    ).status_code == 400
    assert client.post(
        "/api/student/friends/block", json={"friend_id": "bad"}
    ).status_code == 400
    assert client.post(
        "/api/student/friends/unblock", json={"friend_id": "bad"}
    ).status_code == 400
    assert client.post(
        "/api/student/friends/remove", json={"friend_id": "bad"}
    ).status_code == 400
    assert client.post(
        "/api/student/friends/report",
        json={"friend_id": second, "reason": ""},
    ).status_code == 400
    assert client.post(
        "/api/student/friends/report",
        json={"friend_id": "bad", "reason": "spam"},
    ).status_code == 400
    assert client.post(
        "/api/student/friends/report",
        json={"friend_id": 99999999, "reason": "spam"},
    ).status_code == 400
    assert client.post(
        "/api/student/friends/report",
        json={"friend_id": second, "reason": "spam"},
    ).get_json() == {"ok": True}

    assert client.post(
        "/api/student/friends/discovery", json={"enabled": False}
    ).get_json() == {"enabled": False}
    assert client.get("/api/student/friends/discovery").get_json() == {
        "enabled": False
    }
    sdb.add_friend(first, second)
    assert client.post(
        "/api/student/friends/remove", json={"friend_id": second}
    ).get_json() == {"ok": True}
