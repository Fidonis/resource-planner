"""§4 rules 1 to 4 and acceptance cases 1 to 4."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from planner_api.domain.calendar import iso_week, monday_of, week_starts, workdays
from planner_api.domain.capacity import (
    AbsenceSpan,
    absent_days,
    absent_fraction,
    capacity,
    covers_half,
    present_days,
    working_days_in,
)
from planner_api.domain.planning import MemberIn, member_week

WEEK = date(2026, 3, 2)  # a Monday
ANNA = uuid.uuid4()
TYPE = uuid.uuid4()


def span(
    start: date,
    end: date,
    *,
    member: uuid.UUID | None = ANNA,
    start_part: str = "FULL",
    end_part: str = "FULL",
    reduces: bool = True,
) -> AbsenceSpan:
    return AbsenceSpan(
        member_id=member,
        type_id=TYPE,
        start=start,
        end=end,
        start_part=start_part,
        end_part=end_part,
        reduces_capacity=reduces,
    )


def member(allocation: int = 80) -> MemberIn:
    return MemberIn(
        id=ANNA,
        team_id=uuid.uuid4(),
        name="Anna",
        allocation_percent=allocation,
        active=True,
        skill_ids=frozenset(),
    )


def test_week_helpers() -> None:
    assert monday_of(date(2026, 3, 6)) == WEEK
    assert week_starts(date(2026, 3, 4), 3) == [WEEK, date(2026, 3, 9), date(2026, 3, 16)]
    assert [d.weekday() for d in workdays(WEEK)] == [0, 1, 2, 3, 4]
    assert iso_week(WEEK) == 10


def test_no_absence_gives_allocation_share_of_five_days() -> None:
    week = member_week(member(80), WEEK, [], Decimal(0))
    assert week.present_days == Decimal(5)
    assert week.capacity == Decimal("4.00")


def test_acceptance_1_full_week_of_vacation() -> None:
    """Anna (80 %) is away Monday to Friday: capacity 0, five absent days."""
    week = member_week(member(80), WEEK, [span(WEEK, date(2026, 3, 6))], Decimal(0))
    assert week.capacity == Decimal("0.00")
    assert week.absent_days == Decimal(5)


def test_acceptance_2_half_day() -> None:
    """Anna (100 %) with a morning off has 4.5 days in that week."""
    week = member_week(
        member(100), WEEK, [span(WEEK, WEEK, start_part="AM", end_part="AM")], Decimal(0)
    )
    assert week.capacity == Decimal("4.50")
    assert week.absent_days == Decimal("0.5")


def test_acceptance_3_company_holiday_hits_every_member() -> None:
    holiday = span(date(2026, 3, 5), date(2026, 3, 5), member=None)
    for other in (uuid.uuid4(), ANNA):
        m = MemberIn(other, uuid.uuid4(), "x", 100, True, frozenset())
        assert member_week(m, WEEK, [holiday], Decimal(0)).capacity == Decimal("4.00")


def test_acceptance_4_type_without_capacity_effect_is_ignored() -> None:
    on_call = span(WEEK, date(2026, 3, 6), reduces=False)
    week = member_week(member(100), WEEK, [on_call], Decimal(0))
    assert week.capacity == Decimal("5.00")
    assert week.absent_days == Decimal(0)


def test_morning_and_afternoon_on_the_same_day_make_a_whole_day() -> None:
    am = span(WEEK, WEEK, start_part="AM", end_part="AM")
    pm = span(WEEK, WEEK, start_part="PM", end_part="PM")
    assert absent_fraction([am, pm], WEEK) == Decimal(1)


def test_two_absences_on_the_same_morning_count_once() -> None:
    am = span(WEEK, WEEK, start_part="AM", end_part="AM")
    assert absent_fraction([am, am], WEEK) == Decimal("0.5")


def test_overlapping_full_and_half_day_is_one_day() -> None:
    assert (
        absent_fraction([span(WEEK, WEEK), span(WEEK, WEEK, start_part="PM", end_part="PM")], WEEK)
        == 1
    )


def test_first_and_last_day_parts_apply_only_to_those_days() -> None:
    # Wednesday afternoon to Friday morning: 0.5 + 1 + 0.5 days.
    trip = span(date(2026, 3, 4), date(2026, 3, 6), start_part="PM", end_part="AM")
    assert absent_days([trip], WEEK) == Decimal(2)
    assert present_days([trip], WEEK) == Decimal(3)


def test_weekend_never_counts() -> None:
    weekend = span(date(2026, 3, 7), date(2026, 3, 8))
    assert absent_days([weekend], WEEK) == Decimal(0)


def test_absence_outside_the_week_is_ignored() -> None:
    assert absent_days([span(date(2026, 3, 9), date(2026, 3, 13))], WEEK) == Decimal(0)


def test_absence_of_another_member_is_ignored() -> None:
    other = span(WEEK, date(2026, 3, 6), member=uuid.uuid4())
    assert member_week(member(100), WEEK, [other], Decimal(0)).capacity == Decimal("5.00")


@pytest.mark.parametrize(
    ("present", "percent", "expected"),
    [
        ("5", 80, "4.00"),
        ("4.5", 100, "4.50"),
        ("3", 50, "1.50"),
        ("4", 33, "1.32"),
        ("0", 80, "0.00"),
    ],
)
def test_capacity_rounds_to_two_decimals(present: str, percent: int, expected: str) -> None:
    assert capacity(Decimal(present), percent) == Decimal(expected)


def test_working_days_of_a_listed_absence() -> None:
    # Friday afternoon to Tuesday morning: 0.5 (Fri) + 0 (weekend) + 1 (Mon) + 0.5 (Tue).
    s = span(date(2026, 3, 6), date(2026, 3, 10), start_part="PM", end_part="AM")
    assert working_days_in(s) == Decimal(2)


def test_covers_half_tells_the_morning_from_the_afternoon() -> None:
    monday, tuesday, wednesday = WEEK, date(2026, 3, 3), date(2026, 3, 4)
    morning = span(monday, monday, start_part="AM", end_part="AM")
    assert covers_half(morning, monday, "AM") and not covers_half(morning, monday, "PM")
    afternoon = span(monday, monday, start_part="PM", end_part="PM")
    assert covers_half(afternoon, monday, "PM") and not covers_half(afternoon, monday, "AM")
    # Monday afternoon to Wednesday morning: the days in between are whole days
    several = span(monday, wednesday, start_part="PM", end_part="AM")
    assert not covers_half(several, monday, "AM") and covers_half(several, monday, "PM")
    assert covers_half(several, tuesday, "AM") and covers_half(several, tuesday, "PM")
    assert covers_half(several, wednesday, "AM") and not covers_half(several, wednesday, "PM")
    assert not covers_half(several, date(2026, 3, 5), "AM"), "after the span"


def test_covers_half_ignores_weekends() -> None:
    saturday = date(2026, 3, 7)
    whole = span(WEEK, date(2026, 3, 8))
    assert not covers_half(whole, saturday, "AM") and not covers_half(whole, saturday, "PM")


def test_covers_half_shows_a_type_that_costs_no_capacity_too() -> None:
    info = span(WEEK, WEEK, reduces=False)
    assert covers_half(info, WEEK, "AM") and covers_half(info, WEEK, "PM")
    assert absent_fraction([info], WEEK) == Decimal(0)
