"""Type coercion for repository parameters (asyncpg is strict about types).

Pydantic schemas serialize dates/times to ISO strings; PostgreSQL via asyncpg
rejects strings for timestamptz/date/time columns. Repositories convert at
the boundary with these helpers: None and already-typed values pass through,
ISO strings are parsed, anything else raises a clear ValueError.
"""

from datetime import date, datetime, time
from typing import Any


def as_datetime(value: Any) -> datetime | None:
    """Coerce an ISO-8601 string (or datetime) into a datetime."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"Expected ISO datetime string or datetime, got {type(value).__name__}.")


def as_date(value: Any) -> date | None:
    """Coerce an ISO-8601 string (or date/datetime) into a date."""
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise ValueError(f"Expected ISO date string or date, got {type(value).__name__}.")


def as_time(value: Any) -> time | None:
    """Coerce an HH:MM[:SS] string (or time) into a time."""
    if value is None or isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    raise ValueError(f"Expected ISO time string or time, got {type(value).__name__}.")
