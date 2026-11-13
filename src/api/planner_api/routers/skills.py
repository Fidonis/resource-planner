"""Skill endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import SKILL_READ, SKILL_WRITE
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.org import SkillIn, SkillOut, SkillUpdate
from planner_api.services import skills as service

router = APIRouter(prefix="/skills", tags=["skills"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="skills_list",
    summary="List skills",
    description="Skills with the number of members that have them, sorted by key by default.",
    response_model=Page[SkillOut],
    **guard(SKILL_READ),
)
async def list_skills(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of the key or the name.")] = None,
) -> Page[SkillOut]:
    return await service.list_skills(
        ctx, q=q, page=paging.page, page_size=paging.page_size, sort=paging.sort
    )


@router.post(
    "",
    operation_id="skills_create",
    summary="Create a skill",
    status_code=201,
    response_model=SkillOut,
    **guard(SKILL_WRITE),
)
async def create_skill(body: SkillIn, ctx: CtxDep, response: Response) -> SkillOut:
    out = await service.create_skill(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/skills/{out.id}"
    return out


@router.get(
    "/{skill_id}",
    operation_id="skills_get",
    summary="Get a skill",
    response_model=SkillOut,
    **guard(SKILL_READ),
)
async def get_skill(skill_id: uuid.UUID, ctx: CtxDep, response: Response) -> SkillOut:
    out = await service.get_skill(ctx, skill_id)
    etag(response, out.version)
    return out


@router.put(
    "/{skill_id}",
    operation_id="skills_update",
    summary="Change a skill",
    description="Needs the current version in `If-Match` or in the body.",
    response_model=SkillOut,
    **guard(SKILL_WRITE),
)
async def update_skill(
    skill_id: uuid.UUID, body: SkillUpdate, ctx: CtxDep, if_match: IfMatch, response: Response
) -> SkillOut:
    out = await service.update_skill(ctx, skill_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.delete(
    "/{skill_id}",
    operation_id="skills_delete",
    summary="Delete a skill",
    description=(
        "Refused with DELETE_BLOCKED while members have the skill or tasks and assignments use it."
    ),
    status_code=204,
    **guard(SKILL_WRITE),
)
async def delete_skill(skill_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_skill(ctx, skill_id, expected)
    return Response(status_code=204)
