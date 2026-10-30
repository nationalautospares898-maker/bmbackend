"""Injectable clock.

Every business timestamp comes from here, never from a scattered `datetime.now()`
(`ARCHITECTURE.md §6.4`). That is what makes credit ageing, cancellation windows,
financial-year rollover and tax effective dates testable with a frozen clock instead of
a sleep.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """The only time source the domain is allowed to use."""

    def now(self) -> datetime:
        """Current instant, always timezone-aware."""
        ...


class SystemClock:
    """Production clock. UTC-aware; business-local conversion is explicit."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Test clock. Deliberately mutable so a test can step time forward."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._at = at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._at = at

    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        from datetime import timedelta

        self._at = self._at + timedelta(seconds=seconds, days=days)


def business_date(moment: datetime, timezone: str) -> date:
    """The business-local calendar date of an instant.

    T3-02: report day boundaries and the financial-year boundary are evaluated in
    business-local time, not UTC. A sale at 23:59 local on 31 March and one at 00:01 on
    1 April must land in different financial years, which is false if you truncate UTC.
    """
    return moment.astimezone(ZoneInfo(timezone)).date()


def financial_year(moment: datetime, timezone: str, start_month: int) -> str:
    """Financial-year label such as `2026-27`, evaluated in business-local time.

    With `start_month = 4` (India's April start), 2026-03-31 belongs to `2025-26` and
    2026-04-01 to `2026-27`.
    """
    if not 1 <= start_month <= 12:
        raise ValueError("financial_year_start_month must be between 1 and 12")
    local = business_date(moment, timezone)
    start_year = local.year if local.month >= start_month else local.year - 1
    if start_month == 1:
        return str(start_year)
    return f"{start_year}-{(start_year + 1) % 100:02d}"
