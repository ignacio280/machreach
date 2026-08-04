"""The admin-granting script is the only path to administrator access."""
from machreach_core.db import _fetchone, get_db
from scripts.grant_admin import main


def _is_admin(client_id: int) -> bool:
    with get_db() as db:
        return bool(_fetchone(db, "SELECT is_admin FROM clients WHERE id = %s", (client_id,))["is_admin"])


def test_granting_and_revoking_by_email(make_user, capsys):
    client_id = make_user("Owner", "owner@grant.test")

    assert main(["owner@grant.test"]) == 0
    assert _is_admin(client_id) is True

    assert main(["owner@grant.test", "--revoke"]) == 0
    assert _is_admin(client_id) is False


def test_the_email_match_ignores_case(make_user):
    client_id = make_user("Mixed Case", "Mixed.Case@grant.test")

    assert main(["mixed.case@GRANT.test"]) == 0
    assert _is_admin(client_id) is True


def test_an_unknown_email_fails_loudly(capsys):
    assert main(["nobody@grant.test"]) == 1
    assert "No account" in capsys.readouterr().out


def test_granting_twice_is_a_no_op(make_user, capsys):
    make_user("Twice", "twice@grant.test")
    main(["twice@grant.test"])
    capsys.readouterr()

    assert main(["twice@grant.test"]) == 0
    assert "already an administrator" in capsys.readouterr().out


def test_listing_shows_current_admins(make_user, capsys):
    make_user("Listed", "listed@grant.test")
    main(["listed@grant.test"])
    capsys.readouterr()

    assert main(["--list"]) == 0
    assert "listed@grant.test" in capsys.readouterr().out
