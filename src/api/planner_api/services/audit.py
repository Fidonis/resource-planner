"""Audit log queries."""

from __future__ import annotations

import uuid
from datetime import datetime

from planner_api.auth.permissions import AUDIT_READ
from planner_api.repositories import audit as repo
from planner_api.schemas.audit import AuditEntry
from planner_api.schemas.common import Page
from planner_api.services.context import Ctx


async def list_entries(
    ctx: Ctx,
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
) -> Page[AuditEntry]:
    ctx.principal.require(AUDIT_READ)
    rows, total = await repo.list_entries(
        ctx.session,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        via=via,
        client_name=client_name,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page[AuditEntry](items=[AuditEntry.model_validate(r) for r in rows], total=total)
