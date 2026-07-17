"""MachReach Uni background jobs. Run separately with ``python worker.py``."""
from __future__ import annotations

import os
import time

from apscheduler.schedulers.blocking import BlockingScheduler


# ── Sentry error tracking (production only) ──
from machreach_core.config import SENTRY_DSN
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment="worker",
        release=(os.getenv("RENDER_GIT_COMMIT") or "").strip() or None,
        send_default_pii=False,
    )

from machreach_core.db import (
    check_schema_readiness,
    claim_async_jobs,
    fail_async_job,
    init_db,
    record_worker_heartbeat,
    set_async_job_status,
)



def _report_worker_error(context: str, exc: Exception) -> None:
    print(f"[{context}] failed: {type(exc).__name__}")
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("worker_job", context)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass








def _set_quiz_job_status(client_id: int, status: str, progress: str = "", **payload):
    error = str(payload.pop("error", "") or "")
    set_async_job_status(
        "student_quiz_generation",
        str(client_id),
        status,
        progress=progress,
        payload=payload,
        error=error,
    )


def _process_student_quiz_job(job: dict):
    client_id = int(job["job_key"])
    data = job.get("input") or {}
    _set_quiz_job_status(client_id, "running", "Generating your quiz...")
    try:
        from student import db as sdb
        from student import subscription as _sub
        from student.analyzer import generate_quiz

        ok, why = _sub.can_generate_quiz_today(client_id)
        if not ok:
            raise ValueError(why)

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
        count = _sub.cap_questions(client_id, max(1, min(count, 100)))

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
                raise RuntimeError("Failed to generate quiz. Try again.")
            title = ad_hoc_title or f"Quiz: {course_name} ({difficulty})"
            quiz_id = sdb.create_quiz(client_id, title, difficulty, course_id=course_id or None, exam_id=exam_id)
            sdb.add_quiz_questions(quiz_id, questions)
        else:
            if not course_id:
                raise ValueError("course_id required")
            course = sdb.get_course(course_id)
            if not course or course["client_id"] != client_id:
                raise ValueError("Course not found")
            source_text = ""
            for f in sdb.get_course_files(client_id, course_id, exam_id=exam_id):
                if f.get("extracted_text"):
                    source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"
            for n in sdb.get_notes(client_id, course_id):
                if n.get("content_html"):
                    source_text += n["content_html"] + "\n\n"
            if not source_text.strip():
                raise ValueError("No files uploaded for this course/exam. Please upload your study material first.")
            questions = generate_quiz(
                course_name=course["name"],
                topics=topics or None,
                source_text=source_text,
                difficulty=difficulty,
                count=count,
            )
            if not questions:
                raise RuntimeError("Failed to generate quiz. Try again.")
            title = data.get("title", f"Quiz: {course['name']} ({difficulty})")
            quiz_id = sdb.create_quiz(client_id, title, difficulty, course_id=course_id, exam_id=exam_id)
            sdb.add_quiz_questions(quiz_id, questions)

        try:
            _sub.record_generation(client_id, "quiz_generated")
        except Exception:
            pass

        _set_quiz_job_status(
            client_id,
            "done",
            "Quiz generated!",
            quiz_id=quiz_id,
            question_count=len(questions),
            requested=count,
            short=len(questions) < count,
        )
        print(f"[ASYNC JOBS] Generated quiz {quiz_id}")
    except Exception as exc:
        print(f"[ASYNC JOBS] Quiz generation failed: {type(exc).__name__}")
        fail_async_job(
            "student_quiz_generation",
            str(client_id),
            str(exc),
            progress=str(exc),
            retry=not isinstance(exc, ValueError),
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass


def _set_flashcard_job_status(client_id: int, status: str, progress: str = "", **payload):
    error = str(payload.pop("error", "") or "")
    set_async_job_status(
        "student_flashcard_generation",
        str(client_id),
        status,
        progress=progress,
        payload=payload,
        error=error,
    )


def _process_student_flashcard_job(job: dict):
    client_id = int(job["job_key"])
    data = job.get("input") or {}
    _set_flashcard_job_status(client_id, "running", "Generating your flashcards...")
    try:
        from student import db as sdb
        from student import subscription as _sub
        from student.analyzer import generate_flashcards

        ok, why = _sub.can_generate_flashcards_today(client_id)
        if not ok:
            raise ValueError(why)

        course_id = data.get("course_id")
        exam_id = data.get("exam_id")
        ad_hoc_source = (data.get("source_text") or "").strip()[:250000]
        ad_hoc_title = (data.get("title") or "").strip()[:160]
        topics = data.get("topics", [])
        try:
            count = int(data.get("count", 15))
        except (TypeError, ValueError):
            count = 15
        count = _sub.cap_cards(client_id, max(1, min(count, 100)))

        source_type = "upload"
        if ad_hoc_source:
            course_name = ad_hoc_title or "Custom Material"
            source_text = ad_hoc_source
            source_type = "drop"
        else:
            if not course_id:
                raise ValueError("course_id required")
            course = sdb.get_course(course_id)
            if not course or course["client_id"] != client_id:
                raise ValueError("Course not found")
            course_name = course["name"]
            source_text = ""
            for file_row in sdb.get_course_files(client_id, course_id, exam_id=exam_id):
                if file_row.get("extracted_text"):
                    source_text += (
                        f"--- {file_row.get('original_name', '')} ---\n"
                        f"{file_row['extracted_text']}\n\n"
                    )
            for note in sdb.get_notes(client_id, course_id):
                if note.get("content_html"):
                    source_text += note["content_html"] + "\n\n"
            source_text = source_text[:250000]
            if not source_text.strip():
                raise ValueError(
                    "No files uploaded for this course/exam. Please upload your study material first."
                )

        cards = generate_flashcards(
            course_name=course_name,
            topics=topics or None,
            source_text=source_text,
            count=count,
        )
        if not cards:
            raise RuntimeError("Failed to generate flashcards. Try again.")

        title = ad_hoc_title or f"Flashcards: {course_name}"
        deck_id = sdb.create_flashcard_deck(
            client_id,
            title,
            course_id=course_id or None,
            exam_id=exam_id,
            source_type=source_type,
        )
        sdb.add_flashcards(deck_id, cards)
        _sub.record_generation(client_id, "flashcards_generated")
        _set_flashcard_job_status(
            client_id,
            "done",
            "Flashcards generated!",
            deck_id=deck_id,
            card_count=len(cards),
            requested=count,
        )
        print(f"[ASYNC JOBS] Generated flashcard deck {deck_id}")
    except Exception as exc:
        print(f"[ASYNC JOBS] Flashcard generation failed: {type(exc).__name__}")
        fail_async_job(
            "student_flashcard_generation",
            str(client_id),
            str(exc),
            progress=str(exc),
            retry=not isinstance(exc, ValueError),
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass






def process_async_jobs():
    """Claim and run queued one-off jobs from the database."""
    record_worker_heartbeat()
    processed = 0
    for job in claim_async_jobs("student_quiz_generation", limit=2, progress="Generating your quiz..."):
        processed += 1
        _process_student_quiz_job(job)
    for job in claim_async_jobs("student_flashcard_generation", limit=2, progress="Generating your flashcards..."):
        processed += 1
        _process_student_flashcard_job(job)
    if processed:
        print(f"[ASYNC JOBS] Processed {processed} job(s).")


def heartbeat():
    record_worker_heartbeat()


def cancel_retired_product_subscriptions():
    """Drain provider cancellations archived by the removal migration."""
    from machreach_core import lemonsqueezy as ls
    from machreach_core.db import (
        finish_retired_billing_cancellation,
        list_retired_billing_cancellations,
    )

    for row in list_retired_billing_cancellations(limit=25):
        subscription_id = row["subscription_id"]
        try:
            if ls.cancel_subscription(subscription_id):
                finish_retired_billing_cancellation(subscription_id)
            else:
                finish_retired_billing_cancellation(
                    subscription_id, error="provider cancellation failed"
                )
        except Exception as exc:
            finish_retired_billing_cancellation(
                subscription_id, error=type(exc).__name__
            )
            _report_worker_error("RETIRED_BILLING_CANCELLATION", exc)


def recover_worker_state():
    """Recover interrupted student background jobs."""
    try:
        from machreach_core.db import recover_stale_async_jobs
        recovered = recover_stale_async_jobs()
        if recovered:
            print(f"[RECOVERY] jobs={recovered}")
    except Exception as exc:
        print(f"[RECOVERY] failed: {type(exc).__name__}")
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass








def refresh_student_plans():
    """Midnight job: for each student with a plan, check for incomplete
    assignments and regenerate the study plan with those rolled over."""
    try:
        from student import db as sdb
        from student.analyzer import generate_study_plan
        import json
        from datetime import datetime

        client_ids = sdb.get_all_student_client_ids()
        today = datetime.now().strftime("%Y-%m-%d")

        for client_id in client_ids:
            try:
                # Check if there are incomplete assignments
                incomplete = sdb.get_incomplete_assignments(client_id, today)

                # Get course data
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
                    continue

                # Get schedule settings
                schedule_settings = sdb.get_schedule_settings(client_id)
                schedule_list = []
                if schedule_settings:
                    for s in schedule_settings:
                        schedule_list.append({
                            "day": s["day_of_week"],
                            "hours": s["available_hours"],
                            "free": bool(s["is_free_day"]),
                        })

                # Get latest preferences
                plan_row = sdb.get_latest_plan(client_id)
                preferences = plan_row.get("preferences_json", {}) if plan_row else {}

                # Regenerate
                plan = generate_study_plan(
                    courses_data, preferences,
                    schedule_settings=schedule_list or None,
                    course_difficulties=course_difficulties or None,
                    incomplete_assignments=incomplete or None,
                )
                if plan.get("daily_plan"):
                    sdb.save_study_plan(client_id, plan, preferences)
                    print(f"[STUDENT] Refreshed study plan"
                          f" ({len(incomplete)} incomplete assignments rolled over)")
            except Exception as e:
                print(f"[STUDENT] Plan refresh failed: {type(e).__name__}")
                _report_worker_error("STUDENT_PLAN_ACCOUNT", e)
    except Exception as e:
        _report_worker_error("STUDENT_PLAN_REFRESH", e)


def send_streak_risk_pushes():
    """8pm cron: email students whose streak >= 5 and have no activity today."""
    try:
        from student import db as sdb
        import smtplib
        from email.mime.text import MIMEText
        from machreach_core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, BASE_URL
        if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
            print("[STREAK_RISK] SMTP not configured, skipping.")
            return
        recipients = sdb.get_streak_risk_recipients(min_streak=5)
        print(f"[STREAK_RISK] {len(recipients)} user(s) at risk")
        for r in recipients:
            try:
                streak = int(r["streak"])
                name = r.get("name") or "there"
                subject = f"Don't lose your {streak}-day streak"
                body = (
                    f"Hi {name},\n\n"
                    f"You still have time today. Your {streak}-day study streak resets at midnight — "
                    f"10 minutes of focus saves it.\n\n"
                    f"Open MachReach: {BASE_URL}/student/dashboard\n\n"
                    f"— MachReach"
                )
                msg = MIMEText(body, "plain", "utf-8")
                msg["From"] = SMTP_USER
                msg["To"] = r["email"]
                msg["Subject"] = subject
                if int(SMTP_PORT) == 587:
                    with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=30) as srv:
                        srv.starttls()
                        srv.login(SMTP_USER, SMTP_PASSWORD)
                        srv.send_message(msg)
                else:
                    with smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), timeout=30) as srv:
                        srv.login(SMTP_USER, SMTP_PASSWORD)
                        srv.send_message(msg)
                print(f"  [STREAK_RISK] sent (streak={streak})")
            except Exception as e:
                print(f"  [STREAK_RISK] failed: {type(e).__name__}")
                _report_worker_error("STREAK_RISK_ACCOUNT", e)
    except Exception as e:
        _report_worker_error("STREAK_RISK", e)


