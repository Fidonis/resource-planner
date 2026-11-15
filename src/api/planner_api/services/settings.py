"""Read and change the application settings."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.permissions import SETTINGS_ADMIN
from planner_api.config import Settings
from planner_api.repositories import settings as repo
from planner_api.schemas.settings import (
    POOL_TASK_FIELDS,
    PoolTaskField,
    SettingsIn,
    SettingsOut,
    ordered_pool_fields,
)
from planner_api.services.context import Ctx


def known_pool_fields(stored: Any) -> list[PoolTaskField]:
    """A stored choice of pool attributes without the names this version no longer knows."""
    if not isinstance(stored, list):
        return []
    return ordered_pool_fields([cast(PoolTaskField, f) for f in stored if f in POOL_TASK_FIELDS])


def _to_out(values: dict[str, Any], settings: Settings) -> SettingsOut:
    return SettingsOut(
        planning_horizon_weeks=values["planning_horizon_weeks"],
        default_allocation_percent=values["default_allocation_percent"],
        locale=values["locale"],
        pool_task_fields=known_pool_fields(values["pool_task_fields"]),
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
        "pool_task_fields": list(body.pool_task_fields),
    }
    await repo.save_settings(ctx.session, after)
    ctx.audit("update", "settings", "settings", before, after)
    result = _to_out(after, ctx.settings)
    await ctx.uow.finish()
    return result
