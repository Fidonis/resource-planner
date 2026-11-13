"""Reading the audit log."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.models import AuditLog
from planner_api.repositories.base import page_of


async def list_entries(
    session: AsyncSession,
    *,
    entity_type: str | None,
    entity_id: str | None,
    actor_user_id: uuid.UUID | None,
    action: str | None,
    via: str | None,
    client_name: str | None,
    since: datetime | None,
    until: datetime | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[AuditLog], int]:
    stmt = select(AuditLog)
    filters = {
        AuditLog.entity_type: entity_type,
        AuditLog.entity_id: entity_id,
        AuditLog.actor_user_id: actor_user_id,
        AuditLog.action: action,
        AuditLog.via: via,
        AuditLog.client_name: client_name,
    }
    for column, value in filters.items():
        if value is not None:
            stmt = stmt.where(column == value)
    if since is not None:
        stmt = stmt.where(AuditLog.at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.at < until)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={"at": AuditLog.at, "id": AuditLog.id},
        default_sort="-at,-id",
    )
