"""CSV exports (FR-OVR-04). The task and absence files can be imported again."""

from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy import select

from planner_api import csvio
from planner_api.auth.permissions import ABSENCE_READ, ASSIGNMENT_READ, TASK_READ
from planner_api.models import Team
from planner_api.repositories import planning as planning_repo
from planner_api.repositories import work as repo
from planner_api.services import planning as planning_service
from planner_api.services import tasks as tasks_service
from planner_api.services.context import Ctx

PAGE = 200


async def tasks_csv(ctx: Ctx) -> str:
    """``Key;Title;Team;Efforts;Status`` with ``Skill=PD|Skill=PD`` efforts."""
    ctx.principal.require(TASK_READ)
    rows = []
    page = 1
    while True:
        batch, total = await repo.list_tasks(
            ctx.session,
            q=None,
            owner_team_id=None,
            status=None,
            skill_id=None,
            source=None,
            page=page,
            page_size=PAGE,
            sort="key",
        )
        for task in await tasks_service.outs(ctx, batch):
            efforts = "|".join(f"{e.skill_key}={csvio.plain(e.person_days)}" for e in task.efforts)
            rows.append((task.key, task.title, task.owner_team_name or "", efforts, task.status))
        if page * PAGE >= total:
            break
        page += 1
    return csvio.write_table(("Key", "Title", "Team", "Efforts", "Status"), rows)


async def absences_csv(ctx: Ctx, start: date | None, end: date | None) -> str:
    """``Name;From;To;Type;Note;StartPart;EndPart`` — ``*`` stands for all members."""
    ctx.principal.require(ABSENCE_READ)
    rows = []
    page = 1
    while True:
        batch, total = await repo.list_absences(
            ctx.session,
            member_id=None,
            team_id=None,
            type_id=None,
            start=start,
            end=end,
            source=None,
            page=page,
            page_size=PAGE,
            sort="startDate,memberName",
        )
        for row in batch:
            a = row.Absence
            rows.append(
                (
                    row.member_name or "*",
                    a.start_date.isoformat(),
                    a.end_date.isoformat(),
                    row.AbsenceType.key,
                    a.note or "",
                    a.start_part,
                    a.end_part,
                )
            )
        if page * PAGE >= total:
            break
        page += 1
    return csvio.write_table(("Name", "From", "To", "Type", "Note", "StartPart", "EndPart"), rows)


async def assignments_csv(ctx: Ctx, start: date | None, weeks: int | None) -> str:
    """Every assignment of the horizon: ``Task;Skill;Member;Team;Week;PersonDays``."""
    ctx.principal.require(ASSIGNMENT_READ)
    starts = await planning_service.resolve_weeks(ctx, start, weeks)
    chips = await planning_repo.load_chips(ctx.session, starts[0], starts[-1])
    members = {m.id: m for m in await planning_repo.load_members(ctx.session)}
    teams = {i: n for i, n in (await ctx.session.execute(select(Team.id, Team.name))).all()}
    ordered = sorted(
        chips, key=lambda c: (c.week_start, members[c.member_id].name.lower(), c.task_key)
    )
    return csvio.write_table(
        ("Task", "Skill", "Member", "Team", "Week", "PersonDays"),
        (
            (
                c.task_key,
                c.skill_key,
                members[c.member_id].name,
                teams.get(members[c.member_id].team_id, ""),
                c.week_start.isoformat(),
                csvio.plain(c.person_days),
            )
            for c in ordered
        ),
    )


async def overview_csv(
    ctx: Ctx, start: date | None, weeks: int | None, table: Literal["skills", "teams"]
) -> str:
    ctx.principal.require(ASSIGNMENT_READ)
    data = await planning_service.overview(ctx, start=start, weeks=weeks)
    if table == "teams":
        return csvio.write_table(
            ("Team", "Members", "Capacity", "Booked", "Free", "UtilisationPercent"),
            (
                (
                    t.team_name,
                    t.member_count,
                    csvio.plain(t.capacity),
                    csvio.plain(t.booked),
                    csvio.plain(t.free),
                    t.utilisation_percent,
                )
                for t in data.teams
            ),
        )
    return csvio.write_table(
        ("Skill", "Demand", "Assigned", "Open", "Pool", "PoolFree", "Status"),
        (
            (
                s.skill_key,
                csvio.plain(s.demand),
                csvio.plain(s.assigned),
                csvio.plain(s.open),
                csvio.plain(s.pool),
                csvio.plain(s.pool_free),
                s.status,
            )
            for s in data.skills
        ),
    )
