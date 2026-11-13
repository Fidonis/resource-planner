"""The read models: planning board, overview and availability (§4, §7.1)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from planner_api import clock
from planner_api.auth.permissions import ASSIGNMENT_READ, ASSIGNMENT_WRITE
from planner_api.domain.calendar import ZERO, round2, week_starts
from planner_api.domain.planning import (
    TaskIn,
    booked_index,
    build_availability,
    build_overview,
    member_weeks,
    planned_percent,
)
from planner_api.errors import not_found
from planner_api.models import Task, Team
from planner_api.repositories import planning as repo
from planner_api.repositories import settings as settings_repo
from planner_api.schemas.planning import (
    AvailabilityMember,
    AvailabilityOut,
    ChipOut,
    KpiOut,
    MemberPlan,
    MemberWeekPlan,
    OverviewOut,
    PlanningOut,
    PoolEffort,
    PoolTask,
    SkillRef,
    SkillRowOut,
    TeamPlan,
    TeamRowOut,
)
from planner_api.services.context import Ctx
from planner_api.services.effects import figures, week_refs

MAX_WEEKS = 26


async def resolve_weeks(ctx: Ctx, start: date | None, weeks: int | None) -> list[date]:
    """The Mondays of a horizon. Defaults: this week, and the configured number of weeks."""
    if weeks is None:
        weeks = (await settings_repo.load_settings(ctx.session))["planning_horizon_weeks"]
    count = max(1, min(int(weeks), MAX_WEEKS))
    return week_starts(start or clock.today(), count)


def _percent(part: Decimal, whole: Decimal) -> float:
    return round(float(part / whole * 100), 1) if whole > ZERO else 0.0


# --- planning ----------------------------------------------------------------------------


async def planning(
    ctx: Ctx,
    *,
    start: date | None,
    weeks: int | None,
    team_id: uuid.UUID | None,
    skill_id: uuid.UUID | None,
    member_ids: Collection[uuid.UUID] | None,
    task_ids: Collection[uuid.UUID] | None,
    only_open_effort: bool,
    with_members: bool,
    with_tasks: bool,
) -> PlanningOut:
    ctx.principal.require(ASSIGNMENT_READ)
    starts = await resolve_weeks(ctx, start, weeks)
    first, last = starts[0], starts[-1]
    session = ctx.session
    referenced: set[uuid.UUID] = set()

    teams: list[TeamPlan] = []
    if with_members:
        wanted_members = list(member_ids) if member_ids else None
        members = await repo.load_members(session, member_ids=wanted_members, team_id=team_id)
        chips = await repo.load_chips(session, first, last, member_ids=wanted_members)
        spans = await repo.load_spans(
            session, first, last + timedelta(days=6), member_ids=wanted_members
        )
        booked = booked_index([c.as_assignment() for c in chips])
        by_cell: dict[tuple[uuid.UUID, date], list[ChipOut]] = defaultdict(list)
        for chip in chips:
            by_cell[(chip.member_id, chip.week_start)].append(
                ChipOut(
                    id=chip.id,
                    version=chip.version,
                    task_id=chip.task_id,
                    task_key=chip.task_key,
                    task_title=chip.task_title,
                    skill_id=chip.skill_id,
                    skill_key=chip.skill_key,
                    person_days=chip.person_days,
                    closed=chip.task_status != "open",
                )
            )
            referenced.add(chip.skill_id)
        # Members who left but still hold work in the horizon stay visible.
        booked_members = {c.member_id for c in chips}
        shown = [m for m in members if m.active or m.id in booked_members]
        team_rows = (
            await session.execute(
                select(Team.id, Team.name, Team.color)
                .where(Team.id == team_id if team_id else Team.id.is_not(None))
                .order_by(Team.name)
            )
        ).all()
        plans: dict[uuid.UUID, list[MemberPlan]] = defaultdict(list)
        for m in shown:
            grid = member_weeks(m, starts, spans, booked)
            referenced.update(m.skill_ids)
            plans[m.team_id].append(
                MemberPlan(
                    id=m.id,
                    display_name=m.name,
                    team_id=m.team_id,
                    allocation_percent=m.allocation_percent,
                    active=m.active,
                    skill_ids=sorted(m.skill_ids, key=str),
                    writable=ctx.principal.allows_team(ASSIGNMENT_WRITE, m.team_id),
                    weeks=[
                        MemberWeekPlan(
                            **figures(w).model_dump(),
                            assignments=by_cell.get((m.id, w.week_start), []),
                        )
                        for w in grid
                    ],
                    capacity=round2(sum((w.capacity for w in grid), ZERO)),
                    booked=round2(sum((w.booked for w in grid), ZERO)),
                    free=round2(sum((w.free for w in grid), ZERO)),
                )
            )
        for t in team_rows:
            people = plans.get(t.id, [])
            if member_ids and not people:
                continue
            teams.append(
                TeamPlan(
                    id=t.id,
                    name=t.name,
                    color=t.color,
                    capacity=round2(sum((p.capacity for p in people), ZERO)),
                    booked=round2(sum((p.booked for p in people), ZERO)),
                    free=round2(sum((p.free for p in people), ZERO)),
                    members=people,
                )
            )

    pool: list[PoolTask] = []
    if with_tasks:
        wanted_tasks = list(task_ids) if task_ids else None
        rows = await repo.load_tasks(
            session,
            task_ids=wanted_tasks,
            status=None if wanted_tasks else "open",
            skill_id=skill_id,
        )
        totals = await repo.assigned_totals(
            session, task_ids=wanted_tasks, open_only=wanted_tasks is None
        )
        statuses = await _statuses(ctx, [r.id for r in rows]) if wanted_tasks else {}
        skills = await repo.skill_refs(session, {s for r in rows for s in r.efforts})
        for row in rows:
            efforts: list[PoolEffort] = []
            pairs: list[tuple[Decimal, Decimal]] = []
            total_effort = total_open = ZERO
            for effort_skill, effort in sorted(
                row.efforts.items(), key=lambda kv: skills[kv[0]][0].lower()
            ):
                done = round2(totals.get((row.id, effort_skill), ZERO))
                planned = round2(effort)
                pairs.append((planned, done))
                total_effort += planned
                total_open += max(ZERO, planned - done)
                if skill_id is not None and effort_skill != skill_id:
                    continue
                efforts.append(
                    PoolEffort(
                        skill_id=effort_skill,
                        skill_key=skills[effort_skill][0],
                        effort=planned,
                        assigned=done,
                        open=round2(planned - done),
                        overplanned=done > planned,
                        done=done >= planned,
                    )
                )
                referenced.add(effort_skill)
            if not wanted_tasks and only_open_effort and not any(e.open > ZERO for e in efforts):
                continue
            pool.append(
                PoolTask(
                    id=row.id,
                    version=row.version,
                    key=row.key,
                    title=row.title,
                    status=statuses.get(row.id, row.status),
                    owner_team_id=row.owner_team_id,
                    efforts=efforts,
                    total_effort=round2(total_effort),
                    total_open=round2(total_open),
                    planned_percent=planned_percent(pairs),
                )
            )

    refs = await repo.skill_refs(session, referenced)
    return PlanningOut(
        start=first,
        weeks=week_refs(starts),
        skills=sorted(
            (SkillRef(id=i, key=k, name=n) for i, (k, n) in refs.items()),
            key=lambda s: s.key.lower(),
        ),
        teams=teams,
        tasks=pool,
        can_assign=ctx.principal.has(ASSIGNMENT_WRITE),
    )


async def _statuses(ctx: Ctx, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not task_ids:
        return {}
    rows = await ctx.session.execute(select(Task.id, Task.status).where(Task.id.in_(task_ids)))
    return {i: s for i, s in rows.all()}


# --- overview ----------------------------------------------------------------------------


async def overview(ctx: Ctx, *, start: date | None, weeks: int | None) -> OverviewOut:
    ctx.principal.require(ASSIGNMENT_READ)
    starts = await resolve_weeks(ctx, start, weeks)
    first, last = starts[0], starts[-1]
    session = ctx.session
    members = await repo.load_members(session)
    spans = await repo.load_spans(session, first, last + timedelta(days=6))
    horizon = await repo.load_assignments(session, start=first, end=last)
    open_tasks = await repo.load_tasks(session, status="open")
    totals = await repo.assigned_totals(session, open_only=True)
    teams = (
        await session.execute(select(Team.id, Team.name, Team.color).order_by(Team.name))
    ).all()
    skills = await repo.skill_refs(session)
    result = build_overview(
        members=members,
        team_ids=[t.id for t in teams],
        skill_ids=sorted(skills, key=lambda i: skills[i][0].lower()),
        tasks=[TaskIn(t.id, t.status, t.efforts) for t in open_tasks],
        assignments=horizon,
        spans=spans,
        weeks=starts,
        assigned_totals=totals,
    )
    team_info = {t.id: (t.name, t.color) for t in teams}
    return OverviewOut(
        start=first,
        weeks=week_refs(starts),
        kpis=KpiOut(
            demand=result.kpis.demand,
            assigned=result.kpis.assigned,
            assigned_percent=_percent(result.kpis.assigned, result.kpis.demand),
            open=result.kpis.open,
            overbooked_member_weeks=result.kpis.overbooked_member_weeks,
        ),
        skills=[
            SkillRowOut(
                skill_id=r.skill_id,
                skill_key=skills[r.skill_id][0],
                skill_name=skills[r.skill_id][1],
                demand=r.demand,
                assigned=r.assigned,
                assigned_percent=_percent(r.assigned, r.demand),
                open=r.open,
                pool=r.pool,
                pool_free=r.pool_free,
                status=r.status,
            )
            for r in result.skills
        ],
        teams=[
            TeamRowOut(
                team_id=r.team_id,
                team_name=team_info[r.team_id][0],
                color=team_info[r.team_id][1],
                member_count=r.member_count,
                capacity=r.capacity,
                booked=r.booked,
                free=r.free,
                utilisation_percent=_percent(r.booked, r.capacity),
            )
            for r in result.teams
        ],
    )


# --- availability ------------------------------------------------------------------------


async def availability(
    ctx: Ctx,
    *,
    skill_id: uuid.UUID,
    start: date | None,
    weeks: int | None,
    team_id: uuid.UUID | None,
) -> AvailabilityOut:
    ctx.principal.require(ASSIGNMENT_READ)
    skills = await repo.skill_refs(ctx.session, [skill_id])
    if skill_id not in skills:
        raise not_found("Skill", skill_id)
    starts = await resolve_weeks(ctx, start, weeks)
    first, last = starts[0], starts[-1]
    members = await repo.load_members(
        ctx.session, skill_id=skill_id, team_id=team_id, active_only=True
    )
    ids = [m.id for m in members]
    spans = await repo.load_spans(ctx.session, first, last + timedelta(days=6), member_ids=ids)
    assignments = await repo.load_assignments(ctx.session, start=first, end=last, member_ids=ids)
    rows = build_availability(
        members=members, skill_id=skill_id, weeks=starts, spans=spans, assignments=assignments
    )
    people = {m.id: m for m in members}
    names = {t.id: t.name for t in (await ctx.session.execute(select(Team.id, Team.name))).all()}
    return AvailabilityOut(
        skill_id=skill_id,
        skill_key=skills[skill_id][0],
        start=first,
        weeks=week_refs(starts),
        members=[
            AvailabilityMember(
                member_id=r.member_id,
                display_name=people[r.member_id].name,
                team_id=people[r.member_id].team_id,
                team_name=names.get(people[r.member_id].team_id, ""),
                allocation_percent=people[r.member_id].allocation_percent,
                weeks=[figures(w) for w in r.weeks],
                total_capacity=r.total_capacity,
                total_booked=r.total_booked,
                total_free=r.total_free,
            )
            for r in rows
        ],
    )
