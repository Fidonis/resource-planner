"""API token endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, guard
from planner_api.auth.permissions import TOKEN_ADMIN
from planner_api.routers.params import PageDep
from planner_api.schemas.admin import ApiTokenCreated, ApiTokenIn, ApiTokenOut
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.services import tokens as service

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="api_tokens_list",
    summary="List API tokens",
    description="Newest first. The token itself is never shown again, only its first characters.",
    response_model=Page[ApiTokenOut],
    **guard(TOKEN_ADMIN),
)
async def list_tokens(
    ctx: CtxDep,
    paging: PageDep,
    user_id: Annotated[uuid.UUID | None, Query(alias="userId")] = None,
    include_revoked: Annotated[bool, Query(alias="includeRevoked")] = False,
) -> Page[ApiTokenOut]:
    return await service.list_tokens(
        ctx,
        user_id=user_id,
        include_revoked=include_revoked,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )


@router.post(
    "",
    operation_id="api_tokens_create",
    summary="Create an API token",
    description=(
        "The token acts as the account and can do at most what both the account and the token's "
        "permission list allow. Send it as `Authorization: Bearer rpt_...`. It is returned only "
        "in this answer."
    ),
    status_code=201,
    response_model=ApiTokenCreated,
    **guard(TOKEN_ADMIN),
)
async def create_token(body: ApiTokenIn, ctx: CtxDep, response: Response) -> ApiTokenCreated:
    out = await service.create_token(ctx, body)
    response.headers["Location"] = f"/api/v1/api-tokens/{out.id}"
    return out


@router.delete(
    "/{token_id}",
    operation_id="api_tokens_revoke",
    summary="Revoke an API token",
    description="The token stops working at once. The entry stays in the list as revoked.",
    status_code=204,
    **guard(TOKEN_ADMIN),
)
async def revoke_token(token_id: uuid.UUID, ctx: CtxDep) -> Response:
    await service.revoke_token(ctx, token_id)
    return Response(status_code=204)
