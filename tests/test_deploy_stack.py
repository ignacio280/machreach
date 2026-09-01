"""The single-server stack in deploy/ must run the app exactly as Render does.

These pin the properties that keep the move a change of host and nothing
else: the same interpreter line and hashed lock file, the same gunicorn
command (one preloaded worker), an always-on worker, Postgres behind a
health check, HTTPS in front, and a deploy gate that refuses red or
unfinished CI the way Render's checksPass trigger did.
"""
import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_start_command() -> str:
    blueprint = _read(REPO_ROOT / "render.yaml")
    line = next(ln for ln in blueprint.splitlines() if "startCommand: gunicorn" in ln)
    return line.split("startCommand:", 1)[1].strip()


def test_the_image_uses_the_same_python_line_and_hashed_lock_as_render():
    dockerfile = _read(REPO_ROOT / "Dockerfile")
    assert dockerfile.startswith("# ") and "FROM python:3.13-slim" in dockerfile
    assert "pip install --require-hashes -r requirements.lock" in dockerfile
    assert "USER app" in dockerfile


def test_the_compose_web_command_matches_the_render_start_command():
    compose = _read(DEPLOY / "docker-compose.yml")
    expected = _render_start_command().replace("$PORT", "5000")
    flat = " ".join(compose.split())
    assert expected in flat, f"compose web command drifted from render.yaml: {expected}"


def test_the_stack_has_every_role_render_had():
    compose = _read(DEPLOY / "docker-compose.yml")
    assert "image: postgres:17" in compose
    assert "pg_isready -U machreach -d machreach" in compose
    assert "command: python worker.py" in compose
    assert 'restart: unless-stopped' in compose
    assert '"443:443"' in compose and '"80:80"' in compose
    assert "APP_ENV: production" in compose  # flips every production switch without RENDER
    assert "DATABASE_URL: postgresql://machreach:${POSTGRES_PASSWORD}@db:5432/machreach" in compose


def test_the_env_template_and_cloud_init_carry_every_secret_the_render_service_needs():
    blueprint = _read(REPO_ROOT / "render.yaml")
    lines = blueprint.splitlines()
    secret_keys = {
        line.split("key:", 1)[1].strip()
        for line, nxt in zip(lines, lines[1:])
        if "- key:" in line and "sync: false" in nxt
    } - {"DATABASE_URL", "RATELIMIT_STORAGE_URI"}
    assert secret_keys, "could not read the service's secrets from render.yaml"
    for path in (DEPLOY / "env.example", DEPLOY / "cloud-init.yaml"):
        text = _read(path)
        missing = [k for k in secret_keys if f"{k}=" not in text]
        assert not missing, f"{path.name} lacks {missing}"
    cloud_init = _read(DEPLOY / "cloud-init.yaml")
    assert cloud_init.startswith("#cloud-config\n")
    assert "bash /opt/machreach/deploy/bootstrap.sh" in cloud_init


@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.sh")))
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)
    assert _read(DEPLOY / script).startswith("#!/usr/bin/env bash\n")


def test_systemd_units_reference_scripts_that_exist():
    for unit in DEPLOY.glob("systemd/*.service"):
        exec_line = next(ln for ln in _read(unit).splitlines() if ln.startswith("ExecStart="))
        script = exec_line.split("=", 1)[1].split()[0].replace("/opt/machreach/", "")
        assert (REPO_ROOT / script).is_file(), f"{unit.name} runs missing {script}"
    timers = {p.stem for p in DEPLOY.glob("systemd/*.timer")}
    assert timers == {p.stem for p in DEPLOY.glob("systemd/*.service")}


# --- The deploy gate --------------------------------------------------------

def _load_gate():
    spec = importlib.util.spec_from_file_location("checks_passed", DEPLOY / "checks_passed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_only_a_fully_green_commit_is_deployable():
    assert gate.verdict([_run("test"), _run("audit", conclusion="skipped"), _run("lint", conclusion="neutral")]) == (
        True, "3 check run(s) passed"
    )
    assert gate.verdict([]) == (False, "no check runs reported yet")
    assert gate.verdict([_run("test", status="in_progress", conclusion=None)]) == (False, "still running: test")
    assert gate.verdict([_run("test"), _run("browser", conclusion="failure")]) == (False, "failed: browser")
    assert gate.verdict([_run("test", conclusion="cancelled")])[0] is False


def test_the_gate_reads_github_and_exits_by_verdict(monkeypatch, capsys):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        body = json.dumps({"check_runs": [_run("CI / test-and-build")]}).encode()
        return Response(body)

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

    assert gate.main(["ignacio280/machreach", "abc123def456789"]) == 0
    assert seen["url"] == "https://api.github.com/repos/ignacio280/machreach/commits/abc123def456789/check-runs?per_page=100"
    assert seen["auth"] == "Bearer ghp_test"
    assert "1 check run(s) passed" in capsys.readouterr().out

    def unreachable(request, timeout):
        raise gate.urllib.error.URLError("offline")

    monkeypatch.setattr(gate.urllib.request, "urlopen", unreachable)
    assert gate.main(["ignacio280/machreach", "abc123def456789"]) == 1
    assert gate.main(["only-one-arg"]) == 2
