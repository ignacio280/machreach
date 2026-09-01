"""MachReach Uni background jobs.

Three ways to run them, same jobs:

``EMBEDDED_WORKER=1`` on the web service (production)
    gunicorn's post-fork hook (gunicorn.conf.py) calls start_embedded() in
    the worker process, and the scheduler runs on a small thread pool next
    to the request threads. One paid instance instead of two, and a queued
    quiz is still picked up within five seconds. The jobs mostly wait on
    the database, the AI provider, and SMTP, so they cost the web process
    little CPU.

``python worker.py``
    A long-running scheduler process of its own (local development, a
    Procfile-style host, the VPS stack in deploy/).

``python worker.py --once`` (or ``WORKER_MODE=once``)
    One pass and exit: what the Render cron job runs every minute. Every
    interval job runs once, each time-of-day job runs if its scheduled time
    has passed since the run recorded in the database, and the queue of
    student jobs is drained for a bounded number of seconds. Billing is per
    second of runtime, so an idle run costs a few seconds of a starter
    instance instead of a whole month of one.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
import threading
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


# ── Sentry error tracking (production only) ──
from machreach_core.config import LEADERBOARD_WINNERS_RECIPIENT, SENTRY_DSN
if SENTRY_DSN:
    import sentry_sdk
    # Not when embedded: the web process initialised Sentry as "production"
    # already, and a second init here would relabel every web event.
    if not sentry_sdk.is_initialized():
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment="worker",
            release=(os.getenv("RENDER_GIT_COMMIT") or "").strip() or None,
            send_default_pii=False,
        )

from machreach_core.db import (
    _exec,
    _fetchone,
    _now_expr,
    check_schema_readiness,
    claim_async_jobs,
    fail_async_job,
    get_db,
    init_db,
    record_worker_heartbeat,
    set_async_job_status,
)

SCHEDULER_TIMEZONE = "America/Santiago"



def _report_worker_error(context: str, exc: Exception) -> None:
    print(f"[{context}] failed: {type(exc).__name__}")
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("worker_job", context)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def process_leaderboard_payouts() -> None:
    """Catch up closed leaderboard periods and drain their email outbox."""
    try:
        from student.leaderboard_prizes import (
            cleanup_payout_outbox,
            dispatch_payout_notifications,
            run_due_payouts,
        )
        run_due_payouts()
        dispatch_payout_notifications()
        cleanup_payout_outbox()
    except Exception as exc:
        _report_worker_error("LEADERBOARD_PAYOUTS", exc)








def _require_enough_material(text: str) -> str:
    """Refuse to generate from material that says nothing.

    A scanned PDF saved before extraction learned to reject them still sits in
    course material as page markers and no content. Handed that, the model
    writes a confident quiz about material the student never uploaded, which
    is worse than an error: it looks like the product working.
    """
    from student.canvas import MIN_SOURCE_TEXT_CHARS, readable_text_length

    if readable_text_length(text) < MIN_SOURCE_TEXT_CHARS:
        raise ValueError(
            "This material has almost no readable text. If it is a scanned PDF "
            "(pages that are images), upload a version with selectable text."
        )
    return text


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
    # Keyed on this run, not on the student: reusing one key per student made
    # every quiz after the first replay the first one's settled reservation.
    reservation_key = f"quiz-job:{job.get('run_id') or job['job_key']}"
    _set_quiz_job_status(client_id, "running", "Generating your quiz...")
    try:
        from student import db as sdb
        from student import ai_usage, subscription as _sub
        from student.analyzer import generate_quiz

        ok, why = _sub.can_generate_quiz_today(client_id)
        if not ok:
            raise ValueError(why)
        reservation = ai_usage.reserve(
            client_id,
            request_key=reservation_key,
            feature="quiz_worker",
            usage_kind="quiz_generated",
            estimated_input_tokens=65000,
            estimated_output_tokens=15000,
        )
        if reservation.get("status") == "settled":
            import json
            saved = json.loads(reservation.get("result_json") or "{}")
            _set_quiz_job_status(client_id, "done", "Quiz generated!", **saved)
            return

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
            _require_enough_material(ad_hoc_source)
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
            _require_enough_material(source_text)
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

        ai_usage.settle(
            reservation_key,
            {
                "quiz_id": quiz_id,
                "question_count": len(questions),
                "requested": count,
                "short": len(questions) < count,
            },
        )

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
        try:
            from student.ai_usage import fail
            fail(reservation_key, str(exc))
        except Exception:
            pass
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
    reservation_key = f"flashcard-job:{job.get('run_id') or job['job_key']}"
    _set_flashcard_job_status(client_id, "running", "Generating your flashcards...")
    try:
        from student import db as sdb
        from student import ai_usage, subscription as _sub
        from student.analyzer import generate_flashcards

        ok, why = _sub.can_generate_flashcards_today(client_id)
        if not ok:
            raise ValueError(why)
        reservation = ai_usage.reserve(
            client_id,
            request_key=reservation_key,
            feature="flashcard_worker",
            usage_kind="flashcards_generated",
            estimated_input_tokens=65000,
            estimated_output_tokens=15000,
        )
        if reservation.get("status") == "settled":
            import json
            saved = json.loads(reservation.get("result_json") or "{}")
            _set_flashcard_job_status(
                client_id, "done", "Flashcards generated!", **saved
            )
            return

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

        _require_enough_material(source_text)
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
        ai_usage.settle(
            reservation_key,
            {
                "deck_id": deck_id,
                "card_count": len(cards),
                "requested": count,
            },
        )
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
        try:
            from student.ai_usage import fail
            fail(reservation_key, str(exc))
        except Exception:
            pass
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






def process_async_jobs() -> int:
    """Claim and run queued one-off jobs from the database.

    Returns how many jobs were claimed, so a cron run knows whether the queue
    is empty or worth another pass.
    """
    record_worker_heartbeat()
    processed = 0
    for job in claim_async_jobs("student_quiz_generation", limit=2, progress="Generating your quiz..."):
        processed += 1
        _process_student_quiz_job(job)
    for job in claim_async_jobs("student_flashcard_generation", limit=2, progress="Generating your flashcards..."):
        processed += 1
        _process_student_flashcard_job(job)
    for job in claim_async_jobs("verification_email", limit=5, progress="Sending verification email..."):
        processed += 1
        from app import _send_system_email
        from machreach_core.verification_delivery import process_job
        process_job(job, _send_system_email)
    for job in claim_async_jobs("password_reset_email", limit=5, progress="Sending password reset email..."):
        processed += 1
        from app import _send_system_email
        from machreach_core.verification_delivery import process_reset_job
        process_reset_job(job, _send_system_email)
    if processed:
        print(f"[ASYNC JOBS] Processed {processed} job(s).")
    return processed


def heartbeat():
    record_worker_heartbeat()
    try:
        from student.ai_usage import reconcile_stale_reservations
        recovered = reconcile_stale_reservations()
        if recovered:
            print(f"[AI USAGE] Recovered {recovered} stale reservation(s).")
    except Exception as exc:
        _report_worker_error("AI_USAGE_RECOVERY", exc)


def clean_abandoned_unverified_accounts():
    """Remove pending registrations after the documented seven-day window."""
    from machreach_core.verification_delivery import delete_stale_unverified
    deleted = delete_stale_unverified(days=7)
    if deleted:
        print(f"[ACCOUNT CLEANUP] Deleted {deleted} abandoned unverified account(s).")


def expire_billing_grace_periods():
    from student.subscription import expire_payment_grace_periods
    changed = expire_payment_grace_periods()
    if changed:
        print(f"[BILLING] Expired {changed} payment grace period(s).")


def purge_deleted_courses():
    from student.db import purge_expired_course_deletions
    purged = purge_expired_course_deletions()
    if purged:
        print(f"[COURSES] Purged {purged} expired deletion(s).")


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
        from machreach_core.db import (
            recover_stale_async_jobs,
            reconcile_orphaned_async_jobs,
        )
        recovered = recover_stale_async_jobs()
        if recovered:
            print(f"[RECOVERY] jobs={recovered}")
        settled = reconcile_orphaned_async_jobs()
        if settled:
            print(f"[RECOVERY] settled {settled} orphaned job(s)")
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
        from student import ai_usage, subscription
        from student.analyzer import generate_study_plan
        from student.periods import user_date
        import json

        client_ids = sdb.get_all_student_client_ids()

        for client_id in client_ids:
            today = user_date(client_id).isoformat()
            reservation_key = f"daily-plan:{client_id}:{today}"
            try:
                if not subscription.has_plus_access(client_id):
                    continue
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
                reservation = ai_usage.reserve(
                    client_id,
                    request_key=reservation_key,
                    feature="daily_plan_refresh",
                    usage_kind="ai_tool_generated",
                    estimated_input_tokens=50000,
                    estimated_output_tokens=5000,
                )
                if reservation.get("status") == "settled":
                    continue
                plan = generate_study_plan(
                    courses_data, preferences,
                    schedule_settings=schedule_list or None,
                    course_difficulties=course_difficulties or None,
                    incomplete_assignments=incomplete or None,
                )
                if plan.get("daily_plan"):
                    sdb.save_study_plan(client_id, plan, preferences)
                    ai_usage.settle(reservation_key, {"refreshed": True})
                    print(f"[STUDENT] Refreshed study plan"
                          f" ({len(incomplete)} incomplete assignments rolled over)")
                else:
                    ai_usage.fail(reservation_key, "empty plan")
            except Exception as e:
                try:
                    ai_usage.fail(reservation_key, str(e))
                except Exception:
                    pass
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
                name = r.get("name") or ""
                subject = f"No pierdas tu racha de {streak} días"
                saludo = f"Hola {name}" if name else "Hola"
                body = (
                    f"{saludo},\n\n"
                    f"Todavía estás a tiempo. Tu racha de {streak} días de estudio se reinicia a "
                    f"medianoche — con 10 minutos de enfoque la salvas.\n\n"
                    f"Abre MachReach: {BASE_URL}/student/dashboard\n\n"
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
                try:
                    from machreach_core.db import record_operational_event
                    record_operational_event("smtp_failure", "streak_risk")
                except Exception:
                    pass
                _report_worker_error("STREAK_RISK_ACCOUNT", e)
    except Exception as e:
        _report_worker_error("STREAK_RISK", e)


# ── Monthly leaderboard winners email ────────────────────────────────────
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
        # monthly_winners builds its label with strftime("%B"), which is the
        # C-locale English month on Render. Localise it here rather than in the
        # shared helper — the admin preview page still renders the English one.
        _MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                  "agosto", "septiembre", "octubre", "noviembre", "diciembre")
        label_es = f"{_MESES[month - 1]} {year}"

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
        body.append(f"MachReach — Ganadores del ranking de {label_es}")
        body.append("=" * 60)
        body.append("")
        body.append(f"Período: {data['start']} → {data['end_exclusive']} (exclusivo)")
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
        msg["Subject"] = f"MachReach — Ganadores del ranking de {label_es}"

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
        try:
            from machreach_core.db import record_operational_event
            record_operational_event("smtp_failure", "leaderboard")
        except Exception:
            pass
        _report_worker_error("MONTHLY_WINNERS", e)


# ── Schedule ─────────────────────────────────────────────────────────────
# Time-of-day jobs, in the scheduler timezone. One table serves both runtimes:
# the long-running scheduler registers each as a cron trigger, and a cron-job
# run fires whichever scheduled time has passed since the last run it recorded.
DAILY_JOBS = (
    ("refresh_student_plans", refresh_student_plans, {"hour": 0, "minute": 0}),
    ("streak_risk_push", send_streak_risk_pushes, {"hour": 20, "minute": 0}),
    ("clean_abandoned_unverified_accounts", clean_abandoned_unverified_accounts,
     {"hour": 3, "minute": 30}),
    ("purge_deleted_courses", purge_deleted_courses, {"hour": 3, "minute": 45}),
)

# Jobs the scheduler ran every minute to every hour. A cron run executes each
# of them once, in this order: they are idempotent and cheap, so running the
# hourly one every minute costs a query, while skipping any of them for a
# minute is what used to page someone.
INTERVAL_JOBS = (
    ("worker_heartbeat", heartbeat),
    ("recover_worker_state", recover_worker_state),
    ("retired_billing_cancellations", cancel_retired_product_subscriptions),
    ("expire_billing_grace_periods", expire_billing_grace_periods),
    ("leaderboard_payouts", process_leaderboard_payouts),
)

# Schedule state lives in async_jobs beside the heartbeat, so no migration:
# one row per time-of-day job, whose payload holds the last scheduled time
# that was fired (as the schedule time, not the wall clock, so catch-up after
# an outage is exact).
_SCHEDULE_JOB_TYPE = "worker_schedule"


def _schedule_payload(fired_at: datetime) -> str:
    return json.dumps({"fired_at": fired_at.isoformat()}, separators=(",", ":"))


def _last_fired(job_id: str) -> tuple[datetime | None, str | None]:
    """Return (last scheduled time fired, raw payload) for a time-of-day job."""
    with get_db() as db:
        row = _fetchone(
            db,
            "SELECT payload_json FROM async_jobs WHERE job_type = %s AND job_key = %s",
            (_SCHEDULE_JOB_TYPE, job_id),
        )
    if not row:
        return None, None
    raw = row.get("payload_json")
    try:
        fired_at = datetime.fromisoformat(json.loads(raw or "{}").get("fired_at", ""))
    except (TypeError, ValueError, AttributeError):
        return None, raw
    if fired_at.tzinfo is None:
        fired_at = fired_at.replace(tzinfo=timezone.utc)
    return fired_at, raw


def _claim_schedule_slot(job_id: str, fired_at: datetime, expected_payload: str | None) -> bool:
    """Record ``fired_at`` for a job, only if nobody else has since we looked.

    Two overlapping runs both see the same last-fired time; the conditional
    write lets exactly one of them own the slot, which is what keeps a streak
    reminder from going out twice.
    """
    now = _now_expr()
    payload = _schedule_payload(fired_at)
    with get_db() as db:
        if expected_payload is None:
            cur = _exec(db, f"""
                INSERT INTO async_jobs
                    (job_type, job_key, status, progress, input_json, payload_json,
                     error, attempts, max_attempts, updated_at)
                VALUES (%s, %s, 'done', 'Scheduled job', '{{}}', %s, '', 0, 1, {now})
                ON CONFLICT (job_type, job_key) DO NOTHING
            """, (_SCHEDULE_JOB_TYPE, job_id, payload))
        else:
            cur = _exec(db, f"""
                UPDATE async_jobs
                SET payload_json = %s, status = 'done', error = '', updated_at = {now}
                WHERE job_type = %s AND job_key = %s AND payload_json = %s
            """, (payload, _SCHEDULE_JOB_TYPE, job_id, expected_payload))
        return bool(cur.rowcount)


def _latest_due(trigger: CronTrigger, last: datetime, now: datetime) -> datetime | None:
    """The most recent scheduled time after ``last`` that is not in the future.

    Missed days collapse into one run: after an outage a student gets one
    plan refresh and one streak reminder, not one per missed day.
    """
    due = None
    candidate = trigger.get_next_fire_time(last, now)
    while candidate is not None and candidate <= now:
        due = candidate
        candidate = trigger.get_next_fire_time(candidate, now)
    return due


def run_due_daily_jobs(now: datetime | None = None) -> list[str]:
    """Fire every time-of-day job whose scheduled time has passed since it last ran.

    The first run only seeds each job's state with the current time, so
    switching to cron mode never replays a day's jobs. That matches the
    scheduler, which also fires nothing until the next scheduled time.
    """
    now = now or datetime.now(timezone.utc)
    fired: list[str] = []
    for job_id, func, when in DAILY_JOBS:
        try:
            last, raw = _last_fired(job_id)
            if last is None:
                _claim_schedule_slot(job_id, now, raw)
                continue
            due = _latest_due(CronTrigger(timezone=SCHEDULER_TIMEZONE, **when), last, now)
            if due is None or not _claim_schedule_slot(job_id, due, raw):
                continue
        except Exception as exc:
            _report_worker_error(f"SCHEDULE_{job_id.upper()}", exc)
            continue
        print(f"[SCHEDULE] {job_id} due at {due.isoformat()}", flush=True)
        try:
            func()
        except Exception as exc:
            _report_worker_error(job_id.upper(), exc)
        fired.append(job_id)
    return fired


def _keep_heartbeat_fresh(stop: threading.Event, every: float = 30.0) -> None:
    """Beat while a run is busy, so a long AI job does not read as a dead worker."""
    while not stop.wait(every):
        try:
            record_worker_heartbeat()
        except Exception as exc:
            _report_worker_error("WORKER_HEARTBEAT", exc)


def run_once(max_seconds: float | None = None, now: datetime | None = None) -> dict:
    """One cron-job run. Returns counts for the log line and for tests.

    ``max_seconds`` bounds the queue drain, not a job in flight: a claimed
    quiz still finishes. It defaults to WORKER_RUN_MAX_SECONDS (50), just
    under the one-minute schedule, so a busy queue is worked continuously
    and an idle one costs a few seconds.
    """
    if max_seconds is None:
        max_seconds = float(os.getenv("WORKER_RUN_MAX_SECONDS", "50"))
    started = time.monotonic()
    stop = threading.Event()
    beat = threading.Thread(
        target=_keep_heartbeat_fresh, args=(stop,), name="worker-heartbeat", daemon=True
    )
    beat.start()
    processed = passes = 0
    fired: list[str] = []
    try:
        for job_id, func in INTERVAL_JOBS:
            try:
                func()
            except Exception as exc:
                _report_worker_error(job_id.upper(), exc)
        fired = run_due_daily_jobs(now)
        while True:
            passes += 1
            try:
                claimed = int(process_async_jobs() or 0)
            except Exception as exc:
                _report_worker_error("PROCESS_ASYNC_JOBS", exc)
                break
            processed += claimed
            if not claimed or time.monotonic() - started >= max_seconds:
                break
    finally:
        stop.set()
    summary = {
        "processed": processed,
        "passes": passes,
        "fired": fired,
        "seconds": round(time.monotonic() - started, 1),
    }
    print(f"[WORKER RUN] {json.dumps(summary, separators=(',', ':'))}", flush=True)
    return summary


def _once_requested(argv: list[str] | None = None) -> bool:
    argv = sys.argv[1:] if argv is None else argv
    return "--once" in argv or (os.getenv("WORKER_MODE") or "").strip().lower() == "once"


def register_jobs(scheduler) -> None:
    """Put every job on a scheduler; shared by the standalone and embedded runtimes.

    The time-of-day jobs go through run_due_daily_jobs and its database
    slots rather than cron triggers, in every runtime: a process restarted
    at 00:03 still runs the midnight refresh, and two processes that overlap
    for a minute during a deploy cannot both send a streak reminder.
    """
    scheduler.add_job(process_async_jobs, "interval", seconds=5, id="process_async_jobs", max_instances=1)
    scheduler.add_job(heartbeat, "interval", minutes=1, id="worker_heartbeat")
    scheduler.add_job(recover_worker_state, "interval", minutes=1, id="recover_worker_state")
    scheduler.add_job(cancel_retired_product_subscriptions, "interval", minutes=1,
                      id="retired_billing_cancellations")
    scheduler.add_job(expire_billing_grace_periods, "interval", hours=1,
                      id="expire_billing_grace_periods")
    scheduler.add_job(process_leaderboard_payouts, "interval", minutes=5,
                      id="leaderboard_payouts", max_instances=1, coalesce=True)
    scheduler.add_job(run_due_daily_jobs, "interval", minutes=1, id="daily_schedule", max_instances=1)


def _startup_pass() -> None:
    """What a freshly started worker does before its first scheduled tick."""
    for job_id, func in INTERVAL_JOBS:
        try:
            func()
        except Exception as exc:
            _report_worker_error(job_id.upper(), exc)
    try:
        process_async_jobs()
    except Exception as exc:
        _report_worker_error("PROCESS_ASYNC_JOBS", exc)


EMBEDDED_WORKER_THREADS = 4


def embedded_worker_enabled() -> bool:
    return (os.getenv("EMBEDDED_WORKER") or "").strip().lower() in {"1", "true", "yes", "on"}


def start_embedded():
    """Start the scheduler inside this process and return it.

    Called from gunicorn's post_fork hook, so it must return quickly: the
    startup pass is queued on the scheduler's own pool rather than run here,
    and the worker starts answering requests while it runs. Four threads
    bound how many database connections the jobs can hold at once next to
    the request threads (DB_POOL_MAX is sized for both).
    """
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(
        timezone=SCHEDULER_TIMEZONE,
        executors={"default": ThreadPoolExecutor(EMBEDDED_WORKER_THREADS)},
        # A tick that slipped while the process was busy runs late rather
        # than being skipped, and never more than once.
        job_defaults={"coalesce": True, "misfire_grace_time": 60},
    )
    register_jobs(scheduler)
    scheduler.add_job(_startup_pass, id="startup_pass")
    scheduler.start()
    print(f"[EMBEDDED WORKER] started in pid {os.getpid()}", flush=True)
    return scheduler


def stop_embedded(scheduler, wait_seconds: float = 60.0) -> bool:
    """Stop the scheduler, giving a job in flight up to ``wait_seconds``.

    gunicorn recycles its worker every few thousand requests; without this a
    quiz being generated at that moment would sit as "running" until the
    stale-job recovery an hour later. AI calls are bounded to 40 seconds,
    so a minute lets the job finish and record its result. Returns True
    when everything stopped in time.
    """
    done = threading.Event()

    def _shutdown():
        try:
            scheduler.shutdown(wait=True)
        finally:
            done.set()

    threading.Thread(target=_shutdown, name="embedded-worker-shutdown", daemon=True).start()
    finished = done.wait(wait_seconds)
    print(f"[EMBEDDED WORKER] stopped ({'clean' if finished else 'job still running'})", flush=True)
    return finished


def main(argv: list[str] | None = None) -> int:
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

    if _once_requested(argv):
        run_once()
        return 0

    print("MachReach Uni worker started.")

    scheduler = BlockingScheduler(timezone=SCHEDULER_TIMEZONE)
    register_jobs(scheduler)
    _startup_pass()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Worker stopped.")
    return 0


if __name__ == "__main__":
    main()
