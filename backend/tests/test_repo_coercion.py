"""Unit tests for repository parameter coercion (asyncpg type strictness).

Pydantic serializes dates/times to ISO strings; PostgreSQL via asyncpg
rejects strings for timestamptz/date/time columns. Repositories must convert
at the boundary — these tests pin that contract without a live database.
"""

import pytest

from app.repositories.coerce import as_date, as_datetime, as_time


class TestAsDatetime:
    def test_parses_iso_string_with_offset(self) -> None:
        value = as_datetime("2026-11-15T23:59:00+07:00")
        assert value is not None
        assert value.year == 2026 and value.hour == 23

    def test_parses_z_suffix(self) -> None:
        value = as_datetime("2026-11-15T09:00:00Z")
        assert value is not None and value.tzinfo is not None

    def test_passthrough_and_none(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        assert as_datetime(now) is now
        assert as_datetime(None) is None

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            as_datetime("not-a-date")
        with pytest.raises(ValueError):
            as_datetime(12345)


class TestAsDate:
    def test_truncates_datetime_string_to_date(self) -> None:
        from datetime import date

        assert as_date("2026-11-02T09:00:00+07:00") == date(2026, 11, 2)
        assert as_date("2026-11-02") == date(2026, 11, 2)

    def test_datetime_object_becomes_date(self) -> None:
        from datetime import date, datetime, timezone

        value = as_date(datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
        assert value == date(2026, 11, 2)

    def test_passthrough_and_none(self) -> None:
        from datetime import date as date_cls

        today = date_cls(2026, 11, 2)
        assert as_date(today) is today
        assert as_date(None) is None


class TestAsTime:
    def test_parses_hh_mm_and_hh_mm_ss(self) -> None:
        from datetime import time

        assert as_time("08:00") == time(8, 0)
        assert as_time("09:30:00") == time(9, 30)

    def test_passthrough_and_none(self) -> None:
        from datetime import time

        value = time(8, 0)
        assert as_time(value) is value
        assert as_time(None) is None

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            as_time("25:99")
