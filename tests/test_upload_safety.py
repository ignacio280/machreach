"""Adversarial document uploads are rejected before expensive extraction."""

import io
import builtins
import zipfile

import pytest

from student import canvas as canvas_mod
from student.canvas import (
    MAX_DOCUMENT_BYTES,
    MAX_PDF_PAGES,
    NoTextLayer,
    extract_text_from_docx,
    extract_text_from_pdf,
)
from pdf_fixtures import STUDY_MATERIAL, scanned_pdf, text_pdf


def test_oversized_pdf_is_rejected_before_parsing():
    with pytest.raises(ValueError, match="too large"):
        extract_text_from_pdf(b"x" * (MAX_DOCUMENT_BYTES + 1))


def test_pdf_page_limit_is_enforced_before_text_extraction():
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=72, height=72)
    payload = io.BytesIO()
    writer.write(payload)

    with pytest.raises(ValueError, match="too many pages"):
        extract_text_from_pdf(payload.getvalue())


def test_pdf_page_failure_is_isolated_and_bulk_fallback_is_safe(monkeypatch):
    import pdfminer.high_level

    payload = scanned_pdf(pages=2)

    def one_bad_page(stream, buf, page_numbers=None, **kwargs):
        if page_numbers == [0]:
            raise RuntimeError("malformed page")
        buf.write(STUDY_MATERIAL)

    monkeypatch.setattr(pdfminer.high_level, "extract_text_to_fp", one_bad_page)
    page_result = extract_text_from_pdf(payload)
    assert "TOTAL PAGES: 2" in page_result
    assert "--- PAGE 1 of 2 ---" in page_result
    # The unreadable page is skipped, not fatal: page two still comes through.
    assert "Carnot cycle" in page_result

    monkeypatch.setattr(canvas_mod, "MAX_PDF_PAGES", 0)
    with pytest.raises(ValueError, match="too many pages"):
        extract_text_from_pdf(payload)


def test_pdf_of_scanned_pages_is_refused_rather_than_returned_as_page_markers():
    """Page markers are scaffolding this module adds, not document content.

    Returning them for a scan made an image-only PDF look like a successfully
    read document that happened to say nothing, and the quiz generator built a
    quiz about nothing out of it.
    """
    with pytest.raises(NoTextLayer):
        extract_text_from_pdf(scanned_pdf(pages=12))

    with pytest.raises(NoTextLayer):
        extract_text_from_pdf(scanned_pdf(pages=1))

    readable = extract_text_from_pdf(text_pdf([STUDY_MATERIAL[:70], STUDY_MATERIAL[70:140]]))
    assert "Carnot cycle" in readable
    assert "--- PAGE 2 of 2 ---" in readable


def test_pdf_falls_back_when_page_count_dependency_is_unavailable(monkeypatch):
    import pdfminer.high_level

    real_import = builtins.__import__

    def without_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pypdf)
    monkeypatch.setattr(
        pdfminer.high_level,
        "extract_text",
        lambda stream: "safe bulk fallback. " + STUDY_MATERIAL,
    )

    assert "safe bulk fallback" in extract_text_from_pdf(b"%PDF fallback")


def test_pdf_returns_empty_when_optional_extractors_are_unavailable(monkeypatch):
    real_import = builtins.__import__

    def without_pdf_dependencies(name, *args, **kwargs):
        if name == "pypdf" or name.startswith("pdfminer"):
            raise ImportError("PDF dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pdf_dependencies)

    assert extract_text_from_pdf(b"%PDF unavailable") == ""


def test_pdf_bulk_extraction_failure_is_contained(monkeypatch):
    import pdfminer.high_level

    real_import = builtins.__import__

    def without_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf unavailable")
        return real_import(name, *args, **kwargs)

    def fail_bulk(stream):
        raise RuntimeError("corrupt PDF")

    monkeypatch.setattr(builtins, "__import__", without_pypdf)
    monkeypatch.setattr(pdfminer.high_level, "extract_text", fail_bulk)

    assert extract_text_from_pdf(b"%PDF corrupt") == ""


def test_non_zip_docx_is_rejected():
    with pytest.raises(ValueError, match="Invalid DOCX"):
        extract_text_from_docx(b"not really a document")


def test_oversized_docx_is_rejected_before_zip_processing():
    with pytest.raises(ValueError, match="too large"):
        extract_text_from_docx(b"x" * (MAX_DOCUMENT_BYTES + 1))


def test_docx_archive_entry_limit_is_enforced():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(2001):
            archive.writestr(f"entry-{index}", b"")

    with pytest.raises(ValueError, match="too many archive entries"):
        extract_text_from_docx(payload.getvalue())


def test_docx_zip_bomb_is_rejected_by_uncompressed_size():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (26 * 1024 * 1024))

    with pytest.raises(ValueError, match="safe processing limit"):
        extract_text_from_docx(payload.getvalue())


def test_docx_parser_failure_does_not_escape(monkeypatch):
    import docx

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")

    def fail_parse(*args, **kwargs):
        raise RuntimeError("malformed document")

    monkeypatch.setattr(docx, "Document", fail_parse)
    assert extract_text_from_docx(payload.getvalue()) == ""


def test_docx_returns_empty_when_optional_parser_is_unavailable(monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")

    real_import = builtins.__import__

    def without_docx(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("docx unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_docx)

    assert extract_text_from_docx(payload.getvalue()) == ""
