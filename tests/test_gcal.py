from datetime import datetime, timedelta, timezone

from machreach_core import gcal
from machreach_core.db import _exec, get_db


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("provider failed")

    def json(self):
        return self.payload


def test_token_storage_reuses_refresh_token_and_returns_valid_access_token(make_user):
    client_id = make_user()
    gcal.save_tokens(client_id, "access-one", "refresh-one", 3600, "student@example.com", "calendar")
    gcal.save_tokens(client_id, "access-two", "", 3600, "student@example.com", "calendar")

    record = gcal.get_token_record(client_id)
    assert record["google_email"] == "student@example.com"
    assert gcal.decrypt_password(record["refresh_token"]) == "refresh-one"
    assert gcal._valid_access_token(client_id) == "access-two"


def test_expired_token_refreshes_and_persists(monkeypatch, make_user):
    client_id = make_user()
    gcal.save_tokens(client_id, "expired", "refresh", 60)
    with get_db() as db:
        _exec(db, "UPDATE google_calendar_tokens SET expires_at = %s WHERE client_id = %s",
              (datetime.now(timezone.utc) - timedelta(hours=1), client_id))
    monkeypatch.setattr(gcal, "refresh_access_token", lambda token: {"access_token": "fresh", "expires_in": 3600})

    assert gcal._valid_access_token(client_id) == "fresh"
    assert gcal._valid_access_token(999999) is None


def test_calendar_api_normalizes_events_and_handles_mutations(monkeypatch):
    monkeypatch.setattr(gcal, "_valid_access_token", lambda client_id: "token")
    event = {
        "id": "evt-1", "summary": "Exam", "start": {"date": "2026-08-01"},
        "end": {"date": "2026-08-02"}, "description": "x" * 800,
        "location": "Room 1", "htmlLink": "https://calendar/event",
        "attendees": [{"email": "a@example.com"}, {}],
    }
    monkeypatch.setattr(gcal.requests, "get", lambda *a, **k: FakeResponse({"items": [event]}))
    monkeypatch.setattr(gcal.requests, "post", lambda *a, **k: FakeResponse({"id": "created"}))
    monkeypatch.setattr(gcal.requests, "delete", lambda *a, **k: FakeResponse(status_code=204))

    events = gcal.list_events(1, days_ahead=0, max_results=999)
    assert events[0]["all_day"] is True
    assert events[0]["attendees"] == ["a@example.com"]
    assert len(events[0]["description"]) == 500
    created = gcal.create_event(1, "Study", "2026-08-01T10:00:00-04:00", "2026-08-01T11:00:00-04:00",
                                description="Review", location="Library", attendee_emails=["a@example.com"])
    assert created == {"id": "created"}
    assert gcal.delete_event(1, "evt-1") is True


def test_calendar_provider_failures_are_safe(monkeypatch):
    monkeypatch.setattr(gcal, "_valid_access_token", lambda client_id: None)
    assert gcal.list_events(1) == []
    assert gcal.create_event(1, "x", "start", "end") is None
    assert gcal.delete_event(1, "") is False

    monkeypatch.setattr(gcal, "_valid_access_token", lambda client_id: "token")
    def fail(*args, **kwargs):
        raise RuntimeError("network")
    monkeypatch.setattr(gcal.requests, "get", fail)
    monkeypatch.setattr(gcal.requests, "post", fail)
    monkeypatch.setattr(gcal.requests, "delete", fail)
    assert gcal.list_events(1) == []
    assert gcal.create_event(1, "x", "start", "end") is None
    assert gcal.delete_event(1, "evt") is False


def test_configuration_and_refresh_request(monkeypatch):
    monkeypatch.setattr(gcal, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(gcal, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(gcal, "GOOGLE_REDIRECT_URI", "https://example.com/callback")
    monkeypatch.setattr(gcal.requests, "post", lambda *a, **k: FakeResponse({"access_token": "new"}))
    assert gcal.is_configured() is True
    assert gcal.refresh_access_token("refresh") == {"access_token": "new"}
