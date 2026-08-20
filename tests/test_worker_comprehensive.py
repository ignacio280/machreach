import worker
import runpy
from machreach_core import lemonsqueezy
from machreach_core.db import claim_async_jobs, enqueue_async_job, get_async_job_status
from student import ai_usage, analyzer, db as sdb, subscription
from pdf_fixtures import STUDY_MATERIAL


def _claim_job_for(job_type, client_id):
    """Claim queued jobs until this student's own job comes up.

    The worker claims the oldest queued job of a type, and earlier tests leave
    queued rows of the same type behind, so a single claim is not ours.
    """
    while True:
        claimed = claim_async_jobs(job_type, limit=10)
        if not claimed:
            raise AssertionError(f"no queued {job_type} job for client {client_id}")
        for job in claimed:
            if job["job_key"] == str(client_id):
                return job


def test_leaderboard_worker_runs_all_recovery_stages_and_contains_failures(
    monkeypatch,
):
    from student import leaderboard_prizes

    calls = []
    monkeypatch.setattr(
        leaderboard_prizes, "run_due_payouts", lambda: calls.append("payouts")
    )
    monkeypatch.setattr(
        leaderboard_prizes,
        "dispatch_payout_notifications",
        lambda: calls.append("notifications"),
    )
    monkeypatch.setattr(
        leaderboard_prizes,
        "cleanup_payout_outbox",
        lambda: calls.append("cleanup"),
    )
    worker.process_leaderboard_payouts()
    assert calls == ["payouts", "notifications", "cleanup"]

    reported = []
    monkeypatch.setattr(
        leaderboard_prizes,
        "run_due_payouts",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        worker,
        "_report_worker_error",
        lambda context, exc: reported.append((context, type(exc).__name__)),
    )
    worker.process_leaderboard_payouts()
    assert reported == [("LEADERBOARD_PAYOUTS", "RuntimeError")]


def test_ad_hoc_quiz_job_succeeds_and_persists_questions(monkeypatch, make_user):
    client_id = make_user("Quiz Worker")
    enqueue_async_job("student_quiz_generation", str(client_id), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(subscription, "record_generation", lambda *args: None)
    monkeypatch.setattr(analyzer, "generate_quiz", lambda **kwargs: [{
        "topic": "Limits", "question": "Q?", "option_a": "A", "option_b": "B",
        "option_c": "C", "option_d": "D", "correct": "a", "explanation": "Because",
    }])
    job = {"job_key": str(client_id), "input": {
        "source_text": STUDY_MATERIAL, "title": "Custom Quiz", "difficulty": "impossible", "count": "bad",
    }}

    worker._process_student_quiz_job(job)

    status = get_async_job_status("student_quiz_generation", str(client_id))
    assert status["status"] == "done"
    assert status["question_count"] == 1
    assert sdb.get_quiz(status["quiz_id"], client_id)["title"] == "Custom Quiz"


def test_quiz_job_nonretryable_validation_failure(monkeypatch, make_user):
    client_id = make_user("Quiz Failure")
    enqueue_async_job("student_quiz_generation", str(client_id), input_payload={}, max_attempts=3)
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (False, "Daily limit reached"))

    worker._process_student_quiz_job({"job_key": str(client_id), "input": {}})

    status = get_async_job_status("student_quiz_generation", str(client_id))
    assert status["status"] == "error"
    assert "Daily limit" in status["error"]


