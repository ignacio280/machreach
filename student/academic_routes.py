"""
Academic routes â€” onboarding modal, leaderboards, and profile APIs.

Mounted by calling `register_academic_routes(app, csrf, limiter)` from
app.py, right after register_student_routes().

Endpoints
â”€â”€â”€â”€â”€â”€â”€â”€â”€
  GET   /api/academic/countries
  GET   /api/academic/universities?country=CL&q=<query>
  POST  /api/academic/universities          body: {name, country_iso}
  GET   /api/academic/majors?q=<query>&university_id=<id>
  POST  /api/academic/majors                body: {name, university_id}
  GET   /api/academic/profile
  POST  /api/academic/profile               body: {country_iso, university_id, major_id, canvas_url, canvas_token}
  POST  /api/academic/banner/seen
  GET   /api/academic/leaderboard?scope=global|country|university|major
  GET   /api/academic/ranks                 // compact summary for dashboard
  GET   /student/leaderboards               // 301 redirect to /student/leaderboard
"""

from __future__ import annotations

import logging
from flask import jsonify, request, session

from student import academic as ac
from student import db as sdb


log = logging.getLogger("student.academic_routes")


def register_academic_routes(app, csrf, limiter):

    def _logged_in() -> bool:
        return "client_id" in session

    def _cid() -> int:
        return session["client_id"]

    # â”€â”€ countries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/countries", methods=["GET"])
    def academic_countries():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"countries": ac.list_countries()})

    # â”€â”€ universities â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/universities", methods=["GET"])
    def academic_universities():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        country = (request.args.get("country") or "").upper()[:2]
        q = (request.args.get("q") or "").strip()
        if not country:
            return jsonify({"error": "country required"}), 400
        rows = ac.search_universities(country, q, limit=25)
        return jsonify({"universities": rows})

    @app.route("/api/academic/universities", methods=["POST"])
    @limiter.limit("12 per hour")
    def academic_create_university():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        country_iso = (data.get("country_iso") or "").upper()[:2]
        if not name or len(name) < 3:
            return jsonify({"error": "name too short"}), 400
        if not country_iso:
            return jsonify({"error": "country required"}), 400
        univ_id = ac.create_university(name=name, country_iso=country_iso, created_by=_cid())
        return jsonify({"ok": True, "university": ac.get_university(univ_id)})

    # â”€â”€ majors â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/majors", methods=["GET"])
    def academic_majors():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        q = (request.args.get("q") or "").strip()
        univ_id_raw = request.args.get("university_id")
        univ_id = int(univ_id_raw) if (univ_id_raw or "").isdigit() else None
        rows = ac.search_majors(q, university_id=univ_id, limit=20) if q else []
        return jsonify({"majors": rows})

    @app.route("/api/academic/majors", methods=["POST"])
    @limiter.limit("20 per hour")
    def academic_create_major():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        univ_raw = data.get("university_id")
        univ_id = int(univ_raw) if isinstance(univ_raw, int) or (str(univ_raw).isdigit()) else None
        if not name or len(name) < 2:
            return jsonify({"error": "name too short"}), 400
        major_id = ac.create_major(name=name, university_id=univ_id, created_by=_cid())
        return jsonify({"ok": True, "major": ac.get_major(major_id)})

    # â”€â”€ profile â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/profile", methods=["GET"])
    def academic_profile_get():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        prof = ac.get_academic_profile(cid)
        univ = ac.get_university(int(prof["university_id"])) if prof.get("university_id") else None
        major = ac.get_major(int(prof["major_id"])) if prof.get("major_id") else None
        # Compute the user's total prior XP â€” the banner should only appear for
        # pre-existing accounts that actually have progress to "preserve".
        prior_xp = 0
        try:
            from outreach.db import get_db, _fetchval
            with get_db() as db:
                prior_xp = int(_fetchval(
                    db,
                    "SELECT COALESCE(SUM(xp), 0) FROM student_xp WHERE client_id = %s",
                    (cid,),
                ) or 0)
        except Exception:
            prior_xp = 0
        return jsonify({
            "needs_setup": ac.needs_setup(cid),
            "country_iso": prof.get("country_iso") or "",
            "university": univ,
            "major": major,
            "prior_xp": prior_xp,
            "xp_preserve_banner_seen": bool(prof.get("xp_preserve_banner_seen")),
        })

    @app.route("/api/academic/profile", methods=["POST"])
    @limiter.limit("10 per minute")
    def academic_profile_save():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        cid = _cid()
        country_iso = (data.get("country_iso") or "").upper()[:2]
        try:
            university_id = int(data.get("university_id"))
            major_id = int(data.get("major_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid ids"}), 400
        if not country_iso or not university_id or not major_id:
            return jsonify({"error": "missing fields"}), 400

        ac.save_academic_profile(
            client_id=cid,
            country_iso=country_iso,
            university_id=university_id,
            major_id=major_id,
        )

        # Optionally save Canvas credentials if provided (step 4 of the modal)
        canvas_url = (data.get("canvas_url") or "").strip()
        canvas_token = (data.get("canvas_token") or "").strip()
        canvas_saved = False
        if canvas_url and canvas_token:
            try:
                sdb.save_canvas_token(cid, canvas_url, canvas_token)
                canvas_saved = True
            except Exception as e:
                log.warning("canvas save failed: %s", e)

        return jsonify({"ok": True, "canvas_saved": canvas_saved})

    @app.route("/api/academic/banner/seen", methods=["POST"])
    def academic_banner_seen():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        ac.mark_welcome_banner_seen(_cid())
        return jsonify({"ok": True})

    # â”€â”€ leaderboards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/leaderboard", methods=["GET"])
    def academic_leaderboard():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        scope = (request.args.get("scope") or "global").lower()
        if scope not in {"global", "country", "university", "major"}:
            return jsonify({"error": "bad scope"}), 400
        rows = ac.leaderboard(scope, _cid(), limit=100)
        return jsonify({"scope": scope, "rows": rows})

    @app.route("/api/academic/ranks", methods=["GET"])
    def academic_ranks():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        summary = ac.ranks_summary(cid)
        # include league for me
        xp = summary.get("global", {}).get("xp", 0) if summary.get("global") else 0
        return jsonify({"ranks": summary, "league": ac.league_for_xp(int(xp))})

    # â”€â”€ analytics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @app.route("/api/academic/analytics", methods=["GET"])
    def academic_analytics():
        """Return a comprehensive study-analytics payload for the dashboard widget."""
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        from outreach.db import get_db, _fetchall, _fetchval
        from datetime import datetime, timedelta
        from collections import defaultdict

        try:
            with get_db() as db:
                # All study sessions for this student
                rows = _fetchall(
                    db,
                    "SELECT plan_date, focus_minutes, pages_read, notes "
                    "FROM student_study_progress WHERE client_id = %s "
                    "AND COALESCE(focus_minutes, 0) > 0 "
                    "ORDER BY plan_date DESC LIMIT 1000",
                    (cid,),
                )
                total_xp = int(_fetchval(
                    db,
                    "SELECT COALESCE(SUM(xp), 0) FROM student_xp WHERE client_id = %s",
                    (cid,),
                ) or 0)
        except Exception:
            rows = []
            total_xp = 0

        # Aggregate
        total_minutes = sum(int(r.get("focus_minutes") or 0) for r in rows)
        total_pages = sum(int(r.get("pages_read") or 0) for r in rows)
        total_sessions = len(rows)

        # Per-day for last 14 days
        today = datetime.now().date()
        per_day_min = defaultdict(int)
        per_day_sessions = defaultdict(int)
        per_hour = defaultdict(int)
        per_course = defaultdict(int)
        per_dow = defaultdict(int)

        for r in rows:
            pd = (r.get("plan_date") or "")[:19]
            try:
                dt = datetime.strptime(pd[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(pd[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            day_key = dt.strftime("%Y-%m-%d")
            mins = int(r.get("focus_minutes") or 0)
            per_day_min[day_key] += mins
            per_day_sessions[day_key] += 1
            per_hour[dt.hour] += mins
            per_dow[dt.strftime("%a")] += mins
            # Notes look like "pomodoro: Math 101" â€” parse the course name
            notes = r.get("notes") or ""
            if ":" in notes:
                course = notes.split(":", 1)[1].strip()
                if course:
                    per_course[course] += mins

        last_14 = []
        for i in range(13, -1, -1):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            last_14.append({
                "date": d,
                "label": (today - timedelta(days=i)).strftime("%a"),
                "minutes": per_day_min.get(d, 0),
                "sessions": per_day_sessions.get(d, 0),
            })

        # Hours of day, sorted 0-23
        hours_dist = [{"hour": h, "minutes": per_hour.get(h, 0)} for h in range(24)]

        # Day-of-week order
        dow_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        dow_dist = [{"day": d, "minutes": per_dow.get(d, 0)} for d in dow_order]

        # Top courses
        top_courses = sorted(
            ({"course": k, "minutes": v} for k, v in per_course.items()),
            key=lambda x: x["minutes"], reverse=True,
        )[:8]

        # Streak: consecutive days back from today with > 0 minutes
        streak = 0
        cursor = today
        while per_day_min.get(cursor.strftime("%Y-%m-%d"), 0) > 0:
            streak += 1
            cursor -= timedelta(days=1)

        # Best hour / best day
        best_hour = max(hours_dist, key=lambda x: x["minutes"]) if any(h["minutes"] for h in hours_dist) else None
        best_dow = max(dow_dist, key=lambda x: x["minutes"]) if any(d["minutes"] for d in dow_dist) else None

        return jsonify({
            "totals": {
                "minutes": total_minutes,
                "hours": round(total_minutes / 60, 1),
                "sessions": total_sessions,
                "pages": total_pages,
                "xp": total_xp,
                "streak": streak,
            },
            "last_14_days": last_14,
            "hours_dist": hours_dist,
            "dow_dist": dow_dist,
            "top_courses": top_courses,
            "best_hour": best_hour,
            "best_dow": best_dow,
        })

    @app.route("/student/leaderboards", methods=["GET"])
    def student_leaderboards_page():
        # Consolidated: all leaderboards live on the singular /student/leaderboard tab.
        from flask import redirect
        return redirect("/student/leaderboard", code=301)


def _redirect_to_login():
    from flask import redirect, url_for
    try:
        return redirect(url_for("login"))
    except Exception:
        return redirect("/login")


