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

import requests

log = logging.getLogger(__name__)

_TIMEOUT = 20  # seconds


class CanvasClient:
    """Thin wrapper around the Canvas REST API."""

    def __init__(self, base_url: str, token: str):
        # Normalize: "https://school.instructure.com" (no trailing slash)
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    # ── generic helpers ─────────────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base}/api/v1{path}"
        resp = self.session.get(url, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, path: str, params: dict | None = None) -> list:
        """Follow Canvas pagination (Link headers) and collect all pages."""
        results: list = []
        url = f"{self.base}/api/v1{path}"
        p = dict(params or {})
        p.setdefault("per_page", 100)
        while url:
            resp = self.session.get(url, params=p, timeout=_TIMEOUT)
            resp.raise_for_status()
            results.extend(resp.json())
            # Canvas pagination via Link header
            url = None
            p = {}  # params already baked into next URL
            link = resp.headers.get("Link", "")
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
        return results

    def _download_file(self, url: str) -> bytes:
        resp = self.session.get(url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        return resp.content

    # ── courses ─────────────────────────────────────────────
    def get_courses(self, enrollment_state: str = "active") -> list[dict]:
        """Get courses the student is enrolled in for the current term only.

        Canvas keeps past-semester enrollments as 'active', so we fetch
        term info and only return courses from the most recent term that
        actually has courses (i.e. the current semester).
        """
        courses = self._get_paginated("/courses", {
            "enrollment_state": enrollment_state,
            "include[]": ["total_students", "term"],
        })
        # Filter out courses without a name (access-restricted shells)
        courses = [c for c in courses if c.get("name")]

        # ── filter to current term ──
        # Identify the most recent term by end_at (or start_at, or id)
        # among courses that have term info.
        from datetime import datetime as _dt
        now = _dt.utcnow().isoformat() + "Z"

        termed = [c for c in courses if c.get("term")]
        if not termed:
            return courses  # no term info available, return all

        def _term_sort_key(c):
            """Sort terms: prefer ones that are current (start <= now <= end),
            then by latest start_at, then by highest term id."""
            t = c.get("term", {})
            start = t.get("start_at") or ""
            end = t.get("end_at") or "9999"
            # Currently active term gets priority
            is_current = 1 if (start <= now <= end) else 0
            return (is_current, start, t.get("id", 0))

        termed.sort(key=_term_sort_key, reverse=True)
        current_term_id = termed[0]["term"]["id"]

        # Return only courses from the current (most recent) term
        return [c for c in courses
                if c.get("term", {}).get("id") == current_term_id]

    def get_course(self, course_id: int) -> dict:
        return self._get(f"/courses/{course_id}")

    # ── assignments & exams ─────────────────────────────────
    def get_assignments(self, course_id: int) -> list[dict]:
        """All assignments/exams for a course, sorted by due date."""
        items = self._get_paginated(f"/courses/{course_id}/assignments", {
            "order_by": "due_at",
        })
        return items

    def get_upcoming_assignments(self, course_id: int) -> list[dict]:
        """Assignments due in the future."""
        now = datetime.utcnow().isoformat() + "Z"
        all_items = self.get_assignments(course_id)
        return [a for a in all_items
                if a.get("due_at") and a["due_at"] > now]

    # ── syllabus ────────────────────────────────────────────
    def get_syllabus(self, course_id: int) -> str:
        """Get the syllabus body (HTML) if the instructor posted one."""
        data = self._get(f"/courses/{course_id}", {
            "include[]": "syllabus_body",
        })
        return data.get("syllabus_body") or ""

    # ── files ───────────────────────────────────────────────
    def get_files(self, course_id: int) -> list[dict]:
        """All files uploaded by the instructor in this course."""
        return self._get_paginated(f"/courses/{course_id}/files", {
            "sort": "updated_at",
            "order": "desc",
        })

    def get_file_content(self, file_info: dict) -> bytes:
        """Download a file by its Canvas file object (has 'url' key)."""
        return self._download_file(file_info["url"])

    def find_syllabus_files(self, course_id: int) -> list[dict]:
        """Find files that are likely syllabi or course programs."""
        files = self.get_files(course_id)
        patterns = [
            r"syllab",
            r"program[a-z]*",
            r"cronograma",
            r"gu[ií]a\s*(docente|del?\s*curso|acad[eé]mica)?",
            r"sumario",
            r"schedule",
            r"calendar",
            r"outline",
            r"plan\s*(de)?\s*(curso|class|estudio)",
            r"evaluaci[oó]n",
            r"assessment",
            r"course\s*info",
            r"temario",
            r"contenido",
        ]
        regex = re.compile("|".join(patterns), re.IGNORECASE)
        hits: list[dict] = []
        for f in files:
            name = f.get("display_name", "") + " " + f.get("filename", "")
            if regex.search(name):
                hits.append(f)
        return hits

    # ── modules ─────────────────────────────────────────────
    def get_modules(self, course_id: int) -> list[dict]:
        return self._get_paginated(f"/courses/{course_id}/modules")

    def get_module_items(self, course_id: int, module_id: int) -> list[dict]:
        return self._get_paginated(
            f"/courses/{course_id}/modules/{module_id}/items"
        )

    # ── pages (wiki) ────────────────────────────────────────
    def get_pages(self, course_id: int) -> list[dict]:
        return self._get_paginated(f"/courses/{course_id}/pages")

    def get_page(self, course_id: int, page_url: str) -> dict:
        return self._get(f"/courses/{course_id}/pages/{page_url}")

    # ── announcements ───────────────────────────────────────
    def get_announcements(self, course_id: int) -> list[dict]:
        return self._get_paginated("/announcements", {
            "context_codes[]": f"course_{course_id}",
        })


# ── standalone helpers ──────────────────────────────────────

def extract_text_from_pdf(content: bytes) -> str:
    """Best-effort PDF → plain text (uses pdfminer.six for high-quality extraction)."""
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