def test_completed_ai_reservations_restore_worker_results_without_regeneration(
    monkeypatch, make_user
):
    quiz_client = make_user("Settled Quiz Worker")
    cards_client = make_user("Settled Cards Worker")
    enqueue_async_job("student_quiz_generation", str(quiz_client), input_payload={})
    enqueue_async_job("student_flashcard_generation", str(cards_client), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(
        subscription, "can_generate_flashcards_today", lambda cid: (True, "")
    )
    monkeypatch.setattr(
        ai_usage,
        "reserve",
        lambda *args, **kwargs: {
            "status": "settled",
            "result_json": (
                '{"quiz_id": 71, "question_count": 4}'
                if kwargs["feature"] == "quiz_worker"
                else '{"deck_id": 81, "card_count": 6}'
            ),
        },
    )
    monkeypatch.setattr(
        analyzer,
        "generate_quiz",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not regenerate")),
    )
    monkeypatch.setattr(
        analyzer,
        "generate_flashcards",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not regenerate")),
    )

    worker._process_student_quiz_job({"job_key": str(quiz_client), "run_id": "run-901"})
    worker._process_student_flashcard_job({"job_key": str(cards_client), "run_id": "run-902"})

    assert get_async_job_status(
        "student_quiz_generation", str(quiz_client)
    )["quiz_id"] == 71
    assert get_async_job_status(
        "student_flashcard_generation", str(cards_client)
    )["deck_id"] == 81


def test_ad_hoc_flashcard_job_succeeds_and_failure_is_recorded(monkeypatch, make_user):
    client_id = make_user("Cards Worker")
    enqueue_async_job("student_flashcard_generation", str(client_id), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_flashcards_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_cards", lambda cid, count: count)
    monkeypatch.setattr(subscription, "record_generation", lambda *args: None)
    monkeypatch.setattr(analyzer, "generate_flashcards", lambda **kwargs: [{"front": "Q", "back": "A"}])

    worker._process_student_flashcard_job({"job_key": str(client_id), "input": {
        "source_text": STUDY_MATERIAL, "title": "Custom Cards", "count": "bad",
    }})
    status = get_async_job_status("student_flashcard_generation", str(client_id))
    assert status["status"] == "done"
    assert status["card_count"] == 1

    failing_id = make_user("Cards Failure")
    enqueue_async_job("student_flashcard_generation", str(failing_id), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_flashcards_today", lambda cid: (False, "Upgrade required"))
    worker._process_student_flashcard_job({"job_key": str(failing_id), "input": {}})
    assert get_async_job_status("student_flashcard_generation", str(failing_id))["status"] == "error"


def test_ai_worker_generation_failures_remain_retryable(monkeypatch, make_user):
    quiz_client = make_user("Empty Quiz Provider")
    cards_client = make_user("Empty Cards Provider")
    enqueue_async_job("student_quiz_generation", str(quiz_client), input_payload={})
    enqueue_async_job("student_flashcard_generation", str(cards_client), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(
        subscription, "can_generate_flashcards_today", lambda cid: (True, "")
    )
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(subscription, "cap_cards", lambda cid, count: count)
    monkeypatch.setattr(analyzer, "generate_quiz", lambda **kwargs: [])
    monkeypatch.setattr(analyzer, "generate_flashcards", lambda **kwargs: [])

    worker._process_student_quiz_job({
        "job_key": str(quiz_client),
        "input": {"source_text": STUDY_MATERIAL},
    })
    worker._process_student_flashcard_job({
        "job_key": str(cards_client),
        "input": {"source_text": STUDY_MATERIAL},
    })

    assert get_async_job_status(
        "student_quiz_generation", str(quiz_client)
    )["status"] == "queued"
    assert get_async_job_status(
        "student_flashcard_generation", str(cards_client)
    )["status"] == "queued"


def test_ai_worker_rejects_missing_or_cross_tenant_course_sources(
    monkeypatch, make_user
):
    quiz_client = make_user("Missing Quiz Course")
    cards_client = make_user("Missing Cards Course")
    enqueue_async_job("student_quiz_generation", str(quiz_client), input_payload={})
    enqueue_async_job("student_flashcard_generation", str(cards_client), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(
        subscription, "can_generate_flashcards_today", lambda cid: (True, "")
    )

    worker._process_student_quiz_job({
        "job_key": str(quiz_client),
        "input": {"course_id": 999999},
    })
    worker._process_student_flashcard_job({
        "job_key": str(cards_client),
        "input": {},
    })

    quiz_status = get_async_job_status(
        "student_quiz_generation", str(quiz_client)
    )
    cards_status = get_async_job_status(
        "student_flashcard_generation", str(cards_client)
    )
    assert quiz_status["status"] == "error"
    assert "Course not found" in quiz_status["error"]
    assert cards_status["status"] == "error"
    assert "course_id required" in cards_status["error"]


def test_process_loop_claims_both_job_types_and_heartbeat(monkeypatch):
    claimed = {
        "student_quiz_generation": [{"job_key": "1"}],
        "student_flashcard_generation": [{"job_key": "2"}],
    }
    heartbeats = []
    processed = []
    monkeypatch.setattr(worker, "record_worker_heartbeat", lambda: heartbeats.append(True))
    monkeypatch.setattr(worker, "claim_async_jobs", lambda kind, **kwargs: claimed.get(kind, []))
    monkeypatch.setattr(worker, "_process_student_quiz_job", lambda job: processed.append(("quiz", job)))
    monkeypatch.setattr(worker, "_process_student_flashcard_job", lambda job: processed.append(("cards", job)))

    worker.process_async_jobs()
    worker.heartbeat()

    assert len(heartbeats) == 2
    assert [kind for kind, _ in processed] == ["quiz", "cards"]


def test_process_loop_delivers_claimed_verification_jobs(monkeypatch):
    import machreach_core.verification_delivery as delivery

    processed = []
    monkeypatch.setattr(worker, "record_worker_heartbeat", lambda: None)
    monkeypatch.setattr(
        worker,
        "claim_async_jobs",
        lambda kind, **kwargs: (
            [{"id": 31, "job_key": "verify"}] if kind == "verification_email" else []
        ),
    )
    monkeypatch.setattr(
        delivery,
        "process_job",
        lambda job, sender: processed.append((job["id"], callable(sender))),
    )

    worker.process_async_jobs()

    assert processed == [(31, True)]


def test_heartbeat_recovers_stale_ai_usage_and_reports_failure(monkeypatch):
    monkeypatch.setattr(worker, "record_worker_heartbeat", lambda: None)
    monkeypatch.setattr(ai_usage, "reconcile_stale_reservations", lambda: 2)
    worker.heartbeat()

    reported = []
    monkeypatch.setattr(
        ai_usage,
        "reconcile_stale_reservations",
        lambda: (_ for _ in ()).throw(RuntimeError("reconciliation failed")),
    )
    monkeypatch.setattr(
        worker,
        "_report_worker_error",
        lambda context, exc: reported.append(context),
    )
    worker.heartbeat()
    assert reported == ["AI_USAGE_RECOVERY"]


def test_simple_maintenance_jobs_invoke_domain_services(monkeypatch):
    import machreach_core.verification_delivery as delivery

    calls = []
    monkeypatch.setattr(
        delivery,
        "delete_stale_unverified",
        lambda days: calls.append(("verification", days)) or 2,
    )
    monkeypatch.setattr(
        subscription,
        "expire_payment_grace_periods",
        lambda: calls.append(("billing",)) or 1,
    )
    monkeypatch.setattr(
        sdb,
        "purge_expired_course_deletions",
        lambda: calls.append(("courses",)) or 3,
    )

    worker.clean_abandoned_unverified_accounts()
    worker.expire_billing_grace_periods()
    worker.purge_deleted_courses()

    assert calls == [("verification", 7), ("billing",), ("courses",)]


def test_retired_subscription_cancellation_success_failure_and_exception(monkeypatch):
    rows = [{"subscription_id": "ok"}, {"subscription_id": "no"}, {"subscription_id": "boom"}]
    finished = []
    import machreach_core.db as dbmod
    monkeypatch.setattr(dbmod, "list_retired_billing_cancellations", lambda limit=25: rows)
    monkeypatch.setattr(dbmod, "finish_retired_billing_cancellation", lambda sid, error="": finished.append((sid, error)))
    def cancel(subscription_id):
        if subscription_id == "boom":
            raise RuntimeError("provider")
        return subscription_id == "ok"
    monkeypatch.setattr(lemonsqueezy, "cancel_subscription", cancel)
    monkeypatch.setattr(worker, "_report_worker_error", lambda *args: None)

    worker.cancel_retired_product_subscriptions()

    assert ("ok", "") in finished
    assert ("no", "provider cancellation failed") in finished
    assert ("boom", "RuntimeError") in finished


def test_recover_worker_state_reports_recovered_jobs(monkeypatch):
    import machreach_core.db as dbmod
    monkeypatch.setattr(dbmod, "recover_stale_async_jobs", lambda: 2)
    worker.recover_worker_state()


def test_refresh_student_plans_rolls_incomplete_work_forward(monkeypatch):
    saved = []
    monkeypatch.setattr(subscription, "has_plus_access", lambda _cid: True)
    monkeypatch.setattr(
        ai_usage,
        "reserve",
        lambda *_args, **_kwargs: {"status": "reserved"},
    )
    monkeypatch.setattr(ai_usage, "settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sdb, "get_all_student_client_ids", lambda: [41])
    monkeypatch.setattr(
        sdb,
        "get_incomplete_assignments",
        lambda client_id, today: [{"title": "Read chapter"}],
    )
    monkeypatch.setattr(
        sdb,
        "get_courses",
        lambda client_id: [{
            "analysis_json": {"course_name": "Biology"},
            "difficulty": 4,
        }],
    )
    monkeypatch.setattr(
        sdb,
        "get_schedule_settings",
        lambda client_id: [{"day_of_week": 1, "available_hours": 2, "is_free_day": 0}],
    )
    monkeypatch.setattr(
        sdb,
        "get_latest_plan",
        lambda client_id: {"preferences_json": {"pace": "steady"}},
    )
    monkeypatch.setattr(
        sdb,
        "save_study_plan",
        lambda client_id, plan, preferences: saved.append((client_id, plan, preferences)),
    )
    monkeypatch.setattr(
        analyzer,
        "generate_study_plan",
        lambda *args, **kwargs: {"daily_plan": [{"day": "Monday"}]},
    )

    worker.refresh_student_plans()

    assert saved[0][0] == 41
    assert saved[0][2] == {"pace": "steady"}


def test_refresh_student_plans_skips_ineligible_and_recovers_per_account(
    monkeypatch,
):
    reported = []
    failed = []
    monkeypatch.setattr(sdb, "get_all_student_client_ids", lambda: [1, 2, 3, 4])
    monkeypatch.setattr(subscription, "has_plus_access", lambda cid: cid != 1)
    monkeypatch.setattr(sdb, "get_incomplete_assignments", lambda cid, today: [])
    monkeypatch.setattr(
        sdb,
        "get_courses",
        lambda cid: (
            []
            if cid == 2
            else [{"analysis_json": {"course_name": "Biology"}, "difficulty": 3}]
        ),
    )
    monkeypatch.setattr(sdb, "get_schedule_settings", lambda cid: [])
    monkeypatch.setattr(sdb, "get_latest_plan", lambda cid: None)
    monkeypatch.setattr(
        ai_usage,
        "reserve",
        lambda cid, **kwargs: (
            {"status": "settled"} if cid == 3 else {"status": "reserved"}
        ),
    )
    monkeypatch.setattr(
        ai_usage, "fail", lambda key, error: failed.append((key, error))
    )
    monkeypatch.setattr(
        analyzer,
        "generate_study_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(
        worker,
        "_report_worker_error",
        lambda context, exc: reported.append(context),
    )

    worker.refresh_student_plans()

    assert any(key.startswith("daily-plan:4:") for key, _ in failed)
    assert reported == ["STUDENT_PLAN_ACCOUNT"]


class _FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def starttls(self):
        return None

    def login(self, user, password):
        assert user and password

    def send_message(self, message):
        self.sent.append(message)


def _configure_smtp(monkeypatch):
    import machreach_core.config as config
    import smtplib

    _FakeSMTP.sent = []
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "sender@example.test")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "smtp-password")
    monkeypatch.setattr(config, "BASE_URL", "https://machreach.com")
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)


def test_streak_risk_email_is_delivered(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(
        sdb,
        "get_streak_risk_recipients",
        lambda min_streak: [{
            "streak": 7,
            "name": "Focused Student",
            "email": "student@example.test",
        }],
    )

    worker.send_streak_risk_pushes()

    assert len(_FakeSMTP.sent) == 1
    assert _FakeSMTP.sent[0]["To"] == "student@example.test"


def test_monthly_leaderboard_report_is_delivered(monkeypatch):
    _configure_smtp(monkeypatch)
    from student import academic

    monkeypatch.setattr(worker, "LEADERBOARD_WINNERS_RECIPIENT", "owner@example.test")
    monkeypatch.setattr(
        academic,
        "monthly_winners",
        lambda year, month, top_n: {
            "label": "June 2026",
            "start": "2026-06-01",
            "end_exclusive": "2026-07-01",
            "summary": {
                "total_xp_awarded": 250,
                "active_students": 1,
                "total_students": 1,
                "new_students": 1,
            },
            "by_country": [{
                "label": "Chile",
                "rows": [{"rank": 1, "name": "Winner", "xp": 250, "client_id": 9}],
            }],
            "by_university": [],
            "by_major": [],
        },
    )

    worker.send_monthly_leaderboard_email(2026, 6)

    assert len(_FakeSMTP.sent) == 1
    assert _FakeSMTP.sent[0]["To"] == "owner@example.test"
    assert "junio 2026" in _FakeSMTP.sent[0]["Subject"]


def test_ssl_email_paths_and_empty_monthly_report_are_delivered(monkeypatch):
    _configure_smtp(monkeypatch)
    import machreach_core.config as config
    from student import academic

    monkeypatch.setattr(config, "SMTP_PORT", 465)
    monkeypatch.setattr(worker, "LEADERBOARD_WINNERS_RECIPIENT", "owner@example.test")
    monkeypatch.setattr(
        sdb,
        "get_streak_risk_recipients",
        lambda min_streak: [{
            "streak": 5,
            "name": "",
            "email": "risk@example.test",
        }],
    )
    monkeypatch.setattr(
        academic,
        "monthly_winners",
        lambda year, month, top_n: {
            "label": f"{year}-{month:02d}",
            "start": "2026-06-01",
            "end_exclusive": "2026-07-01",
            "summary": {},
            "by_country": [],
            "by_university": [],
            "by_major": [],
        },
    )

    worker.send_streak_risk_pushes()
    worker.send_monthly_leaderboard_email()

    assert {message["To"] for message in _FakeSMTP.sent} == {
        "risk@example.test",
        "owner@example.test",
    }


def test_email_delivery_failures_are_observable_and_contained(monkeypatch):
    _configure_smtp(monkeypatch)
    import machreach_core.db as core_db
    from student import academic
    import smtplib

    class FailingSMTP(_FakeSMTP):
        def send_message(self, message):
            raise RuntimeError("SMTP unavailable")

    events = []
    reports = []
    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)
    monkeypatch.setattr(
        core_db,
        "record_operational_event",
        lambda kind, detail: events.append((kind, detail)),
    )
    monkeypatch.setattr(
        worker,
        "_report_worker_error",
        lambda context, exc: reports.append(context),
    )
    monkeypatch.setattr(
        sdb,
        "get_streak_risk_recipients",
        lambda min_streak: [{
            "streak": 8,
            "name": "At Risk",
            "email": "risk@example.test",
        }],
    )
    monkeypatch.setattr(worker, "LEADERBOARD_WINNERS_RECIPIENT", "owner@example.test")
    monkeypatch.setattr(
        academic,
        "monthly_winners",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("report failed")),
    )

    worker.send_streak_risk_pushes()
    worker.send_monthly_leaderboard_email(2026, 6)

    assert ("smtp_failure", "streak_risk") in events
    assert ("smtp_failure", "leaderboard") in events
    assert reports == ["STREAK_RISK_ACCOUNT", "MONTHLY_WINNERS"]


def test_email_jobs_skip_cleanly_without_configuration(monkeypatch):
    import machreach_core.config as config

    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    monkeypatch.setattr(worker, "LEADERBOARD_WINNERS_RECIPIENT", "")

    worker.send_streak_risk_pushes()
    worker.send_monthly_leaderboard_email(2026, 6)


def test_worker_entrypoint_registers_recoverable_schedule(monkeypatch):
    import apscheduler.schedulers.blocking as blocking
    import machreach_core.db as core_db

    registered = []

    class FakeScheduler:
        def __init__(self, timezone):
            assert timezone == "America/Santiago"

        def add_job(self, function, trigger, **kwargs):
            registered.append((function.__name__, trigger, kwargs["id"]))

        def start(self):
            raise KeyboardInterrupt

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(blocking, "BlockingScheduler", FakeScheduler)
    monkeypatch.setattr(core_db, "init_db", lambda: None)
    monkeypatch.setattr(core_db, "check_schema_readiness", lambda: None)
    monkeypatch.setattr(core_db, "record_worker_heartbeat", lambda: None)
    monkeypatch.setattr(core_db, "claim_async_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        core_db, "list_retired_billing_cancellations", lambda limit=25: []
    )
    monkeypatch.setattr(core_db, "recover_stale_async_jobs", lambda: 0)
    monkeypatch.setattr(
        worker, "process_leaderboard_payouts", lambda: None
    )
    monkeypatch.setattr(sdb, "init_student_db", lambda: None)

    runpy.run_module("worker", run_name="__main__")

    job_ids = {job_id for _, _, job_id in registered}
    assert {
        "process_async_jobs",
        "worker_heartbeat",
        "recover_worker_state",
        "leaderboard_payouts",
        "streak_risk_push",
    } <= job_ids


def test_refresh_and_recovery_failures_are_contained(monkeypatch):
    reported = []
    monkeypatch.setattr(worker, "_report_worker_error", lambda context, exc: reported.append(context))
    monkeypatch.setattr(
        sdb,
        "get_all_student_client_ids",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    worker.refresh_student_plans()

    import machreach_core.db as dbmod

    monkeypatch.setattr(
        dbmod,
        "recover_stale_async_jobs",
        lambda: (_ for _ in ()).throw(RuntimeError("recovery failed")),
    )
    worker.recover_worker_state()

    assert "STUDENT_PLAN_REFRESH" in reported


def test_course_material_quiz_and_flashcard_jobs_use_uploaded_sources(monkeypatch, make_user):
    client_id = make_user("Course Source Worker")
    course_id = sdb.create_manual_course(client_id, "Organic Chemistry", "CHEM-310")
    sdb.add_course_file(
        client_id,
        course_id,
        "reactions.txt",
        "txt",
        "Substitution reactions replace one functional group with another. " + STUDY_MATERIAL,
    )
    enqueue_async_job("student_quiz_generation", str(client_id), input_payload={})
    enqueue_async_job("student_flashcard_generation", str(client_id), input_payload={})
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "can_generate_flashcards_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(subscription, "cap_cards", lambda cid, count: count)
    monkeypatch.setattr(subscription, "record_generation", lambda *args: None)
    monkeypatch.setattr(analyzer, "generate_quiz", lambda **kwargs: [{
        "topic": "Reactions",
        "question": "What happens in substitution?",
        "option_a": "A group is replaced",
        "option_b": "Nothing",
        "option_c": "Only heat changes",
        "option_d": "Mass disappears",
        "correct": "a",
        "explanation": "A functional group is replaced.",
    }])
    monkeypatch.setattr(
        analyzer,
        "generate_flashcards",
        lambda **kwargs: [{"front": "Substitution", "back": "Replacement reaction"}],
    )

    worker._process_student_quiz_job({
        "job_key": str(client_id),
        "input": {"course_id": course_id, "count": 5},
    })
    worker._process_student_flashcard_job({
        "job_key": str(client_id),
        "input": {"course_id": course_id, "count": 5},
    })

    assert get_async_job_status("student_quiz_generation", str(client_id))["status"] == "done"
    assert get_async_job_status("student_flashcard_generation", str(client_id))["status"] == "done"


def test_january_reports_on_the_previous_december(monkeypatch):
    """Called with no month, the report covers the month that just ended.

    In January that means stepping back across the year boundary. Getting it
    wrong sends the report for a month that has not happened yet, and only
    ever on one day of the year — so it is worth pinning rather than trusting.
    """
    import datetime as datetime_module
    from student import academic

    _configure_smtp(monkeypatch)
    monkeypatch.setattr(worker, "LEADERBOARD_WINNERS_RECIPIENT", "owner@example.test")

    class _JanuaryDate(datetime_module.date):
        @classmethod
        def today(cls):
            return cls(2027, 1, 9)

    # The function imports `date` from the module when it runs, so replacing it
    # here is what the call actually picks up.
    monkeypatch.setattr(datetime_module, "date", _JanuaryDate)

    asked = {}
    monkeypatch.setattr(
        academic,
        "monthly_winners",
        lambda year, month, top_n: asked.update(year=year, month=month) or {
            "label": f"{year}-{month:02d}",
            "start": f"{year}-{month:02d}-01",
            "end_exclusive": f"{year + 1}-01-01",
            "summary": {},
            "by_country": [],
            "by_university": [],
            "by_major": [],
        },
    )

    worker.send_monthly_leaderboard_email()

    assert asked == {"year": 2026, "month": 12}
    assert "diciembre 2026" in _FakeSMTP.sent[0]["Subject"]


def test_a_second_quiz_job_generates_a_new_quiz_instead_of_replaying_the_first(
    monkeypatch, make_user
):
    """Each queued generation is its own AI reservation.

    Reusing one reservation key per student made every quiz after the first
    replay the first one's result: the job flipped to "done" in seconds and no
    new quiz was ever written.
    """
    client_id = make_user("Repeat Quiz Worker")
    monkeypatch.setattr(subscription, "FREE_DAILY_QUIZZES", 5)
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(analyzer, "generate_quiz", lambda **kwargs: [{
        "topic": "Limits", "question": "Q?", "option_a": "A", "option_b": "B",
        "option_c": "C", "option_d": "D", "correct": "a", "explanation": "Because",
    }])

    quiz_ids = []
    for title in ("First PDF", "Second PDF"):
        enqueue_async_job(
            "student_quiz_generation",
            str(client_id),
            input_payload={"source_text": f"{title}: {STUDY_MATERIAL}", "title": title, "count": 1},
        )
        job = _claim_job_for("student_quiz_generation", client_id)
        worker._process_student_quiz_job(job)
        status = get_async_job_status("student_quiz_generation", str(client_id))
        assert status["status"] == "done", status
        quiz_ids.append(status["quiz_id"])

    assert quiz_ids[0] != quiz_ids[1]
    assert sdb.get_quiz(quiz_ids[1], client_id)["title"] == "Second PDF"


def test_a_second_flashcard_job_builds_a_new_deck_instead_of_replaying_the_first(
    monkeypatch, make_user
):
    client_id = make_user("Repeat Cards Worker")
    monkeypatch.setattr(subscription, "FREE_DAILY_FLASHCARD_SETS", 5)
    monkeypatch.setattr(subscription, "can_generate_flashcards_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_cards", lambda cid, count: count)
    monkeypatch.setattr(analyzer, "generate_flashcards", lambda **kwargs: [{"front": "Q", "back": "A"}])

    deck_ids = []
    for title in ("First PDF", "Second PDF"):
        enqueue_async_job(
            "student_flashcard_generation",
            str(client_id),
            input_payload={"source_text": f"{title}: {STUDY_MATERIAL}", "title": title, "count": 1},
        )
        job = _claim_job_for("student_flashcard_generation", client_id)
        worker._process_student_flashcard_job(job)
        status = get_async_job_status("student_flashcard_generation", str(client_id))
        assert status["status"] == "done", status
        deck_ids.append(status["deck_id"])

    assert deck_ids[0] != deck_ids[1]



def test_quiz_job_refuses_material_that_is_only_extraction_scaffolding(
    monkeypatch, make_user
):
    """Course files saved before extraction rejected scans still hold markers.

    The page markers are ours, so this reads as a three-page document that
    says nothing. Generating from it produced a confident quiz about material
    the student never uploaded, which looks like the product working.
    """
    client_id = make_user("Scanned Material Worker")
    course_id = sdb.create_manual_course(client_id, "Historia", "HIS-101")
    sdb.add_course_file(
        client_id,
        course_id,
        "escaneo.pdf",
        "pdf",
        '[PDF DOCUMENT — TOTAL PAGES: 3]\n\n--- PAGE 1 of 3 ---\n\n--- PAGE 2 of 3 ---\n\n--- PAGE 3 of 3 ---',
    )
    monkeypatch.setattr(subscription, "can_generate_quiz_today", lambda cid: (True, ""))
    monkeypatch.setattr(subscription, "cap_questions", lambda cid, count: count)
    monkeypatch.setattr(
        analyzer,
        "generate_quiz",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not generate from an empty document")
        ),
    )

    worker._process_student_quiz_job(
        {"job_key": str(client_id), "input": {"course_id": course_id}}
    )

    status = get_async_job_status("student_quiz_generation", str(client_id))
    assert status["status"] == "error"
    assert "scanned PDF" in status["error"]
