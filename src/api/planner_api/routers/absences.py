"""Absence endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from planner_api.auth.deps import CtxDep, DryRunCtxDep, IfMatch, expect, guard, guard_any
from planner_api.auth.permissions import ABSENCE_READ, ABSENCE_WRITE, ABSENCE_WRITE_OWN
from planner_api.routers.params import (
    CSV_RESPONSE,
    DeleteDep,
    HorizonDep,
    PageDep,
    csv_response,
    etag,
    with_csv_request,
)
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.planning import (
    AbsenceDeleteResult,
    AbsenceIn,
    AbsenceOut,
    AbsenceResult,
    AbsenceUpdate,
    CalendarOut,
    ImportResult,
)
from planner_api.services import absences as service
from planner_api.services import exports, imports

router = APIRouter(prefix="/absences", tags=["absences"], responses=ErrorResponses)

DRY_RUN_NOTE = (
    "With `dryRun=true` the change is checked and its effect computed, but nothing is saved and "
    "no audit entry is written."
)
WRITE = (
    "Needs `absence:write` for the member's team (for all members: for all teams), or "
    "`absence:write:own` for the member linked to the caller's account."
)


@router.get(
    "",
    operation_id="absences_list",
    summary="List absences",
    description="Absences that overlap the dates given. An absence for all members is listed "
    "for every member and team that is filtered on.",
    response_model=Page[AbsenceOut],
    **guard(ABSENCE_READ),
)
async def list_absences(
    ctx: CtxDep,
    paging: PageDep,
    member_id: Annotated[uuid.UUID | None, Query(alias="memberId")] = None,
    team_id: Annotated[uuid.UUID | None, Query(alias="teamId")] = None,
    type_id: Annotated[uuid.UUID | None, Query(alias="typeId")] = None,
    start: Annotated[date | None, Query(alias="from", description="Ends on or after.")] = None,
    end: Annotated[date | None, Query(alias="to", description="Starts on or before.")] = None,
    source: Annotated[
        str | None, Query(description="`manual`, `external` or the name of one source.")
    ] = None,
) -> Page[AbsenceOut]:
    return await service.list_absences(
        ctx,
        member_id=member_id,
        team_id=team_id,
        type_id=type_id,
        start=start,
        end=end,
        source=source,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="absences_create",
    summary="Create an absence",
    description=WRITE + " " + DRY_RUN_NOTE,
    status_code=201,
    response_model=AbsenceResult,
    **guard_any(ABSENCE_WRITE, ABSENCE_WRITE_OWN, dry_run=True),
)
async def create_absence(body: AbsenceIn, ctx: DryRunCtxDep, response: Response) -> AbsenceResult:
    result = await service.create_absence(ctx, body)
    if not ctx.dry_run:
        etag(response, result.version)
        response.headers["Location"] = f"/api/v1/absences/{result.id}"
    return result


@router.get(
    "/calendar",
    operation_id="absences_calendar",
    summary="Absence calendar",
    description="Active members by working day, with the absence of each morning and "
    "afternoon, so a half day can be drawn.",
    response_model=CalendarOut,
    **guard(ABSENCE_READ),
)
async def calendar(
    ctx: CtxDep,
    horizon: HorizonDep,
    team_id: Annotated[uuid.UUID | None, Query(alias="teamId")] = None,
) -> CalendarOut:
    return await service.calendar(ctx, start=horizon.start, weeks=horizon.weeks, team_id=team_id)


@router.get(
    "/export-csv",
    operation_id="absences_export_csv",
    summary="Export absences as CSV",
    description="`Name;From;To;Type;Note;StartPart;EndPart`, `*` for all members. "
    "The file can be imported again.",
    response_class=Response,
    responses=CSV_RESPONSE,
    **guard(ABSENCE_READ),
)
async def export_absences(
    ctx: CtxDep,
    start: Annotated[date | None, Query(alias="from", description="Ends on or after.")] = None,
    end: Annotated[date | None, Query(alias="to", description="Starts on or before.")] = None,
) -> Response:
    return csv_response(await exports.absences_csv(ctx, start, end), "absences.csv")


@router.post(
    "/import-csv",
    operation_id="absences_import_csv",
    summary="Import absences from CSV",
    description=(
        "Body: the CSV file (`Content-Type: text/csv`), columns `Name;From;To;Type` and "
        "optionally `Note;StartPart;EndPart`. `*` as name means all members; dates are "
        "`DD.MM.YYYY` or `YYYY-MM-DD`; the type is the key or the name of an absence type. "
        "An absence that exists already is skipped. Rows with errors, and rows for members the "
        "caller may not change, are reported and skipped. " + DRY_RUN_NOTE
    ),
    response_model=ImportResult,
    **with_csv_request(guard_any(ABSENCE_WRITE, ABSENCE_WRITE_OWN, dry_run=True)),
)
async def import_absences(request: Request, ctx: DryRunCtxDep) -> ImportResult:
    return await imports.import_absences(ctx, await request.body())


@router.get(
    "/{absence_id}",
    operation_id="absences_get",
    summary="Get an absence",
    response_model=AbsenceOut,
    **guard(ABSENCE_READ),
)
async def get_absence(absence_id: uuid.UUID, ctx: CtxDep, response: Response) -> AbsenceOut:
    out = await service.get_absence(ctx, absence_id)
    etag(response, out.version)
    return out


@router.put(
    "/{absence_id}",
    operation_id="absences_update",
    summary="Change an absence",
    description=(
        "An absence that an integration delivered answers MANAGED_BY_SOURCE. Needs the current "
        "version in `If-Match` or in the body. " + WRITE + " " + DRY_RUN_NOTE
    ),
    response_model=AbsenceResult,
    **guard_any(ABSENCE_WRITE, ABSENCE_WRITE_OWN, dry_run=True),
)
async def update_absence(
    absence_id: uuid.UUID,
    body: AbsenceUpdate,
    ctx: DryRunCtxDep,
    if_match: IfMatch,
    response: Response,
) -> AbsenceResult:
    result = await service.update_absence(ctx, absence_id, body, expect(if_match, body.version))
    if not ctx.dry_run:
        etag(response, result.version)
    return result


@router.delete(
    "/{absence_id}",
    operation_id="absences_delete",
    summary="Delete an absence",
    description="Answers with the member-weeks that are overbooked once the absence is gone. "
    + WRITE
    + " "
    + DRY_RUN_NOTE,
    response_model=AbsenceDeleteResult,
    **guard_any(ABSENCE_WRITE, ABSENCE_WRITE_OWN, dry_run=True),
)
async def delete_absence(
    absence_id: uuid.UUID, ctx: DryRunCtxDep, expected: DeleteDep
) -> AbsenceDeleteResult:
    return await service.delete_absence(ctx, absence_id, expected)
