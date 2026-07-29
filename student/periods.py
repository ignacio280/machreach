"""Canonical UTC clocks and user-local calendar boundaries.

All persisted instants are UTC-aware. Calendar concepts (day/week/month) are
derived from the user's saved IANA timezone and converted back to UTC for
database queries.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
from typing import Iterator, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/Santiago"


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FrozenClock:
    instant: datetime

    def now(self) -> datetime:
        if self.instant.tzinfo is None:
            raise ValueError("FrozenClock requires an aware datetime")
        return self.instant.astimezone(UTC)


_clock: ContextVar[Clock] = ContextVar("machreach_clock", default=SystemClock())


@contextmanager
def use_clock(clock: Clock) -> Iterator[None]:
    """Temporarily inject a clock for deterministic boundary/worker tests."""
    token = _clock.set(clock)
    try:
        yield
    finally:
        _clock.reset(token)


def utc_now(clock: Clock | None = None) -> datetime:
    now = (clock or _clock.get()).now()
    if now.tzinfo is None:
        raise ValueError("Clock returned a naive datetime")
    return now.astimezone(UTC)


def normalize_timezone(name: str | None) -> str:
    candidate = str(name or "").strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE
    return candidate


def get_user_timezone(client_id: int, db=None) -> str:
    """Read the normalized timezone table, with legacy/country fallback."""
    from machreach_core.db import _fetchone, get_db
    from student.timezones import tz_for_country

    def _read(conn) -> str:
        try:
            row = _fetchone(
                conn,
                "SELECT ep.timezone, c.country_iso, c.mail_preferences "
                "FROM clients c LEFT JOIN student_email_prefs ep "
                "ON ep.client_id = c.id WHERE c.id = %s",
                (client_id,),
            )
        except Exception:
            row = _fetchone(
                conn,
                "SELECT country_iso, mail_preferences FROM clients WHERE id = %s",
                (client_id,),
            )
        if not row:
            return DEFAULT_TIMEZONE
        if row.get("timezone"):
            return normalize_timezone(row["timezone"])
        try:
            legacy = json.loads(row.get("mail_preferences") or "{}")
        except (TypeError, ValueError):
            legacy = {}
        if isinstance(legacy, dict) and legacy.get("timezone"):
            return normalize_timezone(legacy["timezone"])
        return normalize_timezone(tz_for_country(row.get("country_iso") or "CL"))

    if db is not None:
        return _read(db)
    with get_db() as conn:
        return _read(conn)


def local_now(
    client_id: int,
    *,
    at: datetime | None = None,
    timezone_name: str | None = None,
    db=None,
) -> datetime:
    instant = at or utc_now()
    if instant.tzinfo is None:
        raise ValueError("Boundary instants must be timezone-aware")
    tz_name = normalize_timezone(
        timezone_name or get_user_timezone(client_id, db=db)
    )
    return instant.astimezone(ZoneInfo(tz_name))


def user_date(
    client_id: int,
    *,
    at: datetime | None = None,
    timezone_name: str | None = None,
    db=None,
) -> date:
    return local_now(
        client_id, at=at, timezone_name=timezone_name, db=db
    ).date()


def _local_midnight(day: date, timezone_name: str) -> datetime:
    return datetime.combine(day, time.min, ZoneInfo(normalize_timezone(timezone_name)))


def day_window_utc(
    client_id: int,
    *,
    day: date | None = None,
    at: datetime | None = None,
    timezone_name: str | None = None,
    db=None,
) -> tuple[datetime, datetime]:
    tz_name = normalize_timezone(
        timezone_name or get_user_timezone(client_id, db=db)
    )
    local_day = day or user_date(
        client_id, at=at, timezone_name=tz_name, db=db
    )
    start = _local_midnight(local_day, tz_name)
    end = _local_midnight(local_day + timedelta(days=1), tz_name)
    return start.astimezone(UTC), end.astimezone(UTC)


def week_window_utc(
    client_id: int,
    *,
    day: date | None = None,
    at: datetime | None = None,
    timezone_name: str | None = None,
    db=None,
) -> tuple[datetime, datetime]:
    tz_name = normalize_timezone(
        timezone_name or get_user_timezone(client_id, db=db)
    )
    local_day = day or user_date(
        client_id, at=at, timezone_name=tz_name, db=db
    )
    monday = local_day - timedelta(days=local_day.weekday())
    start = _local_midnight(monday, tz_name)
    end = _local_midnight(monday + timedelta(days=7), tz_name)
    return start.astimezone(UTC), end.astimezone(UTC)


def month_window_utc(
    client_id: int,
    *,
    day: date | None = None,
    at: datetime | None = None,
    timezone_name: str | None = None,
    db=None,
) -> tuple[datetime, datetime]:
    tz_name = normalize_timezone(
        timezone_name or get_user_timezone(client_id, db=db)
    )
    local_day = day or user_date(
        client_id, at=at, timezone_name=tz_name, db=db
    )
    start_day = local_day.replace(day=1)
    end_day = (
        date(start_day.year + 1, 1, 1)
        if start_day.month == 12
        else date(start_day.year, start_day.month + 1, 1)
    )
    return (
        _local_midnight(start_day, tz_name).astimezone(UTC),
        _local_midnight(end_day, tz_name).astimezone(UTC),
    )


def iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def zoned_date(timezone_name: str, *, at: datetime | None = None) -> date:
    instant = at or utc_now()
    if instant.tzinfo is None:
        raise ValueError("Boundary instants must be timezone-aware")
    return instant.astimezone(ZoneInfo(normalize_timezone(timezone_name))).date()


def leaderboard_period_key(kind: str, reference: date) -> str:
    if kind == "weekly":
        return iso_week_key(reference)
    if kind == "monthly":
        return month_key(reference)
    raise ValueError(f"Unsupported period kind: {kind}")


def leaderboard_period_window(kind: str, key: str) -> tuple[date, date]:
    if kind == "weekly":
        year_text, week_text = key.split("-W", 1)
        start = date.fromisocalendar(int(year_text), int(week_text), 1)
        return start, start + timedelta(days=7)
    if kind == "monthly":
        year, month = (int(part) for part in key.split("-", 1))
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, end
    raise ValueError(f"Unsupported period kind: {kind}")


def latest_closed_leaderboard_period(kind: str, as_of: date) -> str:
    if kind == "weekly":
        return leaderboard_period_key("weekly", as_of - timedelta(days=as_of.weekday() + 1))
    if kind == "monthly":
        return leaderboard_period_key("monthly", as_of.replace(day=1) - timedelta(days=1))
    raise ValueError(f"Unsupported period kind: {kind}")


def next_leaderboard_period(kind: str, key: str) -> str:
    _, end = leaderboard_period_window(kind, key)
    return leaderboard_period_key(kind, end)
