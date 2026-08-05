"""Every student API contract rejects callers without a student session."""

import re


def _concrete_path(rule: str) -> str:
    path = re.sub(r"<int:[^>]+>", "1", rule)
    path = re.sub(r"<(?:string:)?date_str>", "2099-01-01", path)
    path = re.sub(r"<(?:string:)?[^>]+>", "placeholder", path)
    return path


def test_all_student_api_routes_are_session_protected(
    client, flask_app, monkeypatch
):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    failures = []
    checked = []
    retired_without_state = {
        "/api/student/courses/1/upload": 410,
        "/api/student/courses/1/ai-studio": 410,
    }

    for rule in sorted(flask_app.url_map.iter_rules(), key=lambda item: item.rule):
        if not rule.rule.startswith("/api/student/"):
            continue
        path = _concrete_path(rule.rule)
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        for method in methods:
            response = client.open(path, method=method, json={})
            expected = retired_without_state.get(path, 401)
            checked.append((method, path))
            if response.status_code != expected:
                failures.append(
                    f"{method} {path}: expected {expected}, got "
                    f"{response.status_code} ({response.get_data(as_text=True)[:120]})"
                )

    assert len(checked) >= 100
    assert failures == []
