"""Member endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import MEMBER_READ, MEMBER_WRITE
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.org import (
    MemberCreate,
    MemberOut,
    MemberSkillsIn,
    MemberSkillsResult,
    MemberUpdate,
)
from planner_api.services import members as service

router = APIRouter(prefix="/members", tags=["members"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="members_list",
    summary="List members",
    description="Members with their team and skills, sorted by name by default.",
    response_model=Page[MemberOut],
    **guard(MEMBER_READ),
)
async def list_members(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of the name or the e-mail address.")] = None,
    team_id: Annotated[uuid.UUID | None, Query(alias="teamId")] = None,
    skill_id: Annotated[uuid.UUID | None, Query(alias="skillId")] = None,
    active: Annotated[bool | None, Query(description="Only active or only inactive.")] = None,
) -> Page[MemberOut]:
    return await service.list_members(
        ctx,
        q=q,
        team_id=team_id,
        skill_id=skill_id,
        active=active,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="members_create",
    summary="Create a member",
    status_code=201,
    response_model=MemberOut,
    **guard(MEMBER_WRITE),
)
async def create_member(body: MemberCreate, ctx: CtxDep, response: Response) -> MemberOut:
    out = await service.create_member(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/members/{out.id}"
    return out


@router.get(
    "/{member_id}",
    operation_id="members_get",
    summary="Get a member",
    response_model=MemberOut,
    **guard(MEMBER_READ),
)
async def get_member(member_id: uuid.UUID, ctx: CtxDep, response: Response) -> MemberOut:
    out = await service.get_member(ctx, member_id)
    etag(response, out.version)
    return out


@router.put(
    "/{member_id}",
    operation_id="members_update",
    summary="Change a member",
    description=(
        "Moving a member to another team keeps their assignments (DM-01) and needs the right "
        "in both teams. Needs the current version in `If-Match` or in the body."
    ),
    response_model=MemberOut,
    **guard(MEMBER_WRITE),
)
async def update_member(
    member_id: uuid.UUID, body: MemberUpdate, ctx: CtxDep, if_match: IfMatch, response: Response
) -> MemberOut:
    out = await service.update_member(ctx, member_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.put(
    "/{member_id}/skills",
    operation_id="members_set_skills",
    summary="Replace the skills of a member",
    description=(
        "Assignments that use a skill the member no longer has are kept and reported in "
        "`conflicts` (DM-02)."
    ),
    response_model=MemberSkillsResult,
    **guard(MEMBER_WRITE),
)
async def set_member_skills(
    member_id: uuid.UUID,
    body: MemberSkillsIn,
    ctx: CtxDep,
    if_match: IfMatch,
    response: Response,
) -> MemberSkillsResult:
    out = await service.set_member_skills(ctx, member_id, body, expect(if_match, body.version))
    etag(response, out.member.version)
    return out


@router.delete(
    "/{member_id}",
    operation_id="members_delete",
    summary="Delete a member",
    description=(
        "Refused with DELETE_BLOCKED while the member has assignments; deactivate the member "
        "instead. Absences of the member are deleted with it."
    ),
    status_code=204,
    **guard(MEMBER_WRITE),
)
async def delete_member(member_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_member(ctx, member_id, expected)
    return Response(status_code=204)
