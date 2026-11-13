"""Absence types (§3.1): users manage them; a type in use can only be deactivated."""

from __future__ import annotations

import uuid

from planner_api.auth.permissions import ABSENCE_TYPE_READ, ABSENCE_TYPE_WRITE
from planner_api.errors import blocked
from planner_api.models import AbsenceType
from planner_api.repositories import org as repo
from planner_api.schemas.common import Page
from planner_api.schemas.org import AbsenceTypeIn, AbsenceTypeOut, AbsenceTypeUpdate
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx, Expected

AUDITED = ("key", "name", "color", "letter", "reduces_capacity", "active", "sort_order")


def to_out(row: AbsenceType, absence_count: int, ctx: Ctx) -> AbsenceTypeOut:
    return AbsenceTypeOut(
        id=row.id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        key=row.key,
        name=row.name,
        color=row.color,
        letter=row.letter,
        reduces_capacity=row.reduces_capacity,
        active=row.active,
        sort_order=row.sort_order,
        absence_count=absence_count,
        writable=ctx.principal.has(ABSENCE_TYPE_WRITE),
    )


async def list_absence_types(
    ctx: Ctx, *, active: bool | None, page: int, page_size: int, sort: str | None
) -> Page[AbsenceTypeOut]:
    ctx.principal.require(ABSENCE_TYPE_READ)
    rows, total = await repo.list_absence_types(
        ctx.session, active=active, page=page, page_size=page_size, sort=sort
    )
    counts = await repo.absence_counts_by_type(ctx.session, [r.id for r in rows])
    return Page[AbsenceTypeOut](
        items=[to_out(r, counts.get(r.id, 0), ctx) for r in rows], total=total
    )


async def get_absence_type(ctx: Ctx, type_id: uuid.UUID) -> AbsenceTypeOut:
    ctx.principal.require(ABSENCE_TYPE_READ)
    row = await repo.get_absence_type(ctx.session, type_id)
    counts = await repo.absence_counts_by_type(ctx.session, [row.id])
    return to_out(row, counts.get(row.id, 0), ctx)


async def create_absence_type(ctx: Ctx, body: AbsenceTypeIn) -> AbsenceTypeOut:
    ctx.principal.require(ABSENCE_TYPE_WRITE)
    row = AbsenceType(**body.model_dump())
    ctx.session.add(row)
    await ctx.uow.flush()
    ctx.audit("create", "absence-type", row.id, after=snapshot(row, AUDITED))
    out = to_out(row, 0, ctx)
    await ctx.uow.finish()
    return out


async def update_absence_type(
    ctx: Ctx, type_id: uuid.UUID, body: AbsenceTypeUpdate, expected: Expected
) -> AbsenceTypeOut:
    ctx.principal.require(ABSENCE_TYPE_WRITE)
    row = await repo.get_absence_type(ctx.session, type_id)
    expected.check(row.version)
    before = snapshot(row, AUDITED)
    for name, value in body.model_dump(exclude={"version"}).items():
        setattr(row, name, value)
    await ctx.uow.flush()
    ctx.audit("update", "absence-type", row.id, before, snapshot(row, AUDITED))
    counts = await repo.absence_counts_by_type(ctx.session, [row.id])
    out = to_out(row, counts.get(row.id, 0), ctx)
    await ctx.uow.finish()
    return out


async def delete_absence_type(ctx: Ctx, type_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(ABSENCE_TYPE_WRITE)
    row = await repo.get_absence_type(ctx.session, type_id)
    expected.check(row.version)
    counts = await repo.absence_counts_by_type(ctx.session, [row.id])
    if counts.get(row.id):
        raise blocked("absence type", [{"kind": "absences", "count": counts[row.id]}])
    before = snapshot(row, AUDITED)
    await ctx.session.delete(row)
    ctx.audit("delete", "absence-type", row.id, before)
    await ctx.uow.finish()
