"""Configuration safety checks."""
import os
from pathlib import Path
import subprocess
import sys


def test_encryption_key_is_required_in_render():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["RENDER"] = "1"
    env["SECRET_KEY"] = "prod-secret"
    env["ENCRYPTION_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import outreach.config"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "ENCRYPTION_KEY must be set in production" in result.stderr


def test_known_fallback_secrets_are_rejected_in_non_render_production():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env.pop("RENDER", None)
    env["FLASK_ENV"] = "production"
    env["SECRET_KEY"] = ""
    env["ENCRYPTION_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import outreach.config"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "SECRET_KEY must be set in production" in result.stderr