# ── Monthly leaderboard winners email ────────────────────────────────────
from machreach_core.config import LEADERBOARD_WINNERS_RECIPIENT


def send_monthly_leaderboard_email(year: int | None = None, month: int | None = None):
    """Email the leaderboard winners for a given calendar month.

    With no arguments: sends *previous* calendar month (cron entry point).
    With (year, month): sends that specific month — used by the admin
    preview "Send" button.

    Recipient is configured with LEADERBOARD_WINNERS_RECIPIENT.
    Includes monthly summary stats + per-country/university/major top 3.
    """
    try:
        from datetime import date
        import smtplib
        from email.mime.text import MIMEText
        from machreach_core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

        if not LEADERBOARD_WINNERS_RECIPIENT:
            print("[MONTHLY_WINNERS] recipient not configured, skipping")
            return
        if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
            print("[MONTHLY_WINNERS] SMTP not configured, skipping.")
            return

        if year is None or month is None:
            today = date.today()
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1

        from student.academic import monthly_winners
        data = monthly_winners(year, month, top_n=3)
        label = data["label"]
        summary = data.get("summary", {}) or {}

        def _fmt_rows(rows):
            if not rows:
                return "  (sin participantes este mes)\n"
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            lines = []
            for r in rows:
                m = medals.get(r["rank"], f"#{r['rank']}")
                lines.append(f"  {m} {r['name']} — {r['xp']:,} XP (client {r['client_id']})")
            return "\n".join(lines) + "\n"

        def _section(title: str, groups: list):
            out = [f"\n{title}"]
            if not groups:
                out.append("  (sin participantes este mes)\n")
                return "\n".join(out)
            for grp in groups:
                out.append(f"\n  [{grp['label']}]")
                out.append(_fmt_rows(grp["rows"]))
            return "\n".join(out)

        body = []
        body.append(f"MachReach — Leaderboard winners for {label}")
        body.append("=" * 60)
        body.append("")
        body.append(f"Period: {data['start']} → {data['end_exclusive']} (exclusive)")
        body.append("")
        body.append("📊 RESUMEN DEL MES")
        body.append(f"  Total XP otorgado:    {summary.get('total_xp_awarded', 0):,}")
        body.append(f"  Estudiantes activos:  {summary.get('active_students', 0)} / {summary.get('total_students', 0)}")
        body.append(f"  Nuevas inscripciones: {summary.get('new_students', 0)}")
        body.append("")
        # Global leaderboard intentionally disabled — only Chile is active.

        body.append(_section("🏳️ POR PAÍS", data["by_country"]))
        body.append(_section("🎓 POR UNIVERSIDAD", data["by_university"]))
        body.append(_section("📚 POR CARRERA", data["by_major"]))

        if not (data["by_country"] or data["by_university"] or data["by_major"]):
            body.append("")
            body.append("ℹ️ No hubo XP ganado en este período. Vuelve a revisar el próximo mes.")

        body.append("")
        body.append("— MachReach")

        msg = MIMEText("\n".join(body), "plain", "utf-8")
        msg["From"] = SMTP_USER
        msg["To"] = LEADERBOARD_WINNERS_RECIPIENT
        msg["Subject"] = f"MachReach leaderboard winners — {label}"

        if int(SMTP_PORT) == 587:
            with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=30) as srv:
                srv.starttls()
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), timeout=30) as srv:
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.send_message(msg)

        print(f"[MONTHLY_WINNERS] sent {label} report")
    except Exception as e:
        _report_worker_error("MONTHLY_WINNERS", e)


