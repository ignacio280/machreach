"""
Student routes — all /student/* API endpoints and pages for MachReach Student.

This module exposes a `register_student_routes(app, csrf, limiter)` function
that app.py calls to mount everything.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from flask import jsonify, redirect, request, session, url_for, render_template_string
from markupsafe import Markup

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

    # ── Sync courses (background) ──────────────────────────

    import threading

    # In-memory sync status per client  {client_id: {status, progress, ...}}
    _sync_status: dict[int, dict] = {}

    @app.route("/api/student/sync", methods=["POST"])
    @limiter.limit("3 per minute")
    def student_sync_courses():
        """Kick off a background sync. Returns immediately."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        client_id = _cid()
        tok = sdb.get_canvas_token(client_id)
        if not tok:
            return jsonify({"error": "Canvas not connected"}), 400

        # Don't start if already running
        existing = _sync_status.get(client_id, {})
        if existing.get("status") == "running":
            return jsonify({"message": "Sync already in progress", "sync": existing})

        _sync_status[client_id] = {
            "status": "running",
            "progress": "Starting...",
            "courses_total": 0,
            "courses_done": 0,
            "files_downloaded": 0,
            "started_at": datetime.now().isoformat(),
        }

        def _do_sync():
            try:
                canvas = CanvasClient(tok["canvas_url"], tok["token"])
                courses = canvas.get_courses()
                _sync_status[client_id]["courses_total"] = len(courses)
                _sync_status[client_id]["progress"] = f"Found {len(courses)} courses"

                synced = []
                total_files = 0
                no_syllabus_courses = []

                for idx, c in enumerate(courses):
                    cid_canvas = c["id"]
                    name = c.get("name", "Unknown Course")
                    code = c.get("course_code", "")
                    _sync_status[client_id]["progress"] = f"Syncing {name} ({idx + 1}/{len(courses)})"

                    db_id = sdb.upsert_course(client_id, cid_canvas, name, code)

                    syllabus_html = ""
                    file_texts = []
                    assignments = []
                    found_syllabus = False

                    try:
                        syllabus_html = canvas.get_syllabus(cid_canvas)
                        if syllabus_html and len(syllabus_html.strip()) > 50:
                            found_syllabus = True
                    except Exception:
                        pass

                    # Download ONLY syllabus-related files (syllabus, programa, guía docente, etc.)
                    try:
                        syllabus_files = canvas.find_syllabus_files(cid_canvas)
                        for sf in syllabus_files:
                            fname = sf.get("display_name", sf.get("filename", ""))
                            fl = fname.lower()
                            if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc")):
                                continue
                            if sf.get("size", 0) > 15 * 1024 * 1024:
                                continue
                            try:
                                _sync_status[client_id]["progress"] = f"{name}: downloading {fname}"
                                content = canvas.get_file_content(sf)
                                text = ""
                                if fl.endswith(".pdf"):
                                    text = extract_text_from_pdf(content)
                                elif fl.endswith((".docx", ".doc")):
                                    text = extract_text_from_docx(content)
                                if text and len(text.strip()) > 50:
                                    file_texts.append({"filename": fname, "text": text[:8000]})
                                    total_files += 1
                                    found_syllabus = True
                                    _sync_status[client_id]["files_downloaded"] = total_files
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Include manually-uploaded files
                    try:
                        uploaded = sdb.get_course_files(client_id, db_id)
                        for uf in uploaded:
                            if uf.get("extracted_text") and len(uf["extracted_text"].strip()) > 50:
                                file_texts.append({
                                    "filename": uf["original_name"],
                                    "text": uf["extracted_text"][:8000],
                                })
                                found_syllabus = True
                    except Exception:
                        pass

                    try:
                        assignments = canvas.get_assignments(cid_canvas)
                    except Exception:
                        pass

                    if not found_syllabus:
                        no_syllabus_courses.append(name)

                    # AI analysis
                    _sync_status[client_id]["progress"] = f"{name}: AI analyzing {len(file_texts)} files..."
                    analysis = analyze_course_material(
                        course_name=name,
                        syllabus_html=syllabus_html,
                        file_texts=file_texts,
                        assignments=assignments,
                    )

                    sdb.update_course_analysis(db_id, analysis)
                    if analysis.get("exams"):
                        sdb.save_exams(client_id, db_id, analysis["exams"])

                    _sync_status[client_id]["courses_done"] = idx + 1
                    synced.append(name)

                # Build final status with syllabus warnings
                warnings = []
                if no_syllabus_courses:
                    for cn in no_syllabus_courses:
                        warnings.append(
                            f"Could not find a syllabus/programa for \"{cn}\". "
                            f"Please upload it manually on the course page so the AI can create the best study plan."
                        )

                _sync_status[client_id] = {
                    "status": "done",
                    "progress": f"Synced {len(synced)} courses, {total_files} syllabus files downloaded",
                    "courses_total": len(courses),
                    "courses_done": len(courses),
                    "files_downloaded": total_files,
                    "courses": synced,
                    "warnings": warnings,
                    "no_syllabus": no_syllabus_courses,
                }
            except Exception as e:
                log.error("Background sync failed for client %s: %s", client_id, e)
                _sync_status[client_id] = {
                    "status": "error",
                    "progress": f"Sync failed: {e}",
                    "courses_total": 0,
                    "courses_done": 0,
                    "files_downloaded": 0,
                }

        thread = threading.Thread(target=_do_sync, daemon=True)
        thread.start()

        return jsonify({"message": "Sync started", "sync": _sync_status[client_id]})

    @app.route("/api/student/sync/status", methods=["GET"])
    def student_sync_status():
        """Poll sync progress."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        status = _sync_status.get(_cid(), {"status": "idle", "progress": "No sync running"})
        return jsonify(status)

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

    # ── File uploads ────────────────────────────────────────

    @app.route("/api/student/courses/<int:course_id>/files", methods=["GET"])
    def student_get_course_files(course_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        files = sdb.get_course_files(_cid(), course_id)
        return jsonify({"files": [dict(f) for f in files]})

    @app.route("/api/student/courses/<int:course_id>/upload", methods=["POST"])
    @limiter.limit("30 per minute")
    def student_upload_file(course_id):
        """Upload a PDF or DOCX file for a course. Text is extracted and stored."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        fname = f.filename
        fl = fname.lower()
        if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc")):
            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

        # Read file content (limit 15MB)
        content = f.read(15 * 1024 * 1024 + 1)
        if len(content) > 15 * 1024 * 1024:
            return jsonify({"error": "File too large (max 15MB)"}), 400

        # Extract text
        text = ""
        try:
            if fl.endswith(".pdf"):
                text = extract_text_from_pdf(content)
            elif fl.endswith((".docx", ".doc")):
                text = extract_text_from_docx(content)
        except Exception as e:
            return jsonify({"error": f"Could not extract text: {e}"}), 400

        if not text or len(text.strip()) < 20:
            return jsonify({"error": "Could not extract readable text from this file"}), 400

        file_type = "pdf" if fl.endswith(".pdf") else "docx"
        exam_id = request.form.get("exam_id")
        exam_id = int(exam_id) if exam_id else None
        file_id = sdb.save_course_file(_cid(), course_id, fname, file_type, text, exam_id=exam_id)

        return jsonify({"id": file_id, "filename": fname, "text_length": len(text)})

    @app.route("/api/student/files/<int:file_id>", methods=["DELETE"])
    def student_delete_file(file_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_course_file(file_id, _cid())
        return jsonify({"ok": True})

    @app.route("/api/student/courses/<int:course_id>", methods=["DELETE"])
    def student_delete_course(course_id):
        """Remove a course and all its related data."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        sdb.delete_course(course_id, _cid())
        return jsonify({"ok": True})

    @app.route("/api/student/courses/<int:course_id>", methods=["PUT"])
    def student_update_course(course_id):
        """Update course info (name, code, grading, schedule, tips)."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True)
        sdb.update_course_info(
            course_id, _cid(),
            name=data.get("name", course["name"]),
            code=data.get("code", course.get("code", "")),
            grading=data.get("grading", {}),
            weekly_schedule=data.get("weekly_schedule", []),
            study_tips=data.get("study_tips", []),
        )
        return jsonify({"ok": True})

    # ── Exam CRUD ───────────────────────────────────────────

    @app.route("/api/student/courses/<int:course_id>/exams", methods=["GET"])
    def student_get_course_exams(course_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        exams = sdb.get_course_exams(course_id)
        result = []
        for e in exams:
            e = dict(e)
            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            result.append(e)
        return jsonify({"exams": result})

    @app.route("/api/student/courses/<int:course_id>/exams", methods=["POST"])
    def student_add_exam(course_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Exam name is required"}), 400
        exam_id = sdb.upsert_exam(
            _cid(), course_id, None,
            name=name,
            exam_date=data.get("exam_date") or None,
            weight_pct=int(data.get("weight_pct", 0)),
            topics=data.get("topics", []),
        )
        return jsonify({"ok": True, "id": exam_id})

    @app.route("/api/student/exams/<int:exam_id>", methods=["PUT"])
    def student_update_exam(exam_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Exam name is required"}), 400
        sdb.upsert_exam(
            _cid(), data.get("course_id", 0), exam_id,
            name=name,
            exam_date=data.get("exam_date") or None,
            weight_pct=int(data.get("weight_pct", 0)),
            topics=data.get("topics", []),
        )
        return jsonify({"ok": True})

    @app.route("/api/student/exams/<int:exam_id>", methods=["DELETE"])
    def student_delete_exam(exam_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_exam(exam_id, _cid())
        return jsonify({"ok": True})

    # ── Exams (global list) ─────────────────────────────────

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

    # ── Study plan (background) ──────────────────────────────

    _plan_status: dict[int, dict] = {}

    @app.route("/api/student/plan/generate", methods=["POST"])
    @limiter.limit("3 per minute")
    def student_generate_plan():
        """Kick off background study plan generation. Returns immediately."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401

        client_id = _cid()
        data = request.get_json(force=True) if request.is_json else {}
        preferences = data.get("preferences", {})

        # Gather all course analyses
        courses = sdb.get_courses(client_id)
        courses_data = []
        course_difficulties = {}
        for c in courses:
            analysis = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]
            if analysis and analysis.get("course_name"):
                courses_data.append(analysis)
                diff = c.get("difficulty", 3) or 3
                course_difficulties[analysis["course_name"]] = diff

        if not courses_data:
            return jsonify({"error": "No courses synced. Run /api/student/sync first."}), 400

        # Gather schedule settings
        schedule_settings = sdb.get_schedule_settings(client_id)
        schedule_list = []
        if schedule_settings:
            for s in schedule_settings:
                schedule_list.append({
                    "day": s["day_of_week"],
                    "hours": s["available_hours"],
                    "free": bool(s["is_free_day"]),
                })

        # Gather incomplete assignments from previous days
        today_str = datetime.now().strftime("%Y-%m-%d")
        incomplete = sdb.get_incomplete_assignments(client_id, today_str)

        existing = _plan_status.get(client_id, {})
        if existing.get("status") == "running":
            return jsonify({"message": "Plan generation already in progress", "plan_status": existing})

        _plan_status[client_id] = {
            "status": "running",
            "progress": "Generating your study plan with AI...",
        }

        def _do_plan():
            try:
                plan = generate_study_plan(
                    courses_data, preferences,
                    schedule_settings=schedule_list or None,
                    course_difficulties=course_difficulties or None,
                    incomplete_assignments=incomplete or None,
                )
                if not plan.get("daily_plan"):
                    raise ValueError("AI returned an empty plan")
                plan_id = sdb.save_study_plan(client_id, plan, preferences)
                _plan_status[client_id] = {
                    "status": "done",
                    "progress": "Study plan generated!",
                    "plan_id": plan_id,
                }
            except Exception as e:
                log.error("Plan generation failed for client %s: %s", client_id, e)
                _plan_status[client_id] = {
                    "status": "error",
                    "progress": f"Plan generation failed: {e}",
                }

        thread = threading.Thread(target=_do_plan, daemon=True)
        thread.start()

        return jsonify({"message": "Plan generation started", "plan_status": _plan_status[client_id]})

    @app.route("/api/student/plan/status", methods=["GET"])
    def student_plan_status():
        """Poll plan generation progress."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        status = _plan_status.get(_cid(), {"status": "idle"})
        return jsonify(status)

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

    # ── Focus / Pomodoro ────────────────────────────────────

    @app.route("/api/student/focus/save", methods=["POST"])
    def student_save_focus():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        sdb.save_focus_session(
            _cid(),
            mode=data.get("mode", "pomodoro"),
            minutes=int(data.get("minutes", 0)),
            pages=int(data.get("pages", 0)),
            course_name=data.get("course_name", ""),
        )
        return jsonify({"ok": True})

    @app.route("/api/student/focus/stats", methods=["GET"])
    def student_focus_stats():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify(sdb.get_focus_stats(_cid()))

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

    # ── Frontend pages ──────────────────────────────────────

    def _s_render(title, content_html, active_page="student_dashboard"):
        """Render a student page using MachReach's LAYOUT."""
        from app import LAYOUT
        from outreach.db import get_client
        from outreach.i18n import t_dict
        flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])
        nav = t_dict("nav")
        is_admin = False
        if _logged_in():
            c = get_client(session["client_id"])
            is_admin = bool(c and c.get("is_admin"))
        return render_template_string(
            LAYOUT,
            title=f"Student — {title}",
            content=Markup(content_html),
            logged_in=_logged_in(),
            messages=flashed,
            active_page=active_page,
            client_name=session.get("client_name", ""),
            wide=False,
            nav=nav,
            lang=session.get("lang", "en"),
            is_admin=is_admin,
            account_type="student",
        )

    @app.route("/student")
    def student_dashboard_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        canvas_tok = sdb.get_canvas_token(cid)
        courses = sdb.get_courses(cid)
        exams = sdb.get_upcoming_exams(cid)
        stats = sdb.get_study_stats(cid)
        focus_stats = sdb.get_focus_stats(cid)
        plan_row = sdb.get_latest_plan(cid)

        # Today's plan
        today_plan = None
        today_sessions_html = ""
        today_assignment_progress = {}
        if plan_row:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for day in plan_row["plan_json"].get("daily_plan", []):
                if day.get("date") == today_str:
                    today_plan = day
                    break
            if today_plan:
                ap = sdb.get_assignment_progress(cid, today_str)
                today_assignment_progress = {r["session_index"]: bool(r["completed"]) for r in ap}

        if today_plan:
            sessions = today_plan.get("sessions", [])
            for idx, s in enumerate(sessions):
                prio_colors = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}
                pc = prio_colors.get(s.get("priority", "medium"), "#94A3B8")
                checked = "checked" if today_assignment_progress.get(idx, False) else ""
                strike = "text-decoration:line-through;opacity:0.6;" if today_assignment_progress.get(idx, False) else ""
                today_sessions_html += f"""
                <div style="background:var(--card);border:1px solid var(--border);border-left:4px solid {pc};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;{strike}" id="dash-session-{idx}">
                  <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <input type="checkbox" {checked} onchange="toggleDashAssignment({idx},this.checked)"
                        style="width:18px;height:18px;cursor:pointer;accent-color:var(--primary);">
                      <span style="font-weight:700;color:var(--text);">{_esc(s.get('course',''))}</span>
                      <span style="color:var(--text-muted);font-size:13px;">{s.get('hours',0)}h &middot; {s.get('type','study')}</span>
                    </div>
                    <span style="background:{pc}18;color:{pc};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">{s.get('priority','').upper()}</span>
                  </div>
                  <div style="color:var(--text-secondary);font-size:14px;margin-top:6px;margin-left:26px;">{_esc(s.get('topic',''))}</div>
                  {"<div style='color:var(--text-muted);font-size:12px;margin-top:4px;margin-left:26px;font-style:italic;'>" + _esc(s.get('reason','')) + "</div>" if s.get('reason') else ""}
                </div>"""
        else:
            today_sessions_html = """
            <div style="text-align:center;padding:32px;color:var(--text-muted);">
              <div style="font-size:40px;margin-bottom:12px;">&#128218;</div>
              <p>No study sessions for today.</p>
              <p style="font-size:13px;">Sync your courses and generate a plan to get started.</p>
            </div>"""

        # Upcoming exams HTML
        exams_html = ""
        for e in exams[:5]:
            days_until = None
            if e.get("exam_date"):
                try:
                    ed = datetime.strptime(e["exam_date"], "%Y-%m-%d").date()
                    days_until = (ed - datetime.now().date()).days
                except ValueError:
                    pass
            urgency = "#EF4444" if (days_until is not None and days_until <= 7) else "#F59E0B" if (days_until is not None and days_until <= 14) else "#10B981"
            topics = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            topics_str = ", ".join(topics[:3]) + ("..." if len(topics) > 3 else "") if topics else "No topics listed"
            exams_html += f"""
            <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <span style="font-weight:700;">{_esc(e.get('course_name',''))}</span>
                  <span style="color:var(--text-muted);font-size:13px;margin-left:6px;">&middot; {_esc(e.get('name','Exam'))}</span>
                </div>
                <span style="background:{urgency}18;color:{urgency};padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;">
                  {str(days_until) + 'd' if days_until is not None else '?'} left
                </span>
              </div>
              <div style="color:var(--text-muted);font-size:13px;margin-top:4px;">
                {e.get('exam_date', 'TBD')} &middot; {e.get('weight_pct', 0)}% of grade &middot; {_esc(topics_str)}
              </div>
            </div>"""

        if not exams_html:
            exams_html = "<p style='color:var(--text-muted);text-align:center;padding:20px;'>No upcoming exams. Sync your courses to detect them.</p>"

        # Recommendations
        recs_html = ""
        if plan_row:
            for r in plan_row["plan_json"].get("recommendations", [])[:5]:
                recs_html += f"<li style='margin-bottom:6px;color:var(--text-secondary);font-size:14px;'>{_esc(r)}</li>"

        canvas_status = "Connected" if canvas_tok else "Not connected"
        canvas_color = "#10B981" if canvas_tok else "#EF4444"

        return _s_render("Dashboard", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;font-size:28px;">&#127891; MachReach Student</h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">AI-powered study planner &middot; Canvas integration</p>
          </div>
          <div style="display:flex;gap:10px;">
            <button onclick="syncCourses()" class="btn btn-primary btn-sm" id="sync-btn">&#128260; Sync Canvas</button>
            <button onclick="generatePlan()" class="btn btn-outline btn-sm" id="plan-btn">&#129302; Generate Plan</button>
          </div>
        </div>

        <!-- Motivational quote -->
        <div id="daily-quote" style="background:linear-gradient(135deg,var(--primary),#8B5CF6);color:#fff;border-radius:var(--radius-sm);padding:16px 24px;margin-bottom:20px;position:relative;overflow:hidden;">
          <div style="position:absolute;right:16px;top:50%;transform:translateY(-50%);font-size:48px;opacity:0.15;">&#128161;</div>
          <div style="font-style:italic;font-size:15px;max-width:85%;" id="quote-text"></div>
          <div style="font-size:12px;margin-top:4px;opacity:0.8;" id="quote-author"></div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px;margin-bottom:24px;">
          <div class="stat-card stat-purple"><div class="num">{len(courses)}</div><div class="label">Courses</div></div>
          <div class="stat-card stat-red"><div class="num">{stats['upcoming_exams']}</div><div class="label">Upcoming Exams</div></div>
          <div class="stat-card stat-green"><div class="num">{stats['completion_pct']}%</div><div class="label">Plan Progress</div></div>
          <div class="stat-card stat-blue"><div class="num">{focus_stats['total_hours']}</div><div class="label">Hours Focused</div></div>
          <div class="stat-card" style="background:var(--card);border:1px solid var(--border);"><div class="num" style="color:#F59E0B;">{focus_stats['streak_days']}&#128293;</div><div class="label">Day Streak</div></div>
          <div class="stat-card" style="background:var(--card);border:1px solid var(--border);"><div class="num" style="font-size:14px;color:{canvas_color};">{canvas_status}</div><div class="label">Canvas</div></div>
        </div>

        <!-- Quick actions -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px;">
          <a href="/student/focus" style="text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;text-align:center;transition:border-color 0.2s;">
            <div style="font-size:28px;margin-bottom:6px;">&#127917;</div>
            <div style="font-weight:600;font-size:13px;color:var(--text);">Focus Mode</div>
            <div style="font-size:11px;color:var(--text-muted);">Pomodoro &amp; Pages</div>
          </a>
          <a href="/student/gpa" style="text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;text-align:center;transition:border-color 0.2s;">
            <div style="font-size:28px;margin-bottom:6px;">&#127891;</div>
            <div style="font-weight:600;font-size:13px;color:var(--text);">GPA Calculator</div>
            <div style="font-size:11px;color:var(--text-muted);">Track your grades</div>
          </a>
          <a href="/student/courses" style="text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;text-align:center;transition:border-color 0.2s;">
            <div style="font-size:28px;margin-bottom:6px;">&#128218;</div>
            <div style="font-weight:600;font-size:13px;color:var(--text);">My Courses</div>
            <div style="font-size:11px;color:var(--text-muted);">{len(courses)} synced</div>
          </a>
          <a href="/student/plan" style="text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;text-align:center;transition:border-color 0.2s;">
            <div style="font-size:28px;margin-bottom:6px;">&#128197;</div>
            <div style="font-weight:600;font-size:13px;color:var(--text);">Study Plan</div>
            <div style="font-size:11px;color:var(--text-muted);">AI-generated</div>
          </a>
          <a href="/student/schedule" style="text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;text-align:center;transition:border-color 0.2s;">
            <div style="font-size:28px;margin-bottom:6px;">&#128337;</div>
            <div style="font-weight:600;font-size:13px;color:var(--text);">Schedule</div>
            <div style="font-size:11px;color:var(--text-muted);">Times & Difficulty</div>
          </a>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
          <div class="card">
            <div class="card-header"><h2>&#128197; Today's Study Plan</h2></div>
            {today_sessions_html}
            {"<div style='text-align:center;margin-top:12px;'><button onclick='markComplete()' class='btn btn-primary btn-sm'>&#10003; Mark Today Complete</button></div>" if today_plan else ""}
          </div>

          <div class="card">
            <div class="card-header"><h2>&#128221; Upcoming Exams</h2></div>
            {exams_html}
          </div>
        </div>

        {"<div class='card' style='margin-top:20px;'><div class='card-header'><h2>&#128161; AI Recommendations</h2></div><ul style='padding-left:20px;'>" + recs_html + "</ul></div>" if recs_html else ""}

        <div id="sync-progress" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;margin-top:16px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span id="sync-spinner" style="animation:spin 1s linear infinite;display:inline-block;">&#9203;</span>
            <span id="sync-msg" style="color:var(--text-secondary);font-size:14px;">Starting sync...</span>
          </div>
          <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Courses: <span id="sync-courses">0/0</span> &middot; Files: <span id="sync-files">0</span></div>
          <div style="margin-top:10px;padding:10px 14px;background:var(--bg);border-radius:var(--radius-sm);font-size:13px;color:var(--text-muted);">
            &#9749; Take a break &mdash; this may take a while depending on how many files your courses have.
          </div>
        </div>
        <style>@keyframes spin {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}</style>

        <script>
        // Daily motivational quote
        var quotes = [
          ["The secret of getting ahead is getting started.", "Mark Twain"],
          ["It always seems impossible until it's done.", "Nelson Mandela"],
          ["Success is the sum of small efforts repeated day in and day out.", "Robert Collier"],
          ["Don't watch the clock; do what it does. Keep going.", "Sam Levenson"],
          ["The expert in anything was once a beginner.", "Helen Hayes"],
          ["Education is the passport to the future.", "Malcolm X"],
          ["A little progress each day adds up to big results.", "Satya Nani"],
          ["The beautiful thing about learning is nobody can take it away from you.", "B.B. King"],
          ["You don't have to be great to start, but you have to start to be great.", "Zig Ziglar"],
          ["Study hard, for the well is deep, and our brains are shallow.", "Richard Baxter"],
          ["Push yourself, because no one else is going to do it for you.", "Unknown"],
          ["There are no shortcuts to any place worth going.", "Beverly Sills"],
          ["The mind is not a vessel to be filled, but a fire to be kindled.", "Plutarch"],
          ["Motivation is what gets you started. Habit is what keeps you going.", "Jim Ryun"]
        ];
        var dayIdx = Math.floor(Date.now() / 86400000) % quotes.length;
        document.getElementById('quote-text').textContent = '"' + quotes[dayIdx][0] + '"';
        document.getElementById('quote-author').textContent = '\u2014 ' + quotes[dayIdx][1];

        async function syncCourses() {{
          var btn = document.getElementById('sync-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Starting...';
          document.getElementById('sync-progress').style.display = 'block';
          try {{
            var r = await fetch('/api/student/sync', {{method: 'POST'}});
            var d = await r.json();
            if (!r.ok) {{ alert(d.error || 'Sync failed'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; return; }}
            pollSync(btn);
          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; }}
        }}
        function pollSync(btn) {{
          var iv = setInterval(async function() {{
            try {{
              var r = await fetch('/api/student/sync/status');
              var d = await r.json();
              document.getElementById('sync-msg').textContent = d.progress || 'Syncing...';
              document.getElementById('sync-courses').textContent = (d.courses_done||0) + '/' + (d.courses_total||0);
              document.getElementById('sync-files').textContent = d.files_downloaded || 0;
              if (d.status === 'done') {{
                clearInterval(iv);
                document.getElementById('sync-progress').style.display = 'none';
                btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas';
                var msg = 'Sync complete! ' + d.files_downloaded + ' syllabus files processed across ' + d.courses_done + ' courses.';
                if (d.warnings && d.warnings.length > 0) {{
                  msg += '\n\n\u26A0\uFE0F Warnings:\n' + d.warnings.join('\n');
                }}
                alert(msg);
                location.reload();
              }} else if (d.status === 'error') {{
                clearInterval(iv);
                document.getElementById('sync-progress').style.display = 'none';
                btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas';
                alert(d.progress || 'Sync failed');
              }}
            }} catch(e) {{}}
          }}, 2000);
        }}
        async function generatePlan() {{
          var btn = document.getElementById('plan-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';
          try {{
            var r = await fetch('/api/student/plan/generate', {{method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify({{preferences: {{hours_per_day: 5}}}}) }});
            var d = await r.json();
            if (!r.ok) {{ alert(d.error || 'Generation failed'); btn.disabled = false; btn.innerHTML = '&#129302; Generate Plan'; return; }}
            var iv = setInterval(async function() {{
              try {{
                var r2 = await fetch('/api/student/plan/status');
                var s = await r2.json();
                if (s.status === 'done') {{
                  clearInterval(iv);
                  alert('Study plan generated!');
                  location.reload();
                }} else if (s.status === 'error') {{
                  clearInterval(iv);
                  alert(s.progress || 'Plan generation failed');
                  btn.disabled = false; btn.innerHTML = '&#129302; Generate Plan';
                }}
              }} catch(e) {{}}
            }}, 2000);
          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = '&#129302; Generate Plan'; }}
        }}
        async function markComplete() {{
          var r = await fetch('/api/student/progress/complete', {{method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify({{}})}});
          if (r.ok) {{ alert('Today marked as complete!'); location.reload(); }}
        }}
        async function toggleDashAssignment(idx, completed) {{
          var today = new Date().toISOString().split('T')[0];
          var row = document.getElementById('dash-session-' + idx);
          if (completed) {{
            row.style.textDecoration = 'line-through';
            row.style.opacity = '0.6';
          }} else {{
            row.style.textDecoration = '';
            row.style.opacity = '1';
          }}
          try {{
            await fetch('/api/student/assignments/toggle', {{
              method: 'POST',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ date: today, session_index: idx, completed: completed }})
            }});
          }} catch(e) {{}}
        }}
        </script>
        """, active_page="student_dashboard")

    @app.route("/student/courses")
    def student_courses_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())
        rows = ""
        for c in courses:
            analysis = json.loads(c["analysis_json"]) if isinstance(c.get("analysis_json"), str) else c.get("analysis_json", {})
            n_exams = len(analysis.get("exams", []))
            has_sched = "Yes" if analysis.get("weekly_schedule") else "No"
            grading = analysis.get("grading", {})
            grading_str = ", ".join(f"{k}: {v}%" for k, v in list(grading.items())[:4]) if grading else "Not detected"
            synced = c.get("last_synced", "Never")
            # Count uploaded files
            uploaded_files = sdb.get_course_files(_cid(), c["id"])
            n_files = len(uploaded_files)
            files_list_html = ""
            for uf in uploaded_files:
                files_list_html += f"""<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:12px;">
                  <span>&#128196; {_esc(uf['original_name'])}</span>
                  <button onclick="deleteFile({uf['id']})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:11px;">&#10005;</button>
                </div>"""
            rows += f"""<tr>
              <td style="font-weight:600;"><a href="/student/courses/{c['id']}" style="color:var(--primary);text-decoration:none;">{_esc(c['name'])}</a></td>
              <td>{_esc(c.get('code',''))}</td>
              <td>{n_exams}</td>
              <td>{has_sched}</td>
              <td style="font-size:12px;">{_esc(grading_str)}</td>
              <td style="font-size:11px;color:var(--text-muted);">{n_files} file{'s' if n_files != 1 else ''}
                <button onclick="document.getElementById('upload-{c['id']}').click()" class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 6px;margin-left:4px;" title="Upload file">&#128206;</button>
                <input type="file" id="upload-{c['id']}" style="display:none;" accept=".pdf,.docx,.doc" onchange="uploadFile({c['id']},this)">
                {('<div style=\"margin-top:4px;border-top:1px solid var(--border);padding-top:4px;\">' + files_list_html + '</div>') if files_list_html else ''}
              </td>
              <td style="font-size:12px;color:var(--text-muted);">{synced}</td>
              <td><button onclick="deleteCourse({c['id']},'{_esc(c['name'][:30])}')" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;padding:4px 8px;" title="Remove course">&#128465;</button></td>
            </tr>"""
        if not rows:
            rows = """<tr><td colspan="8" style="text-align:center;padding:32px;color:var(--text-muted);">
              <div style="font-size:36px;margin-bottom:10px;">&#128218;</div>
              No courses synced yet. Connect Canvas and hit Sync.
            </td></tr>"""

        upload_buttons = ""
        for c in courses:
            cname = _esc(c["name"][:25])
            cid = c["id"]
            upload_buttons += f"<button onclick=\"document.getElementById('upload-{cid}').click()\" class=\"btn btn-outline btn-sm\">{cname}</button>"

        return _s_render("Courses", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
          <h1>&#128218; My Courses</h1>
          <button onclick="syncCourses()" class="btn btn-primary btn-sm" id="sync-btn">&#128260; Sync Canvas</button>
        </div>
        <div class="card">
          <table>
            <thead><tr><th>Course</th><th>Code</th><th>Exams</th><th>Schedule</th><th>Grading</th><th>Files</th><th>Last Synced</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div class="card" style="margin-top:16px;padding:16px;">
          <h3 style="margin:0 0 8px;">&#128206; Upload Course Files</h3>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">
            Upload PDFs or DOCX files (syllabi, class notes, schedules, etc.) to help the AI generate a better study plan.
            Click the &#128206; icon next to any course above, or use the buttons below.
          </p>
          <div style="display:flex;flex-wrap:wrap;gap:8px;">
            {upload_buttons}
          </div>
        </div>
        <div id="upload-bar" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 18px;margin-top:16px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px;">
            <span>&#128206; Uploading <b id="upload-bar-name"></b></span>
            <span id="upload-bar-pct">0%</span>
          </div>
          <div style="background:var(--bg);border-radius:8px;height:10px;overflow:hidden;">
            <div id="upload-bar-fill" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.3s ease;border-radius:8px;"></div>
          </div>
        </div>
        <div id="sync-progress" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;margin-top:16px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="animation:spin 1s linear infinite;display:inline-block;">&#9203;</span>
            <span id="sync-msg" style="color:var(--text-secondary);font-size:14px;">Starting sync...</span>
          </div>
          <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Courses: <span id="sync-courses">0/0</span> &middot; Files: <span id="sync-files">0</span></div>
          <div style="margin-top:10px;padding:10px 14px;background:var(--bg);border-radius:var(--radius-sm);font-size:13px;color:var(--text-muted);">
            &#9749; Take a break &mdash; this may take a while depending on how many files your courses have.
          </div>
        </div>
        <style>@keyframes spin {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}</style>
        <script>
        async function syncCourses() {{
          var btn = document.getElementById('sync-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Starting...';
          document.getElementById('sync-progress').style.display = 'block';
          try {{
            var r = await fetch('/api/student/sync', {{method: 'POST'}});
            var d = await r.json();
            if (!r.ok) {{ alert(d.error || 'Sync failed'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; return; }}
            var iv = setInterval(async function() {{
              try {{
                var r2 = await fetch('/api/student/sync/status');
                var s = await r2.json();
                document.getElementById('sync-msg').textContent = s.progress || 'Syncing...';
                document.getElementById('sync-courses').textContent = (s.courses_done||0) + '/' + (s.courses_total||0);
                document.getElementById('sync-files').textContent = s.files_downloaded || 0;
                if (s.status === 'done') {{
                  clearInterval(iv);
                  document.getElementById('sync-progress').style.display = 'none';
                  btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas';
                  alert('Sync complete! ' + s.files_downloaded + ' files processed.');
                  location.reload();
                }} else if (s.status === 'error') {{
                  clearInterval(iv);
                  document.getElementById('sync-progress').style.display = 'none';
                  btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas';
                  alert(s.progress || 'Sync failed');
                }}
              }} catch(e) {{}}
            }}, 2000);
          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; }}
        }}
        function showUploadBar(name) {{
          var bar = document.getElementById('upload-bar');
          document.getElementById('upload-bar-name').textContent = name;
          document.getElementById('upload-bar-fill').style.width = '0%';
          document.getElementById('upload-bar-pct').textContent = '0%';
          bar.style.display = 'block';
        }}
        function updateUploadBar(pct) {{
          document.getElementById('upload-bar-fill').style.width = pct + '%';
          document.getElementById('upload-bar-pct').textContent = Math.round(pct) + '%';
        }}
        function hideUploadBar(msg) {{
          document.getElementById('upload-bar-fill').style.width = '100%';
          document.getElementById('upload-bar-pct').textContent = msg || 'Done!';
          setTimeout(function(){{ document.getElementById('upload-bar').style.display = 'none'; }}, 1500);
        }}
        async function uploadFile(courseId, input) {{
          if (!input.files[0]) return;
          var fd = new FormData();
          fd.append('file', input.files[0]);
          var csrfToken = document.querySelector('meta[name="csrf-token"]');
          showUploadBar(input.files[0].name);
          try {{
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/student/courses/' + courseId + '/upload');
            if (csrfToken) xhr.setRequestHeader('X-CSRFToken', csrfToken.content);
            xhr.upload.onprogress = function(e) {{
              if (e.lengthComputable) updateUploadBar((e.loaded / e.total) * 80);
            }};
            xhr.onload = function() {{
              updateUploadBar(90);
              try {{
                var d = JSON.parse(xhr.responseText);
                if (xhr.status >= 200 && xhr.status < 300) {{
                  hideUploadBar('&#10003; Uploaded!');
                  setTimeout(function(){{ location.reload(); }}, 1000);
                }} else {{
                  hideUploadBar('Failed');
                  alert(d.error || 'Upload failed');
                }}
              }} catch(e) {{ hideUploadBar('Failed'); alert('Upload failed'); }}
            }};
            xhr.onerror = function() {{ hideUploadBar('Failed'); alert('Network error'); }};
            updateUploadBar(5);
            xhr.send(fd);
          }} catch(e) {{ hideUploadBar('Failed'); alert('Network error'); }}
          input.value = '';
        }}
        async function deleteFile(fileId) {{
          if (!confirm('Delete this file?')) return;
          await fetch('/api/student/files/' + fileId, {{method:'DELETE'}});
          location.reload();
        }}
        async function deleteCourse(courseId, name) {{
          if (!confirm('Remove "' + name + '"? This will delete all its exams and uploaded files.')) return;
          try {{
            var r = await fetch('/api/student/courses/' + courseId, {{method:'DELETE'}});
            if (r.ok) {{ location.reload(); }}
            else {{ var d = await r.json(); alert(d.error || 'Failed to remove course'); }}
          }} catch(e) {{ alert('Network error'); }}
        }}
        </script>
        """, active_page="student_courses")

    @app.route("/student/courses/<int:course_id>")
    def student_course_detail_page(course_id):
        if not _logged_in():
            return redirect(url_for("login"))
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return redirect(url_for("student_courses_page"))
        course = dict(course)
        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})
        exams = sdb.get_course_exams(course_id)
        uploaded_files = sdb.get_course_files(_cid(), course_id)

