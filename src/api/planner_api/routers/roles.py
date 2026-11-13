"""Role endpoints, the permission catalogue and the mapping of external roles."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard, guard_any
from planner_api.auth.permissions import ROLE_ADMIN, USER_ADMIN
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.admin import (
    MappingsIn,
    MappingsOut,
    PermissionOut,
    RoleIn,
    RoleOut,
    RoleUpdate,
)
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.services import roles as service

router = APIRouter(tags=["roles"], responses=ErrorResponses)


@router.get(
    "/roles",
    operation_id="roles_list",
    summary="List roles",
    description=(
        "Built-in and custom roles with their permissions. Accounts that may manage users can "
        "read the list to assign roles."
    ),
    response_model=Page[RoleOut],
    **guard_any(ROLE_ADMIN, USER_ADMIN),
)
async def list_roles(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of the role name or key.")] = None,
) -> Page[RoleOut]:
    return await service.list_roles(
        ctx, q=q, page=paging.page, page_size=paging.page_size, sort=paging.sort
    )


@router.post(
    "/roles",
    operation_id="roles_create",
    summary="Create a custom role",
    status_code=201,
    response_model=RoleOut,
    **guard(ROLE_ADMIN),
)
async def create_role(body: RoleIn, ctx: CtxDep, response: Response) -> RoleOut:
    out = await service.create_role(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/roles/{out.id}"
    return out


@router.get(
    "/roles/permissions",
    operation_id="roles_permissions",
    summary="The permission catalogue",
    description="Every permission a role can hold, and whether a role can limit it to teams.",
    response_model=Page[PermissionOut],
    **guard_any(ROLE_ADMIN, USER_ADMIN),
)
async def list_permissions(ctx: CtxDep) -> Page[PermissionOut]:
    items = service.permission_catalogue(ctx)
    return Page[PermissionOut](items=items, total=len(items))


@router.get(
    "/roles/{role_id}",
    operation_id="roles_get",
    summary="Get a role",
    response_model=RoleOut,
    **guard_any(ROLE_ADMIN, USER_ADMIN),
)
async def get_role(role_id: uuid.UUID, ctx: CtxDep, response: Response) -> RoleOut:
    out = await service.get_role(ctx, role_id)
    etag(response, out.version)
    return out


@router.put(
    "/roles/{role_id}",
    operation_id="roles_update",
    summary="Change a custom role",
    description="Built-in roles cannot be changed (BUILTIN_ROLE). Needs the current version.",
    response_model=RoleOut,
    **guard(ROLE_ADMIN),
)
async def update_role(
    role_id: uuid.UUID, body: RoleUpdate, ctx: CtxDep, if_match: IfMatch, response: Response
) -> RoleOut:
    out = await service.update_role(ctx, role_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.delete(
    "/roles/{role_id}",
    operation_id="roles_delete",
    summary="Delete a custom role",
    description=(
        "Refused with DELETE_BLOCKED while accounts hold the role or external roles map to it. "
        "Built-in roles cannot be deleted (BUILTIN_ROLE)."
    ),
    status_code=204,
    **guard(ROLE_ADMIN),
)
async def delete_role(role_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_role(ctx, role_id, expected)
    return Response(status_code=204)


@router.get(
    "/role-mappings",
    operation_id="role_mappings_get",
    summary="Read the mapping of external roles",
    description=(
        "Which role of the identity provider gives which internal role. Applied at every "
        "request, so a change takes effect without a new sign-in."
    ),
    response_model=MappingsOut,
    **guard(ROLE_ADMIN),
)
async def read_mappings(ctx: CtxDep) -> MappingsOut:
    return await service.read_mappings(ctx)


@router.put(
    "/role-mappings",
    operation_id="role_mappings_replace",
    summary="Replace the mapping of external roles",
    description="The list sent becomes the whole mapping; entries left out are removed.",
    response_model=MappingsOut,
    **guard(ROLE_ADMIN),
)
async def replace_mappings(body: MappingsIn, ctx: CtxDep) -> MappingsOut:
    return await service.replace_mappings(ctx, body)
