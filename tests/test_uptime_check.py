from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from scripts.check_uptime import (
    OPERATIONS_HEALTH_URL,
    ProbeError,
    http_status_is_probeable,
    operations_are_healthy,
    probe_with_retries,
    public_is_healthy,
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
