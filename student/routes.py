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
    from student.analyzer import (analyze_course_material, generate_study_plan,
                                  generate_flashcards, generate_quiz, generate_notes,
                                  chat_with_tutor, notes_from_transcript,
                                  flashcards_from_transcript, detect_weak_topics)
    from student import db as sdb

    # ── helpers ─────────────────────────────────────────────
    def _esc(s) -> str:
        """HTML-escape a string."""
        import html as html_module
        return html_module.escape(str(s)) if s else ""

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

        # Gamification
        total_xp = sdb.get_total_xp(cid)
        level_name, level_floor, level_ceil = sdb.get_level(total_xp)
        xp_pct = min(100, int(100 * (total_xp - level_floor) / max(1, level_ceil - level_floor)))
        streak_days = sdb.get_streak_days(cid)
        # Auto-award login badge
        sdb.earn_badge(cid, "first_login")

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

        <!-- XP / Level Bar -->
        <a href="/student/achievements" style="text-decoration:none;display:block;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px 20px;margin-bottom:20px;transition:all 0.2s" onmouseover="this.style.borderColor='var(--primary)';this.style.boxShadow='0 4px 12px rgba(99,102,241,0.12)'" onmouseout="this.style.borderColor='var(--border)';this.style.boxShadow='none'">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.4em">🏆</span>
              <span style="font-weight:700;color:var(--text)">{_esc(level_name)}</span>
              <span style="color:var(--text-muted);font-size:13px">{total_xp} XP</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="color:#ea580c;font-weight:700;font-size:15px">🔥 {streak_days}</span>
              <span style="color:var(--text-muted);font-size:12px">day streak</span>
            </div>
          </div>
          <div style="background:var(--border);border-radius:6px;height:8px;overflow:hidden">
            <div style="background:linear-gradient(90deg,#6366f1,#8b5cf6);height:100%;width:{xp_pct}%;border-radius:6px;transition:width 0.5s"></div>
          </div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:4px;text-align:right">{total_xp - level_floor}/{level_ceil - level_floor} XP to next level</div>
        </a>

        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px;">
          <div class="stat-card stat-purple"><div class="num">{len(courses)}</div><div class="label">Courses</div></div>
          <div class="stat-card stat-red"><div class="num">{stats['upcoming_exams']}</div><div class="label">Upcoming Exams</div></div>
          <div class="stat-card stat-green"><div class="num">{stats['completion_pct']}%</div><div class="label">Plan Progress</div></div>
          <div class="stat-card stat-blue"><div class="num">{focus_stats['total_hours']}</div><div class="label">Hours Focused</div></div>
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
              <span style="font-size:13px;color:var(--text-muted);">Generated: {str(plan_row.get('generated_at','?'))[:16]}</span>
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
            document.getElementById('timer-label').textContent = '📖 Reading — click the big button for each page';
            // Persist floating widget
            localStorage.setItem('focus_float', JSON.stringify({{
              active:true, mode:'stopwatch', startAt:Date.now(), label:'📖 Reading'
            }}));
            showFloatWidget();
            timerInterval = setInterval(function() {{
              totalFocusSeconds++;
              updateDisplay();
              updateFloatFromLocal();
            }}, 1000);
          }} else {{
            document.getElementById('timer-label').textContent = isBreak ? '☕ Break time!' : '🔥 Focus!';
            // Persist floating widget
            var endAt = Date.now() + timeLeft * 1000;
            var label = isBreak ? '☕ Break' : '🔥 Focus';
            var nextPhase = null;
            if (currentMode === 'pomodoro') {{
              // Pre-compute next phase for auto-cycle
              if (!isBreak) {{
                var nextPomoCount = pomoCount + 1;
                var nextBreakMins = (nextPomoCount % 4 === 0)
                  ? parseInt(document.getElementById('pomo-long').value)
                  : parseInt(document.getElementById('pomo-break').value);
                nextPhase = {{
                  active:true, mode:'countdown',
                  endAt: endAt + nextBreakMins*60*1000,
                  label: (nextPomoCount % 4 === 0) ? '🎉 Long Break' : '☕ Break',
                  nextPhase: {{
                    active:true, mode:'countdown',
                    endAt: endAt + nextBreakMins*60*1000 + parseInt(document.getElementById('pomo-work').value)*60*1000,
                    label: '🔥 Focus',
                    nextPhase: null
                  }}
                }};
              }} else {{
                var workMins = parseInt(document.getElementById('pomo-work').value);
                nextPhase = {{
                  active:true, mode:'countdown',
                  endAt: endAt + workMins*60*1000,
                  label: '🔥 Focus',
                  nextPhase: null
                }};
              }}
            }}
            localStorage.setItem('focus_float', JSON.stringify({{
              active:true, mode:'countdown', endAt:endAt, label:label, nextPhase:nextPhase
            }}));
            showFloatWidget();
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

        function showFloatWidget() {{
          var el = document.getElementById('focus-float');
          if (el) el.style.display = 'block';
        }}
        function updateFloatFromLocal() {{
          // Update the float on this page too
          var el = document.getElementById('focus-float');
          if (!el) return;
          var ff = JSON.parse(localStorage.getItem('focus_float')||'null');
          if (!ff || !ff.active) {{ el.style.display='none'; return; }}
          el.style.display='block';
          var ffTime = document.getElementById('ff-time');
          var ffLabel = document.getElementById('ff-label');
          if (ff.mode==='countdown') {{
            var left = ff.endAt - Date.now();
            if (left<0) left=0;
            var m=Math.floor(left/60000), s=Math.floor((left%60000)/1000);
            ffTime.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
          }} else {{
            var elapsed = Math.floor((Date.now()-ff.startAt)/1000);
            var m=Math.floor(elapsed/60), s=elapsed%60;
            ffTime.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
          }}
          ffLabel.textContent = ff.label||'Focus';
        }}

        function pauseTimer() {{
          clearInterval(timerInterval);
          isRunning = false;
          document.getElementById('start-btn').style.display = '';
          document.getElementById('pause-btn').style.display = 'none';
          document.getElementById('timer-label').textContent = 'Paused';
          // Pause floating widget
          localStorage.removeItem('focus_float');
          var el = document.getElementById('focus-float');
          if (el) el.style.display = 'none';
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
          // Clear floating widget
          localStorage.removeItem('focus_float');
          var el = document.getElementById('focus-float');
          if (el) el.style.display = 'none';
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
                document.getElementById('timer-label').textContent = '🎉 Long break!';
              }} else {{
                timeLeft = parseInt(document.getElementById('pomo-break').value) * 60;
                document.getElementById('timer-label').textContent = '☕ Short break';
              }}
              isBreak = true;
            }} else {{
              timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;
              document.getElementById('timer-label').textContent = '🔥 Starting next session...';
              isBreak = false;
            }}
            totalTime = timeLeft;
            updateDisplay();
            // Auto-start next phase after 2 seconds
            setTimeout(function() {{
              startTimer();
            }}, 2000);
          }} else {{
            saveFocusSession();
            document.getElementById('timer-label').textContent = '✓ Session complete!';
            document.getElementById('start-btn').style.display = '';
            document.getElementById('pause-btn').style.display = 'none';
            localStorage.removeItem('focus_float');
            var el = document.getElementById('focus-float');
            if (el) el.style.display = 'none';
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

    # ── Flashcard API routes ────────────────────────────────

    @app.route("/api/student/flashcards/generate", methods=["POST"])
    @limiter.limit("5 per minute")
    def student_generate_flashcards():
        """Generate AI flashcards for a course."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        course_id = data.get("course_id")
        if not course_id:
            return jsonify({"error": "course_id required"}), 400
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Course not found"}), 404

        # Gather source material
        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})
        topics = data.get("topics", [])
        exam_id = data.get("exam_id")
        count = min(int(data.get("count", 15)), 30)

        source_text = ""
        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)
        for f in files[:3]:
            if f.get("extracted_text"):
                source_text += f["extracted_text"][:4000] + "\n\n"

        if not topics and analysis.get("weekly_schedule"):
            topics = []
            for w in analysis["weekly_schedule"]:
                topics.extend(w.get("topics", []))

        cards = generate_flashcards(
            course_name=course["name"],
            topics=topics or None,
            source_text=source_text,
            count=count,
        )
        if not cards:
            return jsonify({"error": "Failed to generate flashcards. Try again."}), 500

        title = data.get("title", f"Flashcards: {course['name']}")
        deck_id = sdb.create_flashcard_deck(_cid(), title, course_id=course_id, exam_id=exam_id)
        sdb.add_flashcards(deck_id, cards)

        return jsonify({"deck_id": deck_id, "card_count": len(cards)})

    @app.route("/api/student/flashcards/decks", methods=["GET"])
    def student_get_flashcard_decks():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course_id = request.args.get("course_id", type=int)
        decks = sdb.get_flashcard_decks(_cid(), course_id)
        return jsonify({"decks": [dict(d) for d in decks]})

    @app.route("/api/student/flashcards/decks/<int:deck_id>", methods=["GET"])
    def student_get_flashcard_deck(deck_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        deck = sdb.get_flashcard_deck(deck_id, _cid())
        if not deck:
            return jsonify({"error": "Not found"}), 404
        cards = sdb.get_flashcards(deck_id)
        return jsonify({"deck": dict(deck), "cards": [dict(c) for c in cards]})

    @app.route("/api/student/flashcards/decks/<int:deck_id>", methods=["DELETE"])
    def student_delete_flashcard_deck(deck_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_flashcard_deck(deck_id, _cid())
        return jsonify({"ok": True})

    @app.route("/api/student/flashcards/progress", methods=["POST"])
    def student_flashcard_progress():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        sdb.update_flashcard_progress(data["card_id"], data.get("correct", False))
        # Gamification — 1 XP per flashcard review
        sdb.award_xp(_cid(), "flashcard_review", 1, "Reviewed a flashcard")
        return jsonify({"ok": True})

    @app.route("/api/student/flashcards/<int:card_id>", methods=["PUT"])
    def student_update_flashcard(card_id):
        """Edit a single flashcard's front/back text."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        front = (data.get("front") or "").strip()
        back = (data.get("back") or "").strip()
        deck_id = data.get("deck_id")
        if not front or not back or not deck_id:
            return jsonify({"error": "front, back, and deck_id required"}), 400
        deck = sdb.get_flashcard_deck(deck_id, _cid())
        if not deck:
            return jsonify({"error": "Not found"}), 404
        sdb.update_flashcard(card_id, deck_id, front, back)
        return jsonify({"ok": True})

    @app.route("/api/student/flashcards/add", methods=["POST"])
    def student_add_flashcard():
        """Add a new flashcard to an existing deck."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        front = (data.get("front") or "").strip()
        back = (data.get("back") or "").strip()
        deck_id = data.get("deck_id")
        if not front or not back or not deck_id:
            return jsonify({"error": "front, back, and deck_id required"}), 400
        deck = sdb.get_flashcard_deck(deck_id, _cid())
        if not deck:
            return jsonify({"error": "Not found"}), 404
        card_id = sdb.add_flashcard(deck_id, front, back)
        return jsonify({"ok": True, "card_id": card_id})

    @app.route("/api/student/flashcards/<int:card_id>", methods=["DELETE"])
    def student_delete_flashcard(card_id):
        """Delete a single flashcard."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        deck_id = request.args.get("deck_id", type=int)
        if not deck_id:
            return jsonify({"error": "deck_id required"}), 400
        deck = sdb.get_flashcard_deck(deck_id, _cid())
        if not deck:
            return jsonify({"error": "Not found"}), 404
        sdb.delete_flashcard(card_id, deck_id)
        return jsonify({"ok": True})

    # ── Quiz API routes ─────────────────────────────────────

    @app.route("/api/student/quizzes/generate", methods=["POST"])
    @limiter.limit("5 per minute")
    def student_generate_quiz():
        """Generate AI quiz for a course."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        course_id = data.get("course_id")
        if not course_id:
            return jsonify({"error": "course_id required"}), 400
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Course not found"}), 404

        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})
        topics = data.get("topics", [])
        exam_id = data.get("exam_id")
        difficulty = data.get("difficulty", "medium")
        if difficulty not in ("easy", "medium", "hard"):
            difficulty = "medium"
        count = min(int(data.get("count", 10)), 20)

        source_text = ""
        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)
        for f in files[:3]:
            if f.get("extracted_text"):
                source_text += f["extracted_text"][:4000] + "\n\n"

        if not topics and analysis.get("weekly_schedule"):
            topics = []
            for w in analysis["weekly_schedule"]:
                topics.extend(w.get("topics", []))

        questions = generate_quiz(
            course_name=course["name"],
            topics=topics or None,
            source_text=source_text,
            difficulty=difficulty,
            count=count,
        )
        if not questions:
            return jsonify({"error": "Failed to generate quiz. Try again."}), 500

        title = data.get("title", f"Quiz: {course['name']} ({difficulty})")
        quiz_id = sdb.create_quiz(_cid(), title, difficulty, course_id=course_id, exam_id=exam_id)
        sdb.add_quiz_questions(quiz_id, questions)

        return jsonify({"quiz_id": quiz_id, "question_count": len(questions)})

    @app.route("/api/student/quizzes", methods=["GET"])
    def student_get_quizzes():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course_id = request.args.get("course_id", type=int)
        quizzes = sdb.get_quizzes(_cid(), course_id)
        return jsonify({"quizzes": [dict(q) for q in quizzes]})

    @app.route("/api/student/quizzes/<int:quiz_id>", methods=["GET"])
    def student_get_quiz(quiz_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        quiz = sdb.get_quiz(quiz_id, _cid())
        if not quiz:
            return jsonify({"error": "Not found"}), 404
        questions = sdb.get_quiz_questions(quiz_id)
        return jsonify({"quiz": dict(quiz), "questions": [dict(q) for q in questions]})

    @app.route("/api/student/quizzes/<int:quiz_id>/score", methods=["POST"])
    def student_submit_quiz_score(quiz_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        score = int(data.get("score", 0))
        sdb.update_quiz_score(quiz_id, score)
        # Gamification
        xp = max(5, score // 10)
        sdb.award_xp(_cid(), "quiz_complete", xp, f"Quiz score: {score}%")
        if score == 100:
            sdb.earn_badge(_cid(), "quiz_master")
        if not sdb.get_badges(_cid()) or not any(b["badge_key"] == "first_quiz" for b in sdb.get_badges(_cid())):
            sdb.earn_badge(_cid(), "first_quiz")
        return jsonify({"ok": True})

    @app.route("/api/student/quizzes/<int:quiz_id>", methods=["DELETE"])
    def student_delete_quiz(quiz_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_quiz(quiz_id, _cid())
        return jsonify({"ok": True})

    # ── Notes API routes ────────────────────────────────────

    @app.route("/api/student/notes/generate", methods=["POST"])
    @limiter.limit("5 per minute")
    def student_generate_notes():
        """Generate AI study notes for a course."""
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        course_id = data.get("course_id")
        if not course_id:
            return jsonify({"error": "course_id required"}), 400
        course = sdb.get_course(course_id)
        if not course or course["client_id"] != _cid():
            return jsonify({"error": "Course not found"}), 404

        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})
        topics = data.get("topics", [])

        source_text = ""
        files = sdb.get_course_files(_cid(), course_id)
        for f in files[:5]:
            if f.get("extracted_text"):
                source_text += f["extracted_text"][:4000] + "\n\n"

        if not topics and analysis.get("weekly_schedule"):
            topics = []
            for w in analysis["weekly_schedule"]:
                topics.extend(w.get("topics", []))

        result = generate_notes(
            course_name=course["name"],
            topics=topics or None,
            source_text=source_text,
        )
        if not result.get("content_html"):
            return jsonify({"error": "Failed to generate notes. Try again."}), 500

        note_id = sdb.create_note(_cid(), result["title"], result["content_html"],
                                  course_id=course_id)
        sdb.award_xp(_cid(), "notes_create", 10, f"Created: {result['title'][:40]}")
        return jsonify({"note_id": note_id, "title": result["title"]})

    @app.route("/api/student/notes", methods=["GET"])
    def student_get_notes():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        course_id = request.args.get("course_id", type=int)
        notes = sdb.get_notes(_cid(), course_id)
        return jsonify({"notes": [dict(n) for n in notes]})

    @app.route("/api/student/notes/<int:note_id>", methods=["GET"])
    def student_get_note(note_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        note = sdb.get_note(note_id, _cid())
        if not note:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(note))

    @app.route("/api/student/notes/<int:note_id>", methods=["PUT"])
    def student_update_note(note_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True)
        sdb.update_note(note_id, _cid(), data.get("content_html", ""))
        return jsonify({"ok": True})

    @app.route("/api/student/notes/<int:note_id>", methods=["DELETE"])
    def student_delete_note(note_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_note(note_id, _cid())
        return jsonify({"ok": True})

    # ── Flashcards Frontend Page ────────────────────────────

    @app.route("/student/flashcards")
    def student_flashcards_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())
        decks = sdb.get_flashcard_decks(_cid())

        course_options = '<option value="">Select a course...</option>'
        for c in courses:
            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

        decks_html = ""
        for d in decks:
            decks_html += f"""
            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/flashcards/{d['id']}'">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <h3 style="margin:0;font-size:16px;">{_esc(d.get('title','Untitled'))}</h3>
                  <span style="font-size:13px;color:var(--text-muted);">{_esc(d.get('course_name',''))} &middot; {d.get('card_count',0)} cards</span>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                  <span style="font-size:12px;color:var(--text-muted);">{str(d.get('created_at',''))[:10]}</span>
                  <button onclick="event.stopPropagation();deleteDeck({d['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>
                </div>
              </div>
            </div>"""
        if not decks_html:
            decks_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">
              <div style="font-size:48px;margin-bottom:12px;">&#127183;</div>
              <p>No flashcard decks yet. Generate your first set from a course!</p>
            </div>"""

        return _s_render("Flashcards", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;">&#127183; AI Flashcards</h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Smart spaced repetition &middot; Generated from your course materials</p>
          </div>
          <button onclick="document.getElementById('gen-form').style.display=document.getElementById('gen-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Flashcards</button>
        </div>

        <div id="gen-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">
          <h3 style="margin:0 0 14px;">Generate AI Flashcards</h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
            <div class="form-group">
              <label>Course</label>
              <select id="fc-course" class="edit-input" onchange="loadExams(this.value,'fc-exam')">{course_options}</select>
            </div>
            <div class="form-group">
              <label>Exam (optional)</label>
              <select id="fc-exam" class="edit-input"><option value="">All topics</option></select>
            </div>
            <div class="form-group">
              <label>Number of cards</label>
              <input type="number" id="fc-count" value="15" min="5" max="30" class="edit-input">
            </div>
            <div class="form-group">
              <label>Custom title (optional)</label>
              <input type="text" id="fc-title" class="edit-input" placeholder="Auto-generated if empty">
            </div>
          </div>
          <button onclick="genFlashcards()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="fc-gen-btn">&#10024; Generate</button>
        </div>

        {decks_html}

        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        </style>
        <script>
        async function loadExams(courseId, selectId) {{
          var sel = document.getElementById(selectId);
          sel.innerHTML = '<option value="">All topics</option>';
          if (!courseId) return;
          try {{
            var r = await fetch('/api/student/courses/' + courseId + '/exams');
            var d = await r.json();
            (d.exams || []).forEach(function(e) {{
              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';
            }});
          }} catch(e) {{}}
        }}
        async function genFlashcards() {{
          var courseId = document.getElementById('fc-course').value;
          if (!courseId) {{ alert('Select a course'); return; }}
          var btn = document.getElementById('fc-gen-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';
          try {{
            var r = await fetch('/api/student/flashcards/generate', {{
              method: 'POST', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{
                course_id: parseInt(courseId),
                exam_id: document.getElementById('fc-exam').value ? parseInt(document.getElementById('fc-exam').value) : null,
                count: parseInt(document.getElementById('fc-count').value),
                title: document.getElementById('fc-title').value || undefined
              }})
            }});
            var d = await r.json();
            if (r.ok) {{
              alert('Generated ' + d.card_count + ' flashcards!');
              window.location = '/student/flashcards/' + d.deck_id;
            }} else {{ alert(d.error || 'Generation failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#10024; Generate';
        }}
        async function deleteDeck(id) {{
          if (!confirm('Delete this flashcard deck?')) return;
          await fetch('/api/student/flashcards/decks/' + id, {{method:'DELETE'}});
          location.reload();
        }}
        </script>
        """, active_page="student_flashcards")

    @app.route("/student/flashcards/<int:deck_id>")
    def student_flashcard_study_page(deck_id):
        """Interactive flashcard study page with flip animation and edit mode."""
        if not _logged_in():
            return redirect(url_for("login"))
        deck = sdb.get_flashcard_deck(deck_id, _cid())
        if not deck:
            return redirect(url_for("student_flashcards_page"))
        cards = sdb.get_flashcards(deck_id)
        cards_json = json.dumps([{"id": c["id"], "front": c["front"], "back": c["back"],
                                  "times_seen": c.get("times_seen", 0),
                                  "times_correct": c.get("times_correct", 0)} for c in cards],
                                ensure_ascii=False)

        return _s_render(f"Study: {deck.get('title','')}", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
          <div>
            <a href="/student/flashcards" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Decks</a>
            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(deck.get('title',''))}</h1>
            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">{_esc(deck.get('course_name',''))} &middot; <span id="card-count-txt">{deck.get('card_count',0)}</span> cards</p>
          </div>
          <div style="display:flex;gap:8px;">
            <button onclick="switchMode('study')" class="btn btn-primary btn-sm" id="mode-study-btn">&#127183; Study</button>
            <button onclick="switchMode('edit')" class="btn btn-outline btn-sm" id="mode-edit-btn">&#9999;&#65039; Edit Cards</button>
          </div>
        </div>

        <!-- ===== STUDY MODE ===== -->
        <div id="study-mode">
          <div id="progress-txt" style="font-size:14px;color:var(--text-muted);text-align:right;margin-bottom:8px;">1 / {len(cards)}</div>
          <div style="background:var(--bg);border-radius:8px;height:8px;margin-bottom:24px;overflow:hidden;">
            <div id="fc-progress-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:{100/max(len(cards),1):.1f}%;transition:width 0.4s ease;border-radius:8px;"></div>
          </div>

          <div style="max-width:600px;margin:0 auto;">
            <div id="fc-card" onclick="flipCard()" style="
              min-height:250px;background:var(--card);border:2px solid var(--border);border-radius:16px;
              padding:40px 32px;text-align:center;cursor:pointer;display:flex;align-items:center;
              justify-content:center;flex-direction:column;user-select:none;
              transition:transform 0.5s ease,box-shadow 0.3s ease;
              box-shadow:0 4px 20px rgba(0,0,0,0.08);position:relative;
            ">
              <div id="fc-side-label" style="position:absolute;top:16px;left:20px;font-size:11px;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:1px;">Question</div>
              <div id="fc-content" style="font-size:20px;line-height:1.5;color:var(--text);"></div>
              <div style="position:absolute;bottom:16px;font-size:12px;color:var(--text-muted);">Click to flip</div>
            </div>

            <div style="display:flex;justify-content:center;gap:16px;margin-top:24px;">
              <button onclick="prevCard()" class="btn btn-outline" style="min-width:100px;">&larr; Prev</button>
              <button onclick="markCard(false)" class="btn" style="min-width:100px;background:#EF4444;color:#fff;border:none;">&#10007; Wrong</button>
              <button onclick="markCard(true)" class="btn" style="min-width:100px;background:#10B981;color:#fff;border:none;">&#10003; Got it</button>
              <button onclick="nextCard()" class="btn btn-outline" style="min-width:100px;">Next &rarr;</button>
            </div>

            <p style="text-align:center;font-size:11px;color:var(--text-muted);margin-top:12px;">
              <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">Space</kbd> flip
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">&larr;</kbd> prev
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">&rarr;</kbd> next
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">1</kbd> wrong
              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">2</kbd> got it
            </p>

            <div id="fc-summary" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:30px;text-align:center;margin-top:24px;">
              <div style="font-size:48px;margin-bottom:12px;">&#127881;</div>
              <h2 style="margin:0 0 8px;">Session Complete!</h2>
              <div id="fc-score" style="font-size:28px;font-weight:700;color:var(--primary);"></div>
              <div id="fc-score-detail" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>
              <button onclick="restartStudy()" class="btn btn-primary" style="margin-top:16px;">&#128260; Study Again</button>
            </div>
          </div>
        </div>

        <!-- ===== EDIT MODE ===== -->
        <div id="edit-mode" style="display:none;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <p style="color:var(--text-muted);font-size:14px;margin:0;">Click any card to edit. Changes save automatically.</p>
            <button onclick="addCard()" class="btn btn-primary btn-sm">&#10133; Add Card</button>
          </div>
          <div id="card-list"></div>
        </div>

        <style>
        .fc-edit-card {{ background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;margin-bottom:10px;transition:border-color 0.2s; }}
        .fc-edit-card:hover {{ border-color:var(--primary); }}
        .fc-edit-input {{ width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:14px;resize:vertical; }}
        .fc-edit-input:focus {{ border-color:var(--primary);outline:none; }}
        .fc-label {{ font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px; }}
        </style>
        <script>
        var cards = {cards_json};
        var deckId = {deck_id};
        var idx = 0, flipped = false, correct = 0, seen = 0;
        var currentMode = 'study';

        function switchMode(mode) {{
          currentMode = mode;
          document.getElementById('study-mode').style.display = mode === 'study' ? '' : 'none';
          document.getElementById('edit-mode').style.display = mode === 'edit' ? '' : 'none';
          document.getElementById('mode-study-btn').className = mode === 'study' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
          document.getElementById('mode-edit-btn').className = mode === 'edit' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
          if (mode === 'edit') renderCardList();
          if (mode === 'study') {{ idx = 0; correct = 0; seen = 0; document.getElementById('fc-card').style.display = 'flex'; document.getElementById('fc-summary').style.display = 'none'; renderCard(); }}
        }}

        // ── Study functions ──
        function renderCard() {{
          if (idx >= cards.length) {{ showSummary(); return; }}
          var c = cards[idx];
          document.getElementById('fc-content').textContent = c.front;
          document.getElementById('fc-side-label').textContent = 'Question';
          document.getElementById('fc-side-label').style.color = 'var(--primary)';
          document.getElementById('fc-card').style.borderColor = 'var(--border)';
          document.getElementById('progress-txt').textContent = (idx + 1) + ' / ' + cards.length;
          document.getElementById('fc-progress-bar').style.width = ((idx + 1) / cards.length * 100) + '%';
          flipped = false;
        }}

        function flipCard() {{
          if (idx >= cards.length) return;
          flipped = !flipped;
          var c = cards[idx];
          document.getElementById('fc-content').textContent = flipped ? c.back : c.front;
          document.getElementById('fc-side-label').textContent = flipped ? 'Answer' : 'Question';
          document.getElementById('fc-side-label').style.color = flipped ? '#10B981' : 'var(--primary)';
          document.getElementById('fc-card').style.borderColor = flipped ? '#10B981' : 'var(--border)';
          document.getElementById('fc-card').style.transform = 'scale(0.97)';
          setTimeout(function() {{ document.getElementById('fc-card').style.transform = 'scale(1)'; }}, 150);
        }}

        function markCard(isCorrect) {{
          if (idx >= cards.length) return;
          if (!flipped) flipCard();
          seen++;
          if (isCorrect) correct++;
          fetch('/api/student/flashcards/progress', {{
            method: 'POST', headers: {{'Content-Type':'application/json'}},
            body: JSON.stringify({{ card_id: cards[idx].id, correct: isCorrect }})
          }}).catch(function(){{}});
          idx++;
          renderCard();
        }}

        function nextCard() {{ if (idx < cards.length - 1) {{ idx++; renderCard(); }} }}
        function prevCard() {{ if (idx > 0) {{ idx--; renderCard(); }} }}

        function showSummary() {{
          document.getElementById('fc-card').style.display = 'none';
          document.getElementById('fc-summary').style.display = 'block';
          var pct = seen > 0 ? Math.round(correct / seen * 100) : 0;
          document.getElementById('fc-score').textContent = pct + '% correct';
          document.getElementById('fc-score-detail').textContent = correct + ' of ' + seen + ' cards';
        }}

        function restartStudy() {{
          idx = 0; correct = 0; seen = 0;
          document.getElementById('fc-card').style.display = 'flex';
          document.getElementById('fc-summary').style.display = 'none';
          renderCard();
        }}

        // ── Edit functions ──
        function renderCardList() {{
          var html = '';
          cards.forEach(function(c, i) {{
            html += '<div class="fc-edit-card" id="ec-' + c.id + '">'
              + '<div style="display:flex;justify-content:space-between;align-items:start;gap:12px;">'
              + '<div style="flex:1;">'
              + '<div class="fc-label">Front (Question)</div>'
              + '<textarea class="fc-edit-input" rows="2" data-id="' + c.id + '" data-side="front" onblur="saveCard(' + c.id + ')">' + escHtml(c.front) + '</textarea>'
              + '</div>'
              + '<div style="flex:1;">'
              + '<div class="fc-label">Back (Answer)</div>'
              + '<textarea class="fc-edit-input" rows="2" data-id="' + c.id + '" data-side="back" onblur="saveCard(' + c.id + ')">' + escHtml(c.back) + '</textarea>'
              + '</div>'
              + '<button onclick="removeCard(' + c.id + ',' + i + ')" style="background:none;border:none;color:#EF4444;cursor:pointer;font-size:16px;padding:4px;margin-top:16px;" title="Delete card">&#128465;</button>'
              + '</div></div>';
          }});
          if (!cards.length) html = '<div style="text-align:center;padding:40px;color:var(--text-muted);"><p>No cards yet. Click "Add Card" to create one.</p></div>';
          document.getElementById('card-list').innerHTML = html;
        }}

        function escHtml(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}

        var saveTimers = {{}};
        async function saveCard(cardId) {{
          var frontEl = document.querySelector('[data-id="' + cardId + '"][data-side="front"]');
          var backEl = document.querySelector('[data-id="' + cardId + '"][data-side="back"]');
          if (!frontEl || !backEl) return;
          var front = frontEl.value.trim(), back = backEl.value.trim();
          if (!front || !back) return;
          // Update local
          var c = cards.find(function(x) {{ return x.id === cardId; }});
          if (c) {{ c.front = front; c.back = back; }}
          // Save to server
          try {{
            var el = document.getElementById('ec-' + cardId);
            el.style.borderColor = '#F59E0B';
            await fetch('/api/student/flashcards/' + cardId, {{
              method: 'PUT', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ deck_id: deckId, front: front, back: back }})
            }});
            el.style.borderColor = '#10B981';
            setTimeout(function() {{ el.style.borderColor = 'var(--border)'; }}, 1000);
          }} catch(e) {{ }}
        }}

        async function addCard() {{
          try {{
            var r = await fetch('/api/student/flashcards/add', {{
              method: 'POST', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ deck_id: deckId, front: 'New question', back: 'Answer' }})
            }});
            var d = await r.json();
            if (d.ok) {{
              cards.push({{ id: d.card_id, front: 'New question', back: 'Answer', times_seen: 0, times_correct: 0 }});
              document.getElementById('card-count-txt').textContent = cards.length;
              renderCardList();
              // Scroll to new card
              var last = document.getElementById('card-list').lastElementChild;
              if (last) last.scrollIntoView({{ behavior: 'smooth' }});
              // Focus the front input
              var inputs = last.querySelectorAll('textarea');
              if (inputs.length) {{ inputs[0].focus(); inputs[0].select(); }}
            }}
          }} catch(e) {{ alert('Failed to add card'); }}
        }}

        async function removeCard(cardId, index) {{
          if (!confirm('Delete this card?')) return;
          try {{
            await fetch('/api/student/flashcards/' + cardId + '?deck_id=' + deckId, {{ method: 'DELETE' }});
            cards.splice(index, 1);
            document.getElementById('card-count-txt').textContent = cards.length;
            renderCardList();
          }} catch(e) {{ alert('Failed to delete'); }}
        }}

        document.addEventListener('keydown', function(e) {{
          if (currentMode !== 'study') return;
          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
          if (e.code === 'Space') {{ e.preventDefault(); flipCard(); }}
          if (e.code === 'ArrowLeft') {{ e.preventDefault(); prevCard(); }}
          if (e.code === 'ArrowRight') {{ e.preventDefault(); nextCard(); }}
          if (e.code === 'Digit1' || e.code === 'Numpad1') {{ e.preventDefault(); markCard(false); }}
          if (e.code === 'Digit2' || e.code === 'Numpad2') {{ e.preventDefault(); markCard(true); }}
        }});

        renderCard();
        </script>
        """, active_page="student_flashcards")

    # ── Quiz Frontend Page ──────────────────────────────────

    @app.route("/student/quizzes")
    def student_quizzes_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())
        quizzes = sdb.get_quizzes(_cid())

        course_options = '<option value="">Select a course...</option>'
        for c in courses:
            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

        quizzes_html = ""
        for q in quizzes:
            diff_color = {"easy": "#10B981", "medium": "#F59E0B", "hard": "#EF4444"}.get(q.get("difficulty", "medium"), "#94A3B8")
            score_txt = f"{q.get('best_score',0)}%" if q.get("attempts", 0) > 0 else "Not taken"
            quizzes_html += f"""
            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/quizzes/{q['id']}'">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <h3 style="margin:0;font-size:16px;">{_esc(q.get('title','Untitled'))}</h3>
                  <span style="font-size:13px;color:var(--text-muted);">{_esc(q.get('course_name',''))} &middot; {q.get('question_count',0)} questions</span>
                </div>
                <div style="display:flex;gap:10px;align-items:center;">
                  <span style="background:{diff_color}18;color:{diff_color};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">{q.get('difficulty','medium').upper()}</span>
                  <span style="font-size:13px;font-weight:600;color:var(--text);">{score_txt}</span>
                  <span style="font-size:12px;color:var(--text-muted);">{q.get('attempts',0)} attempts</span>
                  <button onclick="event.stopPropagation();deleteQuiz({q['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>
                </div>
              </div>
            </div>"""
        if not quizzes_html:
            quizzes_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">
              <div style="font-size:48px;margin-bottom:12px;">&#128221;</div>
              <p>No quizzes yet. Generate your first practice quiz from a course!</p>
            </div>"""

        return _s_render("Quizzes", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;">&#128221; Practice Quizzes</h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Unlimited AI-generated questions &middot; Adjustable difficulty</p>
          </div>
          <button onclick="document.getElementById('qz-form').style.display=document.getElementById('qz-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Quiz</button>
        </div>

        <div id="qz-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">
          <h3 style="margin:0 0 14px;">Generate AI Quiz</h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
            <div class="form-group">
              <label>Course</label>
              <select id="qz-course" class="edit-input" onchange="loadExams(this.value,'qz-exam')">{course_options}</select>
            </div>
            <div class="form-group">
              <label>Exam (optional)</label>
              <select id="qz-exam" class="edit-input"><option value="">All topics</option></select>
            </div>
            <div class="form-group">
              <label>Difficulty</label>
              <select id="qz-diff" class="edit-input">
                <option value="easy">&#127793; Easy &mdash; Basic recall</option>
                <option value="medium" selected>&#128170; Medium &mdash; Exam-level</option>
                <option value="hard">&#128293; Hard &mdash; Challenge</option>
              </select>
            </div>
            <div class="form-group">
              <label>Number of questions</label>
              <input type="number" id="qz-count" value="10" min="5" max="20" class="edit-input">
            </div>
          </div>
          <button onclick="genQuiz()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="qz-gen-btn">&#10024; Generate</button>
        </div>

        {quizzes_html}

        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        </style>
        <script>
        async function loadExams(courseId, selectId) {{
          var sel = document.getElementById(selectId);
          sel.innerHTML = '<option value="">All topics</option>';
          if (!courseId) return;
          try {{
            var r = await fetch('/api/student/courses/' + courseId + '/exams');
            var d = await r.json();
            (d.exams || []).forEach(function(e) {{
              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';
            }});
          }} catch(e) {{}}
        }}
        async function genQuiz() {{
          var courseId = document.getElementById('qz-course').value;
          if (!courseId) {{ alert('Select a course'); return; }}
          var btn = document.getElementById('qz-gen-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';
          try {{
            var r = await fetch('/api/student/quizzes/generate', {{
              method: 'POST', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{
                course_id: parseInt(courseId),
                exam_id: document.getElementById('qz-exam').value ? parseInt(document.getElementById('qz-exam').value) : null,
                difficulty: document.getElementById('qz-diff').value,
                count: parseInt(document.getElementById('qz-count').value)
              }})
            }});
            var d = await r.json();
            if (r.ok) {{
              alert('Generated ' + d.question_count + ' questions!');
              window.location = '/student/quizzes/' + d.quiz_id;
            }} else {{ alert(d.error || 'Generation failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#10024; Generate';
        }}
        async function deleteQuiz(id) {{
          if (!confirm('Delete this quiz?')) return;
          await fetch('/api/student/quizzes/' + id, {{method:'DELETE'}});
          location.reload();
        }}
        </script>
        """, active_page="student_quizzes")

    @app.route("/student/quizzes/<int:quiz_id>")
    def student_quiz_take_page(quiz_id):
        """Interactive quiz-taking page."""
        if not _logged_in():
            return redirect(url_for("login"))
        quiz = sdb.get_quiz(quiz_id, _cid())
        if not quiz:
            return redirect(url_for("student_quizzes_page"))
        questions = sdb.get_quiz_questions(quiz_id)
        questions_json = json.dumps([{
            "id": q["id"], "question": q["question"],
            "option_a": q.get("option_a", ""), "option_b": q.get("option_b", ""),
            "option_c": q.get("option_c", ""), "option_d": q.get("option_d", ""),
            "correct": q["correct"], "explanation": q.get("explanation", "")
        } for q in questions], ensure_ascii=False)

        diff_color = {"easy": "#10B981", "medium": "#F59E0B", "hard": "#EF4444"}.get(quiz.get("difficulty", "medium"), "#94A3B8")

        return _s_render(f"Quiz: {quiz.get('title','')}", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
          <div>
            <a href="/student/quizzes" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Quizzes</a>
            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(quiz.get('title',''))}</h1>
            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">
              {_esc(quiz.get('course_name',''))} &middot;
              <span style="color:{diff_color};font-weight:600;">{quiz.get('difficulty','').upper()}</span> &middot;
              {quiz.get('question_count',0)} questions
            </p>
          </div>
          <div id="qz-progress-txt" style="font-size:14px;color:var(--text-muted);">Question 1 of {len(questions)}</div>
        </div>

        <!-- Progress bar -->
        <div style="background:var(--bg);border-radius:8px;height:8px;margin-bottom:24px;overflow:hidden;">
          <div id="qz-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.4s ease;border-radius:8px;"></div>
        </div>

        <div style="max-width:700px;margin:0 auto;">
          <!-- Question card -->
          <div id="qz-card" class="card" style="padding:30px;">
            <div id="qz-question" style="font-size:18px;font-weight:600;margin-bottom:20px;line-height:1.5;"></div>
            <div id="qz-options"></div>
            <div id="qz-explanation" style="display:none;margin-top:16px;padding:14px;border-radius:var(--radius-sm);font-size:14px;line-height:1.5;"></div>
            <div style="display:flex;justify-content:flex-end;margin-top:20px;">
              <button id="qz-next-btn" onclick="nextQuestion()" class="btn btn-primary" style="display:none;">Next &rarr;</button>
            </div>
          </div>

          <!-- Summary (hidden until done) -->
          <div id="qz-summary" style="display:none;" class="card">
            <div style="text-align:center;padding:30px;">
              <div style="font-size:48px;margin-bottom:12px;" id="qz-emoji">&#127881;</div>
              <h2 style="margin:0 0 8px;">Quiz Complete!</h2>
              <div id="qz-final-score" style="font-size:40px;font-weight:800;color:var(--primary);"></div>
              <div id="qz-final-detail" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>
              <div style="display:flex;gap:12px;justify-content:center;margin-top:24px;">
                <button onclick="restartQuiz()" class="btn btn-primary">&#128260; Retake Quiz</button>
                <a href="/student/quizzes" class="btn btn-outline">Back to Quizzes</a>
              </div>
            </div>
          </div>
        </div>

        <style>
        .qz-option {{
          display:block;width:100%;text-align:left;padding:14px 18px;margin-bottom:10px;
          background:var(--bg);border:2px solid var(--border);border-radius:12px;
          cursor:pointer;font-size:15px;color:var(--text);transition:all 0.2s ease;
        }}
        .qz-option:hover {{ border-color:var(--primary);background:var(--card); }}
        .qz-option.selected {{ border-color:var(--primary);background:var(--primary-light,#EDE9FE); }}
        .qz-option.correct {{ border-color:#10B981;background:#D1FAE5;color:#065F46; }}
        .qz-option.wrong {{ border-color:#EF4444;background:#FEE2E2;color:#991B1B; }}
        .qz-option.disabled {{ pointer-events:none;opacity:0.7; }}
        </style>

        <script>
        var questions = {questions_json};
        var qIdx = 0, score = 0, answered = false;

        function renderQuestion() {{
          if (qIdx >= questions.length) {{ showResults(); return; }}
          var q = questions[qIdx];
          answered = false;
          document.getElementById('qz-question').textContent = 'Q' + (qIdx + 1) + '. ' + q.question;
          document.getElementById('qz-progress-txt').textContent = 'Question ' + (qIdx + 1) + ' of ' + questions.length;
          document.getElementById('qz-bar').style.width = (qIdx / questions.length * 100) + '%';
          document.getElementById('qz-explanation').style.display = 'none';
          document.getElementById('qz-next-btn').style.display = 'none';
          var opts = document.getElementById('qz-options');
          opts.innerHTML = '';
          ['a','b','c','d'].forEach(function(key) {{
            var btn = document.createElement('button');
            btn.className = 'qz-option';
            btn.textContent = key.toUpperCase() + '. ' + q['option_' + key];
            btn.dataset.key = key;
            btn.onclick = function() {{ selectAnswer(key); }};
            opts.appendChild(btn);
          }});
        }}

        function selectAnswer(key) {{
          if (answered) return;
          answered = true;
          var q = questions[qIdx];
          var isCorrect = key === q.correct;
          if (isCorrect) score++;

          document.querySelectorAll('.qz-option').forEach(function(btn) {{
            btn.classList.add('disabled');
            if (btn.dataset.key === q.correct) btn.classList.add('correct');
            if (btn.dataset.key === key && !isCorrect) btn.classList.add('wrong');
          }});

          var exp = document.getElementById('qz-explanation');
          exp.style.display = 'block';
          exp.style.background = isCorrect ? '#D1FAE5' : '#FEE2E2';
          exp.style.color = isCorrect ? '#065F46' : '#991B1B';
          exp.innerHTML = (isCorrect ? '&#10003; Correct! ' : '&#10007; Incorrect. ') + q.explanation;

          document.getElementById('qz-next-btn').style.display = '';
          document.getElementById('qz-next-btn').textContent = qIdx === questions.length - 1 ? 'See Results' : 'Next \\u2192';
        }}

        function nextQuestion() {{
          qIdx++;
          renderQuestion();
        }}

        function showResults() {{
          document.getElementById('qz-card').style.display = 'none';
          document.getElementById('qz-summary').style.display = 'block';
          var pct = Math.round(score / questions.length * 100);
          document.getElementById('qz-final-score').textContent = pct + '%';
          document.getElementById('qz-final-detail').textContent = score + ' of ' + questions.length + ' correct';
          document.getElementById('qz-bar').style.width = '100%';
          document.getElementById('qz-emoji').innerHTML = pct >= 90 ? '&#127942;' : pct >= 70 ? '&#127881;' : pct >= 50 ? '&#128170;' : '&#128218;';
          fetch('/api/student/quizzes/{quiz_id}/score', {{
            method: 'POST', headers: {{'Content-Type':'application/json'}},
            body: JSON.stringify({{ score: pct }})
          }}).catch(function(){{}});
        }}

        function restartQuiz() {{
          qIdx = 0; score = 0;
          document.getElementById('qz-card').style.display = '';
          document.getElementById('qz-summary').style.display = 'none';
          renderQuestion();
        }}

        document.addEventListener('keydown', function(e) {{
          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
          if (!answered) {{
            if (e.code === 'KeyA' || e.code === 'Digit1') {{ e.preventDefault(); selectAnswer('a'); }}
            if (e.code === 'KeyB' || e.code === 'Digit2') {{ e.preventDefault(); selectAnswer('b'); }}
            if (e.code === 'KeyC' || e.code === 'Digit3') {{ e.preventDefault(); selectAnswer('c'); }}
            if (e.code === 'KeyD' || e.code === 'Digit4') {{ e.preventDefault(); selectAnswer('d'); }}
          }} else {{
            if (e.code === 'Enter' || e.code === 'Space') {{ e.preventDefault(); nextQuestion(); }}
          }}
        }});

        renderQuestion();
        </script>
        """, active_page="student_quizzes")

    # ── Notes Frontend Page ─────────────────────────────────

    @app.route("/student/notes")
    def student_notes_page():
        if not _logged_in():
            return redirect(url_for("login"))
        courses = sdb.get_courses(_cid())
        notes = sdb.get_notes(_cid())

        course_options = '<option value="">Select a course...</option>'
        for c in courses:
            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

        notes_html = ""
        for n in notes:
            notes_html += f"""
            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/notes/{n['id']}'">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <h3 style="margin:0;font-size:16px;">{_esc(n.get('title','Untitled'))}</h3>
                  <span style="font-size:13px;color:var(--text-muted);">{_esc(n.get('course_name',''))} &middot; {_esc(n.get('source_type','ai'))} &middot; {str(n.get('created_at',''))[:10]}</span>
                </div>
                <button onclick="event.stopPropagation();deleteNote({n['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>
              </div>
            </div>"""
        if not notes_html:
            notes_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">
              <div style="font-size:48px;margin-bottom:12px;">&#128221;</div>
              <p>No notes yet. Generate AI study notes from your course materials!</p>
            </div>"""

        return _s_render("Notes", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;">&#128221; AI Study Notes</h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Comprehensive notes generated from your course materials</p>
          </div>
          <button onclick="document.getElementById('note-form').style.display=document.getElementById('note-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Notes</button>
        </div>

        <div id="note-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">
          <h3 style="margin:0 0 14px;">Generate AI Study Notes</h3>
          <div class="form-group">
            <label>Course</label>
            <select id="note-course" class="edit-input">{course_options}</select>
          </div>
          <p style="font-size:12px;color:var(--text-muted);margin:8px 0 12px;">The AI will analyze your uploaded files and syllabus to create comprehensive study notes.</p>
          <button onclick="genNotes()" class="btn btn-primary btn-sm" id="note-gen-btn">&#10024; Generate</button>
        </div>

        {notes_html}

        <style>
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}
        .edit-input:focus {{ border-color:var(--primary); outline:none; }}
        </style>
        <script>
        async function genNotes() {{
          var courseId = document.getElementById('note-course').value;
          if (!courseId) {{ alert('Select a course'); return; }}
          var btn = document.getElementById('note-gen-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating (may take ~15s)...';
          try {{
            var r = await fetch('/api/student/notes/generate', {{
              method: 'POST', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ course_id: parseInt(courseId) }})
            }});
            var d = await r.json();
            if (r.ok) {{
              window.location = '/student/notes/' + d.note_id;
            }} else {{ alert(d.error || 'Generation failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#10024; Generate';
        }}
        async function deleteNote(id) {{
          if (!confirm('Delete this note?')) return;
          await fetch('/api/student/notes/' + id, {{method:'DELETE'}});
          location.reload();
        }}
        </script>
        """, active_page="student_notes")

    @app.route("/student/notes/<int:note_id>")
    def student_note_view_page(note_id):
        """View and edit a study note."""
        if not _logged_in():
            return redirect(url_for("login"))
        note = sdb.get_note(note_id, _cid())
        if not note:
            return redirect(url_for("student_notes_page"))

        return _s_render(f"Note: {note.get('title','')}", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
          <div>
            <a href="/student/notes" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Notes</a>
            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(note.get('title',''))}</h1>
            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">Created {str(note.get('created_at',''))[:10]} &middot; {_esc(note.get('source_type','ai'))}</p>
          </div>
          <div style="display:flex;gap:8px;">
            <button onclick="toggleEdit()" class="btn btn-outline btn-sm" id="edit-toggle">&#9999;&#65039; Edit</button>
            <button onclick="printNote()" class="btn btn-outline btn-sm">&#128424; Print</button>
          </div>
        </div>

        <div class="card" style="padding:30px;">
          <div id="note-view">{note.get('content_html','')}</div>
          <div id="note-edit" style="display:none;">
            <!-- Formatting toolbar -->
            <div id="editor-toolbar" style="display:flex;gap:4px;flex-wrap:wrap;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-bottom:none;border-radius:var(--radius-sm) var(--radius-sm) 0 0;">
              <button type="button" onclick="fmt('bold')" class="tb" title="Bold"><b>B</b></button>
              <button type="button" onclick="fmt('italic')" class="tb" title="Italic"><i>I</i></button>
              <button type="button" onclick="fmt('underline')" class="tb" title="Underline"><u>U</u></button>
              <span style="width:1px;background:var(--border);margin:0 4px;"></span>
              <button type="button" onclick="fmt('formatBlock','<h2>')" class="tb" title="Heading 2">H2</button>
              <button type="button" onclick="fmt('formatBlock','<h3>')" class="tb" title="Heading 3">H3</button>
              <button type="button" onclick="fmt('formatBlock','<p>')" class="tb" title="Paragraph">P</button>
              <span style="width:1px;background:var(--border);margin:0 4px;"></span>
              <button type="button" onclick="fmt('insertUnorderedList')" class="tb" title="Bullet list">&#8226; List</button>
              <button type="button" onclick="fmt('insertOrderedList')" class="tb" title="Numbered list">1. List</button>
              <span style="width:1px;background:var(--border);margin:0 4px;"></span>
              <button type="button" onclick="fmt('removeFormat')" class="tb" title="Clear formatting">&#10005; Clear</button>
            </div>
            <!-- WYSIWYG editor -->
            <div id="note-editor" contenteditable="true" style="
              min-height:350px;padding:20px;border:1px solid var(--border);border-radius:0 0 var(--radius-sm) var(--radius-sm);
              background:var(--card);color:var(--text);font-size:15px;line-height:1.7;outline:none;overflow-y:auto;
            ">{note.get('content_html','')}</div>
            <div style="display:flex;gap:8px;margin-top:12px;">
              <button onclick="saveNote()" class="btn btn-primary btn-sm" id="save-note-btn">&#128190; Save</button>
              <button onclick="toggleEdit()" class="btn btn-outline btn-sm">Cancel</button>
            </div>
          </div>
        </div>

        <style>
        .tb {{ background:var(--card);border:1px solid var(--border);border-radius:4px;padding:4px 10px;cursor:pointer;font-size:13px;color:var(--text);transition:all 0.15s; }}
        .tb:hover {{ background:var(--primary-light);color:var(--primary); }}
        #note-view h2, #note-editor h2 {{ color:var(--text);margin:24px 0 12px;font-size:20px;border-bottom:2px solid var(--border);padding-bottom:6px; }}
        #note-view h3, #note-editor h3 {{ color:var(--text);margin:18px 0 8px;font-size:17px; }}
        #note-view p, #note-editor p {{ color:var(--text-secondary);line-height:1.7;margin:8px 0; }}
        #note-view ul, #note-view ol, #note-editor ul, #note-editor ol {{ color:var(--text-secondary);line-height:1.8;padding-left:24px; }}
        #note-view strong, #note-editor strong {{ color:var(--text); }}
        @media print {{
          body * {{ visibility: hidden; }}
          #note-view, #note-view * {{ visibility: visible; }}
          #note-view {{ position: absolute; left: 0; top: 0; width: 100%; }}
        }}
        </style>
        <script>
        var editing = false;
        function fmt(cmd, val) {{
          document.execCommand(cmd, false, val || null);
          document.getElementById('note-editor').focus();
        }}
        function toggleEdit() {{
          editing = !editing;
          document.getElementById('note-view').style.display = editing ? 'none' : '';
          document.getElementById('note-edit').style.display = editing ? '' : 'none';
          document.getElementById('edit-toggle').innerHTML = editing ? '&#128065; View' : '&#9999;&#65039; Edit';
          if (editing) {{
            document.getElementById('note-editor').innerHTML = document.getElementById('note-view').innerHTML;
            document.getElementById('note-editor').focus();
          }}
        }}
        async function saveNote() {{
          var btn = document.getElementById('save-note-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Saving...';
          var html = document.getElementById('note-editor').innerHTML;
          try {{
            var r = await fetch('/api/student/notes/{note_id}', {{
              method: 'PUT', headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify({{ content_html: html }})
            }});
            if (r.ok) {{
              document.getElementById('note-view').innerHTML = html;
              toggleEdit();
            }} else {{ alert('Save failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#128190; Save';
        }}
        function printNote() {{ window.print(); }}
        </script>
        """, active_page="student_notes")

    # ================================================================
    #  FEATURE 1 — AI Study Chat (Tutor)
    # ================================================================

    @app.route("/api/student/chat", methods=["POST"])
    @limiter.limit("30 per minute")
    def student_chat_send():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        cid = _cid()
        data = request.get_json(force=True)
        msg = (data.get("message") or "").strip()
        course_id = data.get("course_id")
        if not msg:
            return jsonify(error="Empty message"), 400

        # Build context from course notes/syllabus
        context_text = ""
        course_name = "General"
        if course_id:
            course = sdb.get_course(int(course_id), cid)
            if course:
                course_name = course.get("name", "General")
                notes = sdb.get_notes(cid, int(course_id))
                for n in notes[:3]:
                    context_text += (n.get("content_html") or "") + "\n"
                analysis = course.get("analysis_json")
                if analysis:
                    import json as _json
                    try:
                        a = _json.loads(analysis) if isinstance(analysis, str) else analysis
                        if a.get("topics"):
                            context_text += "\nTopics: " + ", ".join(
                                t.get("name", "") if isinstance(t, dict) else str(t)
                                for t in a["topics"][:20])
                    except Exception:
                        pass

        # Get recent history
        history_rows = sdb.get_chat_history(cid, int(course_id) if course_id else None, limit=20)
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]

        # Save user message
        sdb.add_chat_message(cid, "user", msg, int(course_id) if course_id else None)

        # Get AI reply
        reply = chat_with_tutor(course_name, msg, history, context_text)

        # Save assistant reply
        sdb.add_chat_message(cid, "assistant", reply, int(course_id) if course_id else None)

        # Award XP
        sdb.award_xp(cid, "chat_question", 2, f"Asked: {msg[:50]}")
        # Check badge
        total_msgs = len(sdb.get_chat_history(cid, limit=1000))
        if total_msgs >= 10:
            sdb.earn_badge(cid, "chat_curious")

        return jsonify(reply=reply)

    @app.route("/api/student/chat/clear", methods=["POST"])
    def student_chat_clear():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        data = request.get_json(force=True)
        sdb.clear_chat_history(_cid(), data.get("course_id"))
        return jsonify(ok=True)

    @app.route("/student/chat")
    def student_chat_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        courses = sdb.get_courses(cid)
        course_opts = "".join(
            f'<option value="{c["id"]}">{_esc(c["name"])}</option>' for c in courses
        )
        return _s_render("AI Tutor", f"""
        <div style="max-width:800px;margin:0 auto">
          <h2 style="display:flex;align-items:center;gap:8px">
            <span style="font-size:1.5em">🤖</span> AI Study Tutor
          </h2>
          <p style="color:var(--text-muted);margin-bottom:16px">
            Ask anything about your courses — your AI tutor uses your own notes and course material to help.
          </p>
          <div style="margin-bottom:12px">
            <select id="chat-course" onchange="loadHistory()"
              style="padding:8px 12px;border:1px solid var(--border);border-radius:8px;width:100%;
                     background:var(--bg);color:var(--text)">
              <option value="">General (no specific course)</option>
              {course_opts}
            </select>
          </div>
          <div id="chat-box" style="border:1px solid var(--border);border-radius:12px;height:450px;
               overflow-y:auto;padding:16px;background:var(--bg);margin-bottom:12px"></div>
          <div style="display:flex;gap:8px">
            <input id="chat-input" type="text" placeholder="Ask your tutor..."
              style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:8px;font-size:15px;
                     background:var(--card);color:var(--text)"
              onkeydown="if(event.key==='Enter')sendMsg()">
            <button onclick="sendMsg()"
              style="padding:10px 20px;background:var(--primary);color:#fff;border:none;border-radius:8px;
                     font-weight:600;cursor:pointer">Send</button>
            <button onclick="clearChat()" title="Clear history"
              style="padding:10px 12px;background:#ef4444;color:#fff;border:none;border-radius:8px;
                     cursor:pointer">🗑</button>
          </div>
        </div>
        <script>
        var chatBox = document.getElementById('chat-box');
        function addBubble(role, text) {{
          var d = document.createElement('div');
          d.style.cssText = 'margin-bottom:10px;display:flex;' + (role==='user'?'justify-content:flex-end':'');
          var b = document.createElement('div');
          b.style.cssText = 'max-width:75%;padding:10px 14px;border-radius:12px;line-height:1.5;white-space:pre-wrap;' +
            (role==='user'
              ? 'background:var(--primary);color:#fff;border-bottom-right-radius:4px'
              : 'background:var(--card);color:var(--text);border:1px solid var(--border);border-bottom-left-radius:4px');
          b.textContent = text;
          d.appendChild(b);
          chatBox.appendChild(d);
          chatBox.scrollTop = chatBox.scrollHeight;
        }}
        async function loadHistory() {{
          chatBox.innerHTML = '';
          var cid = document.getElementById('chat-course').value;
          // Show welcome message
          addBubble('assistant', 'Hi! I\\'m your AI study tutor. Ask me anything about your course material! 📚');
        }}
        loadHistory();
        async function sendMsg() {{
          var inp = document.getElementById('chat-input');
          var msg = inp.value.trim();
          if (!msg) return;
          inp.value = '';
          addBubble('user', msg);
          addBubble('assistant', '💭 Thinking...');
          try {{
            var r = await fetch('/api/student/chat', {{
              method:'POST', headers:{{'Content-Type':'application/json'}},
              body: JSON.stringify({{message: msg, course_id: document.getElementById('chat-course').value || null}})
            }});
            chatBox.removeChild(chatBox.lastChild);
            var d = await r.json();
            if (r.ok) {{ addBubble('assistant', d.reply); }}
            else {{ addBubble('assistant', '❌ ' + (d.error || 'Error')); }}
          }} catch(e) {{ chatBox.removeChild(chatBox.lastChild); addBubble('assistant', '❌ Network error'); }}
        }}
        async function clearChat() {{
          if (!confirm('Clear chat history?')) return;
          await fetch('/api/student/chat/clear', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{course_id: document.getElementById('chat-course').value || null}})
          }});
          loadHistory();
        }}
        </script>
        """, active_page="student_chat")

    # ================================================================
    #  FEATURE 3 — Weak Topic Detector
    # ================================================================

    @app.route("/api/student/weak-topics")
    def student_weak_topics_api():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        cid = _cid()
        fc_data = sdb.get_flashcard_accuracy(cid)
        quiz_data = sdb.get_quiz_scores(cid)
        result = detect_weak_topics(fc_data, quiz_data)
        return jsonify(**result)

    @app.route("/student/weak-topics")
    def student_weak_topics_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        fc_data = sdb.get_flashcard_accuracy(cid)
        quiz_data = sdb.get_quiz_scores(cid)
        result = detect_weak_topics(fc_data, quiz_data)

        weak_html = ""
        for t in result.get("weak_topics", []):
            score = t.get("score", 0)
            color = "#ef4444" if score < 50 else "#f59e0b" if score < 75 else "#22c55e"
            weak_html += f"""
            <div style="padding:14px 18px;border-left:4px solid {color};background:var(--card);
                        border:1px solid var(--border);border-left:4px solid {color};
                        border-radius:var(--radius-sm);margin-bottom:10px;transition:transform 0.15s"
                 onmouseover="this.style.transform='translateX(4px)'" onmouseout="this.style.transform=''">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <strong style="color:var(--text)">{_esc(t.get('topic',''))}</strong>
                <span style="background:{color};color:#fff;padding:3px 12px;border-radius:12px;
                             font-size:13px;font-weight:700">{score}%</span>
              </div>
              <div style="font-size:13px;color:var(--text-muted);margin-top:4px">
                {_esc(t.get('course',''))} — {_esc(t.get('source',''))}
              </div>
            </div>"""

        recs = result.get("recommendations_html", "")

        no_data_html = '<div style="text-align:center;padding:40px;color:var(--text-muted);background:var(--card);border:1px solid var(--border);border-radius:var(--radius)"><div style="font-size:48px;margin-bottom:12px">📊</div><p>Not enough data yet</p><p style="font-size:13px;margin-top:4px">Complete some quizzes and review flashcards to see your weak spots.</p></div>'
        recs_section = ""
        if recs:
            recs_section = '<div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:22px"><h3 style="color:var(--text);margin-bottom:12px">💡 Recommendations</h3>' + recs + '</div>'

        return _s_render("Weak Topics", f"""
        <div style="max-width:800px;margin:0 auto">
          <h2 style="margin-bottom:4px"><span style="font-size:1.3em">🎯</span> Weak Topic Detector</h2>
          <p style="color:var(--text-muted);margin-bottom:24px">
            Based on your flashcard accuracy and quiz scores, here are the topics that need more attention.
          </p>
          <div style="margin-bottom:28px">
            {weak_html if weak_html else no_data_html}
          </div>
          {recs_section}
        </div>
        """, active_page="student_weak_topics")

    # ================================================================
    #  FEATURE 4 — Gamification (XP / Badges / Streaks / Level)
    # ================================================================

    @app.route("/api/student/gamification")
    def student_gamification_api():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        cid = _cid()
        total_xp = sdb.get_total_xp(cid)
        level_name, level_floor, level_ceil = sdb.get_level(total_xp)
        streak = sdb.get_streak_days(cid)
        badges = sdb.get_badges(cid)
        history = sdb.get_xp_history(cid)

        # Auto-award streak badges
        if streak >= 3:
            sdb.earn_badge(cid, "streak_3")
        if streak >= 7:
            sdb.earn_badge(cid, "streak_7")
        if streak >= 30:
            sdb.earn_badge(cid, "streak_30")
        if total_xp >= 100:
            sdb.earn_badge(cid, "xp_100")
        if total_xp >= 500:
            sdb.earn_badge(cid, "xp_500")
        if total_xp >= 1000:
            sdb.earn_badge(cid, "xp_1000")

        return jsonify(
            total_xp=total_xp,
            level=level_name,
            level_floor=level_floor,
            level_ceil=level_ceil,
            streak=streak,
            badges=[{"badge_key": b["badge_key"], "emoji": b.get("emoji","🏅"),
                     "name": b.get("name",""), "desc": b.get("desc","")} for b in badges],
            history=[{"action": h["action"], "xp": h["xp"], "detail": h.get("detail",""),
                      "date": str(h.get("created_at",""))[:10]} for h in history],
        )

    @app.route("/student/achievements")
    def student_achievements_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        total_xp = sdb.get_total_xp(cid)
        level_name, level_floor, level_ceil = sdb.get_level(total_xp)
        streak = sdb.get_streak_days(cid)
        badges = sdb.get_badges(cid)
        history = sdb.get_xp_history(cid, limit=20)

        # Auto-award streak/XP badges
        for key, threshold in [("streak_3", 3), ("streak_7", 7), ("streak_30", 30)]:
            if streak >= threshold:
                sdb.earn_badge(cid, key)
        for key, threshold in [("xp_100", 100), ("xp_500", 500), ("xp_1000", 1000)]:
            if total_xp >= threshold:
                sdb.earn_badge(cid, key)
        badges = sdb.get_badges(cid)

        pct = min(100, int(100 * (total_xp - level_floor) / max(1, level_ceil - level_floor)))

        badges_html = ""
        for b in badges:
            badges_html += f"""
            <div style="text-align:center;padding:16px 12px;background:var(--card);border-radius:var(--radius);
                        border:1px solid var(--border);min-width:110px;flex:1;max-width:160px;
                        transition:transform 0.2s,box-shadow 0.2s;cursor:default"
                 onmouseover="this.style.transform='translateY(-3px)';this.style.boxShadow='var(--shadow-md)'"
                 onmouseout="this.style.transform='';this.style.boxShadow=''">
              <div style="font-size:2.2em;margin-bottom:4px">{b.get('emoji','🏅')}</div>
              <div style="font-weight:700;font-size:13px;color:var(--text)">{_esc(b.get('name',''))}</div>
              <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{_esc(b.get('desc',''))}</div>
            </div>"""

        # All possible badges
        all_badges_html = ""
        for key, info in sdb.BADGE_DEFS.items():
            earned = any(b["badge_key"] == key for b in badges)
            opacity = "1" if earned else "0.25"
            border = "var(--primary)" if earned else "var(--border)"
            all_badges_html += f"""
            <div style="text-align:center;padding:10px 8px;opacity:{opacity};min-width:90px;flex:1;max-width:120px;
                        border:1px solid {border};border-radius:var(--radius-sm);background:var(--card);
                        transition:opacity 0.2s" title="{_esc(info['desc'])}">
              <div style="font-size:1.6em">{info['emoji']}</div>
              <div style="font-size:11px;font-weight:600;color:var(--text);margin-top:2px">{_esc(info['name'])}</div>
            </div>"""

        history_html = ""
        for h in history:
            history_html += f"""
            <div style="display:flex;justify-content:space-between;padding:6px 0;
                        border-bottom:1px solid var(--border);font-size:14px;color:var(--text)">
              <span>{_esc(h.get('detail','') or h['action'])}</span>
              <span style="color:#22c55e;font-weight:600">+{h['xp']} XP</span>
            </div>"""

        return _s_render("Achievements", f"""
        <div style="max-width:800px;margin:0 auto">
          <h2 style="margin-bottom:20px"><span style="font-size:1.3em">🏆</span> Achievements & Progress</h2>

          <!-- Level & XP Bar -->
          <div style="background:linear-gradient(135deg,#6366f1 0%,#8b5cf6 50%,#a855f7 100%);color:#fff;
                      border-radius:var(--radius);padding:28px 32px;margin-bottom:24px;text-align:center;
                      box-shadow:0 8px 32px rgba(99,102,241,0.3);position:relative;overflow:hidden">
            <div style="position:absolute;top:-20px;right:-20px;font-size:120px;opacity:0.08">🏆</div>
            <div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1.5px;font-weight:600">Level</div>
            <div style="font-size:2.2em;font-weight:800;margin:6px 0;letter-spacing:-1px">{_esc(level_name)}</div>
            <div style="font-size:1.4em;font-weight:600;opacity:0.95">{total_xp} XP</div>
            <div style="background:rgba(255,255,255,0.2);border-radius:8px;height:10px;margin:14px auto;max-width:320px">
              <div style="background:#fff;border-radius:8px;height:10px;width:{pct}%;transition:width 0.6s ease;box-shadow:0 0 12px rgba(255,255,255,0.3)"></div>
            </div>
            <div style="font-size:13px;opacity:0.75">{total_xp - level_floor} / {level_ceil - level_floor} XP to next level</div>
          </div>

          <!-- Streak & Badges Count -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:28px">
            <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
                        padding:20px;text-align:center;transition:transform 0.2s"
                 onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform=''">
              <div style="font-size:2.2em">🔥</div>
              <div style="font-size:2.4em;font-weight:800;color:#ea580c;margin:4px 0">{streak}</div>
              <div style="font-size:13px;color:var(--text-muted);font-weight:500">Day Streak</div>
            </div>
            <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
                        padding:20px;text-align:center;transition:transform 0.2s"
                 onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform=''">
              <div style="font-size:2.2em">🏅</div>
              <div style="font-size:2.4em;font-weight:800;color:#16a34a;margin:4px 0">{len(badges)}</div>
              <div style="font-size:13px;color:var(--text-muted);font-weight:500">Badges Earned</div>
            </div>
          </div>

          <!-- Earned Badges -->
          <h3 style="color:var(--text);margin-bottom:12px">🏅 Your Badges</h3>
          <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:28px">
            {badges_html if badges_html else '<p style="color:var(--text-muted)">No badges yet — keep studying!</p>'}
          </div>

          <!-- All Badges -->
          <h3 style="color:var(--text);margin-bottom:12px">🎖 All Badges</h3>
          <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:28px">
            {all_badges_html}
          </div>

          <!-- XP History -->
          <h3 style="color:var(--text);margin-bottom:12px">📊 Recent Activity</h3>
          <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px">
            {history_html if history_html else '<p style="color:var(--text-muted)">No activity yet.</p>'}
          </div>
        </div>
        """, active_page="student_achievements")

    # ================================================================
    #  FEATURE 5 — Email Preferences (for daily study email)
    # ================================================================

    @app.route("/api/student/email-prefs", methods=["GET", "POST"])
    def student_email_prefs_api():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        cid = _cid()
        if request.method == "GET":
            prefs = sdb.get_email_prefs(cid)
            if not prefs:
                return jsonify(daily_email=True, email_hour=7, timezone="America/Mexico_City")
            return jsonify(
                daily_email=bool(prefs.get("daily_email")),
                email_hour=prefs.get("email_hour", 7),
                timezone=prefs.get("timezone", "America/Mexico_City"),
            )
        data = request.get_json(force=True)
        sdb.upsert_email_prefs(
            cid,
            daily_email=data.get("daily_email", True),
            email_hour=data.get("email_hour", 7),
            timezone=data.get("timezone", "America/Mexico_City"),
        )
        return jsonify(ok=True)

    @app.route("/student/settings")
    def student_settings_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        prefs = sdb.get_email_prefs(cid) or {}
        de = prefs.get("daily_email", 1)
        hour = prefs.get("email_hour", 7)
        tz = prefs.get("timezone", "America/Mexico_City")
        canvas_tok = sdb.get_canvas_token(cid)
        canvas_status = "✅ Connected" if canvas_tok else "❌ Not connected"

        return _s_render("Settings", f"""
        <div style="max-width:700px;margin:0 auto">
          <h2>⚙️ Settings</h2>

          <!-- Quick links to pages not in nav -->
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:24px">
            <a href="/student/canvas-settings" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🔗</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Canvas</div>
              <div style="font-size:11px;color:#64748b">{canvas_status}</div>
            </a>
            <a href="/student/focus" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🎯</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Focus Mode</div>
              <div style="font-size:11px;color:#64748b">Pomodoro timer</div>
            </a>
            <a href="/student/youtube" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🎬</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">YouTube</div>
              <div style="font-size:11px;color:#64748b">Video → Notes</div>
            </a>
            <a href="/student/weak-topics" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🎯</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Weak Topics</div>
              <div style="font-size:11px;color:#64748b">Focus areas</div>
            </a>
            <a href="/student/gpa" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🎓</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">GPA</div>
              <div style="font-size:11px;color:#64748b">Grade calculator</div>
            </a>
            <a href="/student/exams" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">📋</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Exams</div>
              <div style="font-size:11px;color:#64748b">Upcoming dates</div>
            </a>
            <a href="/student/schedule" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">🕐</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Schedule</div>
              <div style="font-size:11px;color:#64748b">Times & difficulty</div>
            </a>
            <a href="/mail-hub" style="text-decoration:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center">
              <div style="font-size:24px">📧</div>
              <div style="font-weight:600;font-size:13px;color:#1e293b">Mail Hub</div>
              <div style="font-size:11px;color:#64748b">Email config</div>
            </a>
          </div>

          <!-- Email prefs -->
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:20px">
            <h3>📧 Daily Study Email</h3>
            <p style="color:#64748b;font-size:14px;margin-bottom:12px">
              Get a morning email with your study plan, upcoming exams, and weak topics to review.
            </p>
            <label style="display:flex;align-items:center;gap:8px;margin-bottom:12px;cursor:pointer">
              <input type="checkbox" id="pref-daily" {'checked' if de else ''}>
              <span>Enable daily study email</span>
            </label>
            <div style="display:flex;gap:12px;margin-bottom:12px">
              <div>
                <label style="font-size:13px;color:#64748b">Send at (hour)</label>
                <select id="pref-hour" style="padding:6px;border:1px solid #d1d5db;border-radius:6px;width:80px">
                  {"".join(f'<option value="{h}" {"selected" if h==hour else ""}>{h:02d}:00</option>' for h in range(5,23))}
                </select>
              </div>
              <div>
                <label style="font-size:13px;color:#64748b">Timezone</label>
                <select id="pref-tz" style="padding:6px;border:1px solid #d1d5db;border-radius:6px">
                  <option value="America/Mexico_City" {"selected" if tz=="America/Mexico_City" else ""}>Mexico City</option>
                  <option value="America/New_York" {"selected" if tz=="America/New_York" else ""}>New York</option>
                  <option value="America/Chicago" {"selected" if tz=="America/Chicago" else ""}>Chicago</option>
                  <option value="America/Los_Angeles" {"selected" if tz=="America/Los_Angeles" else ""}>Los Angeles</option>
                  <option value="America/Bogota" {"selected" if tz=="America/Bogota" else ""}>Bogotá</option>
                  <option value="America/Sao_Paulo" {"selected" if tz=="America/Sao_Paulo" else ""}>São Paulo</option>
                  <option value="Europe/Madrid" {"selected" if tz=="Europe/Madrid" else ""}>Madrid</option>
                  <option value="Europe/London" {"selected" if tz=="Europe/London" else ""}>London</option>
                </select>
              </div>
            </div>
            <button onclick="savePrefs()"
              style="padding:8px 20px;background:#6366f1;color:#fff;border:none;border-radius:8px;
                     font-weight:600;cursor:pointer">Save Preferences</button>
          </div>
        </div>
        <script>
        async function savePrefs() {{
          var r = await fetch('/api/student/email-prefs', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{
              daily_email: document.getElementById('pref-daily').checked,
              email_hour: parseInt(document.getElementById('pref-hour').value),
              timezone: document.getElementById('pref-tz').value
            }})
          }});
          if (r.ok) alert('Saved!'); else alert('Error saving.');
        }}
        </script>
        """, active_page="student_settings")

    log.info("Student routes registered.")
