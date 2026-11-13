"""Read and change the application settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.permissions import SETTINGS_ADMIN
from planner_api.config import Settings
from planner_api.repositories import settings as repo
from planner_api.schemas.settings import SettingsIn, SettingsOut
from planner_api.services.context import Ctx


def _to_out(values: dict[str, Any], settings: Settings) -> SettingsOut:
    return SettingsOut(
        planning_horizon_weeks=values["planning_horizon_weeks"],
        default_allocation_percent=values["default_allocation_percent"],
        locale=values["locale"],
        auth_local_enabled=settings.auth_local_enabled,
        auth_oidc_enabled=settings.auth_oidc_enabled,
        demo_seed_enabled=settings.enable_demo_seed,
    )


async def get_values(session: AsyncSession) -> dict[str, Any]:
    return await repo.load_settings(session)


async def read(ctx: Ctx) -> SettingsOut:
    return _to_out(await repo.load_settings(ctx.session), ctx.settings)


async def update(ctx: Ctx, body: SettingsIn) -> SettingsOut:
    ctx.principal.require(SETTINGS_ADMIN)
    before = await repo.load_settings(ctx.session)
    after = {
        "planning_horizon_weeks": body.planning_horizon_weeks,
        "default_allocation_percent": body.default_allocation_percent,
        "locale": body.locale,
    }
    await repo.save_settings(ctx.session, after)
    ctx.audit("update", "settings", "settings", before, after)
    result = _to_out(after, ctx.settings)
    await ctx.uow.finish()
    return result
