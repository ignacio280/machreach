"""The operator's way in when the verification email cannot arrive."""
import pytest

from machreach_core.db import (
    claim_async_jobs,
    enqueue_async_job,
    fail_async_job,
    get_async_job_status,
    get_client,
)
from scripts.provision_account import main


def test_verify_flips_only_the_flag_and_settles_the_stuck_job(make_user, capsys):
    """The real case: registered during the mail outage, the verification email
    burned its retries, and login refuses the account. --verify lets them in
    with the password they chose, and the dead job stops alerting with it."""
    client_id = make_user("Stuck Student", "stuck@estudiante.test")
    before = get_client(client_id)
    enqueue_async_job("verification_email", str(client_id), max_attempts=1)
    claim_async_jobs("verification_email", limit=10)
    fail_async_job("verification_email", str(client_id), "smtp down")

    assert main(["stuck@estudiante.test", "--verify"]) == 0

    after = get_client(client_id)
    assert after["email_verified"]
    assert after["password"] == before["password"]
    assert get_async_job_status("verification_email", str(client_id))["status"] == "done"
    assert "password is untouched" in capsys.readouterr().out


def test_verify_is_idempotent_and_loud_about_missing_accounts(make_user, capsys):
    client_id = make_user("Twice Verified", "twice@estudiante.test")

    assert main(["twice@estudiante.test", "--verify"]) == 0
    assert main(["twice@estudiante.test", "--verify"]) == 0
    assert "already verified" in capsys.readouterr().out
    assert get_client(client_id)["email_verified"]

    assert main(["nobody@estudiante.test", "--verify"]) == 1
    assert "No account" in capsys.readouterr().out


def test_verify_refuses_to_be_combined_with_password_changes():
    with pytest.raises(SystemExit):
        main(["someone@estudiante.test", "--verify", "--reset-password"])
    with pytest.raises(SystemExit):
        main(["someone@estudiante.test", "--verify", "--password-env", "X"])


def test_creating_still_requires_a_name():
    with pytest.raises(SystemExit):
        main(["new@estudiante.test"])
