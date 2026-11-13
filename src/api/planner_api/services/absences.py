"""Absences and the absence calendar (FR-ABS-01 to 07, §4 rules 1 to 3)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Row, select

from planner_api.auth.permissions import ABSENCE_READ, ABSENCE_WRITE, ABSENCE_WRITE_OWN
from planner_api.domain.calendar import monday_of, workdays
from planner_api.domain.capacity import AbsenceSpan, covers_half, working_days_in
from planner_api.errors import ErrorCode, ProblemError, forbidden, validation
from planner_api.models import Absence, AbsenceType, Member
from planner_api.repositories import planning as planning_repo
from planner_api.repositories import work as repo
from planner_api.schemas.common import Page
from planner_api.schemas.planning import (
    AbsenceDeleteResult,
    AbsenceEffect,
    AbsenceIn,
    AbsenceOut,
    AbsenceResult,
    AbsenceUpdate,
    CalendarDay,
    CalendarMember,
    CalendarOut,
    CalendarSlot,
    MemberWeekOut,
)
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx, Expected
from planner_api.services.effects import member_week_outs, week_refs
from planner_api.services.planning import resolve_weeks

AUDITED = (
    "member_id",
    "absence_type_id",
    "start_date",
    "end_date",
    "start_part",
    "end_part",
    "note",
    "external_source",
    "external_id",
)
MAX_SPAN_DAYS = 3660
MAX_EFFECT_WEEKS = 260


def span_of(absence: Absence, reduces_capacity: bool) -> AbsenceSpan:
    return AbsenceSpan(
        member_id=absence.member_id,
        type_id=absence.absence_type_id,
        start=absence.start_date,
        end=absence.end_date,
        start_part=absence.start_part,
        end_part=absence.end_part,
        reduces_capacity=reduces_capacity,
        id=absence.id,
    )


def may_write(ctx: Ctx, member_id: uuid.UUID | None, team_id: uuid.UUID | None) -> bool:
    """Whether the caller may change an absence of this member (``None``: everybody)."""
    principal = ctx.principal
    if member_id is None:
        return principal.has(ABSENCE_WRITE) and principal.scope(ABSENCE_WRITE) is None
    if team_id is not None and principal.allows_team(ABSENCE_WRITE, team_id):
        return True
    return principal.has(ABSENCE_WRITE_OWN) and principal.member_id == member_id


def _require_write(ctx: Ctx, member_id: uuid.UUID | None, team_id: uuid.UUID | None) -> None:
    if not may_write(ctx, member_id, team_id):
        raise forbidden(ABSENCE_WRITE if ctx.principal.has(ABSENCE_WRITE) else ABSENCE_WRITE_OWN)


def _out(row: Row[Any], ctx: Ctx) -> AbsenceOut:
    a: Absence = row.Absence
    t: AbsenceType = row.AbsenceType
    external = a.external_source is not None
    return AbsenceOut(
        id=a.id,
        version=a.version,
        created_at=a.created_at,
        updated_at=a.updated_at,
        external_source=a.external_source,
        external_id=a.external_id,
        managed_fields=["memberId", "absenceTypeId", "startDate", "endDate"] if external else [],
        member_id=a.member_id,
        member_name=row.member_name,
        team_id=row.team_id,
        absence_type_id=t.id,
        type_key=t.key,
        type_name=t.name,
        type_color=t.color,
        type_letter=t.letter,
        reduces_capacity=t.reduces_capacity,
        start_date=a.start_date,
        end_date=a.end_date,
        start_part=a.start_part,
        end_part=a.end_part,
        note=a.note,
        working_days=working_days_in(span_of(a, t.reduces_capacity)),
        writable=(not external) and may_write(ctx, a.member_id, row.team_id),
    )


def validate_span(body: AbsenceIn) -> None:
    """Dates in order and parts that describe one stretch of time."""
    if body.end_date < body.start_date:
        raise validation(
            "The end date must not be before the start date.", field="endDate", code="date_order"
        )
    if (body.end_date - body.start_date).days > MAX_SPAN_DAYS:
        raise validation("An absence can span at most ten years.", field="endDate", code="too_long")
    if body.start_date == body.end_date:
        if body.start_part != body.end_part:
            raise validation(
                "Use the same part for the start and the end of a single day.",
                field="endPart",
                code="invalid_part",
            )
    else:
        if body.start_part == "AM":
            raise validation(
                "The first of several days starts in the morning (FULL) or at noon (PM).",
                field="startPart",
                code="invalid_part",
            )
        if body.end_part == "PM":
            raise validation(
                "The last of several days ends at noon (AM) or in the evening (FULL).",
                field="endPart",
                code="invalid_part",
            )


async def _member_for(ctx: Ctx, member_id: uuid.UUID | None) -> Member | None:
    if member_id is None:
        return None
    member = await ctx.session.get(Member, member_id)
    if member is None:
        raise validation("The member does not exist.", field="memberId", code="not_found")
    return member


async def _type_for(ctx: Ctx, type_id: uuid.UUID) -> AbsenceType:
    found = await ctx.session.get(AbsenceType, type_id)
    if found is None:
        raise validation(
            "The absence type does not exist.", field="absenceTypeId", code="not_found"
        )
    return found


def _require_manual(absence: Absence) -> None:
    if absence.external_source is not None:
        raise ProblemError(
            409,
            ErrorCode.MANAGED_BY_SOURCE,
            f"This absence is managed by '{absence.external_source}' and cannot be changed here.",
        )


async def effect_of(ctx: Ctx, ranges: list[tuple[uuid.UUID | None, date, date]]) -> AbsenceEffect:
    """Member-weeks inside the given spans that are overbooked right now."""
    seen: dict[tuple[uuid.UUID, date], MemberWeekOut] = {}
    for member_id, start, end in ranges:
        first = monday_of(start)
        last = min(monday_of(end), first + timedelta(weeks=MAX_EFFECT_WEEKS))
        pairs = await planning_repo.booked_pairs(
            ctx.session, first, last, member_ids=None if member_id is None else [member_id]
        )
        for week in await member_week_outs(ctx.session, pairs):
            if week.overbooked:
                seen[(week.member_id, week.week_start)] = week
    ordered = sorted(seen.values(), key=lambda w: (w.week_start, str(w.member_id)))
    return AbsenceEffect(dry_run=ctx.dry_run, overbooked_member_weeks=ordered)


# --- read --------------------------------------------------------------------------------


async def list_absences(
    ctx: Ctx,
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
) -> Page[AbsenceOut]:
    ctx.principal.require(ABSENCE_READ)
    rows, total = await repo.list_absences(
        ctx.session,
        member_id=member_id,
        team_id=team_id,
        type_id=type_id,
        start=start,
        end=end,
        source=source,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page[AbsenceOut](items=[_out(r, ctx) for r in rows], total=total)


async def get_absence(ctx: Ctx, absence_id: uuid.UUID) -> AbsenceOut:
    ctx.principal.require(ABSENCE_READ)
    return _out(await repo.get_absence_row(ctx.session, absence_id), ctx)


# --- write -------------------------------------------------------------------------------


async def create_row(ctx: Ctx, body: AbsenceIn) -> Absence:
    """Validate and add an absence without finishing the transaction (the CSV import shares it)."""
    validate_span(body)
    member = await _member_for(ctx, body.member_id)
    _require_write(ctx, body.member_id, member.team_id if member else None)
    absence_type = await _type_for(ctx, body.absence_type_id)
    if not absence_type.active:
        raise validation(
            "The absence type is inactive.", field="absenceTypeId", code="inactive_type"
        )
    row = Absence(
        member_id=body.member_id,
        absence_type_id=body.absence_type_id,
        start_date=body.start_date,
        end_date=body.end_date,
        start_part=body.start_part,
        end_part=body.end_part,
        note=body.note,
    )
    ctx.session.add(row)
    return row


async def create_absence(ctx: Ctx, body: AbsenceIn) -> AbsenceResult:
    row = await create_row(ctx, body)
    await ctx.uow.flush()
    ctx.audit("create", "absence", row.id, after=snapshot(row, AUDITED))
    effect = await effect_of(ctx, [(row.member_id, row.start_date, row.end_date)])
    result = await _result(ctx, row.id, effect)
    await ctx.uow.finish()
    return result


async def update_absence(
    ctx: Ctx, absence_id: uuid.UUID, body: AbsenceUpdate, expected: Expected
) -> AbsenceResult:
    row = await repo.get_absence(ctx.session, absence_id)
    old_member = await _member_for(ctx, row.member_id)
    _require_write(ctx, row.member_id, old_member.team_id if old_member else None)
    expected.check(row.version)
    _require_manual(row)
    validate_span(body)
    new_member = await _member_for(ctx, body.member_id)
    _require_write(ctx, body.member_id, new_member.team_id if new_member else None)
    new_type = await _type_for(ctx, body.absence_type_id)
    if not new_type.active and new_type.id != row.absence_type_id:
        raise validation(
            "The absence type is inactive.", field="absenceTypeId", code="inactive_type"
        )
    before = snapshot(row, AUDITED)
    old_range = (row.member_id, row.start_date, row.end_date)
    row.member_id = body.member_id
    row.absence_type_id = body.absence_type_id
    row.start_date = body.start_date
    row.end_date = body.end_date
    row.start_part = body.start_part
    row.end_part = body.end_part
    row.note = body.note
    await ctx.uow.flush()
    ctx.audit("update", "absence", row.id, before, snapshot(row, AUDITED))
    effect = await effect_of(ctx, [(row.member_id, row.start_date, row.end_date), old_range])
    result = await _result(ctx, row.id, effect)
    await ctx.uow.finish()
    return result


async def delete_absence(
    ctx: Ctx, absence_id: uuid.UUID, expected: Expected
) -> AbsenceDeleteResult:
    row = await repo.get_absence(ctx.session, absence_id)
    member = await _member_for(ctx, row.member_id)
    _require_write(ctx, row.member_id, member.team_id if member else None)
    expected.check(row.version)
    _require_manual(row)
    before = snapshot(row, AUDITED)
    scope = (row.member_id, row.start_date, row.end_date)
    deleted_id = row.id
    await ctx.session.delete(row)
    await ctx.uow.flush()
    ctx.audit("delete", "absence", deleted_id, before)
    effect = await effect_of(ctx, [scope])
    await ctx.uow.finish()
    return AbsenceDeleteResult(
        dry_run=effect.dry_run,
        overbooked_member_weeks=effect.overbooked_member_weeks,
        deleted_id=deleted_id,
    )


async def _result(ctx: Ctx, absence_id: uuid.UUID, effect: AbsenceEffect) -> AbsenceResult:
    row = await repo.get_absence_row(ctx.session, absence_id)
    return AbsenceResult.model_validate({**_out(row, ctx).model_dump(), **effect.model_dump()})


# --- calendar ----------------------------------------------------------------------------


def _slot(
    spans: list[AbsenceSpan],
    order: dict[uuid.UUID, tuple[bool, int]],
    letters: dict[uuid.UUID, tuple[str, str]],
    member_id: uuid.UUID,
    day: date,
    half: str,
) -> CalendarSlot | None:
    """The absence shown in one half day: one that costs capacity first, then by type order."""
    covering = [s for s in spans if s.applies_to(member_id) and covers_half(s, day, half)]
    if not covering:
        return None
    best = min(
        covering,
        key=lambda s: (not s.reduces_capacity, order[s.type_id][1], str(s.id)),
    )
    letter, color = letters[best.type_id]
    assert best.id is not None
    return CalendarSlot(
        absence_id=best.id,
        type_id=best.type_id,
        letter=letter,
        color=color,
        reduces_capacity=best.reduces_capacity,
    )


async def calendar(
    ctx: Ctx, *, start: date | None, weeks: int | None, team_id: uuid.UUID | None
) -> CalendarOut:
    """Members by working days of the horizon, with the absence of each morning and afternoon."""
    ctx.principal.require(ABSENCE_READ)
    starts = await resolve_weeks(ctx, start, weeks)
    days = [d for w in starts for d in workdays(w)]
    members = await planning_repo.load_members(ctx.session, team_id=team_id, active_only=True)
    spans = await planning_repo.load_spans(
        ctx.session, starts[0], starts[-1] + timedelta(days=6), member_ids=[m.id for m in members]
    )
    types = (await ctx.session.scalars(select(AbsenceType))).all()
    order = {t.id: (t.reduces_capacity, t.sort_order) for t in types}
    letters = {t.id: (t.letter, t.color) for t in types}
    names = {m.id: m.name for m in members}
    rows = [
        CalendarMember(
            member_id=m.id,
            display_name=names[m.id],
            team_id=m.team_id,
            days=[
                CalendarDay(
                    date=d,
                    am=_slot(spans, order, letters, m.id, d, "AM"),
                    pm=_slot(spans, order, letters, m.id, d, "PM"),
                )
                for d in days
            ],
        )
        for m in members
    ]
    return CalendarOut(start=starts[0], weeks=week_refs(starts), days=days, members=rows)
