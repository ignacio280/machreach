"""

Student routes — all /student/* API endpoints and pages for MachReach Student.



This module exposes a `register_student_routes(app, csrf, limiter)` function

that app.py calls to mount everything.

"""

from __future__ import annotations



import json

import logging

import re

from datetime import datetime



from flask import jsonify, redirect, request, session, url_for, render_template_string, send_file

from markupsafe import Markup



log = logging.getLogger(__name__)





def register_student_routes(app, csrf, limiter):

    """Register all student routes on the Flask app."""



    # Import here to avoid circular imports at module level

    from student.canvas import CanvasClient, extract_text_from_pdf, extract_text_from_docx

    from student.analyzer import (analyze_course_material, generate_study_plan,

                                  generate_flashcards, generate_quiz, generate_notes,

                                  chat_with_tutor, notes_from_transcript,

                                  flashcards_from_transcript, detect_weak_topics,

                                  generate_practice_problems,

                                  analyze_essay, generate_cram_plan)

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



    @app.route("/api/student/courses/manual", methods=["POST"])

    def student_create_manual_course():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        name = (data.get("name") or "").strip()

        if not name:

            return jsonify({"error": "name required"}), 400

        code = (data.get("code") or "").strip()

        term = (data.get("term") or "").strip()

        try:

            course_id = sdb.create_manual_course(_cid(), name, code, term)

            return jsonify({"ok": True, "course_id": course_id})

        except Exception as e:

            log.exception("create_manual_course failed")

            return jsonify({"error": str(e)}), 500



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



        # Read file content (limit 50MB)

        content = f.read(50 * 1024 * 1024 + 1)

        if len(content) > 50 * 1024 * 1024:

            return jsonify({"error": "File too large (max 50MB)"}), 400



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



    @app.route("/api/student/files/<int:file_id>", methods=["GET"])

    def student_get_file_text(file_id):

        """Get the extracted text of an uploaded file for preview."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        cid = _cid()

        from student.db import get_db, _fetchone

        with get_db() as db:

            f = _fetchone(db, "SELECT id, original_name, file_type, extracted_text, uploaded_at FROM student_course_files WHERE id = %s AND client_id = %s",

                          (file_id, cid),

                          "SELECT id, original_name, file_type, extracted_text, uploaded_at FROM student_course_files WHERE id = ? AND client_id = ?")

        if not f:

            return jsonify({"error": "Not found"}), 404

        return jsonify({"id": f["id"], "name": f["original_name"], "type": f["file_type"], "text": f.get("extracted_text", ""), "uploaded_at": str(f.get("uploaded_at", ""))})



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

                # Override stale analysis exams with live student_exams (canonical source)

                # AND attach the FULL extracted text of every uploaded file per exam,

                # so the AI plans coverage of the entire study material — not just the topic list.

                try:

                    live_exams = sdb.get_course_exams(c["id"]) or []

                    merged_exams = []

                    for ex in live_exams:

                        topics = json.loads(ex["topics_json"]) if isinstance(ex.get("topics_json"), str) else (ex.get("topics_json") or [])

                        # Pull every file uploaded for THIS exam and concatenate its text

                        materials_text = ""

                        material_files = []

                        try:

                            ex_files = sdb.get_course_files(client_id, c["id"], exam_id=ex["id"]) or []

                            chunks = []

                            for f in ex_files:

                                txt = (f.get("extracted_text") or "").strip()

                                if not txt:

                                    continue

                                material_files.append(f.get("original_name", ""))

                                # Cap each individual file at 60k chars to avoid one huge file starving others

                                chunks.append(f"=== FILE: {f.get('original_name','')} ===\n{txt[:60000]}")

                            # Cap the per-exam total at 120k chars (≈ 30k tokens worth)

                            materials_text = ("\n\n".join(chunks))[:120000]

                        except Exception as _fe:

                            print(f"[plan] failed to load files for exam {ex.get('id')}: {_fe}")

                        merged_exams.append({

                            "id": ex.get("id"),

                            "name": ex.get("name", "Exam"),

                            "date": ex.get("exam_date"),

                            "weight_pct": ex.get("weight_pct", 0),

                            "topics": topics,

                            "material_files": material_files,

                            "materials": materials_text,

                        })

                    analysis["exams"] = merged_exams

                except Exception as _e:

                    print(f"[plan] failed to merge live exams for course {c['id']}: {_e}")

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

                # Pull Google Calendar events so the AI plans around real commitments
                try:
                    from outreach import gcal as _gcal
                    gcal_summary = _gcal.events_summary_for_ai(client_id, days_ahead=14)
                except Exception:
                    gcal_summary = ""

                plan = generate_study_plan(

                    courses_data, preferences,

                    schedule_settings=schedule_list or None,

                    course_difficulties=course_difficulties or None,

                    incomplete_assignments=incomplete or None,

                    calendar_events=gcal_summary,

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

    @csrf.exempt

    def student_save_focus():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        cid = _cid()

        mode = data.get("mode", "pomodoro")

        minutes = int(data.get("minutes", 0))

        pages = int(data.get("pages", 0))

        course_name = data.get("course_name", "")



        sdb.save_focus_session(cid, mode=mode, minutes=minutes, pages=pages, course_name=course_name)



        # Award XP based on time, difficulty, and mode

        if minutes > 0:

            # Look up course difficulty (1-5)

            difficulty = 3  # default medium

            if course_name:

                courses = sdb.get_courses(cid)

                for c in courses:

                    if c["name"] == course_name:

                        difficulty = c.get("difficulty", 3) or 3

                        break

            # XP formula: 1 XP per 5 min, multiplied by difficulty and mode

            diff_mult = {1: 1.0, 2: 1.25, 3: 1.5, 4: 1.75, 5: 2.0}.get(difficulty, 1.5)

            mode_mult = 1.2 if mode == "pomodoro" else 1.0

            xp = max(2, int(minutes / 5 * diff_mult * mode_mult + 0.5))

            detail = f"{mode.title()} {minutes}min"

            if course_name:

                detail += f" — {course_name}"

            sdb.award_xp(cid, "focus_session", xp, detail)

            _focus_xp_awarded = xp

        else:

            _focus_xp_awarded = 0



        _page_xp_awarded = 0

        if pages > 0:

            page_xp = max(1, pages)

            sdb.award_xp(cid, "pages_read", page_xp, f"Read {pages} pages")

            _page_xp_awarded = page_xp



        # Auto-award focus badges

        stats = sdb.get_focus_stats(cid)

        total_hours = stats.get("total_hours", 0)

        if isinstance(total_hours, str):

            total_hours = float(total_hours.replace("h", "").replace(",", ""))

        else:

            total_hours = float(total_hours)

        if total_hours >= 1:

            sdb.earn_badge(cid, "focus_1h")

        if total_hours >= 10:

            sdb.earn_badge(cid, "focus_10h")

        if total_hours >= 50:

            sdb.earn_badge(cid, "focus_50h")

        if total_hours >= 100:

            sdb.earn_badge(cid, "focus_100h")

        total_pages = stats.get("total_pages", 0)

        if isinstance(total_pages, str):

            total_pages = int(total_pages)

        if total_pages >= 100:

            sdb.earn_badge(cid, "page_100")

        if total_pages >= 500:

            sdb.earn_badge(cid, "page_500")

        if total_pages >= 1000:

            sdb.earn_badge(cid, "page_1000")



        # Early bird / Night owl badges based on current time

        from datetime import datetime

        hour_now = datetime.now().hour

        if minutes > 0:

            if hour_now < 7:

                sdb.earn_badge(cid, "early_bird")

            if hour_now >= 23:

                sdb.earn_badge(cid, "night_owl")



        # Build promotion payload based on total XP change during this call.

        # We compute after all XP additions above (focus + pages) are done.

        _new_total = sdb.get_total_xp(cid)

        _rank_after = sdb.get_study_rank(_new_total)

        _xp_delta = _focus_xp_awarded + _page_xp_awarded

        _rank_before = sdb.get_study_rank(max(0, _new_total - _xp_delta))

        promotion = None

        if _rank_after["index"] > _rank_before["index"]:

            promotion = {

                "promoted": True,

                "tier_up": _rank_after["tier"] != _rank_before["tier"],

                "reached_elite": _rank_after["division"] == "" and _rank_before["division"] != "",

                "rank_after": _rank_after,

            }



        return jsonify({"ok": True, "stats": stats, "promotion": promotion})



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

                prio_map = {

                    "high": ("var(--red)", "var(--red-light)", "#991B1B"),

                    "medium": ("var(--yellow)", "var(--yellow-light)", "#92400E"),

                    "low": ("var(--green)", "var(--green-light)", "var(--green-dark)"),

                }

                pc, pc_bg, pc_fg = prio_map.get(s.get("priority", "medium"), ("var(--text-muted)", "var(--border-light)", "var(--text-secondary)"))

                checked = "checked" if today_assignment_progress.get(idx, False) else ""

                strike_cls = "strike-done" if today_assignment_progress.get(idx, False) else ""

                today_sessions_html += f"""

                <div class="{strike_cls}" style="background:var(--card);border:1px solid var(--border);border-left:4px solid {pc};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;transition:all 0.25s;" id="dash-session-{idx}">

                  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">

                    <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">

                      <input type="checkbox" {checked} onchange="toggleDashAssignment({idx},this.checked)"

                        style="width:18px;height:18px;cursor:pointer;accent-color:var(--primary);flex-shrink:0;">

                      <span style="font-weight:700;color:var(--text);">{_esc(s.get('course',''))}</span>

                      <span style="color:var(--text-muted);font-size:13px;">{s.get('hours',0)}h &middot; {s.get('type','study')}</span>

                    </div>

                    <span style="background:{pc_bg};color:{pc_fg};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700;">{s.get('priority','').upper()}</span>

                  </div>

                  <div style="color:var(--text-secondary);font-size:14px;margin-top:6px;margin-left:28px;">{_esc(s.get('topic',''))}</div>

                  {"<div style='color:var(--text-muted);font-size:12px;margin-top:4px;margin-left:28px;font-style:italic;'>" + _esc(s.get('reason','')) + "</div>" if s.get('reason') else ""}

                </div>"""

        else:

            today_sessions_html = """

            <div class="empty">

              <div class="empty-icon">&#128218;</div>

              <h3>No study sessions yet</h3>

              <p>Sync your courses and generate a plan to get a personalized study schedule for today.</p>

              <div style="margin-top:16px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">

                <a href="/student/canvas-settings" class="btn btn-outline btn-sm">&#128279; Connect Canvas</a>

                <button onclick="generatePlan()" class="btn btn-primary btn-sm">&#129302; Generate Plan</button>

              </div>

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

            urg_map = {

                "red": ("var(--red)", "var(--red-light)", "#991B1B"),

                "yellow": ("var(--yellow)", "var(--yellow-light)", "#92400E"),

                "green": ("var(--green)", "var(--green-light)", "var(--green-dark)"),

            }

            if days_until is not None and days_until <= 7:

                u_border, u_bg, u_fg = urg_map["red"]

            elif days_until is not None and days_until <= 14:

                u_border, u_bg, u_fg = urg_map["yellow"]

            else:

                u_border, u_bg, u_fg = urg_map["green"]

            topics = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []

            topics_str = ", ".join(topics[:3]) + ("..." if len(topics) > 3 else "") if topics else "No topics listed"

            exams_html += f"""

            <div style="background:var(--card);border:1px solid var(--border);border-left:3px solid {u_border};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;transition:all 0.25s;" onmouseover="this.style.transform='translateX(3px)';this.style.boxShadow='var(--shadow-md)'" onmouseout="this.style.transform='';this.style.boxShadow=''">

              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">

                <div>

                  <span style="font-weight:700;">{_esc(e.get('course_name',''))}</span>

                  <span style="color:var(--text-muted);font-size:13px;margin-left:6px;">&middot; {_esc(e.get('name','Exam'))}</span>

                </div>

                <span style="background:{u_bg};color:{u_fg};padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;">

                  {str(days_until) + 'd' if days_until is not None else '?'} left

                </span>

              </div>

              <div style="color:var(--text-muted);font-size:13px;margin-top:4px;">

                {e.get('exam_date', 'TBD')} &middot; {e.get('weight_pct', 0)}% of grade &middot; {_esc(topics_str)}

              </div>

            </div>"""



        if not exams_html:

            exams_html = """<div class="empty"><div class="empty-icon">&#128221;</div><h3>No upcoming exams</h3><p>Sync your courses to automatically detect exam dates from Canvas.</p></div>"""



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

            <h1 style="margin:0;font-size:28px;">&#127891; <span class="gradient-text">MachReach</span> Student</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">AI-powered study planner &middot; Canvas integration</p>

          </div>

          <div style="display:flex;gap:10px;">

            <button onclick="syncCourses()" class="btn btn-primary btn-sm" id="sync-btn">&#128260; Sync Canvas</button>

            <button onclick="generatePlan()" class="btn btn-outline btn-sm" id="plan-btn">&#129302; Generate Plan</button>

          </div>

        </div>



        <!-- Feature Explorer — always discoverable, collapsible -->

        <div id="feature-explorer" class="card" style="margin-bottom:20px;padding:0;position:relative;overflow:hidden;border:1px solid var(--border);">

          <div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(900px 180px at -10% -30%,rgba(139,92,246,.12),transparent 60%),radial-gradient(700px 160px at 120% 120%,rgba(99,102,241,.10),transparent 60%);pointer-events:none;"></div>

          <div style="position:relative;z-index:1;display:flex;align-items:center;justify-content:space-between;padding:14px 18px;cursor:pointer;" onclick="toggleExplorer()">

            <div style="display:flex;align-items:center;gap:10px;">

              <span style="font-size:20px;">&#129504;</span>

              <div>

                <div style="font-weight:700;font-size:15px;">What can I do here?</div>

                <div style="font-size:12px;color:var(--text-muted);">A visual map of every feature &mdash; click any card to jump there.</div>

              </div>

            </div>

            <span id="fx-caret" style="color:var(--text-muted);font-size:13px;">Show &#9660;</span>

          </div>

          <div id="fx-body" style="position:relative;z-index:1;display:none;padding:4px 18px 18px;">

            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;">

              <a href="/student/courses" class="fx-tile"><span class="fx-ico">&#128218;</span><b>Courses</b><span>Sync Canvas or add courses by hand. Upload PDFs so the AI knows your material.</span></a>

              <a href="/student/plan" class="fx-tile"><span class="fx-ico">&#128197;</span><b>AI Study Plan</b><span>Personalized daily plan based on every exam, weight, and deadline.</span></a>

              <a href="/student/focus" class="fx-tile"><span class="fx-ico">&#127919;</span><b>Focus Mode</b><span>Pomodoro, page-count, and custom timers. Earn XP every session.</span></a>

              <a href="/student/flashcards" class="fx-tile"><span class="fx-ico">&#127183;</span><b>Flashcards</b><span>AI-generated, spaced-repetition review scheduled by difficulty.</span></a>

              <a href="/student/quizzes" class="fx-tile"><span class="fx-ico">&#128221;</span><b>Practice Quizzes</b><span>Up to 100 questions with timer, per-topic analytics, and action plan.</span></a>

              <a href="/student/notes" class="fx-tile"><span class="fx-ico">&#128196;</span><b>AI Notes</b><span>Drop any PDF/DOCX and get organized study notes in seconds.</span></a>

              <a href="/student/chat" class="fx-tile"><span class="fx-ico">&#129302;</span><b>AI Tutor</b><span>Grounded in your uploads only &mdash; zero hallucinations.</span></a>

              <a href="/student/essay" class="fx-tile"><span class="fx-ico">&#9999;&#65039;</span><b>Essay Assistant</b><span>Honest feedback on thesis, structure, flow, grammar.</span></a>

              <a href="/student/panic" class="fx-tile"><span class="fx-ico">&#128680;</span><b>Panic Mode</b><span>Exam tomorrow? Get a minute-by-minute cram plan.</span></a>

              <a href="/student/exchange" class="fx-tile"><span class="fx-ico">&#128257;</span><b>Study Exchange</b><span>Share & fork notes from other students. Earn XP when yours get used.</span></a>

              <a href="/student/leaderboard" class="fx-tile"><span class="fx-ico">&#127942;</span><b>Leaderboards</b><span>Global & university ranks, plus fair-play private groups where everyone starts at 0.</span></a>

              <a href="/student/schedule" class="fx-tile"><span class="fx-ico">&#128197;</span><b>Weekly Schedule</b><span>Drag-and-drop classes and study blocks. Auto-saves.</span></a>

              <a href="/student/exams" class="fx-tile"><span class="fx-ico">&#128203;</span><b>Exams Dashboard</b><span>Every upcoming exam, sorted by urgency.</span></a>

              <a href="/student/weak-topics" class="fx-tile"><span class="fx-ico">&#127919;</span><b>Weak Topics</b><span>AI spots what you struggle with from your quiz scores.</span></a>

              <a href="/student/gpa" class="fx-tile"><span class="fx-ico">&#128200;</span><b>GPA Calculator</b><span>Track GPA and forecast what grades you need.</span></a>

              <a href="/student/achievements" class="fx-tile"><span class="fx-ico">&#127881;</span><b>XP & Achievements</b><span>Full XP history, badges, rank, and progress.</span></a>

              <a href="/mail-hub" class="fx-tile"><span class="fx-ico">&#128231;</span><b>Mail Hub</b><span>Connect Gmail / Outlook. AI sorts professor emails by priority.</span></a>

              <a href="/student/settings" class="fx-tile"><span class="fx-ico">&#9881;&#65039;</span><b>Settings</b><span>Theme, language, daily-email time, and replay the tutorial.</span></a>

            </div>

            <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;">

              <button onclick="localStorage.removeItem('mr-tutorial-done');location.reload();" class="btn btn-outline btn-sm">&#127891; Run guided tour</button>

              <button onclick="localStorage.setItem('mr-fx-hidden','1');document.getElementById('feature-explorer').style.display='none';" class="btn btn-ghost btn-sm">I've got it &mdash; hide this</button>

            </div>

          </div>

        </div>

        <style>

          .fx-tile {{

            display:flex;flex-direction:column;gap:4px;padding:14px;border:1px solid var(--border);border-radius:12px;

            background:var(--bg);text-decoration:none;color:var(--text);transition:all .18s ease;

          }}

          .fx-tile:hover {{ border-color:var(--primary);transform:translateY(-2px);box-shadow:0 8px 20px rgba(99,102,241,.15); }}

          .fx-tile .fx-ico {{ font-size:22px; }}

          .fx-tile b {{ font-size:14px;font-weight:700; }}

          .fx-tile span:last-child {{ font-size:12px;color:var(--text-muted);line-height:1.45; }}

        </style>

        <script>

          (function() {{

            if (localStorage.getItem('mr-fx-hidden') === '1') {{

              var el = document.getElementById('feature-explorer');

              if (el) el.style.display = 'none';

            }} else {{

              // Auto-open for new users who haven't taken the tutorial yet

              var firstVisit = !localStorage.getItem('mr-tutorial-done') && !localStorage.getItem('mr-fx-seen');

              if (firstVisit) {{

                document.getElementById('fx-body').style.display = 'block';

                document.getElementById('fx-caret').innerHTML = 'Hide &#9650;';

                localStorage.setItem('mr-fx-seen', '1');

              }}

            }}

          }})();

          function toggleExplorer() {{

            var body = document.getElementById('fx-body');

            var caret = document.getElementById('fx-caret');

            if (body.style.display === 'none' || !body.style.display) {{

              body.style.display = 'block';

              caret.innerHTML = 'Hide &#9650;';

            }} else {{

              body.style.display = 'none';

              caret.innerHTML = 'Show &#9660;';

            }}

          }}

        </script>



        <!-- XP / Level Bar -->

        <a href="/student/achievements" class="hover-lift" style="text-decoration:none;display:block;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 22px;margin-bottom:20px;">

          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">

            <div style="display:flex;align-items:center;gap:10px;">

              <span style="font-size:1.5em">&#127942;</span>

              <span style="font-weight:800;color:var(--text);font-size:15px;">{_esc(level_name)}</span>

              <span style="color:var(--text-muted);font-size:13px;">{total_xp} XP</span>

            </div>

            <div style="display:flex;align-items:center;gap:8px;">

              <span style="color:var(--yellow);font-weight:700;font-size:15px;"><span class="streak-flame">&#128293;</span> {streak_days}</span>

              <span style="color:var(--text-muted);font-size:12px;">day streak</span>

            </div>

          </div>

          <div class="progress-wrap" style="height:10px;">

            <div class="progress-bar bar-purple" style="width:{xp_pct}%;"></div>

          </div>

          <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:right;">{total_xp - level_floor}/{level_ceil - level_floor} XP to next level</div>

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

        async function syncCourses() {{

          var btn = document.getElementById('sync-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Starting...';

          document.getElementById('sync-progress').style.display = 'block';

          try {{

            var r = await fetch('/api/student/sync', {{method: 'POST'}});

            var d = await _safeJson(r);

            if (!r.ok) {{ alert(d.error || 'Sync failed'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; return; }}

            pollSync(btn);

          }} catch(e) {{ alert('Network error'); btn.disabled = false; btn.innerHTML = '&#128260; Sync Canvas'; document.getElementById('sync-progress').style.display = 'none'; }}

        }}

        function pollSync(btn) {{

          var iv = setInterval(async function() {{

            try {{

              var r = await fetch('/api/student/sync/status');

              var d = await _safeJson(r);

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

            var d = await _safeJson(r);

            if (!r.ok) {{ alert(d.error || 'Generation failed'); btn.disabled = false; btn.innerHTML = '&#129302; Generate Plan'; return; }}

            var iv = setInterval(async function() {{

              try {{

                var r2 = await fetch('/api/student/plan/status');

                var s = await r2.json();

                if (s.status === 'done') {{

                  clearInterval(iv);

                  if (window.showToast) window.showToast('Study plan generated!', 'success'); else alert('Study plan generated!');

                  if (window.confettiBurst) window.confettiBurst(60);

                  setTimeout(function(){{ location.reload(); }}, 900);

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

            try:

                n_exams = len(sdb.get_course_exams(c["id"]) or [])

            except Exception:

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

                <button onclick="document.getElementById('upload-{c['id']}').click()" class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 6px;margin-left:4px;" title="Upload files">&#128206;</button>

                <input type="file" id="upload-{c['id']}" style="display:none;" accept=".pdf,.docx,.doc" multiple onchange="uploadFiles({c['id']},this)">

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

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:8px">

          <h1>&#128218; My Courses</h1>

          <div style="display:flex;gap:8px;flex-wrap:wrap">

            <button onclick="showNewCourseModal()" class="btn btn-outline btn-sm">&#43; New Course</button>

            <button onclick="syncCourses()" class="btn btn-primary btn-sm" id="sync-btn">&#128260; Sync Canvas</button>

          </div>

        </div>



        <div id="new-course-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;align-items:center;justify-content:center">

          <div style="background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;width:92%;max-width:440px">

            <h3 style="margin:0 0 6px">Create a course</h3>

            <p style="color:var(--text-muted);font-size:13px;margin:0 0 14px">For classes outside Canvas. You can add exams and files to it just like a synced course.</p>

            <div class="form-group">

              <label>Course name *</label>

              <input id="nc-name" type="text" placeholder="Intro to Microeconomics" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <div class="form-group">

              <label>Code</label>

              <input id="nc-code" type="text" placeholder="ECON 101" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <div class="form-group">

              <label>Term</label>

              <input id="nc-term" type="text" placeholder="Spring 2026" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">

              <button onclick="hideNewCourseModal()" class="btn btn-ghost btn-sm">Cancel</button>

              <button onclick="saveNewCourse()" class="btn btn-primary btn-sm" id="nc-save">Create</button>

            </div>

          </div>

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

            var d = await _safeJson(r);

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

        function updateUploadBar(pct, msg) {{

          document.getElementById('upload-bar-fill').style.width = pct + '%';

          document.getElementById('upload-bar-pct').textContent = msg || (Math.round(pct) + '%');

        }}

        function hideUploadBar(msg) {{

          document.getElementById('upload-bar-fill').style.width = '100%';

          document.getElementById('upload-bar-pct').textContent = msg || 'Done!';

          setTimeout(function(){{ document.getElementById('upload-bar').style.display = 'none'; }}, 1500);

        }}

        async function uploadFiles(courseId, input) {{

          if (!input.files.length) return;

          var fileList = Array.from(input.files);

          showUploadBar(fileList.length + ' file(s)');

          var done = 0;

          for (var i = 0; i < fileList.length; i++) {{

            updateUploadBar(((i)/fileList.length)*90, fileList[i].name);

            var fd = new FormData();

            fd.append('file', fileList[i]);

            try {{

              var r = await fetch('/api/student/courses/' + courseId + '/upload', {{method:'POST', body:fd}});

              var d = await _safeJson(r);

              if (!r.ok) console.warn('Upload failed:', fileList[i].name, d.error);

            }} catch(e) {{ console.warn('Upload error:', fileList[i].name); }}

            done++;

            updateUploadBar((done/fileList.length)*90, done + '/' + fileList.length + ' done');

          }}

          hideUploadBar(done + '/' + fileList.length + ' uploaded');

          input.value = '';

          setTimeout(function(){{ location.reload(); }}, 1000);

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

            else {{ var d = await _safeJson(r); alert(d.error || 'Failed to remove course'); }}

          }} catch(e) {{ alert('Network error'); }}

        }}

        function showNewCourseModal() {{

          document.getElementById('new-course-modal').style.display = 'flex';

          setTimeout(function(){{ document.getElementById('nc-name').focus(); }}, 50);

        }}

        function hideNewCourseModal() {{

          document.getElementById('new-course-modal').style.display = 'none';

          document.getElementById('nc-name').value = '';

          document.getElementById('nc-code').value = '';

          document.getElementById('nc-term').value = '';

        }}

        async function saveNewCourse() {{

          var name = document.getElementById('nc-name').value.trim();

          if (!name) {{ alert('Course name is required'); return; }}

          var code = document.getElementById('nc-code').value.trim();

          var term = document.getElementById('nc-term').value.trim();

          var btn = document.getElementById('nc-save');

          btn.disabled = true; btn.textContent = 'Creating...';

          try {{

            var meta = document.querySelector('meta[name="csrf-token"]');

            var headers = {{'Content-Type':'application/json'}};

            if (meta) headers['X-CSRFToken'] = meta.getAttribute('content');

            var r = await fetch('/api/student/courses/manual', {{

              method:'POST', headers: headers,

              body: JSON.stringify({{name:name, code:code, term:term}})

            }});

            if (r.ok) {{ location.reload(); }}

            else {{ var d = await _safeJson(r); alert(d.error || 'Failed to create course'); btn.disabled=false; btn.textContent='Create'; }}

          }} catch(e) {{ alert('Network error'); btn.disabled=false; btn.textContent='Create'; }}

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

                  <input type="file" style="display:none;" accept=".pdf,.docx,.doc" multiple onchange="try{{uploadExamFiles({course_id},{e['id']},this)}}catch(err){{alert('Upload error: '+err.message)}}">

                </label>

              </td>

              <td>

                <button onclick="saveExam({e['id']},this)" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;" title="Save">&#128190;</button>

                <button onclick="deleteExam({e['id']})" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;color:var(--red);" title="Delete">&#128465;</button>

              </td>

            </tr>"""



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

            files_html += f"""<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);">

              <span>&#128196; {_esc(uf['original_name'])} <span style="color:var(--text-muted);font-size:11px;">({uf.get('file_type','?')})</span></span>

              <div style="display:flex;gap:8px;">

                <button onclick="viewFileText({uf['id']})" style="background:none;border:none;color:var(--primary);cursor:pointer;font-size:12px;" title="Preview extracted text">&#128065; View</button>

                <button onclick="deleteFile({uf['id']})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:12px;" title="Delete file">&#128465; Remove</button>

              </div>

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



        <!-- Multi-file upload queue (fixed position) -->

        <div id="upload-queue" style="display:none;position:fixed;top:0;left:0;right:0;z-index:99999;background:#1E1B4B;border-bottom:3px solid #7C3AED;padding:14px 24px;box-shadow:0 4px 24px rgba(0,0,0,0.4);">

          <div style="max-width:800px;margin:0 auto;">

            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">

              <span style="color:#E0E7FF;font-size:14px;font-weight:600;">&#128206; Uploading files</span>

              <span id="upload-queue-count" style="color:#A5B4FC;font-size:14px;font-weight:700;">0/0</span>

            </div>

            <div id="upload-queue-list" style="max-height:200px;overflow-y:auto;"></div>

          </div>

        </div>



        <!-- File preview modal -->

        <div id="file-preview-modal" style="display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.6);backdrop-filter:blur(4px);justify-content:center;align-items:center;" onclick="if(event.target===this)this.style.display='none'">

          <div style="background:var(--card);border-radius:var(--radius);width:90%;max-width:800px;max-height:85vh;display:flex;flex-direction:column;box-shadow:var(--shadow-lg);">

            <div style="display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid var(--border);">

              <h3 id="file-preview-name" style="font-size:15px;margin:0;">File Preview</h3>

              <button onclick="document.getElementById('file-preview-modal').style.display='none'" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--text-muted);">&#10005;</button>

            </div>

            <pre id="file-preview-text" style="padding:20px;margin:0;overflow:auto;flex:1;font-size:13px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;font-family:'Inter',monospace;color:var(--text);background:var(--bg);"></pre>

          </div>

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

              + Upload Files

              <input type="file" style="display:none;" accept=".pdf,.docx,.doc" multiple onchange="try{{uploadFiles({course_id},this)}}catch(err){{alert('Upload error: '+err.message)}}">

            </label>

          </div>

          {files_html}

        </div>



        <style>

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        </style>



        <!-- Upload functions -->

        <script>

        function doUpload(cid, fd, fileName) {{

          var row = document.createElement('div');

          row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;';

          row.innerHTML = '<span style="color:#E0E7FF;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%;">&#128196; ' + fileName + '</span>'

            + '<div style="display:flex;align-items:center;gap:8px;flex:1;margin-left:12px;">'

            + '<div style="flex:1;background:#312E81;border-radius:6px;height:8px;overflow:hidden;"><div class="uf-bar" style="height:100%;background:linear-gradient(90deg,#7C3AED,#A78BFA);width:0%;transition:width 0.3s;border-radius:6px;"></div></div>'

            + '<span class="uf-pct" style="color:#A5B4FC;font-size:12px;font-weight:600;min-width:40px;text-align:right;">0%</span>'

            + '</div>';

          var list = document.getElementById('upload-queue-list');

          list.appendChild(row);

          document.getElementById('upload-queue').style.display = 'block';

          var bar = row.querySelector('.uf-bar');

          var pct = row.querySelector('.uf-pct');

          return new Promise(function(resolve) {{

            var csrfToken = document.querySelector('meta[name="csrf-token"]');

            var xhr = new XMLHttpRequest();

            xhr.open('POST', '/api/student/courses/' + cid + '/upload');

            if (csrfToken) xhr.setRequestHeader('X-CSRFToken', csrfToken.content);

            xhr.upload.onprogress = function(e) {{

              if (e.lengthComputable) {{

                var p = Math.round((e.loaded / e.total) * 80);

                bar.style.width = p + '%'; pct.textContent = p + '%';

              }}

            }};

            xhr.onload = function() {{

              bar.style.width = '100%';

              try {{

                var d = JSON.parse(xhr.responseText);

                if (xhr.status >= 200 && xhr.status < 300) {{

                  pct.textContent = '\\u2713'; pct.style.color = '#6EE7B7';

                }} else {{

                  pct.textContent = '\\u2717'; pct.style.color = '#FCA5A5';

                  row.title = d.error || 'Upload failed';

                }}

              }} catch(e) {{ pct.textContent = '\\u2717'; pct.style.color = '#FCA5A5'; }}

              resolve();

            }};

            xhr.onerror = function() {{ pct.textContent = '\\u2717'; pct.style.color = '#FCA5A5'; resolve(); }};

            bar.style.width = '5%'; pct.textContent = '5%';

            xhr.send(fd);

          }});

        }}

        async function uploadFiles(cid, input) {{

          if (!input.files.length) return;

          document.getElementById('upload-queue-list').innerHTML = '';

          var fileList = Array.from(input.files);

          document.getElementById('upload-queue-count').textContent = '0/' + fileList.length;

          for (var i = 0; i < fileList.length; i++) {{

            var fd = new FormData();

            fd.append('file', fileList[i]);

            await doUpload(cid, fd, fileList[i].name);

            document.getElementById('upload-queue-count').textContent = (i+1) + '/' + fileList.length;

          }}

          input.value = '';

          setTimeout(function(){{ location.reload(); }}, 1200);

        }}

        async function uploadExamFiles(cid, examId, input) {{

          if (!input.files.length) return;

          document.getElementById('upload-queue-list').innerHTML = '';

          var fileList = Array.from(input.files);

          document.getElementById('upload-queue-count').textContent = '0/' + fileList.length;

          for (var i = 0; i < fileList.length; i++) {{

            var fd = new FormData();

            fd.append('file', fileList[i]);

            fd.append('exam_id', examId);

            await doUpload(cid, fd, fileList[i].name);

            document.getElementById('upload-queue-count').textContent = (i+1) + '/' + fileList.length;

          }}

          input.value = '';

          setTimeout(function(){{ location.reload(); }}, 1200);

        }}

        async function viewFileText(fileId) {{

          var modal = document.getElementById('file-preview-modal');

          document.getElementById('file-preview-name').textContent = 'Loading...';

          document.getElementById('file-preview-text').textContent = '';

          modal.style.display = 'flex';

          try {{

            var r = await fetch('/api/student/files/' + fileId);

            var d = await _safeJson(r);

            if (d.name) document.getElementById('file-preview-name').textContent = d.name;

            document.getElementById('file-preview-text').textContent = d.text || '(No text extracted)';

          }} catch(e) {{ document.getElementById('file-preview-text').textContent = 'Error loading file'; }}

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



          var csrfToken = document.querySelector('meta[name="csrf-token"]');

          var headers = {{'Content-Type':'application/json'}};

          if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;



          try {{

            // 1. Save all exam rows (existing and new)

            var examRows = document.querySelectorAll('#exams-table tbody tr');

            var examErrors = [];

            for (var i = 0; i < examRows.length; i++) {{

              var tr = examRows[i];

              var nameEl = tr.querySelector('[data-field="name"]');

              if (!nameEl) continue;

              var name = nameEl.value.trim();

              if (!name) continue; // skip empty rows silently

              var exam_date = (tr.querySelector('[data-field="exam_date"]') || {{}}).value || '';

              var weightEl = tr.querySelector('[data-field="weight_pct"]');

              var weight_pct = weightEl ? (parseInt(weightEl.value) || 0) : 0;

              var topicsEl = tr.querySelector('[data-field="topics"]');

              var topicsRaw = topicsEl ? topicsEl.value : '';

              var topics = topicsRaw.split(',').map(function(t){{ return t.trim(); }}).filter(Boolean);

              var examId = tr.dataset.examId;

              var url, method;

              if (examId && examId !== 'new') {{

                url = '/api/student/exams/' + examId; method = 'PUT';

              }} else {{

                url = '/api/student/courses/' + courseId + '/exams'; method = 'POST';

              }}

              try {{

                var er = await fetch(url, {{

                  method: method, headers: headers,

                  body: JSON.stringify({{ name: name, exam_date: exam_date, weight_pct: weight_pct, topics: topics, course_id: courseId }})

                }});

                if (!er.ok) {{

                  var ed = await _safeJson(er);

                  examErrors.push(name + ': ' + (ed.error || 'save failed'));

                }}

              }} catch(e) {{ examErrors.push(name + ': network error'); }}

            }}



            // 2. Save course info + schedule + tips

            var r = await fetch('/api/student/courses/' + courseId, {{

              method:'PUT', headers: headers,

              body: JSON.stringify({{

                name: document.getElementById('course-name').value.trim(),

                code: document.getElementById('course-code').value.trim(),

                weekly_schedule: schedule,

                study_tips: tips

              }})

            }});

            if (r.ok) {{

              if (examErrors.length) alert('Saved with some exam errors:\\n' + examErrors.join('\\n'));

              else alert('All changes saved!');

              location.reload();

            }}

            else {{ var d = await _safeJson(r); alert(d.error || 'Save failed'); }}

          }} catch(e) {{ alert('Network error: ' + e.message); }}

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

            + ' <button onclick="this.closest(\\'tr\\').remove()" class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;color:var(--red);">&#128465;</button></td>';

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

            else {{ var d = await _safeJson(r); alert(d.error || 'Failed'); }}

          }} catch(e) {{ alert('Network error'); }}

        }}



        async function deleteExam(examId) {{

          if (!confirm('Delete this exam?')) return;

          try {{

            await fetch('/api/student/exams/' + examId, {{method:'DELETE'}});

            location.reload();

          }} catch(e) {{ alert('Network error'); }}

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

            var d = await _safeJson(r);

            if (!r.ok) {{ alert(d.error || 'Failed'); btn.disabled = false; btn.innerHTML = '&#128260; Regenerate'; return; }}

            var iv = setInterval(async function() {{

              try {{

                var r2 = await fetch('/api/student/plan/status');

                var s = await r2.json();

                if (s.status === 'done') {{

                  clearInterval(iv);

                  if (window.showToast) window.showToast('Study plan generated!', 'success'); else alert('Study plan generated!');

                  if (window.confettiBurst) window.confettiBurst(60);

                  setTimeout(function(){{ location.reload(); }}, 900);

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

          <div class="stat-card stat-purple"><div class="num" id="stat-hours">{focus_stats['total_hours']}</div><div class="label">Hours Focused</div></div>

          <div class="stat-card stat-blue"><div class="num" id="stat-sessions">{focus_stats['sessions']}</div><div class="label">Sessions</div></div>

          <div class="stat-card stat-green"><div class="num" id="stat-pages">{focus_stats['total_pages']}</div><div class="label">Pages Read</div></div>

          <div class="stat-card stat-red"><div class="num" id="stat-streak">{focus_stats['streak_days']}</div><div class="label">Day Streak &#128293;</div></div>

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

                <small style="display:block;margin-top:6px;color:var(--text-muted);font-size:11px;">Music plays while you're on this page. For uninterrupted background music, use the Spotify desktop app or a separate browser tab.</small>

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



            <!-- Focus Guard card -->

            <div class="card" id="focus-guard-card" style="position:relative;overflow:hidden;">

              <div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(1200px 220px at -10% -20%, rgba(139,92,246,.12), transparent 60%), radial-gradient(900px 200px at 120% 120%, rgba(99,102,241,.10), transparent 60%);pointer-events:none;"></div>

              <div class="card-header" style="position:relative;z-index:1;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">

                <h2 style="margin:0;">&#128737;&#65039; Focus Guard</h2>

                <span id="fg-status" style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;background:rgba(148,163,184,.12);color:var(--text-muted);border:1px solid rgba(148,163,184,.2);">

                  <span id="fg-dot" style="width:8px;height:8px;border-radius:50%;background:#64748b;display:inline-block;"></span>

                  <span id="fg-label">Idle</span>

                </span>

              </div>

              <div style="position:relative;z-index:1;">

                <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;line-height:1.55;">

                  Block Instagram, TikTok, Twitter/X and other time-sinks automatically while a session is running.

                  <b style="color:var(--text);">YouTube stays allowed</b> — because you might actually be studying.

                </p>



                <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;">

                  <span class="fg-chip">Instagram</span>

                  <span class="fg-chip">TikTok</span>

                  <span class="fg-chip">Twitter/X</span>

                  <span class="fg-chip">Facebook</span>

                  <span class="fg-chip">Reddit</span>

                  <span class="fg-chip">Snapchat</span>

                  <span class="fg-chip">Twitch</span>

                  <span class="fg-chip">Netflix</span>

                  <span class="fg-chip fg-chip-allow">YouTube &#10003;</span>

                </div>



                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">

                  <a href="/download/focus-guard.zip" class="btn btn-primary btn-sm" download>&#11015; Download extension</a>

                  <button onclick="document.getElementById('fg-how').style.display='block';this.style.display='none';" class="btn btn-outline btn-sm">How to install</button>

                </div>



                <div id="fg-how" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px 14px;font-size:12px;color:var(--text-muted);line-height:1.7;">

                  <b style="color:var(--text);">Install in 30 seconds:</b>

                  <ol style="margin:6px 0 0 18px;padding:0;">

                    <li>Unzip the downloaded file.</li>

                    <li>Open <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">chrome://extensions</code> (or <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">edge://extensions</code>).</li>

                    <li>Toggle <b>Developer mode</b> on (top-right).</li>

                    <li>Click <b>Load unpacked</b> and select the unzipped <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">focus-guard</code> folder.</li>

                    <li>Start a timer here — external distractions get blocked automatically.</li>

                  </ol>

                </div>

              </div>

            </div>



          </div>

        </div>



        <style>

        .fg-chip {{ font-size:11px;padding:3px 8px;border-radius:999px;background:rgba(239,68,68,.10);color:#fca5a5;border:1px solid rgba(239,68,68,.2);font-weight:600; }}

        .fg-chip-allow {{ background:rgba(34,197,94,.10);color:#86efac;border-color:rgba(34,197,94,.25); }}

        #fg-status.active {{ background:linear-gradient(135deg,rgba(139,92,246,.18),rgba(99,102,241,.18));color:#C7D2FE;border-color:rgba(139,92,246,.35); }}

        #fg-status.active #fg-dot {{ background:#22c55e;box-shadow:0 0 10px #22c55e;animation:fgPulse 1.4s infinite; }}

        @keyframes fgPulse {{ 50% {{ opacity:.4; }} }}

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

        /* === Background-safe alarm =========================================

           We use BOTH:
           1) A pre-loaded HTML5 <audio> element with a base64 WAV. HTML5 audio
              continues to work in background tabs (unlike WebAudio which is
              suspended). It must be primed by a user gesture (start button)
              once per session so browsers allow background playback.
           2) WebAudio chime as a fallback for when the page IS focused.
           3) Desktop Notification when permission has been granted, so the
              user gets an alert even if the tab is hidden and audio is muted.
        */

        var alarmCtx = null;
        var alarmAudio = null;          // HTML5 <audio> element (background-safe)
        var keepaliveAudio = null;      // silent looping audio — keeps tab UNTHROTTLED
        var alarmPrimed = false;
        var notifPermission = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';

        function buildAlarmWavDataUri() {{
          // Generate a short 3-tone bell as a 16-bit PCM WAV, encode as data URI.
          // (~1.2s @ 22050Hz mono = ~26KB base64.)
          var sampleRate = 22050;
          var duration = 1.4;
          var n = Math.floor(sampleRate * duration);
          var buffer = new ArrayBuffer(44 + n * 2);
          var view = new DataView(buffer);
          function writeStr(off, s){{ for(var i=0;i<s.length;i++) view.setUint8(off+i, s.charCodeAt(i)); }}
          writeStr(0,'RIFF'); view.setUint32(4, 36 + n*2, true);
          writeStr(8,'WAVEfmt '); view.setUint32(16,16,true); view.setUint16(20,1,true);
          view.setUint16(22,1,true); view.setUint32(24,sampleRate,true);
          view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
          writeStr(36,'data'); view.setUint32(40, n*2, true);
          var freqs = [523.25, 659.25, 783.99];  // C5 E5 G5
          for (var i=0; i<n; i++) {{
            var t = i / sampleRate;
            // Three staggered chimes
            var s = 0;
            for (var k=0; k<3; k++) {{
              var start = k * 0.35;
              if (t >= start && t < start + 0.6) {{
                var local = t - start;
                var env = Math.exp(-local * 5);
                s += Math.sin(2 * Math.PI * freqs[k] * local) * env * 0.3;
              }}
            }}
            var v = Math.max(-1, Math.min(1, s));
            view.setInt16(44 + i*2, v * 0x7FFF, true);
          }}
          // Convert to base64
          var bytes = new Uint8Array(buffer);
          var binary = '';
          for (var j=0; j<bytes.length; j++) binary += String.fromCharCode(bytes[j]);
          return 'data:audio/wav;base64,' + btoa(binary);
        }}

        function buildSilenceWavDataUri() {{
          // 1-second mono silent 16-bit PCM WAV (very small).
          // Looping this in an <audio> element prevents Chrome from suspending
          // / throttling the tab — tabs marked "playing audio" are never throttled.
          var sampleRate = 8000;
          var n = sampleRate;
          var buffer = new ArrayBuffer(44 + n * 2);
          var view = new DataView(buffer);
          function w(off, s){{ for(var i=0;i<s.length;i++) view.setUint8(off+i, s.charCodeAt(i)); }}
          w(0,'RIFF'); view.setUint32(4, 36+n*2, true);
          w(8,'WAVEfmt '); view.setUint32(16,16,true); view.setUint16(20,1,true);
          view.setUint16(22,1,true); view.setUint32(24,sampleRate,true);
          view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
          w(36,'data'); view.setUint32(40, n*2, true);
          // body is already zeroed = silence
          var bytes = new Uint8Array(buffer);
          var binary = '';
          for (var j=0; j<bytes.length; j++) binary += String.fromCharCode(bytes[j]);
          return 'data:audio/wav;base64,' + btoa(binary);
        }}

        function startKeepalive() {{
          // Keep tab unthrottled by playing a silent looping audio track.
          try {{
            if (!keepaliveAudio) {{
              keepaliveAudio = new Audio(buildSilenceWavDataUri());
              keepaliveAudio.loop = true;
              keepaliveAudio.preload = 'auto';
              keepaliveAudio.volume = 0.001; // effectively silent but counts as "playing audio"
            }}
            keepaliveAudio.currentTime = 0;
            var p = keepaliveAudio.play();
            if (p && p.catch) p.catch(function(){{}});
          }} catch(e) {{}}
        }}

        function stopKeepalive() {{
          try {{
            if (keepaliveAudio) {{
              keepaliveAudio.pause();
              keepaliveAudio.currentTime = 0;
            }}
          }} catch(e) {{}}
        }}

        function primeAlarm() {{
          // MUST be called from a user-gesture handler (e.g. Start button) to
          // unlock both WebAudio and HTML5 audio for later background playback.
          if (alarmPrimed) return;
          try {{
            if (!alarmAudio) {{
              alarmAudio = new Audio(buildAlarmWavDataUri());
              alarmAudio.preload = 'auto';
              alarmAudio.volume = 0.7;
              // Play silently to unlock autoplay policy
              alarmAudio.muted = true;
              var p = alarmAudio.play();
              if (p && p.then) p.then(function(){{
                alarmAudio.pause();
                alarmAudio.currentTime = 0;
                alarmAudio.muted = false;
              }}).catch(function(){{
                alarmAudio.muted = false;
              }});
            }}
            if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (alarmCtx.state === 'suspended') alarmCtx.resume().catch(function(){{}});
          }} catch(e) {{}}
          // Ask for notification permission once, so we can alert when hidden
          if (typeof Notification !== 'undefined' && Notification.permission === 'default') {{
            Notification.requestPermission().then(function(p){{ notifPermission = p; }}).catch(function(){{}});
          }}
          alarmPrimed = true;
        }}

        function showNotification(title, body) {{
          try {{
            if (typeof Notification === 'undefined') return;
            if (Notification.permission !== 'granted') return;
            var n = new Notification(title, {{ body: body, silent: false, tag: 'machreach-focus' }});
            n.onclick = function(){{ window.focus(); n.close(); }};
          }} catch(e) {{}}
        }}

        function playAlarm() {{
          // 1) HTML5 audio (works in background)
          try {{
            if (alarmAudio) {{
              alarmAudio.currentTime = 0;
              var p = alarmAudio.play();
              if (p && p.catch) p.catch(function(){{}});
            }}
          }} catch(e) {{}}
          // 2) WebAudio (richer sound when tab is focused)
          try {{
            if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
            var ctx = alarmCtx;
            if (ctx.state === 'suspended' && ctx.resume) {{
              ctx.resume().then(function(){{ playAlarmTones(ctx); }}).catch(function(){{}});
            }} else {{
              playAlarmTones(ctx);
            }}
          }} catch(e) {{}}
        }}

        function playAlarmTones(ctx) {{

          try {{

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

        var phaseEndAt = null;        // wall-clock end time of current countdown phase

        var phaseEnded = false;       // guard so onTimerEnd fires only once per phase

        var currentMode = 'pomodoro';

        var totalFocusSeconds = 0;

        var sessionStarted = false;

        var pageDone = 0;

        var phaseStartFocusSeconds = 0;



        function saveFocusTimerState() {{

          localStorage.setItem('focus_timer_state', JSON.stringify({{

            currentMode: currentMode, isBreak: isBreak, pomoCount: pomoCount,

            totalFocusSeconds: totalFocusSeconds, phaseStartFocusSeconds: phaseStartFocusSeconds,

            totalTime: totalTime, course: document.getElementById('focus-course').value

          }}));

        }}

        function clearFocusTimerState() {{

          localStorage.removeItem('focus_timer_state');

        }}



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

        function startTimer(isRestore) {{

          if (isRunning) return;

          // Prime the alarm INSIDE the user-gesture handler so audio can play
          // later even when the tab is hidden.
          primeAlarm();

          // Start a silent looping audio track so Chrome treats the tab as
          // "playing audio" and does NOT throttle/freeze it in the background.
          // This is what makes the timer fire on time and the alarm play even
          // when the user is on another tab for 25+ minutes.
          startKeepalive();

          isRunning = true;

          sessionStarted = true;

          document.getElementById('start-btn').style.display = 'none';

          document.getElementById('pause-btn').style.display = '';

          document.getElementById('skip-btn').style.display = currentMode === 'pomodoro' ? '' : 'none';



          if (!isBreak && !isRestore) phaseStartFocusSeconds = totalFocusSeconds;

          saveFocusTimerState();



          if (currentMode === 'pages') {{

            document.getElementById('timer-label').textContent = '📖 Reading — click the big button for each page';

            var swStart = Date.now();

            var swInitial = totalFocusSeconds;

            localStorage.setItem('focus_float', JSON.stringify({{

              active:true, mode:'stopwatch', startAt: isRestore ? (Date.now() - totalFocusSeconds * 1000) : swStart, label:'📖 Reading',

              originalMode:'pages', course: document.getElementById('focus-course').value || ''

            }}));

            showFloatWidget();

            timerInterval = setInterval(function() {{

              var elapsed = Math.floor((Date.now() - swStart) / 1000);

              totalFocusSeconds = swInitial + elapsed;

              updateDisplay();

              updateFloatFromLocal();

            }}, 1000);

          }} else {{

            document.getElementById('timer-label').textContent = isBreak ? '☕ Break time!' : '🔥 Focus!';

            var phaseStart = Date.now();

            var initialTimeLeft = timeLeft;

            var focusAtStart = totalFocusSeconds;

            var endAt = phaseStart + initialTimeLeft * 1000;

            phaseEndAt = endAt;

            phaseEnded = false;

            var label = isBreak ? '☕ Break' : '🔥 Focus';

            var courseName = document.getElementById('focus-course').value || '';

            // Minutes of WORK this phase will credit when it ends (0 for breaks).

            var phaseWorkMinutes = isBreak ? 0 : Math.round(initialTimeLeft / 60);

            // Unique id so we never double-credit a phase across page/global controllers.

            var phaseId = 'p_' + Date.now() + '_' + Math.floor(Math.random()*1e9);

            var nextPhase = null;

            if (currentMode === 'pomodoro') {{

              if (!isBreak) {{

                var nextPomoCount = pomoCount + 1;

                var nextBreakMins = (nextPomoCount % 4 === 0)

                  ? parseInt(document.getElementById('pomo-long').value)

                  : parseInt(document.getElementById('pomo-break').value);

                var followingWorkMins = parseInt(document.getElementById('pomo-work').value);

                nextPhase = {{

                  active:true, mode:'countdown',

                  endAt: endAt + nextBreakMins*60*1000,

                  label: (nextPomoCount % 4 === 0) ? '🎉 Long Break' : '☕ Break',

                  originalMode:'pomodoro', isBreak:true, course:courseName, workMinutes:0,

                  phaseId: 'p_' + (Date.now()+1) + '_' + Math.floor(Math.random()*1e9),

                  nextPhase: {{

                    active:true, mode:'countdown',

                    endAt: endAt + nextBreakMins*60*1000 + followingWorkMins*60*1000,

                    label: '🔥 Focus',

                    originalMode:'pomodoro', isBreak:false, course:courseName, workMinutes: followingWorkMins,

                    phaseId: 'p_' + (Date.now()+2) + '_' + Math.floor(Math.random()*1e9),

                    nextPhase: null

                  }}

                }};

              }} else {{

                var workMins = parseInt(document.getElementById('pomo-work').value);

                nextPhase = {{

                  active:true, mode:'countdown',

                  endAt: endAt + workMins*60*1000,

                  label: '🔥 Focus',

                  originalMode:'pomodoro', isBreak:false, course:courseName, workMinutes: workMins,

                  phaseId: 'p_' + (Date.now()+1) + '_' + Math.floor(Math.random()*1e9),

                  nextPhase: null

                }};

              }}

            }}

            localStorage.setItem('focus_float', JSON.stringify({{

              active:true, mode:'countdown', endAt:endAt, label:label, nextPhase:nextPhase,

              originalMode: currentMode, isBreak: isBreak, course: courseName,

              workMinutes: phaseWorkMinutes, phaseId: phaseId

            }}));

            showFloatWidget();

            // ─── Hard-end timer (fires once at exact phase end) ───
            // setTimeout(0) is more reliably scheduled than setInterval ticks
            // when the tab is throttled, but is ALSO throttled when hidden.
            // Combined with the visibilitychange handler below this gives us
            // belt-and-suspenders coverage.
            if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); }}
            window.__focusEndTimeout = setTimeout(function() {{
              if (phaseEnded) return;
              phaseEnded = true;
              if (timerInterval) {{ clearInterval(timerInterval); timerInterval = null; }}
              isRunning = false;
              timeLeft = 0;
              if (!isBreak) totalFocusSeconds = focusAtStart + initialTimeLeft;
              updateDisplay();
              onTimerEnd();
            }}, Math.max(0, endAt - Date.now()));

            timerInterval = setInterval(function() {{

              var elapsed = Math.floor((Date.now() - phaseStart) / 1000);

              timeLeft = Math.max(0, initialTimeLeft - elapsed);

              if (!isBreak) totalFocusSeconds = focusAtStart + elapsed;

              updateDisplay();

              if (timeLeft <= 0 && !phaseEnded) {{

                phaseEnded = true;

                clearInterval(timerInterval);

                isRunning = false;

                if (!isBreak) totalFocusSeconds = focusAtStart + initialTimeLeft;

                onTimerEnd();

              }}

            }}, 1000);

          }}

        }}

        // === Background-tab safety net ===

        // Browsers throttle setInterval on hidden tabs (often to >=1 min).

        // When the tab becomes visible again, force a real-time check so the timer

        // completes (sound, XP, save) even if it expired while in the background.

        document.addEventListener('visibilitychange', function() {{

          if (document.hidden) return;

          if (!isRunning || phaseEnded || phaseEndAt == null) return;

          var nowMs = Date.now();

          if (nowMs >= phaseEndAt) {{

            phaseEnded = true;

            if (timerInterval) {{ clearInterval(timerInterval); timerInterval = null; }}

            isRunning = false;

            timeLeft = 0;

            updateDisplay();

            onTimerEnd();

          }} else {{

            // Tab is back early — refresh the displayed countdown immediately

            timeLeft = Math.max(0, Math.floor((phaseEndAt - nowMs) / 1000));

            updateDisplay();

          }}

        }});



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

          if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); window.__focusEndTimeout = null; }}

          stopKeepalive();

          isRunning = false;

          document.getElementById('start-btn').style.display = '';

          document.getElementById('pause-btn').style.display = 'none';

          document.getElementById('timer-label').textContent = 'Paused';

          localStorage.removeItem('focus_float');

          clearFocusTimerState();

          var el = document.getElementById('focus-float');

          if (el) el.style.display = 'none';

        }}



        function resetTimer() {{

          clearInterval(timerInterval);

          if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); window.__focusEndTimeout = null; }}

          stopKeepalive();

          isRunning = false;

          isBreak = false;

          if (sessionStarted && totalFocusSeconds > 60) {{

            saveFocusSession();

          }}

          totalFocusSeconds = 0;

          phaseStartFocusSeconds = 0;

          sessionStarted = false;

          pomoCount = 0;

          pageDone = 0;

          document.getElementById('start-btn').style.display = '';

          document.getElementById('pause-btn').style.display = 'none';

          document.getElementById('skip-btn').style.display = 'none';

          localStorage.removeItem('focus_float');

          clearFocusTimerState();

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

          stopKeepalive();

          playAlarm();

          // Desktop notification works even when tab is hidden / muted
          if (currentMode === 'pomodoro' && !isBreak) {{
            showNotification('Focus session complete', 'Time for a break!');
          }} else if (currentMode === 'pomodoro' && isBreak) {{
            showNotification('Break over', 'Back to focus!');
          }} else {{
            showNotification('Focus session complete', 'Great work — XP awarded.');
          }}

          if (currentMode === 'pomodoro') {{

            if (!isBreak) {{

              // Work phase completed — save this session

              var phaseMinutes = Math.round((totalFocusSeconds - phaseStartFocusSeconds) / 60);

              if (phaseMinutes > 0) saveFocusSession(phaseMinutes);

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

            saveFocusTimerState();

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

            clearFocusTimerState();

            var el = document.getElementById('focus-float');

            if (el) el.style.display = 'none';

          }}

        }}



        async function saveFocusSession(overrideMinutes) {{

          var minutes = overrideMinutes !== undefined ? overrideMinutes : Math.round(totalFocusSeconds / 60);

          var pages = currentMode === 'pages' ? pageDone : 0;

          var course = document.getElementById('focus-course').value;

          // Dedupe by phaseId so the global widget controller doesn't also credit the same phase.

          try {{

            var ff = JSON.parse(localStorage.getItem('focus_float')||'null');

            if (ff && ff.phaseId) {{

              var saved = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');

              if (saved.indexOf(ff.phaseId) !== -1) {{

                // Already credited by global controller — just refresh stats.

                try {{

                  var r2 = await fetch('/api/student/focus/stats');

                  if (r2.ok) {{

                    var s = await r2.json();

                    if (s && s.stats) {{

                      document.getElementById('stat-hours').textContent = s.stats.total_hours;

                      document.getElementById('stat-sessions').textContent = s.stats.sessions;

                      document.getElementById('stat-pages').textContent = s.stats.total_pages;

                      document.getElementById('stat-streak').textContent = s.stats.streak_days;

                    }}

                  }}

                }} catch(e) {{}}

                return;

              }}

              saved.push(ff.phaseId);

              if (saved.length > 200) saved = saved.slice(-200);

              localStorage.setItem('focus_saved_phases', JSON.stringify(saved));

            }}

          }} catch(e) {{}}

          var payload = {{ mode: currentMode, minutes: minutes, pages: pages, course_name: course }};

          // Always fire-and-forget via sendBeacon FIRST so XP gets credited
          // even if the tab is hidden, throttled, or the user navigates away.
          // sendBeacon survives tab close and is not blocked by background throttling.
          try {{
            if (navigator.sendBeacon) {{
              var blob = new Blob([JSON.stringify(payload)], {{ type: 'application/json' }});
              navigator.sendBeacon('/api/student/focus/save', blob);
            }}
          }} catch(e) {{}}

          // Also fire a regular fetch so we can update the on-screen stats
          // (sendBeacon doesn't return a response).
          try {{

            var resp = await fetch('/api/student/focus/save', {{

              method: 'POST',

              headers: {{'Content-Type':'application/json'}},

              keepalive: true,

              body: JSON.stringify(payload)

            }});

            if (resp.ok) {{

              var result = await resp.json();

              if (result.stats) {{

                if (window.popNumber) {{

                  window.popNumber(document.getElementById('stat-hours'), result.stats.total_hours);

                  window.popNumber(document.getElementById('stat-sessions'), result.stats.sessions);

                  window.popNumber(document.getElementById('stat-pages'), result.stats.total_pages);

                  window.popNumber(document.getElementById('stat-streak'), result.stats.streak_days);

                }} else {{

                  document.getElementById('stat-hours').textContent = result.stats.total_hours;

                  document.getElementById('stat-sessions').textContent = result.stats.sessions;

                  document.getElementById('stat-pages').textContent = result.stats.total_pages;

                  document.getElementById('stat-streak').textContent = result.stats.streak_days;

                }}

              }}

              if (result.promotion && window.showPromotionToast) {{

                window.showPromotionToast(result.promotion);

              }}

            }}

          }} catch(e) {{}}

        }}



        function loadSpotify() {{

          var url = document.getElementById('spotify-url').value.trim();

          var match = url.match(/open\\.spotify\\.com\\/(playlist|album|track|episode|show)\\/([a-zA-Z0-9]+)/);

          if (!match) {{ alert('Paste a valid Spotify link'); return; }}

          setPlaylist(match[2], match[1]);

        }}



        function setPlaylist(id, type) {{

          type = type || 'playlist';

          var fullUrl = 'https://open.spotify.com/' + type + '/' + id;

          var embed = 'https://open.spotify.com/embed/' + type + '/' + id + '?utm_source=generator&theme=0';

          var inp = document.getElementById('spotify-url');

          if (inp) inp.value = fullUrl;

          try {{ localStorage.setItem('focus_input_spotify-url', fullUrl); }} catch(e) {{}}

          var ifr = document.getElementById('spotify-iframe');

          if (ifr) ifr.src = embed;

        }}



        // Keyboard shortcuts

        document.addEventListener('keydown', function(e) {{

          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

          if (e.code === 'Space') {{ e.preventDefault(); if (isRunning) pauseTimer(); else startTimer(); }}

          if (e.code === 'KeyR' && !e.ctrlKey) {{ e.preventDefault(); resetTimer(); }}

          if (e.code === 'KeyS' && !e.ctrlKey) {{ e.preventDefault(); skipPhase(); }}

          if (e.code === 'KeyP' && currentMode === 'pages') {{ e.preventDefault(); clickPage(); }}

        }});



        // Persist input field values across navigation + live-update timer display when idle

        (function wireFocusInputs() {{

          var ids = ['pomo-work','pomo-break','pomo-long','custom-mins','page-target','focus-course','spotify-url'];

          // Restore saved values first

          ids.forEach(function(id) {{

            var el = document.getElementById(id);

            if (!el) return;

            try {{

              var saved = localStorage.getItem('focus_input_' + id);

              if (saved !== null && saved !== '') el.value = saved;

            }} catch(e) {{}}

          }});

          // Recompute timer display from current inputs (only when idle)

          function liveUpdate() {{

            if (isRunning || sessionStarted) return;

            if (isBreak) return;

            var mins = null;

            if (currentMode === 'pomodoro') {{

              mins = parseInt(document.getElementById('pomo-work').value, 10);

            }} else if (currentMode === 'custom') {{

              var c = document.getElementById('custom-mins');

              if (c) mins = parseInt(c.value, 10);

            }}

            if (mins && mins > 0) {{

              timeLeft = mins * 60;

              totalTime = timeLeft;

              updateDisplay();

            }}

          }}

          ids.forEach(function(id) {{

            var el = document.getElementById(id);

            if (!el) return;

            el.addEventListener('input', function() {{

              try {{ localStorage.setItem('focus_input_' + id, el.value); }} catch(e) {{}}

              liveUpdate();

            }});

            el.addEventListener('change', function() {{

              try {{ localStorage.setItem('focus_input_' + id, el.value); }} catch(e) {{}}

              liveUpdate();

            }});

          }});

          // Apply restored values to timer display on load

          liveUpdate();

        }})();



        // Restore timer if it was running when user navigated away or switched tabs

        (function restoreTimer() {{

          var ff = JSON.parse(localStorage.getItem('focus_float') || 'null');

          var ts = JSON.parse(localStorage.getItem('focus_timer_state') || 'null');

          if (!ff || !ff.active || !ts) return;



          // Restore mode UI without calling setMode (which resets timer)

          currentMode = ts.currentMode;

          document.querySelectorAll('.mode-btn').forEach(function(b) {{ b.classList.remove('active'); b.classList.add('btn-outline'); }});

          var modeBtn = document.getElementById('mode-' + currentMode);

          if (modeBtn) {{ modeBtn.classList.add('active'); modeBtn.classList.remove('btn-outline'); }}

          document.getElementById('settings-pomodoro').style.display = currentMode === 'pomodoro' ? '' : 'none';

          document.getElementById('settings-pages').style.display = currentMode === 'pages' ? '' : 'none';

          document.getElementById('settings-custom').style.display = currentMode === 'custom' ? '' : 'none';

          document.getElementById('pomo-count').style.display = currentMode === 'pomodoro' ? '' : 'none';



          isBreak = ts.isBreak;

          pomoCount = ts.pomoCount;

          phaseStartFocusSeconds = ts.phaseStartFocusSeconds;

          totalTime = ts.totalTime;

          sessionStarted = true;

          if (ts.course) document.getElementById('focus-course').value = ts.course;

          // The global focus controller may have advanced the phase while the

          // user was on another tab — sync local state from focus_float.

          if (typeof ff.isBreak === 'boolean') isBreak = ff.isBreak;

          if (ff.course) document.getElementById('focus-course').value = ff.course;



          if (ff.mode === 'countdown') {{

            var msLeft = ff.endAt - Date.now();

            if (msLeft > 0) {{

              timeLeft = Math.ceil(msLeft / 1000);

              var elapsedInPhase = ts.totalTime - timeLeft;

              if (!isBreak) {{

                totalFocusSeconds = ts.phaseStartFocusSeconds + elapsedInPhase;

              }} else {{

                totalFocusSeconds = ts.totalFocusSeconds;

              }}

              if (currentMode === 'pomodoro') {{

                document.getElementById('pomo-count').textContent = isBreak

                  ? 'Completed ' + pomoCount + ' of 4'

                  : 'Session ' + (pomoCount + 1) + ' of 4';

              }}

              updateDisplay();

              startTimer(true);

            }} else {{

              // Timer ended while user was away

              if (!isBreak) {{

                totalFocusSeconds = ts.phaseStartFocusSeconds + ts.totalTime;

              }} else {{

                totalFocusSeconds = ts.totalFocusSeconds;

              }}

              timeLeft = 0;

              updateDisplay();

              onTimerEnd();

            }}

          }} else if (ff.mode === 'stopwatch') {{

            var elapsed = Math.floor((Date.now() - ff.startAt) / 1000);

            totalFocusSeconds = elapsed;

            updateDisplay();

            startTimer(true);

          }}

        }})();



        /* === Focus Guard status badge === */

        (function() {{

          function readActive() {{

            try {{

              var ff = localStorage.getItem('focus_float');

              if (!ff) return false;

              var d = JSON.parse(ff);

              return !!(d && d.active);

            }} catch(e) {{ return false; }}

          }}

          function render() {{

            var s = document.getElementById('fg-status');

            var l = document.getElementById('fg-label');

            if (!s || !l) return;

            if (readActive()) {{

              s.classList.add('active');

              l.textContent = 'Active — sites blocked';

            }} else {{

              s.classList.remove('active');

              l.textContent = 'Idle';

            }}

          }}

          render();

          setInterval(render, 1500);

          window.addEventListener('storage', function(e) {{ if (e.key === 'focus_float') render(); }});

        }})();

        </script>

        """, active_page="student_focus")



    # ── GPA Calculator page ─────────────────────────────────



    @app.route("/student/gpa")

    def student_gpa_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        prefs = sdb.get_email_prefs(_cid())

        saved_country = prefs.get("gpa_country", "us") if prefs else "us"



        course_rows = ""

        for c in courses:

            course_rows += f"""<div class="gpa-row" style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">

              <input type="text" value="{_esc(c['name'])}" class="edit-input" style="flex:2;" placeholder="Course name">

              <input type="number" value="3" class="edit-input" style="width:70px;" min="1" max="10" placeholder="Credits">

              <select class="edit-input grade-select" style="width:100px;"></select>

              <button onclick="this.parentElement.remove();calcGPA();" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>

            </div>"""



        return _s_render("GPA Calculator", f"""

        <h1 style="margin-bottom:20px;">&#127891; GPA Calculator</h1>

        <div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;">

          <div class="card">

            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">

              <h2>&#128218; Courses</h2>

              <div style="display:flex;gap:8px;align-items:center;">

                <select id="country-select" class="edit-input" style="width:180px;" onchange="changeCountry(this.value)">

                  <option value="us" {"selected" if saved_country == "us" else ""}>&#127482;&#127480; United States (4.0)</option>

                  <option value="uk" {"selected" if saved_country == "uk" else ""}>&#127468;&#127463; United Kingdom</option>

                  <option value="mx" {"selected" if saved_country == "mx" else ""}>&#127474;&#127485; Mexico (0-10)</option>

                  <option value="ar" {"selected" if saved_country == "ar" else ""}>&#127462;&#127479; Argentina (0-10)</option>

                  <option value="co" {"selected" if saved_country == "co" else ""}>&#127464;&#127476; Colombia (0-5)</option>

                  <option value="cl" {"selected" if saved_country == "cl" else ""}>&#127464;&#127473; Chile (1-7)</option>

                  <option value="br" {"selected" if saved_country == "br" else ""}>&#127463;&#127479; Brazil (0-10)</option>

                  <option value="de" {"selected" if saved_country == "de" else ""}>&#127465;&#127466; Germany (1-6)</option>

                  <option value="fr" {"selected" if saved_country == "fr" else ""}>&#127467;&#127479; France (0-20)</option>

                  <option value="es" {"selected" if saved_country == "es" else ""}>&#127466;&#127480; Spain (0-10)</option>

                  <option value="in" {"selected" if saved_country == "in" else ""}>&#127470;&#127475; India (10-point)</option>

                  <option value="au" {"selected" if saved_country == "au" else ""}>&#127462;&#127482; Australia (7-point)</option>

                  <option value="jp" {"selected" if saved_country == "jp" else ""}>&#127471;&#127477; Japan (S-F)</option>

                </select>

                <button onclick="addGPARow()" class="btn btn-outline btn-sm">+ Add Course</button>

              </div>

            </div>

            <div style="display:flex;gap:8px;margin-bottom:8px;font-size:12px;font-weight:600;color:var(--text-muted);padding:0 4px;">

              <span style="flex:2;">Course</span><span style="width:70px;">Credits</span><span style="width:100px;">Grade</span><span style="width:20px;"></span>

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

              <div id="gpa-scale" style="font-size:14px;color:var(--text-muted);">-</div>

              <div id="gpa-credits" style="font-size:13px;color:var(--text-muted);margin-top:8px;"></div>

            </div>

            <div class="card" style="margin-top:16px;">

              <div class="card-header"><h2>&#128200; What-If</h2></div>

              <p style="font-size:13px;color:var(--text-muted);">Enter your current cumulative GPA to see how this semester affects it:</p>

              <div style="display:flex;gap:8px;margin-top:8px;">

                <div class="form-group" style="flex:1;">

                  <label style="font-size:12px;">Current GPA</label>

                  <input type="number" id="cum-gpa" step="0.01" min="0" max="20" value="0" class="edit-input">

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

        var gradingSystems = {{

          us: {{ name: 'United States', scale: 'out of 4.0', max: 4.0, grades: [

            ['A+',4.0],['A',4.0],['A-',3.7],['B+',3.3],['B',3.0],['B-',2.7],['C+',2.3],['C',2.0],['C-',1.7],['D+',1.3],['D',1.0],['F',0.0]

          ]}},

          uk: {{ name: 'United Kingdom', scale: 'classification', max: 4.0, grades: [

            ['1st (70+)',4.0],['2:1 (60-69)',3.3],['2:2 (50-59)',2.5],['3rd (40-49)',1.5],['Fail (<40)',0.0]

          ]}},

          mx: {{ name: 'Mexico', scale: 'out of 10', max: 10, grades: [

            ['10',10],['9',9],['8',8],['7',7],['6',6],['5 (Fail)',5],['NA',0]

          ]}},

          ar: {{ name: 'Argentina', scale: 'out of 10', max: 10, grades: [

            ['10',10],['9',9],['8',8],['7',7],['6',6],['5',5],['4 (Fail)',4],['3',3],['2',2],['1',1]

          ]}},

          co: {{ name: 'Colombia', scale: 'out of 5.0', max: 5.0, grades: [

            ['5.0',5.0],['4.5',4.5],['4.0',4.0],['3.5',3.5],['3.0',3.0],['2.5',2.5],['2.0',2.0],['1.0',1.0],['0',0]

          ]}},

          cl: {{ name: 'Chile', scale: 'out of 7.0', max: 7.0, grades: [

            ['7.0',7.0],['6.5',6.5],['6.0',6.0],['5.5',5.5],['5.0',5.0],['4.5',4.5],['4.0',4.0],['3.5',3.5],['3.0',3.0],['2.0',2.0],['1.0',1.0]

          ]}},

          br: {{ name: 'Brazil', scale: 'out of 10', max: 10, grades: [

            ['10',10],['9',9],['8',8],['7',7],['6',6],['5',5],['4 (Fail)',4],['3',3],['2',2],['1',1],['0',0]

          ]}},

          de: {{ name: 'Germany', scale: '1.0 (best) to 5.0 (worst)', max: 5.0, inverted: true, grades: [

            ['1.0 (sehr gut)',1.0],['1.3',1.3],['1.7',1.7],['2.0 (gut)',2.0],['2.3',2.3],['2.7',2.7],['3.0 (befriedigend)',3.0],['3.3',3.3],['3.7',3.7],['4.0 (ausreichend)',4.0],['5.0 (fail)',5.0]

          ]}},

          fr: {{ name: 'France', scale: 'out of 20', max: 20, grades: [

            ['20',20],['19',19],['18',18],['17',17],['16',16],['15',15],['14',14],['13',13],['12',12],['11',11],['10',10],['9 (Fail)',9],['8',8],['5',5],['0',0]

          ]}},

          es: {{ name: 'Spain', scale: 'out of 10', max: 10, grades: [

            ['MH (10)',10],['SB (9)',9],['NT (8)',8],['NT (7)',7],['AP (6)',6],['AP (5)',5],['SS (4)',4],['SS (3)',3],['SS (0)',0]

          ]}},

          'in': {{ name: 'India', scale: 'out of 10', max: 10, grades: [

            ['O (10)',10],['A+ (9)',9],['A (8)',8],['B+ (7)',7],['B (6)',6],['C (5)',5],['P (4)',4],['F (0)',0]

          ]}},

          au: {{ name: 'Australia', scale: 'out of 7.0', max: 7.0, grades: [

            ['HD (7)',7],['D (6)',6],['CR (5)',5],['P (4)',4],['F (0)',0]

          ]}},

          jp: {{ name: 'Japan', scale: 'out of 4.0', max: 4.0, grades: [

            ['S (4)',4.0],['A (3)',3.0],['B (2)',2.0],['C (1)',1.0],['F (0)',0.0]

          ]}}

        }};



        var currentCountry = '{saved_country}';



        function getGradeOptions(country) {{

          var sys = gradingSystems[country];

          if (!sys) return '';

          return sys.grades.map(function(g, i) {{

            return '<option value="' + g[1] + '"' + (i === 3 ? ' selected' : '') + '>' + g[0] + '</option>';

          }}).join('');

        }}



        function populateGradeSelects() {{

          var opts = getGradeOptions(currentCountry);

          document.querySelectorAll('.grade-select').forEach(function(sel) {{

            sel.innerHTML = opts;

          }});

          var sys = gradingSystems[currentCountry];

          document.getElementById('gpa-scale').textContent = sys ? sys.scale : '';

        }}



        function changeCountry(country) {{

          currentCountry = country;

          populateGradeSelects();

          calcGPA();

          fetch('/api/student/settings/gpa-country', {{

            method:'POST', headers:{{'Content-Type':'application/json'}},

            body:JSON.stringify({{country:country}})

          }});

        }}



        function addGPARow() {{

          var c = document.getElementById('gpa-rows');

          var div = document.createElement('div');

          div.className = 'gpa-row';

          div.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;align-items:center;';

          div.innerHTML = '<input type="text" class="edit-input" style="flex:2;" placeholder="Course name">'

            + '<input type="number" class="edit-input" style="width:70px;" value="3" min="1" max="10" placeholder="Credits">'

            + '<select class="edit-input grade-select" style="width:100px;">' + getGradeOptions(currentCountry) + '</select>'

            + '<button onclick="this.parentElement.remove();calcGPA();" style="background:none;border:none;color:var(--red);cursor:pointer;">&#10005;</button>';

          c.appendChild(div);

        }}



        function calcGPA() {{

          var rows = document.querySelectorAll('#gpa-rows .gpa-row');

          var totalPts = 0, totalCreds = 0;

          var sys = gradingSystems[currentCountry];

          rows.forEach(function(row) {{

            var inputs = row.querySelectorAll('input');

            var sel = row.querySelector('select');

            var credits = parseFloat(inputs[1].value) || 0;

            var grade = parseFloat(sel.value);

            if (sys && sys.inverted) {{

              // German system: lower is better, invert for weighted calc

              totalPts += credits * (sys.max + 1 - grade);

              totalCreds += credits;

            }} else {{

              totalPts += credits * grade;

              totalCreds += credits;

            }}

          }});

          var gpa;

          if (sys && sys.inverted) {{

            gpa = totalCreds > 0 ? (sys.max + 1 - totalPts / totalCreds).toFixed(2) : '0.00';

          }} else {{

            gpa = totalCreds > 0 ? (totalPts / totalCreds).toFixed(2) : '0.00';

          }}

          document.getElementById('gpa-result').textContent = gpa;

          document.getElementById('gpa-credits').textContent = totalCreds + ' credits this semester';

          var g = parseFloat(gpa);

          var maxG = sys ? sys.max : 4.0;

          if (sys && sys.inverted) {{

            document.getElementById('gpa-result').style.color = g <= 1.5 ? 'var(--green)' : g <= 2.5 ? 'var(--primary)' : g <= 3.5 ? '#F59E0B' : 'var(--red)';

          }} else {{

            var ratio = g / maxG;

            document.getElementById('gpa-result').style.color = ratio >= 0.85 ? 'var(--green)' : ratio >= 0.65 ? 'var(--primary)' : ratio >= 0.5 ? '#F59E0B' : 'var(--red)';

          }}

        }}



        function calcCumGPA() {{

          calcGPA();

          var semGPA = parseFloat(document.getElementById('gpa-result').textContent) || 0;

          var semRows = document.querySelectorAll('#gpa-rows .gpa-row');

          var semCredits = 0;

          semRows.forEach(function(row) {{ semCredits += parseFloat(row.querySelectorAll('input')[1].value) || 0; }});

          var cumGPA = parseFloat(document.getElementById('cum-gpa').value) || 0;

          var cumCredits = parseInt(document.getElementById('cum-credits').value) || 0;

          var sys = gradingSystems[currentCountry];

          var totalPts, totalCreds, result;

          if (sys && sys.inverted) {{

            totalPts = (((sys.max + 1 - cumGPA) * cumCredits) + ((sys.max + 1 - semGPA) * semCredits));

            totalCreds = cumCredits + semCredits;

            result = totalCreds > 0 ? (sys.max + 1 - totalPts / totalCreds).toFixed(2) : '0.00';

          }} else {{

            totalPts = (cumGPA * cumCredits) + (semGPA * semCredits);

            totalCreds = cumCredits + semCredits;

            result = totalCreds > 0 ? (totalPts / totalCreds).toFixed(2) : '0.00';

          }}

          document.getElementById('cum-result').textContent = 'Cumulative GPA: ' + result;

        }}



        populateGradeSelects();

        calcGPA();

        </script>

        """, active_page="student_gpa")



    # ── Practice Problems (STEM/Math) ───────────────────────



    @app.route("/student/practice")

    def student_practice_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        course_options = '<option value="">Select a course...</option>'

        for c in courses:

            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'



        return _s_render("Practice Problems", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">

          <div>

            <h1 style="margin:0;">&#128736; Practice Problems</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">AI-generated problems with step-by-step solutions — perfect for math &amp; STEM</p>

          </div>

          <button onclick="document.getElementById('gen-form').style.display=document.getElementById('gen-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Problems</button>

        </div>



        <div id="gen-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">

          <h3 style="margin:0 0 14px;">Generate Practice Problems</h3>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">

            <div class="form-group">

              <label>Course</label>

              <select id="prac-course" class="edit-input">{course_options}</select>

            </div>

            <div class="form-group">

              <label>Topic (optional)</label>

              <input type="text" id="prac-topic" class="edit-input" placeholder="e.g. Improper integrals, derivatives...">

            </div>

            <div class="form-group">

              <label>Difficulty</label>

              <select id="prac-diff" class="edit-input">

                <option value="easy">Easy</option>

                <option value="medium" selected>Medium</option>

                <option value="hard">Hard</option>

              </select>

            </div>

            <div class="form-group">

              <label>Number of problems</label>

              <select id="prac-count" class="edit-input">

                <option value="3">3</option>

                <option value="5" selected>5</option>

                <option value="8">8</option>

                <option value="10">10</option>

              </select>

            </div>

          </div>

          <button onclick="genProblems()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="gen-btn">&#10024; Generate</button>

        </div>



        <div id="problems-area"></div>



        <style>

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .problem-card {{ background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:16px; }}

        .problem-card h3 {{ margin:0 0 12px;font-size:16px;color:var(--primary); }}

        .problem-text {{ font-size:15px;line-height:1.7;color:var(--text); }}

        .solution-area {{ margin-top:12px;padding:16px;background:var(--bg);border-radius:var(--radius-sm);border-left:4px solid var(--primary); }}

        .solution-area h4 {{ margin:0 0 8px;font-size:14px;color:var(--primary); }}

        .solution-text {{ font-size:14px;line-height:1.8;color:var(--text);white-space:pre-wrap; }}

        .answer-box {{ display:inline-block;margin-top:8px;padding:6px 14px;background:var(--primary);color:#fff;border-radius:var(--radius-sm);font-weight:600;font-size:14px; }}

        </style>

        <script>

        async function genProblems() {{

          var courseId = document.getElementById('prac-course').value;

          if (!courseId) {{ alert('Select a course'); return; }}

          var btn = document.getElementById('gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating problems...';

          try {{

            var r = await fetch('/api/student/practice/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{

                course_id: parseInt(courseId),

                topic: document.getElementById('prac-topic').value,

                difficulty: document.getElementById('prac-diff').value,

                count: parseInt(document.getElementById('prac-count').value)

              }})

            }});

            var d = await _safeJson(r);

            if (r.ok && d.problems) {{

              renderProblems(d.problems);

            }} else {{ alert(d.error || 'Generation failed'); }}

          }} catch(e) {{ alert('Network error'); }}

          btn.disabled = false; btn.innerHTML = '&#10024; Generate';

        }}



        function renderProblems(problems) {{

          var html = '';

          problems.forEach(function(p, i) {{

            html += '<div class="problem-card">'

              + '<h3>Problem ' + (i+1) + '</h3>'

              + '<div class="problem-text">' + escHtml(p.problem) + '</div>'

              + '<button onclick="toggleSol(' + i + ')" class="btn btn-outline btn-sm" style="margin-top:12px;" id="sol-btn-' + i + '">&#128161; Show Solution</button>'

              + '<div id="sol-' + i + '" style="display:none;">'

              + '<div class="solution-area">'

              + '<h4>Step-by-step Solution</h4>'

              + '<div class="solution-text">' + escHtml(p.solution) + '</div>'

              + '</div>'

              + '<div style="margin-top:8px;"><span class="answer-box">Answer: ' + escHtml(p.answer) + '</span></div>'

              + '</div></div>';

          }});

          document.getElementById('problems-area').innerHTML = html;

          // Render math

          if (typeof renderMathInElement === 'function') {{

            renderMathInElement(document.getElementById('problems-area'), {{

              delimiters: [

                {{left:'$$',right:'$$',display:true}},

                {{left:'$',right:'$',display:false}},

                {{left:'\\\\(',right:'\\\\)',display:false}},

                {{left:'\\\\[',right:'\\\\]',display:true}}

              ], throwOnError: false

            }});

          }}

        }}



        function toggleSol(i) {{

          var el = document.getElementById('sol-' + i);

          var btn = document.getElementById('sol-btn-' + i);

          if (el.style.display === 'none') {{

            el.style.display = 'block';

            btn.innerHTML = '&#128064; Hide Solution';

            if (typeof renderMathInElement === 'function') {{

              renderMathInElement(el, {{

                delimiters: [

                  {{left:'$$',right:'$$',display:true}},

                  {{left:'$',right:'$',display:false}}

                ], throwOnError: false

              }});

            }}

          }} else {{

            el.style.display = 'none';

            btn.innerHTML = '&#128161; Show Solution';

          }}

        }}



        function escHtml(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}

        </script>

        """, active_page="student_practice")



    @app.route("/api/student/practice/generate", methods=["POST"])

    @limiter.limit("5 per minute")

    def student_generate_practice():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        source_text = ""

        files = sdb.get_course_files(_cid(), course_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course. Please upload your study material first."}), 400



        problems = generate_practice_problems(

            course_name=course["name"],

            topic=data.get("topic", ""),

            difficulty=data.get("difficulty", "medium"),

            count=min(int(data.get("count", 5)), 10),

            source_text=source_text,

        )

        if not problems:

            return jsonify({"error": "Failed to generate problems. Try again."}), 500

        return jsonify({"problems": problems})



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

            var d = await _safeJson(r);

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

    @csrf.exempt

    def student_set_schedule():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        try:

            data = request.get_json(force=True) or {}

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

        except Exception as e:

            import logging, traceback

            logging.getLogger("student.routes").error("schedule save failed: %s\n%s", e, traceback.format_exc())

            return jsonify({"error": f"Could not save schedule: {str(e)[:120]}"}), 500



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

            var csrfMeta = document.querySelector('meta[name="csrf-token"]');

            var csrfTok = csrfMeta ? csrfMeta.content : '';

            var r = await fetch('/api/student/schedule', {{

              method: 'PUT',

              headers: {{'Content-Type':'application/json', 'X-CSRFToken': csrfTok}},

              body: JSON.stringify({{ schedule: schedule }})

            }});

            if (r.ok) {{

              btn.innerHTML = '&#10003; Saved!';

              setTimeout(function() {{ btn.disabled = false; btn.innerHTML = 'Save Schedule'; }}, 1500);

            }} else {{

              var errTxt = 'Failed to save';

              try {{ var j = await r.json(); if (j.error) errTxt = j.error; }} catch(e) {{}}

              alert(errTxt); btn.disabled = false; btn.innerHTML = 'Save Schedule';

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

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])

        exam_id = data.get("exam_id")

        count = min(int(data.get("count", 15)), 100)



        # Drag-and-drop / paste path: caller supplied raw source text — no course needed.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            cards = generate_flashcards(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

                count=count,

            )

            if not cards:

                return jsonify({"error": "Failed to generate flashcards. Try again."}), 500

            title = ad_hoc_title or f"Flashcards: {course_name}"

            deck_id = sdb.create_flashcard_deck(_cid(), title, course_id=course_id or None,

                                                exam_id=exam_id, source_type="drop")

            sdb.add_flashcards(deck_id, cards)

            return jsonify({"deck_id": deck_id, "card_count": len(cards)})



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        # Gather source material — ONLY from student's uploaded files

        source_text = ""

        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        # Also include AI-generated notes for this course

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course/exam. Please upload your study material first."}), 400



        cards = generate_flashcards(

            course_name=course["name"],

            topics=topics or None,

            source_text=source_text,

            count=count,

        )

        if not cards:

            return jsonify({"error": "Failed to generate flashcards. Try again."}), 500



        title = ad_hoc_title or f"Flashcards: {course['name']}"

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

        quality = data.get("quality")  # 0-5 for SRS mode

        correct = data.get("correct", False)

        if quality is not None:

            quality = max(0, min(5, int(quality)))

            correct = quality >= 3

        sdb.update_flashcard_progress(data["card_id"], correct, quality=quality)

        # XP is only awarded from Focus Mode and Exchange notes usage.

        # Check flashcard badges

        from outreach.db import _fetchval, get_db

        with get_db() as db:

            fc_count = _fetchval(db, "SELECT COUNT(*) FROM student_xp WHERE client_id = %s AND action = 'flashcard_review'", (_cid(),)) or 0

        for key, threshold in [("flashcard_fan", 100), ("flashcard_500", 500), ("flashcard_1000", 1000)]:

            if fc_count >= threshold:

                sdb.earn_badge(_cid(), key)

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

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])

        exam_id = data.get("exam_id")

        difficulty = data.get("difficulty", "medium")

        if difficulty not in ("easy", "medium", "hard"):

            difficulty = "medium"

        try:

            count = int(data.get("count", 10))

        except (TypeError, ValueError):

            count = 10

        count = max(1, min(count, 100))  # hard ceiling — generation batches under the hood



        # Drag-and-drop / paste path: caller supplied raw source text — no course needed.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            questions = generate_quiz(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

                difficulty=difficulty,

                count=count,

            )

            if not questions:

                return jsonify({"error": "Failed to generate quiz. Try again."}), 500

            title = ad_hoc_title or f"Quiz: {course_name} ({difficulty})"

            quiz_id = sdb.create_quiz(_cid(), title, difficulty, course_id=course_id or None, exam_id=exam_id)

            sdb.add_quiz_questions(quiz_id, questions)

            return jsonify({

                "quiz_id": quiz_id,

                "question_count": len(questions),

                "requested": count,

                "short": len(questions) < count,

            })



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        # Gather source material — ONLY from student's uploaded files

        source_text = ""

        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        # Also include AI-generated notes

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course/exam. Please upload your study material first."}), 400



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



        return jsonify({

            "quiz_id": quiz_id,

            "question_count": len(questions),

            "requested": count,

            "short": len(questions) < count,

        })



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

        # XP is only awarded from Focus Mode and Exchange notes usage.

        if score == 100:

            sdb.earn_badge(_cid(), "quiz_master")

        if not sdb.get_badges(_cid()) or not any(b["badge_key"] == "first_quiz" for b in sdb.get_badges(_cid())):

            sdb.earn_badge(_cid(), "first_quiz")

        # Check quiz_10 badge

        from outreach.db import _fetchval, get_db

        with get_db() as db:

            quiz_count = _fetchval(db, "SELECT COUNT(*) FROM student_quizzes WHERE client_id = %s AND attempts > 0", (_cid(),)) or 0

        if quiz_count >= 10:

            sdb.earn_badge(_cid(), "quiz_10")

        if quiz_count >= 25:

            sdb.earn_badge(_cid(), "quiz_25")

        if quiz_count >= 50:

            sdb.earn_badge(_cid(), "quiz_50")

        return jsonify({"ok": True})



    @app.route("/api/student/quizzes/<int:quiz_id>", methods=["DELETE"])

    def student_delete_quiz(quiz_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_quiz(quiz_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/quizzes/<int:quiz_id>/analyze", methods=["POST"])

    @limiter.limit("10 per minute")

    def student_analyze_quiz(quiz_id):

        """

        Deep post-quiz AI analysis. Takes the user's per-question answers +

        per-question time-taken and returns topic breakdown, strengths,

        weaknesses, mistake patterns, and a prioritized action plan.

        """

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        quiz = sdb.get_quiz(quiz_id, _cid())

        if not quiz:

            return jsonify({"error": "Not found"}), 404



        data = request.get_json(force=True) or {}

        answers = data.get("answers", [])  # [{q_id, selected, time}]

        if not answers:

            return jsonify({"error": "No answers provided"}), 400



        questions = {q["id"]: dict(q) for q in sdb.get_quiz_questions(quiz_id)}



        # Build structured payload for the AI

        items = []

        topic_stats: dict[str, dict] = {}

        correct_count = 0

        total_time = 0.0

        times: list[float] = []



        for a in answers:

            q = questions.get(int(a.get("q_id", -1)))

            if not q:

                continue

            sel = (a.get("selected") or "").lower()

            is_correct = sel == q["correct"]

            if is_correct:

                correct_count += 1

            t = float(a.get("time", 0) or 0)

            total_time += t

            times.append(t)

            topic = (q.get("topic") or "General").strip() or "General"

            ts = topic_stats.setdefault(topic, {"correct": 0, "total": 0, "time": 0.0})

            ts["total"] += 1

            ts["time"] += t

            if is_correct:

                ts["correct"] += 1

            items.append({

                "topic": topic,

                "question": q["question"],

                "your_answer": sel.upper() if sel else "—",

                "your_answer_text": q.get(f"option_{sel}", "") if sel in ("a", "b", "c", "d") else "",

                "correct_answer": q["correct"].upper(),

                "correct_answer_text": q.get(f"option_{q['correct']}", ""),

                "explanation": q.get("explanation", ""),

                "is_correct": is_correct,

                "seconds": round(t, 1),

            })



        total = len(items)

        score_pct = round(100 * correct_count / total) if total else 0

        avg_time = round(total_time / total, 1) if total else 0.0



        # Topic breakdown (deterministic, not AI)

        breakdown = []

        for topic, s in topic_stats.items():

            pct = round(100 * s["correct"] / s["total"]) if s["total"] else 0

            breakdown.append({

                "topic": topic,

                "correct": s["correct"],

                "total": s["total"],

                "percent": pct,

                "avg_time": round(s["time"] / s["total"], 1) if s["total"] else 0,

            })

        breakdown.sort(key=lambda x: (x["percent"], -x["total"]))



        # AI narrative analysis

        try:

            import json as _json

            from outreach.ai import _ai

            wrong_items = [i for i in items if not i["is_correct"]]

            right_items = [i for i in items if i["is_correct"]]

            wrong_sample = wrong_items[:25]

            right_sample = right_items[:10]

            prompt = f"""You are an elite tutor analyzing a student's quiz results.

Course: {quiz.get('course_name') or 'General'}

Quiz: {quiz.get('title') or ''}

Difficulty: {quiz.get('difficulty') or 'medium'}

Overall score: {score_pct}% ({correct_count}/{total})

Average time per question: {avg_time}s



TOPIC BREAKDOWN (what they got in each concept):

{_json.dumps(breakdown, indent=2)}



QUESTIONS THEY GOT WRONG:

{_json.dumps(wrong_sample, indent=2, default=str)}



QUESTIONS THEY GOT RIGHT (sample):

{_json.dumps(right_sample, indent=2, default=str)}



Produce a student-facing analysis that is blunt, specific, and actionable — BETTER than Gemini's quiz feedback.

Return ONLY valid JSON with this exact shape:

{{

  "headline": "One sharp sentence summarizing performance (no fluff).",

  "verdict": "mastery" | "solid" | "shaky" | "struggling",

  "strengths": [

    {{"topic": "Short concept name", "detail": "Why they clearly understand this, citing a question if useful"}}

  ],

  "weaknesses": [

    {{"topic": "Short concept name", "detail": "What exactly they're missing — specific misconception, not generic advice",

      "fix": "One concrete fix: re-read X, practice Y, watch Z-type resource"}}

  ],

  "mistake_patterns": ["Pattern 1 (e.g. 'confuses mitosis phases', 'guesses on calculations')"],

  "time_insight": "Short note on their pacing — too fast/slow, where they rushed, etc.",

  "next_actions": [

    "Concrete step 1 (e.g. 'Redo questions 3, 7, 12 — all about X')",

    "Concrete step 2",

    "Concrete step 3"

  ],

  "study_plan_30min": [

    {{"minutes": 10, "task": "Specific focused task"}},

    {{"minutes": 15, "task": "Specific focused task"}},

    {{"minutes": 5,  "task": "Specific focused task"}}

  ],

  "encouragement": "One honest, specific, non-patronizing sentence."

}}



RULES:

- 3-6 strengths max. 3-6 weaknesses max. Skip sections if genuinely empty (empty arrays fine).

- Quote specific topics / question numbers when relevant.

- Never say "review the material" — be concrete.

- Keep it in the same language as the quiz content.

No markdown, no code fences. ONLY JSON.

"""

            try:

                resp = _ai().chat.completions.create(

                    model="gpt-4o-mini",

                    messages=[{"role": "user", "content": prompt}],

                    temperature=0.4,

                    max_tokens=2000,

                    response_format={"type": "json_object"},

                )

            except Exception:

                resp = _ai().chat.completions.create(

                    model="gpt-4o-mini",

                    messages=[{"role": "user", "content": prompt}],

                    temperature=0.4,

                    max_tokens=2000,

                )

            raw = (resp.choices[0].message.content or "").strip()

            raw = re.sub(r"^```json?\s*", "", raw, flags=re.IGNORECASE)

            raw = re.sub(r"\s*```$", "", raw)

            try:

                ai = _json.loads(raw)

            except Exception:

                ai = {"headline": "Quiz analyzed.", "verdict": "solid", "strengths": [],

                      "weaknesses": [], "mistake_patterns": [], "time_insight": "",

                      "next_actions": [], "study_plan_30min": [], "encouragement": ""}

        except Exception as e:

            log.error("Quiz analysis failed: %s", e)

            ai = {"headline": "Analysis unavailable.", "verdict": "solid", "strengths": [],

                  "weaknesses": [], "mistake_patterns": [], "time_insight": "",

                  "next_actions": [], "study_plan_30min": [], "encouragement": ""}



        # Simple pacing stats

        pacing = {

            "avg_time": avg_time,

            "fastest": round(min(times), 1) if times else 0,

            "slowest": round(max(times), 1) if times else 0,

            "total_time": round(total_time, 1),

        }



        return jsonify({

            "score": score_pct,

            "correct": correct_count,

            "total": total,

            "breakdown": breakdown,

            "pacing": pacing,

            "items": items,

            "ai": ai,

        })



    # ── Notes API routes ────────────────────────────────────



    @app.route("/api/student/notes/generate", methods=["POST"])

    @limiter.limit("5 per minute")

    def student_generate_notes():

        """Generate AI study notes for a course."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])



        # Drag-and-drop / paste path: caller supplied raw source text — no course required.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            result = generate_notes(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

            )

            if not result.get("content_html"):

                return jsonify({"error": "Failed to generate notes. Try again."}), 500

            title = ad_hoc_title or result.get("title") or f"Notes: {course_name}"

            note_id = sdb.create_note(_cid(), title, result["content_html"],

                                      course_id=course_id or None,

                                      source_type="drop")

            return jsonify({"note_id": note_id, "title": title})



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})



        source_text = ""

        files = sdb.get_course_files(_cid(), course_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f["extracted_text"] + "\n\n"



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

        # Check note badges

        all_notes = sdb.get_notes(_cid())

        nc = len(all_notes) if all_notes else 0

        for key, threshold in [("note_taker", 10), ("note_taker_25", 25), ("note_taker_50", 50)]:

            if nc >= threshold:

                sdb.earn_badge(_cid(), key)

        return jsonify({"note_id": note_id, "title": result["title"]})



    @app.route("/api/student/notes/upload-pdf", methods=["POST"])

    @limiter.limit("10 per minute")

    def student_upload_pdf_note():

        """Upload a PDF/DOCX and create a note from extracted text."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        if "file" not in request.files:

            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]

        if not f.filename:

            return jsonify({"error": "Empty filename"}), 400

        fname = f.filename

        fl = fname.lower()

        if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc")):

            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

        content = f.read(50 * 1024 * 1024 + 1)

        if len(content) > 50 * 1024 * 1024:

            return jsonify({"error": "File too large (max 50MB)"}), 400

        text = ""

        try:

            if fl.endswith(".pdf"):

                text = extract_text_from_pdf(content)

            elif fl.endswith((".docx", ".doc")):

                text = extract_text_from_docx(content)

        except Exception as e:

            return jsonify({"error": f"Could not extract text: {e}"}), 400

        if not text or len(text.strip()) < 20:

            return jsonify({"error": "Could not extract enough readable text from this file"}), 400

        title = fname.rsplit(".", 1)[0]

        import re as _re

        # Clean null bytes and collapse extra whitespace within lines

        text = text.replace('\x00', '')

        text = _re.sub(r'[^\S\n]+', ' ', text)

        # Collapse 3+ consecutive blank lines to 2

        text = _re.sub(r'\n{3,}', '\n\n', text)

        # Build HTML with structure detection

        html = "<h1>" + _esc(title) + "</h1>\n"

        lines = text.split("\n")

        for line in lines:

            p = line.strip()

            if not p:

                continue

            # Detect heading-like lines

            if len(p) < 120 and not p.endswith('.') and (p[0].isupper() or p[0].isdigit()):

                words = p.split()

                if len(words) <= 14 and any(c.isalpha() for c in p):

                    if _re.match(r'^(\d+[\.\)]\s*|Cap[ií]tulo|Secci[oó]n|Definici[oó]n|Teorema|Proposici[oó]n|Ejemplo|Lema|Corolario|Observaci[oó]n)', p, _re.IGNORECASE):

                        html += "<h2>" + _esc(p) + "</h2>\n"

                        continue

                    elif _re.match(r'^\d+\.\d+', p):

                        html += "<h3>" + _esc(p) + "</h3>\n"

                        continue

            html += "<p>" + _esc(p) + "</p>\n"

        note_id = sdb.create_note(_cid(), title, html, source_type="pdf-upload")

        # Check note badges

        all_notes = sdb.get_notes(_cid())

        nc = len(all_notes) if all_notes else 0

        for key, threshold in [("note_taker", 10), ("note_taker_25", 25), ("note_taker_50", 50)]:

            if nc >= threshold:

                sdb.earn_badge(_cid(), key)

        return jsonify({"note_id": note_id, "title": title})



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



    @app.route("/api/student/notes/<int:note_id>/pdf")

    def student_export_note_pdf(note_id):

        """Export a note as PDF."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        note = sdb.get_note(note_id, _cid())

        if not note:

            return jsonify({"error": "Not found"}), 404

        from io import BytesIO

        import re as _re

        from fpdf import FPDF



        title = note.get("title", "Note")

        html = note.get("content_html", "")

        # Strip HTML tags for clean text, preserve structure

        def strip_html(s):

            s = _re.sub(r'<br\s*/?>', '\n', s)

            s = _re.sub(r'</p>|</div>|</li>|</h[1-6]>', '\n', s)

            s = _re.sub(r'<[^>]+>', '', s)

            s = s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

            s = s.replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')

            s = s.replace('&middot;', '·')

            return s.strip()



        # Extract structured blocks from HTML

        blocks = []

        parts = _re.split(r'(<h[1-3][^>]*>.*?</h[1-3]>)', html, flags=_re.DOTALL)

        for part in parts:

            part = part.strip()

            if not part:

                continue

            if _re.match(r'<h1[^>]*>', part):

                blocks.append(('h1', strip_html(part)))

            elif _re.match(r'<h2[^>]*>', part):

                blocks.append(('h2', strip_html(part)))

            elif _re.match(r'<h3[^>]*>', part):

                blocks.append(('h3', strip_html(part)))

            else:

                text = strip_html(part)

                if text:

                    blocks.append(('p', text))



        pdf = FPDF()

        pdf.set_auto_page_break(auto=True, margin=20)

        pdf.add_page()

        pdf.set_font("Helvetica", "B", 20)

        pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)

        pdf.set_text_color(120, 120, 120)

        pdf.cell(0, 6, f"Exported from MachReach  -  {str(note.get('created_at',''))[:10]}", new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(0, 0, 0)

        pdf.ln(6)



        for btype, text in blocks:

            if btype == 'h1':

                pdf.set_font("Helvetica", "B", 18)

                pdf.ln(4)

                pdf.multi_cell(0, 8, text)

                pdf.ln(2)

            elif btype == 'h2':

                pdf.set_font("Helvetica", "B", 15)

                pdf.ln(4)

                pdf.multi_cell(0, 7, text)

                pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 170, pdf.get_y())

                pdf.ln(3)

            elif btype == 'h3':

                pdf.set_font("Helvetica", "B", 13)

                pdf.ln(3)

                pdf.multi_cell(0, 7, text)

                pdf.ln(2)

            else:

                pdf.set_font("Helvetica", "", 11)

                for line in text.split('\n'):

                    line = line.strip()

                    if line:

                        pdf.multi_cell(0, 6, line)

                        pdf.ln(1)



        buf = BytesIO(pdf.output())

        buf.seek(0)

        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:80] or "note"

        return send_file(buf, mimetype="application/pdf",

                         as_attachment=True,

                         download_name=f"{safe_title}.pdf")



    # ── Shared file-extract endpoint (used by drag-drop on

    #    quizzes / flashcards / essays / tutor / notes) ──────

    @app.route("/api/student/extract-file", methods=["POST"])

    @limiter.limit("20 per minute")

    def student_extract_file():

        """Accept a PDF/DOCX/TXT upload, return extracted plain text."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        if "file" not in request.files:

            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]

        if not f.filename:

            return jsonify({"error": "Empty filename"}), 400

        fname = f.filename

        fl = fname.lower()

        if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc") or fl.endswith(".txt")):

            return jsonify({"error": "Only PDF, DOCX, and TXT files are supported"}), 400

        content = f.read(50 * 1024 * 1024 + 1)

        if len(content) > 50 * 1024 * 1024:

            return jsonify({"error": "File too large (max 50MB)"}), 400

        text = ""

        try:

            if fl.endswith(".pdf"):

                text = extract_text_from_pdf(content)

            elif fl.endswith((".docx", ".doc")):

                text = extract_text_from_docx(content)

            else:

                try:

                    text = content.decode("utf-8", errors="ignore")

                except Exception:

                    text = ""

        except Exception as e:

            return jsonify({"error": f"Could not extract text: {e}"}), 400

        if not text or len(text.strip()) < 10:

            return jsonify({"error": "Could not extract enough readable text from this file"}), 400

        text = text.replace("\x00", "")

        return jsonify({

            "text": text,

            "filename": fname,

            "title": fname.rsplit(".", 1)[0],

            "char_count": len(text),

        })



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

            due = sdb.count_due_flashcards(d["id"])

            due_badge = f'<span style="background:#F59E0B22;color:#F59E0B;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">{due} due</span>' if due > 0 else ''

            decks_html += f"""

            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/flashcards/{d['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:center;">

                <div>

                  <h3 style="margin:0;font-size:16px;">{_esc(d.get('title','Untitled'))}</h3>

                  <span style="font-size:13px;color:var(--text-muted);">{_esc(d.get('course_name',''))} &middot; {d.get('card_count',0)} cards</span>

                </div>

                <div style="display:flex;gap:8px;align-items:center;">

                  {due_badge}

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



          <!-- Drag-and-drop zone -->

          <div id="fc-drop" class="dropzone" ondragover="event.preventDefault();this.classList.add('drag')" ondragleave="this.classList.remove('drag')" ondrop="fcHandleDrop(event)" onclick="document.getElementById('fc-file').click()">

            <div style="font-size:32px;">&#128206;</div>

            <div style="font-weight:600;margin-top:6px;">Drop a PDF / DOCX / TXT here</div>

            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">or click to browse &middot; we'll generate flashcards directly from the file (no course needed)</div>

            <input type="file" id="fc-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="fcHandleFile(this.files[0])">

            <div id="fc-file-info" style="margin-top:8px;font-size:13px;color:var(--primary);"></div>

          </div>



          <div style="text-align:center;color:var(--text-muted);font-size:12px;margin:12px 0;">— or pick from your courses —</div>



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

              <input type="number" id="fc-count" value="30" min="5" max="100" class="edit-input">

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

        .dropzone {{ border:2px dashed var(--border); border-radius:12px; padding:18px; text-align:center; cursor:pointer; transition:all .2s; background:var(--bg); }}

        .dropzone.drag {{ border-color:var(--primary); background:var(--card); }}

        </style>

        <script>

        var fcDropText = "";

        async function fcHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.classList.remove('drag');

          if (e.dataTransfer.files.length) await fcHandleFile(e.dataTransfer.files[0]);

        }}

        async function fcHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('Only PDF, DOCX, and TXT files'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var info = document.getElementById('fc-file-info');

          info.textContent = '⏳ Extracting text from ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await _safeJson(r);

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            fcDropText = d.text;

            info.innerHTML = '✅ ' + d.filename + ' — ' + d.char_count.toLocaleString() + ' chars ready';

            if (!document.getElementById('fc-title').value) document.getElementById('fc-title').value = 'Flashcards: ' + d.title;

          }} catch(e) {{ info.textContent = '❌ Network error'; }}

        }}

        async function loadExams(courseId, selectId) {{

          var sel = document.getElementById(selectId);

          sel.innerHTML = '<option value="">All topics</option>';

          if (!courseId) return;

          try {{

            var r = await fetch('/api/student/courses/' + courseId + '/exams');

            var d = await _safeJson(r);

            (d.exams || []).forEach(function(e) {{

              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';

            }});

          }} catch(e) {{}}

        }}

        async function genFlashcards() {{

          var courseId = document.getElementById('fc-course').value;

          if (!courseId && !fcDropText) {{ alert('Drop a file or select a course'); return; }}

          var btn = document.getElementById('fc-gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';

          try {{

            var body = {{

              count: parseInt(document.getElementById('fc-count').value),

              title: document.getElementById('fc-title').value || undefined

            }};

            if (fcDropText) {{

              body.source_text = fcDropText;

            }} else {{

              body.course_id = parseInt(courseId);

              body.exam_id = document.getElementById('fc-exam').value ? parseInt(document.getElementById('fc-exam').value) : null;

            }}

            var r = await fetch('/api/student/flashcards/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify(body)

            }});

            var d = await _safeJson(r);

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

        """Interactive flashcard study page with flip animation, SRS, and edit mode."""

        if not _logged_in():

            return redirect(url_for("login"))

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return redirect(url_for("student_flashcards_page"))



        # Load all cards for cycling study

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

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">{_esc(deck.get('course_name',''))} &middot; <span id="card-count-txt">{len(cards)}</span> cards</p>

          </div>

          <div style="display:flex;gap:8px;">

            <button onclick="switchMode('study')" class="btn btn-primary btn-sm" id="mode-study-btn">&#127183; Study</button>

            <button onclick="switchMode('edit')" class="btn btn-outline btn-sm" id="mode-edit-btn">&#9999;&#65039; Edit Cards</button>

          </div>

        </div>



        <!-- SRS toggle -->

        <div style="display:none;"></div>



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



            <div style="display:flex;justify-content:center;gap:16px;margin-top:24px;" id="answer-btns">

              <button onclick="markCard(false)" class="btn" style="min-width:140px;background:#EF4444;color:#fff;border:none;font-size:15px;padding:12px 24px;">&#10007; Incorrect</button>

              <button onclick="markCard(true)" class="btn" style="min-width:140px;background:#10B981;color:#fff;border:none;font-size:15px;padding:12px 24px;">&#10003; Correct</button>

            </div>



            <p style="text-align:center;font-size:11px;color:var(--text-muted);margin-top:12px;">

              <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">Space</kbd> flip

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">1</kbd> incorrect

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">2</kbd> correct

            </p>

            <div id="round-info" style="text-align:center;font-size:13px;color:var(--text-muted);margin-top:8px;"></div>



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

        var srsEnabled = false;

        // Cycling logic: remaining = cards not yet correct this round

        var remaining = cards.map(function(c) {{ return c; }});

        var roundNum = 1;

        var totalCorrectThisRound = 0;



        function updateRoundInfo() {{

          document.getElementById('round-info').textContent = 'Round ' + roundNum + ' \u2014 ' + remaining.length + ' cards remaining';

        }}



        function switchMode(mode) {{

          currentMode = mode;

          document.getElementById('study-mode').style.display = mode === 'study' ? '' : 'none';

          document.getElementById('edit-mode').style.display = mode === 'edit' ? '' : 'none';

          document.getElementById('mode-study-btn').className = mode === 'study' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';

          document.getElementById('mode-edit-btn').className = mode === 'edit' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';

          if (mode === 'edit') renderCardList();

          if (mode === 'study') {{ idx = 0; correct = 0; seen = 0; remaining = cards.map(function(c){{return c;}}); roundNum = 1; totalCorrectThisRound = 0; document.getElementById('fc-card').style.display = 'flex'; document.getElementById('fc-summary').style.display = 'none'; renderCard(); }}

        }}



        // ── Study functions (cycling) ──

        function renderCard() {{

          if (remaining.length === 0) {{ showSummary(); return; }}

          if (idx >= remaining.length) {{

            // End of round: all remaining cards were incorrect

            idx = 0;

            roundNum++;

            totalCorrectThisRound = 0;

          }}

          var c = remaining[idx];

          document.getElementById('fc-content').innerHTML = escHtml(c.front);

          document.getElementById('fc-side-label').textContent = 'Question';

          document.getElementById('fc-side-label').style.color = 'var(--primary)';

          document.getElementById('fc-card').style.borderColor = 'var(--border)';

          document.getElementById('progress-txt').textContent = (idx + 1) + ' / ' + remaining.length;

          document.getElementById('fc-progress-bar').style.width = ((cards.length - remaining.length) / cards.length * 100) + '%';

          flipped = false;

          updateRoundInfo();

          if (typeof renderMathInElement === 'function') renderMathInElement(document.getElementById('fc-content'), {{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}],throwOnError:false}});

        }}



        function flipCard() {{

          if (remaining.length === 0) return;

          flipped = !flipped;

          var c = remaining[idx];

          document.getElementById('fc-content').innerHTML = flipped ? escHtml(c.back) : escHtml(c.front);

          document.getElementById('fc-side-label').textContent = flipped ? 'Answer' : 'Question';

          document.getElementById('fc-side-label').style.color = flipped ? '#10B981' : 'var(--primary)';

          document.getElementById('fc-card').style.borderColor = flipped ? '#10B981' : 'var(--border)';

          document.getElementById('fc-card').style.transform = 'scale(0.97)';

          setTimeout(function() {{

            document.getElementById('fc-card').style.transform = 'scale(1)';

            if (typeof renderMathInElement === 'function') renderMathInElement(document.getElementById('fc-content'), {{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}],throwOnError:false}});

          }}, 150);

        }}



        function markCard(isCorrect) {{

          if (remaining.length === 0) return;

          if (!flipped) flipCard();

          seen++;

          fetch('/api/student/flashcards/progress', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ card_id: remaining[idx].id, correct: isCorrect }})

          }}).catch(function(){{}});

          if (isCorrect) {{

            correct++;

            // Remove card from remaining — it's correct for this cycle

            remaining.splice(idx, 1);

            if (remaining.length === 0) {{

              // All cards correct! Cycle complete

              showSummary();

              return;

            }}

            // Don't increment idx since we removed the element

            if (idx >= remaining.length) idx = 0;

          }} else {{

            // Card stays, move to next

            idx++;

            if (idx >= remaining.length) {{

              // Finished this round of remaining cards, loop back

              idx = 0;

              roundNum++;

            }}

          }}

          renderCard();

        }}



        function showSummary() {{

          document.getElementById('fc-card').style.display = 'none';

          document.getElementById('fc-summary').style.display = 'block';

          document.getElementById('answer-btns').style.display = 'none';

          document.getElementById('round-info').style.display = 'none';

          document.getElementById('fc-score').textContent = cards.length + ' / ' + cards.length + ' correct!';

          document.getElementById('fc-score-detail').textContent = 'Completed in ' + roundNum + ' round' + (roundNum > 1 ? 's' : '') + ' (' + seen + ' total reviews)';

          document.getElementById('fc-progress-bar').style.width = '100%';

        }}



        function restartStudy() {{

          idx = 0; correct = 0; seen = 0;

          remaining = cards.map(function(c){{return c;}});

          roundNum = 1; totalCorrectThisRound = 0;

          document.getElementById('fc-card').style.display = 'flex';

          document.getElementById('fc-summary').style.display = 'none';

          document.getElementById('answer-btns').style.display = 'flex';

          document.getElementById('round-info').style.display = '';

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

            var d = await _safeJson(r);

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

                  <button onclick="event.stopPropagation();window.location='/student/exam-sim/{q['id']}'" class="btn btn-ghost btn-sm" style="color:var(--primary);font-size:12px;" title="Exam Simulator">&#9889;</button>

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



          <!-- Drag-and-drop zone -->

          <div id="qz-drop" class="dropzone" ondragover="event.preventDefault();this.classList.add('drag')" ondragleave="this.classList.remove('drag')" ondrop="qzHandleDrop(event)" onclick="document.getElementById('qz-file').click()">

            <div style="font-size:32px;">&#128206;</div>

            <div style="font-weight:600;margin-top:6px;">Drop a PDF / DOCX / TXT here</div>

            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">or click to browse &middot; we'll generate quiz questions directly from the file (no course needed)</div>

            <input type="file" id="qz-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="qzHandleFile(this.files[0])">

            <div id="qz-file-info" style="margin-top:8px;font-size:13px;color:var(--primary);"></div>

          </div>



          <div style="text-align:center;color:var(--text-muted);font-size:12px;margin:12px 0;">— or pick from your courses —</div>



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

              <input type="number" id="qz-count" value="10" min="5" max="100" class="edit-input">

              <small style="display:block;color:var(--text-muted);font-size:11px;margin-top:4px;">Up to 100. Large quizzes generate in batches — give it a few seconds.</small>

            </div>

          </div>

          <button onclick="genQuiz()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="qz-gen-btn">&#10024; Generate</button>

        </div>



        {quizzes_html}



        <style>

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .dropzone {{ border:2px dashed var(--border); border-radius:12px; padding:18px; text-align:center; cursor:pointer; transition:all .2s; background:var(--bg); }}

        .dropzone.drag {{ border-color:var(--primary); background:var(--card); }}

        </style>

        <script>

        var qzDropText = "";

        async function qzHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.classList.remove('drag');

          if (e.dataTransfer.files.length) await qzHandleFile(e.dataTransfer.files[0]);

        }}

        async function qzHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('Only PDF, DOCX, and TXT files'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var info = document.getElementById('qz-file-info');

          info.textContent = '⏳ Extracting text from ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await _safeJson(r);

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            qzDropText = d.text;

            qzDropTitle = d.title;

            info.innerHTML = '✅ ' + d.filename + ' — ' + d.char_count.toLocaleString() + ' chars ready';

          }} catch(e) {{ info.textContent = '❌ Network error'; }}

        }}

        var qzDropTitle = '';

        async function loadExams(courseId, selectId) {{

          var sel = document.getElementById(selectId);

          sel.innerHTML = '<option value="">All topics</option>';

          if (!courseId) return;

          try {{

            var r = await fetch('/api/student/courses/' + courseId + '/exams');

            var d = await _safeJson(r);

            (d.exams || []).forEach(function(e) {{

              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';

            }});

          }} catch(e) {{}}

        }}

        async function genQuiz() {{

          var courseId = document.getElementById('qz-course').value;

          if (!courseId && !qzDropText) {{ alert('Drop a file or select a course'); return; }}

          var btn = document.getElementById('qz-gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';

          try {{

            var body = {{

              difficulty: document.getElementById('qz-diff').value,

              count: parseInt(document.getElementById('qz-count').value)

            }};

            if (qzDropText) {{

              body.source_text = qzDropText;

              body.title = 'Quiz: ' + qzDropTitle;

            }} else {{

              body.course_id = parseInt(courseId);

              body.exam_id = document.getElementById('qz-exam').value ? parseInt(document.getElementById('qz-exam').value) : null;

            }}

            var r = await fetch('/api/student/quizzes/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify(body)

            }});

            var d = await _safeJson(r);

            if (r.ok) {{

              var msg = 'Generated ' + d.question_count + ' questions!';

              if (d.short) {{ msg += '\\n(You requested ' + d.requested + ' but the source material only supported ' + d.question_count + ' unique questions.)'; }}

              alert(msg);

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

            "correct": q["correct"], "explanation": q.get("explanation", ""),

            "topic": q.get("topic", "") or ""

        } for q in questions], ensure_ascii=False)



        diff_color = {"easy": "#10B981", "medium": "#F59E0B", "hard": "#EF4444"}.get(quiz.get("difficulty", "medium"), "#94A3B8")



        return _s_render(f"Quiz: {quiz.get('title','')}", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;gap:12px;flex-wrap:wrap;">

          <div>

            <a href="/student/quizzes" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Quizzes</a>

            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(quiz.get('title',''))}</h1>

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">

              {_esc(quiz.get('course_name',''))} &middot;

              <span style="color:{diff_color};font-weight:600;">{quiz.get('difficulty','').upper()}</span> &middot;

              {quiz.get('question_count',0)} questions

            </p>

          </div>

          <div style="display:flex;align-items:center;gap:14px;">

            <div id="qz-timer-wrap" style="display:none;text-align:right;">

              <div id="qz-timer" style="font-size:22px;font-weight:700;font-family:monospace;letter-spacing:1px;color:var(--text);">00:00</div>

              <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;text-transform:uppercase;">Remaining</div>

            </div>

            <div id="qz-progress-txt" style="font-size:14px;color:var(--text-muted);">Question 1 of {len(questions)}</div>

          </div>

        </div>



        <!-- Progress bar -->

        <div style="background:var(--bg);border-radius:8px;height:8px;margin-bottom:24px;overflow:hidden;">

          <div id="qz-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.4s ease;border-radius:8px;"></div>

        </div>



        <div style="max-width:700px;margin:0 auto;">

          <!-- Pre-start setup -->

          <div id="qz-setup" class="card" style="padding:28px;">

            <h2 style="margin:0 0 6px;font-size:20px;">&#9889; Ready to start?</h2>

            <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;">{len(questions)} questions coming up. You can add a timer to simulate exam pressure.</p>



            <div style="border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px;background:var(--bg);">

              <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-weight:600;">

                <input type="checkbox" id="qz-timer-toggle" style="width:18px;height:18px;cursor:pointer;">

                <span>&#9201;&#65039; Enable timer</span>

              </label>

              <div id="qz-timer-config" style="display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--border);">

                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">

                  <div>

                    <label style="font-size:12px;color:var(--text-muted);font-weight:600;">Mode</label>

                    <select id="qz-timer-mode" class="edit-input">

                      <option value="total">Total time for whole quiz</option>

                      <option value="per">Time per question</option>

                    </select>

                  </div>

                  <div>

                    <label style="font-size:12px;color:var(--text-muted);font-weight:600;">

                      <span id="qz-timer-unit-label">Minutes total</span>

                    </label>

                    <input type="number" id="qz-timer-minutes" value="{max(5, len(questions) * 1)}" min="1" max="300" class="edit-input">

                  </div>

                </div>

                <div style="display:flex;gap:6px;flex-wrap:wrap;">

                  <button type="button" onclick="setQuizPreset(60,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128165; 60s / question</button>

                  <button type="button" onclick="setQuizPreset(90,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#9203; 90s / question</button>

                  <button type="button" onclick="setQuizPreset(120,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128336; 2m / question</button>

                  <button type="button" onclick="setQuizPreset({max(5,len(questions)*2)},'total')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128221; Realistic exam</button>

                </div>

                <p style="font-size:11px;color:var(--text-muted);margin:10px 0 0;">When the timer runs out, the quiz auto-finishes with whatever's answered.</p>

              </div>

            </div>



            <button onclick="beginQuiz()" class="btn btn-primary" style="padding:10px 26px;font-size:15px;">&#9654; Start Quiz</button>

          </div>



          <!-- Question card -->

          <div id="qz-card" class="card" style="padding:30px;display:none;">

            <div id="qz-question" style="font-size:18px;font-weight:600;margin-bottom:20px;line-height:1.5;"></div>

            <div id="qz-options"></div>

            <div id="qz-explanation" style="display:none;margin-top:16px;padding:14px;border-radius:var(--radius-sm);font-size:14px;line-height:1.5;"></div>

            <div style="display:flex;justify-content:flex-end;margin-top:20px;">

              <button id="qz-next-btn" onclick="nextQuestion()" class="btn btn-primary" style="display:none;">Next &rarr;</button>

            </div>

          </div>



          <!-- Summary (hidden until done) -->

          <div id="qz-summary" style="display:none;">

            <!-- Hero -->

            <div class="card" style="position:relative;overflow:hidden;text-align:center;padding:32px 24px;margin-bottom:18px;">

              <div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(800px 200px at 50% -20%,rgba(139,92,246,.18),transparent 70%);pointer-events:none;"></div>

              <div style="position:relative;z-index:1;">

                <div style="font-size:56px;margin-bottom:8px;" id="qz-emoji">&#127881;</div>

                <div id="qz-verdict" style="display:inline-block;font-size:11px;letter-spacing:2px;text-transform:uppercase;font-weight:700;padding:4px 12px;border-radius:999px;background:rgba(99,102,241,.15);color:#A78BFA;margin-bottom:10px;">Analyzing...</div>

                <h2 style="margin:0 0 4px;font-size:18px;font-weight:600;color:var(--text-muted);">Quiz complete</h2>

                <div id="qz-final-score" style="font-size:64px;font-weight:800;line-height:1;background:linear-gradient(135deg,#6366F1,#8B5CF6,#EC4899);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;"></div>

                <div id="qz-final-detail" style="font-size:14px;color:var(--text-muted);margin-top:6px;"></div>

                <div id="qz-headline" style="font-size:15px;margin-top:14px;max-width:560px;margin-left:auto;margin-right:auto;line-height:1.5;"></div>

              </div>

            </div>



            <!-- Pacing stats -->

            <div class="card" style="padding:18px;margin-bottom:18px;">

              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">

                <div style="text-align:center;"><div id="qz-pace-total" style="font-size:20px;font-weight:700;">0:00</div><div style="font-size:11px;color:var(--text-muted);">Total time</div></div>

                <div style="text-align:center;"><div id="qz-pace-avg" style="font-size:20px;font-weight:700;">0s</div><div style="font-size:11px;color:var(--text-muted);">Avg / question</div></div>

                <div style="text-align:center;"><div id="qz-pace-fast" style="font-size:20px;font-weight:700;color:#10B981;">0s</div><div style="font-size:11px;color:var(--text-muted);">Fastest</div></div>

                <div style="text-align:center;"><div id="qz-pace-slow" style="font-size:20px;font-weight:700;color:#EF4444;">0s</div><div style="font-size:11px;color:var(--text-muted);">Slowest</div></div>

              </div>

              <div id="qz-pace-insight" style="margin-top:12px;padding:10px 14px;background:var(--bg);border-radius:8px;font-size:13px;color:var(--text-muted);display:none;"></div>

            </div>



            <!-- Topic breakdown -->

            <div class="card" style="padding:20px;margin-bottom:18px;">

              <h3 style="margin:0 0 14px;font-size:16px;">&#128202; Topic breakdown</h3>

              <div id="qz-topics"></div>

            </div>



            <!-- AI analysis grid -->

            <div id="qz-ai-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;">

              <div class="card" style="padding:18px;">

                <h3 style="margin:0 0 10px;font-size:15px;color:#10B981;">&#9989; Strengths</h3>

                <div id="qz-strengths" style="font-size:13px;color:var(--text-muted);">Analyzing...</div>

              </div>

              <div class="card" style="padding:18px;">

                <h3 style="margin:0 0 10px;font-size:15px;color:#EF4444;">&#10060; Needs work</h3>

                <div id="qz-weaknesses" style="font-size:13px;color:var(--text-muted);">Analyzing...</div>

              </div>

            </div>



            <!-- Mistake patterns -->

            <div class="card" id="qz-patterns-card" style="padding:18px;margin-bottom:18px;display:none;">

              <h3 style="margin:0 0 10px;font-size:15px;">&#128269; Mistake patterns</h3>

              <div id="qz-patterns" style="font-size:13px;"></div>

            </div>



            <!-- Action plan -->

            <div class="card" style="padding:20px;margin-bottom:18px;border-left:4px solid #8B5CF6;">

              <h3 style="margin:0 0 10px;font-size:16px;">&#127919; Do this next</h3>

              <div id="qz-actions" style="font-size:14px;line-height:1.7;">Analyzing...</div>

            </div>



            <!-- 30-min study plan -->

            <div class="card" id="qz-plan-card" style="padding:20px;margin-bottom:18px;display:none;">

              <h3 style="margin:0 0 10px;font-size:16px;">&#9201; 30-minute follow-up plan</h3>

              <div id="qz-plan"></div>

            </div>



            <!-- Per-question review (collapsible) -->

            <div class="card" style="padding:20px;margin-bottom:18px;">

              <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;" onclick="toggleReview()">

                <h3 style="margin:0;font-size:16px;">&#128214; Question-by-question review</h3>

                <span id="qz-review-toggle" style="font-size:12px;color:var(--text-muted);">Show &#9660;</span>

              </div>

              <div id="qz-review-body" style="display:none;margin-top:14px;"></div>

            </div>



            <!-- Actions row -->

            <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-bottom:40px;">

              <button onclick="restartQuiz()" class="btn btn-primary">&#128260; Retake quiz</button>

              <button id="qz-retake-wrong" onclick="retakeWrong()" class="btn btn-outline" style="display:none;">&#9998; Retake wrong only</button>

              <a href="/student/quizzes" class="btn btn-outline">Back to quizzes</a>

            </div>



            <div id="qz-encouragement" style="text-align:center;font-size:13px;color:var(--text-muted);font-style:italic;margin-bottom:20px;"></div>

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

        .qz-topic-row {{ display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:13px; }}

        .qz-topic-name {{ flex:0 0 32%;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }}

        .qz-topic-bar {{ flex:1;background:var(--bg);border-radius:6px;height:10px;overflow:hidden;border:1px solid var(--border); }}

        .qz-topic-fill {{ height:100%;border-radius:6px;transition:width .6s ease; }}

        .qz-topic-pct {{ flex:0 0 72px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums; }}

        .qz-review-item {{ padding:14px;margin-bottom:10px;border-radius:10px;border-left:4px solid;background:var(--bg); }}

        .qz-review-item.correct {{ border-color:#10B981; }}

        .qz-review-item.wrong {{ border-color:#EF4444; }}

        .qz-ai-item {{ padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;line-height:1.55; }}

        .qz-ai-item:last-child {{ border-bottom:none; }}

        .qz-ai-topic {{ font-weight:700;color:var(--text);display:block;margin-bottom:2px; }}

        .qz-ai-fix {{ display:block;margin-top:4px;color:#A78BFA;font-size:12px; }}

        @media (max-width: 700px) {{ #qz-ai-grid {{ grid-template-columns:1fr; }} }}

        </style>



        <script>

        var questions = {questions_json};

        var qIdx = 0, score = 0, answered = false;

        var answerLog = [];        // [{{q_id, selected, time, is_correct}}]

        var questionStart = 0;

        var quizStarted = Date.now();



        /* ───── Optional timer ───── */

        var qzTimerEnabled = false;

        var qzTimerInterval = null;

        var qzTimerRemaining = 0;

        var qzTimerTotal = 0;

        var qzTimerPerQuestion = false;



        (function bindSetup() {{

          var toggle = document.getElementById('qz-timer-toggle');

          var cfg = document.getElementById('qz-timer-config');

          var modeSel = document.getElementById('qz-timer-mode');

          var minsInput = document.getElementById('qz-timer-minutes');

          var unitLabel = document.getElementById('qz-timer-unit-label');

          toggle.addEventListener('change', function() {{

            cfg.style.display = toggle.checked ? 'block' : 'none';

          }});

          modeSel.addEventListener('change', function() {{

            if (modeSel.value === 'per') {{

              unitLabel.textContent = 'Seconds per question';

              minsInput.value = 90; minsInput.min = 10; minsInput.max = 600;

            }} else {{

              unitLabel.textContent = 'Minutes total';

              minsInput.value = Math.max(5, questions.length * 1);

              minsInput.min = 1; minsInput.max = 300;

            }}

          }});

        }})();



        window.setQuizPreset = function(val, mode) {{

          document.getElementById('qz-timer-toggle').checked = true;

          document.getElementById('qz-timer-config').style.display = 'block';

          document.getElementById('qz-timer-mode').value = mode;

          var label = document.getElementById('qz-timer-unit-label');

          var input = document.getElementById('qz-timer-minutes');

          if (mode === 'per') {{

            label.textContent = 'Seconds per question';

            input.min = 10; input.max = 600; input.value = val;

          }} else {{

            label.textContent = 'Minutes total';

            input.min = 1; input.max = 300; input.value = val;

          }}

        }};



        function formatMMSS(totalSec) {{

          totalSec = Math.max(0, Math.floor(totalSec));

          var m = Math.floor(totalSec / 60);

          var s = totalSec % 60;

          return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;

        }}



        function tickQuizTimer() {{

          qzTimerRemaining--;

          var el = document.getElementById('qz-timer');

          if (!el) return;

          el.textContent = formatMMSS(qzTimerRemaining);

          // Color shift

          var frac = qzTimerRemaining / (qzTimerTotal || 1);

          if (qzTimerRemaining <= 10) el.style.color = '#EF4444';

          else if (frac <= 0.25) el.style.color = '#F59E0B';

          else el.style.color = 'var(--text)';

          if (qzTimerRemaining <= 0) {{

            clearInterval(qzTimerInterval);

            qzTimerInterval = null;

            // Auto-finish: log a blank for current question if not answered yet

            if (!answered && qIdx < questions.length) {{

              answerLog.push({{ q_id: questions[qIdx].id, selected: '', time: (Date.now() - questionStart) / 1000, is_correct: false }});

            }}

            showResults();

          }}

        }}



        function beginQuiz() {{

          qzTimerEnabled = document.getElementById('qz-timer-toggle').checked;

          if (qzTimerEnabled) {{

            var mode = document.getElementById('qz-timer-mode').value;

            var val = parseInt(document.getElementById('qz-timer-minutes').value, 10) || 0;

            if (mode === 'per') {{

              qzTimerPerQuestion = true;

              qzTimerTotal = Math.max(10, val) * questions.length;

            }} else {{

              qzTimerPerQuestion = false;

              qzTimerTotal = Math.max(1, val) * 60;

            }}

            qzTimerRemaining = qzTimerTotal;

            document.getElementById('qz-timer-wrap').style.display = 'block';

            document.getElementById('qz-timer').textContent = formatMMSS(qzTimerRemaining);

            qzTimerInterval = setInterval(tickQuizTimer, 1000);

          }}

          document.getElementById('qz-setup').style.display = 'none';

          document.getElementById('qz-card').style.display = '';

          quizStarted = Date.now();

          renderQuestion();

        }}



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

          questionStart = Date.now();

        }}



        function selectAnswer(key) {{

          if (answered) return;

          answered = true;

          var q = questions[qIdx];

          var isCorrect = key === q.correct;

          var timeSpent = (Date.now() - questionStart) / 1000;

          if (isCorrect) score++;

          answerLog.push({{ q_id: q.id, selected: key, time: timeSpent, is_correct: isCorrect }});



          document.querySelectorAll('.qz-option').forEach(function(btn) {{

            btn.classList.add('disabled');

            if (btn.dataset.key === q.correct) btn.classList.add('correct');

            if (btn.dataset.key === key && !isCorrect) btn.classList.add('wrong');

          }});



          var exp = document.getElementById('qz-explanation');

          exp.style.display = 'block';

          exp.style.background = isCorrect ? '#D1FAE5' : '#FEE2E2';

          exp.style.color = isCorrect ? '#065F46' : '#991B1B';

          exp.innerHTML = (isCorrect ? '&#10003; Correct! ' : '&#10007; Incorrect. ') + (q.explanation || '');



          document.getElementById('qz-next-btn').style.display = '';

          document.getElementById('qz-next-btn').textContent = qIdx === questions.length - 1 ? 'See Results' : 'Next \\u2192';

        }}



        function nextQuestion() {{ qIdx++; renderQuestion(); }}



        function fmtTime(s) {{

          s = Math.round(s);

          if (s < 60) return s + 's';

          return Math.floor(s/60) + 'm ' + (s%60) + 's';

        }}

        function escH(t) {{ var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }}



        function showResults() {{

          if (qzTimerInterval) {{ clearInterval(qzTimerInterval); qzTimerInterval = null; }}

          document.getElementById('qz-timer-wrap').style.display = 'none';

          document.getElementById('qz-card').style.display = 'none';

          document.getElementById('qz-summary').style.display = 'block';

          var pct = Math.round(score / questions.length * 100);

          document.getElementById('qz-final-score').textContent = pct + '%';

          document.getElementById('qz-final-detail').textContent = score + ' of ' + questions.length + ' correct';

          document.getElementById('qz-bar').style.width = '100%';

          document.getElementById('qz-emoji').innerHTML = pct >= 90 ? '&#127942;' : pct >= 70 ? '&#127881;' : pct >= 50 ? '&#128170;' : '&#128218;';

          if (pct >= 80 && window.confettiBurst) {{ window.confettiBurst(pct >= 95 ? 80 : 50); }}



          fetch('/api/student/quizzes/{quiz_id}/score', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ score: pct }})

          }}).catch(function(){{}});



          // Per-question review (always available immediately)

          renderReview();

          // Show retake-wrong if any wrong

          if (answerLog.some(function(a){{ return !a.is_correct; }})) {{

            document.getElementById('qz-retake-wrong').style.display = '';

          }}



          // Rich analytics (AI)

          fetch('/api/student/quizzes/{quiz_id}/analyze', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ answers: answerLog }})

          }})

          .then(function(r) {{ return r.json(); }})

          .then(renderAnalytics)

          .catch(function() {{

            document.getElementById('qz-headline').textContent = 'Detailed analysis unavailable — try again later.';

            document.getElementById('qz-strengths').textContent = '—';

            document.getElementById('qz-weaknesses').textContent = '—';

            document.getElementById('qz-actions').textContent = '—';

          }});

        }}



        function verdictStyle(v) {{

          var m = {{

            mastery: {{ label:'Mastery', bg:'rgba(16,185,129,.15)', color:'#10B981' }},

            solid:   {{ label:'Solid', bg:'rgba(99,102,241,.15)', color:'#A78BFA' }},

            shaky:   {{ label:'Shaky', bg:'rgba(245,158,11,.15)', color:'#F59E0B' }},

            struggling:{{ label:'Struggling', bg:'rgba(239,68,68,.15)', color:'#EF4444' }},

          }};

          return m[v] || m.solid;

        }}



        function renderAnalytics(data) {{

          if (!data) return;

          var ai = data.ai || {{}};



          // Verdict pill

          var vs = verdictStyle(ai.verdict);

          var vp = document.getElementById('qz-verdict');

          vp.textContent = vs.label;

          vp.style.background = vs.bg;

          vp.style.color = vs.color;



          // Headline

          if (ai.headline) document.getElementById('qz-headline').textContent = ai.headline;



          // Pacing

          if (data.pacing) {{

            document.getElementById('qz-pace-total').textContent = fmtTime(data.pacing.total_time);

            document.getElementById('qz-pace-avg').textContent = fmtTime(data.pacing.avg_time);

            document.getElementById('qz-pace-fast').textContent = fmtTime(data.pacing.fastest);

            document.getElementById('qz-pace-slow').textContent = fmtTime(data.pacing.slowest);

          }}

          if (ai.time_insight) {{

            var pi = document.getElementById('qz-pace-insight');

            pi.style.display = 'block';

            pi.textContent = ai.time_insight;

          }}



          // Topic breakdown

          var topicsEl = document.getElementById('qz-topics');

          topicsEl.innerHTML = '';

          (data.breakdown || []).forEach(function(t) {{

            var color = t.percent >= 80 ? '#10B981' : t.percent >= 50 ? '#F59E0B' : '#EF4444';

            var row = document.createElement('div');

            row.className = 'qz-topic-row';

            row.innerHTML =

              '<span class="qz-topic-name" title="' + escH(t.topic) + '">' + escH(t.topic) + '</span>'

              + '<span class="qz-topic-bar"><span class="qz-topic-fill" style="width:' + t.percent + '%;background:' + color + ';"></span></span>'

              + '<span class="qz-topic-pct" style="color:' + color + ';">' + t.percent + '% <span style="font-weight:400;color:var(--text-muted);font-size:11px;">(' + t.correct + '/' + t.total + ')</span></span>';

            topicsEl.appendChild(row);

          }});

          if (!topicsEl.children.length) {{

            topicsEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">No topic data.</div>';

          }}



          // Strengths

          var sEl = document.getElementById('qz-strengths');

          if ((ai.strengths || []).length) {{

            sEl.innerHTML = ai.strengths.map(function(s) {{

              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#10003; ' + escH(s.topic || '') + '</span>' + escH(s.detail || '') + '</div>';

            }}).join('');

          }} else {{

            sEl.innerHTML = '<span style="font-style:italic;">Nothing stood out yet. Keep building.</span>';

          }}



          // Weaknesses

          var wEl = document.getElementById('qz-weaknesses');

          if ((ai.weaknesses || []).length) {{

            wEl.innerHTML = ai.weaknesses.map(function(w) {{

              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#9888;&#65039; ' + escH(w.topic || '') + '</span>'

                + escH(w.detail || '')

                + (w.fix ? '<span class="qz-ai-fix">&#8594; ' + escH(w.fix) + '</span>' : '')

                + '</div>';

            }}).join('');

          }} else {{

            wEl.innerHTML = '<span style="font-style:italic;color:#10B981;">Clean sweep \u2014 no clear weak spots. Try a harder quiz.</span>';

          }}



          // Mistake patterns

          if ((ai.mistake_patterns || []).length) {{

            document.getElementById('qz-patterns-card').style.display = '';

            document.getElementById('qz-patterns').innerHTML = ai.mistake_patterns.map(function(p) {{

              return '<div style="padding:6px 0;color:var(--text-muted);">&#8226; ' + escH(p) + '</div>';

            }}).join('');

          }}



          // Actions

          var aEl = document.getElementById('qz-actions');

          if ((ai.next_actions || []).length) {{

            aEl.innerHTML = '<ol style="margin:0;padding-left:20px;">' + ai.next_actions.map(function(x) {{

              return '<li style="margin-bottom:6px;">' + escH(x) + '</li>';

            }}).join('') + '</ol>';

          }} else {{

            aEl.textContent = 'Take another quiz on the same material to reinforce what just clicked.';

          }}



          // 30-min plan

          if ((ai.study_plan_30min || []).length) {{

            document.getElementById('qz-plan-card').style.display = '';

            document.getElementById('qz-plan').innerHTML = ai.study_plan_30min.map(function(step) {{

              return '<div style="display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px dashed var(--border);">'

                + '<div style="flex:0 0 56px;font-weight:700;color:#A78BFA;">' + (step.minutes || 0) + ' min</div>'

                + '<div style="font-size:13px;line-height:1.5;">' + escH(step.task || '') + '</div>'

                + '</div>';

            }}).join('');

          }}



          // Encouragement

          if (ai.encouragement) document.getElementById('qz-encouragement').textContent = ai.encouragement;

        }}



        function renderReview() {{

          var body = document.getElementById('qz-review-body');

          body.innerHTML = answerLog.map(function(a, i) {{

            var q = questions[i];

            var sel = a.selected || '';

            return '<div class="qz-review-item ' + (a.is_correct ? 'correct' : 'wrong') + '">'

              + '<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">'

              +   '<div><strong>Q' + (i+1) + '.</strong> ' + escH(q.question) + '</div>'

              +   '<span style="font-size:11px;color:var(--text-muted);white-space:nowrap;">' + fmtTime(a.time) + '</span>'

              + '</div>'

              + (q.topic ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Topic: ' + escH(q.topic) + '</div>' : '')

              + '<div style="font-size:13px;margin-top:8px;">Your answer: <b>' + sel.toUpperCase() + '.</b> ' + escH(q['option_' + sel] || '')

              +   (a.is_correct ? ' &#10003;' : ' &#10007;')

              + '</div>'

              + (!a.is_correct ? '<div style="font-size:13px;margin-top:4px;color:#10B981;">Correct: <b>' + q.correct.toUpperCase() + '.</b> ' + escH(q['option_' + q.correct] || '') + '</div>' : '')

              + (q.explanation ? '<div style="font-size:12px;margin-top:6px;color:var(--text-muted);font-style:italic;">' + escH(q.explanation) + '</div>' : '')

              + '</div>';

          }}).join('');

        }}



        function toggleReview() {{

          var b = document.getElementById('qz-review-body');

          var t = document.getElementById('qz-review-toggle');

          if (b.style.display === 'none') {{ b.style.display = 'block'; t.innerHTML = 'Hide &#9650;'; }}

          else {{ b.style.display = 'none'; t.innerHTML = 'Show &#9660;'; }}

        }}



        function restartQuiz() {{

          if (qzTimerInterval) {{ clearInterval(qzTimerInterval); qzTimerInterval = null; }}

          qIdx = 0; score = 0; answerLog = [];

          // Show setup again so user can re-configure timer

          document.getElementById('qz-setup').style.display = '';

          document.getElementById('qz-card').style.display = 'none';

          document.getElementById('qz-summary').style.display = 'none';

          document.getElementById('qz-timer-wrap').style.display = 'none';

          document.getElementById('qz-bar').style.width = '0%';

        }}



        function retakeWrong() {{

          var wrongIdx = answerLog.map(function(a,i){{return a.is_correct?null:i;}}).filter(function(x){{return x!==null;}});

          if (!wrongIdx.length) return;

          questions = wrongIdx.map(function(i) {{ return questions[i]; }});

          restartQuiz();

        }}



        document.addEventListener('keydown', function(e) {{

          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

          if (document.getElementById('qz-setup').style.display !== 'none') return; // still on setup

          if (!answered) {{

            if (e.code === 'KeyA' || e.code === 'Digit1') {{ e.preventDefault(); selectAnswer('a'); }}

            if (e.code === 'KeyB' || e.code === 'Digit2') {{ e.preventDefault(); selectAnswer('b'); }}

            if (e.code === 'KeyC' || e.code === 'Digit3') {{ e.preventDefault(); selectAnswer('c'); }}

            if (e.code === 'KeyD' || e.code === 'Digit4') {{ e.preventDefault(); selectAnswer('d'); }}

          }} else {{

            if (e.code === 'Enter' || e.code === 'Space') {{ e.preventDefault(); nextQuestion(); }}

          }}

        }});



        // Quiz starts from the setup screen via beginQuiz(). No auto-start.

        </script>

        """, active_page="student_quizzes")



    # ── Exam Simulator Mode ────────────────────────────────



    @app.route("/student/exam-sim/<int:quiz_id>")

    def student_exam_simulator_page(quiz_id):

        """Exam simulator — timed, no going back, pressure UI, analytics."""

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

            "correct": q["correct"], "explanation": q.get("explanation", ""),

            "topic": q.get("topic", "") or ""

        } for q in questions], ensure_ascii=False)



        # Default: 2 min per question

        default_minutes = max(5, len(questions) * 2)



        return _s_render(f"Exam Simulator: {quiz.get('title','')}", f"""

        <!-- Setup screen -->

        <div id="exam-setup" style="max-width:500px;margin:40px auto;text-align:center;">

          <div style="font-size:64px;margin-bottom:16px;">&#128221;</div>

          <h1 style="margin:0;">Exam Simulator</h1>

          <p style="color:var(--text-muted);margin:8px 0 0;">{_esc(quiz.get('title',''))}</p>

          <p style="color:var(--text-muted);font-size:13px;">{len(questions)} questions &middot; {quiz.get('difficulty','').upper()}</p>



          <div class="card" style="padding:24px;margin:24px 0;text-align:left;">

            <h3 style="margin:0 0 16px;">&#9881; Settings</h3>

            <div class="form-group" style="margin-bottom:16px;">

              <label style="font-weight:600;font-size:14px;">Time Limit (minutes)</label>

              <input type="number" id="exam-time" value="{default_minutes}" min="1" max="180" class="edit-input" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:15px;margin-top:6px;">

            </div>

            <div style="background:var(--bg);border-radius:var(--radius-sm);padding:14px;font-size:13px;color:var(--text-muted);">

              <p style="margin:0 0 6px;"><strong style="color:var(--text);">&#9888;&#65039; Exam Rules:</strong></p>

              <ul style="margin:0;padding-left:18px;line-height:1.8;">

                <li>You <strong>cannot go back</strong> to previous questions</li>

                <li>Timer runs continuously — no pausing</li>

                <li>Answers are final once submitted</li>

                <li>Detailed analytics provided at the end</li>

              </ul>

            </div>

          </div>



          <button onclick="startExam()" class="btn btn-primary" style="padding:12px 40px;font-size:16px;">&#9889; Start Exam</button>

          <p style="margin-top:12px;"><a href="/student/quizzes" style="color:var(--text-muted);font-size:13px;">&larr; Back to Quizzes</a></p>

        </div>



        <!-- Active exam -->

        <div id="exam-active" style="display:none;">

          <!-- Timer bar -->

          <div style="position:sticky;top:60px;z-index:50;background:var(--bg);padding:8px 0;margin-bottom:16px;">

            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">

              <span style="font-size:14px;font-weight:600;color:var(--text);" id="exam-q-txt">Question 1 / {len(questions)}</span>

              <span style="font-size:20px;font-weight:700;font-family:monospace;" id="exam-timer" style="color:var(--text);">00:00</span>

            </div>

            <div style="background:var(--border);border-radius:8px;height:6px;overflow:hidden;">

              <div id="exam-timer-bar" style="height:100%;background:linear-gradient(90deg,#10B981,#3B82F6);width:100%;transition:width 1s linear;border-radius:8px;"></div>

            </div>

          </div>



          <div style="max-width:700px;margin:0 auto;">

            <div id="exam-card" class="card" style="padding:30px;">

              <div id="exam-question" style="font-size:18px;font-weight:600;margin-bottom:20px;line-height:1.5;"></div>

              <div id="exam-options"></div>

              <div style="display:flex;justify-content:flex-end;margin-top:20px;">

                <button id="exam-submit-btn" onclick="submitAnswer()" class="btn btn-primary" disabled>Lock In Answer &rarr;</button>

              </div>

            </div>

          </div>

        </div>



        <!-- Results -->

        <div id="exam-results" style="display:none;max-width:800px;margin:0 auto;">

          <div class="card" style="text-align:center;padding:30px;margin-bottom:20px;">

            <div id="exam-emoji" style="font-size:64px;margin-bottom:12px;">&#127942;</div>

            <h1 style="margin:0 0 8px;">Exam Complete!</h1>

            <div id="exam-final-score" style="font-size:48px;font-weight:800;color:var(--primary);"></div>

            <div id="exam-final-detail" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>

            <div id="exam-time-taken" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>

          </div>



          <!-- Analytics -->

          <div class="card" style="padding:24px;margin-bottom:20px;">

            <h2 style="margin:0 0 16px;">&#128202; Analytics</h2>

            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;">

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-avg-time" style="font-size:24px;font-weight:700;color:var(--primary);">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Avg per question</div>

              </div>

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-fastest" style="font-size:24px;font-weight:700;color:#10B981;">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Fastest answer</div>

              </div>

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-slowest" style="font-size:24px;font-weight:700;color:#EF4444;">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Slowest answer</div>

              </div>

            </div>

          </div>



          <!-- Per-question review -->

          <div class="card" style="padding:24px;">

            <h2 style="margin:0 0 16px;">&#128214; Question Review</h2>

            <div id="exam-review"></div>

          </div>



          <div style="display:flex;gap:12px;justify-content:center;margin:24px 0;">

            <button onclick="location.reload()" class="btn btn-primary">&#128260; Retake Exam</button>

            <a href="/student/quizzes" class="btn btn-outline">Back to Quizzes</a>

          </div>

        </div>



        <style>

        .exam-opt {{

          display:block;width:100%;text-align:left;padding:14px 18px;margin-bottom:10px;

          background:var(--bg);border:2px solid var(--border);border-radius:12px;

          cursor:pointer;font-size:15px;color:var(--text);transition:all 0.2s ease;

        }}

        .exam-opt:hover {{ border-color:var(--primary);background:var(--card); }}

        .exam-opt.selected {{ border-color:var(--primary);background:var(--primary-light,#EDE9FE);font-weight:600; }}

        .review-q {{ padding:16px;margin-bottom:12px;border-radius:var(--radius-sm);border-left:4px solid; }}

        .review-q.correct {{ border-color:#10B981;background:#D1FAE520; }}

        .review-q.wrong {{ border-color:#EF4444;background:#FEE2E220; }}

        </style>



        <script>

        var questions = {questions_json};

        var eIdx = 0, eScore = 0, eSelected = null;

        var timePerQuestion = [];

        var answers = [];

        var questionStartTime = 0;

        var timerInterval = null;

        var totalSeconds = 0;

        var elapsedSeconds = 0;



        function startExam() {{

          totalSeconds = parseInt(document.getElementById('exam-time').value) * 60;

          if (totalSeconds < 60) totalSeconds = 60;

          elapsedSeconds = 0;

          document.getElementById('exam-setup').style.display = 'none';

          document.getElementById('exam-active').style.display = '';

          questionStartTime = Date.now();

          timerInterval = setInterval(tickTimer, 1000);

          renderExamQ();

        }}



        function tickTimer() {{

          elapsedSeconds++;

          var remaining = totalSeconds - elapsedSeconds;

          if (remaining <= 0) {{

            clearInterval(timerInterval);

            // Auto-submit current and force end

            if (eSelected) answers.push({{ q: eIdx, selected: eSelected, time: (Date.now() - questionStartTime) / 1000 }});

            finishExam();

            return;

          }}

          var m = Math.floor(remaining / 60);

          var s = remaining % 60;

          var timerEl = document.getElementById('exam-timer');

          timerEl.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;

          timerEl.style.color = remaining <= 60 ? '#EF4444' : remaining <= 300 ? '#F59E0B' : 'var(--text)';

          document.getElementById('exam-timer-bar').style.width = (remaining / totalSeconds * 100) + '%';

          if (remaining <= 60) document.getElementById('exam-timer-bar').style.background = '#EF4444';

          else if (remaining <= 300) document.getElementById('exam-timer-bar').style.background = '#F59E0B';

        }}



        function renderExamQ() {{

          if (eIdx >= questions.length) {{ finishExam(); return; }}

          var q = questions[eIdx];

          eSelected = null;

          document.getElementById('exam-submit-btn').disabled = true;

          document.getElementById('exam-q-txt').textContent = 'Question ' + (eIdx + 1) + ' / ' + questions.length;

          document.getElementById('exam-question').textContent = 'Q' + (eIdx + 1) + '. ' + q.question;

          var opts = document.getElementById('exam-options');

          opts.innerHTML = '';

          ['a','b','c','d'].forEach(function(key) {{

            var btn = document.createElement('button');

            btn.className = 'exam-opt';

            btn.textContent = key.toUpperCase() + '. ' + q['option_' + key];

            btn.dataset.key = key;

            btn.onclick = function() {{

              document.querySelectorAll('.exam-opt').forEach(function(b) {{ b.classList.remove('selected'); }});

              btn.classList.add('selected');

              eSelected = key;

              document.getElementById('exam-submit-btn').disabled = false;

            }};

            opts.appendChild(btn);

          }});

          questionStartTime = Date.now();

        }}



        function submitAnswer() {{

          if (!eSelected) return;

          var q = questions[eIdx];

          var timeTaken = (Date.now() - questionStartTime) / 1000;

          var isCorrect = eSelected === q.correct;

          if (isCorrect) eScore++;

          answers.push({{ q: eIdx, selected: eSelected, correct: q.correct, isCorrect: isCorrect, time: timeTaken, explanation: q.explanation }});

          eIdx++;

          renderExamQ();

        }}



        function finishExam() {{

          clearInterval(timerInterval);

          document.getElementById('exam-active').style.display = 'none';

          document.getElementById('exam-results').style.display = '';

          var pct = Math.round(eScore / questions.length * 100);

          document.getElementById('exam-final-score').textContent = pct + '%';

          document.getElementById('exam-final-detail').textContent = eScore + ' of ' + questions.length + ' correct';

          document.getElementById('exam-time-taken').textContent = 'Time: ' + Math.floor(elapsedSeconds / 60) + 'm ' + (elapsedSeconds % 60) + 's';

          document.getElementById('exam-emoji').innerHTML = pct >= 90 ? '&#127942;' : pct >= 70 ? '&#127881;' : pct >= 50 ? '&#128170;' : '&#128218;';

          if (pct >= 80 && window.confettiBurst) {{ window.confettiBurst(pct >= 95 ? 100 : 60); }}



          // Analytics

          var times = answers.map(function(a) {{ return a.time; }});

          var avg = times.length ? (times.reduce(function(s,t) {{ return s + t; }}, 0) / times.length) : 0;

          document.getElementById('anal-avg-time').textContent = Math.round(avg) + 's';

          document.getElementById('anal-fastest').textContent = Math.round(Math.min.apply(null, times)) + 's';

          document.getElementById('anal-slowest').textContent = Math.round(Math.max.apply(null, times)) + 's';



          // Review

          var reviewHtml = '';

          answers.forEach(function(a) {{

            var q = questions[a.q];

            reviewHtml += '<div class="review-q ' + (a.isCorrect ? 'correct' : 'wrong') + '">'

              + '<div style="display:flex;justify-content:space-between;align-items:start;">'

              + '<strong>Q' + (a.q + 1) + '. ' + escH(q.question) + '</strong>'

              + '<span style="font-size:12px;color:var(--text-muted);white-space:nowrap;">' + Math.round(a.time) + 's</span>'

              + '</div>'

              + '<div style="font-size:13px;margin-top:6px;">Your answer: <strong>' + a.selected.toUpperCase() + '</strong>'

              + (a.isCorrect ? ' &#10003;' : ' &#10007; (Correct: ' + a.correct.toUpperCase() + ')') + '</div>'

              + (a.explanation ? '<div style="font-size:13px;margin-top:6px;color:var(--text-muted);font-style:italic;">' + escH(a.explanation) + '</div>' : '')

              + '</div>';

          }});

          document.getElementById('exam-review').innerHTML = reviewHtml;



          // Submit score

          fetch('/api/student/quizzes/{quiz_id}/score', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ score: pct }})

          }}).catch(function(){{}});

        }}



        function escH(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}



        document.addEventListener('keydown', function(e) {{

          if (document.getElementById('exam-active').style.display === 'none') return;

          if (!eSelected || eSelected === null) {{

            if (e.code === 'KeyA' || e.code === 'Digit1') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="a"]').click(); }}

            if (e.code === 'KeyB' || e.code === 'Digit2') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="b"]').click(); }}

            if (e.code === 'KeyC' || e.code === 'Digit3') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="c"]').click(); }}

            if (e.code === 'KeyD' || e.code === 'Digit4') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="d"]').click(); }}

          }}

          if (e.code === 'Enter' && eSelected) {{ e.preventDefault(); submitAnswer(); }}

        }});

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

            is_pub = n.get("is_public", False)

            share_icon = "&#127760;" if is_pub else "&#128274;"

            share_label = "Public" if is_pub else "Share"

            share_color = "color:var(--green);" if is_pub else ""

            notes_html += f"""

            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/notes/{n['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:center;">

                <div>

                  <h3 style="margin:0;font-size:16px;">{_esc(n.get('title','Untitled'))}</h3>

                  <span style="font-size:13px;color:var(--text-muted);">{_esc(n.get('course_name',''))} &middot; {_esc(n.get('source_type','ai'))} &middot; {str(n.get('created_at',''))[:10]}</span>

                </div>

                <div style="display:flex;gap:6px;align-items:center;">

                  <button onclick="event.stopPropagation();toggleShare({n['id']},{'true' if is_pub else 'false'})" class="btn btn-ghost btn-sm" style="font-size:12px;{share_color}" title="{'Unpublish from Exchange' if is_pub else 'Share to Exchange'}">{share_icon} {share_label}</button>

                  <button onclick="event.stopPropagation();deleteNote({n['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>

                </div>

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



        <!-- Drag-drop PDF upload zone -->

        <div id="pdf-drop-zone" style="border:2px dashed var(--border);border-radius:var(--radius-sm);padding:32px;text-align:center;margin-bottom:8px;cursor:pointer;transition:all 0.3s ease;background:var(--card);"

          ondragover="event.preventDefault();this.style.borderColor='var(--primary)';this.style.background='rgba(139,92,246,0.05)'"

          ondragleave="this.style.borderColor='var(--border)';this.style.background='var(--card)'"

          ondrop="event.preventDefault();this.style.borderColor='var(--border)';this.style.background='var(--card)';handlePDFDrop(event.dataTransfer.files)"

          onclick="document.getElementById('pdf-file-input').click()">

          <div style="font-size:36px;margin-bottom:8px;">&#128196;</div>

          <p style="margin:0;font-weight:600;color:var(--text);">Drag & drop PDF or DOCX files here</p>

          <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">or click to browse &middot; multi-chapter PDFs fully supported</p>

          <input type="file" id="pdf-file-input" style="display:none;" accept=".pdf,.docx,.doc,.txt" multiple onchange="handlePDFDrop(this.files)">

        </div>

        <div id="pdf-upload-status" style="display:none;margin-bottom:16px;padding:12px;border-radius:var(--radius-sm);background:var(--card);border:1px solid var(--border);text-align:center;color:var(--text-muted);font-size:14px;"></div>



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

            var d = await _safeJson(r);

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

        async function toggleShare(noteId, isPublic) {{

          if (isPublic) {{

            await fetch('/api/student/exchange/unpublish', {{

              method:'POST', headers:{{'Content-Type':'application/json'}},

              body:JSON.stringify({{note_id:noteId}})

            }});

          }} else {{

            await fetch('/api/student/exchange/publish', {{

              method:'POST', headers:{{'Content-Type':'application/json'}},

              body:JSON.stringify({{note_id:noteId}})

            }});

          }}

          location.reload();

        }}

        async function handlePDFDrop(files) {{

          if (!files || files.length === 0) return;

          var valid = [];

          for (var i = 0; i < files.length; i++) {{

            var name = files[i].name.toLowerCase();

            if (!name.endsWith('.pdf') && !name.endsWith('.docx') && !name.endsWith('.doc') && !name.endsWith('.txt')) continue;

            if (files[i].size > 50 * 1024 * 1024) {{ alert(files[i].name + ' es demasiado grande (máx 50MB)'); continue; }}

            valid.push(files[i]);

          }}

          if (valid.length === 0) {{ alert('Only PDF, DOCX, and TXT files are supported'); return; }}

          var status = document.getElementById('pdf-upload-status');

          status.style.display = 'block';

          var lastNoteId = null;

          var ok = 0, fail = 0;

          var lastError = '';

          for (var i = 0; i < valid.length; i++) {{

            var file = valid[i];

            status.innerHTML = '&#129504; AI-summarizing ' + (i+1) + '/' + valid.length + ': <b>' + file.name + '</b> (multi-chapter, may take 1-3 min)...';

            try {{

                // 1) extract text

                var fd1 = new FormData(); fd1.append('file', file);

                var rx = await fetch('/api/student/extract-file', {{ method:'POST', body: fd1 }});

                var dx = await _safeJson(rx);

                if (!rx.ok) {{ lastError = 'Extract failed (' + rx.status + '): ' + (dx.error || 'unknown'); fail++; continue; }}

                // 2) AI generate notes from extracted text

                var rg = await fetch('/api/student/notes/generate', {{

                  method:'POST', headers:{{'Content-Type':'application/json'}},

                  body: JSON.stringify({{ source_text: dx.text, title: dx.title }})

                }});

                var dg = await _safeJson(rg);

                if (rg.ok && dg.note_id) {{ lastNoteId = dg.note_id; ok++; }}

                else {{ lastError = 'AI notes failed (' + rg.status + '): ' + (dg.error || 'timeout or server error'); fail++; }}

            }} catch(e) {{ lastError = 'Network: ' + (e && e.message ? e.message : e); fail++; }}

          }}

          if (ok > 0 && valid.length === 1) {{ window.location = '/student/notes/' + lastNoteId; }}

          else if (ok > 0) {{ status.innerHTML = '&#9989; ' + ok + ' notes created' + (fail ? ', ' + fail + ' failed' : '') + '. Reloading...'; setTimeout(function(){{ location.reload(); }}, 1200); }}

          else {{ status.innerHTML = '&#10060; All uploads failed' + (lastError ? '<br><span style="font-size:12px;color:var(--text-muted);">' + lastError + '</span>' : ''); }}

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

            <a href="/api/student/notes/{note_id}/pdf" class="btn btn-outline btn-sm">&#128196; Export PDF</a>

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

        // Re-render math in notes

        if (typeof renderMathInElement === 'function') {{

          renderMathInElement(document.getElementById('note-view'), {{

            delimiters: [

              {{left: '$$', right: '$$', display: true}},

              {{left: '$', right: '$', display: false}},

              {{left: '\\\\(', right: '\\\\)', display: false}},

              {{left: '\\\\[', right: '\\\\]', display: true}}

            ], throwOnError: false

          }});

        }}

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



        # Build context from uploaded files, notes, and syllabus

        context_text = ""

        course_name = "General"

        if course_id:

            course = sdb.get_course(int(course_id))

            if course:

                course_name = course.get("name", "General")

                # PRIMARY: uploaded course files (the student's actual documents)

                files = sdb.get_course_files(cid, int(course_id))

                for f in files:

                    if f.get("extracted_text"):

                        context_text += f"--- File: {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

                # SECONDARY: AI-generated notes

                notes = sdb.get_notes(cid, int(course_id))

                for n in notes:

                    context_text += (n.get("content_html") or "") + "\n"



        # Get recent history

        history_rows = sdb.get_chat_history(cid, int(course_id) if course_id else None, limit=20)

        history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]



        # Save user message

        sdb.add_chat_message(cid, "user", msg, int(course_id) if course_id else None)



        # Get AI reply

        reply = chat_with_tutor(course_name, msg, history, context_text)



        # Save assistant reply

        sdb.add_chat_message(cid, "assistant", reply, int(course_id) if course_id else None)



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

               overflow-y:auto;padding:16px;background:var(--bg);margin-bottom:12px"

               ondragover="event.preventDefault();this.style.borderColor='var(--primary)'"

               ondragleave="this.style.borderColor='var(--border)'"

               ondrop="chatHandleDrop(event)"></div>

          <div id="chat-attached" style="display:none;margin-bottom:8px;padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:8px;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:8px;">

            <span id="chat-attached-info">📄 No file attached</span>

            <button onclick="chatClearAttached()" style="background:transparent;border:none;color:var(--red);cursor:pointer;font-size:16px;">✕</button>

          </div>

          <div style="display:flex;gap:8px">

            <button onclick="document.getElementById('chat-file').click()" title="Attach a file (PDF/DOCX/TXT)"

              style="padding:10px 12px;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:8px;cursor:pointer">📎</button>

            <input type="file" id="chat-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="chatHandleFile(this.files[0])">

            <input id="chat-input" type="text" placeholder="Ask your tutor... (or drag a PDF onto the chat)"

              style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:8px;font-size:15px;

                     background:var(--card);color:var(--text)"

              onkeydown="if(event.key==='Enter')sendMsg()">

            <button onclick="sendMsg()"

              style="padding:10px 20px;background:var(--primary);color:#fff;border:none;border-radius:8px;

                     font-weight:600;cursor:pointer">Send</button>

            <button onclick="clearChat()" title="Clear history"

              style="padding:10px 12px;background:var(--red);color:#fff;border:none;border-radius:8px;

                     cursor:pointer">🗑</button>

          </div>

        </div>

        <script>

        var chatBox = document.getElementById('chat-box');

        var chatAttachedText = "";

        var chatAttachedName = "";

        async function chatHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.style.borderColor = 'var(--border)';

          if (e.dataTransfer.files.length) await chatHandleFile(e.dataTransfer.files[0]);

        }}

        async function chatHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('PDF, DOCX, or TXT only'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var bar = document.getElementById('chat-attached');

          var info = document.getElementById('chat-attached-info');

          bar.style.display = 'flex';

          info.textContent = '⏳ Extracting ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await _safeJson(r);

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            chatAttachedText = d.text;

            chatAttachedName = d.filename;

            info.textContent = '📄 Attached: ' + d.filename + ' (' + d.char_count.toLocaleString() + ' chars) — your next message will use this as context';

          }} catch(e) {{ info.textContent = '❌ Network error'; }}

        }}

        function chatClearAttached() {{

          chatAttachedText = ""; chatAttachedName = "";

          document.getElementById('chat-attached').style.display = 'none';

        }}

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

          if (!msg && !chatAttachedText) return;

          if (!msg) msg = "Please summarize and explain the attached document.";

          inp.value = '';

          var displayMsg = msg + (chatAttachedText ? ' [📄 ' + chatAttachedName + ']' : '');

          addBubble('user', displayMsg);

          addBubble('assistant', '💭 Thinking...');

          // Build payload: prepend attached doc as context if present

          var payloadMsg = msg;

          if (chatAttachedText) {{

            payloadMsg = "I am attaching a document called \\"" + chatAttachedName + "\\". Use its contents as the primary source for your answer.\\n\\n=== DOCUMENT START ===\\n" + chatAttachedText + "\\n=== DOCUMENT END ===\\n\\nMy question: " + msg;

            chatClearAttached();

          }}

          try {{

            var r = await fetch('/api/student/chat', {{

              method:'POST', headers:{{'Content-Type':'application/json'}},

              body: JSON.stringify({{message: payloadMsg, course_id: document.getElementById('chat-course').value || null}})

            }});

            chatBox.removeChild(chatBox.lastChild);

            var d = await _safeJson(r);

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

            if score < 50:

                color = "var(--red)"

            elif score < 75:

                color = "var(--yellow)"

            else:

                color = "var(--green)"

            weak_html += f"""

            <div style="padding:14px 18px;background:var(--card);

                        border:1px solid var(--border);border-left:4px solid {color};

                        border-radius:var(--radius-sm);margin-bottom:10px;transition:transform 0.15s, box-shadow 0.2s"

                 onmouseover="this.style.transform='translateX(4px)';this.style.boxShadow='var(--shadow-md)'" onmouseout="this.style.transform='';this.style.boxShadow=''">

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

        for key, threshold in [("streak_3", 3), ("streak_7", 7), ("streak_14", 14), ("streak_30", 30), ("streak_60", 60), ("streak_100", 100)]:

            if streak >= threshold:

                sdb.earn_badge(cid, key)

        for key, threshold in [("xp_100", 100), ("xp_500", 500), ("xp_1000", 1000), ("xp_2500", 2500), ("xp_5000", 5000)]:

            if total_xp >= threshold:

                sdb.earn_badge(cid, key)

        # Course badges

        courses = sdb.get_courses(cid)

        n_courses = len(courses) if courses else 0

        if n_courses >= 1:

            sdb.earn_badge(cid, "first_course")

        if n_courses >= 5:

            sdb.earn_badge(cid, "five_courses")

        # Perfect week = streak >= 7

        if streak >= 7:

            sdb.earn_badge(cid, "perfect_week")



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



    # ── Study Exchange ──────────────────────────────────────



    @app.route("/student/exchange")

    def student_exchange_page():

        """Study Exchange — browse/share notes like Studocu."""

        if not _logged_in():

            return redirect(url_for("login"))



        search = request.args.get("q", "")

        subject = request.args.get("subject", "")

        university = request.args.get("university", "")

        notes = sdb.browse_public_notes(search=search, subject=subject, university=university)



        notes_html = ""

        for n in notes:

            length_kb = round((n.get("content_length", 0) or 0) / 1024, 1)

            notes_html += f"""

            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/exchange/{n['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:start;">

                <div>

                  <h3 style="margin:0;font-size:16px;">{_esc(n.get('title','Untitled'))}</h3>

                  <span style="font-size:13px;color:var(--text-muted);">

                    {_esc(n.get('course_name',''))}

                    {(' &middot; ' + _esc(n.get('university',''))) if n.get('university') else ''}

                    &middot; {_esc(n.get('source_type','ai'))} &middot; {length_kb}KB

                  </span>

                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">

                    &#128100; {_esc(n.get('author_name','Anonymous'))} &middot; {str(n.get('created_at',''))[:10]}

                  </div>

                </div>

                <div style="display:flex;align-items:center;gap:4px;color:var(--text-muted);font-size:14px;">

                  <span style="color:#EF4444;">&#10084;</span> {n.get('likes',0)}

                </div>

              </div>

            </div>"""

        if not notes_html:

            notes_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">

              <div style="font-size:48px;margin-bottom:12px;">&#128218;</div>

              <p>No shared notes yet. Be the first to share!</p>

            </div>"""



        return _s_render("Study Exchange", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">

          <div>

            <h1 style="margin:0;">&#128218; Study Exchange</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Browse & share study notes with other students</p>

          </div>

          <a href="/student/exchange/my" class="btn btn-outline btn-sm">&#128196; My Shared Notes</a>

        </div>



        <!-- Search/filter -->

        <div class="card" style="padding:16px;margin-bottom:20px;">

          <form method="get" style="display:flex;gap:10px;flex-wrap:wrap;">

            <input name="q" value="{_esc(search)}" placeholder="Search notes..." style="flex:1;min-width:200px;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:14px;">

            <input name="subject" value="{_esc(subject)}" placeholder="Subject/Course" style="width:180px;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:14px;">

            <input name="university" value="{_esc(university)}" placeholder="University" style="width:180px;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:14px;">

            <button type="submit" class="btn btn-primary btn-sm">&#128269; Search</button>

          </form>

        </div>



        {notes_html}

        """, active_page="student_exchange")



    @app.route("/student/exchange/my")

    def student_exchange_my_page():

        """Manage your shared notes."""

        if not _logged_in():

            return redirect(url_for("login"))

        notes = sdb.get_notes(_cid())

        prefs = sdb.get_email_prefs(_cid())

        author_name = prefs.get("name", "") if prefs else ""

        university = prefs.get("university", "") if prefs else ""



        my_html = ""

        for n in notes:

            is_pub = n.get("is_public", False)

            pub_badge = '<span style="background:#10B98122;color:#10B981;padding:2px 8px;border-radius:10px;font-size:11px;">Public</span>' if is_pub else '<span style="background:var(--bg);color:var(--text-muted);padding:2px 8px;border-radius:10px;font-size:11px;">Private</span>'

            action = f'<button onclick="unpublish({n["id"]})" class="btn btn-ghost btn-sm" style="font-size:12px;color:#EF4444;">Unpublish</button>' if is_pub else f'<button onclick="publish({n["id"]})" class="btn btn-ghost btn-sm" style="font-size:12px;color:#10B981;">Share</button>'

            my_html += f"""

            <div class="card" style="margin-bottom:10px;">

              <div style="display:flex;justify-content:space-between;align-items:center;">

                <div>

                  <h3 style="margin:0;font-size:15px;">{_esc(n.get('title','Untitled'))}</h3>

                  <span style="font-size:12px;color:var(--text-muted);">{str(n.get('created_at',''))[:10]}</span>

                </div>

                <div style="display:flex;gap:8px;align-items:center;">

                  {pub_badge}

                  {action}

                </div>

              </div>

            </div>"""

        if not my_html:

            my_html = '<p style="text-align:center;color:var(--text-muted);padding:30px;">No notes to share. Create notes first!</p>'



        return _s_render("My Shared Notes", f"""

        <a href="/student/exchange" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Exchange</a>

        <h1 style="margin:8px 0 20px;">&#128196; My Shared Notes</h1>

        <p style="color:var(--text-muted);font-size:14px;margin-bottom:20px;">Click "Share" to publish your notes to the Study Exchange. Other students can view and fork them.</p>

        {my_html}

        <script>

        async function publish(noteId) {{

          try {{

            var r = await fetch('/api/student/exchange/publish', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ note_id: noteId }})

            }});

            if (r.ok) location.reload();

            else alert('Failed to publish');

          }} catch(e) {{ alert('Error'); }}

        }}

        async function unpublish(noteId) {{

          try {{

            var r = await fetch('/api/student/exchange/unpublish', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ note_id: noteId }})

            }});

            if (r.ok) location.reload();

            else alert('Failed to unpublish');

          }} catch(e) {{ alert('Error'); }}

        }}

        </script>

        """, active_page="student_exchange")



    @app.route("/student/exchange/<int:note_id>")

    def student_exchange_view_page(note_id):

        """View a public note in the exchange."""

        if not _logged_in():

            return redirect(url_for("login"))

        note = sdb.get_public_note(note_id)

        if not note:

            return redirect(url_for("student_exchange_page"))



        liked = sdb.has_liked_note(_cid(), note_id)

        like_class = "color:#EF4444;font-weight:700;" if liked else "color:var(--text-muted);"



        return _s_render(f"Exchange: {note.get('title','')}", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">

          <div>

            <a href="/student/exchange" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Exchange</a>

            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(note.get('title',''))}</h1>

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">

              &#128100; {_esc(note.get('author_name','Anonymous'))}

              {(' &middot; ' + _esc(note.get('university',''))) if note.get('university') else ''}

              &middot; {_esc(note.get('course_name',''))}

              &middot; {str(note.get('created_at',''))[:10]}

            </p>

          </div>

          <div style="display:flex;gap:8px;">

            <button onclick="toggleLike()" class="btn btn-outline btn-sm" id="like-btn" style="{like_class}">

              &#10084; <span id="like-count">{note.get('likes',0)}</span>

            </button>

            <button onclick="forkNote()" class="btn btn-primary btn-sm">&#128203; Fork to My Notes</button>

          </div>

        </div>



        <div class="card" style="padding:30px;">

          {note.get('content_html','')}

        </div>



        <style>

        @media print {{

          body * {{ visibility: hidden; }}

          .card, .card * {{ visibility: visible; }}

          .card {{ position: absolute; left: 0; top: 0; width: 100%; }}

        }}

        </style>

        <script>

        async function toggleLike() {{

          try {{

            var r = await fetch('/api/student/exchange/like', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ note_id: {note_id} }})

            }});

            var d = await _safeJson(r);

            if (r.ok) {{

              document.getElementById('like-count').textContent = d.likes;

              var btn = document.getElementById('like-btn');

              btn.style.color = d.liked ? '#EF4444' : 'var(--text-muted)';

              btn.style.fontWeight = d.liked ? '700' : '400';

            }}

          }} catch(e) {{}}

        }}

        async function forkNote() {{

          if (!confirm('Copy this note to your personal notes?')) return;

          try {{

            var r = await fetch('/api/student/exchange/fork', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ note_id: {note_id} }})

            }});

            var d = await _safeJson(r);

            if (r.ok && d.note_id) {{

              window.location = '/student/notes/' + d.note_id;

            }} else {{ alert(d.error || 'Fork failed'); }}

          }} catch(e) {{ alert('Error'); }}

        }}

        </script>

        """, active_page="student_exchange")



    # ── GPA Country Setting API ─────────────────────────────



    @app.route("/api/student/settings/gpa-country", methods=["POST"])

    def student_set_gpa_country():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        country = data.get("country", "us")

        allowed = {"us","uk","mx","ar","co","cl","br","de","fr","es","in","au","jp"}

        if country not in allowed:

            country = "us"

        sdb.set_gpa_country(_cid(), country)

        return jsonify({"ok": True})



    # ── Study Exchange API ──────────────────────────────────



    @app.route("/api/student/exchange/publish", methods=["POST"])

    def student_exchange_publish():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        note_id = data.get("note_id")

        if not note_id:

            return jsonify({"error": "note_id required"}), 400

        # Verify ownership

        note = sdb.get_note(note_id, _cid())

        if not note:

            return jsonify({"error": "Note not found"}), 404

        prefs = sdb.get_email_prefs(_cid())

        author = prefs.get("name", "Anonymous") if prefs else "Anonymous"

        uni = prefs.get("university", "") if prefs else ""

        sdb.publish_note(note_id, _cid(), author, uni)

        sdb.earn_badge(_cid(), "sharer")

        return jsonify({"ok": True})



    @app.route("/api/student/exchange/unpublish", methods=["POST"])

    def student_exchange_unpublish():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        sdb.unpublish_note(data.get("note_id"), _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/exchange/like", methods=["POST"])

    def student_exchange_like():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        note_id = data.get("note_id")

        liked = sdb.toggle_note_like(_cid(), note_id)

        note = sdb.get_public_note(note_id)

        # Award popular/viral note badges to the author

        if liked and note and note.get("client_id"):

            likes = note.get("likes", 0)

            author_id = note["client_id"]

            if likes >= 5:

                sdb.earn_badge(author_id, "popular_note")

            if likes >= 25:

                sdb.earn_badge(author_id, "viral_note")

        return jsonify({"ok": True, "liked": liked, "likes": note.get("likes", 0) if note else 0})



    @app.route("/api/student/exchange/fork", methods=["POST"])

    def student_exchange_fork():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        note_id = data.get("note_id")

        # Get public note to find author

        pub_note = sdb.get_public_note(note_id)

        new_id = sdb.fork_note(_cid(), note_id)

        if not new_id:

            return jsonify({"error": "Note not found or not public"}), 404

        # Award XP to the original author (once per unique forker)

        if pub_note and pub_note.get("client_id"):

            author_id = pub_note["client_id"]

            is_new = sdb.record_note_fork(note_id, _cid(), author_id)

            if is_new and author_id != _cid():

                sdb.award_xp(author_id, "note_used", 5, f"Your note was used by a student")

                # Check helper badges

                fork_count = sdb.get_note_fork_count(author_id)

                for key, threshold in [("helper_5", 5), ("helper_25", 25), ("helper_100", 100)]:

                    if fork_count >= threshold:

                        sdb.earn_badge(author_id, key)

        return jsonify({"ok": True, "note_id": new_id})



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

        for key, threshold in [("streak_3", 3), ("streak_7", 7), ("streak_14", 14), ("streak_30", 30), ("streak_60", 60), ("streak_100", 100)]:

            if streak >= threshold:

                sdb.earn_badge(cid, key)

        for key, threshold in [("xp_100", 100), ("xp_500", 500), ("xp_1000", 1000), ("xp_2500", 2500), ("xp_5000", 5000)]:

            if total_xp >= threshold:

                sdb.earn_badge(cid, key)

        # Auto-check helper badges

        fork_count = sdb.get_note_fork_count(cid)

        for key, threshold in [("helper_5", 5), ("helper_25", 25), ("helper_100", 100)]:

            if fork_count >= threshold:

                sdb.earn_badge(cid, key)

        badges = sdb.get_badges(cid)



        pct = min(100, int(100 * (total_xp - level_floor) / max(1, level_ceil - level_floor)))



        badges_html = ""

        for b in badges:

            earned_date = str(b.get("earned_at", ""))[:10]

            badges_html += f"""

            <div class="badge-card" style="text-align:center;padding:16px 12px;background:var(--card);border-radius:var(--radius);

                        border:1px solid var(--border);min-width:110px;flex:1;max-width:160px;

                        transition:transform 0.2s,box-shadow 0.2s;cursor:default;position:relative"

                 onmouseover="this.style.transform='translateY(-3px)';this.style.boxShadow='var(--shadow-md)';this.querySelector('.badge-tooltip').style.opacity='1';this.querySelector('.badge-tooltip').style.visibility='visible'"

                 onmouseout="this.style.transform='';this.style.boxShadow='';this.querySelector('.badge-tooltip').style.opacity='0';this.querySelector('.badge-tooltip').style.visibility='hidden'">

              <div style="font-size:2.2em;margin-bottom:4px">{b.get('emoji','🏅')}</div>

              <div style="font-weight:700;font-size:13px;color:var(--text)">{_esc(b.get('name',''))}</div>

              <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{_esc(b.get('desc',''))}</div>

              <div class="badge-tooltip" style="position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);

                          background:var(--text);color:var(--bg);padding:8px 12px;border-radius:8px;font-size:12px;

                          white-space:nowrap;opacity:0;visibility:hidden;transition:opacity 0.2s;z-index:10;

                          pointer-events:none;box-shadow:0 4px 12px rgba(0,0,0,0.2)">

                <div style="font-weight:700;margin-bottom:2px">{_esc(b.get('name',''))}</div>

                <div>{_esc(b.get('desc',''))}</div>

                <div style="opacity:0.7;margin-top:3px">Earned: {earned_date}</div>

                <div style="position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:var(--text)"></div>

              </div>

            </div>"""



        # All possible badges with tooltips

        all_badges_html = ""

        for key, info in sdb.BADGE_DEFS.items():

            earned = any(b["badge_key"] == key for b in badges)

            opacity = "1" if earned else "0.25"

            border = "var(--primary)" if earned else "var(--border)"

            status_text = "Earned!" if earned else "Not yet earned"

            status_color = "#22c55e" if earned else "#94a3b8"

            all_badges_html += f"""

            <div style="text-align:center;padding:10px 8px;opacity:{opacity};min-width:90px;flex:1;max-width:120px;

                        border:1px solid {border};border-radius:var(--radius-sm);background:var(--card);

                        transition:all 0.2s;cursor:default;position:relative"

                 onmouseover="this.style.opacity='1';this.querySelector('.badge-tooltip').style.opacity='1';this.querySelector('.badge-tooltip').style.visibility='visible'"

                 onmouseout="this.style.opacity='{opacity}';this.querySelector('.badge-tooltip').style.opacity='0';this.querySelector('.badge-tooltip').style.visibility='hidden'">

              <div style="font-size:1.6em">{info['emoji']}</div>

              <div style="font-size:11px;font-weight:600;color:var(--text);margin-top:2px">{_esc(info['name'])}</div>

              <div class="badge-tooltip" style="position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);

                          background:var(--text);color:var(--bg);padding:8px 12px;border-radius:8px;font-size:12px;

                          white-space:nowrap;opacity:0;visibility:hidden;transition:opacity 0.2s;z-index:10;

                          pointer-events:none;box-shadow:0 4px 12px rgba(0,0,0,0.2)">

                <div style="font-weight:700;margin-bottom:2px">{_esc(info['name'])}</div>

                <div>{_esc(info['desc'])}</div>

                <div style="color:{status_color};margin-top:3px;font-weight:600">{status_text}</div>

                <div style="position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:var(--text)"></div>

              </div>

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

    @csrf.exempt

    def student_email_prefs_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        if request.method == "GET":

            prefs = sdb.get_email_prefs(cid)

            if not prefs:

                return jsonify(daily_email=True, email_hour=7, timezone="America/Mexico_City",

                               university="", field_of_study="")

            return jsonify(

                daily_email=bool(prefs.get("daily_email")),

                email_hour=prefs.get("email_hour", 7),

                timezone=prefs.get("timezone", "America/Mexico_City"),

                university=prefs.get("university", ""),

                field_of_study=prefs.get("field_of_study", ""),

            )

        try:

            data = request.get_json(force=True) or {}

            sdb.upsert_email_prefs(

                cid,

                daily_email=bool(data.get("daily_email", True)),

                email_hour=int(data.get("email_hour", 7)),

                timezone=str(data.get("timezone", "America/Mexico_City"))[:64],

                university=str(data.get("university", ""))[:120],

                field_of_study=str(data.get("field_of_study", ""))[:120],

                lang=session.get("lang", "en"),

            )

            return jsonify(ok=True)

        except Exception as e:

            import logging, traceback

            logging.getLogger("student.routes").error("email-prefs save failed: %s\n%s", e, traceback.format_exc())

            return jsonify(error=f"Could not save preferences: {str(e)[:120]}"), 500



    # ================================================================

    #  LEADERBOARD / RANKINGS

    # ================================================================



    @app.route("/student/leaderboard")

    def student_leaderboard_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        prefs = sdb.get_email_prefs(cid) or {}

        my_uni = prefs.get("university", "")

        filter_uni = request.args.get("university", "")



        leaders = sdb.get_leaderboard(limit=50, university=filter_uni)

        my_rank = sdb.get_student_rank(cid)

        my_xp = sdb.get_total_xp(cid)

        my_study_rank = sdb.get_study_rank(my_xp)

        my_level = my_study_rank["full_name"]

        my_groups = sdb.get_my_lb_groups(cid)



        rows_html = ""

        for i, r in enumerate(leaders, 1):

            is_me = (r["client_id"] == cid)

            bg = "background:rgba(99,102,241,0.08);" if is_me else ""

            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")

            rank_info = sdb.get_study_rank(r["total_xp"])

            lvl_name = rank_info["full_name"]

            lvl_color = rank_info["color"]

            name_display = _esc(r["name"] or "Student")

            uni_display = _esc(r.get("university", "") or "")

            field_display = _esc(r.get("field_of_study", "") or "")

            sub_text = f"{uni_display}" if uni_display else ""

            if field_display:

                sub_text += f" — {field_display}" if sub_text else field_display

            rows_html += f"""

            <tr style="{bg}">

              <td style="padding:12px 16px;font-size:18px;font-weight:700;text-align:center;width:60px">{medal}</td>

              <td style="padding:12px 16px">

                <div style="font-weight:600;color:var(--text)">{name_display}{"  ← you" if is_me else ""}</div>

                {"<div style='font-size:12px;color:var(--text-muted)'>" + sub_text + "</div>" if sub_text else ""}

              </td>

              <td style="padding:12px 16px;text-align:center">

                <span style="background:linear-gradient(135deg,{lvl_color},#111827);color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;border:1px solid {lvl_color}">{lvl_name}</span>

              </td>

              <td style="padding:12px 16px;text-align:right;font-weight:700;color:#22c55e;font-size:16px">{r['total_xp']} XP</td>

            </tr>"""



        if not rows_html:

            rows_html = '<tr><td colspan="4" style="padding:32px;text-align:center;color:var(--text-muted)">No students on the leaderboard yet. Start earning XP!</td></tr>'



        # Filter options

        filter_html = ""

        if my_uni:

            active_all = "" if filter_uni else "background:var(--primary);color:#fff;"

            active_uni = "background:var(--primary);color:#fff;" if filter_uni == my_uni else ""

            filter_html = f"""

            <div style="display:flex;gap:8px;margin-bottom:20px">

              <a href="/student/leaderboard" class="btn btn-sm" style="border:1px solid var(--border);border-radius:8px;padding:6px 16px;text-decoration:none;font-size:13px;{active_all}">🌍 All Students</a>

              <a href="/student/leaderboard?university={_esc(my_uni)}" class="btn btn-sm" style="border:1px solid var(--border);border-radius:8px;padding:6px 16px;text-decoration:none;font-size:13px;{active_uni}">🏫 {_esc(my_uni)}</a>

            </div>"""



        # Build personal groups HTML

        groups_html = ""

        for g in my_groups:

            gname = _esc(g.get("name", ""))

            gcode = _esc(g.get("invite_code", ""))

            gid = g["id"]

            is_owner = g.get("is_owner")

            mc = g.get("member_count", 0)

            actions = f'<button class="btn btn-ghost btn-sm" onclick="copyInvite(\'{gcode}\')" style="font-size:12px">&#128279; Copy Invite</button>'

            if is_owner:

                actions += f' <button class="btn btn-ghost btn-sm" onclick="deleteLbGroup({gid})" style="font-size:12px;color:var(--red)">&#128465; Delete</button>'

            else:

                actions += f' <button class="btn btn-ghost btn-sm" onclick="leaveLbGroup({gid})" style="font-size:12px;color:var(--red)">Leave</button>'

            groups_html += f"""

            <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:10px">

              <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">

                <div>

                  <a href="/student/leaderboard/group/{gid}" style="font-weight:700;font-size:16px;color:var(--text);text-decoration:none">{gname}</a>

                  <div style="font-size:12px;color:var(--text-muted)">{mc} member{'s' if mc != 1 else ''} &middot; Code: <code style="background:var(--bg);padding:2px 6px;border-radius:4px;font-size:11px">{gcode}</code></div>

                </div>

                <div style="display:flex;gap:4px">{actions}</div>

              </div>

            </div>"""

        if not groups_html:

            groups_html = '<p style="color:var(--text-muted);text-align:center;padding:20px 0;font-size:14px">No groups yet. Create one and invite your friends!</p>'



        return _s_render("Leaderboard", f"""

        <div style="max-width:800px;margin:0 auto">

          <h2 style="margin-bottom:8px">🏆 Student Rankings</h2>

          <p style="color:var(--text-muted);margin-bottom:20px;font-size:14px">

            Compete with other students! Earn XP from focus sessions, quizzes, and flashcards.

          </p>



          <!-- My rank card -->

          <div style="background:linear-gradient(135deg,#6366f1 0%,#8b5cf6 50%,#a855f7 100%);color:#fff;

                      border-radius:var(--radius);padding:20px 28px;margin-bottom:24px;

                      display:flex;align-items:center;justify-content:space-between;

                      box-shadow:0 8px 32px rgba(99,102,241,0.3)">

            <div>

              <div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1px;font-weight:600">Your Rank</div>

              <div style="font-size:2.4em;font-weight:800">#{my_rank if my_rank else '—'}</div>

            </div>

            <div style="text-align:center">

              <div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1px;font-weight:600">Level</div>

              <div style="font-size:1.3em;font-weight:700">{_esc(my_level)}</div>

            </div>

            <div style="text-align:right">

              <div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1px;font-weight:600">Total XP</div>

              <div style="font-size:2.4em;font-weight:800">{my_xp}</div>

            </div>

          </div>



          {filter_html}



          <!-- Leaderboard table -->

          <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">

            <table style="width:100%;border-collapse:collapse">

              <thead>

                <tr style="border-bottom:2px solid var(--border)">

                  <th style="padding:12px 16px;text-align:center;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Rank</th>

                  <th style="padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Student</th>

                  <th style="padding:12px 16px;text-align:center;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Level</th>

                  <th style="padding:12px 16px;text-align:right;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">XP</th>

                </tr>

              </thead>

              <tbody>

                {rows_html}

              </tbody>

            </table>

          </div>



          <!-- Personal Leaderboards -->

          <h2 style="margin-top:40px;margin-bottom:8px">&#128101; Personal Leaderboards <span style="font-size:12px;background:rgba(34,197,94,.15);color:#22c55e;padding:3px 10px;border-radius:10px;font-weight:600;vertical-align:middle">Fair&#8209;play</span></h2>

          <p style="color:var(--text-muted);margin-bottom:20px;font-size:14px;line-height:1.6">

            Create a private group and invite friends. <b style="color:var(--text)">Everyone starts at 0 XP</b> the moment they join &mdash; the scoreboard only counts XP gained inside the group, so older accounts don&rsquo;t have an unfair head start. No levels, no ranks: just who&rsquo;s grinding hardest right now.

          </p>



          <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">

            <button onclick="document.getElementById('create-group-form').style.display='block'" class="btn btn-primary btn-sm">&#43; Create Group</button>

            <button onclick="document.getElementById('join-group-form').style.display='block'" class="btn btn-outline btn-sm">&#128279; Join with Code</button>

          </div>



          <!-- Create group form -->

          <div id="create-group-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">

            <h3 style="font-size:16px;margin-bottom:12px">Create a Group</h3>

            <div class="form-group"><label>Group Name</label><input id="lb-group-name" placeholder="e.g. Study Squad, CS101 Friends" maxlength="60"></div>

            <div style="display:flex;gap:8px">

              <button class="btn btn-primary btn-sm" onclick="createLbGroup()">Create</button>

              <button class="btn btn-ghost btn-sm" onclick="document.getElementById('create-group-form').style.display='none'">Cancel</button>

            </div>

          </div>



          <!-- Join group form -->

          <div id="join-group-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px">

            <h3 style="font-size:16px;margin-bottom:12px">Join a Group</h3>

            <div class="form-group"><label>Invite Code</label><input id="lb-join-code" placeholder="Paste invite code here" maxlength="20"></div>

            <div style="display:flex;gap:8px">

              <button class="btn btn-primary btn-sm" onclick="joinLbGroup()">Join</button>

              <button class="btn btn-ghost btn-sm" onclick="document.getElementById('join-group-form').style.display='none'">Cancel</button>

            </div>

          </div>



          {groups_html}



          <script>

          async function createLbGroup() {{

            var name = document.getElementById('lb-group-name').value.trim();

            if (!name) return alert('Please enter a group name');

            var r = await fetch('/api/student/leaderboard/groups', {{

              method:'POST',headers:{{'Content-Type':'application/json'}},

              body:JSON.stringify({{name:name}})

            }});

            var d = await r.json();

            if (d.ok) {{ window.location.reload(); }} else {{ alert(d.error || 'Failed'); }}

          }}

          async function joinLbGroup() {{

            var code = document.getElementById('lb-join-code').value.trim();

            if (!code) return alert('Please enter an invite code');

            var r = await fetch('/api/student/leaderboard/join', {{

              method:'POST',headers:{{'Content-Type':'application/json'}},

              body:JSON.stringify({{invite_code:code}})

            }});

            var d = await r.json();

            if (d.ok) {{ window.location.reload(); }} else {{ alert(d.error || 'Invalid invite code'); }}

          }}

          async function leaveLbGroup(gid) {{

            if (!confirm('Leave this group?')) return;

            var r = await fetch('/api/student/leaderboard/leave', {{

              method:'POST',headers:{{'Content-Type':'application/json'}},

              body:JSON.stringify({{group_id:gid}})

            }});

            if ((await r.json()).ok) window.location.reload();

          }}

          async function deleteLbGroup(gid) {{

            if (!confirm('Delete this group? All members will be removed.')) return;

            var r = await fetch('/api/student/leaderboard/groups/' + gid, {{method:'DELETE'}});

            if ((await r.json()).ok) window.location.reload();

          }}

          function copyInvite(code) {{

            navigator.clipboard.writeText(code).then(function(){{

              alert('Invite code copied!');

            }});

          }}

          </script>

        </div>

        """, active_page="student_leaderboard")



    @app.route("/student/leaderboard/group/<int:group_id>")

    def student_lb_group_page(group_id):

        """View a personal leaderboard group."""

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        group = sdb.get_lb_group(group_id)

        if not group or not sdb.is_lb_member(cid, group_id):

            return redirect(url_for("student_leaderboard_page"))

        members = sdb.get_lb_group_leaderboard(group_id)

        rows_html = ""

        top_xp = max((r["total_xp"] for r in members), default=0)

        for i, r in enumerate(members, 1):

            is_me = (r["client_id"] == cid)

            bg = "background:rgba(99,102,241,0.08);" if is_me else ""

            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")

            name_display = _esc(r["name"] or "Student")

            xp_val = int(r.get("total_xp") or 0)

            bar_pct = int(100 * xp_val / top_xp) if top_xp > 0 else 0

            rows_html += f"""

            <tr style="{bg}">

              <td style="padding:12px 16px;font-size:18px;font-weight:700;text-align:center;width:60px">{medal}</td>

              <td style="padding:12px 16px"><div style="font-weight:600;color:var(--text)">{name_display}{"  ← you" if is_me else ""}</div></td>

              <td style="padding:12px 16px;min-width:160px"><div style="background:var(--bg);border-radius:8px;height:10px;overflow:hidden"><div style="width:{bar_pct}%;height:100%;background:linear-gradient(90deg,#8b5cf6,#22c55e)"></div></div></td>

              <td style="padding:12px 16px;text-align:right;font-weight:700;color:#22c55e;font-size:16px">+{xp_val} XP</td>

            </tr>"""

        if not rows_html:

            rows_html = '<tr><td colspan="4" style="padding:32px;text-align:center;color:var(--text-muted)">No members yet!</td></tr>'

        is_owner = group["owner_id"] == cid

        # Format group creation date for the "since" label

        created_str = ""

        try:

            _ca = group.get("created_at")

            if _ca:

                created_str = _ca.strftime("%b %d, %Y") if hasattr(_ca, "strftime") else str(_ca)[:10]

        except Exception:

            created_str = ""

        return _s_render(f"Group: {_esc(group['name'])}", f"""

        <div style="max-width:800px;margin:0 auto">

          <a href="/student/leaderboard" style="color:var(--text-muted);font-size:13px;text-decoration:none">&larr; Back to Leaderboard</a>

          <div style="display:flex;justify-content:space-between;align-items:center;margin:12px 0 20px;flex-wrap:wrap;gap:12px">

            <div>

              <h2 style="margin:0">{_esc(group['name'])}</h2>

              <p style="color:var(--text-muted);font-size:13px;margin:4px 0 0">

                Invite code: <code style="background:var(--bg);padding:2px 8px;border-radius:4px">{_esc(group['invite_code'])}</code>

                <button class="btn btn-ghost btn-sm" onclick="navigator.clipboard.writeText('{_esc(group['invite_code'])}').then(function(){{alert('Copied!')}})" style="font-size:11px;padding:2px 8px">Copy</button>

              </p>

            </div>

          </div>

          <div style="background:linear-gradient(135deg,rgba(139,92,246,.12),rgba(34,197,94,.08));border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;margin-bottom:16px;font-size:13px;color:var(--text-muted)">

            &#128161; <b style="color:var(--text)">Fair-play group.</b> Everyone starts at 0 XP the moment they join. This scoreboard only counts XP earned <i>inside</i> the group &mdash; separate from the global ranking. {('Created ' + created_str) if created_str else ''}

          </div>

          <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">

            <table style="width:100%;border-collapse:collapse">

              <thead><tr style="border-bottom:2px solid var(--border)">

                <th style="padding:12px 16px;text-align:center;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Place</th>

                <th style="padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Student</th>

                <th style="padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Progress</th>

                <th style="padding:12px 16px;text-align:right;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">XP&nbsp;Gained</th>

              </tr></thead>

              <tbody>{rows_html}</tbody>

            </table>

          </div>

        </div>

        """, active_page="student_leaderboard")



    # ── Personal Leaderboard API ────────────────────────────



    @app.route("/api/student/leaderboard/groups", methods=["POST"])

    def student_create_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        name = (data.get("name") or "").strip()

        if not name or len(name) > 60:

            return jsonify({"error": "Group name required (max 60 chars)"}), 400

        # Limit to 10 groups per user

        existing = sdb.get_my_lb_groups(_cid())

        if len(existing) >= 10:

            return jsonify({"error": "Maximum 10 groups reached"}), 400

        result = sdb.create_lb_group(_cid(), name)

        return jsonify({"ok": True, "group_id": result["id"], "invite_code": result["invite_code"]})



    @app.route("/api/student/leaderboard/join", methods=["POST"])

    def student_join_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        code = (data.get("invite_code") or "").strip()

        if not code:

            return jsonify({"error": "Invite code required"}), 400

        group = sdb.join_lb_group(_cid(), code)

        if not group:

            return jsonify({"error": "Invalid invite code"}), 404

        return jsonify({"ok": True, "group_id": group["id"]})



    @app.route("/api/student/leaderboard/leave", methods=["POST"])

    def student_leave_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        group_id = data.get("group_id")

        if not group_id:

            return jsonify({"error": "group_id required"}), 400

        # Can't leave if owner

        group = sdb.get_lb_group(group_id)

        if group and group["owner_id"] == _cid():

            return jsonify({"error": "Owners can't leave. Delete the group instead."}), 400

        sdb.leave_lb_group(_cid(), group_id)

        return jsonify({"ok": True})



    @app.route("/api/student/leaderboard/groups/<int:group_id>", methods=["DELETE"])

    def student_delete_lb_group(group_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_lb_group(group_id, _cid())

        return jsonify({"ok": True})



    @app.route("/student/settings", methods=["GET", "POST"])

    def student_settings_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()



        # Handle profile update

        from outreach.db import get_client, update_client, get_email_accounts, get_subscription, get_mail_preferences

        from outreach.config import PLAN_LIMITS

        client = get_client(cid)



        if request.method == "POST":

            name = request.form.get("name", "").strip()

            business = request.form.get("business", "").strip()

            physical_address = request.form.get("physical_address", "").strip()

            if name:

                update_client(cid, name, business, physical_address)

                session["client_name"] = name

            return redirect(url_for("student_settings_page"))



        prefs = sdb.get_email_prefs(cid) or {}

        de = prefs.get("daily_email", 1)

        hour = prefs.get("email_hour", 7)

        tz = prefs.get("timezone", "America/Mexico_City")

        university = prefs.get("university", "") or ""

        field_of_study = prefs.get("field_of_study", "") or ""

        canvas_tok = sdb.get_canvas_token(cid)

        canvas_status = "Connected" if canvas_tok else "Not connected"

        canvas_color = "#10B981" if canvas_tok else "#EF4444"

        mail_rules = get_mail_preferences(cid) or ""



        # Email accounts

        accounts = get_email_accounts(cid)

        sub = get_subscription(cid)

        plan = sub.get("plan", "free") if sub else "free"

        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

        max_mailboxes = limits.get("mailboxes", 1)

        can_add = max_mailboxes == -1 or len(accounts) < max_mailboxes

        limit_text = "Unlimited" if max_mailboxes == -1 else str(max_mailboxes)



        accounts_html = ""

        for a in accounts:

            default_badge = '<span style="background:var(--blue);color:#fff;padding:2px 8px;border-radius:8px;font-size:10px;margin-left:6px;">Default</span>' if a["is_default"] else ""

            accounts_html += f"""

            <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid var(--border);">

              <div style="display:flex;align-items:center;gap:12px;">

                <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;">

                  {_esc(a['email'][:1].upper())}

                </div>

                <div>

                  <div style="font-weight:600;color:var(--text)">{_esc(a['label'] or a['email'])}{default_badge}</div>

                  <div style="font-size:13px;color:var(--text-muted)">{_esc(a['email'])}</div>

                </div>

              </div>

            </div>"""



        if not accounts_html:

            accounts_html = '<p style="color:var(--text-muted);padding:16px 0;text-align:center;font-size:13px;">No email accounts connected yet.</p>'



        # ── Translations ──

        _lang = session.get("lang", "en")

        _ES = {

            "Settings": "Configuración",

            "Profile": "Perfil",

            "Name": "Nombre",

            "Email": "Correo electrónico",

            "Email cannot be changed.": "El correo no se puede cambiar.",

            "Save Changes": "Guardar cambios",

            "University & Studies": "Universidad y Estudios",

            "Set your university and field of study to appear on the leaderboard and connect with classmates.": "Define tu universidad y carrera para aparecer en la clasificación y conectarte con compañeros.",

            "University": "Universidad",

            "Field of Study": "Carrera",

            "View Leaderboard": "Ver clasificación",

            "Canvas LMS": "Canvas LMS",

            "Connected": "Conectado",

            "Not connected": "No conectado",

            "Connect your Canvas LMS to sync courses, exams, and study materials.": "Conecta tu Canvas LMS para sincronizar cursos, exámenes y material de estudio.",

            "Manage Connection": "Administrar conexión",

            "Connect Canvas": "Conectar Canvas",

            "Email Accounts": "Cuentas de correo",

            "mailboxes": "buzones",

            "Manage in Mail Hub": "Administrar en Centro de Correo",

            "+ Add Email Account": "+ Agregar cuenta de correo",

            "No email accounts connected yet.": "Aún no hay cuentas de correo conectadas.",

            "Mail Sorting Rules": "Reglas de ordenado de correo",

            "Tell the AI how to sort your inbox. Write both <strong>prioritize</strong> and <strong>deprioritize</strong> rules in plain English.": "Indícale a la IA cómo ordenar tu bandeja. Escribe reglas para <strong>priorizar</strong> y <strong>despriorizar</strong> en lenguaje natural.",

            "Examples:": "Ejemplos:",

            "Emails from my professors are always urgent": "Los correos de mis profesores son siempre urgentes",

            "Meeting invites from @university.edu are important": "Las invitaciones a reuniones de @universidad.edu son importantes",

            "Do NOT mark no-reply@render.com as urgent": "NO marques no-reply@render.com como urgente",

            "Newsletters and marketing emails are always low priority": "Los boletines y correos de marketing son de baja prioridad",

            "Ignore all emails from noreply@github.com": "Ignora todos los correos de noreply@github.com",

            "Write your mail sorting rules here...": "Escribe aquí tus reglas de ordenado de correo...",

            "Save Rules": "Guardar reglas",

            "Theme": "Tema",

            "Personalize how the app looks. Your choice is saved on this device.": "Personaliza cómo se ve la app. Tu elección se guarda en este dispositivo.",

            "Click any theme to switch instantly. Your choice is saved automatically.": "Haz clic en cualquier tema para cambiar al instante. Se guarda automáticamente.",

            "Daily Study Email": "Correo de estudio diario",

            "Get a morning email with your study plan, upcoming exams, and weak topics to review.": "Recibe un correo matutino con tu plan de estudio, exámenes próximos y temas débiles para repasar.",

            "Enable daily study email": "Habilitar correo diario de estudio",

            "Send at (hour)": "Enviar a (hora)",

            "Timezone": "Zona horaria",

            "Save Preferences": "Guardar preferencias",

            "Interactive Tutorial": "Tutorial interactivo",

            "Replay the guided walkthrough to rediscover all the features available to you.": "Vuelve a ver el recorrido guiado para redescubrir todas las funciones disponibles.",

            "Restart Tutorial": "Reiniciar tutorial",

            "Account Security": "Seguridad de la cuenta",

            "Your account is secure": "Tu cuenta está segura",

            "Password protected with bcrypt encryption. You can change your password below if needed.": "Protegida con cifrado bcrypt. Puedes cambiar tu contraseña abajo si lo necesitas.",

            "Change password": "Cambiar contraseña",

            "(optional)": "(opcional)",

            "Current Password": "Contraseña actual",

            "New Password": "Nueva contraseña",

            "Confirm Password": "Confirmar contraseña",

            "Minimum 6 characters.": "Mínimo 6 caracteres.",

            "Update Password": "Actualizar contraseña",

            "Danger Zone": "Zona de peligro",

            "Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action <strong>cannot be undone</strong>.": "Elimina permanentemente tu cuenta y todos los datos asociados (cursos, exámenes, notas, flashcards, quizzes, historial de chat, XP, insignias). Esta acción <strong>no se puede deshacer</strong>.",

            "Delete My Account": "Eliminar mi cuenta",

            "Type": "Escribe",

            "to confirm:": "para confirmar:",

            "Permanently Delete Account": "Eliminar cuenta permanentemente",

            "Saved!": "¡Guardado!",

            "Saving...": "Guardando...",

            "Please enter at least one rule": "Ingresa al menos una regla",

            "Failed": "Falló",

            "Connection error": "Error de conexión",

            "Error saving.": "Error al guardar.",

        }

        def _T(s):

            return _ES.get(s, s) if _lang == "es" else s



        if not accounts:

            # localize empty-state text after translation dict is defined

            accounts_html = f'<p style="color:var(--text-muted);padding:16px 0;text-align:center;font-size:13px;">{_T("No email accounts connected yet.")}</p>'



        return _s_render("Settings", f"""

        <div>

          <h2 style="margin-bottom:24px">⚙️ {_T("Settings")}</h2>



          <!-- Profile -->

          <div class="card">

            <div class="card-header"><h2>👤 {_T("Profile")}</h2></div>

            <form method="post">

              <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">

                <div>

                  <label>{_T("Name")}</label>

                  <input name="name" value="{_esc(client.get('name','') if client else '')}" required class="edit-input">

                </div>

                <div>

                  <label>{_T("Email")}</label>

                  <input value="{_esc(client.get('email','') if client else '')}" disabled class="edit-input" style="background:var(--border-light);color:var(--text-muted);cursor:not-allowed">

                  <p style="font-size:11px;color:var(--text-muted);margin-top:6px">{_T("Email cannot be changed.")}</p>

                </div>

              </div>

              <button class="btn btn-primary" type="submit" style="margin-top:14px">{_T("Save Changes")}</button>

            </form>

          </div>



          <!-- University & Studies -->

          <div class="card">

            <div class="card-header"><h2>🏫 {_T("University & Studies")}</h2></div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">

              {_T("Set your university and field of study to appear on the leaderboard and connect with classmates.")}

            </p>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:14px">

              <div>

                <label style="font-size:12px">{_T("University")}</label>

                <input id="pref-university" value="{_esc(university)}" placeholder="e.g. MIT, Stanford, UNAM..." class="edit-input">

              </div>

              <div>

                <label style="font-size:12px">{_T("Field of Study")}</label>

                <input id="pref-field" value="{_esc(field_of_study)}" placeholder="e.g. Computer Science, Medicine..." class="edit-input">

              </div>

            </div>

            <a href="/student/leaderboard" class="btn btn-outline btn-sm">🏆 {_T("View Leaderboard")}</a>

          </div>



          <!-- Canvas Connection -->

          <div class="card">

            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">

              <h2>🔗 {_T("Canvas LMS")}</h2>

              <span style="color:{canvas_color};font-weight:600;font-size:13px">● {_T(canvas_status)}</span>

            </div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:12px">

              {_T("Connect your Canvas LMS to sync courses, exams, and study materials.")}

            </p>

            <a href="/student/canvas-settings" class="btn btn-outline btn-sm">{_T("Manage Connection") if canvas_tok else _T("Connect Canvas")}</a>

          </div>



          <!-- Google Calendar Connection -->

          <div class="card">

            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">

              <h2>📅 {_T("Google Calendar")}</h2>

              <span id="gcal-status-badge" style="color:var(--text-muted);font-weight:600;font-size:13px">●</span>

            </div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:12px">

              {_T("Sync your Google Calendar so the AI schedules study blocks around your real classes, work, and exams &mdash; and can add new events directly to your calendar.")}

            </p>

            <div id="gcal-actions" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">

              <a href="/gcal/connect?return=/student/settings" class="btn btn-outline btn-sm" id="gcal-connect-btn">{_T("Connect Google Calendar")}</a>

            </div>

            <script>

              (function(){{

                fetch('/api/gcal/status').then(r=>r.json()).then(d=>{{

                  var b = document.getElementById('gcal-status-badge');

                  var a = document.getElementById('gcal-actions');

                  if (!d || !d.configured) {{

                    b.style.color = 'var(--text-muted)';

                    b.textContent = '● Not configured';

                    a.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">Server admin must set GOOGLE_OAUTH_* env vars.</span>';

                    return;

                  }}

                  if (d.connected) {{

                    b.style.color = '#10B981';

                    b.textContent = '● Connected';

                    a.innerHTML = '<span style="font-size:13px;color:var(--text);font-weight:600;margin-right:8px;">' + (d.email || '') + '</span>'

                      + '<button onclick="gcalDisconnect()" class="btn btn-outline btn-sm">Disconnect</button>';

                  }} else {{

                    b.style.color = '#9CA3AF';

                    b.textContent = '● Not connected';

                  }}

                }}).catch(()=>{{}});

                window.gcalDisconnect = async function(){{

                  if (!confirm('Disconnect Google Calendar?')) return;

                  var m = document.querySelector('meta[name="csrf-token"]');

                  var h = m ? {{'X-CSRFToken': m.content}} : {{}};

                  await fetch('/gcal/disconnect', {{method:'POST', headers: h}});

                  location.reload();

                }};

              }})();

            </script>

          </div>



          <!-- Email Accounts -->

          <div class="card">

            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">

              <h2>📧 {_T("Email Accounts")}</h2>

              <span style="font-size:13px;color:var(--text-muted)">{len(accounts)}/{limit_text} {_T("mailboxes")}</span>

            </div>

            <div>{accounts_html}</div>

            {f"<div style='margin-top:12px'><a href='/mail-hub' class='btn btn-outline btn-sm'>{_T('Manage in Mail Hub')}</a></div>" if accounts else f"<div style='margin-top:12px'><a href='/settings' class='btn btn-primary btn-sm'>{_T('+ Add Email Account')}</a></div>"}

          </div>



          <!-- Mail Sorting Rules -->

          <div class="card">

            <div class="card-header"><h2>&#128340; {_T("Mail Sorting Rules")}</h2></div>

            <p style="color:var(--text-muted);font-size:13px;margin-bottom:6px;">{_T("Tell the AI how to sort your inbox. Write both <strong>prioritize</strong> and <strong>deprioritize</strong> rules in plain English.")}</p>

            <div style="font-size:12px;color:var(--text-muted);margin-bottom:16px;line-height:1.7;background:var(--bg);padding:12px 14px;border-radius:var(--radius-xs);">

              <strong>{_T("Examples:")}</strong><br>

              &#128314; {_T("Emails from my professors are always urgent")}<br>

              &#128314; {_T("Meeting invites from @university.edu are important")}<br>

              &#128315; {_T("Do NOT mark no-reply@render.com as urgent")}<br>

              &#128315; {_T("Newsletters and marketing emails are always low priority")}<br>

              &#128315; {_T("Ignore all emails from noreply@github.com")}

            </div>

            <div class="form-group">

              <textarea id="settings-mail-rules" placeholder="{_T('Write your mail sorting rules here...')}" style="height:120px;font-size:13px;width:100%;padding:10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);resize:vertical;">{_esc(mail_rules)}</textarea>

            </div>

            <button class="btn btn-primary btn-sm" onclick="saveMailRules()" id="save-rules-btn">{_T("Save Rules")}</button>

            <span id="rules-save-status" style="margin-left:10px;font-size:13px;"></span>

          </div>



          <!-- Theme Selector -->

          <div class="card">

            <div class="card-header"><h2>🎨 {_T("Theme")}</h2></div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">

              {_T("Personalize how the app looks. Your choice is saved on this device.")}

            </p>

            <div id="theme-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">

              <button type="button" class="theme-chip" data-theme="default" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0f172a;color:#fff;text-align:left"><div style="font-weight:700">Default</div><div style="font-size:11px;opacity:.7">Indigo / Slate</div></button>

              <button type="button" class="theme-chip" data-theme="midnight" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#050816;color:#e2e8f0;text-align:left"><div style="font-weight:700">Midnight</div><div style="font-size:11px;opacity:.7">Deep black</div></button>

              <button type="button" class="theme-chip" data-theme="forest" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0b2018;color:#d1fae5;text-align:left"><div style="font-weight:700">Forest</div><div style="font-size:11px;opacity:.7">Calm green</div></button>

              <button type="button" class="theme-chip" data-theme="ocean" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#082f49;color:#e0f2fe;text-align:left"><div style="font-weight:700">Ocean</div><div style="font-size:11px;opacity:.7">Deep blue</div></button>

              <button type="button" class="theme-chip" data-theme="rose" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#3f0a1a;color:#fecdd3;text-align:left"><div style="font-weight:700">Rose</div><div style="font-size:11px;opacity:.7">Warm crimson</div></button>

              <button type="button" class="theme-chip" data-theme="sunset" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:linear-gradient(135deg,#7c2d12,#ea580c);color:#fff;text-align:left"><div style="font-weight:700">Sunset</div><div style="font-size:11px;opacity:.7">Orange / amber</div></button>

              <button type="button" class="theme-chip" data-theme="mono" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#111;color:#fff;text-align:left"><div style="font-weight:700">Mono</div><div style="font-size:11px;opacity:.7">Pure black &amp; white</div></button>

              <button type="button" class="theme-chip" data-theme="light" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f8fafc;color:#111827;text-align:left"><div style="font-weight:700">Light</div><div style="font-size:11px;opacity:.7">Clean &amp; bright</div></button>

              <button type="button" class="theme-chip" data-theme="lavender" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f5f3ff;color:#3b0764;text-align:left"><div style="font-weight:700">Lavender</div><div style="font-size:11px;opacity:.7">Soft purple</div></button>

              <button type="button" class="theme-chip" data-theme="mint" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0fdf4;color:#14532d;text-align:left"><div style="font-weight:700">Mint</div><div style="font-size:11px;opacity:.7">Fresh pastel green</div></button>

              <button type="button" class="theme-chip" data-theme="peach" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff7ed;color:#7c2d12;text-align:left"><div style="font-weight:700">Peach</div><div style="font-size:11px;opacity:.7">Warm pastel orange</div></button>

              <button type="button" class="theme-chip" data-theme="sky" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0f9ff;color:#0c4a6e;text-align:left"><div style="font-weight:700">Sky</div><div style="font-size:11px;opacity:.7">Pastel blue</div></button>

              <button type="button" class="theme-chip" data-theme="butter" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fefce8;color:#713f12;text-align:left"><div style="font-weight:700">Butter</div><div style="font-size:11px;opacity:.7">Soft pastel yellow</div></button>

              <button type="button" class="theme-chip" data-theme="lilac" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf4ff;color:#581c87;text-align:left"><div style="font-weight:700">Lilac</div><div style="font-size:11px;opacity:.7">Pastel violet</div></button>

              <button type="button" class="theme-chip" data-theme="blush" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff1f2;color:#881337;text-align:left"><div style="font-weight:700">Blush</div><div style="font-size:11px;opacity:.7">Soft pastel pink</div></button>

              <button type="button" class="theme-chip" data-theme="sand" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#faf5ee;color:#44342a;text-align:left"><div style="font-weight:700">Sand</div><div style="font-size:11px;opacity:.7">Warm beige</div></button>

              <button type="button" class="theme-chip" data-theme="cottoncandy" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf2f8;color:#831843;text-align:left"><div style="font-weight:700">Cotton Candy</div><div style="font-size:11px;opacity:.7">Bubblegum pink</div></button>

              <button type="button" class="theme-chip" data-theme="seafoam" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#ecfeff;color:#164e63;text-align:left"><div style="font-weight:700">Seafoam</div><div style="font-size:11px;opacity:.7">Pastel cyan</div></button>

            </div>

            <script>

              (function() {{

                var current = localStorage.getItem('mr_theme') || 'default';

                function mark() {{

                  document.querySelectorAll('.theme-chip').forEach(function(b) {{

                    b.style.outline = (b.dataset.theme === current) ? '3px solid #6366f1' : 'none';

                    b.style.outlineOffset = (b.dataset.theme === current) ? '2px' : '0';

                  }});

                }}

                document.querySelectorAll('.theme-chip').forEach(function(b) {{

                  b.addEventListener('click', function() {{

                    current = b.dataset.theme;

                    localStorage.setItem('mr_theme', current);

                    if (window.applyMrTheme) window.applyMrTheme(current);

                    mark();

                    var s = document.getElementById('theme-status');

                    if (s) {{

                      s.textContent = '{_T("Saved!")} ';

                      setTimeout(function(){{ if(s) s.textContent=''; }}, 2200);

                    }}

                  }});

                }});

                mark();

              }})();

            </script>

            <span id="theme-status" style="color:var(--text-muted);font-size:13px;display:inline-block;margin-top:10px"></span>

            <p style="color:var(--text-muted);font-size:12px;margin-top:6px">&#128161; {_T("Click any theme to switch instantly. Your choice is saved automatically.")}</p>

          </div>



          <!-- Daily Study Email -->

          <div class="card">

            <div class="card-header"><h2>📬 {_T("Daily Study Email")}</h2></div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">

              {_T("Get a morning email with your study plan, upcoming exams, and weak topics to review.")}

            </p>

            <label style="display:flex;align-items:center;gap:8px;margin-bottom:14px;cursor:pointer;color:var(--text)">

              <input type="checkbox" id="pref-daily" {'checked' if de else ''} style="width:18px;height:18px;accent-color:var(--primary)">

              <span>{_T("Enable daily study email")}</span>

            </label>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">

              <div>

                <label style="font-size:12px">{_T("Send at (hour)")}</label>

                <select id="pref-hour" class="edit-input" style="width:100%">

                  {"".join(f'<option value="{h}" {"selected" if h==hour else ""}>{h:02d}:00</option>' for h in range(5,23))}

                </select>

              </div>

              <div>

                <label style="font-size:12px">{_T("Timezone")}</label>

                <select id="pref-tz" class="edit-input" style="width:100%">

                  <optgroup label="Americas">

                    <option value="America/Mexico_City" {"selected" if tz=="America/Mexico_City" else ""}>Mexico City (CST)</option>

                    <option value="America/Cancun" {"selected" if tz=="America/Cancun" else ""}>Cancún (EST)</option>

                    <option value="America/Tijuana" {"selected" if tz=="America/Tijuana" else ""}>Tijuana (PST)</option>

                    <option value="America/Monterrey" {"selected" if tz=="America/Monterrey" else ""}>Monterrey (CST)</option>

                    <option value="America/New_York" {"selected" if tz=="America/New_York" else ""}>New York (EST)</option>

                    <option value="America/Chicago" {"selected" if tz=="America/Chicago" else ""}>Chicago (CST)</option>

                    <option value="America/Denver" {"selected" if tz=="America/Denver" else ""}>Denver (MST)</option>

                    <option value="America/Los_Angeles" {"selected" if tz=="America/Los_Angeles" else ""}>Los Angeles (PST)</option>

                    <option value="America/Anchorage" {"selected" if tz=="America/Anchorage" else ""}>Anchorage (AKST)</option>

                    <option value="Pacific/Honolulu" {"selected" if tz=="Pacific/Honolulu" else ""}>Honolulu (HST)</option>

                    <option value="America/Toronto" {"selected" if tz=="America/Toronto" else ""}>Toronto (EST)</option>

                    <option value="America/Vancouver" {"selected" if tz=="America/Vancouver" else ""}>Vancouver (PST)</option>

                    <option value="America/Bogota" {"selected" if tz=="America/Bogota" else ""}>Bogotá (COT)</option>

                    <option value="America/Lima" {"selected" if tz=="America/Lima" else ""}>Lima (PET)</option>

                    <option value="America/Santiago" {"selected" if tz=="America/Santiago" else ""}>Santiago (CLT)</option>

                    <option value="America/Buenos_Aires" {"selected" if tz=="America/Buenos_Aires" else ""}>Buenos Aires (ART)</option>

                    <option value="America/Sao_Paulo" {"selected" if tz=="America/Sao_Paulo" else ""}>São Paulo (BRT)</option>

                    <option value="America/Caracas" {"selected" if tz=="America/Caracas" else ""}>Caracas (VET)</option>

                    <option value="America/Costa_Rica" {"selected" if tz=="America/Costa_Rica" else ""}>Costa Rica (CST)</option>

                    <option value="America/Panama" {"selected" if tz=="America/Panama" else ""}>Panama (EST)</option>

                    <option value="America/Guayaquil" {"selected" if tz=="America/Guayaquil" else ""}>Guayaquil (ECT)</option>

                    <option value="America/Montevideo" {"selected" if tz=="America/Montevideo" else ""}>Montevideo (UYT)</option>

                    <option value="America/Asuncion" {"selected" if tz=="America/Asuncion" else ""}>Asunción (PYT)</option>

                    <option value="America/La_Paz" {"selected" if tz=="America/La_Paz" else ""}>La Paz (BOT)</option>

                    <option value="America/Santo_Domingo" {"selected" if tz=="America/Santo_Domingo" else ""}>Santo Domingo (AST)</option>

                    <option value="America/Havana" {"selected" if tz=="America/Havana" else ""}>Havana (CST)</option>

                    <option value="America/Guatemala" {"selected" if tz=="America/Guatemala" else ""}>Guatemala (CST)</option>

                    <option value="America/El_Salvador" {"selected" if tz=="America/El_Salvador" else ""}>El Salvador (CST)</option>

                    <option value="America/Tegucigalpa" {"selected" if tz=="America/Tegucigalpa" else ""}>Tegucigalpa (CST)</option>

                    <option value="America/Managua" {"selected" if tz=="America/Managua" else ""}>Managua (CST)</option>

                  </optgroup>

                  <optgroup label="Europe">

                    <option value="Europe/London" {"selected" if tz=="Europe/London" else ""}>London (GMT)</option>

                    <option value="Europe/Madrid" {"selected" if tz=="Europe/Madrid" else ""}>Madrid (CET)</option>

                    <option value="Europe/Paris" {"selected" if tz=="Europe/Paris" else ""}>Paris (CET)</option>

                    <option value="Europe/Berlin" {"selected" if tz=="Europe/Berlin" else ""}>Berlin (CET)</option>

                    <option value="Europe/Rome" {"selected" if tz=="Europe/Rome" else ""}>Rome (CET)</option>

                    <option value="Europe/Amsterdam" {"selected" if tz=="Europe/Amsterdam" else ""}>Amsterdam (CET)</option>

                    <option value="Europe/Lisbon" {"selected" if tz=="Europe/Lisbon" else ""}>Lisbon (WET)</option>

                    <option value="Europe/Brussels" {"selected" if tz=="Europe/Brussels" else ""}>Brussels (CET)</option>

                    <option value="Europe/Zurich" {"selected" if tz=="Europe/Zurich" else ""}>Zurich (CET)</option>

                    <option value="Europe/Vienna" {"selected" if tz=="Europe/Vienna" else ""}>Vienna (CET)</option>

                    <option value="Europe/Warsaw" {"selected" if tz=="Europe/Warsaw" else ""}>Warsaw (CET)</option>

                    <option value="Europe/Stockholm" {"selected" if tz=="Europe/Stockholm" else ""}>Stockholm (CET)</option>

                    <option value="Europe/Oslo" {"selected" if tz=="Europe/Oslo" else ""}>Oslo (CET)</option>

                    <option value="Europe/Helsinki" {"selected" if tz=="Europe/Helsinki" else ""}>Helsinki (EET)</option>

                    <option value="Europe/Athens" {"selected" if tz=="Europe/Athens" else ""}>Athens (EET)</option>

                    <option value="Europe/Bucharest" {"selected" if tz=="Europe/Bucharest" else ""}>Bucharest (EET)</option>

                    <option value="Europe/Moscow" {"selected" if tz=="Europe/Moscow" else ""}>Moscow (MSK)</option>

                    <option value="Europe/Istanbul" {"selected" if tz=="Europe/Istanbul" else ""}>Istanbul (TRT)</option>

                    <option value="Europe/Dublin" {"selected" if tz=="Europe/Dublin" else ""}>Dublin (GMT)</option>

                    <option value="Europe/Prague" {"selected" if tz=="Europe/Prague" else ""}>Prague (CET)</option>

                    <option value="Europe/Budapest" {"selected" if tz=="Europe/Budapest" else ""}>Budapest (CET)</option>

                    <option value="Europe/Copenhagen" {"selected" if tz=="Europe/Copenhagen" else ""}>Copenhagen (CET)</option>

                  </optgroup>

                  <optgroup label="Asia & Pacific">

                    <option value="Asia/Dubai" {"selected" if tz=="Asia/Dubai" else ""}>Dubai (GST)</option>

                    <option value="Asia/Kolkata" {"selected" if tz=="Asia/Kolkata" else ""}>India (IST)</option>

                    <option value="Asia/Shanghai" {"selected" if tz=="Asia/Shanghai" else ""}>Shanghai (CST)</option>

                    <option value="Asia/Tokyo" {"selected" if tz=="Asia/Tokyo" else ""}>Tokyo (JST)</option>

                    <option value="Asia/Seoul" {"selected" if tz=="Asia/Seoul" else ""}>Seoul (KST)</option>

                    <option value="Asia/Singapore" {"selected" if tz=="Asia/Singapore" else ""}>Singapore (SGT)</option>

                    <option value="Asia/Hong_Kong" {"selected" if tz=="Asia/Hong_Kong" else ""}>Hong Kong (HKT)</option>

                    <option value="Asia/Bangkok" {"selected" if tz=="Asia/Bangkok" else ""}>Bangkok (ICT)</option>

                    <option value="Asia/Jakarta" {"selected" if tz=="Asia/Jakarta" else ""}>Jakarta (WIB)</option>

                    <option value="Asia/Kuala_Lumpur" {"selected" if tz=="Asia/Kuala_Lumpur" else ""}>Kuala Lumpur (MYT)</option>

                    <option value="Asia/Manila" {"selected" if tz=="Asia/Manila" else ""}>Manila (PHT)</option>

                    <option value="Asia/Taipei" {"selected" if tz=="Asia/Taipei" else ""}>Taipei (CST)</option>

                    <option value="Asia/Karachi" {"selected" if tz=="Asia/Karachi" else ""}>Karachi (PKT)</option>

                    <option value="Asia/Dhaka" {"selected" if tz=="Asia/Dhaka" else ""}>Dhaka (BST)</option>

                    <option value="Asia/Riyadh" {"selected" if tz=="Asia/Riyadh" else ""}>Riyadh (AST)</option>

                    <option value="Asia/Tehran" {"selected" if tz=="Asia/Tehran" else ""}>Tehran (IRST)</option>

                    <option value="Australia/Sydney" {"selected" if tz=="Australia/Sydney" else ""}>Sydney (AEST)</option>

                    <option value="Australia/Melbourne" {"selected" if tz=="Australia/Melbourne" else ""}>Melbourne (AEST)</option>

                    <option value="Australia/Perth" {"selected" if tz=="Australia/Perth" else ""}>Perth (AWST)</option>

                    <option value="Pacific/Auckland" {"selected" if tz=="Pacific/Auckland" else ""}>Auckland (NZST)</option>

                  </optgroup>

                  <optgroup label="Africa">

                    <option value="Africa/Cairo" {"selected" if tz=="Africa/Cairo" else ""}>Cairo (EET)</option>

                    <option value="Africa/Lagos" {"selected" if tz=="Africa/Lagos" else ""}>Lagos (WAT)</option>

                    <option value="Africa/Johannesburg" {"selected" if tz=="Africa/Johannesburg" else ""}>Johannesburg (SAST)</option>

                    <option value="Africa/Nairobi" {"selected" if tz=="Africa/Nairobi" else ""}>Nairobi (EAT)</option>

                    <option value="Africa/Casablanca" {"selected" if tz=="Africa/Casablanca" else ""}>Casablanca (WET)</option>

                  </optgroup>

                </select>

              </div>

            </div>

            <button onclick="savePrefs()" class="btn btn-primary btn-sm">{_T("Save Preferences")}</button>

          </div>

        </div>



        <!-- Restart Tutorial -->

        <div class="card" style="margin-top:16px;">

          <div class="card-header"><h2>&#127891; {_T("Interactive Tutorial")}</h2></div>

          <div style="padding:20px;">

            <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">{_T("Replay the guided walkthrough to rediscover all the features available to you.")}</p>

            <button onclick="localStorage.removeItem('mr-tutorial-done');window.location='/student'" class="btn btn-outline btn-sm">&#128260; {_T("Restart Tutorial")}</button>

          </div>

        </div>



        <!-- Account Security (mirrors business settings — optional change) -->

        <div class="card" style="margin-top:16px;">

          <div class="card-header"><h2>&#128272; {_T("Account Security")}</h2></div>

          <div style="padding:20px;">

            <div style="display:flex;align-items:center;gap:12px;padding:14px 18px;background:var(--green-light,rgba(16,185,129,.12));border-radius:var(--radius-sm);margin-bottom:16px;">

              <span style="font-size:22px;">&#9989;</span>

              <div>

                <div style="font-weight:600;font-size:14px;color:var(--green-dark,#059669);">{_T("Your account is secure")}</div>

                <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{_T("Password protected with bcrypt encryption. You can change your password below if needed.")}</div>

              </div>

            </div>

            <details style="cursor:pointer;">

              <summary style="font-size:14px;font-weight:600;color:var(--text);padding:10px 0;list-style:none;display:flex;align-items:center;gap:8px;">

                <span style="transition:transform 0.2s;display:inline-block;" class="pw-arrow">&#9654;</span>

                {_T("Change password")} <span style="font-size:12px;font-weight:400;color:var(--text-muted);">{_T("(optional)")}</span>

              </summary>

              <div style="padding:16px 0 4px;">

                <form method="post" action="/settings/change-password">

                  <div class="form-group" style="margin-bottom:12px;">

                    <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("Current Password")}</label>

                    <input name="current_password" type="password" required class="edit-input" autocomplete="current-password">

                  </div>

                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">

                    <div class="form-group">

                      <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("New Password")}</label>

                      <input name="new_password" type="password" required minlength="6" class="edit-input" autocomplete="new-password">

                    </div>

                    <div class="form-group">

                      <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("Confirm Password")}</label>

                      <input name="confirm_password" type="password" required minlength="6" class="edit-input" autocomplete="new-password">

                    </div>

                  </div>

                  <p style="font-size:11px;color:var(--text-muted);margin:0 0 12px;">{_T("Minimum 6 characters.")}</p>

                  <button class="btn btn-outline btn-sm" type="submit">{_T("Update Password")}</button>

                </form>

              </div>

            </details>

          </div>

        </div>

        <style>

          details[open] .pw-arrow {{ transform: rotate(90deg); }}

        </style>



        <!-- Delete Account -->

        <div class="card" style="margin-top:16px;border-color:var(--red);">

          <div class="card-header"><h2 style="color:var(--red);">&#9888;&#65039; {_T("Danger Zone")}</h2></div>

          <div style="padding:20px;">

            <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">{_T("Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action <strong>cannot be undone</strong>.")}</p>

            <button class="btn btn-ghost btn-sm" style="color:var(--red);border:1px solid var(--red);" onclick="document.getElementById('delete-confirm-box').style.display='block';this.style.display='none';">{_T("Delete My Account")}</button>

            <div id="delete-confirm-box" style="display:none;margin-top:14px;padding:16px;border:1px solid var(--red);border-radius:var(--radius-sm);background:var(--red-light);">

              <form method="post" action="/settings/delete-account">

                <p style="font-size:13px;color:var(--text);margin:0 0 10px;font-weight:600;">{_T("Type")} <code style="background:var(--border);padding:2px 6px;border-radius:4px;">DELETE</code> {_T("to confirm:")}</p>

                <input name="confirm" placeholder="DELETE" required autocomplete="off" class="edit-input" style="border-color:var(--red);max-width:200px;margin-bottom:10px;">

                <br>

                <button class="btn btn-primary btn-sm" type="submit" style="background:var(--red);border-color:var(--red);">{_T("Permanently Delete Account")}</button>

              </form>

            </div>

          </div>

        </div>



        <script>

        async function savePrefs() {{

          var r = await fetch('/api/student/email-prefs', {{

            method:'POST', headers:{{'Content-Type':'application/json'}},

            body: JSON.stringify({{

              daily_email: document.getElementById('pref-daily').checked,

              email_hour: parseInt(document.getElementById('pref-hour').value),

              timezone: document.getElementById('pref-tz').value,

              university: document.getElementById('pref-university').value.trim(),

              field_of_study: document.getElementById('pref-field').value.trim()

            }})

          }});

          if (r.ok) {{

            alert('{_T("Saved!")}');

          }} else {{

            var msg = '{_T("Error saving.")}';

            try {{ var j = await r.json(); if (j && j.error) msg = j.error; }} catch(e) {{}}

            alert(msg);

          }}

        }}

        function saveMailRules() {{

          var text = document.getElementById('settings-mail-rules').value.trim();

          var btn = document.getElementById('save-rules-btn');

          var status = document.getElementById('rules-save-status');

          if (!text) {{ status.innerHTML = '<span style="color:var(--red);">{_T("Please enter at least one rule")}</span>'; return; }}

          btn.disabled = true; btn.textContent = '{_T("Saving...")}';

          fetch('/api/mail-preferences', {{

            method: 'POST', headers: {{'Content-Type': 'application/json'}},

            body: JSON.stringify({{preferences: text}})

          }}).then(function(r) {{ return r.json(); }}).then(function(data) {{

            btn.disabled = false; btn.textContent = '{_T("Save Rules")}';

            if (data.ok) {{

              status.innerHTML = '<span style="color:var(--green);">&#10003; {_T("Saved!")}</span>';

              setTimeout(function() {{ status.innerHTML = ''; }}, 4000);

            }} else {{ status.innerHTML = '<span style="color:var(--red);">' + (data.error || '{_T("Failed")}') + '</span>'; }}

          }}).catch(function() {{

            btn.disabled = false; btn.textContent = '{_T("Save Rules")}';

            status.innerHTML = '<span style="color:var(--red);">{_T("Connection error")}</span>';

          }});

        }}



        </script>

        """, active_page="student_settings")



    # ── Essay Assistant ──────────────────────────────────────

    @app.route("/student/essay")

    def student_essay_page():

        if not _logged_in():

            return redirect(url_for("login"))

        return _s_render("Essay Assistant", f"""

        <div style="max-width:900px;margin:0 auto">

          <h1 style="margin:0 0 6px">✏️ Essay Assistant</h1>

          <p style="color:var(--text-muted);margin:0 0 18px">Paste your draft. Get brutally honest feedback on thesis, structure, grammar, and flow.</p>

          <div class="card">

            <div class="form-group">

              <label>Assignment prompt <span style="color:var(--text-muted);font-size:12px">(optional)</span></label>

              <input id="ea-prompt" type="text" placeholder="What was the essay supposed to answer?" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>



            <!-- Drag & drop file -->

            <div class="form-group">

              <label>Or drop your essay file</label>

              <div id="ea-drop" style="border:2px dashed var(--border);border-radius:10px;padding:18px;text-align:center;cursor:pointer;background:var(--bg);transition:all .2s"

                ondragover="event.preventDefault();this.style.borderColor='var(--primary)'"

                ondragleave="this.style.borderColor='var(--border)'"

                ondrop="eaHandleDrop(event)" onclick="document.getElementById('ea-file').click()">

                <div style="font-size:28px">📄</div>

                <div style="font-weight:600;margin-top:4px;font-size:14px">Drop a PDF / DOCX / TXT</div>

                <div style="font-size:12px;color:var(--text-muted)">we'll extract the text into the editor below</div>

                <input type="file" id="ea-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="eaHandleFile(this.files[0])">

                <div id="ea-file-info" style="margin-top:6px;font-size:12px;color:var(--primary)"></div>

              </div>

            </div>



            <div class="form-group">

              <label>Your essay</label>

              <textarea id="ea-essay" placeholder="Paste your draft here..." style="width:100%;min-height:260px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);resize:vertical"></textarea>

            </div>

            <div style="display:flex;gap:8px;align-items:center">

              <button onclick="analyzeEssay()" class="btn btn-primary" id="ea-btn">Analyze</button>

              <span id="ea-status" style="color:var(--text-muted);font-size:13px"></span>

            </div>

          </div>

          <div id="ea-result" style="margin-top:18px"></div>

        </div>

        <script>

        async function eaHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.style.borderColor = 'var(--border)';

          if (e.dataTransfer.files.length) await eaHandleFile(e.dataTransfer.files[0]);

        }}

        async function eaHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('PDF, DOCX, or TXT only'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var info = document.getElementById('ea-file-info');

          info.textContent = '⏳ Extracting ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await r.json();

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            document.getElementById('ea-essay').value = d.text;

            info.textContent = '✅ Loaded ' + d.filename + ' (' + d.char_count.toLocaleString() + ' chars)';

          }} catch(e) {{ info.textContent = '❌ Network error'; }}

        }}

        async function analyzeEssay() {{

          var essay = document.getElementById('ea-essay').value.trim();

          if (essay.length < 80) {{ alert('Paste at least a couple of paragraphs.'); return; }}

          var btn = document.getElementById('ea-btn');

          var status = document.getElementById('ea-status');

          btn.disabled = true; btn.textContent = 'Analyzing...';

          status.textContent = 'This takes ~10 seconds.';

          var meta = document.querySelector('meta[name="csrf-token"]');

          var headers = {{'Content-Type':'application/json'}};

          if (meta) headers['X-CSRFToken'] = meta.getAttribute('content');

          try {{

            var r = await fetch('/api/student/essay/analyze', {{

              method:'POST', headers: headers,

              body: JSON.stringify({{essay: essay, prompt: document.getElementById('ea-prompt').value}})

            }});

            var d = await r.json();

            if (!r.ok) throw new Error(d.error || 'Analyze failed');

            renderEssay(d);

          }} catch(e) {{ status.innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }}

          finally {{ btn.disabled = false; btn.textContent = 'Analyze'; }}

        }}

        function renderEssay(d) {{

          var out = document.getElementById('ea-result');

          function bar(label, val) {{

            var color = val >= 85 ? '#22c55e' : (val >= 70 ? '#eab308' : '#ef4444');

            return '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:13px"><span>' + label + '</span><span style="font-weight:700">' + val + '</span></div>'

              + '<div style="background:var(--border);height:8px;border-radius:4px;overflow:hidden"><div style="width:' + val + '%;height:100%;background:' + color + '"></div></div></div>';

          }}

          var strengths = (d.strengths || []).map(function(s){{ return '<li>' + s + '</li>'; }}).join('');

          var weaknesses = (d.weaknesses || []).map(function(s){{ return '<li>' + s + '</li>'; }}).join('');

          var grammar = (d.grammar_issues || []).map(function(g){{

            return '<div style="padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px">'

              + '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">' + (g.reason || '') + '</div>'

              + '<div style="text-decoration:line-through;color:#ef4444">' + (g.original || '') + '</div>'

              + '<div style="color:#22c55e;margin-top:4px">→ ' + (g.suggestion || '') + '</div></div>';

          }}).join('') || '<p style="color:var(--text-muted);font-size:13px">No major grammar issues detected.</p>';

          out.innerHTML =

            '<div class="card"><h2 style="margin:0 0 10px">Overall: ' + (d.overall_score || 0) + '/100</h2>'

            + bar('Thesis', d.thesis_strength || 0)

            + bar('Structure', d.structure_score || 0)

            + bar('Grammar', d.grammar_score || 0)

            + bar('Clarity', d.clarity_score || 0)

            + '<div style="margin-top:14px;font-size:13px;color:var(--text-muted)">Words: ' + (d.word_count || 0) + ' · Level: ' + (d.reading_level || '—') + '</div></div>'

            + (d.thesis_feedback ? '<div class="card"><h3 style="margin:0 0 8px">Thesis Feedback</h3><p style="margin:0">' + d.thesis_feedback + '</p></div>' : '')

            + (strengths ? '<div class="card"><h3 style="margin:0 0 8px;color:#22c55e">Strengths</h3><ul style="margin:0;padding-left:20px">' + strengths + '</ul></div>' : '')

            + (weaknesses ? '<div class="card"><h3 style="margin:0 0 8px;color:#ef4444">Weaknesses</h3><ul style="margin:0;padding-left:20px">' + weaknesses + '</ul></div>' : '')

            + '<div class="card"><h3 style="margin:0 0 8px">Grammar & Style</h3>' + grammar + '</div>'

            + (d.improved_intro ? '<div class="card"><h3 style="margin:0 0 8px">Rewritten Intro</h3><p style="margin:0;white-space:pre-wrap">' + d.improved_intro + '</p></div>' : '');

        }}

        </script>

        """, active_page="student_essay")



    @app.route("/api/student/essay/analyze", methods=["POST"])

    def student_essay_analyze_api():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        essay = (data.get("essay") or "").strip()

        if len(essay) < 50:

            return jsonify({"error": "Essay too short"}), 400

        try:

            result = analyze_essay(essay, data.get("prompt", ""))

            return jsonify(result)

        except Exception as e:

            log.exception("essay analyze failed")

            return jsonify({"error": str(e)}), 500



    # ── Panic Mode ───────────────────────────────────────────

    @app.route("/student/panic")

    def student_panic_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        options = '<option value="">— pick a course —</option>'

        for c in courses:

            options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

        return _s_render("Panic Mode", f"""

        <div style="max-width:820px;margin:0 auto">

          <h1 style="margin:0 0 6px;color:#ef4444">🚨 Panic Mode</h1>

          <p style="color:var(--text-muted);margin:0 0 18px">Exam tomorrow and nothing's done? Get a ruthless cram plan in 10 seconds.</p>

          <div class="card">

            <div class="form-group">

              <label>Course</label>

              <select id="pm-course" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">{options}</select>

            </div>

            <div class="form-group">

              <label>Exam name</label>

              <input id="pm-exam" type="text" placeholder="Midterm / Final / Quiz 3" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <div class="form-group">

              <label>Hours available</label>

              <input id="pm-hours" type="number" step="0.5" min="0.5" value="4" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <div class="form-group">

              <label>Topics (one per line)</label>

              <textarea id="pm-topics" placeholder="Ch 1: derivatives&#10;Ch 2: integrals&#10;Ch 3: limits" style="width:100%;min-height:140px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);resize:vertical"></textarea>

            </div>

            <div class="form-group">

              <label>Weak areas <span style="color:var(--text-muted);font-size:12px">(optional, comma-separated)</span></label>

              <input id="pm-weak" type="text" placeholder="integration by parts, chain rule" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>

            <button onclick="buildPlan()" class="btn" id="pm-btn" style="background:#ef4444;color:#fff;border:none">Generate Cram Plan</button>

            <span id="pm-status" style="margin-left:10px;color:var(--text-muted);font-size:13px"></span>

          </div>

          <div id="pm-result" style="margin-top:18px"></div>

        </div>

        <script>

        async function buildPlan() {{

          var hours = parseFloat(document.getElementById('pm-hours').value || '0');

          if (!hours || hours <= 0) {{ alert('Hours required'); return; }}

          var topics = document.getElementById('pm-topics').value.split('\\n').map(function(s){{return s.trim();}}).filter(Boolean);

          if (!topics.length) {{ alert('List at least one topic'); return; }}

          var btn = document.getElementById('pm-btn');

          var status = document.getElementById('pm-status');

          btn.disabled = true; btn.textContent = 'Thinking...';

          status.textContent = 'Building plan...';

          var meta = document.querySelector('meta[name="csrf-token"]');

          var headers = {{'Content-Type':'application/json'}};

          if (meta) headers['X-CSRFToken'] = meta.getAttribute('content');

          try {{

            var r = await fetch('/api/student/panic/plan', {{

              method:'POST', headers: headers,

              body: JSON.stringify({{

                hours: hours,

                topics: topics,

                exam_name: document.getElementById('pm-exam').value || 'Exam',

                course_id: document.getElementById('pm-course').value,

                weak: document.getElementById('pm-weak').value.split(',').map(function(s){{return s.trim();}}).filter(Boolean)

              }})

            }});

            var d = await r.json();

            if (!r.ok) throw new Error(d.error || 'Failed');

            renderPlan(d);

          }} catch(e) {{ status.innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }}

          finally {{ btn.disabled = false; btn.textContent = 'Generate Cram Plan'; }}

        }}

        function renderPlan(d) {{

          var out = document.getElementById('pm-result');

          var blocks = (d.blocks || []).map(function(b, i){{

            var isBreak = (b.technique || '').indexOf('break') >= 0;

            var bg = isBreak ? 'background:rgba(34,197,94,.08)' : '';

            return '<div class="card" style="margin-bottom:10px;' + bg + '">'

              + '<div style="display:flex;justify-content:space-between;align-items:center">'

              + '<div style="font-weight:700">' + (i+1) + '. ' + (b.topic || '') + '</div>'

              + '<div style="color:var(--text-muted);font-size:13px">' + (b.duration_min || 0) + ' min</div></div>'

              + '<div style="font-size:14px;margin-top:4px">' + (b.focus || '') + '</div>'

              + '<div style="font-size:12px;color:var(--text-muted);margin-top:4px">Technique: ' + (b.technique || '') + ' · ' + (b.why || '') + '</div></div>';

          }}).join('');

          var qw = (d.quick_wins || []).map(function(s){{return '<li>' + s + '</li>';}}).join('');

          var skip = (d.skip_these || []).map(function(s){{return '<li>' + s + '</li>';}}).join('');

          out.innerHTML =

            (d.strategy_summary ? '<div class="card"><h3 style="margin:0 0 8px">Strategy</h3><p style="margin:0">' + d.strategy_summary + '</p></div>' : '')

            + (qw ? '<div class="card"><h3 style="margin:0 0 8px;color:#22c55e">Quick Wins</h3><ul style="margin:0;padding-left:20px">' + qw + '</ul></div>' : '')

            + (skip ? '<div class="card"><h3 style="margin:0 0 8px;color:#ef4444">Skip These</h3><ul style="margin:0;padding-left:20px">' + skip + '</ul></div>' : '')

            + '<h3 style="margin:18px 0 10px">Schedule (' + (d.total_minutes || 0) + ' min)</h3>' + blocks;

        }}

        </script>

        """, active_page="student_panic")



    @app.route("/api/student/panic/plan", methods=["POST"])

    def student_panic_plan_api():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        try:

            hours = float(data.get("hours") or 0)

        except Exception:

            hours = 0

        topics = data.get("topics") or []

        if hours <= 0 or not topics:

            return jsonify({"error": "hours and topics required"}), 400

        course_name = ""

        course_ctx = ""

        cid_val = data.get("course_id")

        if cid_val:

            try:

                course = sdb.get_course(int(cid_val))

                if course and course["client_id"] == _cid():

                    course_name = course.get("name", "") or ""

                    analysis = course.get("analysis_json") or {}

                    if isinstance(analysis, str):

                        try:

                            analysis = json.loads(analysis)

                        except Exception:

                            analysis = {}

                    course_ctx = (analysis.get("summary") or "")[:2500]

            except Exception:

                pass

        try:

            plan = generate_cram_plan(

                hours_available=hours,

                exam_topics=topics,

                exam_name=data.get("exam_name") or "Exam",

                course_name=course_name,

                known_weak_areas=data.get("weak") or [],

                course_context=course_ctx,

            )

            return jsonify(plan)

        except Exception as e:

            log.exception("panic plan failed")

            return jsonify({"error": str(e)}), 500



    log.info("Student routes registered.")

