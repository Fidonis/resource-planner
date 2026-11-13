"""Absence type endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import ABSENCE_TYPE_READ, ABSENCE_TYPE_WRITE
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.org import AbsenceTypeIn, AbsenceTypeOut, AbsenceTypeUpdate
from planner_api.services import absence_types as service

router = APIRouter(prefix="/absence-types", tags=["absence-types"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="absence_types_list",
    summary="List absence types",
    description="In the order users arranged them (`sortOrder`, then name).",
    response_model=Page[AbsenceTypeOut],
    **guard(ABSENCE_TYPE_READ),
)
async def list_absence_types(
    ctx: CtxDep,
    paging: PageDep,
    active: Annotated[bool | None, Query(description="Only active or only inactive.")] = None,
) -> Page[AbsenceTypeOut]:
    return await service.list_absence_types(
        ctx, active=active, page=paging.page, page_size=paging.page_size, sort=paging.sort
    )


@router.post(
    "",
    operation_id="absence_types_create",
    summary="Create an absence type",
    status_code=201,
    response_model=AbsenceTypeOut,
    **guard(ABSENCE_TYPE_WRITE),
)
async def create_absence_type(
    body: AbsenceTypeIn, ctx: CtxDep, response: Response
) -> AbsenceTypeOut:
    out = await service.create_absence_type(ctx, body)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/absence-types/{out.id}"
    return out


@router.get(
    "/{type_id}",
    operation_id="absence_types_get",
    summary="Get an absence type",
    response_model=AbsenceTypeOut,
    **guard(ABSENCE_TYPE_READ),
)
async def get_absence_type(type_id: uuid.UUID, ctx: CtxDep, response: Response) -> AbsenceTypeOut:
    out = await service.get_absence_type(ctx, type_id)
    etag(response, out.version)
    return out


@router.put(
    "/{type_id}",
    operation_id="absence_types_update",
    summary="Change an absence type",
    description="Needs the current version in `If-Match` or in the body.",
    response_model=AbsenceTypeOut,
    **guard(ABSENCE_TYPE_WRITE),
)
async def update_absence_type(
    type_id: uuid.UUID,
    body: AbsenceTypeUpdate,
    ctx: CtxDep,
    if_match: IfMatch,
    response: Response,
) -> AbsenceTypeOut:
    out = await service.update_absence_type(ctx, type_id, body, expect(if_match, body.version))
    etag(response, out.version)
    return out


@router.delete(
    "/{type_id}",
    operation_id="absence_types_delete",
    summary="Delete an absence type",
    description="A type that absences use is refused with DELETE_BLOCKED; deactivate it instead.",
    status_code=204,
    **guard(ABSENCE_TYPE_WRITE),
)
async def delete_absence_type(type_id: uuid.UUID, ctx: CtxDep, expected: DeleteDep) -> Response:
    await service.delete_absence_type(ctx, type_id, expected)
    return Response(status_code=204)
