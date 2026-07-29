"""Document safety, signed extension tokens, and translation edge cases."""

import io

from machreach_core import tracker
from machreach_core.i18n import (
    t,
    t_dict,
    translate_rank_name,
    translate_student_html_fragment,
)
from student.canvas import (
    extract_text_from_docx,
    extract_text_from_pdf,
    make_connect_token,
    verify_connect_token,
)
from student import canvas as canvas_mod


def test_canvas_connect_tokens_round_trip_and_reject_tampering():
    token = make_connect_token(712)

    assert verify_connect_token(token) == 712
    assert verify_connect_token(token + "tampered") is None
    assert verify_connect_token("") is None


def test_legacy_scalar_canvas_connect_token_remains_version_checked(monkeypatch):
    class LegacySerializer:
        def loads(self, token, max_age):
            assert token == "legacy-token"
            assert max_age > 0
            return "712"

    monkeypatch.setattr(canvas_mod, "_ext_serializer", lambda: LegacySerializer())
    monkeypatch.setattr(canvas_mod, "_connection_version", lambda client_id: 0)

    assert verify_connect_token("legacy-token") == 712

    monkeypatch.setattr(canvas_mod, "_connection_version", lambda client_id: 1)
    assert verify_connect_token("legacy-token") is None


def test_docx_and_pdf_text_extraction_succeeds():
    from docx import Document
    from pypdf import PdfWriter

    document = Document()
    document.add_paragraph("MachReach document extraction")
    docx_bytes = io.BytesIO()
    document.save(docx_bytes)

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_bytes = io.BytesIO()
    writer.write(pdf_bytes)

    assert "MachReach document extraction" in extract_text_from_docx(docx_bytes.getvalue())
    assert "TOTAL PAGES: 1" in extract_text_from_pdf(pdf_bytes.getvalue())


def test_i18n_translation_helpers_cover_visible_text_and_attributes(flask_app):
    with flask_app.test_request_context("/"):
        assert t("nav.dashboard") == "Panel"
        assert t("missing.key") == "missing.key"
        assert t_dict("common")["save"] == "Guardar"

        markup = (
            '<div title="Guardar cambios">Tu progreso de hoy</div>'
            '<script>const label = "Guardar cambios";</script>'
            '<style>.x{display:block}</style>'
        )
        translated = translate_student_html_fragment(markup, "en")

    assert "Your progress today" in translated
    assert 'title="Save changes"' in translated
    assert 'const label = "Guardar cambios"' in translated
    assert ".x{display:block}" in translated
    assert translate_rank_name("Bronce", "es") == "Bronce"
    assert translate_rank_name("unknown-rank", "en") == "unknown-rank"
    assert translate_student_html_fragment(markup, "es") == markup
    assert len(tracker.TRACKING_PIXEL) == 43