# Build exams HTML with per-exam file uploads
        exams_rows = ""
        for e in exams:
            topics = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            topics_str = ", ".join(topics) if topics else ""
            exam_files = sdb.get_course_files(_cid(), course_id, exam_id=e["id"])
            ef_html = ""
            for ef in exam_files:
                ef_html += f"<span style='display:inline-block;background:var(--bg);padding:2px 8px;border-radius:10px;font-size:11px;margin:2px;'>&#128196; {_esc(ef['original_name'])} <button onclick=\"deleteFile({ef['id']})\" style='background:none;border:none;color:var(--red);cursor:pointer;font-size:10px;'>&#10005;</button></span>"
            exams_rows += f"""<tr data-exam-id="{e['id']}">
              <td><input type="text" value="{_esc(e.get('name',''))}" class="edit-input" data-field="name"></td>
              <td><input type="date" value="{_esc(e.get('exam_date','') or '')}" class="edit-input" data-field="exam_date"></td>
              <td><input type="number" value="{e.get('weight_pct',0)}" class="edit-input" data-field="weight_pct" min="0" max="100" style="width:60px;">%</td>
              <td><input type="text" value="{_esc(topics_str)}" class="edit-input" data-field="topics" placeholder="Topic 1, Topic 2, ..." style="width:100%;"></td>
              <td style="font-size:11px;">
                {ef_html}
                <label class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 6px;cursor:pointer;" title="Upload file for this exam">
                  &#128206;
                  <input type="file" style="display:none;" accept=".pdf,.docx,.doc" onchange="try{{uploadExamFile({course_id},{e['id']},this)}}catch(err){{alert('Upload error: '+err.message)}}">
                </label>
              </td>
              <td>
                <button onclick="saveExam({e['id']},this)" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;" title="Save">&#128190;</button>
                <button onclick="deleteExam({e['id']})" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;color:var(--red);" title="Delete">&#128465;</button>
              </td>
            </tr>"""

        # Grading rows
        grading = analysis.get("grading", {})
        grading_rows = ""
        for k, v in grading.items():
            grading_rows += f"""<div class="grading-row" style="display:flex;gap:8px;margin-bottom:6px;align-items:center;">
              <input type="text" value="{_esc(k)}" class="edit-input" style="flex:2;" placeholder="Component name">
              <input type="number" value="{v}" class="edit-input" style="width:70px;" min="0" max="100">%
              <button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>
            </div>"""

        # Weekly schedule rows
        schedule = analysis.get("weekly_schedule", [])
        schedule_rows = ""
        for w in schedule:
            topics_str = ", ".join(w.get("topics", []))
            schedule_rows += f"""<div class="sched-row" style="display:flex;gap:8px;margin-bottom:6px;align-items:center;">
              <input type="text" value="Week {w.get('week','')}" class="edit-input" style="width:70px;" placeholder="Week" data-key="week">
              <input type="text" value="{_esc(w.get('dates',''))}" class="edit-input" style="width:120px;" placeholder="Dates" data-key="dates">
              <input type="text" value="{_esc(topics_str)}" class="edit-input" style="flex:1;" placeholder="Topics (comma-separated)" data-key="topics">
              <button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>
            </div>"""

        # Study tips
        tips = analysis.get("study_tips", [])
        tips_rows = ""
        for tip in tips:
            tips_rows += f"""<div class="tip-row" style="display:flex;gap:8px;margin-bottom:6px;align-items:center;">
              <input type="text" value="{_esc(tip)}" class="edit-input" style="flex:1;" placeholder="Study tip">
              <button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>
            </div>"""

        # Files list
        files_html = ""
        for uf in uploaded_files:
            files_html += f"""<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);">
              <span>&#128196; {_esc(uf['original_name'])} <span style="color:var(--text-muted);font-size:11px;">({uf.get('file_type','?')})</span></span>
              <button onclick="deleteFile({uf['id']})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:12px;">&#128465; Remove</button>
            </div>"""
        if not files_html:
            files_html = "<p style='color:var(--text-muted);font-size:13px;'>No files uploaded yet.</p>"

        return _s_render(f"Edit: {course['name']}", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px;">
          <div>
            <a href="/student/courses" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Courses</a>
            <h1 style="margin:4px 0 0;">&#9999;&#65039; Edit Course</h1>
          </div>
          <button onclick="saveCourseInfo()" class="btn btn-primary btn-sm" id="save-btn">&#128190; Save All Changes</button>
        </div>

        <!-- Course basic info -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header"><h2>&#128218; Course Info</h2></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:4px 0;">
            <div class="form-group">
              <label>Course Name</label>
              <input type="text" id="course-name" value="{_esc(course['name'])}" class="edit-input">
            </div>
            <div class="form-group">
              <label>Course Code</label>
              <input type="text" id="course-code" value="{_esc(course.get('code',''))}" class="edit-input">
            </div>
          </div>
        </div>

        <!-- Exams -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <h2>&#128221; Exams & Assessments</h2>
            <button onclick="addExamRow()" class="btn btn-outline btn-sm">+ Add Exam</button>
          </div>
          <table id="exams-table">
            <thead><tr><th>Name</th><th>Date</th><th>Weight</th><th>Topics</th><th>Files</th><th></th></tr></thead>
            <tbody>{exams_rows}</tbody>
          </table>
          <p style="font-size:12px;color:var(--text-muted);margin:8px 0 0;">Separate topics with commas. Click &#128190; to save each exam individually.</p>
        </div>

        <!-- Global upload toast (fixed position, always visible) -->
        <div id="upload-toast" style="display:none;position:fixed;top:0;left:0;right:0;z-index:99999;background:#1E1B4B;border-bottom:3px solid #7C3AED;padding:14px 24px;box-shadow:0 4px 24px rgba(0,0,0,0.4);">
          <div style="max-width:800px;margin:0 auto;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <span style="color:#E0E7FF;font-size:14px;font-weight:600;">&#128206; Uploading <span id="upload-toast-name" style="color:#A5B4FC;"></span></span>
              <span id="upload-toast-pct" style="color:#A5B4FC;font-size:14px;font-weight:700;">0%</span>
            </div>
            <div style="background:#312E81;border-radius:8px;height:12px;overflow:hidden;">
              <div id="upload-toast-fill" style="height:100%;background:linear-gradient(90deg,#7C3AED,#A78BFA,#C4B5FD);width:0%;transition:width 0.3s ease;border-radius:8px;"></div>
            </div>
            <div id="upload-toast-status" style="color:#A5B4FC;font-size:12px;margin-top:6px;">Starting upload...</div>
          </div>
        </div>

        <!-- Grading -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <h2>&#128202; Grading Breakdown</h2>
            <button onclick="addGradingRow()" class="btn btn-outline btn-sm">+ Add Component</button>
          </div>
          <div id="grading-container">
            {grading_rows}
          </div>
          <p style="font-size:12px;color:var(--text-muted);margin:8px 0 0;">Make sure percentages add up to 100%.</p>
        </div>

        <!-- Weekly schedule -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <h2>&#128197; Weekly Schedule</h2>
            <button onclick="addScheduleRow()" class="btn btn-outline btn-sm">+ Add Week</button>
          </div>
          <div id="schedule-container">
            {schedule_rows}
          </div>
        </div>

        <!-- Study tips -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <h2>&#128161; Study Tips</h2>
            <button onclick="addTipRow()" class="btn btn-outline btn-sm">+ Add Tip</button>
          </div>
          <div id="tips-container">
            {tips_rows}
          </div>
        </div>

        <!-- Uploaded files -->
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <h2>&#128206; Uploaded Files</h2>
            <label class="btn btn-outline btn-sm" style="cursor:pointer;">
              + Upload File
              <input type="file" style="display:none;" accept=".pdf,.docx,.doc" onchange="try{{uploadFile({course_id},this)}}catch(err){{alert('Upload error: '+err.message)}}">
            </label>
          </div>
          {files_html}
        </div>

        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        </style>

        <!-- Upload functions in isolated script block -->
        <script>
        function showUploadToast(name) {{
          var t = document.getElementById('upload-toast');
          document.getElementById('upload-toast-name').textContent = name;
          document.getElementById('upload-toast-fill').style.width = '0%';
          document.getElementById('upload-toast-pct').textContent = '0%';
          document.getElementById('upload-toast-status').textContent = 'Starting upload...';
          t.style.display = 'block';
        }}
        function updateUploadToast(pct, status) {{
          document.getElementById('upload-toast-fill').style.width = pct + '%';
          document.getElementById('upload-toast-pct').textContent = Math.round(pct) + '%';
          if (status) document.getElementById('upload-toast-status').textContent = status;
        }}
        function hideUploadToast(msg, success) {{
          document.getElementById('upload-toast-fill').style.width = '100%';
          document.getElementById('upload-toast-pct').textContent = success ? '&#10003;' : '&#10007;';
          document.getElementById('upload-toast-status').textContent = msg || 'Done!';
          var delay = success ? 1500 : 3000;
          setTimeout(function(){{ document.getElementById('upload-toast').style.display = 'none'; }}, delay);
        }}
        function doUpload(cid, fd, fileName) {{
          showUploadToast(fileName);
          updateUploadToast(5, 'Preparing upload...');
          var csrfToken = document.querySelector('meta[name="csrf-token"]');
          var xhr = new XMLHttpRequest();
          xhr.open('POST', '/api/student/courses/' + cid + '/upload');
          if (csrfToken) xhr.setRequestHeader('X-CSRFToken', csrfToken.content);
          xhr.upload.onprogress = function(e) {{
            if (e.lengthComputable) {{
              var pct = Math.round((e.loaded / e.total) * 80);
              updateUploadToast(pct, 'Uploading ' + fileName + '...');
            }}
          }};
          xhr.onload = function() {{
            updateUploadToast(90, 'Processing file...');
            try {{
              var d = JSON.parse(xhr.responseText);
              if (xhr.status >= 200 && xhr.status < 300) {{
                hideUploadToast('File uploaded successfully!', true);
                setTimeout(function(){{ location.reload(); }}, 1200);
              }} else {{
                var errMsg = d.error || 'Upload failed (status ' + xhr.status + ')';
                hideUploadToast(errMsg, false);
                alert('Upload failed: ' + errMsg);
              }}
            }} catch(e) {{
              hideUploadToast('Upload failed - server error (status ' + xhr.status + ')', false);
              alert('Upload failed - server returned status ' + xhr.status);
              console.error('Upload response:', xhr.status, xhr.responseText);
            }}
          }};
          xhr.onerror = function() {{
            hideUploadToast('Network error - check connection', false);
            alert('Upload network error - check your internet connection.');
          }};
          xhr.send(fd);
        }}
        function uploadFile(cid, input) {{
          if (!input.files[0]) return;
          var fd = new FormData();
          fd.append('file', input.files[0]);
          doUpload(cid, fd, input.files[0].name);
          input.value = '';
        }}
        function uploadExamFile(cid, examId, input) {{
          if (!input.files[0]) return;
          var fileName = input.files[0].name;
          var fd = new FormData();
          fd.append('file', input.files[0]);
          fd.append('exam_id', examId);
          doUpload(cid, fd, fileName);
          input.value = '';
        }}
        function deleteFile(fileId) {{
          if (!confirm('Delete this file?')) return;
          fetch('/api/student/files/' + fileId, {{method:'DELETE'}}).then(function(){{ location.reload(); }});
        }}
        </script>

        <script>
        var courseId = {course_id};

        async function saveCourseInfo() {{
          var btn = document.getElementById('save-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Saving...';

          // Collect grading
          var grading = {{}};
          document.querySelectorAll('#grading-container .grading-row').forEach(function(row) {{
            var inputs = row.querySelectorAll('input');
            var k = inputs[0].value.trim();
            var v = parseInt(inputs[1].value) || 0;
            if (k) grading[k] = v;
          }});

          // Collect schedule
          var schedule = [];
          document.querySelectorAll('#schedule-container .sched-row').forEach(function(row) {{
            var inputs = row.querySelectorAll('input');
            var weekStr = inputs[0].value.trim();
            var weekNum = parseInt(weekStr.replace(/[^0-9]/g,'')) || schedule.length + 1;
            var dates = inputs[1].value.trim();
            var topics = inputs[2].value.split(',').map(function(t){{ return t.trim(); }}).filter(Boolean);
            schedule.push({{ week: weekNum, dates: dates, topics: topics }});
          }});

          // Collect tips
          var tips = [];
          document.querySelectorAll('#tips-container .tip-row input').forEach(function(inp) {{
            var v = inp.value.trim();
            if (v) tips.push(v);
          }});

          try {{
            var csrfToken = document.querySelector('meta[name="csrf-token"]');
            var headers = {{'Content-Type':'application/json'}};
            if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;
            var r = await fetch('/api/student/courses/' + courseId, {{
              method:'PUT', headers: headers,
              body: JSON.stringify({{
                name: document.getElementById('course-name').value.trim(),
                code: document.getElementById('course-code').value.trim(),
                grading: grading,
                weekly_schedule: schedule,
                study_tips: tips
              }})
            }});
            if (r.ok) {{ alert('Course info saved!'); location.reload(); }}
            else {{ var d = await r.json(); alert(d.error || 'Save failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#128190; Save All Changes';
        }}

        function addExamRow() {{
          var tbody = document.querySelector('#exams-table tbody');
          var tr = document.createElement('tr');
          tr.dataset.examId = 'new';
          tr.innerHTML = '<td><input type="text" class="edit-input" data-field="name" placeholder="Exam name"></td>'
            + '<td><input type="date" class="edit-input" data-field="exam_date"></td>'
            + '<td><input type="number" class="edit-input" data-field="weight_pct" value="0" min="0" max="100" style="width:60px;">%</td>'
            + '<td><input type="text" class="edit-input" data-field="topics" placeholder="Topic 1, Topic 2, ..."></td>'
            + '<td style="font-size:11px;color:var(--text-muted);">Save first</td>'
            + '<td><button onclick="saveExam(null,this)" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;">&#128190;</button>'
            + ' <button onclick="this.closest(\'tr\').remove()" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;color:var(--red);">&#128465;</button></td>';
          tbody.appendChild(tr);
        }}

        async function saveExam(examId, btnEl) {{
          var tr = btnEl.closest('tr');
          var name = tr.querySelector('[data-field="name"]').value.trim();
          var exam_date = tr.querySelector('[data-field="exam_date"]').value;
          var weight_pct = parseInt(tr.querySelector('[data-field="weight_pct"]').value) || 0;
          var topicsRaw = tr.querySelector('[data-field="topics"]').value;
          var topics = topicsRaw.split(',').map(function(t){{ return t.trim(); }}).filter(Boolean);
          if (!name) {{ alert('Exam name is required'); return; }}
          var csrfToken = document.querySelector('meta[name="csrf-token"]');
          var headers = {{'Content-Type':'application/json'}};
          if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;
          try {{
            var url, method;
            if (examId) {{
              url = '/api/student/exams/' + examId;
              method = 'PUT';
            }} else {{
              url = '/api/student/courses/' + courseId + '/exams';
              method = 'POST';
            }}
            var r = await fetch(url, {{
              method: method, headers: headers,
              body: JSON.stringify({{ name: name, exam_date: exam_date, weight_pct: weight_pct, topics: topics, course_id: courseId }})
            }});
            if (r.ok) {{ alert('Exam saved!'); location.reload(); }}
            else {{ var d = await r.json(); alert(d.error || 'Failed'); }}
          }} catch(e) {{ alert('Network error'); }}
        }}

        async function deleteExam(examId) {{
          if (!confirm('Delete this exam?')) return;
          try {{
            await fetch('/api/student/exams/' + examId, {{method:'DELETE'}});
            location.reload();
          }} catch(e) {{ alert('Network error'); }}
        }}

        function addGradingRow() {{
          var c = document.getElementById('grading-container');
          var div = document.createElement('div');
          div.className = 'grading-row';
          div.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center;';
          div.innerHTML = '<input type="text" class="edit-input" style="flex:2;" placeholder="Component name">'
            + '<input type="number" class="edit-input" style="width:70px;" value="0" min="0" max="100">%'
            + '<button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>';
          c.appendChild(div);
        }}

        function addScheduleRow() {{
          var c = document.getElementById('schedule-container');
          var n = c.querySelectorAll('.sched-row').length + 1;
          var div = document.createElement('div');
          div.className = 'sched-row';
          div.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center;';
          div.innerHTML = '<input type="text" class="edit-input" style="width:70px;" value="Week ' + n + '" placeholder="Week">'
            + '<input type="text" class="edit-input" style="width:120px;" placeholder="Dates">'
            + '<input type="text" class="edit-input" style="flex:1;" placeholder="Topics (comma-separated)">'
            + '<button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>';
          c.appendChild(div);
        }}

        function addTipRow() {{
          var c = document.getElementById('tips-container');
          var div = document.createElement('div');
          div.className = 'tip-row';
          div.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center;';
          div.innerHTML = '<input type="text" class="edit-input" style="flex:1;" placeholder="Study tip">'
            + '<button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>';
          c.appendChild(div);
        }}

        </script>
        """, active_page="student_courses")

    @app.route("/student/exams")
    def student_exams_page():
        if not _logged_in():
            return redirect(url_for("login"))
        exams = sdb.get_upcoming_exams(_cid())
        rows = ""
        for e in exams:
            topics = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []
            topics_str = ", ".join(topics) if topics else "-"
            days_until = ""
            if e.get("exam_date"):
                try:
                    ed = datetime.strptime(e["exam_date"], "%Y-%m-%d").date()
                    d = (ed - datetime.now().date()).days
                    color = "#EF4444" if d <= 7 else "#F59E0B" if d <= 14 else "#10B981"
                    days_until = f"<span style='color:{color};font-weight:700;'>{d}d</span>"
                except ValueError:
                    days_until = "?"
            rows += f"""<tr>
              <td style="font-weight:600;">{_esc(e.get('course_name',''))}</td>
              <td>{_esc(e.get('name','Exam'))}</td>
              <td>{e.get('exam_date','TBD')}</td>
              <td>{days_until}</td>
              <td>{e.get('weight_pct',0)}%</td>
              <td style="font-size:12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_esc(topics_str)}</td>
            </tr>"""
        if not rows:
            rows = "<tr><td colspan='6' style='text-align:center;padding:32px;color:var(--text-muted);'>No upcoming exams detected. Sync your courses first.</td></tr>"

        return _s_render("Exams", f"""
        <h1 style="margin-bottom:20px;">&#128221; Upcoming Exams</h1>
        <div class="card">
          <table>
            <thead><tr><th>Course</th><th>Exam</th><th>Date</th><th>Days Left</th><th>Weight</th><th>Topics</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """, active_page="student_exams")

    @app.route("/student/plan")
    def student_plan_page():
        if not _logged_in():
            return redirect(url_for("login"))
        plan_row = sdb.get_latest_plan(_cid())
        progress = sdb.get_progress(_cid())
        completed_dates = set(p["plan_date"] for p in progress if p.get("completed"))

        # Gather assignment-level progress for all plan dates
        all_assignment_progress = {}
        if plan_row:
            for day in plan_row["plan_json"].get("daily_plan", []):
                d = day.get("date", "")
                if d:
                    ap = sdb.get_assignment_progress(_cid(), d)
                    all_assignment_progress[d] = {r["session_index"]: bool(r["completed"]) for r in ap}

        content = ""
        if not plan_row:
            content = """
            <div style="text-align:center;padding:60px 20px;">
              <div style="font-size:48px;margin-bottom:16px;">&#129302;</div>
              <h2>No study plan yet</h2>
              <p style="color:var(--text-muted);margin:12px 0 24px;">Sync your Canvas courses first, then generate an AI study plan.</p>
              <button onclick="generatePlan()" class="btn btn-primary" id="plan-btn">Generate Study Plan</button>
            </div>"""
        else:
            daily = plan_row["plan_json"].get("daily_plan", [])
            days_html = ""
            for day in daily[:30]:
                d = day.get("date", "")
                sessions = day.get("sessions", [])
                ap = all_assignment_progress.get(d, {})

                # Check if all sessions are complete
                all_done = len(sessions) > 0 and all(ap.get(i, False) for i in range(len(sessions)))
                is_free = len(sessions) == 0

                if is_free:
                    icon = "&#127947;"
                    bg = "var(--card)"
                    border_c = "var(--border)"
                elif all_done:
                    icon = "&#10003;"
                    bg = "var(--green-light, #D1FAE5)"
                    border_c = "var(--green, #10B981)"
                else:
                    icon = "&#9744;"
                    bg = "var(--card)"
                    border_c = "var(--border)"

                sessions_html = ""
                if is_free:
                    sessions_html = "<div style='font-size:13px;color:var(--text-muted);font-style:italic;padding:4px 0;'>Free day — no study scheduled</div>"
                else:
                    for idx, s in enumerate(sessions):
                        checked = "checked" if ap.get(idx, False) else ""
                        prio_colors = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}
                        pc = prio_colors.get(s.get("priority", "medium"), "#94A3B8")
                        strike = "text-decoration:line-through;opacity:0.6;" if ap.get(idx, False) else ""
                        sessions_html += f"""
                        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);{strike}" id="session-{d}-{idx}">
                          <input type="checkbox" {checked} onchange="toggleAssignment('{d}',{idx},this.checked)"
                            style="width:18px;height:18px;cursor:pointer;accent-color:var(--primary);">
                          <span style="font-weight:600;color:var(--text);">{_esc(s.get('course',''))}</span>
                          <span style="color:var(--text-secondary);font-size:13px;">{_esc(s.get('topic',''))}</span>
                          <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{s.get('hours',0)}h</span>
                          <span style="background:{pc}18;color:{pc};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;">{s.get('priority','').upper()}</span>
                        </div>"""

                completed_count = sum(1 for i in range(len(sessions)) if ap.get(i, False))
                progress_text = f"{completed_count}/{len(sessions)}" if sessions else ""

                days_html += f"""
                <div style="background:{bg};border:1px solid {border_c};border-radius:var(--radius-sm);padding:12px 16px;margin-bottom:8px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:{'8px' if sessions else '0'};">
                    <span style="font-weight:700;">{icon} {day.get('day_name','')} {d}</span>
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:12px;color:var(--text-muted);">{progress_text}</span>
                      <span style="font-size:13px;color:var(--text-muted);">{day.get('total_hours', 0)}h</span>
                    </div>
                  </div>
                  {sessions_html}
                </div>"""
            content = f"""
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <span style="font-size:13px;color:var(--text-muted);">Generated: {plan_row.get('generated_at','?')}</span>
              <div style="display:flex;gap:8px;">
                <a href="/student/schedule" class="btn btn-outline btn-sm">&#128337; Schedule & Difficulty</a>
                <button onclick="generatePlan()" class="btn btn-outline btn-sm" id="plan-btn">&#128260; Regenerate</button>
              </div>
            </div>
            <div style="background:var(--bg);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:16px;font-size:13px;color:var(--text-muted);">
              &#128161; Check off each assignment as you complete it. Incomplete assignments will be rolled over when the plan regenerates at midnight.
            </div>
            {days_html}"""

        return _s_render("Study Plan", f"""
        <h1 style="margin-bottom:20px;">&#128197; Study Plan</h1>
        <div class="card">{content}</div>
        <script>
        async function toggleAssignment(date, idx, completed) {{
          var row = document.getElementById('session-' + date + '-' + idx);
          if (completed) {{
            row.style.textDecoration = 'line-through';
            row.style.opacity = '0.6';
          }} else {{
            row.style.textDecoration = '';
            row.style.opacity = '1';
          }}
          try {{
            await fetch('/api/student/assignments/toggle', {{
              method: 'POST',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ date: date, session_index: idx, completed: completed }})
            }});
          }} catch(e) {{ console.error('Failed to save assignment progress:', e); }}
        }}
        async function generatePlan() {{
          var btn = document.getElementById('plan-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';
          try {{
            var r = await fetch('/api/student/plan/generate', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{preferences:{{hours_per_day:5}}}}) }});
            var d = await r.json();
            if (!r.ok) {{ alert(d.error || 'Failed'); btn.disabled = false; btn.innerHTML = '&#128260; Regenerate'; return; }}
            var iv = setInterval(async function() {{
              try {{
                var r2 = await fetch('/api/student/plan/status');
                var s = await r2.json();
                if (s.status === 'done') {{
                  clearInterval(iv);
                  alert('Study plan generated!');
                  location.reload();
                }} else if (s.status === 'error') {{
                  clearInterval(iv);
                  alert(s.progress || 'Plan generation failed');
                  btn.disabled = false; btn.innerHTML = '&#128260; Regenerate';
                }}
              }} catch(e) {{}}
            }}, 2000);
          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = '&#128260; Regenerate'; }}
        }}
        </script>
        """, active_page="student_plan")

    # ── Focus / Pomodoro page ───────────────────────────────

    @app.route("/student/focus")
    def student_focus_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())
        focus_stats = sdb.get_focus_stats(_cid())

        course_options = ""
        for c in courses:
            course_options += f'<option value="{_esc(c["name"])}">{_esc(c["name"])}</option>'

        return _s_render("Focus Mode", f"""
        <h1 style="margin-bottom:20px;">&#127917; Focus Mode</h1>

        <!-- Stats bar -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:20px;">
          <div class="stat-card stat-purple"><div class="num">{focus_stats['total_hours']}</div><div class="label">Hours Focused</div></div>
          <div class="stat-card stat-blue"><div class="num">{focus_stats['sessions']}</div><div class="label">Sessions</div></div>
          <div class="stat-card stat-green"><div class="num">{focus_stats['total_pages']}</div><div class="label">Pages Read</div></div>
          <div class="stat-card stat-red"><div class="num">{focus_stats['streak_days']}</div><div class="label">Day Streak &#128293;</div></div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
          <!-- Timer card -->
          <div class="card">
            <div class="card-header"><h2>&#9201; Study Timer</h2></div>

            <!-- Mode tabs -->
            <div style="display:flex;gap:8px;margin-bottom:16px;">
              <button onclick="setMode('pomodoro')" class="btn btn-sm mode-btn active" id="mode-pomodoro">&#127813; Pomodoro</button>
              <button onclick="setMode('pages')" class="btn btn-outline btn-sm mode-btn" id="mode-pages">&#128214; Page Method</button>
              <button onclick="setMode('custom')" class="btn btn-outline btn-sm mode-btn" id="mode-custom">&#9881; Custom</button>
            </div>

            <!-- Course selector -->
            <div class="form-group" style="margin-bottom:12px;">
              <label style="font-size:12px;">Studying for:</label>
              <select id="focus-course" class="edit-input">
                <option value="">General study</option>
                {course_options}
              </select>
            </div>

            <!-- Pomodoro settings -->
            <div id="settings-pomodoro">
              <div style="display:flex;gap:10px;margin-bottom:12px;">
                <div class="form-group" style="flex:1;">
                  <label style="font-size:12px;">Work (min)</label>
                  <input type="number" id="pomo-work" value="25" min="5" max="120" class="edit-input">
                </div>
                <div class="form-group" style="flex:1;">
                  <label style="font-size:12px;">Break (min)</label>
                  <input type="number" id="pomo-break" value="5" min="1" max="30" class="edit-input">
                </div>
                <div class="form-group" style="flex:1;">
                  <label style="font-size:12px;">Long break (min)</label>
                  <input type="number" id="pomo-long" value="15" min="5" max="60" class="edit-input">
                </div>
              </div>
              <p style="font-size:12px;color:var(--text-muted);margin:0;">Long break after every 4 sessions.</p>
            </div>

            <!-- Page method settings -->
            <div id="settings-pages" style="display:none;">
              <div class="form-group" style="margin-bottom:12px;">
                <label style="font-size:12px;">Target pages</label>
                <input type="number" id="page-target" value="20" min="1" max="500" class="edit-input" onchange="updatePageProgress()">
              </div>
              <!-- Big page counter display -->
              <div style="text-align:center;margin:12px 0;">
                <div id="page-counter-display" style="font-size:48px;font-weight:800;color:var(--primary);">0</div>
                <div id="page-status" style="font-size:14px;color:var(--text-muted);">0 / 20 pages</div>
              </div>
              <div style="background:var(--bg);border-radius:8px;height:12px;overflow:hidden;margin-bottom:16px;">
                <div id="page-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.5s ease;border-radius:8px;"></div>
              </div>
              <!-- Big satisfying page-done button -->
              <button id="page-done-btn" onclick="clickPage()" style="
                display:block;width:100%;padding:20px;font-size:22px;font-weight:700;
                background:linear-gradient(135deg,var(--primary),#8B5CF6);color:#fff;
                border:none;border-radius:16px;cursor:pointer;
                transition:transform 0.15s ease,box-shadow 0.15s ease;
                box-shadow:0 4px 16px rgba(139,92,246,0.3);
                user-select:none;
              " onmousedown="this.style.transform='scale(0.95)'" onmouseup="this.style.transform='scale(1)'" onmouseleave="this.style.transform='scale(1)'">
                &#128214; Page Completed!
              </button>
              <div style="display:flex;gap:8px;margin-top:10px;justify-content:center;">
                <button onclick="undoPage()" class="btn btn-ghost btn-sm" style="font-size:12px;">&#8630; Undo last</button>
                <button onclick="resetPages()" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128260; Reset</button>
              </div>
            </div>

            <!-- Custom timer settings -->
            <div id="settings-custom" style="display:none;">
              <div class="form-group" style="margin-bottom:12px;">
                <label style="font-size:12px;">Duration (min)</label>
                <input type="number" id="custom-mins" value="45" min="5" max="300" class="edit-input">
              </div>
            </div>

            <!-- Timer display -->
            <div style="text-align:center;padding:24px 0;">
              <div id="timer-display" style="font-size:64px;font-weight:800;font-family:monospace;color:var(--text);letter-spacing:2px;">25:00</div>
              <div id="timer-label" style="font-size:14px;color:var(--text-muted);margin-top:4px;">Ready to focus</div>
              <div id="pomo-count" style="font-size:12px;color:var(--text-muted);margin-top:4px;">Session 1 of 4</div>
            </div>

            <!-- Controls -->
            <div style="display:flex;justify-content:center;gap:12px;">
              <button onclick="startTimer()" id="start-btn" class="btn btn-primary">&#9654; Start</button>
              <button onclick="pauseTimer()" id="pause-btn" class="btn btn-outline" style="display:none;">&#10074;&#10074; Pause</button>
              <button onclick="resetTimer()" id="reset-btn" class="btn btn-outline">&#8635; Reset</button>
              <button onclick="skipPhase()" id="skip-btn" class="btn btn-ghost btn-sm" style="display:none;">Skip &raquo;</button>
            </div>
            <p style="font-size:11px;color:var(--text-muted);text-align:center;margin-top:10px;">
              Shortcuts: <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">Space</kbd> start/pause
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">R</kbd> reset
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">S</kbd> skip
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">P</kbd> page done
            </p>
          </div>

          <!-- Right column -->
          <div>
            <!-- Spotify card -->
            <div class="card" style="margin-bottom:16px;">
              <div class="card-header"><h2>&#127925; Study Music</h2></div>
              <div style="margin-bottom:12px;">
                <label style="font-size:12px;color:var(--text-muted);">Paste a Spotify playlist or album link:</label>
                <div style="display:flex;gap:8px;margin-top:4px;">
                  <input type="text" id="spotify-url" class="edit-input" placeholder="https://open.spotify.com/playlist/..."
                    value="https://open.spotify.com/playlist/0vvXsWCC9xrXsKd4FyS8kM">
                  <button onclick="loadSpotify()" class="btn btn-outline btn-sm">Load</button>
                </div>
              </div>
              <div id="spotify-embed">
                <iframe id="spotify-iframe" style="border-radius:12px;width:100%;height:352px;border:0;"
                  src="https://open.spotify.com/embed/playlist/0vvXsWCC9xrXsKd4FyS8kM?utm_source=generator&theme=0"
                  allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>
              </div>
              <div style="margin-top:8px;">
                <p style="font-size:11px;color:var(--text-muted);">Quick picks:</p>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                  <button onclick="setPlaylist('0vvXsWCC9xrXsKd4FyS8kM')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127911; Lo-fi Beats</button>
                  <button onclick="setPlaylist('37i9dQZF1DWWQRwui0ExPn')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127926; Lo-Fi</button>
                  <button onclick="setPlaylist('37i9dQZF1DX8Uebhn9wzrS')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127764; Chill Study</button>
                  <button onclick="setPlaylist('37i9dQZF1DX9sIqqvKsjG8')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127793; Deep Focus</button>
                  <button onclick="setPlaylist('37i9dQZF1DWZeKCadgRdKQ')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#9749; Coffee Jazz</button>
                </div>
              </div>
            </div>

            <!-- Flashcard quick-review -->
            <div class="card" style="margin-bottom:16px;">
              <div class="card-header"><h2>&#127183; Quick Flashcards</h2></div>
              <div id="flashcard-area">
                <div id="flashcard-box" onclick="flipCard()" style="
                  min-height:120px;background:var(--bg);border-radius:12px;padding:24px;
                  text-align:center;cursor:pointer;display:flex;align-items:center;justify-content:center;
                  font-size:16px;font-weight:500;border:2px dashed var(--border);transition:all 0.3s ease;
                  user-select:none;
                ">
                  <span style="color:var(--text-muted);">Click + Add Flashcard to start, then click to flip</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;">
                  <div style="display:flex;gap:6px;">
                    <button onclick="prevCard()" class="btn btn-ghost btn-sm">&larr; Prev</button>
                    <button onclick="nextCard()" class="btn btn-ghost btn-sm">Next &rarr;</button>
                  </div>
                  <span id="card-counter" style="font-size:12px;color:var(--text-muted);">0 / 0</span>
                  <div style="display:flex;gap:6px;">
                    <button onclick="deleteCard()" class="btn btn-ghost btn-sm" style="color:var(--red);" title="Delete current card">&#128465;</button>
                    <button onclick="showAddCard()" class="btn btn-outline btn-sm">+ Add</button>
                  </div>
                </div>
                <div id="add-card-form" style="display:none;margin-top:10px;background:var(--bg);border-radius:8px;padding:12px;">
                  <input type="text" id="card-front" class="edit-input" placeholder="Front (question)" style="margin-bottom:6px;">
                  <input type="text" id="card-back" class="edit-input" placeholder="Back (answer)" style="margin-bottom:8px;">
                  <div style="display:flex;gap:6px;">
                    <button onclick="addCard()" class="btn btn-primary btn-sm">Add Card</button>
                    <button onclick="document.getElementById('add-card-form').style.display='none'" class="btn btn-ghost btn-sm">Cancel</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- Quick notes -->
            <div class="card">
              <div class="card-header"><h2>&#128221; Quick Notes</h2></div>
              <textarea id="focus-notes" class="edit-input" rows="5" placeholder="Jot down notes while studying..." style="resize:vertical;"></textarea>
              <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">Notes are saved in your browser.</p>
            </div>
          </div>
        </div>

        <style>
        .mode-btn.active {{ background:var(--primary);color:#fff;border-color:var(--primary); }}
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        #page-done-btn:hover {{ box-shadow:0 6px 24px rgba(139,92,246,0.45); }}
        #page-done-btn:active {{ transform:scale(0.93) !important; }}
        @keyframes pagePop {{ 0%{{transform:scale(1);}} 50%{{transform:scale(1.15);}} 100%{{transform:scale(1);}} }}
        @keyframes confettiFade {{ 0%{{opacity:1;transform:translateY(0) rotate(0deg);}} 100%{{opacity:0;transform:translateY(-60px) rotate(180deg);}} }}
        .page-confetti {{ position:absolute;pointer-events:none;font-size:20px;animation:confettiFade 0.8s ease-out forwards; }}
        </style>

        <script>
        /* === Calming alarm audio === */
        var alarmCtx = null;
        function playAlarm() {{
          try {{
            if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
            var ctx = alarmCtx;
            var now = ctx.currentTime;
            // Gentle 3-chime bell sound
            var freqs = [523.25, 659.25, 783.99]; // C5, E5, G5 major chord
            freqs.forEach(function(freq, i) {{
              var osc = ctx.createOscillator();
              var gain = ctx.createGain();
              osc.type = 'sine';
              osc.frequency.value = freq;
              gain.gain.setValueAtTime(0, now + i * 0.5);
              gain.gain.linearRampToValueAtTime(0.25, now + i * 0.5 + 0.05);
              gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.5 + 1.5);
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(now + i * 0.5);
              osc.stop(now + i * 0.5 + 1.8);
            }});
            // Final gentle chord
            [523.25, 659.25, 783.99].forEach(function(freq) {{
              var osc = ctx.createOscillator();
              var gain = ctx.createGain();
              osc.type = 'sine';
              osc.frequency.value = freq;
              gain.gain.setValueAtTime(0, now + 1.8);
              gain.gain.linearRampToValueAtTime(0.15, now + 1.9);
              gain.gain.exponentialRampToValueAtTime(0.001, now + 3.5);
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(now + 1.8);
              osc.stop(now + 3.8);
            }});
          }} catch(e) {{}}
        }}

        /* === Timer state === */
        var timerInterval = null;
        var timeLeft = 25 * 60;
        var totalTime = 25 * 60;
        var isRunning = false;
        var isBreak = false;
        var pomoCount = 0;
        var currentMode = 'pomodoro';
        var totalFocusSeconds = 0;
        var sessionStarted = false;
        var pageDone = 0;

        // Load saved notes
        var savedNotes = localStorage.getItem('focus_notes');
        if (savedNotes) document.getElementById('focus-notes').value = savedNotes;
        document.getElementById('focus-notes').addEventListener('input', function() {{
          localStorage.setItem('focus_notes', this.value);
        }});

        /* === Flashcards (localStorage) === */
        var flashcards = JSON.parse(localStorage.getItem('focus_flashcards') || '[]');
        var cardIndex = 0;
        var cardFlipped = false;
        function renderCard() {{
          var box = document.getElementById('flashcard-box');
          var counter = document.getElementById('card-counter');
          if (flashcards.length === 0) {{
            box.innerHTML = '<span style="color:var(--text-muted);">Click + Add Flashcard to start, then click to flip</span>';
            counter.textContent = '0 / 0';
            return;
          }}
          var c = flashcards[cardIndex];
          box.innerHTML = cardFlipped
            ? '<div style="color:var(--green);font-size:14px;margin-bottom:4px;">ANSWER</div><div>' + c.back + '</div>'
            : '<div style="color:var(--primary);font-size:14px;margin-bottom:4px;">QUESTION</div><div>' + c.front + '</div>';
          box.style.borderColor = cardFlipped ? 'var(--green)' : 'var(--border)';
          counter.textContent = (cardIndex + 1) + ' / ' + flashcards.length;
        }}
        function flipCard() {{ if (flashcards.length > 0) {{ cardFlipped = !cardFlipped; renderCard(); }} }}
        function nextCard() {{ if (flashcards.length > 0) {{ cardIndex = (cardIndex + 1) % flashcards.length; cardFlipped = false; renderCard(); }} }}
        function prevCard() {{ if (flashcards.length > 0) {{ cardIndex = (cardIndex - 1 + flashcards.length) % flashcards.length; cardFlipped = false; renderCard(); }} }}
        function showAddCard() {{ document.getElementById('add-card-form').style.display = ''; document.getElementById('card-front').focus(); }}
        function deleteCard() {{
          if (flashcards.length === 0) return;
          if (!confirm('Delete this flashcard?')) return;
          flashcards.splice(cardIndex, 1);
          localStorage.setItem('focus_flashcards', JSON.stringify(flashcards));
          if (cardIndex >= flashcards.length) cardIndex = Math.max(0, flashcards.length - 1);
          cardFlipped = false;
          renderCard();
        }}
        function addCard() {{
          var f = document.getElementById('card-front').value.trim();
          var b = document.getElementById('card-back').value.trim();
          if (!f || !b) {{ alert('Both sides required'); return; }}
          flashcards.push({{front: f, back: b}});
          localStorage.setItem('focus_flashcards', JSON.stringify(flashcards));
          cardIndex = flashcards.length - 1; cardFlipped = false;
          document.getElementById('card-front').value = '';
          document.getElementById('card-back').value = '';
          document.getElementById('add-card-form').style.display = 'none';
          renderCard();
        }}
        renderCard();

        /* === Mode switching === */
        function setMode(mode) {{
          currentMode = mode;
          document.querySelectorAll('.mode-btn').forEach(function(b) {{ b.classList.remove('active'); b.classList.add('btn-outline'); }});
          document.getElementById('mode-' + mode).classList.add('active');
          document.getElementById('mode-' + mode).classList.remove('btn-outline');
          document.getElementById('settings-pomodoro').style.display = mode === 'pomodoro' ? '' : 'none';
          document.getElementById('settings-pages').style.display = mode === 'pages' ? '' : 'none';
          document.getElementById('settings-custom').style.display = mode === 'custom' ? '' : 'none';
          document.getElementById('pomo-count').style.display = mode === 'pomodoro' ? '' : 'none';
          resetTimer();
          if (mode === 'pomodoro') {{
            timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;
          }} else if (mode === 'pages') {{
            timeLeft = 0; totalTime = 0;
            pageDone = 0;
            document.getElementById('page-counter-display').textContent = '0';
            document.getElementById('timer-display').textContent = '00:00';
            document.getElementById('timer-label').textContent = 'Start timer, then click the big button for each page';
          }} else {{
            timeLeft = parseInt(document.getElementById('custom-mins').value) * 60;
          }}
          totalTime = timeLeft;
          updateDisplay();
        }}

        function updateDisplay() {{
          if (currentMode === 'pages' && isRunning) {{
            var elapsed = totalFocusSeconds;
            var m = Math.floor(elapsed / 60);
            var s = elapsed % 60;
            document.getElementById('timer-display').textContent = String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
          }} else if (currentMode !== 'pages') {{
            var m = Math.floor(timeLeft / 60);
            var s = timeLeft % 60;
            document.getElementById('timer-display').textContent = String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
          }}
        }}

        /* === Page method click === */
        function clickPage() {{
          if (!isRunning) {{ startTimer(); }}
          pageDone++;
          document.getElementById('page-counter-display').textContent = pageDone;
          document.getElementById('page-counter-display').style.animation = 'none';
          void document.getElementById('page-counter-display').offsetWidth;
          document.getElementById('page-counter-display').style.animation = 'pagePop 0.3s ease';
          updatePageProgress();
          // Confetti burst
          var btn = document.getElementById('page-done-btn');
          var rect = btn.getBoundingClientRect();
          var emojis = ['&#127881;','&#11088;','&#128214;','&#127942;','&#10024;','&#128640;'];
          for (var i = 0; i < 6; i++) {{
            var span = document.createElement('span');
            span.className = 'page-confetti';
            span.innerHTML = emojis[i % emojis.length];
            span.style.left = (rect.left + Math.random() * rect.width) + 'px';
            span.style.top = (rect.top + window.scrollY - 10) + 'px';
            document.body.appendChild(span);
            setTimeout(function(s){{ s.remove(); }}, 900, span);
          }}
        }}
        function undoPage() {{
          if (pageDone > 0) {{
            pageDone--;
            document.getElementById('page-counter-display').textContent = pageDone;
            updatePageProgress();
          }}
        }}
        function resetPages() {{
          pageDone = 0;
          document.getElementById('page-counter-display').textContent = '0';
          updatePageProgress();
        }}
        function updatePageProgress() {{
          var target = parseInt(document.getElementById('page-target').value) || 1;
          var pct = Math.min(100, Math.round(pageDone / target * 100));
          document.getElementById('page-bar').style.width = pct + '%';
          document.getElementById('page-status').textContent = pageDone + ' / ' + target + ' pages (' + pct + '%)';
          if (pageDone >= target && sessionStarted) {{
            playAlarm();
            saveFocusSession();
            document.getElementById('timer-label').textContent = '&#127881; Page goal reached!';
          }}
        }}

        /* === Timer controls === */
        function startTimer() {{
          if (isRunning) return;
          isRunning = true;
          sessionStarted = true;
          document.getElementById('start-btn').style.display = 'none';
          document.getElementById('pause-btn').style.display = '';
          document.getElementById('skip-btn').style.display = currentMode === 'pomodoro' ? '' : 'none';

          if (currentMode === 'pages') {{
            document.getElementById('timer-label').textContent = '&#128214; Reading \u2014 click the big button for each page';
            timerInterval = setInterval(function() {{
              totalFocusSeconds++;
              updateDisplay();
            }}, 1000);
          }} else {{
            document.getElementById('timer-label').textContent = isBreak ? '&#9749; Break time!' : '&#128293; Focus!';
            timerInterval = setInterval(function() {{
              timeLeft--;
              if (!isBreak) totalFocusSeconds++;
              updateDisplay();
              if (timeLeft <= 0) {{
                clearInterval(timerInterval);
                isRunning = false;
                onTimerEnd();
              }}
            }}, 1000);
          }}
        }}

        function pauseTimer() {{
          clearInterval(timerInterval);
          isRunning = false;
          document.getElementById('start-btn').style.display = '';
          document.getElementById('pause-btn').style.display = 'none';
          document.getElementById('timer-label').textContent = 'Paused';
        }}

        function resetTimer() {{
          clearInterval(timerInterval);
          isRunning = false;
          isBreak = false;
          if (sessionStarted && totalFocusSeconds > 60) {{
            saveFocusSession();
          }}
          totalFocusSeconds = 0;
          sessionStarted = false;
          pomoCount = 0;
          pageDone = 0;
          document.getElementById('start-btn').style.display = '';
          document.getElementById('pause-btn').style.display = 'none';
          document.getElementById('skip-btn').style.display = 'none';
          if (currentMode === 'pomodoro') {{
            timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;
            document.getElementById('pomo-count').textContent = 'Session 1 of 4';
          }} else if (currentMode === 'custom') {{
            timeLeft = parseInt(document.getElementById('custom-mins').value) * 60;
          }} else {{
            timeLeft = 0;
            document.getElementById('page-counter-display').textContent = '0';
          }}
          totalTime = timeLeft;
          updateDisplay();
          document.getElementById('timer-label').textContent = 'Ready to focus';
        }}

        function skipPhase() {{
          clearInterval(timerInterval);
          isRunning = false;
          onTimerEnd();
        }}

        function onTimerEnd() {{
          playAlarm();
          if (currentMode === 'pomodoro') {{
            if (!isBreak) {{
              pomoCount++;
              document.getElementById('pomo-count').textContent = 'Completed ' + pomoCount + ' of 4';
              if (pomoCount % 4 === 0) {{
                timeLeft = parseInt(document.getElementById('pomo-long').value) * 60;
                document.getElementById('timer-label').textContent = '&#127881; Long break!';
              }} else {{
                timeLeft = parseInt(document.getElementById('pomo-break').value) * 60;
                document.getElementById('timer-label').textContent = '&#9749; Short break';
              }}
              isBreak = true;
            }} else {{
              timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;
              document.getElementById('timer-label').textContent = 'Ready for next session';
              isBreak = false;
            }}
            totalTime = timeLeft;
            updateDisplay();
            document.getElementById('start-btn').style.display = '';
            document.getElementById('pause-btn').style.display = 'none';
          }} else {{
            saveFocusSession();
            document.getElementById('timer-label').textContent = '&#10003; Session complete!';
            document.getElementById('start-btn').style.display = '';
            document.getElementById('pause-btn').style.display = 'none';
          }}
        }}

        async function saveFocusSession() {{
          var minutes = Math.round(totalFocusSeconds / 60);
          var pages = currentMode === 'pages' ? pageDone : 0;
          var course = document.getElementById('focus-course').value;
          try {{
            await fetch('/api/student/focus/save', {{
              method: 'POST',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ mode: currentMode, minutes: minutes, pages: pages, course_name: course }})
            }});
          }} catch(e) {{}}
        }}

        function loadSpotify() {{
          var url = document.getElementById('spotify-url').value.trim();
          var match = url.match(/open\\.spotify\\.com\\/(playlist|album|track)\\/([a-zA-Z0-9]+)/);
          if (!match) {{ alert('Paste a valid Spotify link'); return; }}
          setPlaylist(match[2], match[1]);
        }}

        function setPlaylist(id, type) {{
          type = type || 'playlist';
          document.getElementById('spotify-iframe').src =
            'https://open.spotify.com/embed/' + type + '/' + id + '?utm_source=generator&theme=0';
        }}

        // Keyboard shortcuts
        document.addEventListener('keydown', function(e) {{
          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
          if (e.code === 'Space') {{ e.preventDefault(); if (isRunning) pauseTimer(); else startTimer(); }}
          if (e.code === 'KeyR' && !e.ctrlKey) {{ e.preventDefault(); resetTimer(); }}
          if (e.code === 'KeyS' && !e.ctrlKey) {{ e.preventDefault(); skipPhase(); }}
          if (e.code === 'KeyP' && currentMode === 'pages') {{ e.preventDefault(); clickPage(); }}
        }});
        </script>
        """, active_page="student_focus")

    # ── GPA Calculator page ─────────────────────────────────

    @app.route("/student/gpa")
    def student_gpa_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())

        course_rows = ""
        for c in courses:
            analysis = json.loads(c["analysis_json"]) if isinstance(c.get("analysis_json"), str) else c.get("analysis_json", {})
            course_rows += f"""<div class="gpa-row" style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">
              <input type="text" value="{_esc(c['name'])}" class="edit-input" style="flex:2;" placeholder="Course name">
              <input type="number" value="3" class="edit-input" style="width:70px;" min="1" max="10" placeholder="Credits">
              <select class="edit-input" style="width:80px;">
                <option value="4.0">A</option><option value="3.7">A-</option>
                <option value="3.3">B+</option><option value="3.0" selected>B</option><option value="2.7">B-</option>
                <option value="2.3">C+</option><option value="2.0">C</option><option value="1.7">C-</option>
                <option value="1.3">D+</option><option value="1.0">D</option><option value="0.0">F</option>
              </select>
              <button onclick="this.parentElement.remove();calcGPA();" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>
            </div>"""

        return _s_render("GPA Calculator", f"""
        <h1 style="margin-bottom:20px;">&#127891; GPA Calculator</h1>
        <div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;">
          <div class="card">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
              <h2>&#128218; Courses</h2>
              <button onclick="addGPARow()" class="btn btn-outline btn-sm">+ Add Course</button>
            </div>
            <div style="display:flex;gap:8px;margin-bottom:8px;font-size:12px;font-weight:600;color:var(--text-muted);padding:0 4px;">
              <span style="flex:2;">Course</span><span style="width:70px;">Credits</span><span style="width:80px;">Grade</span><span style="width:20px;"></span>
            </div>
            <div id="gpa-rows">
              {course_rows}
            </div>
            <button onclick="calcGPA()" class="btn btn-primary btn-sm" style="margin-top:12px;">Calculate GPA</button>
          </div>
          <div>
            <div class="card" style="text-align:center;">
              <div class="card-header"><h2>Your GPA</h2></div>
              <div id="gpa-result" style="font-size:56px;font-weight:800;color:var(--primary);padding:20px 0;">-</div>
              <div id="gpa-scale" style="font-size:14px;color:var(--text-muted);">out of 4.0</div>
              <div id="gpa-credits" style="font-size:13px;color:var(--text-muted);margin-top:8px;"></div>
            </div>
            <div class="card" style="margin-top:16px;">
              <div class="card-header"><h2>&#128200; What-If</h2></div>
              <p style="font-size:13px;color:var(--text-muted);">Enter your current cumulative GPA to see how this semester affects it:</p>
              <div style="display:flex;gap:8px;margin-top:8px;">
                <div class="form-group" style="flex:1;">
                  <label style="font-size:12px;">Current GPA</label>
                  <input type="number" id="cum-gpa" step="0.01" min="0" max="4" value="0" class="edit-input">
                </div>
                <div class="form-group" style="flex:1;">
                  <label style="font-size:12px;">Total credits</label>
                  <input type="number" id="cum-credits" min="0" value="0" class="edit-input">
                </div>
              </div>
              <button onclick="calcCumGPA()" class="btn btn-outline btn-sm" style="margin-top:8px;width:100%;">Calculate Cumulative</button>
              <div id="cum-result" style="text-align:center;font-size:24px;font-weight:700;color:var(--text);margin-top:12px;">-</div>
            </div>
          </div>
        </div>
        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        </style>
        <script>
        function addGPARow() {{
          var c = document.getElementById('gpa-rows');
          var div = document.createElement('div');
          div.className = 'gpa-row';
          div.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;align-items:center;';
          div.innerHTML = '<input type="text" class="edit-input" style="flex:2;" placeholder="Course name">'
            + '<input type="number" class="edit-input" style="width:70px;" value="3" min="1" max="10" placeholder="Credits">'
            + '<select class="edit-input" style="width:80px;"><option value="4.0">A</option><option value="3.7">A-</option><option value="3.3">B+</option><option value="3.0" selected>B</option><option value="2.7">B-</option><option value="2.3">C+</option><option value="2.0">C</option><option value="1.7">C-</option><option value="1.3">D+</option><option value="1.0">D</option><option value="0.0">F</option></select>'
            + '<button onclick="this.parentElement.remove();calcGPA();" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>';
          c.appendChild(div);
        }}

        function calcGPA() {{
          var rows = document.querySelectorAll('#gpa-rows .gpa-row');
          var totalPts = 0, totalCreds = 0;
          rows.forEach(function(row) {{
            var inputs = row.querySelectorAll('input');
            var sel = row.querySelector('select');
            var credits = parseFloat(inputs[1].value) || 0;
            var grade = parseFloat(sel.value);
            totalPts += credits * grade;
            totalCreds += credits;
          }});
          var gpa = totalCreds > 0 ? (totalPts / totalCreds).toFixed(2) : '0.00';
          document.getElementById('gpa-result').textContent = gpa;
          document.getElementById('gpa-credits').textContent = totalCreds + ' credits this semester';
          var g = parseFloat(gpa);
          document.getElementById('gpa-result').style.color = g >= 3.5 ? 'var(--green)' : g >= 2.5 ? 'var(--primary)' : g >= 2.0 ? '#F59E0B' : 'var(--red)';
        }}

        function calcCumGPA() {{
          // Get current semester GPA first
          calcGPA();
          var semGPA = parseFloat(document.getElementById('gpa-result').textContent) || 0;
          var semRows = document.querySelectorAll('#gpa-rows .gpa-row');
          var semCredits = 0;
          semRows.forEach(function(row) {{ semCredits += parseFloat(row.querySelectorAll('input')[1].value) || 0; }});
          var cumGPA = parseFloat(document.getElementById('cum-gpa').value) || 0;
          var cumCredits = parseInt(document.getElementById('cum-credits').value) || 0;
          var totalPts = (cumGPA * cumCredits) + (semGPA * semCredits);
          var totalCreds = cumCredits + semCredits;
          var result = totalCreds > 0 ? (totalPts / totalCreds).toFixed(2) : '0.00';
          document.getElementById('cum-result').textContent = 'Cumulative GPA: ' + result;
        }}

        calcGPA();
        </script>
        """, active_page="student_gpa")

    @app.route("/student/canvas-settings")
    def student_canvas_settings_page():
        if not _logged_in():
            return redirect(url_for("login"))
        tok = sdb.get_canvas_token(_cid())
        connected = bool(tok)
        url_val = tok["canvas_url"] if tok else ""

        return _s_render("Canvas Settings", f"""
        <h1 style="margin-bottom:20px;">&#128279; Canvas Connection</h1>
        <div class="card" style="max-width:600px;">
          <div style="margin-bottom:20px;">
            <span style="display:inline-block;padding:4px 12px;border-radius:12px;font-size:13px;font-weight:600;background:{'#D1FAE5' if connected else '#FEE2E2'};color:{'#065F46' if connected else '#991B1B'};">
              {'&#10003; Connected' if connected else '&#10007; Not Connected'}
            </span>
            {'<span style="color:var(--text-muted);font-size:13px;margin-left:10px;">' + _esc(url_val) + '</span>' if connected else ''}
          </div>

          <form onsubmit="connectCanvas(event)">
            <div class="form-group">
              <label>Canvas URL</label>
              <input id="canvas-url" type="url" placeholder="https://yourschool.instructure.com" value="{_esc(url_val)}" required>
            </div>
            <div class="form-group">
              <label>API Access Token</label>
              <input id="canvas-token" type="password" placeholder="Paste your Canvas access token" {'value="********"' if connected else ''} required>
              <p style="font-size:12px;color:var(--text-muted);margin-top:6px;">
                Go to Canvas &rarr; Account &rarr; Settings &rarr; <b>+ New Access Token</b>
              </p>
            </div>
            <div style="display:flex;gap:10px;">
              <button type="submit" class="btn btn-primary" id="connect-btn">{'Update' if connected else 'Connect Canvas'}</button>
              {'<button type="button" onclick="disconnectCanvas()" class="btn btn-outline" style="color:var(--red);border-color:var(--red);">Disconnect</button>' if connected else ''}
            </div>
          </form>
        </div>
        <script>
        async function connectCanvas(e) {{
          e.preventDefault();
          var btn = document.getElementById('connect-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Connecting...';
          try {{
            var r = await fetch('/api/student/canvas/connect', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{canvas_url:document.getElementById('canvas-url').value,token:document.getElementById('canvas-token').value}})}});
            var d = await r.json();
            if (r.ok) {{ alert('Connected! Found ' + d.courses_found + ' courses.'); location.reload(); }}
            else {{ alert(d.error || 'Connection failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = 'Connect Canvas';
        }}
        async function disconnectCanvas() {{
          if (!confirm('Disconnect Canvas?')) return;
          await fetch('/api/student/canvas/disconnect', {{method:'POST'}});
          location.reload();
        }}
        </script>
        """, active_page="student_canvas")

    # ── Schedule settings (per-day availability) ────────────

    @app.route("/api/student/schedule", methods=["GET"])
    def student_get_schedule():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        settings = sdb.get_schedule_settings(_cid())
        # Build a full week map (0=Mon..6=Sun), unset days = full free day
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        result = []
        settings_map = {s["day_of_week"]: s for s in settings}
        for i in range(7):
            if i in settings_map:
                s = settings_map[i]
                result.append({
                    "day": i,
                    "day_name": days[i],
                    "hours": s["available_hours"],
                    "free": bool(s["is_free_day"]),
                })
            else:
                # Unset = full free day
                result.append({
                    "day": i,
                    "day_name": days[i],
                    "hours": 0,
                    "free": True,
                })
        return jsonify({"schedule": result})

    @app.route("/api/student/schedule", methods=["PUT"])
    def student_set_schedule():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        settings = data.get("schedule", [])
        if not isinstance(settings, list):
            return jsonify({"error": "schedule must be a list"}), 400
        cleaned = []
        for s in settings:
            day = int(s.get("day", -1))
            if day < 0 or day > 6:
                continue
            cleaned.append({
                "day": day,
                "hours": max(0, min(24, float(s.get("hours", 0)))),
                "free": bool(s.get("free", False)),
            })
        sdb.save_schedule_settings(_cid(), cleaned)
        return jsonify({"ok": True})

    # ── Course difficulty ───────────────────────────────────

    @app.route("/api/student/courses/<int:course_id>/difficulty", methods=["PUT"])
    def student_set_difficulty(course_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True)
        difficulty = int(data.get("difficulty", 3))
        sdb.set_course_difficulty(_cid(), course_id, difficulty)
        return jsonify({"ok": True, "difficulty": max(1, min(5, difficulty))})

    # ── Assignment-level completion ─────────────────────────

    @app.route("/api/student/assignments/toggle", methods=["POST"])
    def student_toggle_assignment():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        plan_date = data.get("date")
        session_index = data.get("session_index")
        completed = data.get("completed", True)
        if plan_date is None or session_index is None:
            return jsonify({"error": "date and session_index are required"}), 400
        sdb.toggle_assignment_complete(_cid(), plan_date, int(session_index), bool(completed))
        return jsonify({"ok": True})

    @app.route("/api/student/assignments/progress", methods=["GET"])
    def student_assignment_progress():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        plan_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
        progress = sdb.get_assignment_progress(_cid(), plan_date)
        return jsonify({"progress": progress, "date": plan_date})

    @app.route("/api/student/assignments/incomplete", methods=["GET"])
    def student_incomplete_assignments():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        today = datetime.now().strftime("%Y-%m-%d")
        incomplete = sdb.get_incomplete_assignments(_cid(), today)
        return jsonify({"incomplete": incomplete})

    # ── Schedule settings page ──────────────────────────────

    @app.route("/student/schedule")
    def student_schedule_page():
        if not _logged_in():
            return redirect(url_for("login"))
        settings = sdb.get_schedule_settings(_cid())
        settings_map = {s["day_of_week"]: s for s in settings}
        courses = sdb.get_courses(_cid())

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_rows = ""
        for i in range(7):
            s = settings_map.get(i, {})
            hours = s.get("available_hours", 0) if s else 0
            is_free = s.get("is_free_day", True) if not s else bool(s.get("is_free_day", 0))
            # If no settings saved at all, default: unset = free
            if not settings:
                is_free = True
                hours = 0
            checked = "checked" if is_free else ""
            day_rows += f"""
            <div class="schedule-row" style="display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:8px;">
              <span style="width:100px;font-weight:600;color:var(--text);">{days[i]}</span>
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;min-width:100px;">
                <input type="checkbox" class="free-day-check" data-day="{i}" {checked} onchange="toggleFreeDay({i},this.checked)">
                <span style="font-size:13px;color:var(--text-muted);">Free day</span>
              </label>
              <div id="hours-group-{i}" style="display:flex;align-items:center;gap:8px;{'opacity:0.3;pointer-events:none;' if is_free else ''}">
                <label style="font-size:12px;color:var(--text-muted);">Study hours:</label>
                <input type="number" id="hours-{i}" value="{hours}" min="0" max="24" step="0.5" class="edit-input" style="width:70px;" onchange="updateSchedule()">
              </div>
            </div>"""

        # Course difficulty section
        diff_rows = ""
        for c in courses:
            diff = c.get("difficulty", 3) or 3
            stars = ""
            for star in range(1, 6):
                active = "color:var(--primary);font-weight:700;" if star <= diff else "color:var(--border);"
                stars += f'<span class="diff-star" data-course="{c["id"]}" data-val="{star}" style="cursor:pointer;font-size:20px;{active}" onclick="setDiff({c["id"]},{star})">&#9733;</span>'
            diff_rows += f"""
            <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:8px;">
              <span style="font-weight:600;">{_esc(c['name'])}</span>
              <div style="display:flex;align-items:center;gap:4px;">
                {stars}
                <span id="diff-label-{c['id']}" style="font-size:12px;color:var(--text-muted);margin-left:8px;min-width:60px;">
                  {['','Very Easy','Easy','Medium','Hard','Very Hard'][diff]}
                </span>
              </div>
            </div>"""

        if not diff_rows:
            diff_rows = "<p style='color:var(--text-muted);text-align:center;padding:16px;'>No courses synced yet.</p>"

        return _s_render("Study Schedule", f"""
        <h1 style="margin-bottom:20px;">&#128197; Study Schedule & Difficulty</h1>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
          <div class="card">
            <div class="card-header">
              <h2>&#128337; Weekly Availability</h2>
              <p style="font-size:13px;color:var(--text-muted);margin:4px 0 0;">Set your available study hours for each day. Days left unconfigured are treated as free days.</p>
            </div>
            {day_rows}
            <button onclick="saveSchedule()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="save-sched-btn">Save Schedule</button>
          </div>

          <div class="card">
            <div class="card-header">
              <h2>&#9733; Course Difficulty</h2>
              <p style="font-size:13px;color:var(--text-muted);margin:4px 0 0;">Rate difficulty (1-5 stars). Harder courses get more study time in the AI plan.</p>
            </div>
            {diff_rows}
          </div>
        </div>

        <div class="card" style="margin-top:20px;">
          <div style="background:var(--bg);border-radius:var(--radius-sm);padding:14px 18px;">
            <p style="font-size:13px;color:var(--text-muted);margin:0;">
              &#128161; <b>How it works:</b> When you generate a study plan, the AI will respect your schedule &mdash; only assigning study time on days you're available, for the number of hours you set. Harder courses get proportionally more study time. If you don't configure a day, it's considered a free day (no study).
            </p>
          </div>
        </div>

        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        .diff-star:hover {{ transform:scale(1.2); }}
        </style>
        <script>
        function toggleFreeDay(day, free) {{
          var g = document.getElementById('hours-group-' + day);
          if (free) {{
            g.style.opacity = '0.3';
            g.style.pointerEvents = 'none';
            document.getElementById('hours-' + day).value = 0;
          }} else {{
            g.style.opacity = '1';
            g.style.pointerEvents = '';
            if (parseFloat(document.getElementById('hours-' + day).value) === 0) {{
              document.getElementById('hours-' + day).value = 4;
            }}
          }}
        }}

        async function saveSchedule() {{
          var btn = document.getElementById('save-sched-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Saving...';
          var schedule = [];
          for (var i = 0; i < 7; i++) {{
            var free = document.querySelector('.free-day-check[data-day="' + i + '"]').checked;
            var hours = parseFloat(document.getElementById('hours-' + i).value) || 0;
            schedule.push({{ day: i, hours: free ? 0 : hours, free: free }});
          }}
          try {{
            var r = await fetch('/api/student/schedule', {{
              method: 'PUT',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ schedule: schedule }})
            }});
            if (r.ok) {{
              btn.innerHTML = '&#10003; Saved!';
              setTimeout(function() {{ btn.disabled = false; btn.innerHTML = 'Save Schedule'; }}, 1500);
            }} else {{
              alert('Failed to save'); btn.disabled = false; btn.innerHTML = 'Save Schedule';
            }}
          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = 'Save Schedule'; }}
        }}

        var diffLabels = ['', 'Very Easy', 'Easy', 'Medium', 'Hard', 'Very Hard'];
        async function setDiff(courseId, val) {{
          // Update stars visually
          document.querySelectorAll('.diff-star[data-course="' + courseId + '"]').forEach(function(star) {{
            var sv = parseInt(star.dataset.val);
            star.style.color = sv <= val ? 'var(--primary)' : 'var(--border)';
            star.style.fontWeight = sv <= val ? '700' : '400';
          }});
          document.getElementById('diff-label-' + courseId).textContent = diffLabels[val];
          try {{
            await fetch('/api/student/courses/' + courseId + '/difficulty', {{
              method: 'PUT',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ difficulty: val }})
            }});
          }} catch(e) {{}}
        }}
        </script>
        """, active_page="student_schedule")

    def _esc(s):
        """HTML-escape a string."""
        import html as html_module
        return html_module.escape(str(s)) if s else ""

    log.info("Student routes registered.")
