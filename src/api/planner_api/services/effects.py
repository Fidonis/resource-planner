"""Figures a change reports back, and the small converters the read models share."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.domain.calendar import ZERO, iso_week, round2
from planner_api.domain.planning import MemberWeek, task_skill_status
from planner_api.models import Assignment, TaskEffort
from planner_api.repositories import planning as planning_repo
from planner_api.schemas.planning import (
    MemberWeekFigures,
    MemberWeekOut,
    TaskSkillOut,
    WeekOut,
)


def figures(week: MemberWeek) -> MemberWeekFigures:
    return MemberWeekFigures(
        week_start=week.week_start,
        present_days=week.present_days,
        absent_days=week.absent_days,
        capacity=week.capacity,
        booked=week.booked,
        free=week.free,
        overbooked=week.overbooked,
        full=week.full,
    )


def week_out(member_id: uuid.UUID, week: MemberWeek) -> MemberWeekOut:
    return MemberWeekOut(member_id=member_id, **figures(week).model_dump())


def week_refs(starts: Sequence[date]) -> list[WeekOut]:
    return [
        WeekOut(week_start=w, iso_week=iso_week(w), iso_year=w.isocalendar().year) for w in starts
    ]


async def member_week_outs(
    session: AsyncSession, pairs: Sequence[tuple[uuid.UUID, date]]
) -> list[MemberWeekOut]:
    """Figures for the (member, week) pairs, in the order given."""
    found = {
        (member_id, week.week_start): week_out(member_id, week)
        for member_id, week in await planning_repo.member_weeks_for(session, pairs)
    }
    return [found[pair] for pair in dict.fromkeys(pairs) if pair in found]


async def task_skill_effect(
    session: AsyncSession, task_id: uuid.UUID, skill_id: uuid.UUID
) -> TaskSkillOut:
    effort = (
        await session.execute(
            select(TaskEffort.person_days).where(
                TaskEffort.task_id == task_id, TaskEffort.skill_id == skill_id
            )
        )
    ).scalar_one_or_none() or ZERO
    assigned = (
        await session.execute(
            select(func.coalesce(func.sum(Assignment.person_days), 0)).where(
                Assignment.task_id == task_id, Assignment.skill_id == skill_id
            )
        )
    ).scalar_one()
    status = task_skill_status(skill_id, round2(effort), round2(assigned))
    return TaskSkillOut(
        task_id=task_id,
        skill_id=skill_id,
        effort=status.effort,
        assigned=status.assigned,
        open=status.open,
        overplanned=status.overplanned,
    )
