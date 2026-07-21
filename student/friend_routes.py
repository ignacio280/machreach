"""HTTP adapter for student friend discovery and safety actions.

The underlying policies and persistence remain in ``student.db``. Keeping this
adapter separate lets the legacy student route module shrink one feature at a
time without changing public endpoints.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import jsonify, request


def register_friend_api_routes(
    app: Any,
    limiter: Any,
    *,
    logged_in: Callable[[], bool],
    current_client_id: Callable[[], int],
    db: Any,
) -> None:
    @app.route("/api/student/friends/search")
    @limiter.limit("30 per hour")
    def student_friends_search_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        query = (request.args.get("q") or "").strip()
        return jsonify(
            results=db.search_users(
                query,
                exclude_client_id=current_client_id(),
                limit=20,
            )
        )

    @app.route("/api/student/friends/list")
    def student_friends_list_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        return jsonify(**db.list_friends(current_client_id()))

    @app.route("/api/student/presence/heartbeat", methods=["POST"])
    def student_presence_heartbeat_api():
        if not logged_in():
            return jsonify(ok=False), 401
        db.touch_presence(current_client_id())
        return jsonify(ok=True)

    @app.route("/api/student/friends/add", methods=["POST"])
    @limiter.limit("10 per day")
    def student_friends_add_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        try:
            friend_id = int((request.get_json(silent=True) or {}).get("friend_id"))
        except (TypeError, ValueError):
            return jsonify(error="Invalid friend_id"), 400
        return jsonify(status=db.add_friend(current_client_id(), friend_id))

    @app.route("/api/student/friends/block", methods=["POST"])
    @limiter.limit("20 per day")
    def student_friends_block_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        try:
            friend_id = int((request.get_json(silent=True) or {}).get("friend_id"))
        except (TypeError, ValueError):
            return jsonify(error="Invalid friend_id"), 400
        return jsonify(status=db.block_student(current_client_id(), friend_id))

    @app.route("/api/student/friends/unblock", methods=["POST"])
    @limiter.limit("20 per day")
    def student_friends_unblock_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        try:
            friend_id = int((request.get_json(silent=True) or {}).get("friend_id"))
        except (TypeError, ValueError):
            return jsonify(error="Invalid friend_id"), 400
        return jsonify(ok=db.unblock_student(current_client_id(), friend_id))

    @app.route("/api/student/friends/report", methods=["POST"])
    @limiter.limit("10 per day")
    def student_friends_report_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        data = request.get_json(silent=True) or {}
        try:
            friend_id = int(data.get("friend_id"))
        except (TypeError, ValueError):
            return jsonify(error="Invalid friend_id"), 400
        if not db.report_student(current_client_id(), friend_id, data.get("reason", "")):
            return jsonify(error="A reason is required"), 400
        return jsonify(ok=True)

    @app.route("/api/student/friends/discovery", methods=["GET", "POST"])
    @limiter.limit("20 per hour", methods=["POST"])
    def student_friends_discovery_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        client_id = current_client_id()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            db.set_friend_discovery(client_id, bool(data.get("enabled")))
        return jsonify(enabled=db.get_friend_discovery(client_id))

    @app.route("/api/student/friends/remove", methods=["POST"])
    def student_friends_remove_api():
        if not logged_in():
            return jsonify(error="Login required"), 401
        try:
            friend_id = int((request.get_json(silent=True) or {}).get("friend_id"))
        except (TypeError, ValueError):
            return jsonify(error="Invalid friend_id"), 400
        db.remove_friend(current_client_id(), friend_id)
        return jsonify(ok=True)
