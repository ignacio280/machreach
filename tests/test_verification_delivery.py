from machreach_core import verification_delivery as delivery
from machreach_core.db import _exec, _fetchval, get_db


def test_delivery_creates_single_use_token_and_handles_sender_failure(make_user):
    client_id = make_user("Verify Delivery")
    sent = []

    assert delivery._deliver_once(
        client_id,
        lambda to, subject, body: sent.append((to, subject, body)) or True,
    )
    assert sent and "/verify-email/" in sent[0][2]
    with get_db() as db:
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM email_verification_tokens WHERE client_id = %s",
            (client_id,),
        ) == 1

    assert delivery._deliver_once(
        client_id,
        lambda *_args: (_ for _ in ()).throw(RuntimeError("smtp down")),
    ) is False
    assert delivery._deliver_once(99999999, lambda *_args: False) is True


def test_queueing_never_touches_smtp_and_worker_reports_terminal_failure(monkeypatch):
    queued = []
    monkeypatch.setattr(
        delivery,
        "enqueue_async_job",
        lambda *args, **kwargs: queued.append((args, kwargs)) or {"status": "queued"},
    )
    # No sender argument exists any more: a request cannot send even by mistake.
    assert delivery.queue_verification_email(7) == {"status": "queued"}
    assert delivery.queue_password_reset(7) == {"status": "queued"}
    assert queued[0][0][0] == delivery.JOB_TYPE
    assert queued[1][0][0] == delivery.RESET_JOB_TYPE
    assert all(call[1]["max_attempts"] == delivery.MAX_ATTEMPTS for call in queued)

    monkeypatch.setattr(
        delivery, "get_client", lambda _cid: {"email_verified": False}
    )
    monkeypatch.setattr(delivery, "_deliver_once", lambda *_args: False)
    monkeypatch.setattr(
        delivery, "fail_async_job", lambda *_args, **_kwargs: {"status": "error"}
    )
    events = []
    monkeypatch.setattr(
        delivery,
        "record_operational_event",
        lambda *args: events.append(args),
    )
    assert delivery.process_job({"job_key": "8"}, lambda *_args: False) == {
        "status": "error"
    }
    assert events == [("smtp_failure", "verification")]
    monkeypatch.setattr(delivery, "_deliver_once", lambda *_args: True)

    monkeypatch.setattr(delivery, "get_client", lambda _cid: None)
    assert delivery.process_job({"job_key": "8"}, lambda *_args: False) == {
        "status": "done"
    }


def test_password_reset_job_mints_the_token_at_send_time(make_user):
    client_id = make_user("Reset Recipient")
    sent = []

    result = delivery.process_reset_job(
        {"job_key": str(client_id)},
        lambda to, subject, body: sent.append((to, subject, body)) or True,
    )

    assert result == {"status": "done"}
    assert "/reset-password/" in sent[0][2]
    with get_db() as db:
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM password_reset_tokens WHERE client_id = %s",
            (client_id,),
        ) == 1

    # A deleted account settles as done rather than retrying forever.
    assert delivery.process_reset_job({"job_key": "99887766"}, lambda *_a: False) == {
        "status": "done"
    }
    # A sender that raises is a failed attempt, not a crash.
    assert delivery._deliver_reset_once(
        client_id, lambda *_a: (_ for _ in ()).throw(RuntimeError("smtp down"))
    ) is False


def test_reset_job_terminal_failure_records_the_operational_event(monkeypatch, make_user):
    client_id = make_user("Reset Failing")
    monkeypatch.setattr(
        delivery, "fail_async_job", lambda *_args, **_kwargs: {"status": "error"}
    )
    events = []
    monkeypatch.setattr(
        delivery, "record_operational_event", lambda *args: events.append(args)
    )

    result = delivery.process_reset_job({"job_key": str(client_id)}, lambda *_a: False)

    assert result == {"status": "error"}
    assert events == [("smtp_failure", "password_reset")]


def test_stale_unverified_cleanup_preserves_verified_accounts(make_user):
    stale_id = make_user("Stale Unverified")
    verified_id = make_user("Old Verified")
    # A failed delivery job must not outlive the account it was for, or it
    # would hold /health/operations degraded with nothing left to act on.
    delivery.queue_verification_email(stale_id)
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET created_at = %s WHERE id IN (%s, %s)",
            ("2000-01-01 00:00:00", stale_id, verified_id),
        )
        _exec(
            db,
            "UPDATE clients SET email_verified = 1 WHERE id = %s",
            (verified_id,),
        )

    assert delivery.delete_stale_unverified(days=0) >= 1
    with get_db() as db:
        assert _fetchval(
            db, "SELECT COUNT(*) FROM clients WHERE id = %s", (stale_id,)
        ) == 0
        assert _fetchval(
            db, "SELECT COUNT(*) FROM clients WHERE id = %s", (verified_id,)
        ) == 1
        assert _fetchval(
            db,
            "SELECT COUNT(*) FROM async_jobs WHERE job_type = %s AND job_key = %s",
            (delivery.JOB_TYPE, str(stale_id)),
        ) == 0
