"""Integrations and their sync runs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.errors import not_found
from planner_api.models import Absence, IntegrationConfig, Member, Skill, SyncRun, Task, Team
from planner_api.repositories.base import page_of

OWNED: dict[str, Any] = {
    "tasks": Task,
    "members": Member,
    "absences": Absence,
    "teams": Team,
    "skills": Skill,
}


async def get_integration(session: AsyncSession, integration_id: uuid.UUID) -> IntegrationConfig:
    row = await session.get(IntegrationConfig, integration_id)
    if row is None:
        raise not_found("Integration", integration_id)
    return row


async def list_integrations(
    session: AsyncSession,
    *,
    type_: str | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[IntegrationConfig], int]:
    stmt = select(IntegrationConfig)
    if type_:
        stmt = stmt.where(IntegrationConfig.type == type_)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "name": func.lower(IntegrationConfig.name),
            "type": IntegrationConfig.type,
            "lastSyncAt": IntegrationConfig.last_sync_at,
        },
        default_sort="name",
    )


async def list_runs(
    session: AsyncSession, integration_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[Sequence[SyncRun], int]:
    return await page_of(
        session,
        select(SyncRun).where(SyncRun.integration_id == integration_id),
        page=page,
        page_size=page_size,
        sort=None,
        sorts={"startedAt": SyncRun.started_at},
        default_sort="-startedAt",
    )


async def get_run(session: AsyncSession, integration_id: uuid.UUID, run_id: uuid.UUID) -> SyncRun:
    run = await session.get(SyncRun, run_id)
    if run is None or run.integration_id != integration_id:
        raise not_found("Sync run", run_id)
    return run


async def owned_counts(session: AsyncSession, source: str) -> dict[str, int]:
    """What the integration has written: records whose source is this integration."""
    counts: dict[str, int] = {}
    for kind, model in OWNED.items():
        found = (
            await session.execute(
                select(func.count()).select_from(model).where(model.external_source == source)
            )
        ).scalar_one()
        if found:
            counts[kind] = found
    return counts


async def detach(session: AsyncSession, source: str) -> None:
    """Make what the integration wrote manual data again."""
    for kind, model in OWNED.items():
        values: dict[str, Any] = {"external_source": None, "external_id": None}
        if kind == "tasks":
            values |= {"locked_fields": [], "external_url": None, "external_updated_at": None}
        if kind == "members":
            values["external_snapshot"] = None
        await session.execute(update(model).where(model.external_source == source).values(**values))
