"""Integration endpoints: providers, settings, connection test, manual sync, run history."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from planner_api.auth.deps import CtxDep, IfMatch, expect, guard
from planner_api.auth.permissions import INTEGRATION_ADMIN
from planner_api.routers.params import DeleteDep, PageDep, etag
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.schemas.integrations import (
    ConnectionResult,
    IntegrationIn,
    IntegrationOut,
    IntegrationType,
    IntegrationUpdate,
    ProviderList,
    SyncRunDetail,
    SyncRunOut,
)
from planner_api.services import integrations as service
from planner_api.sync.engine import SyncEngine

router = APIRouter(prefix="/integrations", tags=["integrations"], responses=ErrorResponses)


def get_engine(request: Request) -> SyncEngine:
    engine: SyncEngine = request.app.state.sync_engine
    return engine


EngineDep = Annotated[SyncEngine, Depends(get_engine)]


async def reschedule(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.refresh()


@router.get(
    "/providers",
    operation_id="integrations_providers",
    summary="List the available providers",
    description="Every provider found at start-up, with the JSON Schema of its settings (fields "
    "marked `writeOnly` are secrets) and of the mapping for its type.",
    response_model=ProviderList,
    **guard(INTEGRATION_ADMIN),
)
async def list_providers(ctx: CtxDep, engine: EngineDep) -> ProviderList:
    return service.providers(ctx, engine)


@router.get(
    "",
    operation_id="integrations_list",
    summary="List integrations",
    response_model=Page[IntegrationOut],
    **guard(INTEGRATION_ADMIN),
)
async def list_integrations(
    ctx: CtxDep,
    engine: EngineDep,
    paging: PageDep,
    type_: Annotated[IntegrationType | None, Query(alias="type")] = None,
) -> Page[IntegrationOut]:
    return await service.list_integrations(
        ctx, engine, type_=type_, page=paging.page, page_size=paging.page_size, sort=paging.sort
    )


@router.post(
    "",
    operation_id="integrations_create",
    summary="Create an integration",
    description="Secrets in `config` are stored encrypted and never returned.",
    status_code=201,
    response_model=IntegrationOut,
    **guard(INTEGRATION_ADMIN),
)
async def create_integration(
    body: IntegrationIn, ctx: CtxDep, engine: EngineDep, request: Request, response: Response
) -> IntegrationOut:
    out = await service.create_integration(ctx, engine, body)
    await reschedule(request)
    etag(response, out.version)
    response.headers["Location"] = f"/api/v1/integrations/{out.id}"
    return out


@router.get(
    "/{integration_id}",
    operation_id="integrations_get",
    summary="Get an integration",
    response_model=IntegrationOut,
    **guard(INTEGRATION_ADMIN),
)
async def get_integration(
    integration_id: uuid.UUID, ctx: CtxDep, engine: EngineDep, response: Response
) -> IntegrationOut:
    out = await service.get_integration(ctx, engine, integration_id)
    etag(response, out.version)
    return out


@router.put(
    "/{integration_id}",
    operation_id="integrations_update",
    summary="Change an integration",
    description="Needs the current version in `If-Match` or in the body. A secret that is left "
    "out or empty keeps its value.",
    response_model=IntegrationOut,
    **guard(INTEGRATION_ADMIN),
)
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationUpdate,
    ctx: CtxDep,
    engine: EngineDep,
    if_match: IfMatch,
    request: Request,
    response: Response,
) -> IntegrationOut:
    out = await service.update_integration(
        ctx, engine, integration_id, body, expect(if_match, body.version)
    )
    await reschedule(request)
    etag(response, out.version)
    return out


@router.delete(
    "/{integration_id}",
    operation_id="integrations_delete",
    summary="Delete an integration",
    description="Refused with `DELETE_BLOCKED` while records it wrote exist. With "
    "`detach=true` those records become manual data and the integration is deleted.",
    status_code=204,
    **guard(INTEGRATION_ADMIN),
)
async def delete_integration(
    integration_id: uuid.UUID,
    ctx: CtxDep,
    engine: EngineDep,
    expected: DeleteDep,
    request: Request,
    detach: Annotated[bool, Query(description="Keep what it wrote as manual data.")] = False,
) -> Response:
    await service.delete_integration(ctx, integration_id, expected, engine, detach=detach)
    await reschedule(request)
    return Response(status_code=204)


@router.post(
    "/{integration_id}/test",
    operation_id="integrations_test",
    summary="Test the connection",
    description="Checks that the source can be reached with the stored settings. Nothing is "
    "imported.",
    response_model=ConnectionResult,
    **guard(INTEGRATION_ADMIN),
)
async def test_integration(
    integration_id: uuid.UUID, ctx: CtxDep, engine: EngineDep
) -> ConnectionResult:
    return await service.test_integration(ctx, engine, integration_id)


@router.post(
    "/{integration_id}/sync",
    operation_id="integrations_sync",
    summary="Run a sync now",
    description="Runs the sync and answers when it has ended. With `dryRun=true` everything is "
    "computed and rolled back: the run is recorded with its counts and log, the data is not "
    "changed. `full=true` reads everything even if the provider could read only the changes. "
    "A second request while a run is going on answers `409 SYNC_RUNNING`.",
    response_model=SyncRunDetail,
    **guard(INTEGRATION_ADMIN),
)
async def run_sync(
    integration_id: uuid.UUID,
    ctx: CtxDep,
    engine: EngineDep,
    dry_run: Annotated[bool, Query(alias="dryRun")] = False,
    full: bool = False,
) -> SyncRunDetail:
    return await service.run_sync(ctx, engine, integration_id, dry_run=dry_run, full=full)


@router.get(
    "/{integration_id}/runs",
    operation_id="integrations_runs",
    summary="List the runs of an integration",
    description="Newest first.",
    response_model=Page[SyncRunOut],
    **guard(INTEGRATION_ADMIN),
)
async def list_runs(integration_id: uuid.UUID, ctx: CtxDep, paging: PageDep) -> Page[SyncRunOut]:
    return await service.list_runs(
        ctx, integration_id, page=paging.page, page_size=paging.page_size
    )


@router.get(
    "/{integration_id}/runs/{run_id}",
    operation_id="integrations_run",
    summary="Get one run with its log",
    response_model=SyncRunDetail,
    **guard(INTEGRATION_ADMIN),
)
async def get_run(integration_id: uuid.UUID, run_id: uuid.UUID, ctx: CtxDep) -> SyncRunDetail:
    return await service.get_run(ctx, integration_id, run_id)
