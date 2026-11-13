"""Absences and capacity (§4 rules 1 to 4). Pure functions, no I/O."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from planner_api.domain.calendar import (
    HALF,
    WORKING_DAYS_PER_WEEK,
    ZERO,
    is_workday,
    round2,
    workdays,
)

FULL = "FULL"
AM = "AM"
PM = "PM"


@dataclass(frozen=True, slots=True)
class AbsenceSpan:
    """One absence as the calculation sees it."""

    member_id: uuid.UUID | None  # None applies to every member
    type_id: uuid.UUID
    start: date
    end: date
    start_part: str
    end_part: str
    reduces_capacity: bool
    id: uuid.UUID | None = None

    def applies_to(self, member_id: uuid.UUID) -> bool:
        return self.member_id is None or self.member_id == member_id

    def part_on(self, day: date) -> str | None:
        """Which part of ``day`` the absence covers, or ``None`` when it does not touch it."""
        if day < self.start or day > self.end:
            return None
        if self.start == self.end or day == self.start:
            return self.start_part
        if day == self.end:
            return self.end_part
        return FULL


def covers_half(span: AbsenceSpan, day: date, half: str) -> bool:
    """Whether the absence takes the morning (``AM``) or the afternoon (``PM``) of ``day``."""
    if not is_workday(day):
        return False
    part = span.part_on(day)
    return part in (FULL, half)


def absent_fraction(spans: Iterable[AbsenceSpan], day: date) -> Decimal:
    """Share of ``day`` on which the member is away.

    The half-day slots of all matching absences are united: two absences on the same morning
    count once, and a morning plus an afternoon make a whole day. Absences of types that do not
    reduce capacity are shown elsewhere but ignored here.
    """
    if not is_workday(day):
        return ZERO
    morning = afternoon = False
    for span in spans:
        if not span.reduces_capacity:
            continue
        part = span.part_on(day)
        if part is None:
            continue
        if part in (FULL, AM):
            morning = True
        if part in (FULL, PM):
            afternoon = True
    return (HALF if morning else ZERO) + (HALF if afternoon else ZERO)


def absent_days(spans: Iterable[AbsenceSpan], week_start: date) -> Decimal:
    span_list = list(spans)
    return sum((absent_fraction(span_list, day) for day in workdays(week_start)), ZERO)


def present_days(spans: Iterable[AbsenceSpan], week_start: date) -> Decimal:
    return Decimal(WORKING_DAYS_PER_WEEK) - absent_days(spans, week_start)


def capacity(present: Decimal, allocation_percent: int) -> Decimal:
    """Available person-days of a member in a week."""
    return round2(present * Decimal(allocation_percent) / Decimal(100))


def working_days_in(span: AbsenceSpan) -> Decimal:
    """Working days an absence covers, counting a half day as 0.5 (for list views)."""
    total = ZERO
    day = span.start
    while day <= span.end:
        if is_workday(day):
            part = span.part_on(day)
            total += HALF if part in (AM, PM) else Decimal(1)
        day = date.fromordinal(day.toordinal() + 1)
    return total
