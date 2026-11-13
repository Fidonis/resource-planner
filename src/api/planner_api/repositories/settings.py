"""Application settings stored as key/value rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.models import AppSetting

DEFAULTS: dict[str, Any] = {
    "planning_horizon_weeks": 4,
    "default_allocation_percent": 80,
    "locale": "en",
}


async def load_settings(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(AppSetting.key, AppSetting.value))).all()
    values = dict(DEFAULTS)
    values.update({key: value for key, value in rows if key in DEFAULTS})
    return values


async def save_settings(session: AsyncSession, values: dict[str, Any]) -> None:
    for key, value in values.items():
        statement = insert(AppSetting).values(key=key, value=value)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[AppSetting.key], set_={"value": statement.excluded.value}
            )
        )
