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

                for idx, c in enumerate(courses):
                    cid_canvas = c["id"]
                    name = c.get("name", "Unknown Course")
                    code = c.get("course_code", "")
                    _sync_status[client_id]["progress"] = f"Syncing {name} ({idx + 1}/{len(courses)})"

                    db_id = sdb.upsert_course(client_id, cid_canvas, name, code)

                    syllabus_html = ""
                    file_texts = []
                    assignments = []

                    try:
                        syllabus_html = canvas.get_syllabus(cid_canvas)
                    except Exception:
                        pass

                    # Download ALL PDF/DOCX files — no limit
                    try:
                        all_files = canvas.get_files(cid_canvas)
                        for sf in all_files:
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
                    except Exception:
                        pass

                    try:
                        assignments = canvas.get_assignments(cid_canvas)
                    except Exception:
                        pass

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

                _sync_status[client_id] = {
                    "status": "done",
                    "progress": f"Synced {len(synced)} courses, {total_files} files downloaded",
                    "courses_total": len(courses),
                    "courses_done": len(courses),
                    "files_downloaded": total_files,
                    "courses": synced,
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
        file_id = sdb.save_course_file(_cid(), course_id, fname, file_type, text)

        return jsonify({"id": file_id, "filename": fname, "text_length": len(text)})

    @app.route("/api/student/files/<int:file_id>", methods=["DELETE"])
    def student_delete_file(file_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        sdb.delete_course_file(file_id, _cid())
        return jsonify({"ok": True})

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
        plan_row = sdb.get_latest_plan(cid)

        # Today's plan
        today_plan = None
        today_sessions_html = ""
        if plan_row:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for day in plan_row["plan_json"].get("daily_plan", []):
                if day.get("date") == today_str:
                    today_plan = day
                    break

        if today_plan:
            for s in today_plan.get("sessions", []):
                prio_colors = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}
                pc = prio_colors.get(s.get("priority", "medium"), "#94A3B8")
                today_sessions_html += f"""
                <div style="background:var(--card);border:1px solid var(--border);border-left:4px solid {pc};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                      <span style="font-weight:700;color:var(--text);">{_esc(s.get('course',''))}</span>
                      <span style="color:var(--text-muted);font-size:13px;margin-left:8px;">{s.get('hours',0)}h &middot; {s.get('type','study')}</span>
                    </div>
                    <span style="background:{pc}18;color:{pc};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">{s.get('priority','').upper()}</span>
                  </div>
                  <div style="color:var(--text-secondary);font-size:14px;margin-top:6px;">{_esc(s.get('topic',''))}</div>
                  {"<div style='color:var(--text-muted);font-size:12px;margin-top:4px;font-style:italic;'>" + _esc(s.get('reason','')) + "</div>" if s.get('reason') else ""}
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

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:24px;">
          <div class="stat-card stat-purple"><div class="num">{len(courses)}</div><div class="label">Courses</div></div>
          <div class="stat-card stat-red"><div class="num">{stats['upcoming_exams']}</div><div class="label">Upcoming Exams</div></div>
          <div class="stat-card stat-green"><div class="num">{stats['completion_pct']}%</div><div class="label">Progress</div></div>
          <div class="stat-card stat-blue"><div class="num" style="font-size:14px;color:{canvas_color};">{canvas_status}</div><div class="label">Canvas</div></div>
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
                alert('Sync complete! ' + d.files_downloaded + ' files processed across ' + d.courses_done + ' courses.');
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
            if (r.ok) {{ alert('Plan generated!'); location.reload(); }}
            else {{ alert(d.error || 'Generation failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#129302; Generate Plan';
        }}
        async function markComplete() {{
          var r = await fetch('/api/student/progress/complete', {{method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify({{}})}});
          if (r.ok) {{ alert('Today marked as complete!'); location.reload(); }}
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
              <td style="font-weight:600;">{_esc(c['name'])}</td>
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
            </tr>"""
        if not rows:
            rows = """<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--text-muted);">
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
            <thead><tr><th>Course</th><th>Code</th><th>Exams</th><th>Schedule</th><th>Grading</th><th>Files</th><th>Last Synced</th></tr></thead>
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
        <div id="sync-progress" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;margin-top:16px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="animation:spin 1s linear infinite;display:inline-block;">&#9203;</span>
            <span id="sync-msg" style="color:var(--text-secondary);font-size:14px;">Starting sync...</span>
          </div>
          <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Courses: <span id="sync-courses">0/0</span> &middot; Files: <span id="sync-files">0</span></div>
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
        async function uploadFile(courseId, input) {{
          if (!input.files[0]) return;
          var fd = new FormData();
          fd.append('file', input.files[0]);
          var csrfToken = document.querySelector('meta[name="csrf-token"]');
          var headers = {{}};
          if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;
          try {{
            var r = await fetch('/api/student/courses/' + courseId + '/upload', {{method:'POST', body:fd, headers:headers}});
            var d = await r.json();
            if (r.ok) {{ alert('File uploaded! ' + d.text_length + ' characters extracted. Re-sync to update your study plan.'); location.reload(); }}
            else {{ alert(d.error || 'Upload failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          input.value = '';
        }}
        async function deleteFile(fileId) {{
          if (!confirm('Delete this file?')) return;
          await fetch('/api/student/files/' + fileId, {{method:'DELETE'}});
          location.reload();
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
            for day in daily[:30]:  # Show next 30 days
                d = day.get("date", "")
                done = d in completed_dates
                icon = "&#10003;" if done else "&#9744;"
                bg = "var(--green-light)" if done else "var(--card)"
                border_c = "var(--green)" if done else "var(--border)"
                sessions = ""
                for s in day.get("sessions", []):
                    sessions += f"<div style='font-size:13px;color:var(--text-secondary);'>{_esc(s.get('course',''))} — {_esc(s.get('topic',''))} ({s.get('hours',0)}h)</div>"
                days_html += f"""
                <div style="background:{bg};border:1px solid {border_c};border-radius:var(--radius-sm);padding:12px 16px;margin-bottom:8px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-weight:700;">{icon} {day.get('day_name','')} {d}</span>
                    <span style="font-size:13px;color:var(--text-muted);">{day.get('total_hours', 0)}h</span>
                  </div>
                  {sessions}
                </div>"""
            content = f"""
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <span style="font-size:13px;color:var(--text-muted);">Generated: {plan_row.get('generated_at','?')}</span>
              <button onclick="generatePlan()" class="btn btn-outline btn-sm" id="plan-btn">&#128260; Regenerate</button>
            </div>
            {days_html}"""

        return _s_render("Study Plan", f"""
        <h1 style="margin-bottom:20px;">&#128197; Study Plan</h1>
        <div class="card">{content}</div>
        <script>
        async function generatePlan() {{
          var btn = document.getElementById('plan-btn');
          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';
          try {{
            var r = await fetch('/api/student/plan/generate', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{preferences:{{hours_per_day:5}}}}) }});
            var d = await r.json();
            if (r.ok) {{ alert('Plan generated!'); location.reload(); }}
            else {{ alert(d.error || 'Failed'); }}
          }} catch(e) {{ alert('Network error'); }}
          btn.disabled = false; btn.innerHTML = '&#128260; Regenerate';
        }}
        </script>
        """, active_page="student_plan")

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

    def _esc(s):
        """HTML-escape a string."""
        import html as html_module
        return html_module.escape(str(s)) if s else ""

    log.info("Student routes registered.")
