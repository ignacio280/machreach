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

# Set this to page on every single failing probe again. The default reports a
# continuing outage once; see should_page_again() for why.
ALERT_ON_EVERY_FAILURE = os.environ.get(
    "UPTIME_ALERT_EVERY_FAILURE", ""
).strip().lower() in {"1", "true", "yes"}


class ProbeError(RuntimeError):
    """Raised when a health endpoint cannot be validated."""


def http_status_is_probeable(url: str, status: int) -> bool:
    return status == 200 or (url == OPERATIONS_HEALTH_URL and status == 503)


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
            if not http_status_is_probeable(url, response.status):
                raise ProbeError(f"HTTP {response.status}: {payload}")
            if not isinstance(payload, dict):
                raise ProbeError("response was not a JSON object")
            return payload
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = exc.reason
        if http_status_is_probeable(url, exc.code) and isinstance(payload, dict):
            return payload
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
    """Accept both shapes /health can return, and nothing else.

    With DB_SLEEP_FRIENDLY the liveness probe stops opening a connection and
    reports `database: not_probed` beside the status, so an exact match against
    {"status": "healthy"} rejected every response from the moment that flag went
    on -- this monitor failed every five minutes for four days while production
    was fine.

    Still strict about the parts that carry meaning: a degraded status fails, an
    unrecognised `database` value fails, and so does any key we do not know,
    because a payload this script cannot account for is not evidence of health.
    Nor is `not_probed` evidence the database is reachable -- that is what the
    /health/operations probe below is for, and it runs on every pass.
    """
    if payload.get("status") != "healthy":
        return False
    if set(payload) - {"status", "database"}:
        return False
    if "database" not in payload:
        return True
    return payload["database"] == "not_probed"


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


def previous_run_concluded(
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> str | None:
    """How the run before this one ended: "success", "failure", or unknown.

    Returns None when it genuinely cannot be determined -- no token, no run id,
    an API error, or a first run with nothing before it. Every caller treats
    None as "assume nothing" and pages, because a monitor that goes quiet
    because its own bookkeeping broke is worse than one that repeats itself.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    workflow = os.environ.get("UPTIME_WORKFLOW_FILE", "uptime.yml").strip()
    if not (token and repo and run_id):
        return None

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = (
        f"{api}/repos/{repo}/actions/workflows/{workflow}/runs"
        "?status=completed&per_page=5"
    )

    def _default_fetch(target: str) -> dict[str, Any]:
        request = urllib.request.Request(
            target,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    try:
        payload = (fetch or _default_fetch)(url)
        for run in payload.get("workflow_runs") or []:
            # Skip this very run: it can already be listed as completed by the
            # time the step that asks is running.
            if str(run.get("id")) == run_id:
                continue
            return str(run.get("conclusion") or "") or None
    except Exception as exc:  # noqa: BLE001 - never let bookkeeping hide an outage
        print(f"::warning title=Uptime history unavailable::{exc}")
        return None
    return None


def should_page_again(previous: str | None) -> bool:
    """Page on the transition into an outage, not on every probe during one.

    This runs every five minutes. A single outage therefore sent roughly 288
    failure mails a day -- the one in September ran fifteen days and produced
    about eighteen hundred of them. That volume does not convey fifteen days of
    urgency, it trains you to filter the sender, and then the *next* outage
    arrives in a folder you no longer read. So the first failing probe pages and
    the ones behind it stay quiet, which is the same information, once.

    The outage is never hidden: every run still prints the error and the run
    itself is still red in the Actions tab. Only the notification stops. And
    anything other than a known-failed previous run pages, so a gap in the
    history errs towards telling you.
    """
    if ALERT_ON_EVERY_FAILURE:
        return True
    return previous != "failure"


def failure_exit_code() -> int:
    """Red the first time, quiet while it stays red -- see should_page_again."""
    previous = previous_run_concluded()
    if should_page_again(previous):
        return 1
    print(
        "::warning title=MachReach still down::The previous check already failed, "
        "so this run is left green to stop one outage sending a mail every five "
        "minutes. The error above is current. Set UPTIME_ALERT_EVERY_FAILURE=1 "
        "to page on every probe."
    )
    return 0


def main() -> int:
    try:
        public = probe_with_retries(
            "MachReach public health", PUBLIC_HEALTH_URL, public_is_healthy
        )
        print(f"Public health: {public}")
    except ProbeError as exc:
        print(f"::error title=MachReach unavailable::{exc}")
        diagnose_origin()
        return failure_exit_code()

    try:
        operations = probe_with_retries(
            "MachReach operations",
            OPERATIONS_HEALTH_URL,
            operations_are_healthy,
        )
        print(f"Operations health: {operations}")
    except ProbeError as exc:
        print(f"::error title=MachReach operational alert::{exc}")
        return failure_exit_code()

    if previous_run_concluded() == "failure":
        print("::notice title=MachReach recovered::Production is answering again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
