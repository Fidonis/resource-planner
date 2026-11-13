"""Weeks and working days. Weeks are identified by the ISO date of their Monday."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
HALF = Decimal("0.5")
ZERO = Decimal("0")
WORKING_DAYS_PER_WEEK = 5


def round2(value: Decimal) -> Decimal:
    """Person-days are stored with two decimals (§4 rule 11)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def is_monday(day: date) -> bool:
    return day.weekday() == 0


def iso_week(day: date) -> int:
    return day.isocalendar().week


def week_starts(start: date, weeks: int) -> list[date]:
    first = monday_of(start)
    return [first + timedelta(days=7 * offset) for offset in range(weeks)]


def workdays(week_start: date) -> list[date]:
    """Monday to Friday of the week that starts on ``week_start``."""
    return [week_start + timedelta(days=offset) for offset in range(WORKING_DAYS_PER_WEEK)]


def is_workday(day: date) -> bool:
    return day.weekday() < WORKING_DAYS_PER_WEEK
