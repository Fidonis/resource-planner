"""Plain rows for the planning calculations: members, absences, bookings, efforts."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from planner_api.domain.calendar import ZERO
from planner_api.domain.capacity import AbsenceSpan
from planner_api.domain.planning import (
    AssignmentIn,
    MemberIn,
    MemberWeek,
    booked_index,
    member_week,
)
from planner_api.models import (
    Absence,
    AbsenceType,
    Assignment,
    Member,
    MemberSkill,
    Skill,
    Task,
    TaskEffort,
)


@dataclass(frozen=True, slots=True)
class TaskRow:
    id: uuid.UUID
    version: int
    key: str
    title: str
    status: str
    owner_team_id: uuid.UUID | None
    efforts: dict[uuid.UUID, Decimal]  # skill id -> person-days


@dataclass(frozen=True, slots=True)
class ChipRow:
    """One assignment as the board draws it."""

    id: uuid.UUID
    version: int
    task_id: uuid.UUID
    task_key: str
    task_title: str
    task_status: str
    skill_id: uuid.UUID
    skill_key: str
    member_id: uuid.UUID
    week_start: date
    person_days: Decimal

    def as_assignment(self) -> AssignmentIn:
        return AssignmentIn(
            self.id,
            self.task_id,
            self.skill_id,
            self.member_id,
            self.week_start,
            self.person_days,
            self.task_status,
        )


async def load_members(
    session: AsyncSession,
    *,
    member_ids: Collection[uuid.UUID] | None = None,
    team_id: uuid.UUID | None = None,
    skill_id: uuid.UUID | None = None,
    active_only: bool = False,
) -> list[MemberIn]:
    """Members with their skills, by name. Two queries however many members there are."""
    conditions: list[ColumnElement[bool]] = []
    if member_ids is not None:
        conditions.append(Member.id.in_(member_ids))
    if team_id is not None:
        conditions.append(Member.team_id == team_id)
    if skill_id is not None:
        conditions.append(
            Member.id.in_(select(MemberSkill.member_id).where(MemberSkill.skill_id == skill_id))
        )
    if active_only:
        conditions.append(Member.active.is_(True))
    rows = (
        await session.execute(
            select(
                Member.id,
                Member.team_id,
                Member.display_name,
                Member.allocation_percent,
                Member.active,
            )
            .where(*conditions)
            .order_by(func.lower(Member.display_name), Member.id)
        )
    ).all()
    skills: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if rows:
        links = await session.execute(
            select(MemberSkill.member_id, MemberSkill.skill_id).where(
                MemberSkill.member_id.in_(select(Member.id).where(*conditions))
            )
        )
        for member_id, link_skill in links.all():
            skills[member_id].add(link_skill)
    return [
        MemberIn(
            r.id, r.team_id, r.display_name, r.allocation_percent, r.active, frozenset(skills[r.id])
        )
        for r in rows
    ]


async def load_spans(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    member_ids: Collection[uuid.UUID] | None = None,
) -> list[AbsenceSpan]:
    """Absences that touch ``start`` to ``end``: those of the members and the company-wide ones."""
    stmt = (
        select(
            Absence.id,
            Absence.member_id,
            Absence.absence_type_id,
            Absence.start_date,
            Absence.end_date,
            Absence.start_part,
            Absence.end_part,
            AbsenceType.reduces_capacity,
        )
        .join(AbsenceType, AbsenceType.id == Absence.absence_type_id)
        .where(Absence.start_date <= end, Absence.end_date >= start)
    )
    if member_ids is not None:
        stmt = stmt.where(Absence.member_id.is_(None) | Absence.member_id.in_(member_ids))
    rows = (await session.execute(stmt)).all()
    return [
        AbsenceSpan(
            member_id=r.member_id,
            type_id=r.absence_type_id,
            start=r.start_date,
            end=r.end_date,
            start_part=r.start_part,
            end_part=r.end_part,
            reduces_capacity=r.reduces_capacity,
            id=r.id,
        )
        for r in rows
    ]


async def load_assignments(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    member_ids: Collection[uuid.UUID] | None = None,
    task_ids: Collection[uuid.UUID] | None = None,
    with_cancelled: bool = False,
) -> list[AssignmentIn]:
    stmt = select(
        Assignment.id,
        Assignment.task_id,
        Assignment.skill_id,
        Assignment.member_id,
        Assignment.week_start,
        Assignment.person_days,
        Task.status,
    ).join(Task, Task.id == Assignment.task_id)
    if start is not None:
        stmt = stmt.where(Assignment.week_start >= start)
    if end is not None:
        stmt = stmt.where(Assignment.week_start <= end)
    if member_ids is not None:
        stmt = stmt.where(Assignment.member_id.in_(member_ids))
    if task_ids is not None:
        stmt = stmt.where(Assignment.task_id.in_(task_ids))
    if not with_cancelled:
        stmt = stmt.where(Task.status != "cancelled")
    rows = (await session.execute(stmt)).all()
    return [
        AssignmentIn(
            r.id, r.task_id, r.skill_id, r.member_id, r.week_start, r.person_days, r.status
        )
        for r in rows
    ]


async def load_chips(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    member_ids: Collection[uuid.UUID] | None = None,
) -> list[ChipRow]:
    """Assignments of the weeks ``start`` to ``end`` with the names the board shows."""
    stmt = (
        select(
            Assignment.id,
            Assignment.version,
            Assignment.task_id,
            Task.key.label("task_key"),
            Task.title.label("task_title"),
            Task.status.label("task_status"),
            Assignment.skill_id,
            Skill.key.label("skill_key"),
            Assignment.member_id,
            Assignment.week_start,
            Assignment.person_days,
        )
        .join(Task, Task.id == Assignment.task_id)
        .join(Skill, Skill.id == Assignment.skill_id)
        .where(
            Assignment.week_start >= start,
            Assignment.week_start <= end,
            Task.status != "cancelled",
        )
        .order_by(Task.key, Skill.key, Assignment.id)
    )
    if member_ids is not None:
        stmt = stmt.where(Assignment.member_id.in_(member_ids))
    return [ChipRow(*row) for row in (await session.execute(stmt)).all()]


async def assigned_totals(
    session: AsyncSession,
    *,
    task_ids: Collection[uuid.UUID] | None = None,
    open_only: bool = False,
) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    """Person-days assigned per (task, skill) over all weeks."""
    stmt = select(
        Assignment.task_id, Assignment.skill_id, func.sum(Assignment.person_days)
    ).group_by(Assignment.task_id, Assignment.skill_id)
    if open_only:
        stmt = stmt.join(Task, Task.id == Assignment.task_id).where(Task.status == "open")
    if task_ids is not None:
        stmt = stmt.where(Assignment.task_id.in_(task_ids))
    return {(t, s): total for t, s, total in (await session.execute(stmt)).all()}


async def load_tasks(
    session: AsyncSession,
    *,
    task_ids: Collection[uuid.UUID] | None = None,
    status: str | None = None,
    owner_team_id: uuid.UUID | None = None,
    skill_id: uuid.UUID | None = None,
) -> list[TaskRow]:
    conditions: list[ColumnElement[bool]] = []
    if task_ids is not None:
        conditions.append(Task.id.in_(task_ids))
    if status is not None:
        conditions.append(Task.status == status)
    if owner_team_id is not None:
        conditions.append(Task.owner_team_id == owner_team_id)
    if skill_id is not None:
        conditions.append(
            Task.id.in_(select(TaskEffort.task_id).where(TaskEffort.skill_id == skill_id))
        )
    tasks = (
        await session.execute(
            select(Task.id, Task.version, Task.key, Task.title, Task.status, Task.owner_team_id)
            .where(*conditions)
            .order_by(Task.key, Task.id)
        )
    ).all()
    efforts: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = defaultdict(dict)
    if tasks:
        rows = await session.execute(
            select(TaskEffort.task_id, TaskEffort.skill_id, TaskEffort.person_days).where(
                TaskEffort.task_id.in_(select(Task.id).where(*conditions))
            )
        )
        for task_id, effort_skill, person_days in rows.all():
            efforts[task_id][effort_skill] = person_days
    return [
        TaskRow(t.id, t.version, t.key, t.title, t.status, t.owner_team_id, efforts[t.id])
        for t in tasks
    ]


async def skill_refs(
    session: AsyncSession, skill_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, tuple[str, str]]:
    """Skill id -> (key, name)."""
    stmt = select(Skill.id, Skill.key, Skill.name)
    if skill_ids is not None:
        stmt = stmt.where(Skill.id.in_(skill_ids))
    return {i: (key, name) for i, key, name in (await session.execute(stmt)).all()}


async def member_weeks_for(
    session: AsyncSession, pairs: Sequence[tuple[uuid.UUID, date]]
) -> list[tuple[uuid.UUID, MemberWeek]]:
    """Capacity figures for the given (member, week) pairs, as the database holds them now."""
    unique = list(dict.fromkeys(pairs))
    if not unique:
        return []
    member_ids = {member_id for member_id, _ in unique}
    first = min(week for _, week in unique)
    last = max(week for _, week in unique)
    members = {m.id: m for m in await load_members(session, member_ids=member_ids)}
    spans = await load_spans(session, first, last + timedelta(days=6), member_ids=member_ids)
    booked = booked_index(
        await load_assignments(session, start=first, end=last, member_ids=member_ids)
    )
    return [
        (
            member_id,
            member_week(members[member_id], week, spans, booked.get((member_id, week), ZERO)),
        )
        for member_id, week in unique
        if member_id in members
    ]


async def booked_pairs(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    member_ids: Collection[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, date]]:
    """(member, week) pairs that hold work between the two dates, cancelled tasks aside."""
    stmt = (
        select(Assignment.member_id, Assignment.week_start)
        .join(Task, Task.id == Assignment.task_id)
        .where(
            Assignment.week_start >= start,
            Assignment.week_start <= end,
            Task.status != "cancelled",
        )
        .group_by(Assignment.member_id, Assignment.week_start)
        .order_by(Assignment.week_start, Assignment.member_id)
    )
    if member_ids is not None:
        stmt = stmt.where(Assignment.member_id.in_(member_ids))
    return [(m, w) for m, w in (await session.execute(stmt)).all()]
