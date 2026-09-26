from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from scripts import check_uptime
from scripts.check_uptime import (
    OPERATIONS_HEALTH_URL,
    ProbeError,
    http_status_is_probeable,
    operations_are_healthy,
    probe_with_retries,
    public_is_healthy,
    should_page_again,
)


def test_operations_degraded_status_is_available_to_the_validator() -> None:
    assert http_status_is_probeable(OPERATIONS_HEALTH_URL, 503)
    assert not http_status_is_probeable("https://example.test/health", 503)


def test_probe_retries_transport_timeouts_and_recovers() -> None:
    fetch = Mock(
        side_effect=[
            ProbeError("The read operation timed out"),
            ProbeError("The read operation timed out"),
            {"status": "healthy"},
        ]
    )
    sleep = Mock()

    payload = probe_with_retries(
        "public health",
        "https://example.test/health",
        public_is_healthy,
        fetch=fetch,
        sleep=sleep,
    )

    assert payload == {"status": "healthy"}
    assert fetch.call_count == 3
    assert sleep.call_args_list == [call(3), call(7)]


def test_probe_fails_after_all_attempts() -> None:
    fetch = Mock(side_effect=ProbeError("The read operation timed out"))
    sleep = Mock()

    with pytest.raises(ProbeError, match=r"attempt 3/3.*timed out"):
        probe_with_retries(
            "public health",
            "https://example.test/health",
            public_is_healthy,
            fetch=fetch,
            sleep=sleep,
        )

    assert fetch.call_count == 3
    assert sleep.call_count == 2


def test_probe_retries_an_unhealthy_payload() -> None:
    fetch = Mock(
        side_effect=[
            {"status": "degraded"},
            {"status": "healthy"},
        ]
    )

    payload = probe_with_retries(
        "public health",
        "https://example.test/health",
        public_is_healthy,
        fetch=fetch,
        sleep=Mock(),
    )

    assert payload["status"] == "healthy"
    assert fetch.call_count == 2


def test_operational_alert_fails_uptime() -> None:
    payload = {
        "status": "degraded",
        "checks": {
            "worker_heartbeat": {"status": "alert"},
        },
    }

    assert not operations_are_healthy(payload)


def test_a_liveness_probe_that_skipped_the_database_is_still_healthy() -> None:
    """DB_SLEEP_FRIENDLY makes /health report that it did not probe.

    The exact-match validator this replaced rejected that payload, so the
    scheduled monitor failed every five minutes while the site was fine.
    """
    assert public_is_healthy({"status": "healthy", "database": "not_probed"})
    assert public_is_healthy({"status": "healthy"})


def test_a_payload_the_monitor_cannot_account_for_is_not_health() -> None:
    # Degraded is the 503 shape; the rest are payloads this script has no
    # reading of, and guessing at one is how a real outage gets reported green.
    assert not public_is_healthy({"status": "degraded"})
    assert not public_is_healthy({"status": "healthy", "database": "unreachable"})
    assert not public_is_healthy({"status": "healthy", "database": None})
    assert not public_is_healthy({"status": "healthy", "mode": "maintenance"})
    assert not public_is_healthy({})


# ---------------------------------------------------------------------------
# One outage, one mail
# ---------------------------------------------------------------------------

def test_the_first_failing_probe_pages() -> None:
    """A transition into an outage is the thing worth interrupting someone for."""
    assert should_page_again("success") is True


def test_a_continuing_outage_stays_quiet() -> None:
    """The September outage ran fifteen days at one mail per five minutes."""
    assert should_page_again("failure") is False


@pytest.mark.parametrize("previous", [None, "", "cancelled", "skipped", "timed_out"])
def test_anything_but_a_known_failure_pages(previous) -> None:
    """A gap in the history must err towards telling you, never towards silence."""
    assert should_page_again(previous) is True


def test_the_quiet_behaviour_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setattr(check_uptime, "ALERT_ON_EVERY_FAILURE", True)
    assert check_uptime.should_page_again("failure") is True


def test_history_lookup_reads_the_previous_run_not_this_one(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "ignacio280/machreach")
    monkeypatch.setenv("GITHUB_RUN_ID", "222")
    fetch = Mock(return_value={"workflow_runs": [
        {"id": 222, "conclusion": "success"},   # this run, must be skipped
        {"id": 221, "conclusion": "failure"},
    ]})

    assert check_uptime.previous_run_concluded(fetch=fetch) == "failure"
    assert "workflows/uptime.yml/runs" in fetch.call_args.args[0]


def test_history_lookup_returns_none_when_the_api_fails(monkeypatch, capsys) -> None:
    """A broken lookup must not be mistaken for a healthy history."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "ignacio280/machreach")
    monkeypatch.setenv("GITHUB_RUN_ID", "222")

    result = check_uptime.previous_run_concluded(fetch=Mock(side_effect=OSError("boom")))

    assert result is None
    assert should_page_again(result) is True
    assert "Uptime history unavailable" in capsys.readouterr().out


def test_history_lookup_is_skipped_outside_actions(monkeypatch) -> None:
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    fetch = Mock()

    assert check_uptime.previous_run_concluded(fetch=fetch) is None
    fetch.assert_not_called()


def test_a_down_site_is_still_reported_even_when_it_does_not_page(monkeypatch, capsys) -> None:
    """Quiet means no mail, not no error: the outage must stay on the record."""
    monkeypatch.setattr(check_uptime, "previous_run_concluded", lambda: "failure")
    monkeypatch.setattr(
        check_uptime, "probe_with_retries",
        Mock(side_effect=ProbeError("HTTP 503: Service Unavailable")),
    )
    monkeypatch.setattr(check_uptime, "diagnose_origin", lambda: None)

    assert check_uptime.main() == 0
    out = capsys.readouterr().out
    assert "::error title=MachReach unavailable::HTTP 503" in out
    assert "still down" in out


def test_a_first_outage_exits_non_zero(monkeypatch) -> None:
    monkeypatch.setattr(check_uptime, "previous_run_concluded", lambda: "success")
    monkeypatch.setattr(
        check_uptime, "probe_with_retries",
        Mock(side_effect=ProbeError("HTTP 503: Service Unavailable")),
    )
    monkeypatch.setattr(check_uptime, "diagnose_origin", lambda: None)

    assert check_uptime.main() == 1


def test_recovery_is_announced(monkeypatch, capsys) -> None:
    monkeypatch.setattr(check_uptime, "previous_run_concluded", lambda: "failure")
    monkeypatch.setattr(check_uptime, "probe_with_retries", Mock(return_value={"status": "ok"}))

    assert check_uptime.main() == 0
    assert "MachReach recovered" in capsys.readouterr().out
