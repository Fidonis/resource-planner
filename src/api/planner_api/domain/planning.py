"""Booking, effort status, pools and utilisation (§4 rules 5 to 10). Pure functions, no I/O.

Callers hand in plain values loaded from the database and get plain values back, so every
figure the clients show comes from here and nowhere else.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

from planner_api.domain.calendar import HALF, ZERO, round2
from planner_api.domain.capacity import AbsenceSpan, absent_days, capacity, present_days

CANCELLED = "cancelled"
OPEN = "open"

BOTTLENECK = "BOTTLENECK"
TIGHT = "TIGHT"
STATUS_OK = "OK"

TIGHT_FACTOR = Decimal("0.8")


@dataclass(frozen=True, slots=True)
class MemberIn:
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    allocation_percent: int
    active: bool
    skill_ids: frozenset[uuid.UUID]


@dataclass(frozen=True, slots=True)
class TaskIn:
    id: uuid.UUID
    status: str
    efforts: Mapping[uuid.UUID, Decimal]  # skill id -> person-days


@dataclass(frozen=True, slots=True)
class AssignmentIn:
    id: uuid.UUID
    task_id: uuid.UUID
    skill_id: uuid.UUID
    member_id: uuid.UUID
    week_start: date
    person_days: Decimal
    task_status: str = OPEN


@dataclass(frozen=True, slots=True)
class MemberWeek:
    week_start: date
    present_days: Decimal
    absent_days: Decimal
    capacity: Decimal
    booked: Decimal
    free: Decimal
    overbooked: bool
    full: bool


@dataclass(frozen=True, slots=True)
class TaskSkillStatus:
    skill_id: uuid.UUID
    effort: Decimal
    assigned: Decimal
    open: Decimal
    overplanned: bool


@dataclass(frozen=True, slots=True)
class SkillRow:
    skill_id: uuid.UUID
    demand: Decimal
    assigned: Decimal
    open: Decimal
    pool: Decimal
    pool_free: Decimal
    status: str


@dataclass(frozen=True, slots=True)
class TeamRow:
    team_id: uuid.UUID
    member_count: int
    capacity: Decimal
    booked: Decimal
    free: Decimal
    utilisation: Decimal  # booked / capacity, 0 when there is no capacity


@dataclass(frozen=True, slots=True)
class Kpis:
    demand: Decimal
    assigned: Decimal
    open: Decimal
    overbooked_member_weeks: int


@dataclass(frozen=True, slots=True)
class Overview:
    kpis: Kpis
    skills: list[SkillRow]
    teams: list[TeamRow]


@dataclass(frozen=True, slots=True)
class AvailabilityRow:
    member_id: uuid.UUID
    weeks: list[MemberWeek]
    total_capacity: Decimal
    total_booked: Decimal
    total_free: Decimal


def booked_index(assignments: Iterable[AssignmentIn]) -> dict[tuple[uuid.UUID, date], Decimal]:
    """Booked person-days per (member, week). Work on cancelled tasks is history, not load."""
    booked: dict[tuple[uuid.UUID, date], Decimal] = defaultdict(lambda: ZERO)
    for a in assignments:
        if a.task_status == CANCELLED:
            continue
        booked[(a.member_id, a.week_start)] += a.person_days
    return {key: round2(value) for key, value in booked.items()}


def assigned_index(
    assignments: Iterable[AssignmentIn],
) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    """Assigned person-days per (task, skill) over all weeks."""
    assigned: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(lambda: ZERO)
    for a in assignments:
        assigned[(a.task_id, a.skill_id)] += a.person_days
    return {key: round2(value) for key, value in assigned.items()}


def member_week(
    member: MemberIn, week_start: date, spans: Sequence[AbsenceSpan], booked: Decimal
) -> MemberWeek:
    own = [s for s in spans if s.applies_to(member.id)]
    absent = absent_days(own, week_start)
    present = present_days(own, week_start)
    cap = capacity(present, member.allocation_percent)
    booked = round2(booked)
    overbooked = booked > cap
    return MemberWeek(
        week_start=week_start,
        present_days=present,
        absent_days=absent,
        capacity=cap,
        booked=booked,
        free=round2(cap - booked),
        overbooked=overbooked,
        full=(not overbooked) and cap > ZERO and booked >= cap,
    )


def member_weeks(
    member: MemberIn,
    weeks: Sequence[date],
    spans: Sequence[AbsenceSpan],
    booked: Mapping[tuple[uuid.UUID, date], Decimal],
) -> list[MemberWeek]:
    return [member_week(member, w, spans, booked.get((member.id, w), ZERO)) for w in weeks]


def task_skill_status(skill_id: uuid.UUID, effort: Decimal, assigned: Decimal) -> TaskSkillStatus:
    """§4 rule 7. Over-planning is reported, never clipped (DM-06)."""
    return TaskSkillStatus(
        skill_id=skill_id,
        effort=effort,
        assigned=assigned,
        open=round2(effort - assigned),
        overplanned=assigned > effort,
    )


def planned_percent(pairs: Iterable[tuple[Decimal, Decimal]]) -> float:
    """Share of the effort that is assigned, 0 to 100, from ``(effort, assigned)`` pairs.

    Over-planning does not push it past 100: each pair counts at most its own effort.
    """
    effort_total = ZERO
    planned_total = ZERO
    for effort, assigned in pairs:
        effort_total += effort
        planned_total += min(effort, assigned)
    if effort_total <= ZERO:
        return 0.0
    return round(float(planned_total / effort_total * 100), 1)


def floor_half(value: Decimal) -> Decimal:
    """Round down to a multiple of 0.5 person-days."""
    return (value / HALF).to_integral_value(rounding=ROUND_DOWN) * HALF


def suggest_person_days(open_pd: Decimal, free_pd: Decimal) -> Decimal:
    """§4 rule 10: default amount for a drop onto a member-week.

    ``min(open, floor_half(free))``; when that is not positive, ``min(open, 1)``. A task skill
    that is already fully planned still gets half a day so the request has a valid amount.
    """
    amount = min(open_pd, floor_half(free_pd))
    if amount <= ZERO:
        amount = min(open_pd, Decimal(1))
    if amount <= ZERO:
        amount = HALF
    return round2(amount)


def bottleneck_status(open_pd: Decimal, pool_free: Decimal) -> str:
    """§4 rule 8. Nothing open is never a bottleneck, even when the pool is overbooked."""
    if open_pd <= ZERO:
        return STATUS_OK
    if open_pd > pool_free:
        return BOTTLENECK
    if open_pd > TIGHT_FACTOR * pool_free:
        return TIGHT
    return STATUS_OK


def build_overview(
    *,
    members: Sequence[MemberIn],
    team_ids: Sequence[uuid.UUID],
    skill_ids: Sequence[uuid.UUID],
    tasks: Sequence[TaskIn],
    assignments: Sequence[AssignmentIn],
    spans: Sequence[AbsenceSpan],
    weeks: Sequence[date],
    assigned_totals: Mapping[tuple[uuid.UUID, uuid.UUID], Decimal] | None = None,
) -> Overview:
    """KPI tiles, the table per skill and the table per team for a horizon.

    ``assignments`` book the members; they may be limited to the horizon. What is already
    assigned to a task skill counts over all weeks, so a caller that only loaded the horizon
    hands in ``assigned_totals`` (task, skill -> person-days) computed over every week.
    """
    active = [m for m in members if m.active]
    booked = booked_index(assignments)
    assigned = assigned_totals if assigned_totals is not None else assigned_index(assignments)
    grid = {m.id: member_weeks(m, weeks, spans, booked) for m in active}

    skill_rows: list[SkillRow] = []
    for skill_id in skill_ids:
        demand = assigned_sum = open_sum = ZERO
        for task in tasks:
            if task.status != OPEN or skill_id not in task.efforts:
                continue
            effort = task.efforts[skill_id]
            done = assigned.get((task.id, skill_id), ZERO)
            demand += effort
            assigned_sum += min(done, effort)
            open_sum += max(ZERO, effort - done)
        if demand == ZERO:
            continue
        having = [m for m in active if skill_id in m.skill_ids]
        pool = sum((w.capacity for m in having for w in grid[m.id]), ZERO)
        load = sum((w.booked for m in having for w in grid[m.id]), ZERO)
        skill_rows.append(
            SkillRow(
                skill_id=skill_id,
                demand=round2(demand),
                assigned=round2(assigned_sum),
                open=round2(open_sum),
                pool=round2(pool),
                pool_free=round2(pool - load),
                status=bottleneck_status(round2(open_sum), round2(pool - load)),
            )
        )

    team_rows: list[TeamRow] = []
    for team_id in team_ids:
        own = [m for m in active if m.team_id == team_id]
        cap = sum((w.capacity for m in own for w in grid[m.id]), ZERO)
        load = sum((w.booked for m in own for w in grid[m.id]), ZERO)
        team_rows.append(
            TeamRow(
                team_id=team_id,
                member_count=len(own),
                capacity=round2(cap),
                booked=round2(load),
                free=round2(cap - load),
                utilisation=round2(load / cap) if cap > ZERO else ZERO,
            )
        )

    demand_total = sum((r.demand for r in skill_rows), ZERO)
    assigned_total = sum((r.assigned for r in skill_rows), ZERO)
    overbooked = sum(1 for weeks_of in grid.values() for w in weeks_of if w.overbooked)
    return Overview(
        kpis=Kpis(
            demand=round2(demand_total),
            assigned=round2(assigned_total),
            open=round2(demand_total - assigned_total),
            overbooked_member_weeks=overbooked,
        ),
        skills=skill_rows,
        teams=team_rows,
    )


def build_availability(
    *,
    members: Sequence[MemberIn],
    skill_id: uuid.UUID,
    weeks: Sequence[date],
    spans: Sequence[AbsenceSpan],
    assignments: Sequence[AssignmentIn],
) -> list[AvailabilityRow]:
    """Active members with ``skill_id``, the ones with the most free capacity first."""
    booked = booked_index(assignments)
    rows: list[AvailabilityRow] = []
    for m in members:
        if not m.active or skill_id not in m.skill_ids:
            continue
        weeks_of = member_weeks(m, weeks, spans, booked)
        rows.append(
            AvailabilityRow(
                member_id=m.id,
                weeks=weeks_of,
                total_capacity=round2(sum((w.capacity for w in weeks_of), ZERO)),
                total_booked=round2(sum((w.booked for w in weeks_of), ZERO)),
                total_free=round2(sum((w.free for w in weeks_of), ZERO)),
            )
        )
    name = {m.id: m.name.lower() for m in members}
    rows.sort(key=lambda r: (-r.total_free, name[r.member_id]))
    return rows