if __name__ == "__main__":
    is_production = bool(os.getenv("RENDER")) or any(
        (os.getenv(name) or "").strip().lower() == "production"
        for name in ("FLASK_ENV", "APP_ENV", "ENVIRONMENT")
    )
    if not is_production:
        init_db()
        try:
            from student.db import init_student_db
            init_student_db()
        except Exception as e:
            _report_worker_error("STUDENT_DB_INIT", e)
            raise
    check_schema_readiness()

    print("MachReach Uni worker started.")

    scheduler = BlockingScheduler()
    scheduler.add_job(process_async_jobs, "interval", seconds=5, id="process_async_jobs", max_instances=1)
    scheduler.add_job(heartbeat, "interval", minutes=1, id="worker_heartbeat")
    scheduler.add_job(recover_worker_state, "interval", minutes=1, id="recover_worker_state")
    scheduler.add_job(cancel_retired_product_subscriptions, "interval", minutes=1,
                      id="retired_billing_cancellations")
    scheduler.add_job(refresh_student_plans, "cron", hour=0, minute=0, id="refresh_student_plans")
    scheduler.add_job(send_streak_risk_pushes, "cron", hour=20, minute=0, id="streak_risk_push")
    # First of every month at 01:00 UTC: email previous month's leaderboard winners.
    scheduler.add_job(send_monthly_leaderboard_email, "cron", day=1, hour=1, minute=0,
                      id="monthly_leaderboard_email")

    # Run once immediately
    heartbeat()
    recover_worker_state()
    cancel_retired_product_subscriptions()
    process_async_jobs()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Worker stopped.")
