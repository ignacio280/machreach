"""Adversarial document uploads are rejected before expensive extraction."""

import io
import zipfile

import pytest

from student.canvas import MAX_DOCUMENT_BYTES, extract_text_from_docx, extract_text_from_pdf


def test_oversized_pdf_is_rejected_before_parsing():
    with pytest.raises(ValueError, match="too large"):
        extract_text_from_pdf(b"x" * (MAX_DOCUMENT_BYTES + 1))


def test_non_zip_docx_is_rejected():
    with pytest.raises(ValueError, match="Invalid DOCX"):
        extract_text_from_docx(b"not really a document")


def test_docx_zip_bomb_is_rejected_by_uncompressed_size():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (26 * 1024 * 1024))

    with pytest.raises(ValueError, match="safe processing limit"):
        extract_text_from_docx(payload.getvalue())
