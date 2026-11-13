"""Team endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import TEAM_READ, TEAM_WRITE
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.org import TeamIn, TeamOut, TeamUpdate
from planner_api.services import teams as service

router = APIRouter(prefix="/teams", tags=["teams"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="teams_list",
    summary="List teams",
    description="Teams with their member counts, sorted by name unless `sort` says otherwise.",
    response_model=Page[TeamOut],
    **guard(TEAM_READ),
)
async def list_teams(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of the team name.")] = None,
) -> Page[TeamOut]:
    return await service.list_teams(
        ctx, q=q, page=paging.page, page_size=paging.page_size, sort=paging.sort
    )


@router.post(
    "",
    operation_id="teams_create",
    summary="Create a team",
    status_code=201,
    response_model=TeamOut,
    **guard(TEAM_WRITE),
)
async def create_team(body: TeamIn, ctx: CtxDep, response: Response) -> TeamOut:
    out = await service.create_team(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/teams/{out.id}"
    return out


@router.get(
    "/{team_id}",
    operation_id="teams_get",
    summary="Get a team",
    response_model=TeamOut,
    **guard(TEAM_READ),
)
async def get_team(team_id: uuid.UUID, ctx: CtxDep, response: Response) -> TeamOut:
    out = await service.get_team(ctx, team_id)
    etag(response, out.version)
    return out


@router.put(
    "/{team_id}",
    operation_id="teams_update",
    summary="Change a team",
    description="Needs the current version in `If-Match` or in the body.",
    response_model=TeamOut,
    **guard(TEAM_WRITE),
)
async def update_team(
    team_id: uuid.UUID, body: TeamUpdate, ctx: CtxDep, if_match: IfMatch, response: Response
) -> TeamOut:
    out = await service.update_team(ctx, team_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.delete(
    "/{team_id}",
    operation_id="teams_delete",
    summary="Delete a team",
    description=(
        "Refused with DELETE_BLOCKED while the team has members; move or deactivate them first."
    ),
    status_code=204,
    **guard(TEAM_WRITE),
)
async def delete_team(team_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_team(ctx, team_id, expected)
    return Response(status_code=204)
