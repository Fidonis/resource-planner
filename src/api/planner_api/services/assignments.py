"""Assignments of a task skill to a member in a week (FR-PLAN, DM-02, DM-05, DM-06, NFR-API-10)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Row

from planner_api.auth.permissions import ASSIGNMENT_READ, ASSIGNMENT_WRITE
from planner_api.domain.calendar import is_monday, round2
from planner_api.errors import ErrorCode, ProblemError, validation
from planner_api.models import Assignment, Member, Task
from planner_api.repositories import org as org_repo
from planner_api.repositories import work as repo
from planner_api.schemas.common import Page
from planner_api.schemas.planning import (
    AssignmentEffect,
    AssignmentIn,
    AssignmentOut,
    AssignmentResult,
    AssignmentUpdate,
)
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx, Expected
from planner_api.services.effects import member_week_outs, task_skill_effect

AUDITED = ("task_id", "skill_id", "member_id", "week_start", "person_days")
MAX_PERSON_DAYS = Decimal("9999.99")


def _rule(code: ErrorCode, detail: str, field: str) -> ProblemError:
    return ProblemError(
        422, code, detail, errors=[{"field": field, "message": detail, "code": code.value.lower()}]
    )


def _out(row: Row[Any], ctx: Ctx) -> AssignmentOut:
    a: Assignment = row.Assignment
    closed = row.task_status != "open"
    return AssignmentOut(
        id=a.id,
        version=a.version,
        created_at=a.created_at,
        updated_at=a.updated_at,
        task_id=a.task_id,
        task_key=row.task_key,
        task_title=row.task_title,
        skill_id=a.skill_id,
        skill_key=row.skill_key,
        member_id=a.member_id,
        member_name=row.member_name,
        team_id=row.team_id,
        week_start=a.week_start,
        person_days=a.person_days,
        closed=closed,
        writable=(not closed) and ctx.principal.allows_team(ASSIGNMENT_WRITE, row.team_id),
    )


def _check_week(week_start: date) -> None:
    if not is_monday(week_start):
        raise _rule(ErrorCode.WEEK_NOT_MONDAY, "A week starts on a Monday.", "weekStart")


async def _member(ctx: Ctx, member_id: uuid.UUID) -> Member:
    try:
        return await org_repo.get_member(ctx.session, member_id)
    except ProblemError as exc:
        if exc.status == 404:
            raise validation(
                "The member does not exist.", field="memberId", code="not_found"
            ) from exc
        raise


async def _task(ctx: Ctx, task_id: uuid.UUID) -> Task:
    try:
        return await repo.get_task(ctx.session, task_id)
    except ProblemError as exc:
        if exc.status == 404:
            raise validation("The task does not exist.", field="taskId", code="not_found") from exc
        raise


def _require_open(task: Task) -> None:
    if task.status != "open":
        raise _rule(
            ErrorCode.TASK_CLOSED,
            f"{task.key} is {task.status}. Reopen the task to change its assignments.",
            "taskId",
        )


def _require_member_fits(task: Task, skill_id: uuid.UUID, member: Member) -> None:
    if not member.active:
        raise _rule(ErrorCode.MEMBER_INACTIVE, f"{member.display_name} is inactive.", "memberId")
    if skill_id not in member.skill_ids:
        raise _rule(
            ErrorCode.ASSIGNMENT_SKILL_MISMATCH,
            f"{member.display_name} does not have the skill of this assignment.",
            "skillId",
        )


def _check_total(total: Decimal) -> None:
    if total > MAX_PERSON_DAYS:
        raise validation(
            "The assignment would exceed 9999.99 person-days.", field="personDays", code="too_large"
        )


async def _effect(
    ctx: Ctx,
    task_id: uuid.UUID,
    skill_id: uuid.UUID,
    pairs: list[tuple[uuid.UUID, date]],
    *,
    merged_into: uuid.UUID | None = None,
    deleted_id: uuid.UUID | None = None,
) -> AssignmentEffect:
    weeks = await member_week_outs(ctx.session, pairs)
    task_skill = await task_skill_effect(ctx.session, task_id, skill_id)
    target = weeks[0] if weeks else None
    return AssignmentEffect(
        dry_run=ctx.dry_run,
        overbooked=target.overbooked if target else False,
        overplanned=task_skill.overplanned,
        merged_into=merged_into,
        deleted_id=deleted_id,
        member_weeks=weeks,
        task_skill=task_skill,
    )


async def _result(ctx: Ctx, assignment_id: uuid.UUID, effect: AssignmentEffect) -> AssignmentResult:
    row = await repo.get_assignment_row(ctx.session, assignment_id)
    return AssignmentResult.model_validate({**_out(row, ctx).model_dump(), **effect.model_dump()})


# --- read --------------------------------------------------------------------------------


async def list_assignments(
    ctx: Ctx,
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
) -> Page[AssignmentOut]:
    ctx.principal.require(ASSIGNMENT_READ)
    rows, total = await repo.list_assignments(
        ctx.session,
        start=start,
        end=end,
        member_id=member_id,
        task_id=task_id,
        skill_id=skill_id,
        team_id=team_id,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page[AssignmentOut](items=[_out(r, ctx) for r in rows], total=total)


async def get_assignment(ctx: Ctx, assignment_id: uuid.UUID) -> AssignmentOut:
    ctx.principal.require(ASSIGNMENT_READ)
    return _out(await repo.get_assignment_row(ctx.session, assignment_id), ctx)


# --- write -------------------------------------------------------------------------------


async def create_assignment(ctx: Ctx, body: AssignmentIn) -> tuple[AssignmentResult, bool]:
    """Create an assignment, or add to the one that already has the same task, skill, member and
    week. Returns the result and whether it was merged into an existing assignment."""
    ctx.principal.require(ASSIGNMENT_WRITE)
    _check_week(body.week_start)
    member = await _member(ctx, body.member_id)
    ctx.principal.require_team(ASSIGNMENT_WRITE, member.team_id)
    task = await _task(ctx, body.task_id)
    _require_open(task)
    if body.skill_id not in {e.skill_id for e in task.efforts}:
        raise _rule(
            ErrorCode.ASSIGNMENT_SKILL_NOT_IN_TASK,
            f"{task.key} has no effort for this skill.",
            "skillId",
        )
    _require_member_fits(task, body.skill_id, member)

    existing = await repo.find_assignment(
        ctx.session, task.id, body.skill_id, member.id, body.week_start
    )
    if existing is not None:
        before = snapshot(existing, AUDITED)
        total = round2(existing.person_days + body.person_days)
        _check_total(total)
        existing.person_days = total
        await ctx.uow.flush()
        ctx.audit(
            "merge",
            "assignment",
            existing.id,
            before,
            snapshot(existing, AUDITED),
            note="added to the assignment of the same task, skill, member and week",
        )
        row = existing
    else:
        row = Assignment(
            task_id=task.id,
            skill_id=body.skill_id,
            member_id=member.id,
            week_start=body.week_start,
            person_days=round2(body.person_days),
        )
        ctx.session.add(row)
        await ctx.uow.flush()
        ctx.audit("create", "assignment", row.id, after=snapshot(row, AUDITED))

    effect = await _effect(
        ctx,
        task.id,
        body.skill_id,
        [(member.id, body.week_start)],
        merged_into=row.id if existing is not None else None,
    )
    result = await _result(ctx, row.id, effect)
    await ctx.uow.finish()
    return result, existing is not None


async def update_assignment(
    ctx: Ctx, assignment_id: uuid.UUID, body: AssignmentUpdate, expected: Expected
) -> AssignmentResult:
    """Change the amount, and move the assignment to another member or week."""
    ctx.principal.require(ASSIGNMENT_WRITE)
    _check_week(body.week_start)
    row = await repo.get_assignment(ctx.session, assignment_id, lock=True)
    source = await _member(ctx, row.member_id)
    ctx.principal.require_team(ASSIGNMENT_WRITE, source.team_id)
    expected.check(row.version)
    task = await _task(ctx, row.task_id)
    _require_open(task)
    target = source
    if body.member_id != row.member_id:
        target = await _member(ctx, body.member_id)
        ctx.principal.require_team(ASSIGNMENT_WRITE, target.team_id)
        _require_member_fits(task, row.skill_id, target)

    old_pair = (row.member_id, row.week_start)
    new_pair = (target.id, body.week_start)
    before = snapshot(row, AUDITED)
    merged_into: uuid.UUID | None = None
    deleted_id: uuid.UUID | None = None
    survivor = row
    if new_pair == old_pair:
        row.person_days = round2(body.person_days)
        await ctx.uow.flush()
        ctx.audit("update", "assignment", row.id, before, snapshot(row, AUDITED))
    else:
        other = await repo.find_assignment(
            ctx.session, row.task_id, row.skill_id, target.id, body.week_start
        )
        if other is not None and other.id != row.id:
            other_before = snapshot(other, AUDITED)
            total = round2(other.person_days + body.person_days)
            _check_total(total)
            other.person_days = total
            deleted_id = row.id
            merged_into = other.id
            survivor = other
            await ctx.session.delete(row)
            await ctx.uow.flush()
            ctx.audit(
                "merge",
                "assignment",
                other.id,
                other_before,
                snapshot(other, AUDITED),
                note="an assignment was moved here",
            )
            ctx.audit("delete", "assignment", deleted_id, before, note="merged into another")
        else:
            row.member_id = target.id
            row.week_start = body.week_start
            row.person_days = round2(body.person_days)
            await ctx.uow.flush()
            ctx.audit("update", "assignment", row.id, before, snapshot(row, AUDITED))

    pairs = [new_pair] if new_pair == old_pair else [new_pair, old_pair]
    effect = await _effect(
        ctx, row.task_id, row.skill_id, pairs, merged_into=merged_into, deleted_id=deleted_id
    )
    result = await _result(ctx, survivor.id, effect)
    await ctx.uow.finish()
    return result


async def delete_assignment(
    ctx: Ctx, assignment_id: uuid.UUID, expected: Expected
) -> AssignmentEffect:
    ctx.principal.require(ASSIGNMENT_WRITE)
    row = await repo.get_assignment(ctx.session, assignment_id, lock=True)
    member = await _member(ctx, row.member_id)
    ctx.principal.require_team(ASSIGNMENT_WRITE, member.team_id)
    expected.check(row.version)
    task = await _task(ctx, row.task_id)
    _require_open(task)
    before = snapshot(row, AUDITED)
    pair = (row.member_id, row.week_start)
    task_id, skill_id, deleted_id = row.task_id, row.skill_id, row.id
    await ctx.session.delete(row)
    await ctx.uow.flush()
    ctx.audit("delete", "assignment", deleted_id, before)
    effect = await _effect(ctx, task_id, skill_id, [pair], deleted_id=deleted_id)
    await ctx.uow.finish()
    return effect
