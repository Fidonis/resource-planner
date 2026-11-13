"""Assignment endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, DryRunCtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import ASSIGNMENT_READ, ASSIGNMENT_WRITE
from planner_api.routers.params import (
    CSV_RESPONSE,
    DeleteDep,
    HorizonDep,
    PageDep,
    csv_response,
    etag,
)
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.planning import (
    AssignmentEffect,
    AssignmentIn,
    AssignmentOut,
    AssignmentResult,
    AssignmentUpdate,
)
from planner_api.services import assignments as service
from planner_api.services import exports

router = APIRouter(prefix="/assignments", tags=["assignments"], responses=ErrorResponses)

DRY_RUN_NOTE = (
    "With `dryRun=true` the change is checked and its effect computed, but nothing is saved and "
    "no audit entry is written; the answer has the shape of the real one with `dryRun: true`."
)


@router.get(
    "",
    operation_id="assignments_list",
    summary="List assignments",
    description="Assignments with the names of their task, skill and member.",
    response_model=Page[AssignmentOut],
    **guard(ASSIGNMENT_READ),
)
async def list_assignments(
    ctx: CtxDep,
    paging: PageDep,
    start: Annotated[
        date | None, Query(alias="from", description="Weeks starting on or after this day.")
    ] = None,
    end: Annotated[
        date | None, Query(alias="to", description="Weeks starting on or before this day.")
    ] = None,
    member_id: Annotated[uuid.UUID | None, Query(alias="memberId")] = None,
    task_id: Annotated[uuid.UUID | None, Query(alias="taskId")] = None,
    skill_id: Annotated[uuid.UUID | None, Query(alias="skillId")] = None,
    team_id: Annotated[
        uuid.UUID | None, Query(alias="teamId", description="Team of the member.")
    ] = None,
) -> Page[AssignmentOut]:
    return await service.list_assignments(
        ctx,
        start=start,
        end=end,
        member_id=member_id,
        task_id=task_id,
        skill_id=skill_id,
        team_id=team_id,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="assignments_create",
    summary="Assign a task skill to a member for a week",
    description=(
        "The member must be active and have the skill (ASSIGNMENT_SKILL_MISMATCH), the task must "
        "be open (TASK_CLOSED) and need the skill (ASSIGNMENT_SKILL_NOT_IN_TASK). An assignment "
        "that already exists for the same task, skill, member and week is added to and answered "
        "with 200 and `mergedInto`. Overbooking is allowed and flagged in `overbooked`. "
        + DRY_RUN_NOTE
    ),
    status_code=201,
    response_model=AssignmentResult,
    responses={200: {"model": AssignmentResult, "description": "Merged into an existing one."}},
    **guard(ASSIGNMENT_WRITE, dry_run=True),
)
async def create_assignment(
    body: AssignmentIn, ctx: DryRunCtxDep, response: Response
) -> AssignmentResult:
    result, merged = await service.create_assignment(ctx, body)
    if merged:
        response.status_code = 200
    if not ctx.dry_run:
        etag(response, result.version)
        response.headers["Location"] = f"/api/v1/assignments/{result.id}"
    return result


@router.get(
    "/export-csv",
    operation_id="assignments_export_csv",
    summary="Export the assignments of the horizon as CSV",
    description="`Task;Skill;Member;Team;Week;PersonDays`.",
    response_class=Response,
    responses=CSV_RESPONSE,
    **guard(ASSIGNMENT_READ),
)
async def export_assignments(ctx: CtxDep, horizon: HorizonDep) -> Response:
    return csv_response(
        await exports.assignments_csv(ctx, horizon.start, horizon.weeks), "assignments.csv"
    )


@router.get(
    "/{assignment_id}",
    operation_id="assignments_get",
    summary="Get an assignment",
    response_model=AssignmentOut,
    **guard(ASSIGNMENT_READ),
)
async def get_assignment(
    assignment_id: uuid.UUID, ctx: CtxDep, response: Response
) -> AssignmentOut:
    out = await service.get_assignment(ctx, assignment_id)
    etag(response, out.version)
    return out


@router.put(
    "/{assignment_id}",
    operation_id="assignments_update",
    summary="Change the amount, member or week of an assignment",
    description=(
        "Moving to a member and week that already hold the same task skill adds the amount "
        "there; the answer then names the surviving assignment and `deletedId`. Needs the "
        "current version in `If-Match` or in the body. " + DRY_RUN_NOTE
    ),
    response_model=AssignmentResult,
    **guard(ASSIGNMENT_WRITE, dry_run=True),
)
async def update_assignment(
    assignment_id: uuid.UUID,
    body: AssignmentUpdate,
    ctx: DryRunCtxDep,
    if_match: IfMatch,
    response: Response,
) -> AssignmentResult:
    result = await service.update_assignment(
        ctx, assignment_id, body, expect(if_match, body.version)
    )
    if not ctx.dry_run:
        etag(response, result.version)
    return result


@router.delete(
    "/{assignment_id}",
    operation_id="assignments_delete",
    summary="Delete an assignment",
    description="Answers with the effect on the member's week and the task skill. " + DRY_RUN_NOTE,
    response_model=AssignmentEffect,
    **guard(ASSIGNMENT_WRITE, dry_run=True),
)
async def delete_assignment(
    assignment_id: uuid.UUID, ctx: DryRunCtxDep, expected: DeleteDep
) -> AssignmentEffect:
    return await service.delete_assignment(ctx, assignment_id, expected)
