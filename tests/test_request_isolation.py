"""The web process must never do unbounded work inside a request.

Every "server failure" mail since May is one property violated: /health shares
eight threads and one GIL with whatever requests are doing. These tests pin the
two escapes — document extraction in a killable child process, and a cap on how
many threads may sit in an inline model call.
"""
import pytest

from pdf_fixtures import STUDY_MATERIAL, scanned_pdf, text_pdf
from student import ai_gate, extraction
from student.canvas import NoTextLayer


def test_extraction_happens_in_a_child_process_and_returns_the_text():
    result = extraction.extract_pdf_text(text_pdf([STUDY_MATERIAL[:70], STUDY_MATERIAL[70:140]]))

    assert "Carnot cycle" in result
    assert "--- PAGE 2 of 2 ---" in result


def test_a_scan_is_still_refused_through_the_child_process():
    with pytest.raises(NoTextLayer):
        extraction.extract_pdf_text(scanned_pdf(pages=3))


def test_a_document_that_blows_the_time_budget_kills_the_child_not_the_instance(monkeypatch):
    """The old failure mode: pdfminer pegging the web process for minutes.

    The instance answering /health must never be the thing doing that work, so
    past the deadline the child dies and the student gets an actionable error.
    """
    monkeypatch.setattr(extraction, "EXTRACT_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(extraction.ExtractionTooHeavy):
        extraction.extract_pdf_text(text_pdf([STUDY_MATERIAL[:70]]))


def test_inline_ai_slots_refuse_the_caller_past_the_cap_and_recover():
    """Eight threads all waiting on a model left none to answer /health."""
    held = []
    try:
        for _ in range(ai_gate._MAX_SLOTS):
            slot = ai_gate.inline_ai_slot()
            slot.__enter__()
            held.append(slot)

        with pytest.raises(ai_gate.InlineAIBusy):
            with ai_gate.inline_ai_slot():
                pass
    finally:
        for slot in held:
            slot.__exit__(None, None, None)

    # Released slots are usable again — the refusal is congestion, not a latch.
    with ai_gate.inline_ai_slot():
        pass


def test_extract_file_endpoint_still_speaks_no_text_layer_through_the_child(
    client, flask_app, make_user, monkeypatch
):
    from io import BytesIO

    from machreach_core.db import _exec, get_db

    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    client_id = make_user("Isolated Extraction")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Isolated Extraction"
        session["account_type"] = "student"
        session["session_version"] = 0

    scanned = client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(scanned_pdf(pages=4)), "apuntes.pdf")},
        content_type="multipart/form-data",
    )
    assert scanned.status_code == 400
    assert scanned.get_json()["no_text_layer"] is True

    readable = client.post(
        "/api/student/extract-file",
        data={"file": (BytesIO(text_pdf([STUDY_MATERIAL[:70]])), "apuntes.pdf")},
        content_type="multipart/form-data",
    )
    assert readable.status_code == 200
    assert "Carnot" in readable.get_json()["text"]
