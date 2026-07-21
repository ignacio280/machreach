"""Check MachReach's public and operational production health endpoints."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


PUBLIC_HEALTH_URL = os.environ.get(
    "UPTIME_PUBLIC_HEALTH_URL", "https://machreach.com/health"
)
OPERATIONS_HEALTH_URL = os.environ.get(
    "UPTIME_OPERATIONS_HEALTH_URL", "https://machreach.com/health/operations"
)
ORIGIN_HEALTH_URL = os.environ.get(
    "UPTIME_ORIGIN_HEALTH_URL", "https://machreach.onrender.com/health"
)
USER_AGENT = "MachReach-Uptime/2.0"


class ProbeError(RuntimeError):
    """Raised when a health endpoint cannot be validated."""


def fetch_json(url: str, timeout: float = 15) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    if url == OPERATIONS_HEALTH_URL:
        secret = os.environ.get("OPERATIONS_SECRET", "").strip()
        if not secret:
            raise ProbeError("OPERATIONS_SECRET is not configured")
        headers["X-Operations-Secret"] = secret
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            if response.status != 200:
                raise ProbeError(f"HTTP {response.status}: {payload}")
            if not isinstance(payload, dict):
                raise ProbeError("response was not a JSON object")
            return payload
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = exc.reason
        raise ProbeError(f"HTTP {exc.code}: {payload}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProbeError(str(exc)) from exc


def probe_with_retries(
    name: str,
    url: str,
    validator: Callable[[dict[str, Any]], bool],
    *,
    attempts: int = 3,
    timeout: float = 15,
    delays: tuple[float, ...] = (3, 7),
    fetch: Callable[[str, float], dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            payload = fetch(url, timeout)
            if not validator(payload):
                raise ProbeError(f"unhealthy response: {payload}")
            if attempt > 1:
                print(f"{name} recovered on attempt {attempt}/{attempts}")
            return payload
        except ProbeError as exc:
            errors.append(f"attempt {attempt}/{attempts}: {exc}")
            if attempt < attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)]
                print(f"::warning title={name} retry::{errors[-1]}; retrying in {delay}s")
                sleep(delay)

    raise ProbeError("; ".join(errors))


def public_is_healthy(payload: dict[str, Any]) -> bool:
    return payload == {"status": "healthy"}


def operations_are_healthy(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "ok"


def diagnose_origin() -> None:
    """Report whether the Render origin works without masking a public outage."""
    try:
        payload = probe_with_retries(
            "Render origin",
            ORIGIN_HEALTH_URL,
            public_is_healthy,
            attempts=1,
        )
        print(
            "::notice title=Render origin reachable::"
            f"The origin is healthy while the public domain is unavailable: {payload}"
        )
    except ProbeError as exc:
        print(f"::notice title=Render origin unavailable::{exc}")


def main() -> int:
    try:
        public = probe_with_retries(
            "MachReach public health", PUBLIC_HEALTH_URL, public_is_healthy
        )
        print(f"Public health: {public}")
    except ProbeError as exc:
        print(f"::error title=MachReach unavailable::{exc}")
        diagnose_origin()
        return 1

    try:
        operations = probe_with_retries(
            "MachReach operations",
            OPERATIONS_HEALTH_URL,
            operations_are_healthy,
        )
        print(f"Operations health: {operations}")
    except ProbeError as exc:
        print(f"::error title=MachReach operational alert::{exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
