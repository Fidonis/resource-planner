"""The machine ingest API: a system that pushes its data instead of being read (§7.1)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from planner_api.auth.permissions import INGEST_WRITE
from planner_api.errors import validation
from planner_api.schemas.integrations import (
    IngestAbsencesIn,
    IngestMembersIn,
    IngestResult,
    IngestTasksIn,
    LogEntryOut,
)
from planner_api.services.context import Ctx
from planner_api.sync.apply import Applier
from planner_api.sync.mapping import AbsenceRecord, MemberRecord, TaskRecord


def _guard(ctx: Ctx, source: str) -> Applier:
    ctx.principal.require(INGEST_WRITE)
    try:
        uuid.UUID(source)
    except ValueError:
        return Applier(ctx, source, f"ingest from '{source}'")
    # An id of that shape belongs to an integration; its data is written by its own syncs only.
    raise validation(
        "The source must not look like the id of an integration.", field="source", code="reserved"
    )


def _result(ctx: Ctx, applier: Applier) -> IngestResult:
    tally = applier.tally
    return IngestResult(
        dry_run=ctx.dry_run,
        created=tally.created,
        updated=tally.updated,
        skipped=tally.skipped,
        errors=tally.errors,
        messages=[
            LogEntryOut(level=e.level, message=e.message, reference=e.reference) for e in tally.log
        ],
    )


async def ingest_tasks(ctx: Ctx, body: IngestTasksIn) -> IngestResult:
    applier = _guard(ctx, body.source)
    await applier.load()
    records = []
    for task in body.tasks:
        efforts: dict[str, Decimal] | None = None
        if task.efforts is not None:
            efforts = {}
            for effort in task.efforts:
                efforts[effort.skill] = efforts.get(effort.skill, Decimal(0)) + effort.person_days
        owned = ("title", "status") if efforts is None else ("title", "status", "efforts")
        records.append(
            TaskRecord(
                external_id=task.external_id,
                key=task.key,
                title=task.title,
                status=task.status,
                description=task.description,
                url=task.url,
                updated_at=task.updated_at,
                owner_team=task.team,
                efforts=efforts,
                owned=owned,
                create_skills=tuple(sorted(efforts)) if body.create_skills and efforts else (),
            )
        )
    await applier.tasks(records)
    result = _result(ctx, applier)
    await ctx.uow.finish()
    return result


async def ingest_members(ctx: Ctx, body: IngestMembersIn) -> IngestResult:
    applier = _guard(ctx, body.source)
    await applier.load()
    records = [
        MemberRecord(
            external_id=m.external_id,
            display_name=m.display_name,
            email=m.email,
            active=m.active,
            team=m.team or body.default_team,
            team_is_default=not m.team and body.default_team is not None,
            create_team=body.create_teams and bool(m.team),
            skills=tuple(m.skills),
        )
        for m in body.members
    ]
    await applier.members(records)
    result = _result(ctx, applier)
    await ctx.uow.finish()
    return result


async def ingest_absences(ctx: Ctx, body: IngestAbsencesIn) -> IngestResult:
    applier = _guard(ctx, body.source)
    await applier.load()
    records = [
        AbsenceRecord(
            external_id=a.external_id,
            person_ref=a.person,
            type_key=a.type,
            start=a.start_date,
            end=a.end_date,
            start_part=a.start_part,
            end_part=a.end_part or (a.start_part if a.start_date == a.end_date else "FULL"),
            note=a.note,
        )
        for a in body.absences
    ]
    await applier.absences(records)
    result = _result(ctx, applier)
    await ctx.uow.finish()
    return result
