"""Database access for tasks, assignments and absences."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import date
from typing import Any

from sqlalchemy import Row, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from planner_api.errors import not_found
from planner_api.models import (
    Absence,
    AbsenceType,
    Assignment,
    Member,
    Skill,
    Task,
)
from planner_api.repositories.base import like, page_of, page_rows

# --- tasks -------------------------------------------------------------------------------


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    stmt = select(Task).where(Task.id == task_id).options(selectinload(Task.efforts))
    task = (await session.scalars(stmt)).one_or_none()
    if task is None:
        raise not_found("Task", task_id)
    return task


async def find_task(
    session: AsyncSession, key: str, external_source: str | None = None
) -> Task | None:
    stmt = (
        select(Task)
        .where(
            Task.key == key,
            Task.external_source.is_(None)
            if external_source is None
            else Task.external_source == external_source,
        )
        .options(selectinload(Task.efforts))
    )
    return (await session.scalars(stmt)).one_or_none()


async def list_tasks(
    session: AsyncSession,
    *,
    q: str | None,
    owner_team_id: uuid.UUID | None,
    status: str | None,
    skill_id: uuid.UUID | None,
    source: str | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[Task], int]:
    from planner_api.models import TaskEffort

    stmt = select(Task).options(selectinload(Task.efforts))
    if q:
        pattern = like(q)
        stmt = stmt.where(
            Task.key.ilike(pattern, escape="\\") | Task.title.ilike(pattern, escape="\\")
        )
    if owner_team_id is not None:
        stmt = stmt.where(Task.owner_team_id == owner_team_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if skill_id is not None:
        stmt = stmt.where(
            Task.id.in_(select(TaskEffort.task_id).where(TaskEffort.skill_id == skill_id))
        )
    if source == "manual":
        stmt = stmt.where(Task.external_source.is_(None))
    elif source == "external":
        stmt = stmt.where(Task.external_source.is_not(None))
    elif source:
        stmt = stmt.where(Task.external_source == source)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "key": Task.key,
            "title": func.lower(Task.title),
            "status": Task.status,
            "createdAt": Task.created_at,
            "updatedAt": Task.updated_at,
        },
        default_sort="key",
    )


async def assignment_count_for_task(session: AsyncSession, task_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Assignment).where(Assignment.task_id == task_id)
    return (await session.execute(stmt)).scalar_one()


async def find_skills_by_key(session: AsyncSession, keys: Collection[str]) -> dict[str, Skill]:
    """Skills by lower-cased key."""
    if not keys:
        return {}
    wanted = {k.lower() for k in keys}
    rows = await session.scalars(select(Skill).where(func.lower(Skill.key).in_(wanted)))
    return {skill.key.lower(): skill for skill in rows.all()}


# --- assignments -------------------------------------------------------------------------


async def get_assignment(
    session: AsyncSession, assignment_id: uuid.UUID, *, lock: bool = False
) -> Assignment:
    stmt = select(Assignment).where(Assignment.id == assignment_id)
    if lock:
        stmt = stmt.with_for_update()
    assignment = (await session.scalars(stmt)).one_or_none()
    if assignment is None:
        raise not_found("Assignment", assignment_id)
    return assignment


async def find_assignment(
    session: AsyncSession,
    task_id: uuid.UUID,
    skill_id: uuid.UUID,
    member_id: uuid.UUID,
    week_start: date,
) -> Assignment | None:
    stmt = (
        select(Assignment)
        .where(
            Assignment.task_id == task_id,
            Assignment.skill_id == skill_id,
            Assignment.member_id == member_id,
            Assignment.week_start == week_start,
        )
        .with_for_update()
    )
    return (await session.scalars(stmt)).one_or_none()


def assignment_select() -> Any:
    return (
        select(
            Assignment,
            Task.key.label("task_key"),
            Task.title.label("task_title"),
            Task.status.label("task_status"),
            Skill.key.label("skill_key"),
            Member.display_name.label("member_name"),
            Member.team_id.label("team_id"),
        )
        .join(Task, Task.id == Assignment.task_id)
        .join(Skill, Skill.id == Assignment.skill_id)
        .join(Member, Member.id == Assignment.member_id)
    )


async def get_assignment_row(session: AsyncSession, assignment_id: uuid.UUID) -> Row[Any]:
    row = (
        await session.execute(assignment_select().where(Assignment.id == assignment_id))
    ).one_or_none()
    if row is None:
        raise not_found("Assignment", assignment_id)
    return row


async def list_assignments(
    session: AsyncSession,
    *,
    start: date | None,
    end: date | None,
    member_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    skill_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[Row[Any]], int]:
    stmt = assignment_select()
    if start is not None:
        stmt = stmt.where(Assignment.week_start >= start)
    if end is not None:
        stmt = stmt.where(Assignment.week_start <= end)
    if member_id is not None:
        stmt = stmt.where(Assignment.member_id == member_id)
    if task_id is not None:
        stmt = stmt.where(Assignment.task_id == task_id)
    if skill_id is not None:
        stmt = stmt.where(Assignment.skill_id == skill_id)
    if team_id is not None:
        stmt = stmt.where(Member.team_id == team_id)
    return await page_rows(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "weekStart": Assignment.week_start,
            "taskKey": Task.key,
            "memberName": func.lower(Member.display_name),
            "personDays": Assignment.person_days,
            "createdAt": Assignment.created_at,
        },
        default_sort="weekStart,taskKey,memberName",
    )


# --- absences ----------------------------------------------------------------------------


def absence_select() -> Any:
    return (
        select(
            Absence,
            AbsenceType,
            Member.display_name.label("member_name"),
            Member.team_id.label("team_id"),
        )
        .join(AbsenceType, AbsenceType.id == Absence.absence_type_id)
        .outerjoin(Member, Member.id == Absence.member_id)
    )


async def get_absence(session: AsyncSession, absence_id: uuid.UUID) -> Absence:
    absence = await session.get(Absence, absence_id)
    if absence is None:
        raise not_found("Absence", absence_id)
    return absence


async def get_absence_row(session: AsyncSession, absence_id: uuid.UUID) -> Row[Any]:
    row = (await session.execute(absence_select().where(Absence.id == absence_id))).one_or_none()
    if row is None:
        raise not_found("Absence", absence_id)
    return row


async def list_absences(
    session: AsyncSession,
    *,
    member_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    type_id: uuid.UUID | None,
    start: date | None,
    end: date | None,
    source: str | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[Row[Any]], int]:
    stmt = absence_select()
    if member_id is not None:
        # An absence for everybody applies to the member too.
        stmt = stmt.where(or_(Absence.member_id == member_id, Absence.member_id.is_(None)))
    if team_id is not None:
        stmt = stmt.where(or_(Member.team_id == team_id, Absence.member_id.is_(None)))
    if type_id is not None:
        stmt = stmt.where(Absence.absence_type_id == type_id)
    if start is not None:
        stmt = stmt.where(Absence.end_date >= start)
    if end is not None:
        stmt = stmt.where(Absence.start_date <= end)
    if source == "manual":
        stmt = stmt.where(Absence.external_source.is_(None))
    elif source == "external":
        stmt = stmt.where(Absence.external_source.is_not(None))
    elif source:
        stmt = stmt.where(Absence.external_source == source)
    return await page_rows(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "startDate": Absence.start_date,
            "endDate": Absence.end_date,
            "memberName": func.lower(Member.display_name),
            "type": AbsenceType.sort_order,
            "createdAt": Absence.created_at,
        },
        default_sort="startDate,memberName",
    )


async def find_identical_absence(
    session: AsyncSession,
    *,
    member_id: uuid.UUID | None,
    type_id: uuid.UUID,
    start: date,
    end: date,
    start_part: str,
    end_part: str,
) -> Absence | None:
    stmt = select(Absence).where(
        Absence.member_id.is_(None) if member_id is None else Absence.member_id == member_id,
        Absence.absence_type_id == type_id,
        Absence.start_date == start,
        Absence.end_date == end,
        Absence.start_part == start_part,
        Absence.end_part == end_part,
    )
    return (await session.scalars(stmt)).first()


async def absence_types_by_key(session: AsyncSession) -> dict[str, AbsenceType]:
    """Active and inactive types by lower-cased key and by lower-cased name."""
    rows = (await session.scalars(select(AbsenceType))).all()
    found: dict[str, AbsenceType] = {}
    for row in rows:
        found.setdefault(row.name.lower(), row)
    for row in rows:
        found[row.key.lower()] = row
    return found


async def members_by_name(session: AsyncSession) -> dict[str, list[Member]]:
    """Members by lower-cased display name and by lower-cased e-mail address."""
    rows = (await session.scalars(select(Member))).all()
    found: dict[str, list[Member]] = {}
    for row in rows:
        found.setdefault(row.display_name.lower(), []).append(row)
        if row.email:
            found.setdefault(row.email.lower(), []).append(row)
    return found
