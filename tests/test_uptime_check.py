from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from scripts.check_uptime import (
    ProbeError,
    operations_are_healthy,
    probe_with_retries,
    public_is_healthy,
)


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


def test_backup_freshness_alone_is_advisory_for_uptime() -> None:
    payload = {
        "status": "degraded",
        "checks": {
            "backup_freshness": {"status": "alert"},
            "worker_heartbeat": {"status": "ok"},
            "failed_webhooks": {"status": "ok", "count": 0},
        },
    }

    assert operations_are_healthy(payload)


def test_non_backup_operational_alert_still_fails_uptime() -> None:
    payload = {
        "status": "degraded",
        "checks": {
            "backup_freshness": {"status": "alert"},
            "worker_heartbeat": {"status": "alert"},
        },
    }

    assert not operations_are_healthy(payload)
