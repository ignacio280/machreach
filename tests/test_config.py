"""Configuration safety checks."""
import os
from pathlib import Path
import subprocess
import sys


def test_default_database_filename_is_machreach():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    # Keep an explicit empty value so load_dotenv() cannot rehydrate a local
    # developer override from the ignored .env file in the repository root.
    env["DATABASE_PATH"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from machreach_core.config import DATABASE_PATH; print(DATABASE_PATH.name)",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout.strip() == "machreach.db"


def test_encryption_key_is_required_in_render():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["RENDER"] = "1"
    env["SECRET_KEY"] = "prod-secret"
    env["ENCRYPTION_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import machreach_core.config"],
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
        [sys.executable, "-c", "import machreach_core.config"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "SECRET_KEY must be set in production" in result.stderr
