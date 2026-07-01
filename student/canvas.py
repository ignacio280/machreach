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
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests
from requests import HTTPError

log = logging.getLogger(__name__)

_TIMEOUT = 20  # seconds

# ── Browser-extension "connect" token ──────────────────────────────────────
# The Focus Guard extension can read a student's Canvas course list from their
# own logged-in browser session (no API token, no admin OAuth key) and POST it
# back to MachReach. To authenticate that POST without relying on cross-site
# cookies, the logged-in MachReach page embeds this signed token; the extension
# captures it and includes it when importing courses. It only authorizes
# course import for one client_id and carries no other privilege.
_EXT_CONNECT_SALT = "canvas-ext-connect"
_EXT_CONNECT_MAX_AGE_SECONDS = 15 * 60


def _ext_serializer():
    from itsdangerous import URLSafeTimedSerializer
    from outreach.config import SECRET_KEY
    return URLSafeTimedSerializer(SECRET_KEY, salt=_EXT_CONNECT_SALT)


def make_connect_token(client_id: int) -> str:
    """Short-lived signed token identifying a student to the browser extension."""
    return _ext_serializer().dumps(int(client_id))


def verify_connect_token(token: str) -> int | None:
    """Return the client_id encoded in a connect token, or None if invalid."""
    try:
        return int(_ext_serializer().loads(
            (token or "").strip(),
            max_age=_EXT_CONNECT_MAX_AGE_SECONDS,
        ))
    except Exception:
        return None


# ── standalone helpers ──────────────────────────────────────

def extract_text_from_pdf(content: bytes) -> str:
    """Best-effort PDF → plain text with explicit page markers so downstream AI
    knows the exact page count and which page each passage belongs to."""
    # First, try pdfminer page-by-page so we can insert real page markers.
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        from pypdf import PdfReader  # used only for accurate page count
        reader = PdfReader(io.BytesIO(content))
        total_pages = len(reader.pages)
        pages_text = []
        for i in range(total_pages):
            buf = io.StringIO()
            try:
                extract_text_to_fp(io.BytesIO(content), buf, page_numbers=[i], laparams=LAParams())
                pages_text.append(buf.getvalue().strip())
            except Exception:
                pages_text.append("")
        body = "\n\n".join(
            f"--- PAGE {i+1} of {total_pages} ---\n{txt}" for i, txt in enumerate(pages_text)
        )
        header = f"[PDF DOCUMENT — TOTAL PAGES: {total_pages}]\n\n"
        return (header + body).strip()
    except ImportError:
        pass
    except Exception as e:
        log.warning("Per-page PDF extraction failed, falling back: %s", e)
    # Fallback: bulk pdfminer (no page markers)
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract
        text = _pdfminer_extract(io.BytesIO(content))
        return (text or "").strip()
    except ImportError:
        log.warning("pdfminer.six not installed — skipping PDF extraction")
        return ""
    except Exception as e:
        log.warning("PDF extraction failed: %s", e)
        return ""


def extract_text_from_docx(content: bytes) -> str:
    """Best-effort DOCX → plain text (uses python-docx if available)."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except ImportError:
        log.warning("python-docx not installed — skipping DOCX extraction")
        return ""
    except Exception as e:
        log.warning("DOCX extraction failed: %s", e)
        return ""
