"""
Canvas LMS API client — fetches courses, files, assignments, and syllabus
data for the authenticated student.

Docs: https://canvas.instructure.com/doc/api/
Auth: Bearer token from user's Canvas account settings.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile


log = logging.getLogger(__name__)

_TIMEOUT = 20  # seconds
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_EXTRACTED_CHARS = 250_000
MAX_DOCX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
# A scan has no text layer, but the page markers below still make its
# extraction look full. These two thresholds together separate "photographs of
# pages" from "a genuinely short document": a real page carries far more than
# the stray page number a scanner leaves behind.
MIN_TEXT_LAYER_CHARS = 200
MIN_TEXT_LAYER_CHARS_PER_PAGE = 20
# Below this there is nothing to build questions from, and a model handed an
# empty document invents a generic one instead of saying so.
MIN_SOURCE_TEXT_CHARS = 200

_SCAFFOLDING_RE = re.compile(
    r"^(?:\[PDF DOCUMENT — TOTAL PAGES: \d+\]|--- PAGE \d+ of \d+ ---)\s*$",
    re.MULTILINE,
)


class NoTextLayer(ValueError):
    """The document parsed cleanly but carries no extractable text.

    Almost always a scan: the pages are images, so a text extractor finds
    nothing. Callers must not read this as "the student uploaded nothing" —
    they uploaded a real file, and need to be told it has no text layer.
    """


def readable_text_length(text: str) -> int:
    """Characters of actual material, ignoring extraction scaffolding.

    The page markers are ours, not the document's, so a 40-page scan can carry
    a thousand characters and still say nothing at all.
    """
    return len(_SCAFFOLDING_RE.sub("", text or "").strip())

# ── Browser-extension "connect" token ──────────────────────────────────────
# The Focus Guard extension can read a student's Canvas course list from their
# own logged-in browser session (no API token, no admin OAuth key) and POST it
# back to MachReach. To authenticate that POST without relying on cross-site
# cookies, the logged-in MachReach page embeds this signed token; the extension
# captures it and includes it when importing courses. It only authorizes
# course import for one client_id and carries no other privilege.
_EXT_CONNECT_SALT = "canvas-ext-connect"
_EXT_CONNECT_MAX_AGE_SECONDS = 15 * 60


def init_connection_state_schema() -> None:
    """Create durable token-revocation state during database startup."""
    from machreach_core.db import _exec, get_db
    with get_db() as db:
        _exec(
            db,
            "CREATE TABLE IF NOT EXISTS student_canvas_connection_state ("
            "client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,"
            "token_version INTEGER NOT NULL DEFAULT 0)",
        )


def _ext_serializer():
    from itsdangerous import URLSafeTimedSerializer
    from machreach_core.config import SECRET_KEY
    return URLSafeTimedSerializer(SECRET_KEY, salt=_EXT_CONNECT_SALT)


def _connection_version(client_id: int) -> int:
    from machreach_core.db import _fetchval, get_db
    init_connection_state_schema()
    with get_db() as db:
        return int(
            _fetchval(
                db,
                "SELECT token_version FROM student_canvas_connection_state "
                "WHERE client_id=%s",
                (client_id,),
            )
            or 0
        )


def make_connect_token(client_id: int) -> str:
    """Short-lived signed token identifying a student to the browser extension."""
    return _ext_serializer().dumps(
        {"client_id": int(client_id), "version": _connection_version(client_id)}
    )


def verify_connect_token(token: str) -> int | None:
    """Return the client_id encoded in a connect token, or None if invalid."""
    try:
        payload = _ext_serializer().loads(
            (token or "").strip(),
            max_age=_EXT_CONNECT_MAX_AGE_SECONDS,
        )
        if isinstance(payload, dict):
            client_id = int(payload["client_id"])
            version = int(payload.get("version") or 0)
        else:
            client_id = int(payload)
            version = 0
        return client_id if version == _connection_version(client_id) else None
    except Exception:
        return None


def revoke_connect_tokens(client_id: int) -> None:
    """Immediately invalidate every outstanding extension connect token."""
    from machreach_core.db import _exec, get_db
    init_connection_state_schema()
    with get_db() as db:
        _exec(
            db,
            "INSERT INTO student_canvas_connection_state(client_id, token_version) "
            "VALUES (%s, 1) ON CONFLICT(client_id) DO UPDATE SET "
            "token_version=student_canvas_connection_state.token_version + 1",
            (client_id,),
        )


# ── standalone helpers ──────────────────────────────────────

def extract_text_from_pdf(content: bytes) -> str:
    """Best-effort PDF → plain text with explicit page markers so downstream AI
    knows the exact page count and which page each passage belongs to."""
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("PDF is too large (max 10 MB)")

    total_pages = None
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content), strict=True)
        total_pages = len(reader.pages)
        if total_pages > MAX_PDF_PAGES:
            raise ValueError(f"PDF has too many pages (max {MAX_PDF_PAGES})")
    except ImportError:
        pass

    # First, try pdfminer page-by-page so we can insert real page markers.
    pages_text: list[str] | None = None
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        if total_pages is None:
            raise ValueError("PDF page count could not be validated")
        pages_text = []
        for i in range(total_pages):
            buf = io.StringIO()
            try:
                extract_text_to_fp(io.BytesIO(content), buf, page_numbers=[i], laparams=LAParams())
                pages_text.append(buf.getvalue().strip())
            except Exception:
                pages_text.append("")
    except ImportError:
        pages_text = None
    except Exception as e:
        log.warning("Per-page PDF extraction failed, falling back: %s", e)
        pages_text = None

    if pages_text is not None:
        _reject_scan(sum(len(page) for page in pages_text), total_pages)
        body = "\n\n".join(
            f"--- PAGE {i+1} of {total_pages} ---\n{txt}" for i, txt in enumerate(pages_text)
        )
        header = f"[PDF DOCUMENT — TOTAL PAGES: {total_pages}]\n\n"
        return (header + body).strip()[:MAX_EXTRACTED_CHARS]

    # Fallback: bulk pdfminer (no page markers)
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract
        text = (_pdfminer_extract(io.BytesIO(content)) or "").strip()[:MAX_EXTRACTED_CHARS]
    except ImportError:
        log.warning("pdfminer.six not installed — skipping PDF extraction")
        return ""
    except Exception as e:
        log.warning("PDF extraction failed: %s", e)
        return ""
    _reject_scan(len(text), total_pages)
    return text


def _reject_scan(found_chars: int, total_pages: int | None) -> None:
    """Refuse a PDF whose pages are images rather than text.

    Without this the extraction still returns one page marker per page, which
    reads downstream as a real document that happens to say nothing — and a
    model handed an empty document writes a generic quiz rather than failing.
    """
    per_page_floor = MIN_TEXT_LAYER_CHARS_PER_PAGE * max(1, total_pages or 1)
    if found_chars < MIN_TEXT_LAYER_CHARS and found_chars < per_page_floor:
        raise NoTextLayer("This PDF has no selectable text; its pages are images.")


def extract_text_from_docx(content: bytes) -> str:
    """Best-effort DOCX → plain text (uses python-docx if available)."""
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("DOCX is too large (max 10 MB)")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ValueError("Invalid DOCX document")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000:
            raise ValueError("DOCX contains too many archive entries")
        uncompressed = sum(max(0, entry.file_size) for entry in entries)
        if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise ValueError("DOCX expands beyond the safe processing limit")
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs).strip()[:MAX_EXTRACTED_CHARS]
    except ImportError:
        log.warning("python-docx not installed — skipping DOCX extraction")
        return ""
    except Exception as e:
        log.warning("DOCX extraction failed: %s", e)
        return ""
