"""Database access for teams, skills, members and absence types."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from planner_api.errors import not_found
from planner_api.models import (
    Absence,
    AbsenceType,
    Assignment,
    Member,
    MemberSkill,
    Skill,
    Task,
    TaskEffort,
    Team,
)
from planner_api.repositories.base import like as _like
from planner_api.repositories.base import page_of

# --- teams -------------------------------------------------------------------------------


async def get_team(session: AsyncSession, team_id: uuid.UUID) -> Team:
    team = await session.get(Team, team_id)
    if team is None:
        raise not_found("Team", team_id)
    return team


async def list_teams(
    session: AsyncSession, *, q: str | None, page: int, page_size: int, sort: str | None
) -> tuple[Sequence[Team], int]:
    stmt = select(Team)
    if q:
        stmt = stmt.where(Team.name.ilike(_like(q), escape="\\"))
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={"name": func.lower(Team.name), "createdAt": Team.created_at},
        default_sort="name",
    )


async def member_counts_by_team(
    session: AsyncSession, team_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not team_ids:
        return {}
    rows = await session.execute(
        select(Member.team_id, func.count())
        .where(Member.team_id.in_(team_ids))
        .group_by(Member.team_id)
    )
    return {team_id: count for team_id, count in rows.all()}


async def team_names(
    session: AsyncSession, team_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not team_ids:
        return {}
    rows = await session.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids)))
    return {team_id: name for team_id, name in rows.all()}


async def team_blockers(session: AsyncSession, team_id: uuid.UUID) -> list[dict[str, Any]]:
    members = (
        await session.execute(
            select(func.count()).select_from(Member).where(Member.team_id == team_id)
        )
    ).scalar_one()
    return [{"kind": "members", "count": members}] if members else []


# --- skills ------------------------------------------------------------------------------


async def get_skill(session: AsyncSession, skill_id: uuid.UUID) -> Skill:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise not_found("Skill", skill_id)
    return skill


async def get_skills(
    session: AsyncSession, skill_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Skill]:
    if not skill_ids:
        return {}
    rows = await session.scalars(select(Skill).where(Skill.id.in_(skill_ids)))
    return {skill.id: skill for skill in rows.all()}


async def list_skills(
    session: AsyncSession, *, q: str | None, page: int, page_size: int, sort: str | None
) -> tuple[Sequence[Skill], int]:
    stmt = select(Skill)
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            Skill.key.ilike(pattern, escape="\\") | Skill.name.ilike(pattern, escape="\\")
        )
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "key": func.lower(Skill.key),
            "name": func.lower(Skill.name),
            "createdAt": Skill.created_at,
        },
        default_sort="key",
    )


async def member_counts_by_skill(
    session: AsyncSession, skill_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not skill_ids:
        return {}
    rows = await session.execute(
        select(MemberSkill.skill_id, func.count())
        .where(MemberSkill.skill_id.in_(skill_ids))
        .group_by(MemberSkill.skill_id)
    )
    return {skill_id: count for skill_id, count in rows.all()}


async def skill_blockers(session: AsyncSession, skill_id: uuid.UUID) -> list[dict[str, Any]]:
    counts = {
        "members": select(func.count())
        .select_from(MemberSkill)
        .where(MemberSkill.skill_id == skill_id),
        "task efforts": select(func.count())
        .select_from(TaskEffort)
        .where(TaskEffort.skill_id == skill_id),
        "assignments": select(func.count())
        .select_from(Assignment)
        .where(Assignment.skill_id == skill_id),
    }
    blockers: list[dict[str, Any]] = []
    for kind, stmt in counts.items():
        count = (await session.execute(stmt)).scalar_one()
        if count:
            blockers.append({"kind": kind, "count": count})
    return blockers


# --- members -----------------------------------------------------------------------------


async def get_member(session: AsyncSession, member_id: uuid.UUID) -> Member:
    stmt = select(Member).where(Member.id == member_id).options(selectinload(Member.skill_links))
    member = (await session.scalars(stmt)).one_or_none()
    if member is None:
        raise not_found("Member", member_id)
    return member


async def list_members(
    session: AsyncSession,
    *,
    q: str | None,
    team_id: uuid.UUID | None,
    skill_id: uuid.UUID | None,
    active: bool | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[Member], int]:
    stmt = select(Member).options(selectinload(Member.skill_links))
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            Member.display_name.ilike(pattern, escape="\\")
            | Member.email.ilike(pattern, escape="\\")
        )
    if team_id is not None:
        stmt = stmt.where(Member.team_id == team_id)
    if skill_id is not None:
        stmt = stmt.where(
            Member.id.in_(select(MemberSkill.member_id).where(MemberSkill.skill_id == skill_id))
        )
    if active is not None:
        stmt = stmt.where(Member.active == active)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "displayName": func.lower(Member.display_name),
            "email": func.lower(Member.email),
            "allocationPercent": Member.allocation_percent,
            "createdAt": Member.created_at,
        },
        default_sort="displayName",
    )


async def assignment_count_for_member(session: AsyncSession, member_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Assignment).where(Assignment.member_id == member_id)
    return (await session.execute(stmt)).scalar_one()


async def absence_count_for_member(session: AsyncSession, member_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Absence).where(Absence.member_id == member_id)
    return (await session.execute(stmt)).scalar_one()


@dataclass(frozen=True, slots=True)
class OrphanedAssignments:
    task_id: uuid.UUID
    task_key: str
    skill_id: uuid.UUID
    skill_key: str
    count: int
    person_days: Decimal


async def assignments_for_skills(
    session: AsyncSession, member_id: uuid.UUID, skill_ids: Collection[uuid.UUID]
) -> list[OrphanedAssignments]:
    """Assignments of a member that use one of ``skill_ids``, grouped by task and skill."""
    if not skill_ids:
        return []
    stmt = (
        select(
            Assignment.task_id,
            Task.key,
            Assignment.skill_id,
            Skill.key,
            func.count(),
            func.sum(Assignment.person_days),
        )
        .join(Task, Task.id == Assignment.task_id)
        .join(Skill, Skill.id == Assignment.skill_id)
        .where(Assignment.member_id == member_id, Assignment.skill_id.in_(skill_ids))
        .group_by(Assignment.task_id, Task.key, Assignment.skill_id, Skill.key)
        .order_by(Task.key, Skill.key)
    )
    rows = (await session.execute(stmt)).all()
    return [OrphanedAssignments(*row) for row in rows]


# --- absence types -----------------------------------------------------------------------


async def get_absence_type(session: AsyncSession, type_id: uuid.UUID) -> AbsenceType:
    absence_type = await session.get(AbsenceType, type_id)
    if absence_type is None:
        raise not_found("Absence type", type_id)
    return absence_type


async def list_absence_types(
    session: AsyncSession,
    *,
    active: bool | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[AbsenceType], int]:
    stmt = select(AbsenceType)
    if active is not None:
        stmt = stmt.where(AbsenceType.active == active)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "sortOrder": AbsenceType.sort_order,
            "name": func.lower(AbsenceType.name),
            "key": AbsenceType.key,
        },
        default_sort="sortOrder,name",
    )


async def absence_counts_by_type(
    session: AsyncSession, type_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not type_ids:
        return {}
    rows = await session.execute(
        select(Absence.absence_type_id, func.count())
        .where(Absence.absence_type_id.in_(type_ids))
        .group_by(Absence.absence_type_id)
    )
    return {type_id: count for type_id, count in rows.all()}
