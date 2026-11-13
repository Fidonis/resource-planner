"""Settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from planner_api.auth.deps import CtxDep, guard, perm
from planner_api.auth.permissions import SETTINGS_ADMIN
from planner_api.schemas.common import ErrorResponses
from planner_api.schemas.settings import SettingsIn, SettingsOut
from planner_api.services import settings as service

router = APIRouter(prefix="/settings", tags=["settings"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="settings_get",
    summary="Read the settings",
    description="Any signed-in caller may read them. The switches for the sign-in modes are "
    "set by the server environment and shown read-only.",
    response_model=SettingsOut,
    openapi_extra=perm("authenticated"),
)
async def get_settings(ctx: CtxDep) -> SettingsOut:
    return await service.read(ctx)


@router.put(
    "",
    operation_id="settings_update",
    summary="Change the settings",
    response_model=SettingsOut,
    **guard(SETTINGS_ADMIN),
)
async def update_settings(body: SettingsIn, ctx: CtxDep) -> SettingsOut:
    return await service.update(ctx, body)
