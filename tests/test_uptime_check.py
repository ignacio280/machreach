from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from scripts.check_uptime import ProbeError, probe_with_retries, public_is_healthy


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
