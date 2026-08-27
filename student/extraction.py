"""Document text extraction, isolated in a child process.

pdfminer is pure Python: extracting a long PDF is minutes of CPU with the GIL
held. Run inside a gunicorn request that starves every other thread in the
process — including /health, which Render kills after five seconds of silence.
One student uploading a scanned textbook took the whole instance down, and the
extraction's memory spike is what the "exceeded its memory limit" restarts
were. So extraction happens in a child process the web worker can kill: the
GIL it holds is its own, a timeout ends it, and whatever it allocated dies
with it.

The child is this same module run with ``-m`` (see ``main``): it reads the
document from stdin, writes a JSON verdict to stdout, and imports only
student.canvas, which is deliberately light at module level.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from student.canvas import NoTextLayer

# A 200-page text PDF finishes well inside this on half a shared CPU; a
# pathological one is killed rather than allowed to peg the instance. Stays
# far under gunicorn's --timeout 120 so the request dies gracefully first.
EXTRACT_TIMEOUT_SECONDS = int(os.getenv("EXTRACT_TIMEOUT_SECONDS", "90"))

# The child aborts itself past this allocation instead of dragging the whole
# instance over Render's memory limit (512MB on starter, shared with gunicorn).
_CHILD_MEMORY_CAP_BYTES = 384 * 1024 * 1024

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class ExtractionTooHeavy(ValueError):
    """The document could not be processed within the safety limits."""


def extract_pdf_text(content: bytes) -> str:
    return _run_child("pdf", content)


def extract_docx_text(content: bytes) -> str:
    return _run_child("docx", content)


def _run_child(kind: str, content: bytes) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "student.extraction", kind],
            input=content,
            capture_output=True,
            timeout=EXTRACT_TIMEOUT_SECONDS,
            cwd=_REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        raise ExtractionTooHeavy(
            "El archivo es demasiado pesado para procesarlo. "
            "Divídelo en partes más pequeñas e inténtalo de nuevo."
        )
    if proc.returncode != 0:
        # Killed by the memory cap, or a crash. Either way the instance is
        # fine, which is the point; tell the student what to do about it.
        raise ExtractionTooHeavy(
            "El archivo es demasiado pesado para procesarlo. "
            "Divídelo en partes más pequeñas e inténtalo de nuevo."
        )

    verdict = json.loads(proc.stdout.decode("utf-8"))
    if verdict.get("ok"):
        return verdict.get("text", "")
    if verdict.get("kind") == "no_text_layer":
        raise NoTextLayer(verdict.get("message", ""))
    raise ValueError(verdict.get("message", "Could not extract text"))


def main(argv: list[str]) -> int:
    """Child-process entry: stdin document → stdout JSON verdict."""
    if sys.platform != "win32":  # local dev is Windows; Render is Linux
        try:
            import resource

            resource.setrlimit(
                resource.RLIMIT_AS, (_CHILD_MEMORY_CAP_BYTES, _CHILD_MEMORY_CAP_BYTES)
            )
        except (ImportError, ValueError, OSError):
            pass  # the limit cannot shrink — proceed uncapped rather than fail

    kind = argv[0] if argv else ""
    content = sys.stdin.buffer.read()
    from student.canvas import extract_text_from_docx, extract_text_from_pdf

    try:
        if kind == "pdf":
            text = extract_text_from_pdf(content)
        elif kind == "docx":
            text = extract_text_from_docx(content)
        else:
            raise ValueError(f"unknown document kind {kind!r}")
    except NoTextLayer as exc:
        print(json.dumps({"ok": False, "kind": "no_text_layer", "message": str(exc)}))
        return 0
    except Exception as exc:  # surfaced to the student by the parent
        print(json.dumps({"ok": False, "kind": "error", "message": str(exc)[:300]}))
        return 0
    print(json.dumps({"ok": True, "text": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
