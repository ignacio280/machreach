"""Exit 0 when every GitHub check run on a commit has passed.

deploy.sh asks this before deploying a new master commit, which is what
Render's `autoDeployTrigger: checksPass` did: a push whose CI is red, or
still running, is not deployed. Stdlib only, because it runs on the server's
system Python, not in the app image.

    python3 deploy/checks_passed.py ignacio280/machreach <sha>

GITHUB_TOKEN, when set, raises the API rate limit; a public repository needs
none.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PASSING = {"success", "skipped", "neutral"}


def check_runs(repo: str, sha: str, *, opener=None) -> list[dict]:
    opener = opener or urllib.request.urlopen
    url = f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "machreach-deploy"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=20) as response:
        payload = json.load(response)
    return list(payload.get("check_runs") or [])


def verdict(runs: list[dict]) -> tuple[bool, str]:
    """(deployable, reason). No runs at all is not deployable: CI has not started."""
    if not runs:
        return False, "no check runs reported yet"
    pending = [r.get("name") for r in runs if r.get("status") != "completed"]
    if pending:
        return False, "still running: " + ", ".join(str(n) for n in pending)
    failed = [r.get("name") for r in runs if r.get("conclusion") not in PASSING]
    if failed:
        return False, "failed: " + ", ".join(str(n) for n in failed)
    return True, f"{len(runs)} check run(s) passed"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: checks_passed.py <owner/repo> <sha>", file=sys.stderr)
        return 2
    repo, sha = argv
    try:
        runs = check_runs(repo, sha)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[checks] could not read check runs: {exc}", file=sys.stderr)
        return 1
    ok, reason = verdict(runs)
    print(f"[checks] {sha[:12]}: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
