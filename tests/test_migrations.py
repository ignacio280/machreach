"""Fresh-install and readiness regressions for database initialization."""

import os
from pathlib import Path
import subprocess
import sys


def test_fresh_database_boot_has_no_schema_order_errors(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env.pop("DATABASE_URL", None)
    env.pop("RENDER", None)
    env["DATABASE_PATH"] = str(tmp_path / "fresh.db")
    env["SECRET_KEY"] = "fresh-boot-secret"
    env["ENCRYPTION_KEY"] = "fresh-boot-encryption"

    result = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "backfill_course_catalog failed" not in result.stderr
    assert "no such column" not in result.stderr
