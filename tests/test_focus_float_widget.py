from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "static" / "machreach_layout" / "layout-2.js"


def _function_source(source: str, name: str, next_name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index(f"function {next_name}(", start)
    return source[start:end]


def test_closing_floating_timer_minimizes_without_stopping_session():
    source = CONTROLLER.read_text(encoding="utf-8")
    close_source = _function_source(source, "closeFocusFloat", "activateFocusFloat")

    assert "localStorage.setItem('focus_float_minimized', '1')" in close_source
    assert "localStorage.removeItem('focus_float')" not in close_source
    assert ".pause()" not in close_source
    assert "is-minimized" in close_source


def test_minimized_timer_expands_before_navigating_to_focus_page():
    source = CONTROLLER.read_text(encoding="utf-8")
    activate_source = source[source.index("function activateFocusFloat(") :]

    assert "el.classList.contains('is-minimized')" in activate_source
    assert "localStorage.removeItem('focus_float_minimized')" in activate_source
    assert "window.location = '/student/focus'" in activate_source
