"""Tasks and their effort per skill (FR-TASK-01 to 06, DM-03, §9.4)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from planner_api.auth.permissions import INTEGRATION_ADMIN, TASK_READ, TASK_WRITE
from planner_api.domain.calendar import ZERO, round2
from planner_api.domain.planning import planned_percent
from planner_api.errors import ErrorCode, ProblemError, blocked, validation
from planner_api.models import Assignment, Task, TaskEffort
from planner_api.models.base import touch
from planner_api.repositories import org as org_repo
from planner_api.repositories import planning as planning_repo
from planner_api.repositories import work as repo
from planner_api.schemas.common import Page
from planner_api.schemas.planning import (
    EffortIn,
    EffortOut,
    EffortsIn,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from planner_api.services.common import reject_locked, snapshot
from planner_api.services.context import Ctx, Expected

AUDITED = ("key", "title", "description", "owner_team_id", "status")
DEFAULT_LOCKED = ("title", "status", "efforts")


def managed_fields(task: Task) -> list[str]:
    """What the source system of an imported task still owns (§9.4); the key always."""
    if task.external_source is None:
        return []
    return ["key", *[f for f in DEFAULT_LOCKED if f in (task.locked_fields or [])]]


def effort_map(task: Task) -> dict[uuid.UUID, Decimal]:
    return {e.skill_id: e.person_days for e in task.efforts}


def _to_out(
    task: Task,
    assigned: Mapping[tuple[uuid.UUID, uuid.UUID], Decimal],
    skills: Mapping[uuid.UUID, tuple[str, str]],
    teams: Mapping[uuid.UUID, str],
    ctx: Ctx,
) -> TaskOut:
    efforts: list[EffortOut] = []
    pairs: list[tuple[Decimal, Decimal]] = []
    total_effort = total_assigned = total_open = ZERO
    for effort in sorted(task.efforts, key=lambda e: skills[e.skill_id][0].lower()):
        done = round2(assigned.get((task.id, effort.skill_id), ZERO))
        planned = round2(effort.person_days)
        key, name = skills[effort.skill_id]
        efforts.append(
            EffortOut(
                skill_id=effort.skill_id,
                skill_key=key,
                skill_name=name,
                person_days=planned,
                assigned=done,
                open=round2(planned - done),
                overplanned=done > planned,
            )
        )
        pairs.append((planned, done))
        total_effort += planned
        total_assigned += done
        total_open += max(ZERO, planned - done)
    return TaskOut(
        id=task.id,
        version=task.version,
        created_at=task.created_at,
        updated_at=task.updated_at,
        external_source=task.external_source,
        external_id=task.external_id,
        managed_fields=managed_fields(task),
        key=task.key,
        title=task.title,
        description=task.description,
        owner_team_id=task.owner_team_id,
        owner_team_name=teams.get(task.owner_team_id) if task.owner_team_id else None,
        status=task.status,
        external_url=task.external_url,
        external_updated_at=task.external_updated_at,
        locked_fields=list(task.locked_fields or []),
        efforts=efforts,
        total_effort=round2(total_effort),
        total_assigned=round2(total_assigned),
        total_open=round2(total_open),
        planned_percent=planned_percent(pairs),
        overplanned=any(e.overplanned for e in efforts),
        writable=ctx.principal.has(TASK_WRITE),
    )


async def outs(ctx: Ctx, tasks: Sequence[Task]) -> list[TaskOut]:
    """Output documents for many tasks with a fixed number of queries."""
    if not tasks:
        return []
    assigned = await planning_repo.assigned_totals(ctx.session, task_ids=[t.id for t in tasks])
    skills = await planning_repo.skill_refs(
        ctx.session, {e.skill_id for t in tasks for e in t.efforts}
    )
    teams = await org_repo.team_names(
        ctx.session, {t.owner_team_id for t in tasks if t.owner_team_id}
    )
    return [_to_out(t, assigned, skills, teams, ctx) for t in tasks]


async def out(ctx: Ctx, task: Task) -> TaskOut:
    return (await outs(ctx, [task]))[0]


async def list_tasks(
    ctx: Ctx,
    *,
    q: str | None,
    owner_team_id: uuid.UUID | None,
    status: str | None,
    skill_id: uuid.UUID | None,
    source: str | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> Page[TaskOut]:
    ctx.principal.require(TASK_READ)
    rows, total = await repo.list_tasks(
        ctx.session,
        q=q,
        owner_team_id=owner_team_id,
        status=status,
        skill_id=skill_id,
        source=source,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page[TaskOut](items=await outs(ctx, rows), total=total)


async def get_task(ctx: Ctx, task_id: uuid.UUID) -> TaskOut:
    ctx.principal.require(TASK_READ)
    return await out(ctx, await repo.get_task(ctx.session, task_id))


# --- efforts -----------------------------------------------------------------------------


async def prepare_efforts(ctx: Ctx, efforts: Sequence[EffortIn]) -> dict[uuid.UUID, Decimal]:
    """Validate the skills of a list of efforts and turn it into a mapping."""
    wanted: dict[uuid.UUID, Decimal] = {}
    for effort in efforts:
        if effort.skill_id in wanted:
            raise validation("A skill can appear only once.", field="efforts", code="duplicate")
        wanted[effort.skill_id] = round2(effort.person_days)
    found = await org_repo.get_skills(ctx.session, set(wanted))
    missing = sorted(str(s) for s in wanted.keys() - found.keys())
    if missing:
        raise validation(
            f"Unknown skills: {', '.join(missing)}.", field="efforts", code="not_found"
        )
    return wanted


async def apply_efforts(
    ctx: Ctx, task: Task, wanted: Mapping[uuid.UUID, Decimal], *, force: bool
) -> list[dict[str, Any]]:
    """Make the efforts of ``task`` equal ``wanted`` (DM-03).

    An effort that falls below what is already assigned is refused. With ``force`` the excess
    assignments are removed, the latest weeks first, and returned for the audit log.
    """
    assigned = await planning_repo.assigned_totals(ctx.session, task_ids=[task.id])
    excess: list[tuple[uuid.UUID, Decimal, Decimal]] = []
    for (_, skill_id), total in assigned.items():
        target = wanted.get(skill_id, ZERO)
        if total > target:
            excess.append((skill_id, round2(total), target))
    if excess and not force:
        keys = await planning_repo.skill_refs(ctx.session, {s for s, _, _ in excess})
        raise ProblemError(
            409,
            ErrorCode.EFFORT_BELOW_ASSIGNED,
            "The effort is lower than the work already assigned. Remove the assignments first "
            "or repeat the request with force=true to remove the excess.",
            errors=[
                {"field": "efforts", "message": f"{keys[s][0]}: {a} assigned.", "code": "assigned"}
                for s, a, _ in excess
            ],
            extra={
                "conflicts": [
                    {
                        "skillId": str(s),
                        "skillKey": keys[s][0],
                        "assigned": float(a),
                        "requested": float(t),
                    }
                    for s, a, t in excess
                ]
            },
        )
    current = {e.skill_id: e for e in task.efforts}
    changed = False
    for skill_id, row in list(current.items()):
        if skill_id not in wanted:
            task.efforts.remove(row)
            changed = True
    for skill_id, days in wanted.items():
        effort = current.get(skill_id)
        if effort is None:
            task.efforts.append(TaskEffort(skill_id=skill_id, person_days=days))
            changed = True
        elif effort.person_days != days:
            effort.person_days = days
            changed = True
    removed: list[dict[str, Any]] = []
    for skill_id, total, target in excess:
        removed.extend(await _trim(ctx, task.id, skill_id, total - target))
    if changed:
        touch(task)
    return removed


async def _trim(
    ctx: Ctx, task_id: uuid.UUID, skill_id: uuid.UUID, surplus: Decimal
) -> list[dict[str, Any]]:
    """Take ``surplus`` person-days off the assignments of a task skill, latest weeks first."""
    rows = (
        await ctx.session.scalars(
            select(Assignment)
            .where(Assignment.task_id == task_id, Assignment.skill_id == skill_id)
            .order_by(Assignment.week_start.desc(), Assignment.person_days.desc())
        )
    ).all()
    removed: list[dict[str, Any]] = []
    left = surplus
    for row in rows:
        if left <= ZERO:
            break
        take = min(row.person_days, left)
        removed.append(
            {
                "assignment_id": row.id,
                "member_id": row.member_id,
                "skill_id": skill_id,
                "week_start": row.week_start,
                "removed": take,
            }
        )
        if take >= row.person_days:
            await ctx.session.delete(row)
        else:
            row.person_days = round2(row.person_days - take)
        left -= take
    return removed


def _removed_note(removed: Sequence[Mapping[str, Any]]) -> str | None:
    if not removed:
        return None
    days = sum((r["removed"] for r in removed), ZERO)
    return (
        f"{len(removed)} assignment(s) reduced or removed ({days} person-days) "
        "because the effort was lowered with force"
    )


# --- create, change, delete --------------------------------------------------------------


async def _require_team(ctx: Ctx, team_id: uuid.UUID | None) -> None:
    if team_id is not None and team_id not in await org_repo.team_names(ctx.session, [team_id]):
        raise validation("The team does not exist.", field="ownerTeamId", code="not_found")


def audit_after(task: Task) -> dict[str, Any]:
    return snapshot(task, AUDITED) | {"efforts": effort_map(task)}


async def create_task(ctx: Ctx, body: TaskCreate) -> TaskOut:
    ctx.principal.require(TASK_WRITE)
    await _require_team(ctx, body.owner_team_id)
    wanted = await prepare_efforts(ctx, body.efforts)
    task = Task(
        key=body.key,
        title=body.title,
        description=body.description,
        owner_team_id=body.owner_team_id,
        status=body.status,
        locked_fields=[],
        efforts=[TaskEffort(skill_id=s, person_days=d) for s, d in wanted.items()],
    )
    ctx.session.add(task)
    await ctx.uow.flush()
    ctx.audit("create", "task", task.id, after=audit_after(task))
    result = await out(ctx, task)
    await ctx.uow.finish()
    return result


async def update_task(
    ctx: Ctx, task_id: uuid.UUID, body: TaskUpdate, expected: Expected, *, force: bool
) -> TaskOut:
    ctx.principal.require(TASK_WRITE)
    task = await repo.get_task(ctx.session, task_id)
    expected.check(task.version)
    await _require_team(ctx, body.owner_team_id)
    current = effort_map(task)
    wanted = await prepare_efforts(ctx, body.efforts) if body.efforts is not None else None
    reject_locked(
        task.external_source,
        managed_fields(task),
        {
            "key": (task.key, body.key),
            "title": (task.title, body.title),
            "status": (task.status, body.status),
            "efforts": (current, wanted if wanted is not None else current),
        },
    )
    before = audit_after(task)
    if body.locked_fields is not None and body.locked_fields != (task.locked_fields or []):
        ctx.principal.require(INTEGRATION_ADMIN)
        if not set(body.locked_fields) <= set(task.locked_fields or []):
            raise validation(
                "Fields can only be unlocked, not locked.", field="lockedFields", code="invalid"
            )
        task.locked_fields = list(body.locked_fields)
    task.key = body.key
    task.title = body.title
    task.description = body.description
    task.owner_team_id = body.owner_team_id
    task.status = body.status
    removed: list[dict[str, Any]] = []
    if wanted is not None:
        removed = await apply_efforts(ctx, task, wanted, force=force)
    await ctx.uow.flush()
    after = audit_after(task) | ({"removed_assignments": removed} if removed else {})
    ctx.audit("update", "task", task.id, before, after, note=_removed_note(removed))
    result = await out(ctx, task)
    await ctx.uow.finish()
    return result


async def set_efforts(
    ctx: Ctx, task_id: uuid.UUID, body: EffortsIn, expected: Expected, *, force: bool
) -> TaskOut:
    ctx.principal.require(TASK_WRITE)
    task = await repo.get_task(ctx.session, task_id)
    expected.check(task.version)
    current = effort_map(task)
    wanted = await prepare_efforts(ctx, body.efforts)
    reject_locked(task.external_source, managed_fields(task), {"efforts": (current, wanted)})
    before = {"efforts": current}
    removed = await apply_efforts(ctx, task, wanted, force=force)
    await ctx.uow.flush()
    after = {"efforts": effort_map(task)} | ({"removed_assignments": removed} if removed else {})
    ctx.audit("update-efforts", "task", task.id, before, after, note=_removed_note(removed))
    result = await out(ctx, task)
    await ctx.uow.finish()
    return result


async def delete_task(ctx: Ctx, task_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(TASK_WRITE)
    task = await repo.get_task(ctx.session, task_id)
    expected.check(task.version)
    count = await repo.assignment_count_for_task(ctx.session, task.id)
    if count:
        raise blocked("task", [{"kind": "assignments", "count": count}])
    before = audit_after(task)
    await ctx.session.delete(task)
    ctx.audit("delete", "task", task.id, before)
    await ctx.uow.finish()
