"""Task endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response

from planner_api.auth.deps import CtxDep, DryRunCtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import TASK_READ, TASK_WRITE
from planner_api.routers.params import (
    CSV_RESPONSE,
    DeleteDep,
    PageDep,
    csv_response,
    etag,
    with_csv_request,
)
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.planning import (
    EffortsIn,
    ImportResult,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from planner_api.services import exports, imports
from planner_api.services import tasks as service

router = APIRouter(prefix="/tasks", tags=["tasks"], responses=ErrorResponses)

Force = Annotated[
    bool,
    Query(
        description="Remove the assignments that exceed the new effort instead of refusing "
        "the change (DM-03). The audit log records what was removed."
    ),
]


@router.get(
    "",
    operation_id="tasks_list",
    summary="List tasks",
    description="Tasks with the effort per skill and how much of it is assigned.",
    response_model=Page[TaskOut],
    **guard(TASK_READ),
)
async def list_tasks(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of the key or the title.")] = None,
    team_id: Annotated[uuid.UUID | None, Query(alias="teamId", description="Owner team.")] = None,
    status: Annotated[Literal["open", "done", "cancelled"] | None, Query()] = None,
    skill_id: Annotated[
        uuid.UUID | None, Query(alias="skillId", description="Tasks with an effort for the skill.")
    ] = None,
    source: Annotated[
        str | None,
        Query(description="`manual`, `external` (any integration) or the name of one source."),
    ] = None,
) -> Page[TaskOut]:
    return await service.list_tasks(
        ctx,
        q=q,
        owner_team_id=team_id,
        status=status,
        skill_id=skill_id,
        source=source,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="tasks_create",
    summary="Create a task",
    status_code=201,
    response_model=TaskOut,
    **guard(TASK_WRITE),
)
async def create_task(body: TaskCreate, ctx: CtxDep, response: Response) -> TaskOut:
    out = await service.create_task(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/tasks/{out.id}"
    return out


@router.get(
    "/export-csv",
    operation_id="tasks_export_csv",
    summary="Export the tasks as CSV",
    description="`Key;Title;Team;Efforts;Status`, efforts as `Skill=PD|Skill=PD`. "
    "The file can be imported again.",
    response_class=Response,
    responses=CSV_RESPONSE,
    **guard(TASK_READ),
)
async def export_tasks(ctx: CtxDep) -> Response:
    return csv_response(await exports.tasks_csv(ctx), "tasks.csv")


@router.post(
    "/import-csv",
    operation_id="tasks_import_csv",
    summary="Import tasks from CSV",
    description=(
        "Body: the CSV file (`Content-Type: text/csv`), columns `Key;Title;Team;Efforts` and "
        "optionally `Status`, efforts as `Skill=PD|Skill=PD` (decimal point or comma). "
        "A task with a known key is updated; an empty team, efforts or status cell leaves the "
        "value as it is. Rows with errors are reported and skipped. Unknown skills are reported "
        "unless `createSkills=true`. With `dryRun=true` nothing is saved."
    ),
    response_model=ImportResult,
    **with_csv_request(guard(TASK_WRITE, dry_run=True)),
)
async def import_tasks(
    request: Request,
    ctx: DryRunCtxDep,
    create_skills: Annotated[
        bool, Query(alias="createSkills", description="Create the skills the file names.")
    ] = False,
    force: Force = False,
) -> ImportResult:
    return await imports.import_tasks(
        ctx, await request.body(), create_skills=create_skills, force=force
    )


@router.get(
    "/{task_id}",
    operation_id="tasks_get",
    summary="Get a task",
    response_model=TaskOut,
    **guard(TASK_READ),
)
async def get_task(task_id: uuid.UUID, ctx: CtxDep, response: Response) -> TaskOut:
    out = await service.get_task(ctx, task_id)
    etag(response, out.version)
    return out


@router.put(
    "/{task_id}",
    operation_id="tasks_update",
    summary="Change a task",
    description=(
        "Send `efforts` to replace the efforts in the same call; leave it out to keep them. "
        "Fields that an integration owns answer FIELD_LOCKED. Needs the current version in "
        "`If-Match` or in the body."
    ),
    response_model=TaskOut,
    **guard(TASK_WRITE),
)
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    ctx: CtxDep,
    if_match: IfMatch,
    response: Response,
    force: Force = False,
) -> TaskOut:
    out = await service.update_task(ctx, task_id, body, expect(if_match, body.version), force=force)
    etag(response, out.version)
    return out


@router.put(
    "/{task_id}/efforts",
    operation_id="tasks_set_efforts",
    summary="Replace the efforts of a task",
    description=(
        "An effort below what is already assigned is refused with EFFORT_BELOW_ASSIGNED "
        "unless `force=true`, which removes the excess assignments, latest weeks first (DM-03)."
    ),
    response_model=TaskOut,
    **guard(TASK_WRITE),
)
async def set_efforts(
    task_id: uuid.UUID,
    body: EffortsIn,
    ctx: CtxDep,
    if_match: IfMatch,
    response: Response,
    force: Force = False,
) -> TaskOut:
    out = await service.set_efforts(ctx, task_id, body, expect(if_match, body.version), force=force)
    etag(response, out.version)
    return out


@router.delete(
    "/{task_id}",
    operation_id="tasks_delete",
    summary="Delete a task",
    description="Refused with DELETE_BLOCKED while the task has assignments; cancel it instead.",
    status_code=204,
    **guard(TASK_WRITE),
)
async def delete_task(task_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_task(ctx, task_id, expected)
    return Response(status_code=204)
