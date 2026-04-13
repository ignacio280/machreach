"""
Student routes — all /student/* API endpoints and pages for MachReach Student.

This module exposes a `register_student_routes(app, csrf, limiter)` function
that app.py calls to mount everything.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from flask import jsonify, redirect, request, session, url_for

log = logging.getLogger(__name__)


def register_student_routes(app, csrf, limiter):
    """Register all student routes on the Flask app."""

    # Import here to avoid circular imports at module level
    from student.canvas import CanvasClient, extract_text_from_pdf, extract_text_from_docx
    from student.analyzer import analyze_course_material, generate_study_plan
    from student import db as sdb

    # ── helpers ─────────────────────────────────────────────
    def _logged_in() -> bool:
        return "client_id" in session

    def _cid() -> int:
        return session["client_id"]

    def _get_canvas(client_id: int) -> CanvasClient | None:
        tok = sdb.get_canvas_token(client_id)
        if not tok:
            return None
        return CanvasClient(tok["canvas_url"], tok["token"])

    # ── Canvas connection ───────────────────────────────────

    @app.route("/api/student/canvas/connect", methods=["POST"])
    @limiter.limit("10 per minute")
    def student_canvas_connect():
        """Save Canvas URL + API token and test the connection."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)
        canvas_url = (data.get("canvas_url") or "").strip().rstrip("/")
        token = (data.get("token") or "").strip()

        if not canvas_url or not token:
            return jsonify({"error": "canvas_url and token are required"}), 400

        # Test connection
        try:
            client = CanvasClient(canvas_url, token)
            courses = client.get_courses()
        except Exception as e:
            return jsonify({"error": f"Canvas connection failed: {e}"}), 400

        sdb.save_canvas_token(_cid(), canvas_url, token)

        return jsonify({
            "message": "Canvas connected",
            "courses_found": len(courses),
            "courses": [{"id": c["id"], "name": c.get("name", "?")} for c in courses[:20]],
        })

    @app.route("/api/student/canvas/disconnect", methods=["POST"])
    def student_canvas_disconnect():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_canvas_token(_cid())
        return jsonify({"message": "Canvas disconnected"})

    @app.route("/api/student/canvas/status", methods=["GET"])
    def student_canvas_status():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        tok = sdb.get_canvas_token(_cid())
        if not tok:
            return jsonify({"connected": False})
        return jsonify({
            "connected": True,
            "canvas_url": tok["canvas_url"],
        })

    # ── Sync courses ────────────────────────────────────────

    @app.route("/api/student/sync", methods=["POST"])
    @limiter.limit("5 per minute")
    def student_sync_courses():
        """
        Full sync: fetch courses from Canvas, analyze syllabi & files with AI,
        extract exams, and save everything.
        """
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        canvas = _get_canvas(_cid())
        if not canvas:
            return jsonify({"error": "Canvas not connected"}), 400

        try:
            courses = canvas.get_courses()
        except Exception as e:
            return jsonify({"error": f"Failed to fetch courses: {e}"}), 500

        synced = []
        for c in courses:
            cid = c["id"]
            name = c.get("name", "Unknown Course")
            code = c.get("course_code", "")

            # Save/update course in DB
            db_id = sdb.upsert_course(_cid(), cid, name, code)

            # Gather material for AI analysis
            syllabus_html = ""
            file_texts = []
            assignments = []

            try:
                syllabus_html = canvas.get_syllabus(cid)
            except Exception:
                pass

            try:
                syllabus_files = canvas.find_syllabus_files(cid)
                for sf in syllabus_files[:5]:  # Limit to 5 files per course
                    content = canvas.get_file_content(sf)
                    fname = sf.get("display_name", sf.get("filename", ""))
                    text = ""
                    if fname.lower().endswith(".pdf"):
                        text = extract_text_from_pdf(content)
                    elif fname.lower().endswith((".docx", ".doc")):
                        text = extract_text_from_docx(content)
                    if text:
                        file_texts.append({"filename": fname, "text": text})
            except Exception:
                pass

            try:
                assignments = canvas.get_assignments(cid)
            except Exception:
                pass

            # AI analysis
            analysis = analyze_course_material(
                course_name=name,
                syllabus_html=syllabus_html,
                file_texts=file_texts,
                assignments=assignments,
            )

            sdb.update_course_analysis(db_id, analysis)

            # Save extracted exams
            if analysis.get("exams"):
                sdb.save_exams(_cid(), db_id, analysis["exams"])

            synced.append({
                "course": name,
                "exams_found": len(analysis.get("exams", [])),
                "has_schedule": bool(analysis.get("weekly_schedule")),
                "has_grading": bool(analysis.get("grading")),
            })

        return jsonify({
            "message": f"Synced {len(synced)} courses",
            "courses": synced,
        })

    # ── Courses ─────────────────────────────────────────────

    @app.route("/api/student/courses", methods=["GET"])
    def student_get_courses():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        courses = sdb.get_courses(_cid())
        result = []
        for c in courses:
            c = dict(c)
            c["analysis_json"] = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]
            result.append(c)
        return jsonify({"courses": result})

    @app.route("/api/student/courses/<int:course_id>", methods=["GET"])
    def student_get_course(course_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Course not found"}), 404

        course = dict(course)
        course["analysis_json"] = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else course["analysis_json"]
        return jsonify(course)

    # ── Exams ───────────────────────────────────────────────

    @app.route("/api/student/exams", methods=["GET"])
    def student_get_exams():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        upcoming_only = request.args.get("upcoming", "true").lower() == "true"
        if upcoming_only:
            exams = sdb.get_upcoming_exams(_cid())
        else:
            exams = sdb.get_exams(_cid())

        result = []
        for e in exams:
            e = dict(e)
            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            result.append(e)

        return jsonify({"exams": result})

    # ── Study plan ──────────────────────────────────────────

    @app.route("/api/student/plan/generate", methods=["POST"])
    @limiter.limit("3 per minute")
    def student_generate_plan():
        """Generate an AI study plan based on all synced courses."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) if request.is_json else {}
        preferences = data.get("preferences", {})

        # Gather all course analyses
        courses = sdb.get_courses(_cid())
        courses_data = []
        for c in courses:
            analysis = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]
            if analysis and analysis.get("course_name"):
                courses_data.append(analysis)

        if not courses_data:
            return jsonify({"error": "No courses synced. Run /api/student/sync first."}), 400

        plan = generate_study_plan(courses_data, preferences)
        plan_id = sdb.save_study_plan(_cid(), plan, preferences)

        return jsonify({
            "message": "Study plan generated",
            "plan_id": plan_id,
            "plan": plan,
        })

    @app.route("/api/student/plan", methods=["GET"])
    def student_get_plan():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        plan = sdb.get_latest_plan(_cid())
        if not plan:
            return jsonify({"error": "No plan generated yet"}), 404

        return jsonify(plan)

    @app.route("/api/student/plan/today", methods=["GET"])
    def student_get_today():
        """Get today's study sessions from the latest plan."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        plan_row = sdb.get_latest_plan(_cid())
        if not plan_row:
            return jsonify({"error": "No plan generated yet"}), 404

        today = datetime.now().strftime("%Y-%m-%d")
        plan_data = plan_row["plan_json"]
        daily_plan = plan_data.get("daily_plan", [])

        for day in daily_plan:
            if day.get("date") == today:
                return jsonify({"today": day, "upcoming_exams": plan_data.get("upcoming_exams", [])})

        return jsonify({"today": None, "message": "No study sessions scheduled for today"})

    # ── Progress ────────────────────────────────────────────

    @app.route("/api/student/progress/complete", methods=["POST"])
    def student_mark_complete():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) if request.is_json else {}
        plan_date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        notes = data.get("notes", "")

        sdb.mark_day_complete(_cid(), plan_date, notes)
        return jsonify({"message": f"Marked {plan_date} as complete"})

    @app.route("/api/student/stats", methods=["GET"])
    def student_get_stats():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        stats = sdb.get_study_stats(_cid())
        return jsonify(stats)

    # ── Dashboard data (single call for the frontend) ──────

    @app.route("/api/student/dashboard", methods=["GET"])
    def student_dashboard():
        """All-in-one endpoint for the student dashboard."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        cid = _cid()
        canvas_tok = sdb.get_canvas_token(cid)
        courses = sdb.get_courses(cid)
        exams = sdb.get_upcoming_exams(cid)
        stats = sdb.get_study_stats(cid)
        plan_row = sdb.get_latest_plan(cid)

        # Today's plan
        today_plan = None
        if plan_row:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for day in plan_row["plan_json"].get("daily_plan", []):
                if day.get("date") == today_str:
                    today_plan = day
                    break

        # Parse exam topics
        exam_list = []
        for e in exams:
            e = dict(e)
            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            days_until = None
            if e.get("exam_date"):
                try:
                    ed = datetime.strptime(e["exam_date"], "%Y-%m-%d").date()
                    days_until = (ed - datetime.now().date()).days
                except ValueError:
                    pass
            e["days_until"] = days_until
            exam_list.append(e)

        return jsonify({
            "canvas_connected": bool(canvas_tok),
            "courses": [{"id": c["id"], "name": c["name"], "code": c.get("code", "")} for c in courses],
            "upcoming_exams": exam_list,
            "stats": stats,
            "today": today_plan,
            "has_plan": plan_row is not None,
            "recommendations": plan_row["plan_json"].get("recommendations", []) if plan_row else [],
        })

    log.info("Student routes registered.")
