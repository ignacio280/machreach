"""English coverage for the student surface.

The student module renders Spanish-authored HTML and relies on
``translate_student_html_fragment`` as an interim bridge to English. That bridge
is a dictionary, so new Spanish copy silently ships untranslated unless
something measures it. This renders every student page with lang=en and fails
if visible Spanish reappears.
"""
import html as html_module
import re

import pytest

from machreach_core.db import _exec, get_db
from machreach_core.i18n import translate_student_title


PAGES = [
    "/student", "/student/focus", "/student/planner", "/student/courses",
    "/student/quizzes", "/student/flashcards", "/student/reviews",
    "/student/leaderboard", "/student/friends", "/student/shop",
    "/student/gpa", "/student/achievements", "/student/settings",
    "/student/profile",
]

# Text that is Spanish *on purpose* in English mode.
ALLOWED_SPANISH = {
    "Español",   # the language switcher offers Spanish; it must read Spanish
}

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TEXT_NODE = re.compile(r"(?<=>)([^<>]+)(?=<)")
_ACCENTED = re.compile(r"[áéíóúñ¿¡]")
_SPANISH_WORD = re.compile(
    r"\b(?:tus?|tu|los|las|del|para|con|una|más|está|están|sesión|sesiones|"
    r"cursos?|prueba|pruebas|nota|notas|semana|día|días|racha|amigos?|tienda|"
    r"ajustes|monedas?|logros?|estudio|estudiar|siguiente|guardar|cerrar|"
    r"agregar|eliminar|volver|todavía|aún|todos|todas)\b", re.I)


def _visible_spanish(markup: str) -> list[str]:
    """Visible text nodes that still look Spanish, ignoring scripts/styles."""
    stripped = _SCRIPT_OR_STYLE.sub(" ", markup)
    found = []
    for node in _TEXT_NODE.findall(stripped):
        text = html_module.unescape(node.strip())
        if len(text) <= 2 or text in ALLOWED_SPANISH:
            continue
        if _ACCENTED.search(text) or _SPANISH_WORD.search(text):
            found.append(text)
    return found


@pytest.fixture()
def english_student(client, make_user):
    user_id = make_user(name="I18n Tester")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (user_id,))
    with client.session_transaction() as sess:
        sess["client_id"] = user_id
        sess["client_name"] = "I18n Tester"
        sess["account_type"] = "student"
        sess["session_version"] = 0
        sess["lang"] = "en"
    return client


@pytest.mark.parametrize("path", PAGES)
def test_student_page_has_no_visible_spanish_in_english(english_student, path):
    response = english_student.get(path, follow_redirects=True)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    leftovers = _visible_spanish(response.get_data(as_text=True))
    assert not leftovers, (
        f"{path} still renders Spanish in English mode: {sorted(set(leftovers))[:8]}"
    )


def test_page_titles_are_translated():
    """Titles bypass the fragment translator, so they need their own path."""
    assert translate_student_title("Mis Cursos", "en") == "My Courses"
    assert translate_student_title("Logros", "en") == "Achievements"


def test_titles_are_untouched_in_spanish():
    assert translate_student_title("Mis Cursos", "es") == "Mis Cursos"


def test_unknown_titles_pass_through_rather_than_blanking():
    assert translate_student_title("Something New", "en") == "Something New"
    assert translate_student_title("", "en") == ""


def test_consent_banner_is_translated(client):
    """The consent banner renders in the shared layout on every page, so it is
    outside the student fragment translator and uses real i18n keys."""
    with client.session_transaction() as sess:
        sess["lang"] = "en"
    body = client.get("/login").get_data(as_text=True)
    assert "Essential only" in body
    assert "Allow analytics" in body
    assert "Tu privacidad" not in body

    with client.session_transaction() as sess:
        sess["lang"] = "es"
    body = client.get("/login").get_data(as_text=True)
    assert "Solo esenciales" in body
    assert "Essential only" not in body
