"""Isolated Flask server used only by Playwright journeys."""

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

data_dir = Path(tempfile.mkdtemp(prefix="machreach_e2e_"))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("RENDER", None)
os.environ["DATABASE_PATH"] = str(data_dir / "e2e.db")
os.environ["SECRET_KEY"] = "e2e-session-secret"
os.environ["ENCRYPTION_KEY"] = "e2e-encryption-key"
os.environ["BASE_URL"] = "http://127.0.0.1:4173"
os.environ["LEMON_SQUEEZY_WEBHOOK_SECRET"] = "e2e-webhook-secret"
os.environ["LS_VARIANT_STUDENT_PLUS"] = "e2e-plus-variant"

import app as appmod  # noqa: E402
import worker  # noqa: E402
from flask import jsonify  # noqa: E402
from machreach_core import lemonsqueezy as lemon  # noqa: E402
from machreach_core.db import _exec, claim_async_jobs, create_client, get_db  # noqa: E402
from student import analyzer  # noqa: E402


appmod.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
appmod.limiter.enabled = False
appmod._send_system_email = lambda *_args, **_kwargs: True
lemon.create_checkout = lambda *_args, **_kwargs: (
    "https://checkout.lemonsqueezy.test/buy/e2e-plus"
)
analyzer.generate_quiz = lambda **_kwargs: [{
    "question": "What is velocity?",
    "option_a": "Distance divided by time",
    "option_b": "Time divided by distance",
    "option_c": "Mass times volume",
    "option_d": "Energy divided by mass",
    "correct": "a",
    "explanation": "Velocity measures displacement over time.",
    "topic": "Kinematics",
}]


@appmod.app.post("/__e2e__/run-quiz-worker")
def run_quiz_worker():
    jobs = claim_async_jobs("student_quiz_generation", limit=1)
    for job in jobs:
        worker._process_student_quiz_job(job)
    return jsonify(processed=len(jobs))


def seed_verified_student(name: str, email: str) -> None:
    client_id = create_client(
        name,
        email,
        appmod._hash_pw("e2e-password-123"),
        "",
        "student",
    )
    with get_db() as db:
        _exec(
            db,
            "UPDATE clients SET email_verified = 1, academic_setup_complete = 1 "
            "WHERE id = %s",
            (client_id,),
        )


seed_verified_student("Browser Student", "browser-e2e@example.test")
seed_verified_student("Delete Student", "delete-e2e@example.test")


if __name__ == "__main__":
    appmod.app.run(host="127.0.0.1", port=4173, use_reloader=False)
