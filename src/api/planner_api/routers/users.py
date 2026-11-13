"""User endpoints (local accounts and accounts created by single sign-on)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import USER_ADMIN
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.admin import (
    PasswordReset,
    ResetPasswordIn,
    UserCreated,
    UserIn,
    UserOut,
    UserUpdate,
)
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.services import users as service

router = APIRouter(prefix="/users", tags=["users"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="users_list",
    summary="List accounts",
    description="Local accounts and accounts that single sign-on created, sorted by user name.",
    response_model=Page[UserOut],
    **guard(USER_ADMIN),
)
async def list_users(
    ctx: CtxDep,
    paging: PageDep,
    q: Annotated[str | None, Query(description="Part of user name, name or e-mail.")] = None,
    local: Annotated[
        bool | None, Query(description="Only local (true) or only SSO accounts.")
    ] = None,
    active: Annotated[bool | None, Query()] = None,
) -> Page[UserOut]:
    return await service.list_users(
        ctx,
        q=q,
        local=local,
        active=active,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="users_create",
    summary="Create a local account",
    description=(
        "The account has to choose a new password at its first sign-in. Without `password` "
        "the server generates one and returns it once as `temporaryPassword`."
    ),
    status_code=201,
    response_model=UserCreated,
    **guard(USER_ADMIN),
)
async def create_user(body: UserIn, ctx: CtxDep, response: Response) -> UserCreated:
    out = await service.create_user(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/users/{out.id}"
    return out


@router.get(
    "/{user_id}",
    operation_id="users_get",
    summary="Get an account",
    response_model=UserOut,
    **guard(USER_ADMIN),
)
async def get_user(user_id: uuid.UUID, ctx: CtxDep, response: Response) -> UserOut:
    out = await service.get_user(ctx, user_id)
    etag(response, out.version)
    return out


@router.put(
    "/{user_id}",
    operation_id="users_update",
    summary="Change an account",
    description=(
        "Name, e-mail, status, roles (each optionally limited to teams) and the linked member. "
        "Name and e-mail of a single-sign-on account belong to the identity provider. Needs the "
        "current version in `If-Match` or in the body. The last active local account that can "
        "manage users cannot be switched off or lose that right (LAST_ADMIN)."
    ),
    response_model=UserOut,
    **guard(USER_ADMIN),
)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, ctx: CtxDep, if_match: IfMatch, response: Response
) -> UserOut:
    out = await service.update_user(ctx, user_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.delete(
    "/{user_id}",
    operation_id="users_delete",
    summary="Delete an account",
    description=(
        "Its sessions and API tokens go with it; the audit log keeps the name. Your own account "
        "and the last administrator cannot be deleted."
    ),
    status_code=204,
    **guard(USER_ADMIN),
)
async def delete_user(user_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_user(ctx, user_id, expected)
    return Response(status_code=204)


@router.post(
    "/{user_id}/reset-password",
    operation_id="users_reset_password",
    summary="Reset the password of a local account",
    description=(
        "Sets a temporary password, ends all sessions of the account and lifts a lock. The "
        "account has to choose a new password at its next sign-in."
    ),
    response_model=PasswordReset,
    **guard(USER_ADMIN),
)
async def reset_password(user_id: uuid.UUID, body: ResetPasswordIn, ctx: CtxDep) -> PasswordReset:
    return await service.reset_password(ctx, user_id, body)
