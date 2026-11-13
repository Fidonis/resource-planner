"""The read models: planning board, overview and availability."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, guard
from planner_api.auth.permissions import ASSIGNMENT_READ
from planner_api.routers.params import CSV_RESPONSE, HorizonDep, csv_response
from planner_api.schemas.common import ErrorResponses
from planner_api.schemas.planning import AvailabilityOut, OverviewOut, PlanningOut
from planner_api.services import exports
from planner_api.services import planning as service

router = APIRouter(tags=["planning"], responses=ErrorResponses)


@router.get(
    "/planning",
    operation_id="planning_get",
    summary="Planning board",
    description=(
        "For a horizon of weeks: the teams with their members, the capacity, absent days, booked "
        "days and assignments of each member and week, and the tasks with effort, assigned and "
        "open per skill. All figures are calculated here; clients must not recompute them. "
        "`memberId` and `taskId` limit the answer to those rows, `withMembers=false` and "
        "`withTasks=false` leave a part out; a client uses them to refresh what a change touched."
    ),
    response_model=PlanningOut,
    **guard(ASSIGNMENT_READ),
)
async def get_planning(
    ctx: CtxDep,
    horizon: HorizonDep,
    team_id: Annotated[
        uuid.UUID | None, Query(alias="teamId", description="Only the members of this team.")
    ] = None,
    skill_id: Annotated[
        uuid.UUID | None,
        Query(alias="skillId", description="Only the tasks and efforts of this skill."),
    ] = None,
    member_ids: Annotated[
        list[uuid.UUID] | None,
        Query(alias="memberId", description="Only these members. Repeat the parameter."),
    ] = None,
    task_ids: Annotated[
        list[uuid.UUID] | None,
        Query(alias="taskId", description="Only these tasks, whatever their status."),
    ] = None,
    only_open_effort: Annotated[
        bool,
        Query(
            alias="onlyOpenEffort",
            description="Leave out tasks that have nothing left to plan. Ignored with `taskId`.",
        ),
    ] = True,
    with_members: Annotated[bool, Query(alias="withMembers")] = True,
    with_tasks: Annotated[bool, Query(alias="withTasks")] = True,
) -> PlanningOut:
    return await service.planning(
        ctx,
        start=horizon.start,
        weeks=horizon.weeks,
        team_id=team_id,
        skill_id=skill_id,
        member_ids=member_ids,
        task_ids=task_ids,
        only_open_effort=only_open_effort,
        with_members=with_members,
        with_tasks=with_tasks,
    )


@router.get(
    "/overview",
    operation_id="overview_get",
    summary="Overview",
    description="Demand, assigned and open person-days, the pool and the status per skill, and "
    "the utilisation per team, for a horizon.",
    response_model=OverviewOut,
    **guard(ASSIGNMENT_READ),
)
async def get_overview(ctx: CtxDep, horizon: HorizonDep) -> OverviewOut:
    return await service.overview(ctx, start=horizon.start, weeks=horizon.weeks)


@router.get(
    "/overview/export-csv",
    operation_id="overview_export_csv",
    summary="Export a table of the overview as CSV",
    response_class=Response,
    responses=CSV_RESPONSE,
    **guard(ASSIGNMENT_READ),
)
async def export_overview(
    ctx: CtxDep,
    horizon: HorizonDep,
    table: Annotated[Literal["skills", "teams"], Query(description="Which table.")] = "skills",
) -> Response:
    return csv_response(
        await exports.overview_csv(ctx, horizon.start, horizon.weeks, table),
        f"overview-{table}.csv",
    )


@router.get(
    "/availability",
    operation_id="availability_get",
    summary="Who has capacity for a skill",
    description="The active members that have the skill with capacity, booked, free and absent "
    "days per week, the one with the most free person-days first. It feeds the Assign dialog.",
    response_model=AvailabilityOut,
    **guard(ASSIGNMENT_READ),
)
async def get_availability(
    ctx: CtxDep,
    horizon: HorizonDep,
    skill_id: Annotated[uuid.UUID, Query(alias="skillId")],
    team_id: Annotated[uuid.UUID | None, Query(alias="teamId")] = None,
) -> AvailabilityOut:
    return await service.availability(
        ctx, skill_id=skill_id, start=horizon.start, weeks=horizon.weeks, team_id=team_id
    )
