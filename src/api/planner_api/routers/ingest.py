"""The machine ingest API: idempotent upsert by source and external id."""

from __future__ import annotations

from fastapi import APIRouter

from planner_api.auth.deps import DryRunCtxDep, guard
from planner_api.auth.permissions import INGEST_WRITE
from planner_api.schemas.common import ErrorResponses
from planner_api.schemas.integrations import (
    IngestAbsencesIn,
    IngestMembersIn,
    IngestResult,
    IngestTasksIn,
)
from planner_api.services import ingest as service

router = APIRouter(prefix="/ingest", tags=["ingest"], responses=ErrorResponses)

NOTE = (
    "Records are matched by `source` and `externalId`, so sending the same data again changes "
    "nothing. A record that cannot be written is reported in `messages` and the others are "
    "still saved. Assignments are never touched. With `dryRun=true` nothing is saved."
)


@router.post(
    "/tasks",
    operation_id="ingest_tasks",
    summary="Create or update tasks of an outside system",
    description=NOTE + " Fields the source owns (title, status, efforts when sent) are locked "
    "for editing in the planner.",
    response_model=IngestResult,
    **guard(INGEST_WRITE, dry_run=True),
)
async def ingest_tasks(body: IngestTasksIn, ctx: DryRunCtxDep) -> IngestResult:
    return await service.ingest_tasks(ctx, body)


@router.post(
    "/members",
    operation_id="ingest_members",
    summary="Create or update members of an outside system",
    description=NOTE + " A member without a team is refused unless `defaultTeam` names one.",
    response_model=IngestResult,
    **guard(INGEST_WRITE, dry_run=True),
)
async def ingest_members(body: IngestMembersIn, ctx: DryRunCtxDep) -> IngestResult:
    return await service.ingest_members(ctx, body)


@router.post(
    "/absences",
    operation_id="ingest_absences",
    summary="Create or update absences of an outside system",
    description=NOTE,
    response_model=IngestResult,
    **guard(INGEST_WRITE, dry_run=True),
)
async def ingest_absences(body: IngestAbsencesIn, ctx: DryRunCtxDep) -> IngestResult:
    return await service.ingest_absences(ctx, body)
